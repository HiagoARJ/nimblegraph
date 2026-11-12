"""Ktor (Kotlin): the routing DSL, nested by lambda ranges: `routing { route("/api") { get("/x") { ... } } }`.
Spring on Kotlin is handled by spring.py."""

from __future__ import annotations

from nimblegraph.frameworks import Context, Route, arg_str, colon_params, enclosing, join_paths

VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "head": "HEAD",
         "options": "OPTIONS"}


def detect(ctx: Context) -> None:
    for f, rec in sorted(ctx.parsed.items()):
        calls = rec.get("calls", [])
        if not any(c.get("callee") == "routing" for c in calls) and not any(
                e.startswith("io.ktor") for e in ctx.externals.get(f, set())):
            continue
        blocks = []
        for c in calls:
            args = c.get("args") or []
            fn = next((a for a in args if a.get("t") == "fn"), None)
            if c.get("callee") == "route" and fn and fn.get("line"):
                blocks.append((fn["line"], fn["end"], arg_str(args[0]) or ""))
        found = False
        for c in calls:
            verb = VERBS.get(c.get("callee") or "")
            args = c.get("args") or []
            if not verb or not any(a.get("t") == "fn" for a in args):
                continue
            path = arg_str(args[0]) if args and args[0].get("t") == "str" else ""
            full = join_paths(*enclosing(blocks, c.get("line", 0)), path or "")
            ctx.routes.append(Route(verb, colon_params(full), f, c.get("line", 0), f, c.get("sym"), "api", "ktor"))
            found = True
        if found:
            ctx.fact(f, "route", strong=False)
