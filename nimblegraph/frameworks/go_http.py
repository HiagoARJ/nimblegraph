"""Go HTTP routers: net/http (`mux.HandleFunc("GET /x", h)`, Go 1.22 patterns), Gin, Echo, Chi, Fiber and
gorilla/mux. Group prefixes come from `api := r.Group("/api")` assignments, from closures
(`r.Route("/x", func(r chi.Router) {...})`) and from route-registering functions that receive a group
(`users.Register(v1.Group("/users"))` -> `func Register(router *gin.RouterGroup) { router.POST(...) }`)."""

from __future__ import annotations

from collections import defaultdict

from nimblegraph.frameworks import Context, Route, arg_str, colon_params, enclosing, join_paths, method_owner

VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
TITLE = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "Head": "HEAD",
         "Options": "OPTIONS"}
ANY = {"Any": "ALL", "Handle": "ALL", "HandleFunc": "ALL", "All": "ALL", "Match": "ALL"}
GROUPS = {"Group", "Route", "Mount", "PathPrefix", "Subrouter", "Prefix"}
ROUTER_PKGS = ("net/http", "github.com/gin-gonic/gin", "github.com/labstack/echo", "github.com/go-chi/chi",
               "github.com/gofiber/fiber", "github.com/gorilla/mux", "github.com/julienschmidt/httprouter")


def _verb(prop: str) -> str | None:
    if prop in VERBS:
        return prop
    if prop in TITLE:
        return TITLE[prop]
    return ANY.get(prop)


def _handler(ctx: Context, f: str, a: dict) -> tuple[str, str | None]:
    if a.get("t") == "member":
        name = a["v"].rpartition(".")[2]
        hit = ctx.resolve_local(f, a["v"])  # users.List (package function)
        if hit:
            return hit[0], hit[1]
        mo = method_owner(ctx, f, name)
        if mo:
            return mo
        return f, a["v"]
    if a.get("t") == "id":
        hit = ctx.resolve_local(f, a["v"])
        if hit:
            return hit[0], hit[1]
        return f, a["v"]
    if a.get("t") == "call":  # http.HandlerFunc(h), h.Wrap(x)
        inner = (a.get("args") or [None])[0]
        if inner and inner.get("t") in ("id", "member"):
            return _handler(ctx, f, inner)
    return f, None


def _params(rec: dict) -> dict[str, list[str]]:
    return {s["name"]: s.get("params") or [] for s in rec.get("symbols", []) if s.get("kind") == "function"}


def _var_prefixes(rec: dict) -> dict[str, str]:
    """var -> prefix from `api := r.Group("/api")` (a group of a group adds up)."""
    out: dict[str, str] = {}
    for a in rec.get("assigns", []):
        if a.get("prop") in GROUPS and a.get("args"):
            p = arg_str(a["args"][0])
            if p is not None:
                out[a["name"]] = join_paths(out.get(a.get("obj") or "", ""), p)
    return out


def _arg_prefix(a: dict, var_pre: dict[str, str]) -> str | None:
    if a.get("t") == "call" and (a.get("callee") or "").rsplit(".", 1)[-1] in GROUPS:
        obj = (a.get("callee") or "").rsplit(".", 1)[0]
        p = arg_str((a.get("args") or [None])[0])
        if p is not None:
            return join_paths(var_pre.get(obj, ""), p)
    if a.get("t") == "id" and a["v"] in var_pre:
        return var_pre[a["v"]]
    return None


def _param_prefixes(ctx: Context) -> dict[tuple[str, str, int], list[str]]:
    """(file, function, param index) -> prefixes passed by the callers; two passes let a prefix travel one more hop."""
    out: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    for _ in range(2):
        nxt: dict[tuple[str, str, int], list[str]] = defaultdict(list)
        for f, rec in ctx.parsed.items():
            if f.endswith("_test.go"):
                continue
            params = _params(rec)
            var_pre = _var_prefixes(rec)
            for c in rec.get("calls", []):
                target = f"{c['obj']}.{c['prop']}" if c.get("obj") else c.get("callee")
                if not target:
                    continue
                hit = ctx.resolve_local(f, target)
                if not hit:
                    continue
                # the caller's own parameter prefix, when it forwards it: func A(r) { B(r.Group("/x")) }
                for i, pname in enumerate(params.get(c.get("sym") or "", [])):
                    pres = out.get((f, c.get("sym"), i))
                    if pres:
                        var_pre = {**var_pre, pname: pres[0]}
                for i, a in enumerate(c.get("args") or []):
                    p = _arg_prefix(a, var_pre)
                    if p is not None:
                        nxt[(hit[0], hit[1], i)].append(p)
        out = nxt
    return out


def _mounted(ctx: Context) -> dict[tuple[str, str], list[str]]:
    """(file, function) -> prefixes of `r.Mount("/api/v1", apiv1.Routes())`: the routes a function builds and
    returns are served under the prefix where its result is mounted (two passes for mounts of mounts)."""
    out: dict[tuple[str, str], list[str]] = {}
    for _ in range(3):
        nxt: dict[tuple[str, str], list[str]] = defaultdict(list)
        for f, rec in ctx.parsed.items():
            if f.endswith("_test.go"):
                continue
            var_pre = _var_prefixes(rec)
            for c in rec.get("calls", []):
                args = c.get("args") or []
                if c.get("prop") not in ("Mount", "Route", "Group", "Handle") or len(args) < 2                         or args[1].get("t") != "call" or not args[1].get("callee"):
                    continue
                pre = arg_str(args[0])
                hit = ctx.resolve_local(f, args[1]["callee"])
                if pre is None or not hit:
                    continue
                for o in out.get((f, c.get("sym") or ""), [""]):
                    full = join_paths(o, var_pre.get(c.get("obj") or "", ""), pre)
                    if full not in nxt[hit]:
                        nxt[hit].append(full)
        out = dict(nxt)
    return out


def detect(ctx: Context) -> None:
    passed = _param_prefixes(ctx)
    mounted = _mounted(ctx)
    for f, rec in sorted(ctx.parsed.items()):
        if f.endswith("_test.go"):
            continue
        ext = ctx.externals.get(f, set())
        if not any(e.startswith(ROUTER_PKGS) for e in ext) and not rec.get("assigns"):
            continue
        params = _params(rec)
        prefixes = _var_prefixes(rec)
        blocks = []
        for c in rec.get("calls", []):
            fn = next((a for a in c.get("args") or [] if a.get("t") == "fn"), None)  # Group("/x", func(){}, mw)
            if c.get("prop") in GROUPS and fn is not None:
                p = arg_str(c["args"][0])
                if p is not None and fn.get("line"):
                    blocks.append((fn["line"], fn["end"], join_paths(prefixes.get(c.get("obj") or "", ""), p)))
        found = False
        for c in rec.get("calls", []):
            prop, obj, args = c.get("prop") or "", c.get("obj"), c.get("args") or []
            verb = _verb(prop)
            if verb is None or not obj or len(args) < 2:
                continue
            path = arg_str(args[0])
            if path is None or not (path.startswith("/") or path.split(" ", 1)[0] in VERBS):
                continue
            if " " in path and path.split(" ", 1)[0] in VERBS:  # Go 1.22: "GET /users/{id}"
                verb, path = path.split(" ", 1)
                path = path.strip()
                if "/" in path and not path.startswith("/"):  # "GET example.com/x"
                    path = "/" + path.split("/", 1)[1]
            if obj == "http" and prop not in ("HandleFunc", "Handle"):
                continue  # http.Get(url): a client call
            handler_file, handler = _handler(ctx, f, args[-1])
            sym = c.get("sym") or ""
            outer = mounted.get((f, sym), [""])  # built in a function whose result is mounted elsewhere
            if obj in params.get(sym, []):  # func Register(router *gin.RouterGroup): prefixes from the callers
                outer = sorted(set(passed.get((f, sym, params[sym].index(obj)), []))) or outer
            for o in outer:
                full = join_paths(o, *enclosing(blocks, c.get("line", 0)), prefixes.get(obj, ""), path)
                ctx.routes.append(Route(verb, colon_params(full), f, c.get("line", 0), handler_file, handler, "api", "go"))
            found = True
            if handler_file != f:
                ctx.fact(handler_file, "controller", strong=False)
        if found:
            ctx.fact(f, "route", strong=False)
        if rec.get("entry"):
            ctx.entries.add(f)
