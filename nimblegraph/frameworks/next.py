"""Next.js: app router (page/route/layout), pages router (pages/, pages/api/) and middleware.ts matcher."""

from __future__ import annotations

import posixpath
import re

from nimblegraph.frameworks import Context, Route, join_paths

PAGE_EXTS = (".tsx", ".jsx", ".ts", ".js", ".mdx")
ROUTE_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def next_roots(ctx: Context) -> list[str]:
    roots = set()
    for d, deps in ctx.package_deps.items():
        if "next" in deps:
            roots.add(d)
    for f in ctx.files:
        if re.search(r"(^|/)next\.config\.[cm]?[jt]s$", f):
            roots.add(posixpath.dirname(f))
    return sorted(roots)


def segment_path(segs: list[str]) -> str | None:
    """Folder segments -> URL path; None for private folders (_x)."""
    out = []
    for s in segs:
        if s.startswith("_"):
            return None
        if s.startswith("(") and s.endswith(")"):
            continue
        if s.startswith("@"):
            continue
        m = re.fullmatch(r"\[\[?\.\.\.(.+?)\]?\]", s)
        if m:
            out.append("*" + m.group(1))
            continue
        m = re.fullmatch(r"\[(.+)\]", s)
        if m:
            out.append(":" + m.group(1))
            continue
        out.append(s)
    return join_paths(*out)


def _sub(root: str, rel: str) -> str:
    return f"{root}/{rel}" if root else rel


def matcher_regex(pattern: str) -> re.Pattern | None:
    """path-to-regexp style matcher (`/api/:path*`) -> regex."""
    p = pattern
    p = re.sub(r"/:(\w+)\*", r"(?:/.*)?", p)
    p = re.sub(r"/:(\w+)\+", r"/.+", p)
    p = re.sub(r"/:(\w+)\?", r"(?:/[^/]+)?", p)
    p = re.sub(r":(\w+)", r"[^/]+", p)
    try:
        return re.compile(p)
    except re.error:
        return None


def matches(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    sample = re.sub(r"[:*]\w+", "x", path)
    for pat in patterns:
        rx = matcher_regex(pat)
        if rx is None or rx.fullmatch(sample) or rx.fullmatch(path):
            return True
    return False


def detect(ctx: Context) -> None:
    for root in next_roots(ctx):
        before = len(ctx.routes)
        for app_dir in (_sub(root, "app"), _sub(root, "src/app")):
            _app_router(ctx, app_dir)
        for pages_dir in (_sub(root, "pages"), _sub(root, "src/pages")):
            _pages_router(ctx, pages_dir)
        new_routes = ctx.routes[before:]
        for mw in (_sub(root, "middleware.ts"), _sub(root, "middleware.js"), _sub(root, "src/middleware.ts"),
                   _sub(root, "src/middleware.js")):
            if mw not in ctx.files:
                continue
            ctx.fact(mw, "middleware")
            ctx.entries.add(mw)
            patterns = _matcher(ctx, mw)
            for r in new_routes:
                if matches(r.path, patterns):
                    r.middlewares.insert(0, (mw, "middleware", "next"))


def _matcher(ctx: Context, mw: str) -> list[str]:
    cfg = ctx.rec(mw).get("consts", {}).get("config")
    if not cfg or cfg.get("t") != "obj":
        return []
    m = cfg["props"].get("matcher")
    if not m:
        return []
    items = m["items"] if m["t"] == "arr" else [m]
    out = []
    for it in items:
        if it["t"] in ("str", "tpl"):
            out.append(it["v"])
        elif it["t"] == "obj" and it["props"].get("source", {}).get("t") == "str":
            out.append(it["props"]["source"]["v"])
    return out


def _app_router(ctx: Context, app_dir: str) -> None:
    prefix = app_dir + "/"
    for f in sorted(ctx.files):
        if not f.startswith(prefix):
            continue
        rel = f[len(prefix):]
        parts = rel.split("/")
        name, ext = posixpath.splitext(parts[-1])
        if ext not in PAGE_EXTS:
            continue
        path = segment_path(parts[:-1])
        if path is None:
            continue
        if name == "page":
            ctx.routes.append(Route("PAGE", path, f, 1, f, "default", "page", "next"))
            ctx.fact(f, "page")
            ctx.entries.add(f)
        elif name == "layout":
            ctx.routes.append(Route("PAGE", path, f, 1, f, "default", "layout", "next"))
            ctx.fact(f, "layout")
            ctx.entries.add(f)
        elif name == "route":
            exported = {e["name"] for e in ctx.rec(f).get("exports", [])}
            methods = [m for m in ROUTE_METHODS if m in exported] or ["ALL"]
            for m in methods:
                line = next((e["line"] for e in ctx.rec(f).get("exports", []) if e["name"] == m), 1)
                ctx.routes.append(Route(m, path, f, line, f, m if m != "ALL" else None, "api", "next"))
            ctx.fact(f, "api-route")
            ctx.entries.add(f)
        elif name in ("loading", "error", "not-found", "template", "default", "global-error"):
            ctx.fact(f, "page", strong=False)


def _pages_router(ctx: Context, pages_dir: str) -> None:
    prefix = pages_dir + "/"
    for f in sorted(ctx.files):
        if not f.startswith(prefix):
            continue
        rel = f[len(prefix):]
        parts = rel.split("/")
        name, ext = posixpath.splitext(parts[-1])
        if ext not in PAGE_EXTS or name.startswith("_"):
            continue
        segs = parts[:-1] + ([] if name == "index" else [name])
        path = segment_path(segs)
        if path is None:
            continue
        if parts[0] == "api":
            ctx.routes.append(Route("ALL", path, f, 1, f, "default", "api", "next"))
            ctx.fact(f, "api-route")
        else:
            ctx.routes.append(Route("PAGE", path, f, 1, f, "default", "page", "next"))
            ctx.fact(f, "page")
        ctx.entries.add(f)
