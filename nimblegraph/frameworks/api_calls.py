"""Front-end HTTP calls (fetch, axios, ky, $fetch, SWR, API clients) matched to backend routes.
Parameter segments match anything, and a call may omit a prefix such as /api (baseURL)."""

from __future__ import annotations

import functools
import re

from nimblegraph.frameworks import HTTP_METHODS, ApiCall, Context, Route, arg_str

FETCHERS = {"fetch", "$fetch", "ofetch", "ky", "axios"}


def normalize_url(url: str) -> str | None:
    u = url.strip()
    u = re.sub(r"^[a-z]+://[^/]+", "", u)  # origin
    u = u.split("?")[0].split("#")[0]
    if not u:
        return "/"
    if u.startswith(":param"):  # `${base}${path}` -> unknown
        u = u[len(":param"):]
        if not u.startswith("/"):
            return None
    if not u.startswith("/"):
        if re.match(r"^[\w.-]+(/|$)", u):
            u = "/" + u
        else:
            return None
    u = re.sub(r"(?<=[^/]):param", "", u)  # `/users${query}` -> /users: a query string, not a path segment
    return re.sub(r"/+", "/", u).rstrip("/") or "/"


@functools.lru_cache(maxsize=1 << 16)
def segments(path: str) -> tuple[str, ...]:  # memoized: match() compares every call with every route
    return tuple(s for s in path.split("/") if s)


def _seg_match(call: str, route: str) -> int:
    """3 literal match, 2 parameter on both sides, 1 parameter on one side, 0 mismatch."""
    if call == route or call.lower() == route.lower():  # Express and ASP.NET match paths case-insensitively
        return 3
    call_param = call.startswith(":") or "${" in call
    route_param = route.startswith((":", "*"))
    if call_param and route_param:
        return 2
    if route_param:  # /users/42 -> /users/:id; but a dynamic id never means the literal /users/report
        return 1
    return 0


ALIGN = {3: 2, 2: 1}  # literal segments weigh more than parameter-to-parameter ones


def score(call_path: str, route_path: str) -> tuple[int, int]:
    """(match kind, alignment): kind 3 exact, 2 the call omits a route prefix (baseURL), 1 the route is a suffix
    of the call. At least one literal segment must match."""
    cs, rs = segments(call_path), segments(route_path)
    if not cs and not rs:
        return 3, 0

    def run(a: list[str], b: list[str]) -> int | None:
        total, literal = 0, False
        for c, r in zip(a, b):
            m = _seg_match(c, r)
            if not m:
                return None
            literal = literal or m == 3
            total += ALIGN.get(m, 0)
        return total if literal else None

    if rs and rs[-1].startswith("*") and len(cs) >= len(rs) - 1:
        aligned = run(cs[:len(rs) - 1], rs[:-1])
        if aligned is not None:
            return 3, aligned
    if len(cs) == len(rs):
        aligned = run(cs, rs)
        if aligned is not None:
            return 3, aligned
    if 0 < len(cs) < len(rs):
        aligned = run(cs, rs[len(rs) - len(cs):])
        if aligned is not None:
            return 2, aligned
    if 0 < len(rs) < len(cs):
        aligned = run(cs[len(cs) - len(rs):], rs)
        if aligned is not None:
            return 1, aligned
    return 0, 0


def match(routes: list[Route], method: str, url: str) -> int | None:
    best, best_key = None, (0, 0, 0)
    for i, r in enumerate(routes):
        if r.kind != "api":
            continue
        if r.method not in (method, "ALL", "USE") and method != "ANY":
            continue
        kind, lit = score(url, r.path)
        if not kind:
            continue
        key = (kind, lit, -len(segments(r.path)))
        if key > best_key:
            best, best_key = i, key
    return best


def _method_from_opts(a: dict | None, default: str = "GET") -> str:
    if a and a.get("t") == "obj":
        m = arg_str(a["props"].get("method"))
        if m:
            return m.upper()
    return default


def _from_call(ctx: Context, f: str, c: dict) -> tuple[str, str] | None:
    args = c.get("args") or []
    callee, prop = c.get("callee"), c.get("prop")
    if callee:
        if callee.startswith("useSWR"):
            url = arg_str(args[0]) if args else None
            if url is None and args and args[0].get("t") == "arr" and args[0]["items"]:
                url = arg_str(args[0]["items"][0])
            return ("GET", url) if url else None
        if callee in FETCHERS:
            if args and args[0].get("t") == "obj":  # axios({ url, method })
                url = arg_str(args[0]["props"].get("url"))
                return (_method_from_opts(args[0]), url) if url else None
            url = arg_str(args[0]) if args else None
            return (_method_from_opts(args[1] if len(args) > 1 else None), url) if url else None
        return None
    if prop in HTTP_METHODS and ctx.call_role(f, c) == "client":
        url = arg_str(args[0]) if args else None
        m = prop.upper()
        return ("DELETE" if m == "DEL" else m, url) if url else None
    if prop in ("request",) and ctx.obj_role(f, c["obj"]) == "client" and args and args[0].get("t") == "obj":
        url = arg_str(args[0]["props"].get("url"))
        return (_method_from_opts(args[0]), url) if url else None
    return None


def _dynamic_prefix(a: dict | None) -> str | None:
    """The literal prefix of a URL argument whose path comes from outside: `/api${path}` -> '/api', `path` -> ''."""
    if not a:
        return None
    if a.get("t") == "id":
        return ""
    if a.get("t") == "tpl" and a["v"].endswith(":param"):
        prefix = a["v"][:-len(":param")]
        # `/api${path}` is a wrapper; `/orders/${id}` is a call with an id
        if prefix == "" or prefix.startswith("/") and not prefix.endswith("/") and ":param" not in prefix:
            return prefix
    return None


def wrappers(ctx: Context) -> dict[tuple[str, str], tuple[str, str]]:
    """(file, function) -> (prefix, default method) for functions that pass a path argument to fetch/axios/a
    client: `request(path)` doing fetch(`/api${path}`)."""
    out = {}
    for f in sorted(ctx.files):
        for c in ctx.rec(f).get("calls", []):
            sym = c.get("sym")
            if not sym or "." in sym:
                continue
            args = c.get("args") or []
            callee, prop = c.get("callee"), c.get("prop")
            if callee in FETCHERS and args and args[0].get("t") != "obj":
                prefix = _dynamic_prefix(args[0])
                method = _method_from_opts(args[1] if len(args) > 1 else None, "ANY")
            elif prop in HTTP_METHODS and c.get("obj") and ctx.obj_role(f, c["obj"]) == "client" and args:
                prefix = _dynamic_prefix(args[0])
                method = "DELETE" if prop.upper() == "DEL" else prop.upper()
            else:
                continue
            if prefix is not None:
                out[(f, sym)] = (prefix, method)
    return out


def detect(ctx: Context) -> None:
    wraps = wrappers(ctx)
    for f in sorted(ctx.files):
        for c in ctx.rec(f).get("calls", []):
            if (f, c.get("sym")) in wraps and (c.get("callee") in FETCHERS or c.get("prop") in HTTP_METHODS):
                args = c.get("args") or []
                if args and _dynamic_prefix(args[0]) is not None:
                    continue  # the wrapper's own fetch: its callers are the real calls
            hit = _from_call(ctx, f, c)
            if not hit and c.get("callee") and not c.get("obj") and wraps:
                hit = _from_wrapper(ctx, f, c, wraps)
            if not hit:
                continue
            method, raw = hit
            url = normalize_url(raw)
            if url is None:
                continue
            call = ApiCall(f, c.get("sym"), method, url, c.get("line", 0))
            call.route = match(ctx.routes, method, url)
            ctx.api_calls.append(call)


def _from_wrapper(ctx: Context, f: str, c: dict, wraps: dict) -> tuple[str, str] | None:
    hit = ctx.resolve_local(f, c["callee"])
    w = wraps.get(hit) if hit else None
    if not w:
        return None
    args = c.get("args") or []
    path = arg_str(args[0]) if args else None
    if path is None or not path.startswith("/"):
        return None
    prefix, default = w
    method = _method_from_opts(args[1] if len(args) > 1 else None, "GET" if default == "ANY" else default)
    return method, prefix + path
