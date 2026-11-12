"""Rust web frameworks: Axum (`.route("/x", get(h).post(h2))`, `.nest("/api", api())` across functions),
Actix Web (`#[get("/x")]`, `.route("/x", web::get().to(h))`) and Rocket (`#[get("/x")]` + `.mount("/api",
routes![a, b])`)."""

from __future__ import annotations

from collections import defaultdict

from nimblegraph.frameworks import Context, Route, arg_str, colon_params, join_paths

ATTR_VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "head": "HEAD",
              "options": "OPTIONS"}


def _resolve(ctx: Context, f: str, name: str | None) -> tuple[str, str | None]:
    if not name:
        return f, None
    for key in (name.replace(".", "::"), name, name.rsplit(".", 1)[-1]):
        hit = ctx.resolve_local(f, key)
        if hit:
            return hit[0], hit[1] if hit[1] != "*" else name.rsplit(".", 1)[-1]
    return f, name.rsplit(".", 1)[-1]


def detect(ctx: Context) -> None:
    # nests: (file, function) -> prefixes given by `.nest("/api", api())` / `.mount("/api", routes![...])`
    fn_prefix: dict[tuple[str, str], list[str]] = {}
    for _ in range(3):  # a nested router's own prefix may come from a function seen later
        nxt: dict[tuple[str, str], list[str]] = defaultdict(list)
        for f, rec in ctx.parsed.items():
            for c in rec.get("calls", []):
                args = c.get("args") or []
                if c.get("prop") not in ("nest", "nest_service", "mount") or len(args) < 2:
                    continue
                pre = arg_str(args[0])
                if pre is None:
                    continue
                targets = args[1].get("items", []) if args[1].get("t") == "arr" else [args[1]]
                for t in targets:
                    name = t.get("callee") if t.get("t") == "call" else t.get("v")
                    if name:
                        tf, ts = _resolve(ctx, f, name)
                        for o in fn_prefix.get((f, c.get("sym") or ""), [""]):
                            p = join_paths(o, pre)
                            if p not in nxt[(tf, ts or name)]:
                                nxt[(tf, ts or name)].append(p)
        fn_prefix = dict(nxt)

    for f, rec in sorted(ctx.parsed.items()):
        found = False
        for c in rec.get("calls", []):  # Axum / Actix method routers
            if not c.get("methods") or c.get("prop") != "route":
                continue
            path = arg_str((c.get("args") or [None])[0])
            if path is None:
                continue
            for pre in fn_prefix.get((f, c.get("sym") or ""), [""]):
                for verb, h in c["methods"]:
                    hf, hs = _resolve(ctx, f, h)
                    ctx.routes.append(Route(verb if verb != "ANY" else "ALL", colon_params(join_paths(pre, path)), f,
                                            c.get("line", 0), hf, hs, "api", "axum"))
                    found = True
        for s in rec.get("symbols", []):  # Actix / Rocket attribute macros
            for d in s.get("decorators") or []:
                verb = ATTR_VERBS.get(d["name"])
                if d["name"] == "route":
                    meth = (d.get("args") or [{}])[-1]
                    verb = arg_str((meth.get("props") or {}).get("method")) if meth.get("t") == "obj" else "ALL"
                if not verb:
                    continue
                path = arg_str((d.get("args") or [None])[0])
                if path is None:
                    continue
                path = path.split("?", 1)[0]  # rocket: "/search?<q>"
                for pre in fn_prefix.get((f, s["name"]), [""]):
                    ctx.routes.append(Route(verb.upper(), colon_params(join_paths(pre, path)), f, s["line"], f, s["name"],
                                            "api", "actix" if "rocket" not in ctx.deps(f) else "rocket"))
                    found = True
        if found:
            ctx.fact(f, "controller", strong=False)
        if rec.get("entry"):
            ctx.entries.add(f)
