"""Vapor (Swift): `app.get("users", ":id") { req in }`, `let users = routes.grouped("users")`,
`users.post(use: create)` inside a RouteCollection's boot(routes:), group closures and
`app.register(collection: TodoController())` (the collection's routes get the prefix of where it is registered)."""

from __future__ import annotations

from nimblegraph.frameworks import Context, Route, colon_params, enclosing, join_paths, prop

VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "on": "ALL"}


def _segments(args: list[dict]) -> list[str]:
    """Path components are separate arguments: get("users", ":id") -> users/:id."""
    out = []
    for a in args:
        if a.get("t") == "str":
            out.append(a["v"])
        elif a.get("t") == "member" and a["v"].endswith(".constant"):
            out.append(a["v"].rsplit(".", 2)[-2])
    return out


def detect(ctx: Context) -> None:
    # collection prefixes: x.grouped("api").register(collection: C()) / app.register(collection: C())
    coll_prefix: dict[str, list[str]] = {}
    for f, rec in ctx.parsed.items():
        groups = {a["name"]: join_paths(*_segments(a.get("args") or [])) for a in rec.get("assigns", [])
                  if a.get("prop") == "grouped"}
        for c in rec.get("calls", []):
            if c.get("prop") != "register":
                continue
            target = prop((c.get("args") or [None])[-1], "collection")
            name = (target or {}).get("callee") or (target or {}).get("v")
            if name:
                coll_prefix.setdefault(name, []).append(groups.get(c.get("obj") or "", ""))
    for f, rec in sorted(ctx.parsed.items()):
        calls = rec.get("calls", [])
        groups: dict[str, str] = {}
        for a in rec.get("assigns", []):
            if a.get("prop") == "grouped":
                groups[a["name"]] = join_paths(groups.get(a.get("obj") or "", ""), *_segments(a.get("args") or []))
        blocks = []
        for c in calls:
            if c.get("prop") == "group" and c.get("args") and c["args"][-1].get("t") == "fn":
                fn = c["args"][-1]
                blocks.append((fn["line"], fn["end"], join_paths(groups.get(c.get("obj") or "", ""),
                                                                 *_segments(c["args"][:-1]))))
        found = False
        for c in calls:
            verb = VERBS.get(c.get("prop") or "")
            if not verb or not c.get("obj"):
                continue
            args = c.get("args") or []
            use = prop(args[-1] if args else None, "use")
            has_closure = any(a.get("t") == "fn" for a in args)
            if use is None and not has_closure:
                continue
            sym = c.get("sym") or ""
            outer = coll_prefix.get(sym, [""])
            name = use["v"].rsplit(".", 1)[-1] if use and use.get("t") in ("id", "member") else None  # index / self.index
            handler = f"{sym}.{name}" if name and sym else sym or None
            for o in outer:
                full = join_paths(o, *enclosing(blocks, c.get("line", 0)), groups.get(c["obj"], ""), *_segments(args))
                ctx.routes.append(Route(verb, colon_params(full), f, c.get("line", 0), f, handler, "api", "vapor"))
            found = True
        if found:
            ctx.fact(f, "controller", strong=False)
