"""Python web frameworks: FastAPI (`@router.get`, APIRouter(prefix=...), include_router), Flask (`@app.route`,
Blueprint url_prefix, register_blueprint), Django (`urls.py`: path/re_path, include(), class-based views) and
Django REST framework routers (`router.register("users", UserViewSet)`)."""

from __future__ import annotations

import posixpath

from nimblegraph.frameworks import Context, Route, arg_str, colon_params, is_hole, join_paths, prop, strs

VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "head": "HEAD",
         "options": "OPTIONS", "api_route": "ALL", "route": "ALL", "websocket": "WS", "api_view": None}
ROUTER_FACTORIES = {"FastAPI": "fastapi", "APIRouter": "fastapi", "Flask": "flask", "Blueprint": "flask",
                    "Quart": "flask", "Sanic": "flask", "Starlette": "fastapi"}
DRF = {"DefaultRouter", "SimpleRouter", "ExtendedSimpleRouter", "NestedSimpleRouter"}


def _routers(ctx: Context, f: str, rec: dict) -> dict[str, tuple[str, str]]:
    """var -> (framework, own prefix)."""
    out = {}
    for a in rec.get("assigns", []):
        fac = _factory(ctx, f, a.get("callee") or a.get("prop") or "")
        if fac in ROUTER_FACTORIES:
            last = (a.get("args") or [None])[-1]
            pre = arg_str(prop(last, "prefix", "url_prefix")) or ""
            out[a["name"]] = (ROUTER_FACTORIES[fac], "" if is_hole(pre) else pre)
        elif fac in DRF:
            out[a["name"]] = ("drf", "")
    return out


def _factory(ctx: Context, f: str, name: str, depth: int = 0) -> str:
    """UserAPIRouter -> APIRouter, NetBoxRouter -> DefaultRouter: a project subclass reads as its framework base."""
    if name in ROUTER_FACTORIES or name in DRF or depth > 4 or not name:
        return name
    hit = ctx.resolve_local(f, name.rsplit(".", 1)[-1])
    if not hit:
        return name
    s = ctx.symbol(hit[0], hit[1])
    base = (s or {}).get("extends")
    return _factory(ctx, hit[0], base, depth + 1) if base else name


def _target(ctx: Context, f: str, a: dict | None) -> tuple[str, str] | None:
    """users.router / router (imported) -> (file, var)."""
    if not a:
        return None
    if a.get("t") == "member":
        mod, _, var = a["v"].rpartition(".")
        hit = ctx.resolve_local(f, mod)
        if hit:
            return hit[0], var
    if a.get("t") == "id":
        hit = ctx.resolve_local(f, a["v"])
        if hit:
            return hit[0], hit[1] if hit[1] != "*" else a["v"]
        return f, a["v"]
    return None


def detect(ctx: Context) -> None:
    routers = {f: _routers(ctx, f, rec) for f, rec in ctx.parsed.items()}
    # mounts: include_router / register_blueprint give a router (file, var) an outer prefix
    mounts: dict[tuple[str, str], list[tuple[str, str, str]]] = {}  # (file, var) -> [(file, parent var, prefix)]
    for f, rec in ctx.parsed.items():
        for c in rec.get("calls", []):
            if c.get("prop") in ("include_router", "register_blueprint", "include_app") and c.get("args"):
                tgt = _target(ctx, f, c["args"][0])
                if tgt:
                    pre = arg_str(prop(c["args"][-1], "prefix", "url_prefix")) or ""
                    mounts.setdefault(tgt, []).append((f, c.get("obj") or "", "" if is_hole(pre) else pre))

    def chains(f: str, var: str, depth: int = 0) -> list[list[tuple[str, str]]]:
        """Every mount chain [(file, prefix), ...] outermost first for a router var."""
        if depth > 6:
            return [[]]
        ups = mounts.get((f, var))
        if not ups:
            return [[]]
        out = []
        for pf, pvar, pre in ups:
            parent_pre = routers.get(pf, {}).get(pvar, ("", ""))[1]
            for ch in chains(pf, pvar, depth + 1):
                out.append(ch + [(pf, join_paths(parent_pre, pre))])
        return out

    included = _included(ctx)
    for f, rec in sorted(ctx.parsed.items()):
        rts = routers.get(f, {})
        if rts:
            ctx.fact(f, "route", strong=False)
        # module functions and methods of controller classes (@controller(router) class X: @router.get(...))
        handlers = [(s, s["name"], s["line"]) for s in rec.get("symbols", [])]
        handlers += [(m, f"{s['name']}.{m['name']}", m.get("line", s["line"])) for s in rec.get("symbols", [])
                     for m in s.get("methods") or []]
        for owner, hname, hline in handlers:
            for d in owner.get("decorators") or []:
                full = d.get("full") or d["name"]
                obj, _, meth = full.rpartition(".")
                if obj not in rts or meth not in VERBS:
                    continue
                fw, own = rts[obj]
                path = arg_str((d.get("args") or [None])[0]) or ""
                verbs = [VERBS[meth]] if VERBS[meth] not in ("ALL", None) else \
                    [m.upper() for m in strs(prop((d.get("args") or [None])[-1], "methods"))] or \
                    (["GET"] if fw == "flask" else ["ALL"])
                for ch in chains(f, obj):
                    mounts_ = [(mf, mp) for mf, mp in ch]
                    full_path = join_paths(*[mp for _mf, mp in ch], own, path)
                    for v in verbs:
                        ctx.routes.append(Route(v, colon_params(full_path), f, hline, f, hname, "api", fw,
                                                mounts=mounts_))
                ctx.fact(f, "controller", strong=False)
    drf_mounts: dict[str, list[str]] = {}
    for f, rec in sorted(ctx.parsed.items()):
        if f not in included:
            _django(ctx, f, rec, drf_mounts=drf_mounts)
    for f, rec in sorted(ctx.parsed.items()):
        _drf(ctx, f, rec, routers.get(f, {}), drf_mounts.get(f) or [""])
    for f, rec in ctx.parsed.items():
        if rec.get("entry") or any(a.get("callee") in ("FastAPI", "Flask") for a in rec.get("assigns", [])):
            ctx.entries.add(f)


def _module_file(ctx: Context, dotted: str) -> str | None:
    tail = dotted.replace(".", "/")
    hits = [g for g in ctx.files if g.endswith((tail + ".py", tail + "/__init__.py"))
            and (g == tail + ".py" or g.endswith("/" + tail + ".py") or g.endswith("/" + tail + "/__init__.py")
                 or g == tail + "/__init__.py")]
    return min(hits, key=len) if hits else None


def _django_view(ctx: Context, f: str, a: dict) -> tuple[str, str | None]:
    v = a.get("callee") if a.get("t") == "call" else a.get("v")
    if not v:
        return f, None
    v = v.removesuffix(".as_view")
    mod, _, name = v.rpartition(".")
    hit = ctx.resolve_local(f, mod) if mod else ctx.resolve_local(f, name)
    if hit:
        return hit[0], name if mod else hit[1]
    return f, name


def _django(ctx: Context, f: str, rec: dict, prefix: str = "", seen: frozenset = frozenset(), mounts=(),
            drf_mounts: dict | None = None) -> None:
    """urls.py, recursively through include() with the accumulated prefix (included files start from their root).
    DRF routers reached on the way (`include(router.urls)`, an included `urlpatterns = router.urls`) are recorded in
    drf_mounts with that prefix."""
    drf_mounts = drf_mounts if drf_mounts is not None else {}
    if prefix:
        drf_mounts.setdefault(f, []).append(prefix)
    if not rec.get("urlpatterns"):
        return
    for c in rec.get("calls", []):
        if c.get("callee") not in ("path", "re_path", "url") or len(c.get("args") or []) < 2:
            continue
        raw = arg_str(c["args"][0])
        if raw is None:
            continue
        seg = colon_params(raw)
        target = c["args"][1]
        if target.get("t") == "call" and target.get("callee") == "include":
            inc = arg_str((target.get("args") or [None])[0])
            g = _module_file(ctx, inc) if inc else None
            if inc is None and target.get("args") and target["args"][0].get("t") in ("id", "member"):
                ref = target["args"][0]["v"]
                hit = ctx.resolve_local(f, ref.split(".")[0])
                g = hit[0] if hit else None
                if ref.endswith(".urls"):  # include(router.urls): a DRF router of this file or an imported one
                    drf_mounts.setdefault(g or f, []).append(join_paths(prefix, seg))
                    continue
            if g and g not in seen and g in ctx.parsed:
                _django(ctx, g, ctx.parsed[g], join_paths(prefix, seg), seen | {f}, (*mounts, (f, join_paths(seg))),
                        drf_mounts)
            continue
        hf, h = _django_view(ctx, f, target)
        ctx.routes.append(Route("ALL", join_paths(prefix, seg), f, c.get("line", 0), hf, h, "api", "django",
                                mounts=list(mounts)))
        if hf != f:
            ctx.fact(hf, "controller")
    ctx.fact(f, "route")


def _included(ctx: Context) -> set[str]:
    """urls.py files reached through include(): they are walked from their parent with the prefix."""
    out: set[str] = set()
    for f, rec in ctx.parsed.items():
        for c in rec.get("calls", []) if rec.get("urlpatterns") else []:
            for a in c.get("args") or []:
                if a.get("t") == "call" and a.get("callee") == "include":
                    inc = arg_str((a.get("args") or [None])[0])
                    g = _module_file(ctx, inc) if inc else None
                    if g and g != f:
                        out.add(g)
    return out


def _drf(ctx: Context, f: str, rec: dict, rts: dict, prefixes: list[str]) -> None:
    for c in rec.get("calls", []):
        if c.get("prop") != "register" or rts.get(c.get("obj") or "", ("",))[0] != "drf" or len(c.get("args") or []) < 2:
            continue
        base = arg_str(c["args"][0])
        if base is None:
            continue
        hf, h = _django_view(ctx, f, c["args"][1])
        base = posixpath.join("/", base.strip("^$/"))
        for pre in dict.fromkeys(prefixes):
            for p in (base, base + "/:pk"):
                ctx.routes.append(Route("ALL", join_paths(pre, p), f, c.get("line", 0), hf, h, "api", "drf"))
        ctx.fact(hf, "controller")
