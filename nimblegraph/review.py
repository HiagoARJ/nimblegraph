"""`nimblegraph review`: one report for a change (working tree or base...HEAD) - contexts touched, new rule violations,
impacted routes/pages, tests to run, changed endpoints without tests, broken contracts, ADRs, stale notes and
the reviewers from CODEOWNERS."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pathspec

from nimblegraph import arch as arch_mod
from nimblegraph import gitutil, metrics, query, testmap
from nimblegraph import rules as rules_mod
from nimblegraph.config import Config
from nimblegraph.query import Graph

CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS")


def _spec(pattern: str) -> pathspec.PathSpec:
    try:
        return pathspec.PathSpec.from_lines("gitignore", [pattern])  # pathspec >= 1.0
    except (KeyError, LookupError):
        return pathspec.PathSpec.from_lines("gitwildmatch", [pattern])


def codeowners(cfg: Config) -> list[tuple[pathspec.PathSpec, list[str], str]]:
    for rel in CODEOWNERS_PATHS:
        p = cfg.root / rel
        if not p.is_file():
            continue
        out = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                spec = _spec(parts[0])
            except (ValueError, TypeError):
                continue
            out.append((spec, parts[1:], parts[0]))
        return out
    return []


def owners_of(rules: list, path: str) -> list[str]:
    """Last matching CODEOWNERS line wins (GitHub semantics)."""
    hit: list[str] = []
    for spec, owners, _pattern in rules:
        if spec.match_file(path):
            hit = owners
    return hit


@dataclass
class Review:
    base: str | None
    changed: list[str]  # every changed path
    indexed: list[str]  # changed source files
    by_context: dict[str, list[str]]
    violations: list = field(default_factory=list)
    check: object = None
    routes: list = field(default_factory=list)
    pages: list = field(default_factory=list)
    affected: int = 0
    tests: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    untested: list = field(default_factory=list)  # changed endpoints without any test
    contracts: list = field(default_factory=list)
    adrs: dict[str, list[str]] = field(default_factory=dict)
    adr_titles: dict[str, str] = field(default_factory=dict)
    stale_notes: list[str] = field(default_factory=list)
    owners: dict[str, list[str]] = field(default_factory=dict)  # owner -> files


def review(cfg: Config, g: Graph, notes, base: str | None = None, files: list[str] | None = None) -> Review:
    changed = files if files is not None else gitutil.changed_files(cfg.root, base)
    indexed = [f for f in changed if f in g.files]
    rules_cfg = arch_mod.load_rules(cfg)
    a = arch_mod.model(g, rules_cfg)
    by_ctx: dict[str, list[str]] = defaultdict(list)
    for f in indexed:
        by_ctx[a.context.get(f) or "-"].append(f)
    rv = Review(base, changed, indexed, dict(by_ctx))
    res = rules_mod.run(cfg, g, rules_cfg)
    rv.check = res
    scope = set(indexed)
    rv.violations = [v for v in res.new() if rules_mod.in_scope(v, scope)]
    if indexed:
        imp = query.impact(g, indexed)
        rv.routes = sorted(imp["routes"], key=lambda r: (r.path, r.method))
        rv.pages = sorted(imp["pages"], key=lambda r: r.path)
        rv.affected = len(imp["affected"])
        tm = testmap.test_map(g)
        rv.tests, rv.commands = testmap.tests_to_run(g, indexed, tm)
        cov = testmap.coverage(g, a, tm)
        rv.untested = [r for r, e2e, unit in cov.endpoints if not e2e and not unit
                       and (r.target in scope or r.file in scope)]
        rv.contracts = [i for i in testmap.contracts(g) if i.file in scope and i.sub != "unused-route"]
        adr_list = metrics.adrs(cfg, g)
        rv.adrs = metrics.adrs_for(adr_list, indexed)
        rv.adr_titles = {x.id: x.title for x in adr_list}
        rv.stale_notes = sorted(f for f in indexed if notes is not None and notes.files.get(f) and notes.stale(f))
    co = codeowners(cfg)
    owners: dict[str, list[str]] = defaultdict(list)
    for f in changed:
        for o in owners_of(co, f):
            owners[o].append(f)
    rv.owners = dict(owners)
    return rv
