"""Governance commands of the `nimblegraph` CLI: check, rules (and, registered here too, the architecture readers)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nimblegraph import arch as arch_mod
from nimblegraph import arch_views, build, config, gitutil
from nimblegraph import rules as rules_mod
from nimblegraph.render import finish
from nimblegraph.store import Store
from nimblegraph.query import Graph

FORMATS = ("text", "json", "markdown", "github")


def add_parsers(sub, common) -> None:
    p = sub.add_parser("check", help="architecture rules: new violations vs the baseline (exit 1 on new errors)")
    common(p)
    p.add_argument("files", nargs="*", help="only violations of these files")
    p.add_argument("--git", action="store_true", help="only the files changed in git (diff HEAD + untracked)")
    p.add_argument("--base", help="only the files changed since this ref (base...HEAD + working tree)")
    p.add_argument("--all", action="store_true", help="also the violations already in the baseline")
    p.add_argument("--baseline", action="store_true", help="accept the current violations (.nimblegraph/baseline.json)")
    p.add_argument("--rule", action="append", help="only this rule / sub-rule prefix / preset (repeatable)")
    p.add_argument("--format", choices=FORMATS, default="text")
    p.add_argument("--strict", action="store_true", help="exit 1 on new warnings too")
    p.add_argument("--record", action="store_true", help="append the metrics to .nimblegraph/history.jsonl (nimblegraph trend)")
    p.add_argument("--hook", action="store_true", help=argparse.SUPPRESS)

    p = sub.add_parser("rules", help="active presets and rules; --init writes .nimblegraph/rules.toml")
    common(p)
    p.add_argument("--init", nargs="*", metavar="PRESET", help="write rules.toml (presets detected when none given)")
    p.add_argument("--force", action="store_true", help="with --init: overwrite an existing rules.toml")
    p.add_argument("--export", choices=["depcruise"], help="convert the path rules for another tool")
    p.add_argument("--out", help="with --export: file to write")

    p = sub.add_parser("conventions", help="naming conventions per package+layer (inferred or from rules.toml)")
    common(p)

    p = sub.add_parser("ddd", help="bounded contexts, aggregates, building blocks, context map and leaks")
    common(p)
    p.add_argument("context", nargs="?", help="one context in detail")

    p = sub.add_parser("glossary", help="ubiquitous language: domain terms per context, synonyms, glossary.toml")
    common(p)
    p.add_argument("--init", action="store_true", help="write a glossary.toml draft from the code")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("architecture", aliases=["arch"], help="module metrics (Ca Ce I A D), god files, dependency matrix")
    common(p)
    p.add_argument("--by", choices=["context", "folder", "layer", "package", "ring"], default=None,
                   help="module = bounded context (default when there are contexts), folder, layer, package or ring")
    p.add_argument("--level", type=int, default=3, help="with --by folder: path segments per module (default 3)")
    p.add_argument("--dsm", action="store_true", help="dependency structure matrix")
    p.add_argument("--format", choices=["text", "mermaid"], default="text")
    p.add_argument("--out", help="write the mermaid diagram to a file")

    p = sub.add_parser("hotspots", help="files that change often and are big/coupled (git log)")
    common(p)
    p.add_argument("--months", type=int, default=6)
    p.add_argument("--top", type=int, default=15)

    p = sub.add_parser("trend", help="metric history recorded by `nimblegraph check --record`")
    common(p)

    p = sub.add_parser("adr", help="architecture decision records and the files each one applies to")
    common(p)

    p = sub.add_parser("deadcode", help="exports nobody imports and orphan files")
    common(p)

    p = sub.add_parser("tests", help="structural test coverage; with files/--git: the tests to run and the command")
    common(p)
    p.add_argument("files", nargs="*")
    p.add_argument("--git", action="store_true", help="tests for the files changed in git")
    p.add_argument("--base", help="tests for the files changed since this ref")

    p = sub.add_parser("contracts", help="front-end calls x back-end routes: missing routes, wrong methods, unused routes")
    common(p)

    p = sub.add_parser("review", help="report of a change: violations, impact, tests to run, ADRs, notes, reviewers")
    common(p)
    p.add_argument("files", nargs="*", help="review these files instead of the git changes")
    p.add_argument("--base", help="changes since this ref (base...HEAD + working tree); default: working tree")
    p.add_argument("--format", choices=["text", "markdown"], default="text")


def _graph(args) -> tuple:
    from nimblegraph import cli
    return cli._load(args)


def _budget(args, cfg) -> int:
    return args.budget or max(int(cfg["budget"]), 2500)


def _scope(args, cfg, g) -> tuple[set[str] | None, str]:
    if args.git or args.base:
        names = gitutil.changed_files(cfg.root, args.base)
        files = {n for n in names if n in g.files}
        return files, f"{len(files)} changed files" + (f" since {args.base}" if args.base else "")
    if args.files:
        files = set()
        for n in args.files:
            files |= set(g.find_files(n))
        return files, f"{len(files)} files"
    return None, ""


def cmd_check(args) -> int:
    if args.hook:
        return check_hook(args)
    cfg, g, _notes = _graph(args)
    try:
        res = rules_mod.run(cfg, g, only=args.rule)
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return 2
    if args.baseline:
        path = rules_mod.save_baseline(cfg, res.violations)
        c = res.counts()
        print(f"baseline: {len(res.violations)} violations accepted ({c['error']} errors, {c['warn']} warnings,"
              f" {c['info']} info); {len(res.resolved)} resolved entries dropped -> {path}")
        return 0
    scope, scope_desc = _scope(args, cfg, g)
    shown = res.violations if args.all else res.new()
    if scope is not None:
        shown = [v for v in shown if rules_mod.in_scope(v, scope)]
    if args.record:
        from nimblegraph import metrics
        metrics.record(cfg, g, res)
    if args.format == "json":
        sys.stdout.write(arch_views.check_json(res, shown) + "\n")
    elif args.format == "markdown":
        sys.stdout.write(arch_views.check_markdown(res, shown))
    elif args.format == "github":
        sys.stdout.write(arch_views.check_github(shown))
    else:
        o = arch_views.check_view(res, shown, f"check {cfg.root.name}", scope_desc, args.all)
        print(finish(o, _budget(args, cfg)))
    if res.errors:
        return 2
    bad = {"error", "warn"} if args.strict else {"error"}
    return 1 if any(v.state == "new" and v.severity in bad for v in shown) else 0


def check_hook(args) -> int:
    """PostToolUse hook: after an Edit/Write, report the new errors of the edited file to Claude (exit 2 + stderr)."""
    try:
        raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode()
        data = json.loads(raw.decode("utf-8-sig") or "{}")
    except (OSError, ValueError):
        return 0
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path")
    if not path:
        return 0
    try:
        cfg = config.load(Path(args.root) if args.root else (Path(data["cwd"]) if data.get("cwd") else None))
    except config.RootError:
        return 0
    p = Path(path)
    if not p.is_absolute():
        p = Path(data.get("cwd") or cfg.root) / p
    try:
        rel = p.resolve().relative_to(cfg.root).as_posix()
    except ValueError:
        return 0
    if Path(rel).suffix not in cfg["extensions"]:
        return 0
    build.run(cfg, engine_pref=args.engine)
    store = Store(cfg.db_path)
    try:
        g = Graph.load(store)
    finally:
        store.close()
    if rel not in g.files:
        return 0
    res = rules_mod.run(cfg, g)
    mine = [v for v in res.new() if rules_mod.in_scope(v, {rel})]
    errors = [v for v in mine if v.severity == "error"]
    if not errors:
        return 0
    lines = [f"nimblegraph: {rel} breaks {len(errors)} architecture rule(s) (.nimblegraph/rules.toml):"]
    for v in mine:
        if v.severity == "info":
            continue
        lines.append(f"- [{v.severity}] {v.rule}: {v.message}" + (f" ({v.src}:{v.line} -> {v.dst})" if v.dst else "")
                     + (f" ADR {v.adr}" if v.adr else ""))
    lines.append("Fix the dependency (or, if it is intended, say so to the user: `nimblegraph check --baseline` accepts it).")
    print("\n".join(lines), file=sys.stderr)
    return 2


def cmd_rules(args) -> int:
    cfg, g, _notes = _graph(args)
    rules_cfg = arch_mod.load_rules(cfg)
    a = arch_mod.model(g, rules_cfg)
    rules, presets, auto = rules_mod.active_rules(g, a, rules_cfg)
    if args.init is not None:
        target = cfg.dir / arch_mod.RULES_FILE
        if target.is_file() and not args.force:
            print(f"{target} exists; use --force to overwrite")
            return 1
        chosen = args.init or rules_mod.detect_presets(g, a)
        unknown = [p for p in chosen if p not in rules_mod.PRESETS]
        if unknown:
            print(f"unknown preset(s): {', '.join(unknown)}; presets: {', '.join(rules_mod.PRESET_HELP)}")
            return 2
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rules_mod.init_toml(chosen, a), encoding="utf-8", newline="\n")
        print(f"wrote {target} (presets: {', '.join(chosen)})\nnext: nimblegraph check  |  nimblegraph check --baseline")
        return 0
    if args.export:
        _write(rules_mod.export_depcruise(g, a, rules), args.out)
        return 0
    print(finish(arch_views.rules_view(rules, presets, auto, a.context_how), _budget(args, cfg)))
    return 0


def _arch(args):
    cfg, g, notes = _graph(args)
    rules_cfg = arch_mod.load_rules(cfg)
    return cfg, g, notes, rules_cfg, arch_mod.model(g, rules_cfg)


def _write(text: str, out: str | None) -> None:
    if out:
        Path(out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {out}")
    else:
        sys.stdout.write(text)


def cmd_conventions(args) -> int:
    from nimblegraph import naming
    cfg, g, _n, rules_cfg, _a = _arch(args)
    convs = naming.conventions(g, rules_cfg.get("naming") or {})
    print(finish(arch_views.conventions_view(convs, naming.route_smells(g), naming.layer_mismatches(g)),
                 _budget(args, cfg)))
    return 0


def cmd_ddd(args) -> int:
    cfg, g, _n, _r, a = _arch(args)
    links = arch_mod.context_links(g, a)
    aggs = arch_mod.aggregates(g, a)
    if args.context:
        from nimblegraph import ddd
        name = args.context
        if name not in a.contexts():
            hits = [c for c in a.contexts() if name.lower() in c.lower()]
            if len(hits) != 1:
                print(f"no context '{name}'; contexts: {', '.join(a.contexts()) or 'none'}")
                return 1
            name = hits[0]
        print(finish(arch_views.ddd_context(g, a, ddd.context_info(g, a, name), links, aggs), _budget(args, cfg)))
        return 0
    print(finish(arch_views.ddd_overview(g, a, links, aggs), _budget(args, cfg)))
    return 0


def cmd_glossary(args) -> int:
    from nimblegraph import ddd
    cfg, g, _n, _r, a = _arch(args)
    terms = ddd.domain_terms(g, a)
    groups = ddd.synonym_groups(terms)
    if args.init:
        target = cfg.dir / ddd.GLOSSARY_FILE
        if target.is_file() and not args.force:
            print(f"{target} exists; use --force to overwrite")
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(ddd.glossary_toml(terms, groups), encoding="utf-8", newline="\n")
        print(f"wrote {target} ({len(terms)} terms)")
        return 0
    print(finish(arch_views.glossary_view(terms, groups, ddd.load_glossary(cfg), ddd.glossary_hits(cfg, g)),
                 _budget(args, cfg)))
    return 0


def cmd_architecture(args) -> int:
    from nimblegraph import metrics
    cfg, g, _n, _r, a = _arch(args)
    by = args.by or ("context" if len([c for c in a.contexts() if c != "shared"]) >= 2 else "folder")
    if args.format == "mermaid":
        _write(metrics.mermaid(g, a, by, args.level), args.out)
        return 0
    mods = metrics.module_metrics(g, a, by, args.level)
    names, cells = metrics.dsm(g, a, by, args.level) if args.dsm else (None, None)
    print(finish(arch_views.architecture_view(mods, metrics.god_files(g), by, names, cells), _budget(args, cfg)))
    return 0


def cmd_hotspots(args) -> int:
    from nimblegraph import metrics
    cfg, g, _n = _graph(args)
    rows, repos = metrics.hotspots(cfg, g, args.months, args.top)
    print(finish(arch_views.hotspots_view(rows, repos, args.months), _budget(args, cfg)))
    return 0


def cmd_trend(args) -> int:
    from nimblegraph import metrics
    cfg = config.load(Path(args.root) if args.root else None)
    print(finish(arch_views.trend_view(metrics.history(cfg)), _budget(args, cfg)))
    return 0


def cmd_adr(args) -> int:
    from nimblegraph import metrics
    cfg, g, _n = _graph(args)
    print(finish(arch_views.adr_view(metrics.adrs(cfg, g)), _budget(args, cfg)))
    return 0


def cmd_deadcode(args) -> int:
    from nimblegraph import metrics, query
    cfg, g, _n = _graph(args)
    print(finish(arch_views.deadcode_view(metrics.dead_exports(g), query.orphans(g)), _budget(args, cfg)))
    return 0


def cmd_tests(args) -> int:
    from nimblegraph import testmap
    cfg, g, _n, _r, a = _arch(args)
    if args.files or args.git or args.base:
        if args.files:
            targets = sorted({h for n in args.files for h in g.find_files(n)})
        else:
            targets = [f for f in gitutil.changed_files(cfg.root, args.base) if f in g.files]
        if not targets:
            print("no indexed files to analyse")
            return 1
        tests, cmds = testmap.tests_to_run(g, targets)
        print(finish(arch_views.tests_for_view(targets, tests, cmds), _budget(args, cfg)))
        return 0
    print(finish(arch_views.tests_view(testmap.coverage(g, a)), _budget(args, cfg)))
    return 0


def cmd_contracts(args) -> int:
    from nimblegraph import testmap
    cfg, g, _n = _graph(args)
    issues = testmap.contracts(g)
    print(finish(arch_views.contracts_view(issues), _budget(args, cfg)))
    return 1 if any(i.sub != "unused-route" for i in issues) else 0


def cmd_review(args) -> int:
    from nimblegraph import review
    from nimblegraph.render import progressive
    cfg, g, notes = _graph(args)
    files = None
    if args.files:
        files = sorted({h for n in args.files for h in g.find_files(n)})
    rv = review.review(cfg, g, notes, args.base, files)
    if args.format == "markdown":
        sys.stdout.write(arch_views.review_markdown(rv))
    else:
        print(progressive(lambda d: arch_views.review_view(rv, d), _budget(args, cfg)))
    return 1 if any(v.severity == "error" for v in rv.violations) else 0


HANDLERS = {"check": cmd_check, "rules": cmd_rules, "conventions": cmd_conventions, "ddd": cmd_ddd,
            "glossary": cmd_glossary, "architecture": cmd_architecture, "arch": cmd_architecture,
            "hotspots": cmd_hotspots, "trend": cmd_trend, "adr": cmd_adr, "deadcode": cmd_deadcode,
            "tests": cmd_tests, "contracts": cmd_contracts, "review": cmd_review}
