"""Express / Fastify / Hono / Koa-router: route registrations, mount trees with prefixes propagated across
files, and middlewares in execution order (global -> router -> route)."""

from __future__ import annotations

from dataclasses import dataclass, field

from nimblegraph.frameworks import (
    HTTP_METHODS, ROUTER_FACTORIES, SERVER_PKGS, Context, Route, arg_str, join_paths,
)

NodeKey = tuple[str, str]  # (file, scope): scope is a router variable or "fn:<symbol>"
MAX_CHAINS = 4
FASTIFY_HOOKS = ("onRequest", "preParsing", "preValidation", "preHandler")


@dataclass
class Mount:
    parent: NodeKey
    file: str
    line: int
    prefix: str
    children: list[NodeKey]
    middlewares: list[tuple[str | None, str]] = field(default_factory=list)


@dataclass
class Use:
    file: str
    line: int
    prefix: str | None
    refs: list[tuple[str | None, str]]


@dataclass
class RouteCall:
    file: str
    line: int
    method: str
    path: str
    handler: tuple[str | None, str | None]
    middlewares: list[tuple[str | None, str]]
    validation: list[tuple[str, str]]


def _routerish(factory: str | None) -> bool:
    return bool(factory) and (factory in ROUTER_FACTORIES or factory.endswith(".Router") or factory in SERVER_PKGS)


class Express:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.routes: dict[NodeKey, list[RouteCall]] = {}
        self.uses: dict[NodeKey, list[Use]] = {}
        self.mounts: list[Mount] = []
        self.listen_files: set[str] = set()

    # ------------------------------------------------------------ nodes
    def node_of(self, f: str, c: dict) -> NodeKey:
        root = c["obj"].split(".")[0]
        rv = self.ctx.rec(f).get("vars", {})
        if root in rv:
            return (f, root)
        hit = self.ctx.resolve_local(f, root)
        if hit and hit[0] != f and _routerish(self.ctx.rec(hit[0]).get("vars", {}).get(hit[1])):
            return hit
        if c.get("sym"):
            return (f, "fn:" + c["sym"].split(".")[0])
        return (f, root)

    def nodes_in(self, f: str, name: str) -> list[NodeKey]:
        rv = self.ctx.rec(f).get("vars", {})
        if name in rv and _routerish(rv[name]):
            return [(f, name)]
        keys = {k for k in self._known if k[0] == f and k[1] == "fn:" + name}
        if keys:
            return sorted(keys)
        if name in ("default", None) or not self.ctx.symbol(f, name):
            return sorted(k for k in self._known if k[0] == f)
        return []

    def child_nodes(self, f: str, a: dict) -> list[NodeKey]:
        t = a.get("t")
        if t == "id":
            name = a["v"]
            rv = self.ctx.rec(f).get("vars", {})
            if name in rv and _routerish(rv[name]):
                return [(f, name)]
            hit = self.ctx.resolve_local(f, name)
            if hit and hit[0] != f:
                return self.nodes_in(*hit)
            if hit:
                return [k for k in self._known if k == (f, "fn:" + name)]
            return []
        if t == "require":
            target = self.ctx.spec_target(f, a["v"])
            return self.nodes_in(target, self.ctx.default_local(target) or "default") if target else []
        if t == "call":
            callee = a["callee"]
            root = callee.split(".")[0]
            if callee.endswith((".routes", ".middleware", ".allowedMethods")):
                return self.child_nodes(f, {"t": "id", "v": root})
            hit = self.ctx.resolve_local(f, root)
            if hit:
                return self.nodes_in(*hit) if hit[0] != f else [k for k in self._known if k == (f, "fn:" + root)]
        return []

    # ------------------------------------------------------------ references
    def mw_refs(self, f: str, a: dict) -> list[tuple[str | None, str]]:
        t = a.get("t")
        ctx = self.ctx
        if t == "id":
            hit = ctx.resolve_local(f, a["v"])
            return [hit if hit else (None, f"{a['v']}|{f}")]
        if t == "member":
            root, _, rest = a["v"].partition(".")
            hit = ctx.resolve_local(f, root)
            return [(hit[0], f"{hit[1]}.{rest}") if hit else (None, f"{a['v']}|{f}")]
        if t in ("call", "new"):
            callee = a["callee"]
            root = callee.split(".")[0]
            hit = ctx.resolve_local(f, root)
            if not hit:
                return [(None, f"{callee}|{f}")]
            inner = [x["v"] for x in a.get("args", []) if x.get("t") in ("id", "member")]
            label = callee if hit[1] == root else callee.replace(root, hit[1], 1)
            return [(hit[0], f"{label}({','.join(inner)})" if inner else label)]
        if t == "fn":
            return [(f, "(inline)")]
        if t == "arr":
            return [r for x in a.get("items", []) for r in self.mw_refs(f, x)]
        return []

    def handler_ref(self, f: str, a: dict, sym: str | None) -> tuple[str | None, str | None]:
        t = a.get("t")
        ctx = self.ctx
        if t == "member":
            v = a["v"][:-5] if a["v"].endswith(".bind") else a["v"]
            root, _, rest = v.partition(".")
            if root == "this":
                return (f, f"{sym.split('.')[0]}.{rest}" if sym else rest)
            hit = ctx.resolve_local(f, root)
            return (hit[0], f"{hit[1]}.{rest}" if rest else hit[1]) if hit else (None, v)
        if t == "id":
            hit = ctx.resolve_local(f, a["v"])
            return hit if hit else (None, a["v"])
        if t == "call":
            if a["callee"].endswith(".bind"):
                return self.handler_ref(f, {"t": "member", "v": a["callee"][:-5]}, sym)
            for inner in a.get("args", []):
                if inner.get("t") in ("member", "id", "fn", "call"):
                    return self.handler_ref(f, inner, sym)
            hit = ctx.resolve_local(f, a["callee"].split(".")[0])
            return (hit[0], a["callee"]) if hit else (None, a["callee"])
        if t == "arr" and a.get("items"):
            return self.handler_ref(f, a["items"][-1], sym)
        return (f, sym or "(inline)")

    def validation_refs(self, f: str, args: list[dict]) -> list[tuple[str, str]]:
        out = []
        for a in args:
            if a.get("t") not in ("call", "new"):
                continue
            for x in a.get("args", []):
                if x.get("t") == "id":
                    hit = self.ctx.resolve_local(f, x["v"])
                    if hit and hit[0] != f:
                        out.append(hit)
                elif x.get("t") == "member":
                    root, _, rest = x["v"].partition(".")
                    hit = self.ctx.resolve_local(f, root)
                    if hit and hit[0] != f:
                        out.append((hit[0], f"{hit[1]}.{rest}"))
        return out

    # ------------------------------------------------------------ collection
    def collect(self) -> None:
        ctx = self.ctx
        self._known: set[NodeKey] = set()
        pending: list[tuple[str, dict]] = []
        for f in sorted(ctx.files):
            for c in ctx.rec(f).get("calls", []):
                prop = c.get("prop")
                if not c.get("obj"):
                    continue
                if prop in HTTP_METHODS and ctx.call_role(f, c) == "route":
                    self._add_route(f, c)
                elif prop == "route" and len(c["args"]) == 1 and c["args"][0]["t"] == "obj":
                    self._fastify_route(f, c)
                elif prop in ("use", "mount", "register", "route", "addHook", "listen"):
                    if ctx.obj_role(f, c["obj"]) == "router" or ctx.side(f) in ("server", "both"):
                        pending.append((f, c))
        # every object that registers something is a node; mounts may target nodes that only mount others
        for f, c in pending:
            self._known.add(self.node_of(f, c))
        for f, c in pending:
            self._add_use(f, c)

    def _add_route(self, f: str, c: dict) -> None:
        args = c["args"]
        path = arg_str(args[0]) or ""
        mids: list[tuple[str | None, str]] = []
        for a in args[1:-1]:
            if a.get("t") == "obj":  # fastify route options
                for hook in FASTIFY_HOOKS:
                    if hook in a["props"]:
                        mids.extend(self.mw_refs(f, a["props"][hook]))
            else:
                mids.extend(self.mw_refs(f, a))
        method = c["prop"].upper()
        method = "DELETE" if method == "DEL" else method
        rc = RouteCall(f, c["line"], method, path, self.handler_ref(f, args[-1], c.get("sym")), mids,
                       self.validation_refs(f, args[1:-1]))
        key = self.node_of(f, c)
        self.routes.setdefault(key, []).append(rc)
        self._known.add(key)

    def _fastify_route(self, f: str, c: dict) -> None:
        props = c["args"][0]["props"]
        url = arg_str(props.get("url")) or arg_str(props.get("path"))
        if url is None or "handler" not in props:
            return
        m = props.get("method")
        methods = [x["v"] for x in (m["items"] if m and m["t"] == "arr" else [m] if m else []) if x and x["t"] == "str"]
        mids = [r for hook in FASTIFY_HOOKS if hook in props for r in self.mw_refs(f, props[hook])]
        key = self.node_of(f, c)
        for method in methods or ["GET"]:
            rc = RouteCall(f, c["line"], method.upper(), url, self.handler_ref(f, props["handler"], c.get("sym")), mids, [])
            self.routes.setdefault(key, []).append(rc)
        self._known.add(key)

    def _add_use(self, f: str, c: dict) -> None:
        prop = c["prop"]
        args = c["args"]
        parent = self.node_of(f, c)
        if prop == "listen":
            self.listen_files.add(f)
            return
        if prop == "addHook":
            if len(args) >= 2 and arg_str(args[0]) in FASTIFY_HOOKS:
                self.uses.setdefault(parent, []).append(Use(f, c["line"], None, self.mw_refs(f, args[1])))
            return
        if prop == "register":
            if not args:
                return
            kids = [k for k in self.child_nodes(f, args[0]) if k in self._known]
            opts = args[1] if len(args) > 1 and args[1].get("t") == "obj" else None
            prefix = arg_str(opts["props"].get("prefix")) if opts else None
            if kids:
                self.mounts.append(Mount(parent, f, c["line"], prefix or "", kids))
            else:
                self.uses.setdefault(parent, []).append(Use(f, c["line"], None, self.mw_refs(f, args[0])))
            return
        prefix = arg_str(args[0]) if args and arg_str(args[0]) is not None and arg_str(args[0]).startswith("/") else None
        rest = args[1:] if prefix is not None else args
        if prop == "route" and prefix is None:
            return
        mids: list[tuple[str | None, str]] = []
        mounted = False
        for a in rest:
            kids = [k for k in self.child_nodes(f, a) if k in self._known and k != parent]
            if kids:
                self.mounts.append(Mount(parent, f, c["line"], prefix or "", kids, list(mids)))
                mounted = True
            else:
                mids.extend(self.mw_refs(f, a))
        if mids and not mounted:
            self.uses.setdefault(parent, []).append(Use(f, c["line"], prefix, mids))

    # ------------------------------------------------------------ chains
    def chains(self, node: NodeKey, seen: frozenset = frozenset()) -> list[list[tuple[Mount, NodeKey]]]:
        parents = [m for m in self.mounts if node in m.children]
        if not parents or node in seen:
            return [[]]
        out = []
        for m in parents:
            for ch in self.chains(m.parent, seen | {node}):
                out.append(ch + [(m, node)])
                if len(out) >= MAX_CHAINS:
                    return out
        return out

    def node_uses(self, node: NodeKey, before: tuple[str, int] | None, rel_path: str, scope: str):
        out = []
        for u in self.uses.get(node, []):
            if before and u.file == before[0] and u.line > before[1]:
                continue
            if u.prefix and not (rel_path + "/").startswith(u.prefix.rstrip("/") + "/"):
                continue
            out.extend((fi, sym, scope) for fi, sym in u.refs)
        return out

    def emit(self) -> None:
        ctx = self.ctx
        for node, calls in sorted(self.routes.items()):
            for ch in self.chains(node):
                for rc in calls:
                    prefixes = [m.prefix for m, _ in ch]
                    full = join_paths(*prefixes, rc.path)
                    mws: list[tuple[str | None, str, str]] = []
                    remaining = [rc.path]
                    for i, (m, _child) in enumerate(ch):
                        rel = join_paths(*prefixes[i:], rc.path)
                        mws += self.node_uses(m.parent, (m.file, m.line), rel, "global" if i == 0 else "router")
                        mws += [(fi, sym, "router") for fi, sym in m.middlewares]
                    remaining = join_paths(rc.path)
                    mws += self.node_uses(node, (rc.file, rc.line), remaining, "router" if ch else "global")
                    mws += [(fi, sym, "route") for fi, sym in rc.middlewares]
                    mws += [(fi, sym, "validation") for fi, sym in rc.validation]
                    mounts = [(m.file, m.prefix or "/") for m, _ in ch] + [(rc.file, "")]
                    hf, hs = rc.handler
                    ctx.routes.append(Route(rc.method, full, rc.file, rc.line, hf, hs, "api", "express",
                                            mounts=mounts, middlewares=mws))
                    ctx.fact(rc.file, "route", strong=False)
                    if hf and hf != rc.file:
                        ctx.fact(hf, "controller", strong=False)
                    for fi, _sym, scope in mws:
                        if fi and scope in ("global", "router", "route") and fi != rc.file:
                            ctx.fact(fi, "middleware", strong=False)
        for m in self.mounts:
            ctx.fact(m.file, "route", strong=False)
        ctx.entries |= self.listen_files


def detect(ctx: Context) -> None:
    ex = Express(ctx)
    ex.collect()
    ex.emit()
