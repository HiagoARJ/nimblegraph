"""Build pipeline: scan (hash) -> parse only changed files (cache in `parsed`) -> resolve imports,
follow barrels -> rewrite the graph tables."""

from __future__ import annotations

import posixpath
from pathlib import Path
import time
from collections import defaultdict
from dataclasses import dataclass, field

from nimblegraph import classify, entries, scanner
from nimblegraph import engine as engines
from nimblegraph import lang as langs
from nimblegraph.config import Config
from nimblegraph.frameworks import Context
from nimblegraph.frameworks import run_all as run_frameworks
from nimblegraph.lang.manifest import read as read_manifest
from nimblegraph.lang.resolve import LangResolver
from nimblegraph.resolve import Resolution, Resolver, package_name
from nimblegraph.store import Store

EDGE_KIND = {
    "import": "import", "require": "import", "type": "type", "dynamic": "dynamic",
    "reexport": "reexport", "reexport-all": "reexport", "side-effect": "side-effect",
}
FOLLOW_KINDS = ("import", "type", "require")
# bump when the per-file record changes (extract.mjs / parse/ts.py): cached parses are discarded
EXTRACTOR_VERSION = "8"


@dataclass
class BuildResult:
    files: int = 0
    parsed: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    engine: str = ""
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    edges: int = 0
    unresolved: int = 0
    routes: int = 0
    routes_added: list[str] = field(default_factory=list)
    routes_removed: list[str] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def run(cfg: Config, engine_pref: str = "auto", full: bool = False) -> BuildResult:
    t0 = time.time()
    res = BuildResult()
    store = Store(cfg.db_path)
    try:
        prev = store.file_stats()
        sr = scanner.scan(cfg, None if full else prev)
        res.files = len(sr.files)
        res.added = sorted(p for p in sr.files if p not in prev)
        res.removed = sorted(p for p in prev if p not in sr.files)
        res.changed = sorted(p for p, fi in sr.files.items() if p in prev and prev[p][2] != fi.hash)

        fp = f"{sr.config_fingerprint()}:{EXTRACTOR_VERSION}"
        if full or store.get_meta("config_fp") != fp:
            store.clear_parsed()
        cached = store.parsed_hashes()
        to_parse = sorted(p for p, fi in sr.files.items() if cached.get(p) != fi.hash)
        stale = [p for p in cached if p not in sr.files]
        if not (full or to_parse or stale or res.dirty) and store.get_meta("config_fp") == fp \
                and store.get_meta("built_at") and store.get_meta("settings_fp") == _settings_fp(cfg):
            # nothing changed: keep the graph tables (watch mode calls this every second)
            res.engine = store.get_meta("engine", "")
            res.edges = store.query("SELECT COUNT(*) FROM edges")[0][0]
            res.routes = store.query("SELECT COUNT(*) FROM routes")[0][0]
            res.unresolved = store.query("SELECT COUNT(*) FROM unresolved")[0][0]
            res.seconds = round(time.time() - t0, 2)
            return res

        engine_desc = store.get_meta("engine", "").split(" + ")[0]
        js_parse = [p for p in to_parse if langs.is_js(p)]
        other_parse = [p for p in to_parse if not langs.is_js(p)]
        if js_parse:
            eng, warn = engines.select(cfg.root, engine_pref)
            if warn:
                res.warnings.append(warn)
            out = eng.extract(cfg.root, js_parse)
            if out.warning:
                res.warnings.append(out.warning)
            records = {p: (sr.files[p].hash, eng.name, r) for p, r in out.records.items()}
            if out.failed and eng.name == "node":
                py = engines.PythonEngine().extract(cfg.root, out.failed)
                records.update({p: (sr.files[p].hash, "python", r) for p, r in py.records.items()})
                if py.warning:
                    res.warnings.append(py.warning)
            store.save_parsed(records)
            res.parsed = len(records)
            engine_desc = eng.describe()
            if eng.name == "node" and out.header.get("ts"):
                engine_desc += f" (ts {out.header['ts']} {out.header.get('ts_from', '')})".rstrip()
        if other_parse:
            from nimblegraph.lang.engine import TreeSitterEngine
            ts_eng = TreeSitterEngine()
            out = ts_eng.extract(cfg.root, other_parse)
            if out.warning:
                res.warnings.append(out.warning)
            store.save_parsed({p: (sr.files[p].hash, "tree-sitter", r) for p, r in out.records.items()})
            res.parsed += len(out.records)
        store.drop_parsed(stale)

        parsed = store.load_parsed()
        engine_desc = _engine_desc(engine_desc, parsed)
        old_routes = {f"{m} {p}" for m, p in store.query("SELECT method, path FROM routes")}
        tables, stats = assemble(cfg, sr, parsed)
        new_routes = {f"{row[1]} {row[2]}" for row in tables["routes"]}
        res.routes_added = sorted(new_routes - old_routes) if prev else []
        res.routes_removed = sorted(old_routes - new_routes)
        store.replace_graph(tables)
        res.edges = stats["edges"]
        res.unresolved = stats["unresolved"]
        res.routes = stats["routes"]
        res.engine = engine_desc
        res.seconds = round(time.time() - t0, 2)
        store.set_meta("config_fp", fp)
        store.set_meta("settings_fp", _settings_fp(cfg))
        store.set_meta("engine", engine_desc)
        store.set_meta("built_at", time.time())
        store.set_meta("build_seconds", res.seconds)
        store.set_meta("warnings", res.warnings)
        store.set_meta("packages", stats["packages"])
        store.set_meta("module_exports", stats["module_exports"])
        from nimblegraph.config import _is_workspace_root
        store.set_meta("workspace", _is_workspace_root(cfg.root))
        if res.dirty or not store.get_meta("last_changes"):
            store.set_meta("last_changes", {"added": res.added, "removed": res.removed, "changed": res.changed,
                                            "routes_added": res.routes_added, "routes_removed": res.routes_removed,
                                            "at": time.time()})
        store.commit()
    finally:
        store.close()
    return res


def _engine_desc(js_desc: str, parsed: dict[str, dict]) -> str:
    """'node v24 (ts 5.9) + tree-sitter: Go, Java (regex: C#)' -- the JS engine first, then the other languages."""
    seen: dict[str, bool] = {}
    for rec in parsed.values():
        lg = rec.get("lang")
        if lg and lg != "javascript":
            seen[lg] = seen.get(lg, False) or bool(rec.get("partial"))
    if not seen:
        return js_desc
    label = lambda n: langs.BY_NAME[n].label if n in langs.BY_NAME else n  # noqa: E731
    full = [label(n) for n in sorted(seen) if not seen[n]]
    partial = [label(n) for n in sorted(seen) if seen[n]]
    desc = "tree-sitter: " + ", ".join(full) if full else "tree-sitter"
    if partial:
        desc += f" (regex: {', '.join(partial)})"
    return f"{js_desc} + {desc}" if js_desc and any(langs.is_js(p) for p in parsed) else desc


def _settings_fp(cfg: Config) -> str:
    """config.toml settings that change the graph (aliases, layers, entries...)."""
    import hashlib
    import json
    h = hashlib.sha1(json.dumps(cfg.settings, sort_keys=True, default=str).encode())
    # the tool's own code: an updated nimblegraph rebuilds the graph tables even when the project did not change
    pkg = Path(__file__).parent
    for f in sorted([*pkg.glob("*.py"), *pkg.glob("frameworks/*.py"), *pkg.glob("lang/*.py")]):
        h.update(f"{f.name}:{f.stat().st_mtime_ns}".encode())
    return h.hexdigest()


# ---------------------------------------------------------------------- assembly

def _nearest_package(path: str, pkg_dirs: list[str]) -> str:
    best = ""
    for d in pkg_dirs:
        if (d == "" or path.startswith(d + "/")) and len(d) >= len(best):
            best = d
    return best


def assemble(cfg: Config, sr: scanner.ScanResult, parsed: dict[str, dict]) -> tuple[dict[str, list[tuple]], dict]:
    files = set(sr.files)
    resolver = Resolver(cfg.root, files, sr.packages, cfg["aliases"])

    def classify_import(imp: dict, path: str) -> Resolution:
        if "resolved" in imp:  # Node engine
            r = imp.get("resolved")
            if r:
                hit = resolver.normalize(r)
                if hit:
                    return Resolution("file", hit)
            if imp.get("external"):
                name = package_name(imp["spec"])
                return Resolution("ext", name[5:] if name.startswith("node:") else name)
        return resolver.resolve(imp["spec"], path)

    # per-file resolved imports (JS/TS; the other languages go through lang.resolve below)
    resolved: dict[str, list[tuple[dict, Resolution]]] = {}
    for path, rec in parsed.items():
        if path not in files or not langs.is_js(path):
            continue
        resolved[path] = [(imp, classify_import(imp, path)) for imp in rec.get("imports", [])]

    # exports, for barrel following
    local_exports: dict[str, set[str]] = {}
    reexports: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    stars: dict[str, list[str]] = defaultdict(list)
    for path, rec in parsed.items():
        if path not in resolved:
            continue
        names = {e["name"] for e in rec.get("exports", [])}
        names |= {s["name"] for s in rec.get("symbols", []) if s.get("exported")}
        local_exports[path] = names
        for imp, r in resolved[path]:
            if r.kind != "file":
                continue
            if imp["kind"] == "reexport":
                for imported, exported in imp["names"]:
                    reexports[path][exported] = (r.target, imported)
            elif imp["kind"] == "reexport-all":
                stars[path].append(r.target)

    export_cache: dict[str, set[str]] = {}

    def exports_of(f: str, seen: frozenset = frozenset()) -> set[str]:
        if f in export_cache:
            return export_cache[f]
        if f in seen or len(seen) > 12:
            return set()
        out = set(local_exports.get(f, ())) | set(reexports.get(f, {}))
        for t in stars.get(f, ()):
            out |= exports_of(t, seen | {f}) - {"default"}
        export_cache[f] = out
        return out

    def origin(f: str, name: str, depth: int = 0) -> str:
        if depth > 8 or name in local_exports.get(f, ()):
            return f
        hop = reexports.get(f, {}).get(name)
        if hop:
            target, imported = hop
            return target if imported == "*" else origin(target, imported, depth + 1)
        if name != "default":
            for t in stars.get(f, ()):
                if name in exports_of(t):
                    return origin(t, name, depth + 1)
        return f

    edges: dict[tuple, list] = {}  # (src, dst, kind, via) -> [names set, line]

    def add_edge(src: str, dst: str, kind: str, names, via: str | None, line: int) -> None:
        if src == dst:
            return
        key = (src, dst, kind, via)
        cur = edges.get(key)
        if cur is None:
            edges[key] = [set(names), line]
        else:
            cur[0].update(names)
            cur[1] = min(cur[1], line)

    externals: dict[tuple, int] = {}  # (file, pkg, kind) -> first line
    unresolved: list[tuple] = []
    symbols: list[tuple] = []
    local_targets: dict[str, dict[str, tuple[str, str]]] = {}
    spec_targets: dict[str, dict[str, str]] = {}
    ext_by_file: dict[str, set[str]] = defaultdict(set)

    for path, items in resolved.items():
        rec = parsed[path]
        local_target: dict[str, tuple[str, str]] = {}  # local name -> (file, imported name)
        spec_target: dict[str, str] = {}
        local_targets[path] = local_target
        spec_targets[path] = spec_target
        for imp, r in items:
            kind = imp["kind"]
            line = imp.get("line", 0)
            if r.kind == "ext":
                ek = (path, r.target, EDGE_KIND.get(kind, kind))
                externals[ek] = min(externals.get(ek, line), line)
                ext_by_file[path].add(r.target)
                continue
            if r.kind == "asset":
                continue
            if r.kind == "unresolved":
                unresolved.append((path, imp["spec"], line, kind))
                continue
            target = r.target
            spec_target[imp["spec"]] = target
            ekind = EDGE_KIND.get(kind, kind)
            names = imp.get("names") or []
            if kind in FOLLOW_KINDS and names:
                groups: dict[str, list[str]] = defaultdict(list)
                for imported, local in names:
                    dst = target if imported == "*" else origin(target, imported)
                    groups[dst].append(imported)
                    local_target[local] = (dst, imported)
                for dst, nms in groups.items():
                    add_edge(path, dst, ekind, nms, None if dst == target else target, line)
            else:
                add_edge(path, target, ekind, [n[0] for n in names], None, line)
        # renders: <Comp/> whose root identifier is an import or a lazy() local
        for tag in rec.get("jsx", []):
            root = tag.split(".")[0]
            if root in local_target:
                dst, imported = local_target[root]
                add_edge(path, dst, "renders", [root], None, 0)
            elif root in rec.get("dyn_locals", {}):
                dst = spec_target.get(rec["dyn_locals"][root])
                if dst:
                    add_edge(path, dst, "renders", [root], None, 0)
        _add_symbols(symbols, path, rec)

    # ---- the other languages: file imports and references resolved through the declaration index
    manifests = {d: read_manifest(cfg.root, d, names) for d, names in (sr.manifests or {}).items()}
    other = {p: r for p, r in parsed.items() if p in files and not langs.is_js(p)}
    if other:
        lres = LangResolver(cfg.root, files, other, manifests)
        for path, rec in other.items():
            links = lres.links(path, rec)
            local_targets[path] = dict(links.locals)
            spec_targets[path] = dict(links.specs)
            for (dst, kind), (names, line) in links.edges.items():
                add_edge(path, dst, kind, names, None, line)
            for pkg, line in links.externals.items():
                externals[(path, pkg, "import")] = line
                ext_by_file[path].add(pkg)
            unresolved += [(path, spec, line, kind) for spec, line, kind in links.unresolved]
            _add_symbols(symbols, path, rec)

    pkg_dirs = sorted(sr.packages)
    pkg_names = {}
    pkg_deps: dict[str, set[str]] = {}
    packages: dict[str, dict] = {}  # meta for governance: versions per package (version drift, duplicate libs)
    for d in pkg_dirs:
        pj = resolver.package_json(d) or {}
        pkg_names[d] = pj.get("name") or (posixpath.basename(d) if d else "")
        versions: dict[str, str] = {}
        for key in ("peerDependencies", "devDependencies", "dependencies"):
            if isinstance(pj.get(key), dict):
                versions.update({k: str(v) for k, v in pj[key].items()})
        pkg_deps[d] = set(versions)
        packages[d] = {"name": pkg_names[d], "deps": versions, "scripts": sorted((pj.get("scripts") or {}))
                       if isinstance(pj.get("scripts"), dict) else []}
    # manifests of the other ecosystems: a file belongs to the nearest manifest of its own language
    lang_pkg_dirs: dict[str, list[str]] = defaultdict(list)
    other_pkg_names: dict[str, str] = {}  # a JS file under a pyproject.toml keeps its package.json name
    for d, m in sorted(manifests.items()):
        for fn in (sr.manifests or {}).get(d, []):
            for lg in langs.manifest_langs(fn):
                if d not in lang_pkg_dirs[lg.name]:
                    lang_pkg_dirs[lg.name].append(d)
        other_pkg_names[d] = m["name"]
        pkg_deps[d] = pkg_deps.get(d, set()) | set(m["deps"])
        cur = packages.setdefault(d, {"name": m["name"], "deps": {}, "scripts": []})
        cur["deps"] = {**m["deps"], **cur["deps"]}
        cur["ecosystem"] = m["ecosystem"]
    pkg_dir_cache: dict[str, str] = {}

    def package_dir_of(f: str) -> str:
        if f not in pkg_dir_cache:
            lg = langs.of(f)
            if lg is None or lg is langs.JS:
                pkg_dir_cache[f] = _nearest_package(f, pkg_dirs)
            else:
                dirs = lang_pkg_dirs.get(lg.name, [])
                if lg.name == "cpp":
                    dirs = dirs or lang_pkg_dirs.get("c", [])
                pkg_dir_cache[f] = _nearest_package(f, dirs) if dirs else ""
        return pkg_dir_cache[f]

    # ---- frameworks, routes, API calls
    ctx = Context(cfg.root, files, {p: r for p, r in parsed.items() if p in files}, local_targets, spec_targets,
                  ext_by_file, pkg_deps, package_dir_of, resolver)
    run_frameworks(ctx)
    ctx.entries |= {f for f, r in other.items() if r.get("entry")}
    module_exports = _module_exports(ctx)
    entry_set = entries.find(cfg.root, files, pkg_dirs, resolver, ctx.entries, cfg["entries"])
    layers = classify.classify(sorted(files), ctx.parsed, ctx.facts, cfg["layers"], entry_set,
                               {c.file for c in ctx.api_calls}, package_dir_of)

    route_rows, mount_rows, mw_rows = [], [], []
    for i, r in enumerate(ctx.routes, start=1):
        route_rows.append((i, r.method, r.path, r.file, r.handler_file, r.handler, r.kind, r.line))
        mount_rows += [(i, seq, f, prefix) for seq, (f, prefix) in enumerate(r.mounts)]
        mw_rows += [(i, seq, f, sym, scope) for seq, (f, sym, scope) in enumerate(r.middlewares)]
    call_rows = []
    for c in ctx.api_calls:
        rid = c.route + 1 if c.route is not None else None
        call_rows.append((c.file, c.symbol, c.method, c.url, rid, c.line))
        if c.route is not None:
            r = ctx.routes[c.route]
            add_edge(c.file, r.handler_file or r.file, "calls_api", [f"{r.method} {r.path}"], None, c.line)

    ref_rows = sorted(_symbol_refs(ctx))
    inherit_rows = sorted(_inherits(ctx), key=lambda r: tuple(x or "" for x in r))

    file_rows = []
    for p, fi in sorted(sr.files.items()):
        pkg = (pkg_names if langs.is_js(p) else other_pkg_names).get(package_dir_of(p), "")
        file_rows.append((p, fi.hash, fi.mtime, fi.size, fi.loc, fi.tokens, layers.get(p), int(p in entry_set), pkg))

    edge_rows = [(s, d, k, ",".join(sorted(n)), via, line)
                 for (s, d, k, via), (n, line) in sorted(edges.items(), key=lambda kv: (*kv[0][:3], kv[0][3] or ""))]
    tables = {
        "files": file_rows,
        "symbols": symbols,
        "edges": edge_rows,
        "externals": sorted((*k, ln) for k, ln in externals.items()),
        "unresolved": unresolved,
        "routes": route_rows,
        "route_mounts": mount_rows,
        "route_middlewares": mw_rows,
        "api_calls": call_rows,
        "symbol_refs": ref_rows,
        "inherits": inherit_rows,
    }
    return tables, {"edges": len(edge_rows), "unresolved": len(unresolved), "routes": len(route_rows),
                    "packages": packages, "module_exports": module_exports}


def _module_exports(ctx: Context) -> list[str]:
    """Files of the providers a Nest @Module exports (its public API for other modules)."""
    out: set[str] = set()
    for f, rec in ctx.parsed.items():
        for s in rec.get("symbols", []):
            for d in s.get("decorators") or []:
                if d.get("name") != "Module" or not d.get("args") or d["args"][0].get("t") != "obj":
                    continue
                exp = d["args"][0]["props"].get("exports") or {}
                for item in exp.get("items", []) if exp.get("t") == "arr" else []:
                    if item.get("t") == "id":
                        hit = ctx.resolve_local(f, item["v"])
                        if hit and hit[0] in ctx.files:
                            out.add(hit[0])
    return sorted(out)


def _add_symbols(rows: list[tuple], path: str, rec: dict) -> None:
    for s in rec.get("symbols", []):
        rows.append((path, s["name"], s["kind"], s.get("line"), s.get("end"), int(bool(s.get("exported"))), None,
                     s.get("extends"), ",".join(s.get("implements") or []), _deco_names(s),
                     int(bool(s.get("abstract")))))
        for m in s.get("methods", []) or []:
            rows.append((path, f"{s['name']}.{m['name']}", "method", m.get("line"), None, 0, s["name"],
                         None, "", _deco_names(m), 0))


def _deco_names(s: dict) -> str:
    return ",".join(dict.fromkeys(d.get("name", "?") for d in s.get("decorators") or []))


def _inherits(ctx: Context) -> set[tuple]:
    """(file, symbol, extends|implements, ref_file, ref_symbol, ref_pkg): where each base class / interface lives."""
    rows: set[tuple] = set()
    for f, rec in ctx.parsed.items():
        for s in rec.get("symbols", []):
            bases = ([("extends", s["extends"])] if s.get("extends") else []) +                 [("implements", n) for n in s.get("implements") or []]
            for kind, name in bases:
                hit = ctx.resolve_local(f, name)
                if hit:
                    rows.add((f, s["name"], kind, hit[0], hit[1], None))
                else:
                    rows.add((f, s["name"], kind, None, name, ctx.external_of(f, name)))
    return rows


def _symbol_refs(ctx: Context) -> set[tuple]:
    """(file, symbol, ref_file, ref_symbol) for top-level symbols and methods; `this.x` uses the injected type."""
    rows: set[tuple] = set()

    def method_of(hit: tuple[str, str] | None, m: str, depth: int = 0) -> tuple[str, str] | None:
        """(file, Class|obj|instance) + member -> (file, 'Class.m') when that method exists."""
        if not hit or depth > 3:
            return None
        fi, name = hit
        s = ctx.symbol(fi, name)
        if s and any(x["name"] == m for x in s.get("methods", []) or []):
            return fi, f"{name}.{m}"
        factory = ctx.rec(fi).get("vars", {}).get(name)  # const users = new UserService()
        if factory and "." not in factory:
            return method_of(ctx.resolve_local(fi, factory), m, depth + 1)
        return None

    def add(f: str, sym: str, names, members=(), ctor=None, cls: str | None = None, this_refs=()) -> None:
        consumed: set[str] = set()
        for mem in members:
            parts = mem.split(".")
            if len(parts) < 2 or parts[0] == "this" and len(parts) < 3:
                continue
            if parts[0] == "this":
                t = (ctor or {}).get(parts[1])
                hit = method_of(ctx.resolve_local(f, t), parts[2]) if t else None
            else:
                hit = method_of(ctx.resolve_local(f, parts[0]), parts[1])
                if hit:
                    consumed.add(parts[0])
            if hit and hit != (f, sym):
                rows.add((f, sym, hit[0], hit[1]))
        if cls:
            own = ctx.symbol(f, cls) or {}
            own_methods = {x["name"] for x in own.get("methods", []) or []}
            for t in this_refs:
                if t in own_methods and f"{cls}.{t}" != sym:
                    rows.add((f, sym, f, f"{cls}.{t}"))
        for n in set(names) - consumed:
            hit = ctx.resolve_local(f, n)
            if hit and hit != (f, sym):
                rows.add((f, sym, hit[0], hit[1]))

    for f, rec in ctx.parsed.items():
        for s in rec.get("symbols", []):
            add(f, s["name"], s.get("refs", []), s.get("members", []))
            ctor = s.get("ctor_types") or {}
            for m in s.get("methods", []) or []:
                injected = [ctor[t] for t in m.get("this_refs", []) if t in ctor
                            and not any(x.startswith(f"this.{t}.") for x in m.get("members", []))]
                add(f, f"{s['name']}.{m['name']}", list(m.get("refs", [])) + injected, m.get("members", []),
                    ctor, s["name"], m.get("this_refs", []))
    return rows
