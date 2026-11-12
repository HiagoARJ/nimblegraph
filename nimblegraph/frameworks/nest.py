"""NestJS: @Controller + @Get/@Post..., setGlobalPrefix, @UseGuards/@UsePipes/@UseInterceptors (class and method),
useGlobal*, DTO parameter types, and the module that declares each controller."""

from __future__ import annotations

from nimblegraph.classify import RE_TEST
from nimblegraph.frameworks import Context, Route, arg_str, join_paths

HTTP_DECORATORS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE",
                   "Options": "OPTIONS", "Head": "HEAD", "All": "ALL"}
SCOPED_DECORATORS = {"UseGuards": "guard", "UsePipes": "pipe", "UseInterceptors": "interceptor", "UseFilters": "filter"}
GLOBAL_CALLS = {"useGlobalGuards": "guard", "useGlobalPipes": "pipe", "useGlobalInterceptors": "interceptor",
                "useGlobalFilters": "filter"}
CLASS_LAYERS = (("Controller", "controller"), ("Module", "module"), ("Entity", "model"), ("Schema", "model"),
                ("Resolver", "controller"), ("WebSocketGateway", "controller"))


def _ref(ctx: Context, f: str, a: dict) -> tuple[str | None, str] | None:
    t = a.get("t")
    if t == "id":
        return ctx.resolve_local(f, a["v"]) or (None, f"{a['v']}|{f}")
    if t in ("new", "call"):
        root = a["callee"].split(".")[0]
        hit = ctx.resolve_local(f, root)
        return (hit[0], a["callee"]) if hit else (None, f"{a['callee']}|{f}")
    if t == "member":
        root = a["v"].split(".")[0]
        hit = ctx.resolve_local(f, root)
        return (hit[0], a["v"]) if hit else (None, f"{a['v']}|{f}")
    return None


def _scoped(ctx: Context, f: str, decorators: list[dict]) -> list[tuple[str | None, str, str]]:
    out = []
    for d in decorators:
        scope = SCOPED_DECORATORS.get(d["name"])
        if not scope:
            continue
        for a in d.get("args", []):
            r = _ref(ctx, f, a)
            if r:
                out.append((r[0], r[1], scope))
    return out


def _prefix(d: dict) -> str:
    args = d.get("args") or []
    if not args:
        return ""
    a = args[0]
    if a["t"] == "obj":
        return arg_str(a["props"].get("path")) or ""
    if a["t"] == "arr" and a.get("items"):
        return arg_str(a["items"][0]) or ""
    return arg_str(a) or ""


def detect(ctx: Context) -> None:
    global_prefix: dict[str, tuple[str, str]] = {}  # package dir -> (prefix, file)
    global_mws: dict[str, list[tuple[str | None, str, str]]] = {}
    declared_in: dict[tuple[str, str], str] = {}  # (controller file, class) -> module file
    for f in sorted(ctx.files):
        rec = ctx.rec(f)
        for c in ([] if RE_TEST.search(f.lower()) else rec.get("calls", [])):
            prop = c.get("prop")
            if prop == "setGlobalPrefix" and c["args"] and arg_str(c["args"][0]) is not None:
                global_prefix[ctx.package_dir_of(f)] = (arg_str(c["args"][0]), f)
                ctx.entries.add(f)
            elif prop in GLOBAL_CALLS:
                for a in c["args"]:
                    r = _ref(ctx, f, a)
                    if r:
                        global_mws.setdefault(ctx.package_dir_of(f), []).append((r[0], r[1], GLOBAL_CALLS[prop]))
                ctx.entries.add(f)
        for s in rec.get("symbols", []):
            decos = {d["name"]: d for d in s.get("decorators", []) or []}
            for name, layer in CLASS_LAYERS:
                if name in decos:
                    ctx.fact(f, layer)
                    break
            else:
                if "Injectable" in decos:
                    low = s["name"].lower()
                    layer = ("middleware" if low.endswith(("guard", "interceptor", "pipe", "filter", "strategy", "middleware"))
                             else "repository" if low.endswith("repository") else "service")
                    ctx.fact(f, layer)
            mod = decos.get("Module")
            if mod and mod.get("args") and mod["args"][0]["t"] == "obj":
                ctrls = mod["args"][0]["props"].get("controllers")
                for item in (ctrls or {}).get("items", []) or []:
                    if item["t"] == "id":
                        hit = ctx.resolve_local(f, item["v"])
                        if hit:
                            declared_in[hit] = f

    for f in sorted(ctx.files):
        for s in ctx.rec(f).get("symbols", []):
            decos = {d["name"]: d for d in s.get("decorators", []) or []}
            if "Controller" not in decos:
                continue
            cprefix = _prefix(decos["Controller"])
            pkg = ctx.package_dir_of(f)
            gprefix, gfile = global_prefix.get(pkg) or global_prefix.get("", ("", None))
            class_mws = _scoped(ctx, f, s.get("decorators", []))
            module_file = declared_in.get((f, s["name"]))
            for m in s.get("methods", []) or []:
                http = next((d for d in m.get("decorators", []) if d["name"] in HTTP_DECORATORS), None)
                if not http:
                    continue
                path = join_paths(gprefix, cprefix, _prefix(http))
                mws = list(global_mws.get(pkg, []) or global_mws.get("", []))
                mws += class_mws + _scoped(ctx, f, m.get("decorators", []))
                mws = list(dict.fromkeys(mws))  # the same global filter registered twice is one step
                for tname in m.get("param_types", []):
                    hit = ctx.resolve_local(f, tname)
                    if hit:
                        mws.append((hit[0], hit[1], "validation"))
                mounts = []
                if gfile:
                    mounts.append((gfile, "/" + gprefix.strip("/") if gprefix else "/"))
                if module_file:
                    mounts.append((module_file, ""))
                mounts.append((f, "/" + cprefix.strip("/") if cprefix else "/"))
                ctx.routes.append(Route(HTTP_DECORATORS[http["name"]], path, f, m["line"], f, f"{s['name']}.{m['name']}",
                                        "api", "nest", mounts=mounts, middlewares=mws))
                for fi, _sym, scope in mws:
                    if fi and scope == "validation":
                        ctx.fact(fi, "type", strong=False)
