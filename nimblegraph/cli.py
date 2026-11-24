"""`nimblegraph` command line."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from nimblegraph import __version__, build, config, query, update, views
from nimblegraph import notes as notes_mod
from nimblegraph import tokens as tk
from nimblegraph.classify import FLOW_ORDER
from nimblegraph.query import Graph, TreeNode, tree
from nimblegraph.render import Out, finish, progressive
from nimblegraph.store import Store


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", help="project root (default: discovered from the current folder)")
    p.add_argument("--budget", type=int, help="token budget for the answer (default 1500)")
    p.add_argument("--depth", type=int, help="traversal depth (default 4)")
    p.add_argument("--engine", choices=["auto", "node", "python"], default="auto", help="parser engine")
    p.add_argument("--no-build", action="store_true", help="answer from the existing index without updating it")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="nimblegraph", description="Dependency graph of a project for Claude Code.")
    ap.add_argument("--version", action="version", version=f"nimblegraph {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")

    p = sub.add_parser("setup", help="install the latest Node LTS engine, typescript and the `nimblegraph` shim")
    p.add_argument("--no-shim", action="store_true")
    p.add_argument("--force", action="store_true", help="reinstall typescript")

    p = sub.add_parser("build", help="index the project (incremental)")
    _common(p)
    p.add_argument("--full", action="store_true", help="discard the parse cache")
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("status", help="index summary, engine, notes, unresolved imports")
    _common(p)

    p = sub.add_parser("file", help="note, exports, imports and importers of a file")
    _common(p)
    p.add_argument("target")

    for name, help_ in (("deps", "what a file uses, as a tree"),
                        ("rdeps", "who uses a file (or file#Symbol: who imports that symbol), as a tree")):
        p = sub.add_parser(name, help=help_)
        _common(p)
        p.add_argument("target")
        p.add_argument("--types", action="store_true", help="include type-only imports")

    p = sub.add_parser("analyze", help="the whole chain around a term: routes, pages, flow front -> back")
    _common(p)
    p.add_argument("term", nargs="+")

    p = sub.add_parser("endpoint", help="every file involved in an endpoint, in request order")
    _common(p)
    p.add_argument("route", nargs="+", help='"POST /users", /users/42, or a term')
    p.add_argument("--files", action="store_true", help="only the list of paths")

    p = sub.add_parser("flow", help="end-to-end chain of a page or route, front to model")
    _common(p)
    p.add_argument("target", nargs="+")

    p = sub.add_parser("routes", help="method, path, file and handler of every route and page")
    _common(p)
    p.add_argument("filter", nargs="*")

    p = sub.add_parser("find", help="search paths, symbols, routes and notes")
    _common(p)
    p.add_argument("text", nargs="+")

    p = sub.add_parser("impact", help="pages, routes and tests affected by a change")
    _common(p)
    p.add_argument("files", nargs="*")
    p.add_argument("--git", action="store_true", help="use the files changed in git (diff + untracked)")

    p = sub.add_parser("entries", help="entry points")
    _common(p)

    p = sub.add_parser("cycles", help="import cycles")
    _common(p)

    p = sub.add_parser("orphans", help="files nobody imports")
    _common(p)

    p = sub.add_parser("describe", help="write or update notes (file, symbol, batch or chain)")
    _common(p)
    p.add_argument("target", nargs="?", help="file to describe")
    p.add_argument("--symbol", help="note for a symbol of the file (file#Symbol)")
    p.add_argument("--chain", help="note for a chain: a term (user) or a route (GET /api/users)")
    p.add_argument("--batch", help="JSON file with many notes, or - for stdin")
    p.add_argument("--json", help="the same JSON as --batch, inline (one shell line)")
    for field in ("title", "summary", "resp", "scope", "flow"):
        p.add_argument(f"--{field}")
    p.add_argument("--tags", help="comma separated")

    p = sub.add_parser("todo", help="files without a note or with a stale one, most important first")
    _common(p)
    p.add_argument("--limit", type=int, default=15)

    p = sub.add_parser("update", help="rebuild, show what changed and which notes to rewrite (--ai: let Claude do it)")
    _common(p)
    p.add_argument("target", nargs="*")
    p.add_argument("--ai", action="store_true", help="run Claude Code headless to write the notes")
    p.add_argument("--local", action="store_true", help="write the notes with the local model (nimblegraph local)")
    p.add_argument("--dry-run", action="store_true", help="with --ai: print the claude command instead of running it")

    p = sub.add_parser("local", help="notes written by a local Hugging Face model (Ollama / LM Studio / llama-server)")
    lsub = p.add_subparsers(dest="local_cmd", metavar="<status|setup|describe>")
    lp = lsub.add_parser("status", help="server, model, settings and the progress of a background run")
    _common(lp)
    lp = lsub.add_parser("setup", help="check the server, download the model (Ollama) and describe one file as a test")
    _common(lp)
    lp.add_argument("--model", help="model name, e.g. hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:UD-Q3_K_XL")
    lp = lsub.add_parser("describe", help="write notes with the local model")
    _common(lp)
    lp.add_argument("target", nargs="*", help="term, route or file (default: the files of `nimblegraph todo`)")
    lp.add_argument("--all", action="store_true", help="every file without an up-to-date note")
    lp.add_argument("--limit", type=int, default=15, help="files per run without --all (default 15)")
    lp.add_argument("--force", action="store_true", help="rewrite notes that are up to date too")
    lp.add_argument("--chains", action="store_true", help="with --all: also a chain note per scope (feature)")
    lp.add_argument("--dry-run", action="store_true", help="list the files only")
    lp.add_argument("--background", action="store_true",
                    help="run detached (survives closing the terminal); follow with `nimblegraph local status`")
    lp.add_argument("--log", help=argparse.SUPPRESS)
    lp = lsub.add_parser("stop", help="stop a background run after the file in progress (nothing is lost)")
    _common(lp)

    p = sub.add_parser("init", help="create .nimblegraph/, ignore graph.db in git; --claude-md adds the CLAUDE.md section")
    p.add_argument("--root")
    p.add_argument("--claude-md", action="store_true", help="append the nimblegraph section to CLAUDE.md")
    p.add_argument("--hook", action="store_true", help="rebuild the index after every Edit/Write (Claude Code hook)")
    p.add_argument("--guard", action="store_true",
                   help="after every Edit/Write, tell Claude about new architecture violations (nimblegraph check --hook)")
    p.add_argument("--engine", choices=["auto", "node", "python"], default="auto")

    p = sub.add_parser("export", help="mermaid | dot | json of the graph or of a chain")
    _common(p)
    p.add_argument("target", nargs="*", help="term, route or file (default: whole graph)")
    p.add_argument("--format", choices=["mermaid", "dot", "json"], default="mermaid")
    p.add_argument("--out", help="write to a file instead of stdout")

    p = sub.add_parser("ui", help="visual graph in the browser, updated live")
    _common(p)
    p.add_argument("--port", type=int, default=4777)
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.add_argument("--dev", action="store_true", help="also run the Vite dev server (UI development)")

    from nimblegraph import cli_arch
    cli_arch.add_parsers(sub, _common)
    return ap


# ---------------------------------------------------------------------- helpers

RE_MSYS = re.compile(r"^[A-Za-z]:[/\\](?:[^/\\]+[/\\])*?Git(?:[/\\]usr)?(/.*)$")


def _unmangle(arg: str) -> str:
    """Git Bash rewrites `/api/users` into `C:/Program Files/Git/api/users`; undo it."""
    m = RE_MSYS.match(arg)
    return m.group(1) if m else arg


def _term(parts) -> str:
    if isinstance(parts, str):
        parts = [parts]
    return " ".join(_unmangle(p) for p in parts).strip()


def _setup_stdout() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _cfg(args) -> config.Config:
    return config.load(Path(args.root) if args.root else None)


def _load(args, cfg: config.Config | None = None) -> tuple[config.Config, Graph, notes_mod.Notes]:
    cfg = cfg or _cfg(args)
    if not args.no_build or not cfg.db_path.is_file():
        build.run(cfg, engine_pref=args.engine)
    store = Store(cfg.db_path)
    try:
        g = Graph.load(store)
    finally:
        store.close()
    return cfg, g, notes_mod.load_for(cfg, g)


def _budget(args, cfg) -> int:
    return args.budget or int(cfg["budget"])


def _depth(args, cfg) -> int:
    return args.depth if args.depth is not None else int(cfg["depth"])


def _pick(g: Graph, term: str, quiet: bool = False) -> str | None:
    hits = g.find_files(term)
    if len(hits) == 1:
        return hits[0]
    if quiet:
        return None
    if not hits:
        print(f"no file matches '{term}'")
    else:
        print(f"'{term}' matches {len(hits)} files; be more specific:")
        for h in hits[:15]:
            print(f" {h}")
        if len(hits) > 15:
            print(f" +{len(hits) - 15} more")
    return None


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _names(e) -> str:
    return "{" + ",".join(e.names) + "}" if e.names else ""


# ---------------------------------------------------------------------- build / status / file / deps

def cmd_build(args) -> int:
    cfg = _cfg(args)
    r = build.run(cfg, engine_pref=args.engine, full=args.full)
    if args.quiet:
        return 0
    changes = [f"{sign}{len(items)}" for sign, items in (("+", r.added), ("-", r.removed), ("~", r.changed)) if items]
    print(f"@build {cfg.root} | {r.files} files, {r.parsed} parsed, {r.edges} edges, {r.routes} routes,"
          f" {r.unresolved} unresolved | {' '.join(changes) or 'no changes'} | {r.seconds}s | {r.engine}")
    for w in r.warnings:
        print(f"WARN {w}")
    return 0


def cmd_status(args) -> int:
    cfg, g, notes = _load(args)
    store = Store(cfg.db_path)
    try:
        n_sym = store.query("SELECT COUNT(*) FROM symbols WHERE parent IS NULL")[0][0]
        kinds = Counter(k for (k,) in store.query("SELECT kind FROM edges"))
        ext_pkgs = Counter(p for (p,) in store.query("SELECT pkg FROM externals"))
    finally:
        store.close()
    o = Out(f"status {cfg.root.name}")
    loc = sum(f.loc for f in g.files.values())
    tok = sum(f.tokens for f in g.files.values())
    o.add(f"engine {g.meta.get('engine') or '?'} | built {_ago(g.meta.get('built_at'))} in {g.meta.get('build_seconds')}s")
    o.add(f"files {len(g.files)} | loc {loc} | reading everything ~{tk.fmt(tok)} tok | symbols {n_sym}")
    o.add("edges " + " ".join(f"{k} {v}" for k, v in kinds.most_common()))
    layers = Counter(f.layer or "module" for f in g.files.values())
    o.add("LAYERS " + ", ".join(f"{l} {n}" for l, n in sorted(layers.items(), key=lambda x: FLOW_ORDER.get(x[0], 50))))
    rk = Counter(r.kind for r in g.routes.values())
    if g.routes:
        matched = sum(1 for c in g.api_calls if c.route_id)
        o.add(f"ROUTES api {rk.get('api', 0)} | pages {rk.get('page', 0)} | layouts {rk.get('layout', 0)}"
              f" | api calls {len(g.api_calls)} ({matched} matched)")
    describable = [p for p in g.files if g.layer(p) not in notes_mod.SKIP_LAYERS]
    c = notes.counts(describable)
    chains_bad = sum(1 for k in notes.chains
                     if notes.chain_state(k, query.chain_files(g, k, _depth(args, cfg), notes)) != "ok")
    o.add(f"NOTES {c['described']}/{len(describable)} files | {c['stale']} stale | chains {len(notes.chains)}"
          f" ({chains_bad} stale)" + (f" | {c['orphans']} notes of removed files" if c["orphans"] else ""))
    pkgs = Counter(f.pkg for f in g.files.values() if f.pkg)
    if len(pkgs) > 1:
        o.add("PACKAGES " + ", ".join(f"{p} ({n})" for p, n in pkgs.most_common()))
    if ext_pkgs:
        o.add(f"EXT {len(ext_pkgs)} pkgs: " + ", ".join(p for p, _ in ext_pkgs.most_common(20))
              + (" ..." if len(ext_pkgs) > 20 else ""))
    unres = [(f, s, ln) for f, items in sorted(g.unresolved.items()) for s, ln in items]
    if unres:
        o.add(f"UNRESOLVED {len(unres)}")
        for f, s, ln in unres[:30]:
            o.add(f" {o.path(f)}:{ln} {s}")
        if len(unres) > 30:
            o.add(f" +{len(unres) - 30} more")
    for w in g.meta.get("warnings") or []:
        o.add(f"WARN {w}")
    o.read_cost = tok
    print(finish(o, _budget(args, cfg)))
    return 0


def _file_view(g: Graph, notes: notes_mod.Notes, path: str, detail: int) -> Out:
    f = g.files[path]
    o = Out(f"file {path}", stats=f"{f.layer or 'module'}{' entry' if f.is_entry else ''} | {f.loc} loc"
            f" ~{tk.fmt(f.tokens)} tok" + (f" | {f.pkg}" if f.pkg else ""))
    o.paths.add(path)
    note = notes.files.get(path)
    if note:
        star = " (STALE: the file changed after this note)" if notes.stale(path) else ""
        o.add(f"NOTE {note.get('title', '')}{star}")
        if detail >= 1:
            for k in ("summary", "resp", "scope", "flow"):
                if note.get(k):
                    o.add(f" {k}: {note[k]}")
            if note.get("tags"):
                o.add(" tags: " + ", ".join(note["tags"]))
    else:
        o.add("NOTE none -> nimblegraph describe " + path + " --title .. --summary ..")
    sym_notes = [(k.split("#", 1)[1], n) for k, n in notes.files.items() if k.startswith(path + "#")]
    for sym, n in sym_notes:
        o.add(f"NOTE #{sym} - {notes.text(n, detail)}")
    defined = g.routes_of_file(path)
    if defined:
        o.add("ROUTES " + ", ".join(r.label + (f"#{r.handler}" if r.handler and r.handler != "default" else "")
                                    for r in sorted(defined, key=lambda r: (r.path, r.method))))
    calls = g.calls_from(path)
    if calls:
        o.add("CALLS " + ", ".join(
            f"{c.method} {c.url}" + (f" > {o.path(g.routes[c.route_id].target)}" if c.route_id else " (no route)")
            for c in calls))
    syms = g.symbols.get(path, [])
    exported = [s for s in syms if s["exported"] and not s["parent"]]
    if exported:
        o.add("EXPORTS " + ", ".join(f"{s['name']}({s['kind']})" for s in exported))
    if detail >= 3:
        local = [s for s in syms if not s["exported"] and not s["parent"] and s["kind"] != "type"]
        if local:
            o.add("LOCAL " + ", ".join(f"{s['name']}:{s['line']}" for s in local))
        by_parent: dict[str, list[str]] = {}
        for m in (s for s in syms if s["parent"]):
            by_parent.setdefault(m["parent"], []).append(f"{m['name'].split('.', 1)[1]}:{m['line']}")
        for parent, ms in by_parent.items():
            o.add(f"METHODS {parent}: " + ", ".join(ms))
    outs = sorted((e for e in g.out.get(path, []) if e.kind not in ("renders", "calls_api")),
                  key=lambda e: (e.kind != "import", e.dst))
    if outs:
        o.add(f"IMPORTS {len({e.dst for e in outs})}")
        for e in outs:
            kind = "" if e.kind == "import" else f"{e.kind} "
            via = f" via {o.path(e.via)}" if e.via and detail >= 3 else ""
            names = _names(e) if detail >= 3 else ""
            o.add(f" > {kind}{o.path(e.dst)} {names}{via}".rstrip() + (notes.suffix(e.dst, None, 1) if detail >= 1 else ""))
    ins = sorted({e.src for e in g.inn.get(path, []) if e.kind not in ("renders", "calls_api")} | g.via_users(path))
    if ins:
        o.add(f"IMPORTED-BY {len(ins)}")
        if detail == 0 and len(ins) > 10:
            o.add(" " + ", ".join(o.path(s) for s in ins[:10]) + f" +{len(ins) - 10}")
        else:
            for s in ins:
                o.add(f" < {o.path(s)}")
    callers = sorted({(c.file, c.symbol) for r in defined for c in g.calls_to(r.id)}, key=lambda x: (x[0], x[1] or ""))
    if callers:
        o.add(f"CALLED-BY {len(callers)}")
        for f2, sym in callers:
            o.add(f" < {o.path(f2)}" + (f"#{sym}" if sym else ""))
    if g.externals.get(path):
        o.add("EXT " + ", ".join(sorted(g.externals[path])))
    if g.unresolved.get(path):
        o.add("UNRESOLVED " + ", ".join(f"{s}:{ln}" for s, ln in g.unresolved[path]))
    o.read_cost = f.tokens
    o.hint = f"nimblegraph deps {path}"
    return o


def cmd_file(args) -> int:
    cfg, g, notes = _load(args)
    path = _pick(g, args.target)
    if not path:
        return 1
    print(progressive(lambda d: _file_view(g, notes, path, d), _budget(args, cfg)))
    return 0


def _render_tree(o: Out, g: Graph, notes, node: TreeNode, indent: int, reverse: bool, detail: int, level: int = 1) -> None:
    for c in node.children:
        e = c.edge
        tag = "" if e.kind == "import" else f"{e.kind} "
        names = _names(e) if not reverse and detail >= 3 else ""
        arrow = "<" if reverse else ">"
        if c.repeat:
            o.add(f"{' ' * indent}{arrow} ^{o.path(c.path)}")
            continue
        if detail == 0 and level >= 3:
            hidden = _count(c)
            o.add(f"{' ' * indent}{arrow} {tag}{o.path(c.path)}" + (f" (+{hidden})" if hidden else ""))
            continue
        more = f" (+{c.more})" if c.more else ""
        o.add(f"{' ' * indent}{arrow} {tag}{o.path(c.path)} {names}".rstrip() + notes.suffix(c.path, None, detail) + more)
        _render_tree(o, g, notes, c, indent + 1, reverse, detail, level + 1)


def _count(node: TreeNode) -> int:
    return sum(1 + _count(c) for c in node.children if not c.repeat) + node.more


def cmd_tree(args, reverse: bool) -> int:
    cfg, g, notes = _load(args)
    target, _, symbol = args.target.partition("#")  # rdeps src/shop.ts#moveFunds: only who imports that symbol
    path = _pick(g, target)
    if not path:
        return 1
    depth = _depth(args, cfg)
    top, seen = tree(g, path, depth, reverse=reverse, types=args.types, symbol=symbol or None)
    label = path + (f"#{symbol}" if symbol else "")

    def make(detail: int) -> Out:
        o = Out(f"{'rdeps' if reverse else 'deps'} {label}", stats=f"{len(seen)} files, depth {depth}")
        o.add(o.path(path) + notes.suffix(path, None, detail))
        _render_tree(o, g, notes, top, 1, reverse, detail)
        if not reverse:
            ext = sorted({p for f in seen for p in g.externals.get(f, ())})
            if ext:
                o.add("EXT " + ", ".join(ext))
        o.read_cost = sum(g.files[f].tokens for f in seen if f in g.files)
        o.hint = f"nimblegraph {'rdeps' if reverse else 'deps'} {path} --depth {max(1, depth - 1)}"
        return o

    print(progressive(make, _budget(args, cfg)))
    return 0


# ---------------------------------------------------------------------- feature queries

def cmd_analyze(args) -> int:
    cfg, g, notes = _load(args)
    term = _term(args.term)
    depth = _depth(args, cfg)
    ch = query.analyze(g, term, depth, notes)
    seeds = sorted(ch.seeds)
    hint = f"nimblegraph file {seeds[0]}" if len(seeds) == 1 else f"nimblegraph analyze {term} --depth {max(1, depth - 1)}"
    print(progressive(lambda d: views.chain_view(g, ch, f"analyze {term}", notes, hint=hint, term=term, detail=d),
                      _budget(args, cfg)))
    return 0 if ch.files else 1


def cmd_endpoint(args) -> int:
    cfg, g, notes = _load(args)
    q = _term(args.route)
    routes = g.find_routes(q)
    if not routes:
        print(f"no endpoint matches '{q}'; see `nimblegraph routes`")
        return 1
    same_path = len({r.path for r in routes}) == 1
    if len(routes) == 1 or (same_path and len(routes) <= 3):
        budget = _budget(args, cfg) // len(routes)
        texts = []
        for r in routes:
            v = query.endpoint(g, r, _depth(args, cfg))
            if args.files:
                texts.append(finish(views.endpoint_view(g, v, notes, files_only=True), budget))
            else:
                texts.append(progressive(lambda d, v=v: views.endpoint_view(g, v, notes, detail=d), budget))
        print("\n".join(texts))
        return 0
    print(finish(views.endpoint_list(g, routes, q), _budget(args, cfg)))
    return 0


def cmd_flow(args) -> int:
    cfg, g, notes = _load(args)
    target = _term(args.target)
    ch = query.flow(g, target, _depth(args, cfg))
    if not ch.files:
        print(f"no page, route or file matches '{target}'")
        return 1
    print(progressive(lambda d: views.chain_view(g, ch, f"flow {target}", notes, term=target, detail=d),
                      _budget(args, cfg)))
    return 0


def cmd_routes(args) -> int:
    cfg, g, _notes = _load(args)
    print(finish(views.routes_view(g, _term(args.filter) or None), _budget(args, cfg)))
    return 0


def cmd_find(args) -> int:
    cfg, g, notes = _load(args)
    print(finish(views.find_view(g, _term(args.text), notes), _budget(args, cfg)))
    return 0


def _git_changed(root: Path) -> list[str]:
    out: list[str] = []
    for cmd in (["git", "-C", str(root), "diff", "--name-only", "--relative", "HEAD"],
                ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        out += [line.strip() for line in r.stdout.splitlines() if line.strip()]
    return out


def cmd_impact(args) -> int:
    cfg, g, _notes = _load(args)
    names = _git_changed(cfg.root) if args.git else list(args.files)
    targets: list[str] = []
    for n in names:
        hits = g.find_files(n, fuzzy=not args.git)
        targets += [h for h in hits if h not in targets]
    if not targets:
        print("no indexed files to analyse" + (" (git shows no changes in source files)" if args.git else ""))
        return 1
    res = query.impact(g, targets)
    print(finish(views.impact_view(g, targets, res), _budget(args, cfg)))
    return 0


def cmd_entries(args) -> int:
    cfg, g, notes = _load(args)
    o = Out("entries")
    ent = sorted(p for p, f in g.files.items() if f.is_entry)
    o.stats = f"{len(ent)} files"
    for p in ent:
        o.add(f" {o.path(p)} ({g.layer(p)}){notes.suffix(p, None, 1)}")
    o.read_cost = sum(g.files[p].tokens for p in ent)
    print(finish(o, _budget(args, cfg)))
    return 0


def cmd_cycles(args) -> int:
    cfg, g, _notes = _load(args)
    print(finish(views.cycles_view(g, query.cycles(g)), _budget(args, cfg)))
    return 0


def cmd_orphans(args) -> int:
    cfg, g, _notes = _load(args)
    print(finish(views.orphans_view(g, query.orphans(g)), _budget(args, cfg)))
    return 0


# ---------------------------------------------------------------------- notes

FILE_FIELDS = ("title", "summary", "resp", "scope", "flow", "tags")


def _describe_file(cfg, g, notes, target: str, fields: dict, symbol: str | None) -> str:
    hits = g.find_files(target, fuzzy=False) or g.find_files(target)
    if len(hits) != 1:
        raise notes_mod.NoteError(f"'{target}' matches {len(hits)} indexed files")
    path = hits[0]
    if symbol and not any(s["name"] in (symbol, symbol.split(".")[0]) for s in g.symbols.get(path, [])):
        raise notes_mod.NoteError(f"{path} has no symbol {symbol}")
    note = notes.set_file(path, fields, notes_mod.file_hash(cfg.root / path), symbol)
    key = f"{path}#{symbol}" if symbol else path
    return f" ok {key} (~{notes_mod.note_cost(note)} tok per read)"


def _describe_chain(cfg, g, notes, key: str, fields: dict, depth: int) -> str:
    key = notes_mod.chain_key(key)
    files = query.chain_files(g, key, depth, notes)
    if not files:
        raise notes_mod.NoteError(f"chain '{key}' matches no files")
    note = notes.set_chain(key, fields, {f: g.files[f].hash for f in files})
    cost = tk.estimate(" ".join(str(note.get(k, "")) for k in notes_mod.CHAIN_FIELDS))
    return f" ok chain {key}: {len(files)} files (~{cost} tok per read)"


def _read_stdin() -> str:
    """Bytes from stdin as UTF-8 (the Windows console code page would garble accents)."""
    data = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode(sys.stdin.encoding or "utf-8", errors="replace")


def _batch_entries(raw: str) -> list[dict]:
    data = json.loads(raw)
    if isinstance(data, dict):
        entries = [{"file": k, **v} for k, v in (data.get("files") or {}).items()]
        entries += [{"chain": k, **v} for k, v in (data.get("chains") or {}).items()]
        if not entries and ("file" in data or "chain" in data):
            entries = [data]
        return entries
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    raise ValueError("expected a JSON list or object")


def cmd_describe(args) -> int:
    cfg, g, notes = _load(args)
    depth = _depth(args, cfg)
    ok, bad = [], []
    if args.batch or args.json:
        try:
            if args.json:
                raw = args.json
            else:
                raw = _read_stdin() if args.batch == "-" else Path(args.batch).read_text(encoding="utf-8-sig")
            entries = _batch_entries(raw)
        except (OSError, ValueError) as e:
            print(f"error: invalid batch: {e}\nformat: {update.BATCH_FORMAT}")
            return 2
        for e in entries:
            target = e.pop("file", None)
            chain = e.pop("chain", None)
            symbol = e.pop("symbol", None)
            label = chain or target or "?"
            try:
                if chain:
                    ok.append(_describe_chain(cfg, g, notes, chain, e, depth))
                elif target:
                    ok.append(_describe_file(cfg, g, notes, target, e, symbol))
                else:
                    raise notes_mod.NoteError("entry needs `file` or `chain`")
            except notes_mod.NoteError as err:
                bad.append(f" REJECTED {label}: {err}")
    else:
        fields = {k: getattr(args, k) for k in FILE_FIELDS if getattr(args, k) is not None}
        try:
            if args.chain:
                ok.append(_describe_chain(cfg, g, notes, _term(args.chain), fields, depth))
            elif args.target:
                ok.append(_describe_file(cfg, g, notes, args.target, fields, args.symbol))
            else:
                print("usage: nimblegraph describe <file> --title .. | --chain <term> --title .. | --batch <file|->")
                return 2
        except notes_mod.NoteError as err:
            bad.append(f" REJECTED {args.chain or args.target}: {err}")
    if ok:
        notes.save()
    print(f"@describe | {len(ok)} saved" + (f", {len(bad)} rejected" if bad else ""))
    for line in ok + bad:
        print(line)
    if bad:
        print("limits (tok): title 12, summary 40, resp 30, scope 12, flow 40, tags <= 6 | chain: title 12, summary 60, flow 80")
    return 1 if bad else 0


def cmd_todo(args) -> int:
    cfg, g, notes = _load(args)
    print(progressive(lambda d: update.todo_view(g, notes, args.limit, d), _budget(args, cfg)))
    return 0


def cmd_update(args) -> int:
    cfg = _cfg(args)
    r = build.run(cfg, engine_pref=args.engine)
    args.no_build = True
    cfg, g, notes = _load(args, cfg)
    depth = _depth(args, cfg)
    target = _term(args.target)
    if args.ai:
        return update.run_ai(cfg.root, target or None, cfg["notes_language"], dry_run=args.dry_run)
    if args.local:
        return _local_describe(cfg, g, notes, target or None, all_files=False, limit=15, force=False, chains=False,
                               dry_run=args.dry_run, depth=depth)
    if target:
        key = notes_mod.chain_key(target)
        found = notes.find_chain(target)
        if found:
            key = found[0]
        files = query.chain_files(g, target, depth, notes)
        if not files:
            print(f"no files match '{target}'")
            return 1
        state = notes.chain_state(key, files)
        print(progressive(lambda d: update.target_view(g, notes, target, files, key, state, d), _budget(args, cfg)))
        return 0
    fresh = r.dirty
    changes = {"added": r.added, "removed": r.removed, "changed": r.changed,
               "routes_added": r.routes_added, "routes_removed": r.routes_removed} if fresh else (
        g.meta.get("last_changes") or {})
    states = {k: notes.chain_state(k, query.chain_files(g, k, depth, notes)) for k in notes.chains}
    print(progressive(lambda d: update.changes_view(g, notes, changes, fresh, states, d), _budget(args, cfg)))
    return 0


def cmd_init(args) -> int:
    from nimblegraph import project
    cfg = _cfg(args)
    for line in project.init(cfg, claude_md=args.claude_md, hook=args.hook, guard=args.guard):
        print(line)
    r = build.run(cfg, engine_pref=args.engine)
    print(f"indexed {r.files} files, {r.edges} edges, {r.routes} routes in {r.seconds}s ({r.engine})")
    print("next: nimblegraph analyze <term> | nimblegraph todo | nimblegraph ui")
    return 0


def cmd_export(args) -> int:
    from nimblegraph import export
    cfg, g, notes = _load(args)
    target = _term(args.target) or None
    files, edges = export.select(g, target, _depth(args, cfg), notes)
    if not files:
        print(f"nothing matches '{target}'")
        return 1
    text = export.FORMATS[args.format](g, files, edges, notes)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out}: {len(files)} files, {len(edges)} edges ({args.format})")
    else:
        sys.stdout.write(text)
    return 0


def _local_describe(cfg, g, notes, target, all_files, limit, force, chains, dry_run, depth) -> int:
    from nimblegraph import local_ai
    client = local_ai.client_for(cfg)
    if target:
        files = query.chain_files(g, target, depth, notes)
        if not files:
            print(f"no files match '{target}'")
            return 1
        paths = local_ai.select_files(g, notes, files, force)
        chain_list = [notes_mod.chain_key(target)]
    else:
        paths = local_ai.select_files(g, notes, None, force)
        if not all_files:
            paths = paths[:limit]
        chain_list = []
    if dry_run:
        for p in paths:
            print(p)
        print(f"{len(paths)} files" + (f" + chain {chain_list[0]}" if chain_list else ""))
        return 0
    try:
        client.detect()
        if not client.has_model():
            print(f"model {client.model} is not loaded in {client.url}: run `nimblegraph local setup`")
            return 3
    except local_ai.LocalAIError as e:
        print(f"error: {e}\nstart Ollama (or LM Studio / llama-server) and run `nimblegraph local setup`")
        return 3
    print(f"describing {len(paths)} files with {client.model} ({client.kind} at {client.url}) — Ctrl+C stops, "
          "progress is saved", flush=True)

    def show(ev):
        mark = "skip" if ev.get("skipped") else "ok " if ev["ok"] else "ERR"
        tail = ev["title"] if ev["ok"] else ev["error"]
        print(f"[{ev['i']}/{ev['total']}] {mark} {ev['path']} ({ev['seconds']}s, eta {ev['eta']}) - {tail}", flush=True)

    import threading
    stop = threading.Event()
    stop_file = _bg_paths(cfg)["stop"]
    stop_file.unlink(missing_ok=True)

    def watch_stop():
        while not stop.wait(2):
            if stop_file.exists():
                print("stop requested: finishing the file in progress", flush=True)
                stop.set()
    threading.Thread(target=watch_stop, daemon=True).start()
    try:
        res = local_ai.run_batch(cfg, g, notes, paths, client, cfg["notes_language"], show, stop, chain_list)
    except KeyboardInterrupt:
        stop.set()
        notes.save()
        print("stopped; notes written so far are saved. Run the same command to continue.")
        return 130
    if chains and all_files and not target and not stop.is_set():
        # missing chains, plus existing ones that went stale (a file changed, entered or left the chain)
        keys = [k for k in sorted(set(local_ai.chain_keys_from_scopes(notes)) | set(notes.chains))
                if force or k not in notes.chains
                or notes.chain_state(k, query.chain_files(g, k, depth, notes)) != "ok"]
        if keys:
            print(f"chain notes for {len(keys)} scopes: {', '.join(keys)}", flush=True)
            local_ai.run_batch(cfg, g, notes, [], client, cfg["notes_language"], show, stop, keys)
    rate = res["out_tokens"] / res["seconds"] if res["seconds"] else 0
    print(f"@local describe | {res['ok']}/{res['done']} saved | {res['seconds']}s | ~{rate:.1f} tok/s generated")
    return 0 if res["ok"] == res["done"] else 1


def _bg_paths(cfg) -> dict:
    return {k: cfg.dir / f"local-describe.{k}" for k in ("log", "pid", "stop")}


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        return str(pid) in r.stdout
    try:
        # a finished child of this very process stays a zombie until reaped, and kill(pid, 0) succeeds on zombies
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass  # not our child: the usual case, the CLI that started it has exited
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _bg_running(cfg) -> int | None:
    pid_file = _bg_paths(cfg)["pid"]
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def _start_background(cfg, args) -> int:
    paths = _bg_paths(cfg)
    pid = _bg_running(cfg)
    if pid:
        print(f"a background run is already going (PID {pid}); see `nimblegraph local status`")
        return 1
    paths["stop"].unlink(missing_ok=True)
    argv = [sys.executable, "-m", "nimblegraph", "local", "describe", "--root", str(cfg.root), "--no-build",
            "--log", str(paths["log"]), *(_term(args.target).split() if args.target else [])]
    argv += ["--all"] if args.all else ["--limit", str(args.limit)]
    argv += [flag for flag, on in (("--force", args.force), ("--chains", args.chains)) if on]
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
             if os.name == "nt" else 0)
    proc = subprocess.Popen(argv, cwd=cfg.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags, start_new_session=os.name != "nt")
    paths["pid"].write_text(str(proc.pid), encoding="ascii")
    print(f"running in background (PID {proc.pid})\nlog: {paths['log']}\nprogress: nimblegraph local status | stop: nimblegraph local stop")
    return 0


def _background_status(cfg, notes_total: int | None = None) -> None:
    paths = _bg_paths(cfg)
    pid = _bg_running(cfg)
    if not paths["log"].is_file() and not pid:
        return
    lines = []
    try:
        lines = paths["log"].read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        pass
    progress = [ln for ln in lines if ln.startswith("[")]
    errors = sum(1 for ln in progress if "] ERR " in ln)
    state = f"RUNNING (PID {pid})" if pid else "finished / stopped"
    print(f"batch   {state}" + (f" | {len(progress)} done, {errors} errors" if progress else ""))
    if progress:
        print(f"last    {progress[-1][:160]}")
    if lines and lines[-1].startswith("@local describe"):
        print(f"result  {lines[-1]}")


def cmd_local(args) -> int:
    from nimblegraph import local_ai
    cfg = _cfg(args)
    sub = args.local_cmd or "status"
    if sub == "stop":
        pid = _bg_running(cfg)
        if not pid:
            print("no background run")
            return 1
        _bg_paths(cfg)["stop"].write_text("stop", encoding="ascii")
        print(f"asked PID {pid} to stop after the file in progress; notes are saved")
        return 0
    if sub == "describe" and args.background:
        return _start_background(cfg, args)
    client = local_ai.client_for(cfg)
    if sub == "setup" and args.model:
        client.model = args.model
        _save_local_setting(cfg, "model", args.model)
    if sub in ("status", "setup"):
        s = local_ai.settings(cfg)
        print(f"server  {client.url}")
        print(f"model   {client.model}")
        print(f"context {s['context']} tok | code per file {s['max_file_tokens']} tok | language {cfg['notes_language']}")
        try:
            kind = client.detect()
            models = client.models()
            print(f"status  {kind} running; {len(models)} models" + (f": {', '.join(models[:6])}" if models else ""))
        except local_ai.LocalAIError as e:
            print(f"status  OFF ({e})")
            print("        install Ollama (winget install Ollama.Ollama), start it, then: nimblegraph local setup")
            return 3
        has = client.has_model()
        print(f"loaded  {'yes' if has else 'no'}")
        if sub == "status":
            _background_status(cfg)
            try:
                notes = notes_mod.Notes.load(cfg.notes_path)
                local = sum(1 for n in notes.files.values() if str(n.get("by", "")).startswith("local:"))
                print(f"notes   {len(notes.files)} files, {local} written by the local model, {len(notes.chains)} chains")
            except OSError:
                pass
            return 0
        if not has:
            print(f"downloading {client.model} ...", flush=True)
            last = [""]

            def prog(ev):
                st = ev.get("status", "")
                if ev.get("total"):
                    pct = int(100 * (ev.get("completed") or 0) / ev["total"])
                    st = f"{st} {pct}%"
                if st != last[0] and (not ev.get("total") or st.endswith("0%")):
                    print("  " + st, flush=True)
                    last[0] = st
            client.pull(prog)
        _cfg_args = argparse.Namespace(**{**vars(args), "no_build": False})
        cfg, g, notes = _load(_cfg_args, cfg)
        sample = local_ai.select_files(g, notes)[:1] or sorted(g.files)[:1]
        if not sample:
            print("no files to test with")
            return 0
        print(f"test: {sample[0]} (the first call also loads the model into memory) ...", flush=True)
        r = local_ai.describe_file(client, g, notes, cfg.root, sample[0], cfg["notes_language"],
                                   int(local_ai.settings(cfg)["max_file_tokens"]))
        if not r.ok:
            print(f"test failed: {r.error}")
            return 1
        notes.save()
        n = notes.files[sample[0]]
        print(f"ok in {r.seconds:.1f}s\n  title: {n.get('title')}\n  summary: {n.get('summary')}\n"
              f"  resp: {n.get('resp')}\n  scope: {n.get('scope')} | flow: {n.get('flow')} | tags: {n.get('tags')}")
        print("next: nimblegraph local describe --all   (or: nimblegraph local describe <term>)")
        return 0
    if sub == "describe":
        if args.log:
            sys.stdout = open(args.log, "w", encoding="utf-8-sig", buffering=1)  # noqa: SIM115 (lives with the run)
            sys.stderr = sys.stdout
        cfg, g, notes = _load(args, cfg)
        return _local_describe(cfg, g, notes, _term(args.target) or None, args.all, args.limit, args.force,
                               args.chains, args.dry_run, _depth(args, cfg))
    return 2


def _save_local_setting(cfg, key: str, value: str) -> None:
    """Writes [local_ai] key = value into .nimblegraph/config.toml (creating the section)."""
    import re as _re
    path = cfg.dir / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f'{key} = "{value}"'
    if "[local_ai]" not in text:
        text = text.rstrip("\n") + f"\n\n[local_ai]\n{line}\n"
    else:
        head, _, tail = text.partition("[local_ai]")
        body, sep, rest = tail.partition("\n[")
        if _re.search(rf"^{key}\s*=", body, _re.M):
            body = _re.sub(rf"^{key}\s*=.*$", line, body, flags=_re.M)
        else:
            body = body.rstrip("\n") + f"\n{line}\n"
        text = head + "[local_ai]" + body + (sep + rest if sep else "")
    path.write_text(text, encoding="utf-8", newline="\n")
    cfg.settings.setdefault("local_ai", {})[key] = value


def cmd_ui(args) -> int:
    from nimblegraph import install, server
    cfg = _cfg(args)
    if not server.UI_DIST.is_dir() and not args.dev:
        print("building the UI (first run) ...", flush=True)
        install.build_ui()
    srv, app = server.serve(cfg, port=args.port, open_browser=not (args.no_open or args.dev), engine_pref=args.engine)
    url = app.url
    dev = None
    if args.dev:
        dev = install.start_ui_dev(srv.server_address[1])
        url = "http://127.0.0.1:5173/"
        if not args.no_open:
            import webbrowser
            webbrowser.open(url)
    print(f"nimblegraph ui: {url}  (api {app.url}, live; Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if dev:
            dev.terminate()
    return 0


# ---------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    if args.cmd == "setup":
        from nimblegraph import install
        return install.run(skip_shim=args.no_shim)
    handlers = {
        "build": cmd_build,
        "status": cmd_status,
        "file": cmd_file,
        "deps": lambda a: cmd_tree(a, reverse=False),
        "rdeps": lambda a: cmd_tree(a, reverse=True),
        "analyze": cmd_analyze,
        "endpoint": cmd_endpoint,
        "flow": cmd_flow,
        "routes": cmd_routes,
        "find": cmd_find,
        "impact": cmd_impact,
        "entries": cmd_entries,
        "cycles": cmd_cycles,
        "orphans": cmd_orphans,
        "describe": cmd_describe,
        "todo": cmd_todo,
        "update": cmd_update,
        "ui": cmd_ui,
        "init": cmd_init,
        "export": cmd_export,
        "local": cmd_local,
    }
    from nimblegraph import cli_arch
    handlers.update(cli_arch.HANDLERS)
    try:
        return handlers[args.cmd](args)
    except config.RootError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
