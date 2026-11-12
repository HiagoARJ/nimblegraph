"""React Router: nested <Route path element>, createBrowserRouter/useRoutes objects, Component and lazy()."""

from __future__ import annotations

from nimblegraph.frameworks import Context, Route, join_paths


def _target(ctx: Context, f: str, comp: str) -> tuple[str, str] | None:
    root = comp.split(".")[0]
    dyn = ctx.rec(f).get("dyn_locals", {}).get(root)
    if dyn:
        t = ctx.spec_target(f, dyn)
        if t:
            return t, "default"
    return ctx.resolve_local(f, root)


def _walk(ctx: Context, f: str, node: dict, parent: str, out: list[Route]) -> None:
    path = node.get("path")
    if path is None:
        full = parent or "/"
    elif path.startswith("/"):
        full = join_paths(path)
    else:
        full = join_paths(parent, path)
    target = None
    handler = None
    for comp in node.get("comps") or []:
        hit = _target(ctx, f, comp)
        if hit:
            target, handler = hit[0], comp
            break
    if target is None and node.get("lazy"):
        target = ctx.spec_target(f, node["lazy"])
        handler = "lazy"
    if target is None and node.get("comps"):
        target, handler = f, node["comps"][0]
    if target is not None and (path is not None or node.get("index") or node.get("children")):
        kind = "layout" if node.get("children") else "page"  # an element with child routes renders an <Outlet/>
        out.append(Route("PAGE", full, f, 1, target, handler, kind, "react-router"))
    for child in node.get("children") or []:
        _walk(ctx, f, child, full, out)


def detect(ctx: Context) -> None:
    for f in sorted(ctx.files):
        objs = ctx.rec(f).get("route_objects") or []
        if not objs:
            continue
        found: list[Route] = []
        for node in objs:
            _walk(ctx, f, node, "", found)
        # the same tree declared twice (JSX + objects) is kept once
        seen = set()
        for r in found:
            key = (r.path, r.handler_file, r.kind)
            if key in seen:
                continue
            seen.add(key)
            ctx.routes.append(r)
            if r.handler_file and r.handler_file != f:
                ctx.fact(r.handler_file, r.kind, strong=False)
        ctx.fact(f, "route", strong=False)
