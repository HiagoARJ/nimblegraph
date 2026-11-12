"""ASP.NET Core (C#): attribute-routed controllers ([Route("api/[controller]")] + [HttpGet("{id}")]) and Minimal
APIs (app.MapGet("/x", ...), MapGroup prefixes). Base types give layer facts (DbContext, hubs, middleware)."""

from __future__ import annotations

from nimblegraph.frameworks import Context, Route, arg_str, colon_params, join_paths, method_owner

VERBS = {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT", "HttpDelete": "DELETE", "HttpPatch": "PATCH",
         "HttpHead": "HEAD", "HttpOptions": "OPTIONS"}
MAP = {"MapGet": "GET", "MapPost": "POST", "MapPut": "PUT", "MapDelete": "DELETE", "MapPatch": "PATCH",
       "MapMethods": "ALL", "Map": "ALL", "MapFallback": "ALL"}
CONTROLLER_BASES = {"ControllerBase", "Controller", "ApiController", "ODataController"}
BASE_LAYERS = {"DbContext": "repository", "IdentityDbContext": "repository", "Hub": "controller",
               "BackgroundService": "service", "IMiddleware": "middleware", "IHostedService": "service",
               "Profile": "config", "AbstractValidator": "type"}


def _first_str(d: dict) -> str | None:
    for a in d.get("args") or []:
        s = arg_str(a)
        if s is not None:
            return s
        if a.get("t") == "obj":
            s = arg_str(a["props"].get("template") or a["props"].get("Template"))
            if s is not None:
                return s
    return None


def _tokens(path: str, ctrl: str, action: str) -> str:
    """[controller]/[action] lowercased: ASP.NET matches URLs case-insensitively and clients call /api/users."""
    return path.replace("[controller]", ctrl.lower()).replace("[action]", action.lower()).replace("[area]", "")


def detect(ctx: Context) -> None:
    for f, rec in sorted(ctx.parsed.items()):
        for s in rec.get("symbols", []):
            bases = {b for b in [s.get("extends"), *(s.get("implements") or [])] if b}
            for b, layer in BASE_LAYERS.items():
                if b in bases:
                    ctx.fact(f, layer)
            decos = {d["name"]: d for d in s.get("decorators") or []}
            if s.get("extends") == "PageModel" and "/Pages/" in f"/{f}":  # Razor Pages: Pages/Basket/Index.cshtml.cs
                rel = f"/{f}".rsplit("/Pages/", 1)[1].removesuffix(".cshtml.cs").removesuffix(".cs")
                rel = rel[:-5] if rel.endswith("Index") else rel
                ctx.routes.append(Route("PAGE", join_paths(rel).lower(), f, s["line"], f, s["name"], "page", "razor"))
                ctx.fact(f, "page")
                continue
            is_ctrl = "ApiController" in decos or bool(bases & CONTROLLER_BASES) or (
                s["name"].endswith("Controller") and s.get("kind") == "class" and "Route" in decos)
            if not is_ctrl or s.get("abstract"):
                continue
            ctx.fact(f, "controller")
            ctrl = s["name"][:-10] if s["name"].endswith("Controller") else s["name"]
            prefix = _first_str(decos["Route"]) if "Route" in decos else ""
            for m in s.get("methods", []) or []:
                mdecos = {d["name"]: d for d in m.get("decorators") or []}
                verbs = [(VERBS[n], d) for n, d in mdecos.items() if n in VERBS]
                if not verbs and "Route" in mdecos:
                    verbs = [("ALL", mdecos["Route"])]
                for verb, d in verbs:
                    sub = _first_str(d)
                    if sub is None and "Route" in mdecos:
                        sub = _first_str(mdecos["Route"])
                    sub = sub or ""
                    full = sub[1:] if sub.startswith("~/") else join_paths(prefix or "", sub) if not sub.startswith("/") \
                        else sub
                    path = colon_params(_tokens(join_paths(full), ctrl, m["name"]))
                    mws = []
                    for t in m.get("param_types") or []:
                        tgt = ctx.resolve_local(f, t)
                        if tgt and tgt[0] != f and t.endswith(("Dto", "DTO", "Request", "Command", "Query", "Model",
                                                               "Input")):
                            mws.append((tgt[0], t, "validation"))
                    ctx.routes.append(Route(verb, path, f, m.get("line", s["line"]), f, f"{s['name']}.{m['name']}",
                                            "api", "aspnet", middlewares=mws))
        _minimal(ctx, f, rec)


def _minimal(ctx: Context, f: str, rec: dict) -> None:
    groups: dict[str, str] = {}  # var -> prefix (MapGroup)
    for a in rec.get("assigns", []):
        if a.get("prop") == "MapGroup":
            parent = groups.get(a.get("obj") or "", "")
            groups[a["name"]] = join_paths(parent, arg_str((a.get("args") or [None])[0]) or "")
    for c in rec.get("calls", []):
        verb = MAP.get(c.get("prop") or "")
        if verb is None or not c.get("obj") or not c.get("args"):
            continue
        path = arg_str(c["args"][0])
        if path is None:
            continue
        last = c["args"][-1]
        handler_file, handler = f, None
        if last.get("t") in ("member", "id"):
            name = last["v"].rsplit(".", 1)[-1]
            owner = last["v"].rsplit(".", 1)[0] if "." in last["v"] else None
            hit = ctx.resolve_local(f, owner) if owner else ctx.resolve_local(f, name)
            if hit:
                handler_file, handler = hit[0], f"{hit[1]}.{name}" if owner else hit[1]
            else:
                mo = method_owner(ctx, f, name)
                if mo:
                    handler_file, handler = mo
        elif last.get("t") == "fn" and c.get("sym"):  # a lambda inside an endpoint class: the class handles it
            handler = c["sym"]
        full = join_paths(groups.get(c["obj"], ""), path)
        ctx.routes.append(Route(verb, colon_params(full), f, c.get("line", 0), handler_file, handler, "api", "aspnet"))
        ctx.entries.add(f)
