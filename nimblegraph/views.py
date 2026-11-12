"""Text views for the query commands (analyze, endpoint, flow, routes, find, impact, cycles, orphans)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from nimblegraph.classify import FLOW_ORDER
from nimblegraph.query import Chain, EndpointView, Graph, RouteRow, areas, roots_of, shared_split, term_variants, tokens
from nimblegraph.render import Out


class NoNotes:
    """Used when there is no notes.json: no titles, nothing stale."""

    chains: dict = {}

    def suffix(self, path: str, symbol: str | None = None, detail: int = 3) -> str:
        return ""

    def missing(self, paths, graph=None) -> list[str]:
        return []

    def find_chain(self, term: str):
        return None

    def chain_state(self, key: str, files) -> str:
        return "missing"

    def text(self, note: dict, detail: int) -> str:
        return note.get("title", "")

    def stale(self, path: str, symbol: str | None = None) -> bool:
        return False

    def get(self, path: str, symbol: str | None = None):
        return None


def _order(g: Graph, f: str) -> tuple:
    return (FLOW_ORDER.get(g.layer(f), 50), f)


def _ref(o: Out, f: str | None, sym: str | None) -> str:
    if not f:
        return sym or "?"
    return o.path(f) + (f"#{sym}" if sym and sym not in ("default",) else "")


# ---------------------------------------------------------------------- analyze / flow

MAX_LEVEL_COLLAPSED = 3  # detail 0: deeper flow levels become counts


def chain_view(g: Graph, ch: Chain, title: str, notes=None, hint: str | None = None, term: str = "",
               detail: int = 3) -> Out:
    notes = notes or NoNotes()
    o = Out(title, stats=f"{len(ch.files)} files")
    if not ch.files:
        o.add("no files match; try `nimblegraph find <text>` or `nimblegraph routes <text>`")
        return o
    files = ch.files
    if ch.broad:
        return _broad_view(g, ch, o, term, detail)
    found = notes.find_chain(term) if term else None
    if found:
        key, cnote = found
        state = notes.chain_state(key, files)
        star = "*" if state.startswith("stale") else ""
        o.add(f"CHAIN {key}{star} - {notes.text(cnote, detail)}")
        if detail >= 2 and cnote.get("flow"):
            o.add(f" flow: {cnote['flow']}")
    linked = {label for links in ch.links.values() for _d, label in links if label}
    api = [r for r in g.routes.values() if r.kind == "api" and (r.target in ch.seeds or r.label in linked)]
    words = term_variants(term) if term else []
    on_term = [r for r in api if words and all(any(v in tokens(r.path) for v in vs) for vs in words)]
    if on_term:  # a file with many controllers: keep the routes about the term (plus the ones the front calls)
        api = [r for r in api if r in on_term or r.label in linked]
    if api:
        o.add("ROUTES")
        groups: dict[tuple, list[RouteRow]] = defaultdict(list)
        for r in api:
            groups[(r.path, r.target)].append(r)
        items = sorted(groups.items())
        cap = 12 if detail >= 2 else 6
        for (path, target), rs in items[:cap]:
            methods = ",".join(sorted({r.method for r in rs}))
            handler = rs[0].handler if len(rs) == 1 else None
            o.add(f" {methods} {path} > {_ref(o, target, handler)}")
        if len(items) > cap:
            o.add(f" +{len(items) - cap} more routes -> nimblegraph routes {term}".rstrip())
    pages = [r for r in g.routes.values() if r.kind in ("page", "layout") and r.target in files]
    if pages:
        o.add("PAGES")
        pages = sorted(pages, key=lambda r: (r.kind == "layout", r.target not in ch.seeds, r.path, r.target))
        cap = 12 if detail >= 2 else 6
        if len(pages) > cap:
            o.add(f" ({len(pages) - cap} more pages not shown)")
        for r in sorted(pages[:cap], key=lambda r: (r.path, r.target)):
            o.add(f" {r.path} > {o.path(r.target)}{' (layout)' if r.kind == 'layout' else ''}{notes.suffix(r.target, None, detail)}")
    o.add("FLOW")
    shown: set[str] = set()

    def grouped(f: str) -> list[tuple[str, str | None]]:
        """Several API calls from one file to the same handler file become one line."""
        plain, apis = [], defaultdict(list)
        for dst, label in ch.links.get(f, []):
            if label:
                apis[dst].append(label)
            else:
                plain.append((dst, None))
        out = sorted(plain, key=lambda x: _order(g, x[0]))
        for dst, labels in sorted(apis.items()):
            out.append((dst, labels[0] if len(labels) == 1 else _api_group(labels)))
        return out

    def hidden(f: str) -> int:
        """Marks everything below f as shown and counts it (detail 0)."""
        n = 0
        for dst, _label in grouped(f):
            if dst in files and dst not in shown:
                shown.add(dst)
                n += 1 + hidden(dst)
        return n

    def visit(f: str, ind: int, level: int) -> None:
        for dst, label in grouped(f):
            if dst not in files:
                continue
            child = ind
            if label:
                o.add(" " * ind + f"~> {label}")
                child = ind + 1
            if dst in shown:
                o.add(" " * child + f"> ^{o.path(dst)}")
                continue
            shown.add(dst)
            more = ch.cut.get(dst, 0)
            collapse = detail == 0 and level >= MAX_LEVEL_COLLAPSED
            if collapse:
                more += hidden(dst)
            cut = f" (+{more})" if more else ""
            o.add(" " * child + f"> {o.path(dst)}{notes.suffix(dst, None, detail)}{cut}")
            if not collapse:
                visit(dst, child + 1, level + 1)

    for r in roots_of(g, ch) + sorted(files, key=lambda f: _order(g, f)):
        if r in shown:
            continue
        shown.add(r)
        cut = f" (+{ch.cut[r]})" if r in ch.cut else ""
        o.add(f" {o.path(r)}{notes.suffix(r, None, detail)}{cut}")
        visit(r, 2, 1)
    if ch.shared:
        keep, rest = shared_split(g, ch)
        tail = f" (+{len(rest)} generic: ui kit, utils, i18n...)" if rest else ""
        o.add("SHARED " + (", ".join(o.path(f) for f in keep) if keep else "") + tail)
    tests = sorted(ch.tests | {u for f in ch.seeds for u in g.importers(f) if g.layer(u) == "test"})
    if tests:
        o.add("TESTS " + ", ".join(o.path(f) for f in tests))
    missing = notes.missing(sorted(files), g)
    if missing:
        stale = any(notes.stale(f) for f in missing)
        o.add("NO-NOTE " + ", ".join(o.path(f) for f in missing if not notes.stale(f))
              + ("  (* = stale note)" if stale else ""))
    ext = Counter(p for f in files for p in g.externals.get(f, ()))
    if ext:
        o.add("EXT " + ", ".join(p for p, _ in ext.most_common(20)) + (" ..." if len(ext) > 20 else ""))
    o.read_cost = sum(g.files[f].tokens for f in files | ch.shared if f in g.files)
    o.hint = hint
    return o


def _broad_view(g: Graph, ch: Chain, o: Out, term: str, detail: int) -> Out:
    """The term matches a whole domain: show where it lives and how to narrow it down."""
    o.stats = f"{len(ch.seeds)} files match: too broad for one chain"
    o.add("AREAS (files matching per folder)")
    rows = areas(ch.seeds)
    cap = 25 if detail >= 2 else 12
    for folder, n in rows[:cap]:
        o.add(f" {n:4} {o.path(folder + '/') if folder != '.' else '.'}")
    if len(rows) > cap:
        o.add(f" +{len(rows) - cap} folders")
    words = Counter()
    seed_words = {w for vs in term_variants(term) for w in vs}
    for f in ch.seeds:
        for w in tokens(f.rsplit("/", 1)[-1]):
            if w not in seed_words and len(w) > 2 and w not in ("tsx", "spec", "test", "index"):
                words[w] += 1
    common = [w for w, _ in words.most_common(12)]
    routes = [r for r in g.routes.values() if r.kind == "api" and all(
        any(v in tokens(r.path) for v in vs) for vs in term_variants(term))]
    if routes:
        o.add(f"ROUTES {len(routes)} -> nimblegraph routes {term}")
    o.add("NARROW " + ", ".join(f'nimblegraph analyze "{term} {w}"' for w in common[:6]))
    o.add(f"  or a file/folder: nimblegraph analyze <path>, a route: nimblegraph endpoint \"<METHOD /path>\"")
    o.read_cost = sum(g.files[f].tokens for f in ch.seeds if f in g.files)
    return o


def _api_group(labels: list[str]) -> str:
    paths = [lb.split(" ", 1)[1] for lb in labels]
    segs = [p.split("/") for p in paths]
    common = []
    for parts in zip(*segs):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    prefix = "/".join(common) or "/"
    return f"{len(labels)} routes {prefix}/**" if any(p != prefix for p in paths) else f"{','.join(sorted({lb.split(' ')[0] for lb in labels}))} {prefix}"


# ---------------------------------------------------------------------- endpoint

def _mw_label(scope: str) -> str:
    return {"guard": "guard ", "pipe": "pipe ", "interceptor": "interceptor ", "filter": "filter ", "next": "next "}.get(scope, "")


def endpoint_view(g: Graph, v: EndpointView, notes=None, files_only: bool = False, detail: int = 3) -> Out:
    notes = notes or NoNotes()
    r = v.route
    o = Out(f"endpoint {r.label}", stats=f"{len(v.files)} files")
    if files_only:
        ordered = [f for f, _ in r.mounts] + [f for f, _s, sc in r.middlewares if f and sc != "validation"]
        ordered += [f for f, _s, sc in r.middlewares if f and sc == "validation"]
        ordered += [r.target] + [f for _d, f, _s in v.down] + sorted(v.shared)
        ordered += [f for path in v.front for f, _ in path] + sorted(v.tests)
        seen = []
        for f in ordered:
            if f and f not in seen:
                seen.append(f)
        o.lines = seen
        o.paths = set()
        o.read_cost = sum(g.files[f].tokens for f in seen if f in g.files)
        return o
    mounts: list[tuple[str, str]] = []
    for f, p in r.mounts:
        if mounts and mounts[-1][0] == f:
            mounts[-1] = (f, mounts[-1][1] or p)
        else:
            mounts.append((f, p))
    if len(mounts) > 1 or (mounts and mounts[0][1]):
        o.add("MOUNT " + " > ".join(f"{o.path(f)} {p}".rstrip() for f, p in mounts))
    mws = [m for m in r.middlewares if m[2] != "validation"]
    if mws:
        o.add("MIDDLEWARE")
        ext_run: list[str] = []
        ext_owner = None
        for f, sym, scope in mws + [(None, None, None)]:
            if f is None and sym is not None:
                name, _, owner = sym.partition("|")
                owner = owner or r.file
                if ext_owner not in (None, owner) and ext_run:
                    o.add(f" {o.path(ext_owner)}: " + ", ".join(ext_run))
                    ext_run = []
                ext_run.append(f"{_mw_label(scope)}{name}")
                ext_owner = owner
                continue
            if ext_run:
                o.add(f" {o.path(ext_owner)}: " + ", ".join(ext_run))
                ext_run, ext_owner = [], None
            if sym is None:
                break
            o.add(f" {_mw_label(scope)}{_ref(o, f, sym)}{notes.suffix(f, sym, min(detail, 1))}")
    val = [m for m in r.middlewares if m[2] == "validation"]
    if val:
        o.add("VALIDATION " + ", ".join(_ref(o, f, s) for f, s, _ in val))
    o.add(f"HANDLER {_ref(o, r.target, r.handler)}{notes.suffix(r.target, r.handler, detail)}")
    if v.down:
        o.add("DOWN")
        last_shown = None
        for d, f, sym in v.down:
            if detail == 0 and d >= 2:  # deep levels become a count on the last shown line
                if last_shown is not None:
                    o.lines[last_shown] = re.sub(r"( \(\+(\d+)\))?$", lambda m: f" (+{int(m.group(2) or 0) + 1})",
                                                 o.lines[last_shown], count=1)
                continue
            o.add(" " * (d + 1) + f"> {_ref(o, f, sym)}{notes.suffix(f, sym, detail)}")
            last_shown = len(o.lines) - 1
    if v.shared:
        o.add("SHARED " + ", ".join(o.path(f) for f in sorted(v.shared)))
    if v.front:
        o.add("FRONT")
        for path in v.front[:8]:
            o.add(" " + " > ".join(_ref(o, f, s) for f, s in path))
        if len(v.front) > 8:
            o.add(f" +{len(v.front) - 8} more callers")
    if v.tests:
        o.add("TESTS " + ", ".join(o.path(f) for f in sorted(v.tests)))
    missing = [f for f in notes.missing(sorted(v.files), g) if not notes.stale(f)]
    if missing:
        o.add("NO-NOTE " + ", ".join(o.path(f) for f in missing))
    if v.externals:
        o.add("EXT " + ", ".join(sorted(v.externals)))
    o.read_cost = sum(g.files[f].tokens for f in v.files | v.shared if f in g.files)
    o.hint = f"nimblegraph endpoint \"{r.label}\" --files"
    return o


def endpoint_list(g: Graph, routes: list[RouteRow], query: str) -> Out:
    o = Out(f"endpoint {query}", stats=f"{len(routes)} routes")
    for r in routes:
        o.add(f" {r.label} > {_ref(o, r.target, r.handler)}")
    o.add(f"> pick one: nimblegraph endpoint \"{routes[0].label}\"")
    o.read_cost = sum(g.files[f].tokens for f in {r.target for r in routes} if f in g.files)
    return o


# ---------------------------------------------------------------------- routes / find

def routes_view(g: Graph, flt: str | None) -> Out:
    rs = sorted(g.routes.values(), key=lambda r: (r.kind != "api", r.path, r.method))
    if flt:
        low = flt.lower()
        words = term_variants(flt)
        rs = [r for r in rs if low in f"{r.method} {r.path} {r.target} {r.handler or ''}".lower()
              or (words and all(any(v in tokens(r.path) | tokens(r.handler or "") for v in vs) for vs in words))]
    o = Out("routes" + (f" {flt}" if flt else ""), stats=f"{len(rs)} routes")
    api = [r for r in rs if r.kind == "api"]
    pages = [r for r in rs if r.kind != "api"]
    if api:
        o.add("API")
        for r in api:
            o.add(f" {r.label} > {_ref(o, r.target, r.handler)}")
    if pages:
        o.add("PAGES")
        for r in pages:
            o.add(f" {r.path} > {o.path(r.target)}{' (layout)' if r.kind == 'layout' else ''}")
    if not rs:
        o.add("no routes" + (" match" if flt else " detected"))
    o.read_cost = sum(g.files[f].tokens for f in {r.target for r in rs} if f in g.files)
    return o


def find_view(g: Graph, text: str, notes=None) -> Out:
    low = text.lower()
    words = term_variants(text)
    o = Out(f"find {text}")
    files = [p for p in sorted(g.files) if low in p.lower()]
    syms = []
    for p in sorted(g.symbols):
        for s in g.symbols[p]:
            name = s["name"]
            if low in name.lower() or (words and all(any(v in tokens(name) for v in vs) for vs in words)):
                syms.append((p, s))
    routes = [r for r in g.routes.values() if low in r.path.lower()]
    note_hits = [f" {o.path(k.split('#')[0])}{'#' + k.split('#')[1] if '#' in k else ''} - {n.get('title', '')}"
                 for k, n in (notes.search(text) if notes and hasattr(notes, "search") else [])][:40]
    if note_hits:
        note_hits = ["NOTES", *note_hits]
    o.stats = f"{len(files)} files, {len(syms)} symbols, {len(routes)} routes"
    if files:
        o.add("FILES")
        for p in files[:60]:
            o.add(f" {o.path(p)} ({g.layer(p)})")
    if syms:
        o.add("SYMBOLS")
        for p, s in syms[:80]:
            o.add(f" {o.path(p)}#{s['name']} {s['kind']}:{s['line']}")
    if routes:
        o.add("ROUTES")
        for r in sorted(routes, key=lambda r: (r.path, r.method))[:40]:
            o.add(f" {r.label} > {_ref(o, r.target, r.handler)}")
    for line in note_hits:
        o.add(line)
    if not (files or syms or routes or note_hits):
        o.add("nothing found")
    o.read_cost = sum(g.files[f].tokens for f in set(files) | {p for p, _ in syms} if f in g.files)
    return o


# ---------------------------------------------------------------------- impact / cycles / orphans

def impact_view(g: Graph, targets: list[str], res: dict) -> Out:
    affected = res["affected"]
    o = Out("impact " + (targets[0] if len(targets) == 1 else f"{len(targets)} files"),
            stats=f"{len(affected)} affected")
    layers = Counter(g.layer(f) for f in affected - set(targets))
    if layers:
        o.add("LAYERS " + ", ".join(f"{l} {n}" for l, n in sorted(layers.items(), key=lambda x: FLOW_ORDER.get(x[0], 50))))
    if res["routes"]:
        o.add(f"ROUTES {len(res['routes'])}")
        for r in sorted(res["routes"], key=lambda r: (r.path, r.method)):
            o.add(f" {r.label} > {_ref(o, r.target, r.handler)}")
    if res["pages"]:
        o.add(f"PAGES {len(res['pages'])}")
        for r in sorted(res["pages"], key=lambda r: r.path):
            o.add(f" {r.path} > {o.path(r.target)}")
    if res["callers"]:
        o.add("FRONT " + ", ".join(o.path(f) for f in sorted(res["callers"])))
    entries = sorted(f for f in affected if g.files.get(f) and g.files[f].is_entry)
    if entries:
        o.add("ENTRIES " + ", ".join(o.path(f) for f in entries))
    if res["tests"]:
        o.add(f"TESTS {len(res['tests'])}")
        for f in sorted(res["tests"]):
            o.add(f" {o.path(f)}")
    o.read_cost = sum(g.files[f].tokens for f in affected if f in g.files)
    return o


def cycles_view(g: Graph, cyc: list[list[str]]) -> Out:
    o = Out("cycles", stats=f"{len(cyc)} cycles")
    for c in cyc:
        o.add(" " + " > ".join(o.path(f) for f in c + [c[0]]))
    if not cyc:
        o.add("no import cycles")
    o.read_cost = sum(g.files[f].tokens for c in cyc for f in c if f in g.files)
    return o


def orphans_view(g: Graph, files: list[str]) -> Out:
    o = Out("orphans", stats=f"{len(files)} files nobody imports")
    by_layer: dict[str, list[str]] = defaultdict(list)
    for f in files:
        by_layer[g.layer(f)].append(f)
    for layer in sorted(by_layer, key=lambda l: FLOW_ORDER.get(l, 50)):
        o.add(f"{layer.upper()} " + ", ".join(o.path(f) for f in by_layer[layer]))
    if not files:
        o.add("none")
    o.read_cost = sum(g.files[f].tokens for f in files if f in g.files)
    return o


def is_route_query(q: str) -> bool:
    return q.startswith("/") or bool(re.match(r"^[A-Za-z]+\s+/", q))
