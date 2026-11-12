"""Tests on the graph: what each test reaches, structural coverage (endpoints, use cases, aggregates, pages),
the tests to run for a change (with the command of the project's runner), and front x back contracts."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

from nimblegraph import lang as langs
from nimblegraph import query
from nimblegraph.frameworks.api_calls import score
from nimblegraph.query import IMPORT_KINDS, Graph

E2E_PKGS = {"supertest", "@playwright/test", "playwright", "cypress", "puppeteer", "pactum", "frisby"}
COMPONENT_PKGS = ("@testing-library/",)
RUNNERS = ("vitest", "jest", "mocha", "ava", "@playwright/test", "cypress")
COVER_ROLES = ("use-case", "command-handler", "query-handler", "aggregate", "domain-service", "service", "controller")


@dataclass
class TestInfo:
    file: str
    kind: str  # e2e | component | unit
    direct: set[str] = field(default_factory=set)  # files imported by the test
    reach: set[str] = field(default_factory=set)  # everything below (depth-limited)
    routes: set[int] = field(default_factory=set)  # API routes it calls


def _kind(g: Graph, f: str) -> str:
    ext = g.externals.get(f, set())
    low = f.lower()
    if any(c.route_id for c in g.calls_from(f)) or ext & E2E_PKGS or re.search(r"(^|/)(e2e|e2e-tests|cypress)/|e2e", low):
        return "e2e"
    if low.endswith(("x", ".vue")) or any(p.startswith(COMPONENT_PKGS) for p in ext):
        return "component"
    return "unit"


def test_map(g: Graph, depth: int = 8) -> dict[str, TestInfo]:
    out = {}
    for f in sorted(g.files):
        if g.layer(f) != "test":
            continue
        t = TestInfo(f, _kind(g, f))
        t.routes = {c.route_id for c in g.calls_from(f) if c.route_id}
        t.direct = {e.dst for e in g.out.get(f, []) if e.kind in IMPORT_KINDS + ("type",) and g.layer(e.dst) != "test"}
        seen = set(t.direct)
        q = deque((d, 1) for d in t.direct)
        while q:
            x, d = q.popleft()
            if d >= depth:
                continue
            for e in g.out.get(x, []):
                if e.kind in IMPORT_KINDS + ("renders", "implements") and e.dst not in seen and g.layer(e.dst) != "test":
                    seen.add(e.dst)
                    q.append((e.dst, d + 1))
        for rid in t.routes:  # an e2e call runs the handler and everything below it
            r = g.routes.get(rid)
            if r:
                for f2 in query.endpoint(g, r, depth).files:
                    seen.add(f2)
        t.reach = seen
        out[f] = t
    return out


@dataclass
class Coverage:
    tests: dict[str, TestInfo]
    endpoints: list[tuple[object, list[str], list[str]]]  # (route, e2e tests, unit tests)
    by_role: dict[str, list[tuple[str, str, list[str]]]]  # role -> [(file, symbol, tests)]
    pages: list[tuple[object, list[str]]]
    contexts: dict[str, tuple[int, int]]  # context -> (files reached by tests, files)


def coverage(g: Graph, a, tm: dict[str, TestInfo] | None = None) -> Coverage:
    tm = tm if tm is not None else test_map(g)
    reached_by: dict[str, list[str]] = defaultdict(list)
    direct_by: dict[str, list[str]] = defaultdict(list)
    for t in tm.values():
        for f in t.reach:
            reached_by[f].append(t.file)
        for f in t.direct:
            direct_by[f].append(t.file)
    endpoints = []
    for r in sorted((r for r in g.routes.values() if r.kind == "api"), key=lambda r: (r.path, r.method)):
        e2e = sorted(t.file for t in tm.values() if r.id in t.routes)
        unit = sorted(set(direct_by.get(r.target, [])) - set(e2e))
        endpoints.append((r, e2e, unit))
    by_role: dict[str, list] = defaultdict(list)
    for (f, sym), roles in sorted(a.sym_roles.items()):
        for role in roles & set(COVER_ROLES):
            by_role[role].append((f, sym, sorted(set(reached_by.get(f, [])))))
    pages = [(r, sorted(set(reached_by.get(r.target, []))))
             for r in sorted((r for r in g.routes.values() if r.kind == "page"), key=lambda r: r.path)]
    ctx: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f in g.files:
        if g.layer(f) in ("test", "config", "type"):
            continue
        c = a.context.get(f) or "-"
        ctx[c][1] += 1
        if f in reached_by:
            ctx[c][0] += 1
    return Coverage(tm, endpoints, dict(by_role), pages, {k: (v[0], v[1]) for k, v in ctx.items()})


# ---------------------------------------------------------------------- tests to run

def _pkg_dir(g: Graph, f: str) -> str:
    best = ""
    for d in (g.meta.get("packages") or {}):
        if d and f.startswith(d + "/") and len(d) > len(best):
            best = d
    return best


def _runner(g: Graph, pkg_dir: str, test_file: str, kind: str) -> str:
    pk = g.meta.get("packages") or {}
    deps = set((pk.get(pkg_dir) or {}).get("deps") or {}) | set((pk.get("") or {}).get("deps") or {})
    ext = g.externals.get(test_file, set())
    lg = langs.name_of(test_file)
    if lg and lg != "javascript":
        eco = (pk.get(pkg_dir) or {}).get("ecosystem") or ""
        if lg in ("java", "kotlin"):
            return "gradle" if eco == "gradle" else "maven"
        if lg == "ruby":
            return "rspec" if "/spec/" in f"/{test_file}" or "rspec" in deps or "rspec-rails" in deps else "rails-test"
        return {"python": "pytest", "go": "go", "csharp": "dotnet", "php": "phpunit", "rust": "cargo",
                "c": "ctest", "cpp": "ctest", "swift": "swift"}.get(lg, "pytest")
    if "@playwright/test" in ext or kind == "e2e" and "@playwright/test" in deps and "supertest" not in ext:
        return "playwright"
    if "cypress" in ext:
        return "cypress"
    if "node:test" in ext or "test" in ext and not deps & {"vitest", "jest", "mocha"}:
        return "node"
    for r in ("vitest", "jest", "mocha", "ava"):
        if r in deps or r in ext:
            return r
    return "node"


RUN_CMD = {"vitest": "npx vitest run {files}", "jest": "npx jest {files}", "mocha": "npx mocha {files}",
           "ava": "npx ava {files}", "playwright": "npx playwright test {files}", "cypress": "npx cypress run --spec {files}",
           "node": "node --test {files}",
           # the other ecosystems: {files} paths, {dirs} their folders, {names} the test class/file names
           "pytest": "python -m pytest {files}", "go": "go test {dirs}", "maven": "mvn test -Dtest={names,}",
           "gradle": "./gradlew test {--tests names}", "dotnet": "dotnet test --filter \"{names|}\"",
           "phpunit": "vendor/bin/phpunit --filter \"{names|}\"", "rspec": "bundle exec rspec {files}",
           "rails-test": "bin/rails test {files}", "cargo": "cargo test {--test rust}", "ctest": "ctest --test-dir build",
           "swift": "swift test --filter \"{names|}\""}


def _command(runner: str, files: list[str]) -> str:
    names = sorted({langs.strip_ext(f.rsplit("/", 1)[-1]) for f in files})
    cmd = RUN_CMD[runner]
    dirs = sorted({"./" + (f.rsplit("/", 1)[0] if "/" in f else "") for f in files})
    rust = [langs.strip_ext(f.rsplit("/", 1)[-1]) for f in files if f.startswith("tests/")]
    return (cmd.replace("{files}", " ".join(files)).replace("{dirs}", " ".join(dirs))
            .replace("{names,}", ",".join(names)).replace("{names|}", "|".join(names))
            .replace("{--tests names}", " ".join(f"--tests {n}" for n in names))
            .replace("{--test rust}", " ".join(f"--test {n}" for n in rust)).strip())


def tests_to_run(g: Graph, targets: list[str], tm: dict[str, TestInfo] | None = None) -> tuple[list[str], list[str]]:
    """(test files, commands grouped by package and runner) for a change to `targets`."""
    tm = tm if tm is not None else test_map(g)
    res = query.impact(g, targets)
    tests = set(res["tests"])
    tset = set(targets)
    for t in tm.values():
        if t.reach & tset:
            tests.add(t.file)
    tests |= tset & set(tm)
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for t in sorted(tests):
        d = _pkg_dir(g, t)
        groups[(d, _runner(g, d, t, tm[t].kind if t in tm else "unit"))].append(t)
    cmds = []
    for (d, runner), files in sorted(groups.items()):
        rel = [f[len(d) + 1:] if d else f for f in files]
        cmd = _command(runner, rel)
        cmds.append(f"cd {d} && {cmd}" if d else cmd)
    return sorted(tests), cmds


# ---------------------------------------------------------------------- contracts

@dataclass
class ContractIssue:
    sub: str  # no-route | method-mismatch | unused-route
    file: str
    line: int
    label: str
    message: str


UI_PKGS = {"react", "react-dom", "next", "vue", "svelte", "@angular/core", "react-native", "expo", "solid-js",
           "preact", "nuxt", "@remix-run/react"}


def front_call(g: Graph, file: str, server_dirs: set[str]) -> bool:
    """A call made by front-end code: its package has a UI framework, or at least it declares no API routes
    (a back-end calling a URL is talking to another service, not to its own front)."""
    d = _pkg_dir(g, file)
    deps = set(((g.meta.get("packages") or {}).get(d) or {}).get("deps") or {})
    return bool(deps & UI_PKGS) or d not in server_dirs


def contracts(g: Graph) -> list[ContractIssue]:
    out = []
    api = [r for r in g.routes.values() if r.kind == "api"]
    server_dirs = {_pkg_dir(g, r.file) for r in api}
    front_calls = [c for c in g.api_calls if g.layer(c.file) != "test" and (c.route_id or front_call(g, c.file, server_dirs))]
    for c in front_calls:
        if c.route_id:
            continue
        same_path = sorted({r.method for r in api if score(c.url, r.path)[0] == 3 and r.method not in (c.method, "ALL")})
        label = f"{c.method} {c.url}"
        if same_path:
            out.append(ContractIssue("method-mismatch", c.file, c.line, label,
                                     f"no route for this method; the path accepts {', '.join(same_path)}"))
        else:
            out.append(ContractIssue("no-route", c.file, c.line, label, "the front-end calls a route that does not exist"))
    if front_calls:
        called = {c.route_id for c in g.api_calls if c.route_id}
        for r in sorted(api, key=lambda r: (r.path, r.method)):
            if r.id not in called:
                out.append(ContractIssue("unused-route", r.file, r.line, r.label,
                                         "called by no front-end code and no test"))
    return out


def contract_violations(g: Graph, a, m, r) -> list:
    from nimblegraph.rules import SEV_ORDER, Violation
    out = []
    for i in contracts(g):
        sev = r.severity if i.sub != "unused-route" else ("info" if SEV_ORDER[r.severity] <= SEV_ORDER["info"] else r.severity)
        out.append(Violation(f"contracts/{i.sub}", sev, i.file, i.label, i.line, i.message, r.adr))
    return out


def missing_test_violations(g: Graph, a, m, r) -> list:
    from nimblegraph.rules import Violation
    wants = list((m.rules_cfg.get("tests") or {}).get("require_test_for") or r.params.get("require") or [])
    if not wants:
        return []
    cov = coverage(g, a)
    out = []
    for want in wants:
        kind, _, val = str(want).partition(":")
        if kind == "route":
            for rt, e2e, unit in cov.endpoints:
                if not e2e and not unit:
                    out.append(Violation("tests/missing", r.severity, rt.file, rt.label, rt.line,
                                         "endpoint without any test", r.adr))
        elif kind == "role":
            for f, sym, tests in cov.by_role.get(val, []):
                if not tests:
                    out.append(Violation("tests/missing", r.severity, f, sym, 0, f"{val} without any test", r.adr))
        elif kind == "layer":
            for f in sorted(g.files):
                if g.layer(f) == val and not any(f in t.reach for t in cov.tests.values()):
                    out.append(Violation("tests/missing", r.severity, f, None, 0, f"{val} without any test", r.adr))
    return out
