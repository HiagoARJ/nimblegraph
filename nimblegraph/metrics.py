"""Architecture reading: module metrics (Robert Martin's Ca, Ce, I, A, D), god files, dependency matrix,
context/layer diagrams, git hotspots, metric history (trend), ADRs linked to files, and dead exports."""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from nimblegraph import arch as arch_mod
from nimblegraph import gitutil, query
from nimblegraph.config import Config
from nimblegraph.query import IMPORT_KINDS, Graph

HISTORY_FILE = "history.jsonl"
SKIP = {"test", "config"}
ADR_DIRS = ("docs/adr", "docs/adrs", "docs/decisions", "docs/architecture/decisions", "adr", "adrs", "doc/adr")
FRAMEWORK_DECOS = {"Controller", "Injectable", "Module", "Entity", "Resolver", "CommandHandler", "QueryHandler",
                   "EventsHandler", "Catch", "WebSocketGateway", "Processor", "Schema", "Component", "Directive",
                   "Pipe", "NgModule", "Service", "Repository", "Middleware"}


# ---------------------------------------------------------------------- module metrics

def unit_of(g: Graph, a: arch_mod.Arch, f: str, by: str, level: int) -> str:
    if by == "context":
        return a.context.get(f) or "(none)"
    if by == "layer":
        return g.layer(f)
    if by == "package":
        return g.files[f].pkg or "(root)"
    if by == "ring":
        return a.ring.get(f) or "(none)"
    parts = f.split("/")[:-1]
    return "/".join(parts[:level]) or "."


@dataclass
class ModuleMetric:
    name: str
    files: int = 0
    loc: int = 0
    ca: int = 0  # files outside that depend on this module
    ce: int = 0  # files outside this module depends on
    abstract: int = 0
    concrete: int = 0

    @property
    def instability(self) -> float:
        return self.ce / (self.ca + self.ce) if self.ca + self.ce else 0.0

    @property
    def abstractness(self) -> float:
        total = self.abstract + self.concrete
        return self.abstract / total if total else 0.0

    @property
    def distance(self) -> float:
        return abs(self.abstractness + self.instability - 1)

    @property
    def zone(self) -> str:
        if self.distance < 0.5:
            return ""
        if self.abstractness < 0.5 and self.instability < 0.5:
            return "pain"  # concrete and depended upon: rigid
        if self.abstractness >= 0.5 and self.instability >= 0.5:
            return "useless"  # abstract and nobody depends on it
        return ""


def module_metrics(g: Graph, a: arch_mod.Arch, by: str = "context", level: int = 3) -> list[ModuleMetric]:
    files = [f for f in g.files if g.layer(f) not in SKIP]
    unit = {f: unit_of(g, a, f, by, level) for f in files}
    mods: dict[str, ModuleMetric] = {}
    ca_sets: dict[str, set[str]] = defaultdict(set)
    ce_sets: dict[str, set[str]] = defaultdict(set)
    for f in files:
        m = mods.setdefault(unit[f], ModuleMetric(unit[f]))
        m.files += 1
        m.loc += g.files[f].loc
        for s in g.symbols.get(f, []):
            if s["parent"] or s["kind"] not in ("class", "type"):
                continue
            if s["kind"] == "type" or s.get("abstract"):
                m.abstract += 1
            else:
                m.concrete += 1
        for e in g.out.get(f, []):
            if e.kind not in IMPORT_KINDS + ("type",) or e.dst not in unit or unit[e.dst] == unit[f]:
                continue
            ce_sets[unit[f]].add(e.dst)
            ca_sets[unit[e.dst]].add(f)
    for name, m in mods.items():
        m.ca, m.ce = len(ca_sets[name]), len(ce_sets[name])
    return sorted(mods.values(), key=lambda m: (-m.files, m.name))


def god_files(g: Graph, top: int = 10) -> list[tuple[str, int, int, int]]:
    """(file, importers, dependencies, loc), by importers x dependencies (files that everything flows through)."""
    rows = []
    for f, node in g.files.items():
        if g.layer(f) in SKIP | {"type"}:
            continue
        ins = len(g.importers(f))
        outs = len({e.dst for e in g.out.get(f, []) if e.kind in IMPORT_KINDS})
        if ins and outs:
            rows.append((f, ins, outs, node.loc))
    return sorted(rows, key=lambda r: (-(r[1] * r[2]), -r[3], r[0]))[:top]


def dsm(g: Graph, a: arch_mod.Arch, by: str, level: int) -> tuple[list[str], dict[tuple[str, str], int]]:
    cells: Counter = Counter()
    names: Counter = Counter()
    for f in g.files:
        if g.layer(f) in SKIP:
            continue
        u = unit_of(g, a, f, by, level)
        names[u] += 1
        for e in g.out.get(f, []):
            if e.kind in IMPORT_KINDS and e.dst in g.files and g.layer(e.dst) not in SKIP:
                v = unit_of(g, a, e.dst, by, level)
                if v != u:
                    cells[(u, v)] += 1
    return [n for n, _ in names.most_common()], dict(cells)


def _mid(name: str, ids: dict[str, str]) -> str:
    if name not in ids:
        ids[name] = f"n{len(ids)}"
    return ids[name]


def mermaid(g: Graph, a: arch_mod.Arch, by: str, level: int) -> str:
    names, cells = dsm(g, a, by, level)
    links = arch_mod.context_links(g, a) if by == "context" else {}
    ids: dict[str, str] = {}
    lines = ["flowchart LR"]
    for n in names:
        lines.append(f'  {_mid(n, ids)}["{n}"]')
    for (u, v), n in sorted(cells.items()):
        leaks = len(links[(u, v)].leaks) if (u, v) in links else 0
        label = f"{n}" + (f", {leaks} leaks" if leaks else "")
        arrow = "-.->" if leaks else "-->"
        lines.append(f"  {_mid(u, ids)} {arrow}|{label}| {_mid(v, ids)}")
    for (u, v), link in sorted(links.items()):
        if link.http and (u, v) not in cells:
            lines.append(f"  {_mid(u, ids)} ==>|http {link.http}| {_mid(v, ids)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- hotspots / history

@dataclass
class Hotspot:
    file: str
    commits: int
    loc: int
    importers: int
    score: float


def hotspots(cfg: Config, g: Graph, months: int = 6, top: int = 15) -> tuple[list[Hotspot], list[str]]:
    counts, repos = gitutil.churn(cfg.root, months)
    rows = []
    for f, n in counts.items():
        node = g.files.get(f)
        if not node or g.layer(f) in SKIP:
            continue
        imp = len(g.importers(f))
        rows.append(Hotspot(f, n, node.loc, imp, round(n * math.log2(2 + node.loc) * (1 + math.log2(1 + imp)), 1)))
    return sorted(rows, key=lambda h: (-h.score, h.file))[:top], repos


def snapshot(cfg: Config, g: Graph, res=None) -> dict:
    a = arch_mod.model(g, arch_mod.load_rules(cfg))
    mods = [m for m in module_metrics(g, a, "context") if m.name != "(none)"]
    links = arch_mod.context_links(g, a)
    snap = {
        "at": time.strftime("%Y-%m-%d %H:%M"), "commit": gitutil.head(cfg.root),
        "files": len(g.files), "loc": sum(f.loc for f in g.files.values()),
        "cycles": len(query.cycles(g)), "contexts": len(mods),
        "context_links": len(links), "leaks": sum(len(lk.leaks) for lk in links.values()),
        "avg_distance": round(sum(m.distance for m in mods) / len(mods), 3) if mods else None,
    }
    if res is not None:
        snap["violations"] = res.counts()
        snap["new"] = res.counts("new")
    return snap


def record(cfg: Config, g: Graph, res=None) -> dict:
    snap = snapshot(cfg, g, res)
    p = cfg.dir / HISTORY_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = history(cfg)
    if rows and rows[-1].get("commit") == snap["commit"] and rows[-1].get("at", "")[:10] == snap["at"][:10]:
        rows[-1] = snap  # one snapshot per commit and day
    else:
        rows.append(snap)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    return snap


def history(cfg: Config) -> list[dict]:
    p = arch_mod.governance_path(cfg, HISTORY_FILE)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------- ADRs

@dataclass
class Adr:
    id: str
    title: str
    status: str
    path: str
    applies: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def adrs(cfg: Config, g: Graph) -> list[Adr]:
    rules = arch_mod.load_rules(cfg)
    dirs = [(rules.get("adr") or {}).get("dir")] if (rules.get("adr") or {}).get("dir") else list(ADR_DIRS)
    out = []
    for d in dirs:
        base = cfg.root / d
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.match(r"^(\d+)", p.stem)
            title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), p.stem)
            status = ""
            sm = re.search(r"^\s*(?:[-*]\s*)?\**status\**\s*[:=]\s*\**\s*([A-Za-z-]+)", text, re.I | re.M)
            if sm:
                status = sm.group(1).lower()
            applies: list[str] = []
            for am in re.finditer(r"^\s*(?:[-*]\s*)?\**applies[-_ ]to\**\s*[:=]\s*(.+)$", text, re.I | re.M):
                applies += [x.strip(" `'\"[]") for x in re.split(r"[,\s]+", am.group(1)) if x.strip(" `'\"[]")]
            adr = Adr(m.group(1) if m else p.stem, title, status, p.relative_to(cfg.root).as_posix(),
                      applies)
            adr.files = sorted(f for f in g.files if any(arch_mod._glob_hit(f, gl) for gl in applies))
            out.append(adr)
        if out:
            break
    return out


def adrs_for(adr_list: list[Adr], files) -> dict[str, list[str]]:
    """ADR id -> touched files among `files`."""
    fs = set(files)
    return {a.id: sorted(fs & set(a.files)) for a in adr_list if fs & set(a.files)}


# ---------------------------------------------------------------------- dead code

def dead_exports(g: Graph) -> list[tuple[str, str, int, str]]:
    """(file, symbol, line, kind) for exported top-level symbols nobody imports (framework-wired code excluded)."""
    used: dict[str, set[str]] = defaultdict(set)
    for edges in g.inn.values():
        for e in edges:
            for n in e.names:
                used[e.dst].add(n)
    for r in g.routes.values():
        if r.handler:
            used[r.target].add(r.handler.split(".")[0])
    out = []
    skip_layers = SKIP | {"entry", "page", "layout", "api-route", "route"}
    for f, syms in g.symbols.items():
        node = g.files.get(f)
        if not node or node.is_entry or g.layer(f) in skip_layers:
            continue
        names = used.get(f, set())
        if "*" in names:
            continue
        exported = [s for s in syms if s["exported"] and not s["parent"]]
        if "default" in names and len(exported) <= 1:
            continue
        if not g.importers(f) and not names:
            continue  # orphan file: reported by `nimblegraph orphans`
        for s in exported:
            if s["name"] in names or set(s.get("decorators") or []) & FRAMEWORK_DECOS:
                continue
            out.append((f, s["name"], s["line"], s["kind"]))
    return sorted(out)
