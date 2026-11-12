"""JSON payloads of the UI API (pure functions over the loaded graph and notes)."""

from __future__ import annotations

from collections import defaultdict

from nimblegraph import query
from nimblegraph.notes import SKIP_LAYERS, Notes, chain_key
from nimblegraph.query import Graph


def _note_brief(notes: Notes, path: str) -> dict | None:
    n = notes.files.get(path)
    if not n:
        return None
    return {"title": n.get("title", ""), "summary": n.get("summary", ""), "scope": n.get("scope", "")}


def graph_payload(g: Graph, notes: Notes, root_name: str, rules_cfg: dict | None = None) -> dict:
    from nimblegraph import arch
    a = arch.model(g, rules_cfg or {})
    nodes = []
    importers = {p: len(g.importers(p)) for p in g.files}
    route_count: dict[str, int] = defaultdict(int)
    for r in g.routes.values():
        route_count[r.target] += 1
    for p, f in sorted(g.files.items()):
        nodes.append({
            "id": p, "layer": f.layer or "module", "loc": f.loc, "tokens": f.tokens, "importers": importers[p],
            "entry": f.is_entry, "pkg": f.pkg, "note": _note_brief(notes, p), "stale": notes.stale(p),
            "routes": route_count.get(p, 0), "hub": g.is_hub(p),
            "context": a.context.get(p), "ring": a.ring.get(p), "roles": sorted(a.roles.get(p, ())),
        })
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    for src, edges in g.out.items():
        for e in edges:
            if e.dst in g.files and src in g.files:
                pairs[(src, e.dst)].add(e.kind)
    links = [{"source": s, "target": d, "kinds": sorted(k)} for (s, d), k in sorted(pairs.items())]
    routes = [{"id": r.id, "method": r.method, "path": r.path, "kind": r.kind, "file": r.file, "target": r.target,
               "handler": r.handler} for r in sorted(g.routes.values(), key=lambda r: (r.kind != "api", r.path, r.method))]
    chains = []
    for key, c in sorted(notes.chains.items()):
        files = query.chain_files(g, key, 4, notes)
        chains.append({"key": key, "title": c.get("title", ""), "summary": c.get("summary", ""), "flow": c.get("flow", ""),
                       "state": notes.chain_state(key, files), "files": sorted(files)})
    scopes: dict[str, list[str]] = defaultdict(list)
    for key, n in notes.files.items():
        if n.get("scope") and "#" not in key and key in g.files:
            scopes[n["scope"]].append(key)
    describable = [p for p in g.files if g.layer(p) not in SKIP_LAYERS]
    counts = notes.counts(describable)
    return {
        "root": root_name,
        "engine": g.meta.get("engine"),
        "built_at": g.meta.get("built_at"),
        "stats": {"files": len(g.files), "links": len(links), "routes": len(g.routes),
                  "notes": counts["described"], "stale": counts["stale"], "describable": len(describable)},
        "nodes": nodes,
        "links": links,
        "routes": routes,
        "chains": chains,
        "scopes": [{"scope": s, "files": sorted(fs)} for s, fs in sorted(scopes.items())],
    }


def file_payload(g: Graph, notes: Notes, path: str) -> dict | None:
    if path not in g.files:
        hits = g.find_files(path)
        if len(hits) != 1:
            return None
        path = hits[0]
    f = g.files[path]
    syms = g.symbols.get(path, [])
    defined = g.routes_of_file(path)
    return {
        "path": path, "layer": f.layer or "module", "loc": f.loc, "tokens": f.tokens, "entry": f.is_entry, "pkg": f.pkg,
        "note": notes.files.get(path), "stale": notes.stale(path),
        "symbol_notes": {k.split("#", 1)[1]: n for k, n in notes.files.items() if k.startswith(path + "#")},
        "exports": [{"name": s["name"], "kind": s["kind"], "line": s["line"]} for s in syms if s["exported"] and not s["parent"]],
        "symbols": [{"name": s["name"], "kind": s["kind"], "line": s["line"], "parent": s["parent"]} for s in syms],
        "imports": [{"path": e.dst, "kind": e.kind, "names": e.names, "via": e.via, "layer": g.layer(e.dst),
                     "note": _note_brief(notes, e.dst)}
                    for e in sorted(g.out.get(path, []), key=lambda e: e.dst) if e.kind not in ("renders", "calls_api")],
        "importers": [{"path": p, "layer": g.layer(p), "note": _note_brief(notes, p)}
                      for p in sorted({e.src for e in g.inn.get(path, []) if e.kind not in ("renders", "calls_api")}
                                      | g.via_users(path))],
        "routes": [{"id": r.id, "method": r.method, "path": r.path, "kind": r.kind, "handler": r.handler} for r in defined],
        "calls": [{"method": c.method, "url": c.url, "symbol": c.symbol, "route_id": c.route_id,
                   "target": g.routes[c.route_id].target if c.route_id else None} for c in g.calls_from(path)],
        "callers": [{"path": c.file, "symbol": c.symbol, "route": g.routes[c.route_id].label}
                    for r in defined for c in g.calls_to(r.id)],
        "externals": sorted(g.externals.get(path, ())),
        "unresolved": [{"spec": s, "line": ln} for s, ln in g.unresolved.get(path, [])],
    }


def chain_payload(g: Graph, notes: Notes, target: str, depth: int = 4) -> dict:
    t = target.strip()
    is_route = t.startswith("/") or (" /" in t and t.split(" ", 1)[0].isalpha())
    if t in g.files:
        ch = query.flow(g, t, depth)
    else:
        ch = query.flow(g, t, depth) if is_route else query.analyze(g, t, depth, notes)
    flow: list[dict] = []
    shown: set[str] = set()

    def visit(f: str, depth_: int, label: str | None) -> None:
        if f in shown:
            flow.append({"path": f, "depth": depth_, "label": label, "ref": True})
            return
        shown.add(f)
        n = notes.files.get(f)
        flow.append({"path": f, "depth": depth_, "label": label, "ref": False, "layer": g.layer(f),
                     "title": n.get("title") if n else None, "stale": notes.stale(f)})
        for dst, lab in sorted(ch.links.get(f, []), key=lambda x: (x[1] is not None, x[0])):
            if dst in ch.files:
                visit(dst, depth_ + 1, lab)

    for r in query.roots_of(g, ch):
        visit(r, 0, None)
    for f in sorted(ch.files - shown):
        visit(f, 0, None)
    if is_route:
        k = chain_key(t)
        found = (k, notes.chains[k]) if k in notes.chains else None
    else:
        found = notes.find_chain(t)
    chain_note = None
    if found:
        key, n = found
        chain_note = {"key": key, "title": n.get("title"), "summary": n.get("summary"), "flow": n.get("flow"),
                      "state": notes.chain_state(key, {f for f in ch.files if g.layer(f) not in SKIP_LAYERS})}
    links = [{"source": s, "target": d, "label": lab} for s, ls in ch.links.items() for d, lab in ls if d in ch.files]
    routes = [r.id for r in g.routes.values() if r.target in ch.files]
    return {"target": t, "files": sorted(ch.files), "seeds": sorted(ch.seeds), "shared": sorted(ch.shared),
            "tests": sorted(ch.tests), "links": links, "flow": flow, "chain_note": chain_note, "routes": routes,
            "missing": list(notes.missing(sorted(ch.files), g))}


def endpoint_payload(g: Graph, notes: Notes, route: str, depth: int = 4) -> dict:
    rs = g.find_routes(route)
    if len(rs) != 1:
        return {"matches": [{"id": r.id, "method": r.method, "path": r.path, "target": r.target, "handler": r.handler}
                            for r in rs]}
    r = rs[0]
    v = query.endpoint(g, r, depth)

    def mw(f, sym, scope):
        name, _, owner = (sym or "").partition("|")
        return {"file": f or owner or None, "symbol": name, "scope": scope, "external": f is None,
                "title": (notes.get(f, name) or notes.files.get(f) or {}).get("title") if f else None}

    steps = []
    for f, p in r.mounts:  # module and controller in the same file: one mount step
        if steps and steps[-1]["file"] == f:
            steps[-1]["prefix"] = steps[-1]["prefix"] or p
        else:
            steps.append({"step": "mount", "file": f, "prefix": p})
    steps += [{"step": "middleware", **mw(*m)} for m in r.middlewares if m[2] != "validation"]
    steps += [{"step": "validation", **mw(*m)} for m in r.middlewares if m[2] == "validation"]
    steps.append({"step": "handler", "file": r.target, "symbol": r.handler,
                  "title": (notes.get(r.target, r.handler) or notes.files.get(r.target) or {}).get("title")})
    steps += [{"step": "down", "file": f, "symbol": s, "depth": d,
               "title": (notes.get(f, s) or notes.files.get(f) or {}).get("title")} for d, f, s in v.down]
    return {
        "route": {"id": r.id, "method": r.method, "path": r.path, "kind": r.kind, "file": r.file, "target": r.target,
                  "handler": r.handler},
        "steps": steps, "shared": sorted(v.shared),
        "front": [[{"file": f, "symbol": s} for f, s in path] for path in v.front],
        "tests": sorted(v.tests), "externals": sorted(v.externals), "files": sorted(v.files),
    }


def search_payload(g: Graph, notes: Notes, q: str, limit: int = 50) -> dict:
    low = q.lower().strip()
    if not low:
        return {"files": [], "symbols": [], "routes": [], "notes": []}
    files = [p for p in sorted(g.files) if low in p.lower()][:limit]
    symbols = [{"file": p, "name": s["name"], "kind": s["kind"], "line": s["line"]}
               for p in sorted(g.symbols) for s in g.symbols[p] if low in s["name"].lower()][:limit]
    routes = [{"id": r.id, "method": r.method, "path": r.path, "target": r.target}
              for r in g.routes.values() if low in r.path.lower()][:limit]
    note_hits = [{"key": k, "title": n.get("title"), "summary": n.get("summary")} for k, n in notes.search(q)][:limit]
    return {"files": files, "symbols": symbols, "routes": routes, "notes": note_hits}


def arch_payload(cfg, g: Graph) -> dict:
    """Architecture view: contexts, context map, aggregates, rule violations, module metrics, test coverage."""
    from nimblegraph import arch, metrics, rules, testmap
    rules_cfg = arch.load_rules(cfg)
    a = arch.model(g, rules_cfg)
    res = rules.run(cfg, g, rules_cfg)
    links = arch.context_links(g, a)
    contexts = []
    for c in a.contexts():
        files = a.files_of(c)
        roles: dict[str, int] = defaultdict(int)
        for f in files:
            for r in a.roles.get(f, ()):
                roles[r] += 1
        contexts.append({"name": c, "how": a.context_how.get(c), "root": a.context_root.get(c, ""), "files": files,
                         "roles": dict(sorted(roles.items()))})
    by = "context" if len([c for c in a.contexts() if not arch.is_shared(c)]) >= 2 else "folder"
    mods = metrics.module_metrics(g, a, by, 3)
    cov = testmap.coverage(g, a)
    return {
        "presets": res.presets, "auto": res.auto, "rule_errors": res.errors,
        "summary": {"new": res.counts("new"), "known": res.counts("known"), "resolved": len(res.resolved)},
        "violations": [v.as_dict() for v in res.violations[:2000]],
        "contexts": contexts,
        "links": [{"source": s, "target": d, "imports": lk.imports, "http": lk.http,
                   "leaks": [{"src": x, "dst": y, "line": ln} for x, y, ln in lk.leaks[:200]]}
                  for (s, d), lk in sorted(links.items())],
        "aggregates": [{"root": ag.root, "file": ag.root_file, "context": ag.context, "members": ag.members}
                       for ag in arch.aggregates(g, a)],
        "metrics": {"by": by, "modules": [
            {"name": m.name, "files": m.files, "loc": m.loc, "ca": m.ca, "ce": m.ce, "i": round(m.instability, 2),
             "a": round(m.abstractness, 2), "d": round(m.distance, 2), "zone": m.zone} for m in mods]},
        "gods": [{"file": f, "importers": i, "deps": o, "loc": loc} for f, i, o, loc in metrics.god_files(g)],
        "tests": {
            "count": len(cov.tests),
            "endpoints": [{"label": r.label, "target": r.target, "e2e": e2e, "unit": unit} for r, e2e, unit in cov.endpoints],
            "untested_roles": {role: [{"file": f, "symbol": s} for f, s, t in items if not t]
                               for role, items in cov.by_role.items()},
            "contexts": {c: {"reached": x, "files": n} for c, (x, n) in cov.contexts.items()},
        },
    }
