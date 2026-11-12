"""Compact text (and json / markdown / github) views of the governance commands: check, rules."""

from __future__ import annotations

import json
from itertools import groupby

from nimblegraph import rules as rules_mod
from nimblegraph.render import Out

SEV_LABEL = {"error": "ERROR", "warn": "WARN", "info": "INFO"}


def _where(v) -> str:
    return f"{v.src}:{v.line}" if v.line else v.src


def check_view(res: rules_mod.CheckResult, shown: list, title: str, scope: str = "", show_all: bool = False) -> Out:
    new, known = res.counts("new"), res.counts("known")
    head = "auto presets" if res.auto else "presets"
    n_new = sum(new.values())
    stats = (f"{head} {','.join(res.presets)} | new {n_new}"
             + (f" ({', '.join(f'{n} {s}' for s, n in new.items() if n)})" if n_new else "")
             + f" | known {sum(known.values())} | resolved {len(res.resolved)}" + (f" | {scope}" if scope else ""))
    o = Out(title, stats=stats)
    for err in res.errors:
        o.add(f"RULE ERROR {err}")
    rule_by_name = {r.name: r for r in res.rules}
    key = lambda v: (rules_mod.SEV_ORDER[v.severity], v.rule)  # noqa: E731
    for (_sev_i, rule), items in groupby(sorted(shown, key=key), key=key):
        items = list(items)
        r = rule_by_name.get(rule)
        msg = (r.message if r and r.message else items[0].message)
        adr = items[0].adr or (r.adr if r else None)
        o.add(f"{SEV_LABEL[items[0].severity]} {rule}" + (f" (ADR {adr})" if adr else "") + f" - {msg}")
        cap = 12 if len(shown) > 60 else len(items)
        for v in items[:cap]:
            o.path(v.src)
            dst = v.dst or ""
            if dst and " " not in dst and not dst.startswith(("pkg:", "cycle:")):
                o.path(dst)
            extra = "" if v.message == msg else (v.message[len(msg):] if v.message.startswith(msg) else f" - {v.message}")
            extra = extra if not extra or extra.startswith((" ", ":")) else " " + extra
            known_tag = " (known)" if show_all and v.state == "known" else ""
            o.add(f" {_where(v)}" + (f" > {dst}" if dst else "") + extra + known_tag)
        if len(items) > cap:
            o.add(f" +{len(items) - cap} more -> nimblegraph check --rule {rule} | --format json")
    if not shown and not res.errors:
        o.add("OK no new violations" if not show_all else "OK no violations")
    if res.resolved:
        o.add(f"RESOLVED {len(res.resolved)} baseline entries no longer happen -> nimblegraph check --baseline to prune")
        for b in res.resolved[:10]:
            o.add(f" {b.get('rule')} {b.get('src')}" + (f" > {b['dst']}" if b.get("dst") else ""))
    if not show_all and sum(known.values()):
        o.hint = "nimblegraph check --all (includes the baseline)"
    else:
        o.hint = "nimblegraph check <file> | --git"
    return o


def check_json(res: rules_mod.CheckResult, shown: list) -> str:
    return json.dumps({
        "presets": res.presets, "auto": res.auto,
        "summary": {"new": res.counts("new"), "known": res.counts("known"), "resolved": len(res.resolved)},
        "violations": [v.as_dict() for v in shown], "resolved": res.resolved, "rule_errors": res.errors,
    }, indent=1, ensure_ascii=False)


def _md_cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def check_markdown(res: rules_mod.CheckResult, shown: list, title: str = "Architecture check") -> str:
    new = res.counts("new")
    lines = [f"### {title}", "",
             f"Presets: {', '.join(res.presets)} - **{sum(new.values())} new** "
             f"({new['error']} errors, {new['warn']} warnings, {new['info']} info), "
             f"{sum(res.counts('known').values())} known, {len(res.resolved)} resolved", ""]
    if shown:
        lines += ["| | Rule | Where | Target | Message |", "|---|---|---|---|---|"]
        icon = {"error": ":x:", "warn": ":warning:", "info": ":information_source:"}
        for v in shown:
            lines.append(f"| {icon[v.severity]} | `{v.rule}` | `{_where(v)}` | `{_md_cell(v.dst or '')}` "
                         f"| {_md_cell(v.message)}{' (ADR ' + v.adr + ')' if v.adr else ''} |")
    else:
        lines.append("No new violations.")
    return "\n".join(lines) + "\n"


def check_github(shown: list) -> str:
    level = {"error": "error", "warn": "warning", "info": "notice"}
    out = []
    for v in shown:
        msg = v.message + (f" ({v.dst})" if v.dst else "")
        msg = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        loc = f"file={v.src}" + (f",line={v.line}" if v.line else "")
        out.append(f"::{level[v.severity]} {loc},title={v.rule}::{msg}")
    return "\n".join(out) + ("\n" if out else "")


def _sel(sel: dict) -> str:
    if not sel:
        return "*"
    return " ".join(f"{k}={v}" for k, v in sel.items())


def rules_view(rules: list, presets: list[str], auto: bool, contexts: dict[str, str]) -> Out:
    o = Out("rules", stats=f"{len(rules)} rules | {'auto-detected' if auto else 'rules.toml'} presets: {', '.join(presets)}")
    o.add("PRESETS (* active)")
    for name, help_ in rules_mod.PRESET_HELP.items():
        o.add(f" {'*' if name in presets else ' '} {name}: {help_}")
    o.add("RULES")
    for r in rules:
        what = ""
        if r.kind in ("deny", "allow-only"):
            what = f" {_sel(r.frm)} -> {'only ' if r.kind == 'allow-only' else ''}{_sel(r.to)}"
        elif r.kind == "deny-external":
            what = f" {_sel(r.frm)} -> packages {', '.join(r.params.get('packages', [])[:6])}..."
        elif r.kind == "no-cycles":
            what = f" by {r.params.get('granularity', 'file')}"
        elif r.kind.startswith("max-"):
            what = f" {_sel(r.frm)} max {r.params.get('max')}"
        o.add(f" {r.severity:5} {r.name} [{r.kind}]{what}")
    if contexts:
        o.add("CONTEXTS " + ", ".join(f"{c} ({how})" for c, how in sorted(contexts.items())))
    o.hint = "nimblegraph rules --init | nimblegraph check"
    return o


# ---------------------------------------------------------------------- conventions

def conventions_view(convs: list, smells: list, mismatches: list) -> Out:
    o = Out("conventions", stats=f"{len(convs)} conventions")
    for c in convs:
        src = "rules.toml" if c.configured else f"{round(c.share * 100)}% of {c.samples}"
        o.add(f"{c.what} {c.group}: {c.value} ({src})" + (f" | {len(c.outliers)} off" if c.outliers else ""))
        for where, name, line in c.outliers[:8]:
            if not where.endswith("/"):
                o.path(where)
            o.add(f" {where}{':' + str(line) if line else ''}" + (f" {name}" if name and name not in where else ""))
        if len(c.outliers) > 8:
            o.add(f" +{len(c.outliers) - 8} more")
    if mismatches:
        o.add(f"LAYER MISMATCH {len(mismatches)}")
        for f, _cls, line, msg in mismatches[:15]:
            o.add(f" {o.path(f)}:{line} {msg}")
    if smells:
        o.add(f"ROUTES {len(smells)}")
        for sub, f, line, label, msg in smells[:15]:
            o.add(f" {label} ({o.path(f)}:{line}) {sub.split('/')[1]}: {msg}")
    if not convs and not smells and not mismatches:
        o.add("no convention reaches 70% in groups of 5+ files; set them in [naming] of rules.toml")
    o.hint = "nimblegraph check --rule naming"
    return o


# ---------------------------------------------------------------------- ddd

ROLE_ORDER = ("aggregate", "entity", "value-object", "domain-event", "command", "command-handler", "query",
              "query-handler", "event-handler", "use-case", "domain-service", "service", "port", "adapter",
              "repository", "factory", "mapper", "dto", "controller", "module")
ROLE_SHORT = {"value-object": "vo", "domain-event": "event", "command-handler": "cmd-handler",
              "domain-service": "domain-svc"}


def _link_text(lk) -> str:
    bits = []
    if lk.imports:
        bits.append(f"{lk.imports} imports")
    if lk.http:
        bits.append(f"{lk.http} http")
    if lk.leaks:
        bits.append(f"{len(lk.leaks)} LEAKS")
    return ", ".join(bits)


def ddd_overview(g, a, links: dict, aggs: list) -> Out:
    ctxs = a.contexts()
    no_ctx = sum(1 for f, c in a.context.items() if not c and g.layer(f) not in ("test", "config"))
    o = Out("ddd", stats=f"{len(ctxs)} contexts | {len(aggs)} aggregates | {no_ctx} files outside contexts")
    o.add("CONTEXTS")
    for c in ctxs:
        files = a.files_of(c)
        roles: dict[str, int] = {}
        for f in files:
            for r in a.roles.get(f, ()):
                roles[r] = roles.get(r, 0) + 1
        parts = [f"{ROLE_SHORT.get(r, r)} {roles[r]}" for r in ROLE_ORDER if r in roles]
        root = a.context_root.get(c, "")
        o.add(f" {c} ({a.context_how.get(c)}{': ' + root if root else ''}) {len(files)} files"
              + (" | " + ", ".join(parts) if parts else ""))
    if aggs:
        o.add("AGGREGATES")
        for ag in aggs:
            o.add(f" {ag.root} [{ag.context or '-'}] {o.path(ag.root_file)}"
                  + (" > " + ", ".join(o.path(m) for m in ag.members) if ag.members else ""))
    if links:
        o.add("CONTEXT MAP")
        for (cs, cd), lk in sorted(links.items(), key=lambda kv: (-kv[1].imports - kv[1].http, kv[0])):
            o.add(f" {cs} -> {cd}: {_link_text(lk)}")
        leaks = [(cs, cd, s, d, ln) for (cs, cd), lk in sorted(links.items()) for s, d, ln in lk.leaks]
        if leaks:
            o.add("LEAKS (internal files of another context)")
            for cs, cd, s, d, ln in leaks[:20]:
                o.add(f" {o.path(s)}:{ln} > {o.path(d)} [{cs} -> {cd}]")
            if len(leaks) > 20:
                o.add(f" +{len(leaks) - 20} more")
    o.hint = "nimblegraph ddd <context>"
    return o


def ddd_context(g, a, info, links: dict, aggs: list) -> Out:
    o = Out(f"ddd {info.name}", stats=f"{info.how} {info.root} | {len(info.files)} files")
    mine = [ag for ag in aggs if ag.context == info.name]
    if mine:
        o.add("AGGREGATES")
        for ag in mine:
            o.add(f" {ag.root} {o.path(ag.root_file)}")
            for m in ag.members:
                o.add(f"  - {o.path(m)}" + (f" ({', '.join(sorted(a.roles[m]))})" if a.roles.get(m) else ""))
    for role in ROLE_ORDER:
        items = info.blocks.get(role) or []
        if not items or (role == "aggregate" and mine):
            continue
        o.add(f"{role.upper()} " + ", ".join(f"{s} ({o.path(f)})" for f, s in items[:12])
              + (f" +{len(items) - 12}" if len(items) > 12 else ""))
        if role == "port":
            for f, s in items:
                impls = g.subclasses.get((f, s), [])
                if impls:
                    o.add(f" {s} <- " + ", ".join(f"{sym} ({o.path(fi)})" for fi, sym, _k in impls))
    if info.routes:
        o.add("ROUTES " + ", ".join(info.routes[:20]) + (f" +{len(info.routes) - 20}" if len(info.routes) > 20 else ""))
    out_l = sorted((cd, lk) for (cs, cd), lk in links.items() if cs == info.name)
    in_l = sorted((cs, lk) for (cs, cd), lk in links.items() if cd == info.name)
    if out_l:
        o.add("USES " + "; ".join(f"{cd} ({_link_text(lk)})" for cd, lk in out_l))
    if in_l:
        o.add("USED BY " + "; ".join(f"{cs} ({_link_text(lk)})" for cs, lk in in_l))
    o.hint = f"nimblegraph analyze {info.name}"
    return o


def glossary_view(terms: dict, groups: list, glossary: dict, hits: list) -> Out:
    o = Out("glossary", stats=f"{len(terms)} domain terms | glossary {len(glossary)} terms")
    multi = [t for t in terms.values() if len({c for c in t.contexts if c != '-'}) > 1]
    if terms:
        o.add("TERMS " + ", ".join(t.name + (f"[{','.join(sorted(t.contexts - {'-'}))}]" if t.contexts - {'-'} else "")
                                   for t in sorted(terms.values(), key=lambda t: t.name)))
    else:
        o.add("no domain building blocks found (aggregates, entities, value objects, events, use cases...)")
    if multi:
        o.add("SAME TERM IN MANY CONTEXTS (fine in DDD when the meaning differs; say so in the glossary)")
        for t in multi:
            o.add(f" {t.name}: " + ", ".join(sorted(t.contexts)))
    if groups:
        o.add("POSSIBLE SYNONYMS (pick one term, list the others as synonyms)")
        for gr in groups:
            o.add(" " + ", ".join(gr))
    if glossary:
        known = {k.lower() for k in glossary} | {str(s).lower() for v in glossary.values() if isinstance(v, dict)
                                                 for s in v.get("synonyms", [])}
        missing = sorted(t for t in terms if t.lower() not in known)
        if missing:
            o.add("NOT IN THE GLOSSARY " + ", ".join(missing))
    if hits:
        o.add(f"SYNONYMS IN USE {len(hits)}")
        for f, line, _term, _used, msg in hits[:20]:
            o.add(f" {o.path(f)}:{line} {msg}")
    o.hint = "nimblegraph glossary --init"
    return o


# ---------------------------------------------------------------------- architecture / metrics

def architecture_view(mods: list, gods: list, by: str, names=None, cells=None) -> Out:
    o = Out(f"architecture by {by}", stats=f"{len(mods)} modules")
    o.add("MODULE files loc | Ca Ce I | A D zone")
    for m in mods:
        o.add(f" {m.name} {m.files} {m.loc} | {m.ca} {m.ce} {m.instability:.2f} | {m.abstractness:.2f}"
              f" {m.distance:.2f}{' ' + m.zone.upper() if m.zone else ''}")
    o.add("(I = Ce/(Ca+Ce); A = interfaces/types/abstract classes share; D = |A+I-1|; PAIN = concrete and depended"
          " upon; USELESS = abstract and unused)")
    if gods:
        o.add("GOD FILES (importers x dependencies)")
        for f, ins, outs, loc in gods:
            o.add(f" {o.path(f)} in {ins} out {outs} loc {loc}")
    if names and cells is not None:
        short = names[:15]
        o.add("DSM (row depends on column): " + " | ".join(f"{i + 1}={n}" for i, n in enumerate(short)))
        for i, n in enumerate(short):
            row = " ".join("  -" if n == m else f"{cells.get((n, m), 0) or '.':>3}" for m in short)
            o.add(f" {i + 1:>2} {row}")
    o.hint = "nimblegraph architecture --by folder --level 3 | --dsm | --format mermaid"
    return o


def hotspots_view(rows: list, repos: list[str], months: int) -> Out:
    o = Out("hotspots", stats=f"last {months} months | repos: {', '.join(repos) or 'none'}")
    if not repos:
        o.add("no git repository at the root or in its sub-folders")
        return o
    o.add("FILE commits loc importers score (commits x size x coupling)")
    for h in rows:
        o.add(f" {o.path(h.file)} {h.commits} {h.loc} {h.importers} {h.score}")
    o.hint = "nimblegraph analyze <file> | nimblegraph architecture"
    return o


def trend_view(rows: list[dict]) -> Out:
    o = Out("trend", stats=f"{len(rows)} snapshots")
    if not rows:
        o.add("no history yet: `nimblegraph check --record` (in CI or by hand) appends one snapshot per commit and day")
        return o
    o.add("AT commit | files loc | errors warn info (new) | cycles leaks links avgD")
    for r in rows[-25:]:
        v = r.get("violations") or {}
        n = r.get("new") or {}
        o.add(f" {r.get('at')} {r.get('commit') or '-'} | {r.get('files')} {r.get('loc')} | "
              f"{v.get('error', '-')} {v.get('warn', '-')} {v.get('info', '-')} ({sum(n.values()) if n else '-'}) | "
              f"{r.get('cycles')} {r.get('leaks')} {r.get('context_links')} {r.get('avg_distance')}")
    if len(rows) >= 2:
        first, last = rows[0], rows[-1]

        def errors(r: dict):
            return (r.get("violations") or {}).get("error", "-")
        o.add(f"SINCE {first.get('at')}: errors {errors(first)} -> {errors(last)}, cycles {first.get('cycles')} -> "
              f"{last.get('cycles')}, leaks {first.get('leaks')} -> {last.get('leaks')}")
    return o


def adr_view(items: list) -> Out:
    o = Out("adr", stats=f"{len(items)} decisions")
    if not items:
        o.add("no ADRs in docs/adr, docs/adrs, docs/decisions, adr (or set [adr] dir in rules.toml)")
    for x in items:
        o.add(f"ADR {x.id} [{x.status or '?'}] {x.title} ({o.path(x.path)})"
              + (f" applies to {', '.join(x.applies)}: {len(x.files)} files" if x.applies else " (no Applies-to:)"))
    o.hint = "an `Applies-to: <globs>` line links an ADR to files (nimblegraph review shows the ADRs a change touches)"
    return o


def deadcode_view(dead: list, orphans: list[str]) -> Out:
    o = Out("deadcode", stats=f"{len(dead)} unused exports | {len(orphans)} orphan files")
    by_file: dict[str, list[str]] = {}
    for f, sym, line, _kind in dead:
        by_file.setdefault(f, []).append(f"{sym}:{line}")
    if by_file:
        o.add("UNUSED EXPORTS (nobody imports them; framework-wired classes excluded)")
        for f, syms in sorted(by_file.items()):
            o.add(f" {o.path(f)} {', '.join(syms)}")
    if orphans:
        o.add("ORPHAN FILES (nobody imports them)")
        for f in orphans:
            o.add(f" {o.path(f)}")
    if not by_file and not orphans:
        o.add("nothing unused")
    o.hint = "nimblegraph rdeps <file>"
    return o


# ---------------------------------------------------------------------- tests / contracts / review

def tests_view(cov) -> Out:
    eps = cov.endpoints
    e2e = sum(1 for _r, a, _u in eps if a)
    unit = sum(1 for _r, a, u in eps if not a and u)
    none = [r for r, a, u in eps if not a and not u]
    kinds: dict[str, int] = {}
    for t in cov.tests.values():
        kinds[t.kind] = kinds.get(t.kind, 0) + 1
    o = Out("tests", stats=f"{len(cov.tests)} tests (" + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
            + f") | endpoints {len(eps)}: {e2e} e2e, {unit} unit only, {len(none)} untested")
    if none:
        o.add("ENDPOINTS WITHOUT TESTS")
        for r in none[:25]:
            o.add(f" {r.label} ({o.path(r.target)})")
        if len(none) > 25:
            o.add(f" +{len(none) - 25} more")
    for role, items in sorted(cov.by_role.items()):
        bad = [(f, s) for f, s, t in items if not t]
        if bad:
            o.add(f"{role.upper()} WITHOUT TESTS {len(bad)}/{len(items)}: "
                  + ", ".join(s for _f, s in bad[:12]) + (f" +{len(bad) - 12}" if len(bad) > 12 else ""))
    pages = [r for r, t in cov.pages if not t]
    if pages:
        o.add(f"PAGES WITHOUT TESTS {len(pages)}/{len(cov.pages)}: " + ", ".join(r.path for r in pages[:12])
              + (f" +{len(pages) - 12}" if len(pages) > 12 else ""))
    if cov.contexts:
        o.add("REACHED BY TESTS " + ", ".join(f"{c} {a}/{b} ({round(100 * a / b) if b else 0}%)"
                                              for c, (a, b) in sorted(cov.contexts.items())))
    o.hint = "nimblegraph tests --git (tests to run for your change)"
    return o


def tests_for_view(targets: list[str], tests: list[str], cmds: list[str]) -> Out:
    o = Out("tests for", stats=f"{len(targets)} files -> {len(tests)} tests")
    for t in tests:
        o.add(f" {o.path(t)}")
    if not tests:
        o.add("no test reaches these files")
    if cmds:
        o.add("RUN")
        for c in cmds:
            o.add(f" {c}")
    return o


def contracts_view(issues: list) -> Out:
    by: dict[str, list] = {}
    for i in issues:
        by.setdefault(i.sub, []).append(i)
    o = Out("contracts", stats=", ".join(f"{len(v)} {k}" for k, v in sorted(by.items())) or "front and back agree")
    for sub in ("no-route", "method-mismatch", "unused-route"):
        items = by.get(sub, [])
        if not items:
            continue
        o.add(f"{sub.upper()} {len(items)}")
        for i in items[:30]:
            o.add(f" {i.label} ({o.path(i.file)}:{i.line})" + (f" - {i.message}" if sub == "method-mismatch" else ""))
        if len(items) > 30:
            o.add(f" +{len(items) - 30} more")
    o.hint = 'nimblegraph endpoint "<METHOD /path>"'
    return o


def review_view(rv, detail: int = 3) -> Out:
    title = "review" + (f" {rv.base}...HEAD" if rv.base else " working tree")
    o = Out(title, stats=f"{len(rv.changed)} changed, {len(rv.indexed)} source files | {rv.affected} affected")
    if not rv.changed:
        o.add("no changes")
        return o
    o.add("CHANGED " + "; ".join(f"{c}: {len(fs)}" for c, fs in sorted(rv.by_context.items())))
    if detail >= 2:
        for c, fs in sorted(rv.by_context.items()):
            for f in fs:
                o.add(f" [{c}] {o.path(f)}")
    if rv.violations:
        o.add(f"NEW VIOLATIONS {len(rv.violations)}")
        for v in rv.violations:
            o.add(f" {SEV_LABEL[v.severity]} {v.rule} {_where(v)}" + (f" > {v.dst}" if v.dst else "") + f" - {v.message}")
    else:
        o.add("NEW VIOLATIONS none")
    if rv.contracts:
        o.add("CONTRACTS " + "; ".join(f"{i.label} ({i.sub})" for i in rv.contracts))
    if rv.routes:
        o.add("ROUTES " + ", ".join(r.label for r in rv.routes[:25]) + (f" +{len(rv.routes) - 25}" if len(rv.routes) > 25 else ""))
    if rv.pages:
        o.add("PAGES " + ", ".join(r.path for r in rv.pages[:20]))
    if rv.untested:
        o.add("CHANGED ENDPOINTS WITHOUT TESTS " + ", ".join(r.label for r in rv.untested))
    if rv.tests:
        o.add(f"TESTS {len(rv.tests)}" + (": " + ", ".join(o.path(t) for t in rv.tests[:15]) if detail >= 1 else ""))
        for c in rv.commands:
            o.add(f" run: {c}")
    if rv.adrs:
        o.add("ADRS " + "; ".join(f"{k} {rv.adr_titles.get(k, '')} ({len(v)} files)" for k, v in rv.adrs.items()))
    if rv.stale_notes:
        o.add("STALE NOTES " + ", ".join(o.path(f) for f in rv.stale_notes) + " -> nimblegraph describe")
    if rv.owners:
        o.add("REVIEWERS " + ", ".join(f"{k} ({len(v)})" for k, v in sorted(rv.owners.items())))
    o.hint = "nimblegraph review --format markdown"
    return o


def review_markdown(rv) -> str:
    md = ["## nimblegraph review" + (f" `{rv.base}...HEAD`" if rv.base else ""), ""]
    if not rv.changed:
        return "\n".join(md + ["No changes."]) + "\n"
    md.append(f"**{len(rv.indexed)} source files changed** ({len(rv.changed)} total) in "
              + ", ".join(f"`{c}` ({len(fs)})" for c, fs in sorted(rv.by_context.items()))
              + f"; {rv.affected} files affected.")
    md.append("")
    if rv.violations:
        md += ["### New architecture violations", "", "| | Rule | Where | Target | Message |", "|---|---|---|---|---|"]
        icon = {"error": ":x:", "warn": ":warning:", "info": ":information_source:"}
        for v in rv.violations:
            md.append(f"| {icon[v.severity]} | `{v.rule}` | `{_where(v)}` | `{_md_cell(v.dst or '')}` | {_md_cell(v.message)} |")
        md.append("")
    else:
        md += [":white_check_mark: No new architecture violations.", ""]
    if rv.contracts:
        md += ["### Front x back contracts", ""]
        md += [f"- `{i.label}` in `{i.file}:{i.line}`: {i.message}" for i in rv.contracts]
        md.append("")
    if rv.routes or rv.pages:
        md += ["### Impact", ""]
        if rv.routes:
            md.append("Routes: " + ", ".join(f"`{r.label}`" for r in rv.routes[:40]))
        if rv.pages:
            md.append("Pages: " + ", ".join(f"`{r.path}`" for r in rv.pages[:40]))
        if rv.untested:
            md.append("Changed endpoints **without tests**: " + ", ".join(f"`{r.label}`" for r in rv.untested))
        md.append("")
    if rv.tests:
        md += ["### Tests to run", ""] + [f"```\n{c}\n```" for c in rv.commands] + [""]
    if rv.adrs:
        md += ["### Decisions (ADR) touched", ""]
        md += [f"- ADR {k} {rv.adr_titles.get(k, '')}: {len(v)} files" for k, v in rv.adrs.items()]
        md.append("")
    if rv.stale_notes:
        md += ["### Notes to refresh", ""] + [f"- `{f}`" for f in rv.stale_notes] + [""]
    if rv.owners:
        md += ["### Reviewers (CODEOWNERS)", ""] + [f"- {k}: {len(v)} files" for k, v in sorted(rv.owners.items())] + [""]
    return "\n".join(md)
