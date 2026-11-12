"""Naming conventions: inferred from the project itself (what most files of the same package + layer do) or set
in `[naming]` of rules.toml; name x layer coherence; REST route naming; glossary synonyms (via ddd)."""

from __future__ import annotations

import fnmatch
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from nimblegraph import lang as langs
from nimblegraph.query import Graph

STYLES = ("kebab", "camel", "pascal", "snake")
MIN_SAMPLES = 5
MIN_SHARE = 0.7
SKIP_LAYERS = {"test", "config", "entry"}
SPECIAL_FILES = {"index", "main", "app", "page", "layout", "route", "loading", "error", "not-found", "template",
                 "default", "middleware", "server", "client", "types", "constants", "utils", "setup", "vite-env",
                 "env", "global", "globals", "root", "__root", "head"}
SPECIAL_DIRS = {"__tests__", "__mocks__", "node_modules", "src", "app", "lib", "test", "tests", "e2e", ".github"}
CLASS_SUFFIXES = ("Controller", "Service", "Repository", "Repo", "Guard", "Interceptor", "Pipe", "Middleware",
                  "Module", "Dto", "Entity", "Model", "Resolver", "Gateway", "Handler", "UseCase", "Store",
                  "Provider", "Factory", "Mapper", "Strategy", "Filter", "Subscriber", "Listener", "Job", "Processor")
SUFFIX_LAYER = {"Controller": "controller", "Service": "service", "Repository": "repository", "Repo": "repository",
                "Guard": "middleware", "Interceptor": "middleware", "Pipe": "middleware", "Middleware": "middleware",
                "Entity": "model", "Model": "model"}
SPECIFIC_LAYERS = {"controller", "service", "repository", "middleware", "model"}
SUFFIX_LAYERS = SPECIFIC_LAYERS | {"store", "route"}  # layers whose files share one role (not type/module/util)
OTHER_ROLE_SUFFIXES = CLASS_SUFFIXES + ("Helper", "Builder", "Reader", "Writer", "Client", "Catalog", "Policy",
                                        "Calculator", "Validator", "Parser", "Formatter", "Util", "Utils", "Error",
                                        "Exception", "Task", "Scheduler", "Cron", "Consumer", "Producer", "Publisher",
                                        "Emitter", "Facade", "Manager", "Registry", "Config", "Options", "Adapter",
                                        "Port", "Command", "Query", "Event", "Spec", "Specification", "Seeder")
RE_VERB = re.compile(r"^(get|create|update|delete|remove|add|list|fetch|set|edit|save|insert|do|make|find)"
                     r"(?:[A-Z_-]|$)")
ROUTE_PREFIXES = {"api", "v1", "v2", "v3", "v4", "rest", "public", "internal", "admin"}


def styles_of(name: str) -> set[str]:
    """Styles a name satisfies; a single lowercase word fits kebab, camel and snake at once."""
    if re.fullmatch(r"[a-z][a-z0-9]*", name):
        return {"kebab", "camel", "snake"}
    if re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)+", name):
        return {"kebab"}
    if re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)+", name):
        return {"snake"}
    if re.fullmatch(r"[a-z][a-z0-9]*([A-Z][a-z0-9]*)+", name):
        return {"camel"}
    if re.fullmatch(r"[A-Z][a-zA-Z0-9]*", name):
        return {"pascal"}
    return set()


def stem_parts(path: str) -> tuple[str, str | None]:
    """'src/users/user.service.ts' -> ('user', 'service'); 'Button.tsx' -> ('Button', None)."""
    base = posixpath.basename(path)
    base = langs.strip_ext(base)
    parts = base.split(".")
    return parts[0], (parts[1] if len(parts) > 1 else None)


@dataclass
class Convention:
    what: str  # file-case | file-suffix | class-suffix | folder-case | route-case | route-param
    group: str  # "pkg layer" or "routes" / "folders"
    value: str  # kebab / .service / *Service
    share: float
    samples: int
    configured: bool = False
    outliers: list[tuple[str, str, int]] = field(default_factory=list)  # (where, name, line)


def _dominant(counts: Counter, total: int) -> tuple[str | None, float]:
    if not counts or total == 0:
        return None, 0.0
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return best[0], best[1] / total


def _groups(g: Graph) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = defaultdict(list)
    for f, node in g.files.items():
        layer = node.layer or "module"
        if layer in SKIP_LAYERS:
            continue
        stem, _ = stem_parts(f)
        if stem.lower() in SPECIAL_FILES or stem.startswith(("[", "(", "_", "+", "$", "-")):
            continue
        out[(node.pkg or ".", layer)].append(f)
    return out


def _main_classes(g: Graph, f: str) -> list[dict]:
    return [s for s in g.symbols.get(f, []) if s["kind"] == "class" and s["exported"] and not s["parent"]]


def conventions(g: Graph, naming_cfg: dict | None = None) -> list[Convention]:
    cfg = naming_cfg or {}
    layer_cfg = cfg.get("layers") or {}
    out: list[Convention] = []
    for (pkg, layer), files in sorted(_groups(g).items()):
        group = f"{pkg} {layer}"
        lc = layer_cfg.get(layer) or {}
        # file case
        want = lc.get("style") or cfg.get("files")
        stems = {f: stem_parts(f)[0] for f in files}
        if want:
            bad = [f for f, s in stems.items() if want not in styles_of(s)]
            out.append(Convention("file-case", group, want, 1 - len(bad) / len(files), len(files), True,
                                  [(f, posixpath.basename(f), 0) for f in sorted(bad)]))
        elif len(files) >= MIN_SAMPLES:
            counts = Counter(st for s in stems.values() for st in styles_of(s))
            decisive = sum(1 for s in stems.values() if len(styles_of(s)) == 1)
            style, share = _dominant(counts, len(files))
            if style and share >= MIN_SHARE and decisive >= 2:
                bad = [f for f, s in stems.items() if style not in styles_of(s)]
                out.append(Convention("file-case", group, style, share, len(files), False,
                                      [(f, posixpath.basename(f), 0) for f in sorted(bad)]))
        # dotted suffix (user.service.ts)
        if lc.get("file"):
            pat = lc["file"]
            bad = [f for f in files if not fnmatch.fnmatch(posixpath.basename(f), pat)]
            out.append(Convention("file-suffix", group, pat, 1 - len(bad) / len(files), len(files), True,
                                  [(f, posixpath.basename(f), 0) for f in sorted(bad)]))
        elif len(files) >= MIN_SAMPLES and layer in SUFFIX_LAYERS:
            suffixes = Counter(stem_parts(f)[1] for f in files if stem_parts(f)[1])
            suf, share = _dominant(suffixes, len(files))
            if suf and share >= MIN_SHARE:
                bad = [f for f in files if not stem_parts(f)[1]]  # another suffix (.factory) is another role
                out.append(Convention("file-suffix", group, f".{suf}", share, len(files), False,
                                      [(f, posixpath.basename(f), 0) for f in sorted(bad)]))
        # class suffix
        classes = [(f, s) for f in files for s in _main_classes(g, f)]
        if lc.get("class"):
            pat = lc["class"]
            bad = [(f, s) for f, s in classes if not fnmatch.fnmatchcase(s["name"], pat)]
            if classes:
                out.append(Convention("class-suffix", group, pat, 1 - len(bad) / len(classes), len(classes), True,
                                      [(f, s["name"], s["line"]) for f, s in bad]))
        elif len(classes) >= MIN_SAMPLES and layer in SUFFIX_LAYERS:
            counts = Counter(next((x for x in CLASS_SUFFIXES if s["name"].endswith(x) and s["name"] != x), None)
                             for _f, s in classes)
            counts.pop(None, None)
            suf, share = _dominant(counts, len(classes))
            if suf and share >= MIN_SHARE:
                bad = [(f, s) for f, s in classes if not s["name"].endswith(OTHER_ROLE_SUFFIXES)]
                out.append(Convention("class-suffix", group, f"*{suf}", share, len(classes), False,
                                      [(f, s["name"], s["line"]) for f, s in bad]))
    out += _folder_conventions(g, cfg.get("folders"))
    out += _route_conventions(g, cfg.get("routes"))
    return out


def _folder_conventions(g: Graph, want: str | None) -> list[Convention]:
    dirs: set[str] = set()
    for f, node in g.files.items():
        if (node.layer or "") in SKIP_LAYERS:
            continue
        parts = f.split("/")[:-1]
        for i in range(len(parts)):
            dirs.add("/".join(parts[:i + 1]))
    names = {d: posixpath.basename(d) for d in dirs
             if posixpath.basename(d).lower() not in SPECIAL_DIRS and not re.match(r"^[\[(@_.+$]", posixpath.basename(d))}
    if not names:
        return []
    if want:
        bad = sorted(d for d, n in names.items() if want not in styles_of(n))
        return [Convention("folder-case", "folders", want, 1 - len(bad) / len(names), len(names), True,
                           [(d + "/", posixpath.basename(d), 0) for d in bad])]
    if len(names) < MIN_SAMPLES:
        return []
    counts = Counter(st for n in names.values() for st in styles_of(n))
    decisive = sum(1 for n in names.values() if len(styles_of(n)) == 1)
    style, share = _dominant(counts, len(names))
    if not style or share < MIN_SHARE or decisive < 2:
        return []
    bad = sorted(d for d, n in names.items() if style not in styles_of(n))
    return [Convention("folder-case", "folders", style, share, len(names), False,
                       [(d + "/", posixpath.basename(d), 0) for d in bad])]


def _literal_segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s and not s.startswith((":", "*", "{", "[", "$"))]


def _route_conventions(g: Graph, want: str | None) -> list[Convention]:
    routes = [r for r in g.routes.values() if r.kind == "api"]
    if not routes:
        return []
    out = []
    segs = [(r, s) for r in routes for s in _literal_segments(r.path) if len(styles_of(s)) == 1 or not styles_of(s)]
    multi = [(r, s) for r, s in segs if styles_of(s)]
    style = want
    share = 1.0
    if not style and len(multi) >= 3:
        counts = Counter(st for _r, s in multi for st in styles_of(s))
        style, share = _dominant(counts, len(multi))
        style = style if share >= MIN_SHARE else None
    if style:
        bad = [(r, s) for r, s in segs if style not in styles_of(s)]
        out.append(Convention("route-case", "routes", style, share, len(multi), bool(want),
                              [(r.file, f"{r.method} {r.path} ({s})", r.line) for r, s in bad]))
    params = [(r, p[1:]) for r in routes for p in r.path.split("/") if p.startswith(":") and p != ":param"]
    pm = [(r, p) for r, p in params if len(styles_of(p)) == 1]
    if len(pm) >= 3:
        counts = Counter(st for _r, p in pm for st in styles_of(p))
        pstyle, pshare = _dominant(counts, len(pm))
        if pstyle and pshare >= MIN_SHARE:
            bad = [(r, p) for r, p in params if styles_of(p) and pstyle not in styles_of(p)]
            out.append(Convention("route-param", "routes", pstyle, pshare, len(pm), False,
                                  [(r.file, f"{r.method} {r.path} (:{p})", r.line) for r, p in bad]))
    return out


def route_smells(g: Graph) -> list[tuple[str, str, int, str, str]]:
    """(sub-rule, file, line, route label, message): verbs in paths and singular/plural resource pairs."""
    out = []
    routes = [r for r in g.routes.values() if r.kind == "api"]
    for r in routes:
        for s in _literal_segments(r.path):
            if RE_VERB.match(s):
                out.append(("naming/route-verb", r.file, r.line, f"{r.method} {r.path}",
                            f"verb '{s}' in the path; the HTTP method is the verb (e.g. POST /resource)"))
                break
    roots: Counter = Counter()
    first: dict[str, object] = {}
    for r in routes:
        segs = [s for s in _literal_segments(r.path) if s.lower() not in ROUTE_PREFIXES]
        if segs:
            roots[segs[0]] += 1
            first.setdefault(segs[0], r)
    for res, n in roots.items():
        plural = res + "s"
        if plural in roots:
            minority, major = (res, plural) if n <= roots[plural] else (plural, res)
            r = first[minority]
            out.append(("naming/route-plural", r.file, r.line, f"{r.method} {r.path}",  # type: ignore[attr-defined]
                        f"resource '/{minority}' next to '/{major}': pick one form"))
    return out


def layer_mismatches(g: Graph) -> list[tuple[str, str, int, str]]:
    """(file, class, line, message) for classes whose suffix says another layer than the file's."""
    out = []
    for f, node in g.files.items():
        layer = node.layer or "module"
        if layer not in SPECIFIC_LAYERS:
            continue
        classes = _main_classes(g, f)
        expected = {SUFFIX_LAYER[x] for s in classes for x in SUFFIX_LAYER if s["name"].endswith(x)}
        if len(expected) != 1:
            continue
        for s in classes:
            exp = next((SUFFIX_LAYER[x] for x in SUFFIX_LAYER if s["name"].endswith(x)), None)
            if exp and exp != layer:
                out.append((f, s["name"], s["line"], f"{s['name']} is named like a {exp} but lives in a {layer} file"))
    return out


SUB_MESSAGES = {
    "file-case": "file name does not follow the {value} convention of {group} ({pct} of {n})",
    "file-suffix": "file name misses the {value} suffix used by {group} ({pct} of {n})",
    "class-suffix": "class name misses the {value} suffix used by {group} ({pct} of {n})",
    "folder-case": "folder name does not follow the {value} convention ({pct} of {n} folders)",
    "route-case": "route segment does not follow the {value} style of the API ({pct} of {n})",
    "route-param": "route parameter does not follow the {value} style ({pct} of {n})",
}


def violations(g: Graph, a, m, r) -> list:
    from nimblegraph.rules import Violation
    naming_cfg = (m.rules_cfg or {}).get("naming") or {}
    out = []
    for c in conventions(g, naming_cfg):
        msg = SUB_MESSAGES[c.what].format(value=c.value, group=c.group, pct=f"{round(c.share * 100)}%", n=c.samples)
        if c.configured:
            msg = msg.split(" (")[0] + " (rules.toml)"
        for where, name, line in c.outliers:
            dst = name if c.what.startswith(("route", "class")) else None
            out.append(Violation(f"naming/{c.what}", r.severity, where, dst, line, msg, r.adr))
    for f, cls, line, msg in layer_mismatches(g):
        out.append(Violation("naming/layer-mismatch", r.severity, f, cls, line, msg, r.adr))
    for sub, f, line, label, msg in route_smells(g):
        out.append(Violation(sub, r.severity, f, label, line, msg, r.adr))
    if m.cfg is not None:
        from nimblegraph import ddd
        for f, line, term, used, msg in ddd.glossary_hits(m.cfg, g):
            out.append(Violation("naming/glossary", r.severity, f, used, line, msg, r.adr))
    return out
