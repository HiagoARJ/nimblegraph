"""TanStack Router: file routes (createFileRoute('/_app/users/$id/')), the root route and code routes (createRoute)."""

from __future__ import annotations

import posixpath

from nimblegraph.frameworks import Context, Route, arg_str, join_paths

FILE_ROUTE = ("createFileRoute", "createLazyFileRoute")
ROOT_ROUTE = ("createRootRoute", "createRootRouteWithContext")


def route_path(raw: str) -> str:
    """'/_app/advisories/$id/' -> '/advisories/:id' (pathless `_layout` and `(group)` segments dropped)."""
    out = []
    for s in raw.split("/"):
        if not s or s.startswith("_") or (s.startswith("(") and s.endswith(")")):
            continue
        if s == "$":
            out.append("*")
        elif s.startswith("$"):
            out.append(":" + s[1:])
        else:
            out.append(s)
    return join_paths(*out)


def _is_layout(ctx: Context, f: str, raw: str) -> bool:
    last = [s for s in raw.split("/") if s][-1:] or [""]
    if last[0].startswith("_"):
        return True  # pathless layout route (_app, _auth)
    stem = posixpath.splitext(f)[0].removesuffix(".lazy")
    return any(x.startswith(stem + "/") for x in ctx.files) and not raw.endswith("/")


def detect(ctx: Context) -> None:
    for f in sorted(ctx.files):
        for c in ctx.rec(f).get("calls", []):
            callee = c.get("callee")
            args = c.get("args") or []
            if callee in FILE_ROUTE:
                raw = arg_str(args[0]) if args else None
                if raw is None:
                    continue
                kind = "layout" if _is_layout(ctx, f, raw) else "page"
                ctx.routes.append(Route("PAGE", route_path(raw), f, c.get("line", 1), f, "Route", kind, "tanstack-router"))
                ctx.fact(f, kind)
                ctx.entries.add(f)
            elif callee in ROOT_ROUTE:
                ctx.routes.append(Route("PAGE", "/", f, c.get("line", 1), f, "Route", "layout", "tanstack-router"))
                ctx.fact(f, "layout")
                ctx.entries.add(f)
            elif callee == "createRoute" and args and args[0].get("t") == "obj":
                props = args[0]["props"]
                raw = arg_str(props.get("path"))
                if raw is None:
                    continue
                comp = props.get("component")
                target, handler = f, "Route"
                if comp and comp.get("t") == "id":
                    hit = ctx.resolve_local(f, comp["v"])
                    if hit:
                        target, handler = hit
                ctx.routes.append(Route("PAGE", route_path(raw), f, c.get("line", 1), target, handler, "page", "tanstack-router"))
                ctx.fact(target, "page", strong=False)
