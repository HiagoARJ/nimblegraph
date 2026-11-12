"""In-memory graph loaded from graph.db, target lookup, and the traversals behind every query command."""

from __future__ import annotations

import functools
import math
import posixpath
import unicodedata
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

from nimblegraph import lang as langs
from nimblegraph.classify import FLOW_ORDER
from nimblegraph.frameworks.api_calls import normalize_url, score
from nimblegraph.store import Store

IMPORT_KINDS = ("import", "dynamic", "reexport", "side-effect")
USE_KINDS = ("import", "dynamic", "reexport", "side-effect", "renders", "implements")  # implements: x.h -> x.c
HTTP = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "ALL", "PAGE")
ROOT_LAYERS = {"page", "layout", "entry", "api-route", "route"}
FRONT_LAYERS = {"page", "layout", "component", "hook", "store", "api-client"}
BACK_STOPS = {"controller", "route", "api-route", "entry"}  # going up from back-end code stops at these
FEW_USERS = 4  # a back-end module with this many users or fewer is explained by them
DATA_DIRS = {"db", "database", "schema", "schemas", "models", "entities", "entity", "prisma", "migrations"}
GENERIC_DIRS = {"ui", "i18n", "locales", "lang", "styles", "icons"}
GENERIC_STEMS = {"utils", "util", "helpers", "logger", "log", "env", "cn", "constants", "config", "index"}
NOISE_LAYERS = {"test", "config"}
GENERIC_LAYERS = {"util", "type", "config", "component", "layout", "store", "module", "middleware", "entry"}


@dataclass
class FileNode:
    path: str
    hash: str
    loc: int
    tokens: int
    layer: str | None
    is_entry: bool
    pkg: str


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    names: list[str]
    via: str | None
    line: int


@dataclass
class RouteRow:
    id: int
    method: str
    path: str
    file: str
    handler_file: str | None
    handler: str | None
    kind: str
    line: int
    mounts: list[tuple[str, str]] = field(default_factory=list)
    middlewares: list[tuple[str | None, str, str]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def target(self) -> str:
        return self.handler_file or self.file


@dataclass
class ApiCallRow:
    file: str
    symbol: str | None
    method: str
    url: str
    route_id: int | None
    line: int


@dataclass
class Graph:
    files: dict[str, FileNode] = field(default_factory=dict)
    out: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    inn: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    symbols: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    externals: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    external_lines: dict[tuple[str, str], int] = field(default_factory=dict)  # (file, pkg) -> first import line
    unresolved: dict[str, list[tuple[str, int]]] = field(default_factory=lambda: defaultdict(list))
    routes: dict[int, RouteRow] = field(default_factory=dict)
    api_calls: list[ApiCallRow] = field(default_factory=list)
    sym_refs: dict[tuple[str, str], list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))
    # (file, symbol) -> [(extends|implements, ref_file|None, ref_symbol, ref_pkg|None)]; subclasses is the reverse
    inherits: dict[tuple[str, str], list[tuple]] = field(default_factory=lambda: defaultdict(list))
    subclasses: dict[tuple[str, str], list[tuple[str, str, str]]] = field(default_factory=lambda: defaultdict(list))
    meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, store: Store) -> "Graph":
        g = cls()
        for path, h, loc, tok, layer, entry, pkg in store.query(
                "SELECT path, hash, loc, tokens, layer, is_entry, pkg FROM files"):
            g.files[path] = FileNode(path, h, loc or 0, tok or 0, layer, bool(entry), pkg or "")
        for src, dst, kind, names, via, line in store.query("SELECT src, dst, kind, names, via, line FROM edges"):
            e = Edge(src, dst, kind, [n for n in (names or "").split(",") if n], via, line or 0)
            g.out[src].append(e)
            g.inn[dst].append(e)
        for f, name, kind, line, end, exported, parent, ext, impl, decos, abstract in store.query(
                "SELECT file, name, kind, line, end_line, exported, parent, extends, implements, decorators, abstract"
                " FROM symbols"):
            g.symbols[f].append({"name": name, "kind": kind, "line": line, "end": end, "exported": bool(exported),
                                 "parent": parent, "extends": ext, "implements": [x for x in (impl or "").split(",") if x],
                                 "decorators": [x for x in (decos or "").split(",") if x], "abstract": bool(abstract)})
        for f, sym, kind, rf, rs, pkg in store.query(
                "SELECT file, symbol, kind, ref_file, ref_symbol, ref_pkg FROM inherits"):
            g.inherits[(f, sym)].append((kind, rf, rs, pkg))
            if rf:
                g.subclasses[(rf, rs)].append((f, sym, kind))
        for f, pkg, _kind, line in store.query("SELECT file, pkg, kind, line FROM externals"):
            g.externals[f].add(pkg)
            g.external_lines[(f, pkg)] = min(g.external_lines.get((f, pkg), line or 0), line or 0)
        for f, spec, line, _kind in store.query("SELECT file, spec, line, kind FROM unresolved"):
            g.unresolved[f].append((spec, line))
        for rid, method, path, f, hf, h, kind, line in store.query(
                "SELECT id, method, path, file, handler_file, handler, kind, line FROM routes"):
            g.routes[rid] = RouteRow(rid, method, path, f, hf, h, kind, line or 0)
        for rid, _seq, f, prefix in store.query("SELECT route_id, seq, file, prefix FROM route_mounts ORDER BY route_id, seq"):
            g.routes[rid].mounts.append((f, prefix))
        for rid, _seq, f, sym, scope in store.query(
                "SELECT route_id, seq, file, symbol, scope FROM route_middlewares ORDER BY route_id, seq"):
            g.routes[rid].middlewares.append((f, sym, scope))
        for row in store.query("SELECT file, symbol, method, url, route_id, line FROM api_calls"):
            g.api_calls.append(ApiCallRow(*row))
        for f, sym, rf, rs in store.query("SELECT file, symbol, ref_file, ref_symbol FROM symbol_refs"):
            g.sym_refs[(f, sym)].append((rf, rs))
        for k in ("engine", "built_at", "build_seconds", "warnings", "last_changes", "packages", "module_exports",
                  "workspace"):
            g.meta[k] = store.get_meta(k)
        return g

    # ------------------------------------------------------------ lookups
    def layer(self, path: str) -> str:
        f = self.files.get(path)
        return (f.layer if f else None) or "module"

    def deps_of(self, path: str, kinds=IMPORT_KINDS, types: bool = False) -> list[Edge]:
        ks = set(kinds) | ({"type"} if types else set())
        return [e for e in self.out.get(path, []) if e.kind in ks]

    def rdeps_of(self, path: str, kinds=IMPORT_KINDS, types: bool = False) -> list[Edge]:
        ks = set(kinds) | ({"type"} if types else set())
        return [e for e in self.inn.get(path, []) if e.kind in ks]

    def importers(self, path: str) -> set[str]:
        """Distinct files that use `path` (any kind except renders), including imports routed through it as a barrel."""
        out = {e.src for e in self.inn.get(path, []) if e.kind != "renders"}
        return out | self.via_users(path)

    def via_users(self, barrel: str) -> set[str]:
        if not hasattr(self, "_via"):
            via: dict[str, set[str]] = defaultdict(set)
            for edges in self.out.values():
                for e in edges:
                    if e.via:
                        via[e.via].add(e.src)
            self._via = via
        return self._via.get(barrel, set())

    def hub_min(self) -> int:
        """Importers that make a file shared: 5% of the files, between 6 and 20."""
        return max(6, min(20, math.ceil(len(self.files) * 0.05)))

    def is_hub(self, path: str) -> bool:
        """Widely imported generic code (ui kit, utils, types) is listed once as SHARED instead of expanded;
        domain code (hooks, services, models...) needs far more importers to count as a hub."""
        n = len(self.importers(path))
        layer = self.layer(path)
        generic = layer in GENERIC_LAYERS or "/ui/" in f"/{path}" or "/lib/" in f"/{path}"
        return n >= (self.hub_min() if generic else max(15, 3 * self.hub_min()))

    def find_files(self, term: str, fuzzy: bool = True) -> list[str]:
        """Resolves a path-ish term to files: exact, suffix, then basename/stem match, then directory."""
        t = term.replace("\\", "/").strip().lstrip("./")
        if t in self.files:
            return [t]
        suffix = [p for p in self.files if p.endswith("/" + t)]
        if suffix or not fuzzy:
            return sorted(suffix)
        stem_hits = []
        tl = t.lower()
        for p in self.files:
            base = posixpath.basename(p).lower()
            stem = langs.strip_ext(base)
            if tl in (base, stem):
                stem_hits.append(p)
        if stem_hits:
            return sorted(stem_hits)
        prefix = t.rstrip("/") + "/"
        return sorted(p for p in self.files if p.startswith(prefix) or ("/" + prefix) in p)

    def symbol_kind(self, path: str, name: str) -> str | None:
        for s in self.symbols.get(path, []):
            if s["name"] == name:
                return s["kind"]
        return None

    # ------------------------------------------------------------ routes
    def find_routes(self, query: str, kinds=("api",)) -> list[RouteRow]:
        """'POST /users', '/api/users/42' (concrete URL), '/users' or a free term."""
        q = query.strip()
        m = re.match(r"^([A-Za-z]+)\s+(\S+)$", q)
        method, path = (m.group(1).upper(), m.group(2)) if m and m.group(1).upper() in HTTP else (None, q)
        cands = [r for r in self.routes.values() if r.kind in kinds and (method is None or r.method in (method, "ALL"))]
        if path.startswith("/"):
            url = normalize_url(path) or path
            exact = [r for r in cands if r.path == url]
            if exact:
                return sorted(exact, key=lambda r: r.id)
            scored = [(score(url, r.path), r) for r in cands]
            best = max((s for s, _ in scored if s[0] == 3), default=None)
            if best:
                return [r for s, r in scored if s == best]
            under = [r for r in cands if r.path.startswith(url.rstrip("/") + "/")]
            if under:
                return sorted(under, key=lambda r: (r.path, r.method))
            best = max((s for s, _ in scored if s[0]), default=None)
            return [r for s, r in scored if s == best] if best else []
        words = term_variants(path)
        hits = []
        for r in cands:
            toks = tokens(r.path) | tokens(r.handler or "") | tokens(posixpath.basename(r.target))
            if all(any(v in toks for v in vs) for vs in words):
                hits.append(r)
        return sorted(hits, key=lambda r: (r.path, r.method))

    def routes_of_file(self, path: str) -> list[RouteRow]:
        return [r for r in self.routes.values() if path in (r.file, r.handler_file)]

    def calls_from(self, path: str) -> list[ApiCallRow]:
        return [c for c in self.api_calls if c.file == path]

    def calls_to(self, rid: int) -> list[ApiCallRow]:
        return [c for c in self.api_calls if c.route_id == rid]


# ---------------------------------------------------------------------- words

def fold(s: str) -> str:
    """Lowercase without accents: 'Cotação' -> 'cotacao' (Portuguese scopes vs English file names)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


@functools.lru_cache(maxsize=None)
def tokens(s: str) -> frozenset[str]:
    """Cached: seed_files re-tokenizes every path/symbol on each analyze (status runs one per chain)."""
    s = langs.strip_ext(s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return frozenset(fold(t) for t in re.split(r"[\W_]+", s) if t)


def word_variants(w: str) -> set[str]:
    w = fold(w)
    out = {w, w + "s", w + "es"}
    if w.endswith("cao"):  # Portuguese: cotacao <-> cotacoes, acao <-> acoes
        out.add(w[:-3] + "coes")
    if w.endswith("coes"):
        out.add(w[:-4] + "cao")
    if w.endswith("ao"):
        out |= {w[:-2] + "oes", w[:-2] + "aes"}
    if w.endswith("y"):
        out.add(w[:-1] + "ies")
    if w.endswith("ies"):
        out.add(w[:-3] + "y")
    if w.endswith("es"):
        out.add(w[:-2])
    if w.endswith("s"):
        out.add(w[:-1])
    return out


def term_variants(term: str) -> list[set[str]]:
    """Every word of the term must match (each in any singular/plural form)."""
    return [word_variants(w) for w in sorted(tokens(term))]


# ---------------------------------------------------------------------- trees (deps / rdeps)

@dataclass
class TreeNode:
    path: str
    edge: Edge | None
    children: list["TreeNode"] = field(default_factory=list)
    repeat: bool = False  # already shown elsewhere (^ref)
    more: int = 0  # children cut by depth


def tree(g: Graph, root: str, depth: int, reverse: bool = False, types: bool = False,
         symbol: str | None = None) -> tuple[TreeNode, set[str]]:
    """BFS-ordered tree without repeats; a node already shown becomes a ^ref leaf. With `symbol` (reverse), the
    first level keeps only the files that import that symbol (or the whole module)."""
    seen = {root}
    top = TreeNode(root, None)
    frontier = [(top, 0)]
    while frontier:
        nxt = []
        for node, d in frontier:
            edges = g.rdeps_of(node.path, types=types) if reverse else g.deps_of(node.path, types=types)
            if symbol and reverse and d == 0:
                edges = [e for e in edges if symbol in e.names or "*" in e.names]
            by_target: dict[str, Edge] = {}
            for e in edges:
                tgt = e.src if reverse else e.dst
                if tgt not in by_target:
                    by_target[tgt] = e
            targets = sorted(by_target)
            if d >= depth:
                node.more = len([t for t in targets if t not in seen])
                continue
            for tgt in targets:
                child = TreeNode(tgt, by_target[tgt], repeat=tgt in seen)
                node.children.append(child)
                if not child.repeat:
                    seen.add(tgt)
                    nxt.append((child, d + 1))
        frontier = nxt
    return top, seen


# ---------------------------------------------------------------------- analyze / flow

@dataclass
class Chain:
    """A feature slice: the files involved and the links between them (imports, renders, API calls)."""
    seeds: set[str]
    files: set[str] = field(default_factory=set)
    shared: set[str] = field(default_factory=set)
    links: dict[str, list[tuple[str, str | None]]] = field(default_factory=lambda: defaultdict(list))  # src -> [(dst, api label)]
    cut: dict[str, int] = field(default_factory=dict)  # file -> children not expanded (depth)
    up: set[str] = field(default_factory=set)  # files reached only by going up (who uses the seeds)
    tests: set[str] = field(default_factory=set)  # tests that call the slice's routes
    broad: bool = False  # too many files match the term: only the areas are shown
    how: str = ""
    words: list = field(default_factory=list)  # the term (analyze), to tell relevant shared files from generic ones
    down_only: set[str] = field(default_factory=set)  # seeds found by symbol name (their imports are not the feature's)


def seed_files(g: Graph, term: str, notes=None, extra: dict | None = None) -> tuple[set[str], str]:
    """Term -> seed files: path, route, then file/folder/symbol names (singular/plural, camel/kebab/snake)."""
    t = term.strip()
    hits = g.find_files(t, fuzzy=False)
    if hits:
        return set(hits), "path"
    if t.startswith("/") or re.match(r"^[A-Za-z]+\s+/", t):
        rs = g.find_routes(t, kinds=("api", "page", "layout"))
        if rs:
            return {f for r in rs for f in (r.file, r.handler_file) if f}, "route"
    words = term_variants(t)
    if not words:
        return set(), ""
    by_name, by_dir, by_sym = set(), set(), set()
    for p in g.files:
        if g.layer(p) in NOISE_LAYERS:
            continue
        name_toks = tokens(posixpath.basename(p))
        if all(any(v in name_toks for v in vs) for vs in words):
            by_name.add(p)
            continue
        dir_toks = set().union(*(tokens(d) for d in p.split("/")[:-1])) if "/" in p else set()
        if all(any(v in dir_toks for v in vs) for vs in words):
            by_dir.add(p)
        for s in g.symbols.get(p, []):
            if s["exported"] and not s["parent"] and all(any(v in tokens(s["name"]) for v in vs) for vs in words):
                by_sym.add(p)
                break
    if len(by_dir) > 40:
        by_dir = set()
    if by_name or by_dir:  # exported symbols only when no file or folder is named after the term ...
        hits = by_name | by_dir
        # ... or in packages the names do not reach: FriendsPage.tsx (web) + listFriends() (server)
        pkgs = {g.files[f].pkg for f in hits}
        other = {f for f in by_sym if g.files[f].pkg not in pkgs and g.layer(f) not in NOISE_LAYERS | {"type"}}
        how = "+".join(k for k, v in (("name", by_name), ("folder", by_dir), ("symbol", other)) if v)
        other = other if len(other) <= 8 else set()
        if extra is not None:
            extra["down_only"] = other  # implementation found by symbol name: its users are not the feature
        return hits | other, how
    if by_sym:
        return by_sym, "symbol"
    if notes is not None and hasattr(notes, "with_scope"):
        by_note = {f for f in notes.with_scope(words) if f in g.files}
        if by_note:
            return by_note, "note scope/tags"
    return set(), ""


def _expand_down(g: Graph, ch: Chain, starts: set[str], depth: int) -> None:
    frontier = deque((s, 0) for s in starts)
    seen = set(starts)
    while frontier:
        f, d = frontier.popleft()
        if f in ch.shared:
            continue
        deps = sorted({e.dst for e in g.out.get(f, []) if e.kind in USE_KINDS})
        if d >= depth:
            rest = [x for x in deps if x not in ch.files and g.layer(x) not in NOISE_LAYERS]
            if rest:
                ch.cut[f] = len(rest)
            continue
        for dst in deps:
            if g.layer(dst) in NOISE_LAYERS:
                continue
            if dst not in ch.seeds and (g.is_hub(dst) or ui_kit(g, dst)):
                ch.shared.add(dst)
                continue
            if (dst, None) not in ch.links[f]:
                ch.links[f].append((dst, None))
            ch.files.add(dst)
            if dst not in seen:
                seen.add(dst)
                frontier.append((dst, d + 1))


def _expand_up(g: Graph, ch: Chain, starts: set[str], limit: int = 80) -> None:
    frontier = deque(starts)
    seen = set(starts)
    while frontier and len(seen) < limit:
        f = frontier.popleft()
        if g.layer(f) in ROOT_LAYERS and f not in ch.seeds:
            continue
        # going up through a barrel would reach everyone who imports anything from it; the real users of this
        # file already point at it directly (edge with `via` = the barrel)
        users = sorted({e.src for e in g.inn.get(f, []) if e.kind in USE_KINDS and e.kind != "reexport"})
        users = [u for u in users if g.layer(u) not in NOISE_LAYERS]
        if len(users) > 12:
            users = sorted(users, key=lambda u: (FLOW_ORDER.get(g.layer(u), 50), u))[:12]
        for u in users:
            if (f, None) not in ch.links[u]:
                ch.links[u].append((f, None))
            if u not in ch.files:
                ch.up.add(u)
            ch.files.add(u)
            if u not in seen:
                seen.add(u)
                frontier.append(u)


def _expand_up_back(g: Graph, ch: Chain, starts: set[str], words: list[set[str]], limit: int = 40) -> None:
    """Who uses back-end code, as far as the feature goes: importers that declare routes, controllers and files
    named after the term. Other services that happen to import it are other features."""
    frontier = deque(starts)
    seen = set(starts)
    while frontier and len(seen) < limit:
        f = frontier.popleft()
        users = sorted({e.src for e in g.inn.get(f, []) if e.kind in USE_KINDS and e.kind != "reexport"})
        users = [u for u in users if g.layer(u) not in NOISE_LAYERS]
        few = f in starts and len(users) <= FEW_USERS
        for u in users:
            routes = any(r.kind == "api" for r in g.routes_of_file(u))
            if not (routes or few or g.layer(u) in BACK_STOPS or about(g, u, words)):
                continue
            if (f, None) not in ch.links[u]:
                ch.links[u].append((f, None))
            if u not in ch.files:
                ch.up.add(u)
            ch.files.add(u)
            if u not in seen and not routes and g.layer(u) not in BACK_STOPS:
                seen.add(u)
                frontier.append(u)


def _symbol_down(g: Graph, ch: Chain, files: set[str], words: list[set[str]], depth: int) -> None:
    """From a file found by symbol name (queries.ts for listFriends), follow what those symbols reference."""
    frontier = deque()
    seen: set[tuple[str, str]] = set()
    for f in files:
        for s in g.symbols.get(f, []):
            if s["exported"] and not s["parent"] and all(any(v in tokens(s["name"]) for v in vs) for vs in words):
                frontier.append((f, s["name"], 0))
                seen.add((f, s["name"]))
    while frontier:
        f, sym, d = frontier.popleft()
        if d >= depth:
            continue
        for rf, rs in refs_of(g, f, sym):
            if (rf, rs) in seen or g.layer(rf) in NOISE_LAYERS or g.symbol_kind(rf, rs) == "type":
                continue
            seen.add((rf, rs))
            if rf != f:
                if g.is_hub(rf) or ui_kit(g, rf):
                    ch.shared.add(rf)
                    continue
                if (rf, None) not in ch.links[f]:
                    ch.links[f].append((rf, None))
                ch.files.add(rf)
            frontier.append((rf, rs, d + 1 if rf != f else d))


def about(g: Graph, f: str, words: list[set[str]]) -> bool:
    """The file's path or one of its exported names is about the term."""
    if not words:
        return False
    toks = set(tokens(f))
    for d in f.split("/")[:-1]:
        toks |= tokens(d)
    if all(any(v in toks for v in vs) for vs in words):
        return True
    return any(s["exported"] and not s["parent"] and all(any(v in tokens(s["name"]) for v in vs) for vs in words)
               for s in g.symbols.get(f, []))


def ui_kit(g: Graph, f: str) -> bool:
    """Design-system components (components/ui/button.tsx): shared plumbing, not part of any feature."""
    parts = f.split("/")
    return g.layer(f) == "component" and "ui" in parts[:-1]


def generic(g: Graph, f: str) -> bool:
    parts = [p.lower() for p in f.split("/")]
    stem = langs.strip_ext(parts[-1])
    return (ui_kit(g, f) or g.layer(f) in ("util", "config", "type") or bool(set(parts[:-1]) & GENERIC_DIRS)
            or stem in GENERIC_STEMS)


def shared_split(g: Graph, ch: Chain) -> tuple[list[str], list[str]]:
    """Shared files worth naming (about the term, data/schema modules, domain modules a seed imports directly)
    and the generic rest (UI kit, utils, i18n, logger...), which is only counted."""
    if not ch.words:
        return sorted(ch.shared), []
    direct = {e.dst for s in ch.seeds - ch.down_only for e in g.out.get(s, [])}
    keep, rest = [], []
    for f in sorted(ch.shared):
        data = bool({p.lower() for p in f.split("/")[:-1]} & DATA_DIRS) or \
            langs.strip_ext(f.rsplit("/", 1)[-1]).lower() in ("schema", "models", "entities")
        if about(g, f, ch.words) or data or (f in direct and not generic(g, f)):
            keep.append(f)
        else:
            rest.append(f)
    return keep, rest


def _front_users(g: Graph, hub: str, call_syms: set[str]) -> set[str]:
    """Files that import, from a shared API client, the functions behind some calls: `api.checkout` is used by
    `useCheckout`, imported by ShopPage.tsx."""
    names = {s.split(".")[0] for s in call_syms if s and "." not in s}
    objs = {s for s in call_syms if s and "." in s}
    for s in g.symbols.get(hub, []):
        if s["parent"]:
            continue
        refs = {rs for rf, rs in g.sym_refs.get((hub, s["name"]), []) if rf == hub}
        if refs & (call_syms | objs):
            names.add(s["name"])
    out = set()
    for e in g.inn.get(hub, []):
        if e.kind not in USE_KINDS or g.layer(e.src) in NOISE_LAYERS:
            continue
        if set(e.names) & names:
            out.add(e.src)
        elif objs and {o.split(".")[0] for o in objs} & set(e.names):  # imports the client object: which members?
            if any((hub, x) in g.sym_refs.get((e.src, s["name"]), []) for s in g.symbols.get(e.src, []) for x in objs):
                out.add(e.src)
    return out


def _mixed_router(g: Graph, f: str, words: list[set[str]]) -> bool:
    """The file declares API routes that are not about the term (one router for the whole API)."""
    return any(not all(any(v in tokens(r.path) for v in vs) for vs in words)
               for r in g.routes_of_file(f) if r.kind == "api")


def _cross(g: Graph, ch: Chain, depth: int, words: list[set[str]] | None = None) -> None:
    """front -> API route -> handler, and handler -> callers on the front."""
    handlers = set()
    hub_calls: dict[str, set[str]] = defaultdict(set)
    for f in sorted((ch.files - ch.up) | (ch.shared if words else set())):  # the slice's own API calls
        hub = f not in ch.seeds and len(g.importers(f)) >= g.hub_min()
        if hub and not words:
            continue  # a shared HTTP client (axios instance + interceptors) is not part of this feature
        for c in g.calls_from(f):
            r = g.routes.get(c.route_id) if c.route_id else None
            if not r:
                continue
            if hub and not all(any(v in tokens(c.url) for v in vs) for vs in words or []):
                continue  # the shared client: only the calls about this term (GET /friends for "friends")
            if hub:
                hub_calls[f].add(c.symbol or "")
            if hub and f in ch.shared:
                ch.shared.discard(f)
                ch.files.add(f)
            link = (r.target, r.label)
            if link not in ch.links[f]:
                ch.links[f].append(link)
            if r.target not in ch.files and not (words and _mixed_router(g, r.target, words)):
                handlers.add(r.target)  # a router full of other features is shown, not expanded
            ch.files.add(r.target)
    if handlers:
        _expand_down(g, ch, handlers, max(1, depth - 1))
    if words:  # routes about the term (POST /payments/checkout for "payments"): who calls them on the front
        for r in g.routes.values():
            if r.kind != "api" or not all(any(v in tokens(r.path) for v in vs) for vs in words):
                continue
            for c in g.calls_to(r.id):
                if g.layer(c.file) == "test":
                    continue
                if len(g.importers(c.file)) >= g.hub_min():
                    hub_calls[c.file].add(c.symbol or "")
                    ch.shared.discard(c.file)
                link = (r.target, r.label)
                if link not in ch.links[c.file]:
                    ch.links[c.file].append(link)
                ch.files.update((c.file, r.target))
    fronts = set()
    for hub, syms in hub_calls.items():
        for u in _front_users(g, hub, syms):
            if (hub, None) not in ch.links[u]:
                ch.links[u].append((hub, None))
            if u not in ch.files:
                fronts.add(u)
            ch.files.add(u)
    up_front = {u for u in fronts if g.layer(u) not in ROOT_LAYERS}
    if up_front:
        _expand_up(g, ch, up_front)
    callers = set()
    for r in g.routes.values():
        if r.kind == "api" and r.target in ch.seeds:
            for c in g.calls_to(r.id):
                if g.layer(c.file) == "test":
                    ch.tests.add(c.file)
                    continue
                link = (r.target, r.label)
                if link not in ch.links[c.file]:
                    ch.links[c.file].append(link)
                if c.file not in ch.files:
                    callers.add(c.file)
                ch.files.add(c.file)
    if callers:
        _expand_up(g, ch, callers)


MAX_SEEDS = 60


def areas(files: set[str], levels: int = 4) -> list[tuple[str, int]]:
    """Where a broad term lives: file counts per folder (first `levels` segments)."""
    counts: dict[str, int] = defaultdict(int)
    for f in files:
        counts["/".join(f.split("/")[:-1][:levels]) or "."] += 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def analyze(g: Graph, term: str, depth: int, notes=None) -> Chain:
    extra: dict = {}
    seeds, how = seed_files(g, term, notes, extra)
    ch = Chain(seeds=seeds, files=set(seeds), how=how, words=term_variants(term) if how != "path" else [])
    if not seeds:
        return ch
    if len(seeds) > MAX_SEEDS:  # the whole domain matches: a chain of thousands of files helps nobody
        ch.broad = True
        return ch
    up_from = {s for s in seeds if not g.is_hub(s) and s not in extra.get("down_only", ())} or seeds  # a hub's users are the whole app
    up_from = {s for s in up_from if g.layer(s) not in ("page", "layout", "entry")}  # a page's user is the router
    by_symbol = extra.get("down_only", set())
    ch.down_only = set(by_symbol)
    _expand_up(g, ch, {s for s in up_from if g.layer(s) in FRONT_LAYERS or how == "path"})
    _expand_up_back(g, ch, {s for s in up_from if g.layer(s) not in FRONT_LAYERS and how != "path"}, ch.words)
    _expand_down(g, ch, seeds - by_symbol, depth)
    _symbol_down(g, ch, by_symbol, ch.words, depth)
    _cross(g, ch, depth, term_variants(term) if how != "path" else None)
    ch.shared -= ch.files
    return ch


def flow(g: Graph, target: str, depth: int) -> Chain:
    """End-to-end chain from a page/route down to the models; for an API route, starts at its callers."""
    t = target.strip()
    seeds: set[str] = set()
    if t.startswith("/") or re.match(r"^[A-Za-z]+\s+/", t):
        rs = g.find_routes(t, kinds=("api", "page", "layout"))
        for r in rs:
            seeds.add(r.target)
            if r.kind == "api":
                seeds |= {c.file for c in g.calls_to(r.id)}
    else:
        seeds = set(g.find_files(t))
        if len(seeds) > 6:
            seeds = set()
    ch = Chain(seeds=seeds, files=set(seeds), how="flow")
    if seeds:
        callers = {s for s in seeds if g.layer(s) not in ROOT_LAYERS and any(c.route_id for c in g.calls_from(s))}
        if callers:
            _expand_up(g, ch, callers)
        _expand_down(g, ch, seeds, depth + 2)
        _cross(g, ch, depth + 2)
        ch.shared -= ch.files
    return ch


def roots_of(g: Graph, ch: Chain) -> list[str]:
    targets = {d for links in ch.links.values() for d, _ in links}
    roots = [f for f in ch.files if f not in targets]
    if not roots:
        roots = sorted(ch.seeds)
    return sorted(roots, key=lambda f: (FLOW_ORDER.get(g.layer(f), 50), f))


# ---------------------------------------------------------------------- endpoint

@dataclass
class EndpointView:
    route: RouteRow
    down: list[tuple[int, str, str | None]] = field(default_factory=list)  # (indent, file, symbol)
    shared: set[str] = field(default_factory=set)
    front: list[list[tuple[str, str | None]]] = field(default_factory=list)  # paths page -> ... -> caller#sym
    tests: set[str] = field(default_factory=set)
    externals: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)


CODE_KINDS = {"class", "function", "component", "hook", "method"}


def refs_of(g: Graph, f: str, sym: str) -> list[tuple[str, str]]:
    refs = g.sym_refs.get((f, sym), [])
    if not refs and "." in sym:  # obj.method without its own entry -> the object
        refs = g.sym_refs.get((f, sym.split(".")[0]), [])
    return sorted(set(refs))


def endpoint(g: Graph, r: RouteRow, depth: int) -> EndpointView:
    v = EndpointView(route=r)
    listed = {f for f, _ in r.mounts} | {f for f, _s, _sc in r.middlewares if f} | {r.file}
    skip_syms = {(f, s) for f, s, _sc in r.middlewares if f}
    v.files |= listed
    hf = r.handler_file
    if hf:
        v.files.add(hf)
        v.externals |= g.externals.get(hf, set())
        seen_syms = {(hf, r.handler)} | skip_syms
        seen_files = {hf} | listed
        _sym_down(g, v, hf, r.handler or "", 0, depth, seen_syms, seen_files)
    v.shared -= v.files
    for c in g.calls_to(r.id):
        if g.layer(c.file) == "test":
            v.tests.add(c.file)
            continue
        v.front.append(path_to_root(g, c.file) + [(c.file, c.symbol)])
        v.files.add(c.file)
    users = set()
    for f in v.files:
        users |= g.importers(f)
    v.tests |= {u for u in users if g.layer(u) == "test"}
    return v


def _sym_down(g: Graph, v: EndpointView, f: str, sym: str, d: int, depth: int, seen_syms: set, seen_files: set) -> None:
    """What a symbol really uses: same-file helpers are shown when they are code (a service class next to its
    controller), same-file constants are looked through; other files are followed by symbol too."""
    if d > depth:
        return
    refs = refs_of(g, f, sym)
    if not refs and g.symbol_kind(f, sym) is None:  # inline/anonymous handler: follow the whole file
        _walk_down(g, v, f, d, depth, seen_files)
        return
    for rf, rs in refs:
        if (rf, rs) in seen_syms:
            continue
        seen_syms.add((rf, rs))
        kind = g.symbol_kind(rf, rs)
        if kind == "type" or g.layer(rf) in NOISE_LAYERS | {"type"}:
            continue
        if rf == f and kind not in CODE_KINDS:
            _sym_down(g, v, rf, rs, d, depth, seen_syms, seen_files)
            continue
        if rf != f and g.is_hub(rf):
            v.shared.add(rf)
            continue
        v.down.append((d, rf, rs))
        v.files.add(rf)
        v.externals |= g.externals.get(rf, set())
        seen_files.add(rf)
        _sym_down(g, v, rf, rs, d + 1, depth, seen_syms, seen_files)


def _walk_down(g: Graph, v: EndpointView, f: str, d: int, depth: int, seen: set[str]) -> None:
    """Below the handler's direct references the chain follows whole files."""
    if d > depth:
        return
    for dst in sorted({e.dst for e in g.out.get(f, []) if e.kind in IMPORT_KINDS}):
        if g.layer(dst) in NOISE_LAYERS:
            continue
        if g.is_hub(dst):
            v.shared.add(dst)
            continue
        if dst in seen:
            continue
        seen.add(dst)
        v.down.append((d, dst, None))
        v.files.add(dst)
        v.externals |= g.externals.get(dst, set())
        _walk_down(g, v, dst, d + 1, depth, seen)


def path_to_root(g: Graph, start: str, limit: int = 200) -> list[tuple[str, str | None]]:
    """Shortest chain of importers from a page/entry down to `start` (exclusive)."""
    prev: dict[str, str | None] = {start: None}
    q = deque([start])
    found = None
    while q and len(prev) < limit:
        f = q.popleft()
        if f != start and g.layer(f) in ("page", "layout", "entry", "route"):
            found = f
            break
        for e in sorted(g.inn.get(f, []), key=lambda e: e.src):
            if e.kind in USE_KINDS and e.src not in prev and g.layer(e.src) not in NOISE_LAYERS:
                prev[e.src] = f
                q.append(e.src)
    if not found:
        return []
    chain = []
    cur = found
    while cur and cur != start:
        chain.append((cur, None))
        cur = prev.get(cur)
    return chain


# ---------------------------------------------------------------------- impact / cycles / orphans

def impact(g: Graph, targets: list[str]) -> dict:
    affected = set(targets)
    q = deque(targets)
    while q:
        f = q.popleft()
        for u in {e.src for e in g.inn.get(f, []) if e.kind != "calls_api"} | g.via_users(f):
            if u not in affected:
                affected.add(u)
                q.append(u)
    routes = [r for r in g.routes.values() if r.kind == "api" and (
        r.target in affected or r.file in affected or any(f in affected for f, _ in r.mounts)
        or any(f in affected for f, _s, _sc in r.middlewares if f))]
    pages = [r for r in g.routes.values() if r.kind in ("page", "layout") and r.target in affected]
    tests = {f for f in affected if g.layer(f) == "test"}
    rids = {r.id for r in routes}
    tests |= {c.file for c in g.api_calls if c.route_id in rids and g.layer(c.file) == "test"}
    callers = {c.file for c in g.api_calls if c.route_id in rids and g.layer(c.file) != "test"}
    return {"affected": affected, "routes": routes, "pages": pages, "tests": tests, "callers": callers}


CYCLE_KINDS = ("import", "reexport", "side-effect")


def cycles(g: Graph) -> list[list[str]]:
    """Strongly connected components of value imports (type-only, dynamic and renders excluded)."""
    adj = {f: sorted({e.dst for e in g.out.get(f, []) if e.kind in CYCLE_KINDS}) for f in g.files}
    return cycles_of(adj)


def cycles_of(adj: dict[str, list[str]]) -> list[list[str]]:
    """One concrete cycle per strongly connected component (Tarjan, iterative) of any graph of names."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        work = [(v, iter(adj.get(v, [])))]
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        while work:
            node, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(adj.get(w, []))))
                    advanced = True
                    break
                if w in on:
                    low[node] = min(low[node], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))

    for f in sorted(adj):
        if f not in index:
            strong(f)
    return sorted((_cycle_path(adj, c) for c in out), key=lambda c: (len(c), c))


def _cycle_path(adj: dict[str, list[str]], comp: list[str]) -> list[str]:
    """One concrete cycle inside an SCC, starting at its first file."""
    members = set(comp)
    start = comp[0]
    prev = {start: None}
    q = deque([start])
    while q:
        f = q.popleft()
        for n in adj.get(f, []):
            if n == start:
                path = [f]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return list(reversed(path))
            if n in members and n not in prev:
                prev[n] = f
                q.append(n)
    return comp


def orphans(g: Graph) -> list[str]:
    used = set()
    for edges in g.inn.values():
        for e in edges:
            used.add(e.dst)
    for r in g.routes.values():
        used |= {r.file, r.target} | {f for f, _ in r.mounts} | {f for f, _s, _sc in r.middlewares if f}
    out = []
    for p, f in g.files.items():
        if p in used or f.is_entry or g.via_users(p):
            continue
        if f.layer in ("test", "config", "page", "layout", "api-route", "entry"):
            continue
        out.append(p)
    return sorted(out)


def chain_files(g: Graph, key: str, depth: int, notes=None) -> set[str]:
    """Files of a feature chain (what a chain note covers): flow for routes, analyze for terms."""
    t = key.strip()
    ch = flow(g, t, depth) if t.startswith("/") or re.match(r"^[A-Za-z]+\s+/", t) else analyze(g, t, depth, notes)
    return {f for f in ch.files if g.layer(f) not in NOISE_LAYERS}
