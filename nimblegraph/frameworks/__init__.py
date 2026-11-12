"""Framework detection shared context. Each framework module turns parsed records into routes,
mounts, middlewares and layer facts; api_calls links front-end HTTP calls to backend routes."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from nimblegraph.classify import RE_TEST

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "del", "head", "options", "all")
ROUTER_FACTORIES = {
    "express", "express.Router", "Router", "Hono", "Fastify", "fastify", "Koa", "KoaRouter", "createRouter",
    "Elysia", "polka", "restify.createServer", "createApp", "H3", "OpenAPIHono",
}
CLIENT_FACTORIES = {"axios.create", "ky.create", "ky.extend", "ofetch.create", "$fetch.create", "wretch", "createClient"}
CLIENT_PKGS = {"axios", "ky", "ofetch", "wretch", "swr", "@tanstack/react-query", "react-query"}
SERVER_PKGS = {"express", "fastify", "hono", "koa", "@koa/router", "koa-router", "restify", "polka", "elysia", "h3",
               "@nestjs/core", "@nestjs/common", "@hono/node-server"}
UI_PKGS = {"react", "react-dom", "next", "vue", "svelte", "@angular/core", "react-native", "expo", "solid-js", "preact"}


@dataclass
class Route:
    method: str  # GET/POST/.. | ALL | PAGE
    path: str
    file: str  # where the route is declared
    line: int
    handler_file: str | None
    handler: str | None
    kind: str  # api | page | layout
    framework: str
    mounts: list[tuple[str, str]] = field(default_factory=list)  # (file, prefix), outermost first
    middlewares: list[tuple[str | None, str, str]] = field(default_factory=list)  # (file, symbol, scope)


@dataclass
class ApiCall:
    file: str
    symbol: str | None
    method: str
    url: str
    line: int
    route: int | None = None  # index into Context.routes


def join_paths(*parts: str) -> str:
    segs: list[str] = []
    for p in parts:
        if not p:
            continue
        segs.extend(s for s in p.strip().split("/") if s)
    return "/" + "/".join(segs)


def arg_str(a: dict | None) -> str | None:
    if a and a.get("t") in ("str", "tpl"):
        return a["v"]
    return None


def is_hole(s: str | None) -> bool:
    """A string that is only an unresolved name ({=settings.X}): not a usable path."""
    return bool(s) and s.startswith("{=") and s.endswith("}") and s.count("{=") == 1


def colon_params(path: str) -> str:
    """Route parameters in the `:name` form the matcher uses: {id}, {id:int}, <int:id>, (?P<id>..), [id] -> :id."""
    import re
    path = re.sub(r"\(\?P<(\w+)>(?:[^()]|\([^()]*\))*\)", r":\1", path)  # Django regex groups first
    path = re.sub(r"\{\s*\*?([\w-]+)[^}]*\}", r":\1", path)
    path = re.sub(r"<(?:\w+:)?(\w+)>", r":\1", path)
    path = path.replace("^", "").replace("$", "")
    path = re.sub(r"^/?(\{=[^{}]*\})+/?", "/", path)  # a prefix nobody could resolve (runtime settings): dropped
    path = re.sub(r"\{=[^{}]*\}", ":param", path)
    return re.sub(r"/\?(?=/|$)|\?$", "", path)  # optional trailing slash of a regex route


def prop(a: dict | None, *keys: str) -> dict | None:
    """A named argument (Python kwargs, Java/C# annotation fields) from a trailing {'t': 'obj'} ARG."""
    if a and a.get("t") == "obj":
        for k in keys:
            if k in a["props"]:
                return a["props"][k]
    return None


def strs(a: dict | None) -> list[str]:
    """'x' or ['x', 'y'] (annotation arrays, methods=[...])."""
    if not a:
        return []
    if a.get("t") == "arr":
        return [s for s in (arg_str(i) for i in a.get("items", [])) if s is not None]
    s = arg_str(a)
    return [s] if s is not None else []


def enclosing(blocks: list[tuple[int, int, str]], line: int) -> list[str]:
    """Prefixes of the closures (start, end, prefix) that contain `line`, outermost first."""
    inside = [b for b in blocks if b[0] < line <= b[1]]
    return [p for _s, _e, p in sorted(inside, key=lambda b: (b[0], -b[1]))]


def class_file(ctx: "Context", name: str, near: str | None = None) -> str | None:
    """The file declaring class `name` (or a qualified Ruby name like Api::UsersController); unique, or the one
    nearest to `near`."""
    idx = ctx._class_index
    if idx is None:  # name and qualified name -> files, once per parsed view
        idx = {}
        for g, rec in ctx.parsed.items():
            for s in rec.get("symbols", []):
                for key in {s["name"], s.get("qualified") or s["name"]}:
                    idx.setdefault(key, set()).add(g)
        ctx._class_index = idx
    hits = idx.get(name, set())
    if not hits and "::" in name:
        hits = idx.get(name.rsplit("::", 1)[-1], set())
    hits = sorted(hits)
    if len(hits) <= 1 or near is None:
        return hits[0] if hits else None
    near_parts = near.split("/")
    return max(hits, key=lambda h: sum(1 for a, b in zip(h.split("/"), near_parts) if a == b))


def method_owner(ctx: "Context", f: str, name: str) -> tuple[str, str] | None:
    """`h.List` / `List` as a handler: the class (struct) that has that method, preferring the same file, then the
    files `f` imports, then a unique match in the project. Uses an index built once per parsed view."""
    idx = ctx._method_index
    if idx is None:
        idx = {}
        for g, rec in ctx.parsed.items():
            for s in rec.get("symbols", []):
                for m in s.get("methods", []) or []:
                    idx.setdefault(m["name"], []).append((g, f"{s['name']}.{m['name']}"))
                if "." in s["name"]:  # Go/Rust/Swift `Type.method` declared away from its type
                    idx.setdefault(s["name"].rsplit(".", 1)[1], []).append((g, s["name"]))
        ctx._method_index = idx
    hits = idx.get(name, [])
    if not hits:
        return None
    for pool in ({f}, {t[0] for t in ctx.local_targets.get(f, {}).values()}):
        near = sorted(h for h in hits if h[0] in pool)
        if near:
            return near[0]
    return hits[0] if len(hits) == 1 else None


class Context:
    def __init__(self, root, files: set[str], parsed: dict[str, dict], local_targets: dict[str, dict],
                 spec_targets: dict[str, dict], externals: dict[str, set[str]], package_deps, package_dir_of,
                 resolver):
        self.root = root
        self.files = files
        self.parsed = parsed
        self.local_targets = local_targets  # file -> local name -> (file, imported)
        self.spec_targets = spec_targets  # file -> spec -> file
        self.externals = externals  # file -> {pkg}
        self.package_deps = package_deps  # pkg dir -> set of dependency names
        self.package_dir_of = package_dir_of  # file -> pkg dir
        self.resolver = resolver
        self.routes: list[Route] = []
        self.api_calls: list[ApiCall] = []
        self.facts: dict[str, tuple[str, bool]] = {}  # file -> (layer, strong) from framework facts
        self.entries: set[str] = set()
        self._ext_locals: dict[str, dict[str, str]] = {}
        self._sym_names: dict[str, set[str]] = {}  # file -> top-level symbol names (resolve_local is hot)

    @property
    def parsed(self) -> dict[str, dict]:
        return self._parsed

    @parsed.setter
    def parsed(self, value: dict[str, dict]) -> None:  # run_all swaps the view per language
        self._parsed = value
        self._sym_names = {}
        self._method_index = None
        self._class_index = None

    # ------------------------------------------------------------ records
    def rec(self, f: str) -> dict:
        return self.parsed.get(f) or {}

    def symbol(self, f: str, name: str) -> dict | None:
        for s in self.rec(f).get("symbols", []):
            if s["name"] == name:
                return s
        return None

    def default_local(self, f: str) -> str | None:
        for e in self.rec(f).get("exports", []):
            if e["name"] == "default":
                return e.get("local")
        return None

    def fact(self, f: str | None, layer: str, strong: bool = True) -> None:
        """Strong facts (decorators, file conventions) win over weak ones (usage-based guesses)."""
        if not f:
            return
        cur = self.facts.get(f)
        if cur is None or (strong and not cur[1]):
            self.facts[f] = (layer, strong)

    # ------------------------------------------------------------ name resolution
    def resolve_local(self, f: str, name: str) -> tuple[str, str] | None:
        """A local identifier -> (file, symbol) where it is defined (follows imports and barrels)."""
        if not name:
            return None
        hit = self.local_targets.get(f, {}).get(name)
        if hit:
            dst, imported = hit
            if imported == "default":
                imported = self.default_local(dst) or "default"
            elif imported == "*":
                imported = name
            return dst, imported
        names = self._sym_names.get(f)
        if names is None:
            names = self._sym_names[f] = {s["name"] for s in self.rec(f).get("symbols", [])}
        if name in names:
            return f, name
        return None

    def external_of(self, f: str, name: str) -> str | None:
        """Package an identifier was imported from (when it is external)."""
        if f not in self._ext_locals:
            m = {}
            for imp in self.rec(f).get("imports", []):
                spec = imp["spec"]
                if spec.startswith((".", "/")) or spec in self.spec_targets.get(f, {}):
                    continue
                for _imported, local in imp.get("names") or []:
                    m[local] = spec
            self._ext_locals[f] = m
        return self._ext_locals[f].get(name)

    def deps(self, f: str) -> set[str]:
        d = self.package_dir_of(f)
        out = set(self.package_deps.get(d, ()))
        if d:
            out |= self.package_deps.get("", set())
        return out

    def side(self, f: str) -> str | None:
        """'server' | 'client' | 'both' | None, from the file first, then its package."""
        ext = self.externals.get(f, set())
        rec = self.rec(f)
        server = bool(ext & SERVER_PKGS)
        client = bool(ext & (UI_PKGS | CLIENT_PKGS)) or f.endswith((".tsx", ".jsx")) or "use client" in rec.get("directives", [])
        if server != client:
            return "server" if server else "client"
        deps = self.deps(f)
        server = bool(deps & SERVER_PKGS)
        client = bool(deps & UI_PKGS)
        if server and client:
            return "both"
        if server or client:
            return "server" if server else "client"
        return None

    # ------------------------------------------------------------ objects
    def obj_factory(self, f: str, root: str, depth: int = 0) -> str | None:
        """Factory call that created `root` (e.g. 'express.Router', 'axios.create'), following imports."""
        rec = self.rec(f)
        c = rec.get("vars", {}).get(root)
        if c:
            return c
        if depth > 3:
            return None
        hit = self.resolve_local(f, root)
        if hit and hit[0] != f:
            return self.obj_factory(hit[0], hit[1], depth + 1)
        pkg = self.external_of(f, root)
        if pkg:
            return pkg  # e.g. `import axios from 'axios'` -> 'axios'
        return None

    def obj_role(self, f: str, obj: str) -> str | None:
        """'router' | 'client' | None for the object of a member call."""
        root = obj.split(".")[0]
        if root == "this":
            return None
        fac = self.obj_factory(f, root)
        if not fac:
            return None
        if fac in ROUTER_FACTORIES or fac.endswith(".Router") or fac in SERVER_PKGS:
            return "router"
        if fac in CLIENT_FACTORIES or fac in CLIENT_PKGS or fac.split(".")[0] in ("axios", "ky"):
            return "client"
        return None

    def call_role(self, f: str, c: dict) -> str | None:
        """For an HTTP-method member call: 'route' (server registration) or 'client' (HTTP request)."""
        if not c.get("obj") or c.get("prop") not in HTTP_METHODS:
            return None
        args = c.get("args") or []
        path = arg_str(args[0]) if args else None
        if path is None:
            return None
        if RE_TEST.search(f.lower()):  # supertest & co. call the API, they do not declare it
            return "client" if path.startswith(("/", "http")) else None
        if c["obj"].endswith("()"):  # request(app).get(..) only means an HTTP call inside tests
            return None
        role = self.obj_role(f, c["obj"])
        if role == "router":
            return "route"
        if role == "client":
            return "client"
        last = args[-1]
        routable = path == "" or path.startswith(("/", "*"))
        if c["obj"].split(".")[0] == "this":  # this.app.get('/x', this.handler) vs this.http.post(url, body, opts)
            if c["obj"] != "this" and self.side(f) != "client" and routable and len(args) >= 2 and (last["t"] == "fn" or last["t"] == "member" and last["v"].startswith("this.")):
                return "route"
            return "client" if path.startswith(("/", "http")) else None
        side = self.side(f)
        if side in ("server", "both") and "@nestjs/core" in self.deps(f):
            return "client" if path.startswith(("/", "http")) else None  # Nest declares routes with decorators
        if not routable:
            return "client" if side == "client" and path.startswith("http") else None
        handlerish = len(args) >= 2 and (last["t"] in ("fn", "member", "call")
                                         or last["t"] == "id" and self.resolve_local(f, last["v"]) is not None)
        if side == "server":
            return "route" if handlerish else None
        if side == "client":
            return "client" if path.startswith(("/", "http")) else None
        if handlerish and args[-1]["t"] in ("fn", "member", "call"):
            return "route"
        return "client" if path.startswith(("/", "http")) else None

    def spec_target(self, f: str, spec: str) -> str | None:
        return self.spec_targets.get(f, {}).get(spec)

    def under(self, f: str, d: str) -> bool:
        return d == "" or f.startswith(d.rstrip("/") + "/")


def run_all(ctx: Context) -> None:
    """JS detectors see only JS/TS records; each detector of another language runs when the project has files
    of one of its languages. api_calls runs last so front-end calls match the routes of every back end."""
    import importlib

    from nimblegraph import lang as langs
    from nimblegraph.frameworks import api_calls, express, nest, next as nextjs, react_router, tanstack_router
    full = ctx.parsed
    js = {f: r for f, r in full.items() if langs.is_js(f)}
    present = {r.get("lang") for r in full.values()}
    ctx.parsed = js
    try:
        nextjs.detect(ctx)
        express.detect(ctx)
        nest.detect(ctx)
        react_router.detect(ctx)
        tanstack_router.detect(ctx)
        from nimblegraph.frameworks import strings
        ctx.parsed = {f: r for f, r in full.items() if r.get("lang") not in (None, "javascript")}
        strings.resolve(ctx)  # constants from other files into path/prefix arguments
        for mod_name, languages in DETECTORS:
            if present & set(languages):
                ctx.parsed = {f: r for f, r in full.items() if r.get("lang") in languages}
                importlib.import_module(f"nimblegraph.frameworks.{mod_name}").detect(ctx)
        ctx.parsed = js
        api_calls.detect(ctx)
        from nimblegraph.frameworks import http_calls
        ctx.parsed = {f: r for f, r in full.items() if r.get("lang") not in (None, "javascript")}
        http_calls.detect(ctx)  # clients and tests of the other languages
    finally:
        ctx.parsed = full


# detectors of the other languages: (module in nimblegraph/frameworks, languages it reads)
DETECTORS: list[tuple[str, tuple[str, ...]]] = [
    ("python_web", ("python",)),
    ("go_http", ("go",)),
    ("spring", ("java", "kotlin")),
    ("aspnet", ("csharp",)),
    ("php_web", ("php",)),
    ("ruby_web", ("ruby",)),
    ("rust_web", ("rust",)),
    ("ktor", ("kotlin",)),
    ("vapor", ("swift",)),
]


def stem(path: str) -> str:
    base = posixpath.basename(path)
    return base.split(".")[0]
