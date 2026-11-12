"""Executable architecture rules: presets (layered, nest, clean, ddd, react, deps, naming, fullstack) and the
rules of `.nimblegraph/rules.toml`, evaluated on the graph. A baseline (`.nimblegraph/baseline.json`) records the
accepted violations so `nimblegraph check` only fails on new ones (ratchet)."""

from __future__ import annotations

import fnmatch
import json
import posixpath
import time
from collections import defaultdict
from dataclasses import dataclass, field

from nimblegraph import arch as arch_mod
from nimblegraph.config import Config
from nimblegraph.query import CYCLE_KINDS, IMPORT_KINDS, Graph, cycles_of

BASELINE_FILE = "baseline.json"
SEVERITIES = ("error", "warn", "info")
SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}
SKIP_SRC_LAYERS = {"test", "config"}

FRAMEWORK_PKGS = ["@nestjs/*", "typeorm", "@prisma/client", "prisma", "mongoose", "sequelize", "@mikro-orm/*",
                  "drizzle-orm", "knex", "express", "fastify", "koa", "@hapi/*", "axios", "ky", "got", "node-fetch",
                  "pg", "mysql2", "mongodb", "redis", "ioredis", "@aws-sdk/*", "aws-sdk", "firebase-admin",
                  "react", "react-dom", "next", "vue", "@angular/*",
                  # the other ecosystems (external names as the resolvers record them)
                  "django", "fastapi", "flask", "starlette", "sqlalchemy", "pydantic", "requests", "httpx", "celery",
                  "github.com/gin-gonic/gin", "github.com/labstack/echo", "github.com/go-chi/chi", "github.com/gofiber/*",
                  "gorm.io/*", "net/http", "database/sql",
                  "org.springframework", "jakarta.persistence", "javax.persistence", "jakarta.ws", "org.hibernate",
                  "io.ktor", "Microsoft.AspNetCore", "Microsoft.EntityFrameworkCore", "Dapper",
                  "Illuminate", "Symfony", "Doctrine", "rails", "activerecord", "sinatra",
                  "axum", "actix_web", "rocket", "diesel", "sqlx", "reqwest", "Vapor", "Fluent"]
FRAMEWORK_OK = ["@nestjs/cqrs"]  # AggregateRoot/IEvent of @nestjs/cqrs are the usual Nest DDD building blocks

LIB_GROUPS = {
    "http client": ["axios", "ky", "got", "node-fetch", "superagent", "undici", "ofetch", "cross-fetch"],
    "dates": ["moment", "dayjs", "date-fns", "luxon"],
    "state": ["redux", "@reduxjs/toolkit", "zustand", "mobx", "jotai", "recoil", "valtio", "pinia", "vuex"],
    "validation": ["zod", "yup", "joi", "class-validator", "valibot", "superstruct", "ajv"],
    "orm": ["typeorm", "prisma", "@prisma/client", "sequelize", "mongoose", "@mikro-orm/core", "drizzle-orm", "knex"],
    "test runner": ["jest", "vitest", "mocha", "ava", "jasmine"],
    "utility": ["lodash", "lodash-es", "underscore", "ramda"],
    "logging": ["winston", "pino", "bunyan", "log4js"],
}


# ---------------------------------------------------------------------- data

@dataclass
class Rule:
    name: str
    kind: str = "deny"
    severity: str = "error"
    message: str = ""
    frm: dict = field(default_factory=dict)
    to: dict = field(default_factory=dict)
    except_: list[str] = field(default_factory=list)
    adr: str | None = None
    params: dict = field(default_factory=dict)
    preset: str | None = None

    @classmethod
    def from_dict(cls, d: dict, preset: str | None = None) -> "Rule":
        known = {"name", "kind", "severity", "message", "from", "to", "except", "adr"}
        return cls(name=str(d.get("name") or d.get("kind") or "rule"), kind=str(d.get("kind", "deny")),
                   severity=str(d.get("severity", "error")), message=str(d.get("message", "")),
                   frm=dict(d.get("from") or {}), to=dict(d.get("to") or {}), except_=list(d.get("except") or []),
                   adr=str(d["adr"]) if d.get("adr") else None,
                   params={k: v for k, v in d.items() if k not in known}, preset=preset)


@dataclass
class Violation:
    rule: str
    severity: str
    src: str  # a file, or <pkg dir>/package.json for dependency rules
    dst: str | None  # file, "pkg:<name>", "cycle:a > b", "metric" ...
    line: int
    message: str
    adr: str | None = None
    files: tuple[str, ...] = ()  # every file involved (cycles), for scoping
    state: str = "new"  # new | known (in the baseline)

    @property
    def key(self) -> str:
        return f"{self.rule}|{self.src}|{self.dst or ''}"

    def as_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "src": self.src, "dst": self.dst, "line": self.line,
                "message": self.message, "adr": self.adr, "state": self.state, "key": self.key}


# ---------------------------------------------------------------------- presets

def _deny(name, frm, to, sev="error", msg="", **kw):
    return {"name": name, "kind": "deny", "from": frm, "to": to, "severity": sev, "message": msg, **kw}


PRESETS: dict[str, list[dict]] = {
    "layered": [
        _deny("layered/controller-no-repository", {"layer": "controller", "not": {"ring": ["application", "domain"]}},
              {"layer": "repository", "not": {"role": "port"}},
              msg="controllers go through a service, not straight to the repository"),
        _deny("layered/controller-no-model", {"layer": "controller", "not": {"ring": ["application", "domain"]}},
              {"layer": "model"}, "warn", "controllers should not use persistence models directly"),
        _deny("layered/inner-no-controller", {"layer": ["service", "repository", "model"]},
              {"layer": ["controller", "route", "middleware"]}, msg="inner layers must not depend on the HTTP layer"),
        _deny("layered/repository-no-service", {"layer": "repository"}, {"layer": ["service", "controller"]},
              msg="repositories must not depend on services"),
        _deny("layered/model-no-upper", {"layer": "model"}, {"layer": ["service", "repository"]},
              msg="models must not depend on services or repositories"),
        {"name": "layered/no-file-cycles", "kind": "no-cycles", "granularity": "file", "severity": "warn",
         "message": "import cycle"},
    ],
    "nest": [
        {"name": "nest/module-public-api", "kind": "public-api", "severity": "warn",
         "message": "use the other module's public API (its module/index), not its internal files"},
    ],
    "clean": [
        _deny("clean/domain-independent", {"ring": "domain"}, {"ring": ["application", "infrastructure", "presentation"]},
              msg="the domain depends on nothing outside it"),
        _deny("clean/application-no-outer", {"ring": "application"}, {"ring": ["infrastructure", "presentation"]},
              msg="the application layer depends on ports, not on infrastructure or presentation"),
        _deny("clean/infrastructure-no-presentation", {"ring": "infrastructure"}, {"ring": "presentation"}, "warn",
              "infrastructure must not depend on controllers"),
        _deny("clean/presentation-no-infrastructure", {"ring": "presentation"}, {"ring": "infrastructure"}, "warn",
              "controllers reach infrastructure through use cases / ports"),
        {"name": "clean/domain-pure", "kind": "deny-external", "from": {"ring": "domain"},
         "packages": FRAMEWORK_PKGS, "allow": FRAMEWORK_OK, "severity": "error",
         "message": "the domain uses no framework, ORM or HTTP packages"},
    ],
    "ddd": [
        {"name": "ddd/public-api", "kind": "public-api", "severity": "error",
         "message": "another bounded context is reached only through its public API"},
        {"name": "ddd/aggregate-boundary", "kind": "aggregate-boundary", "severity": "error",
         "message": "an aggregate's internal entities are reached only through its root"},
        _deny("ddd/no-foreign-model", {"context": "*"}, {"role": ["entity", "aggregate"], "context": {"not": ["$same", "shared"]}},
              "warn", "a context should not use another context's entities/aggregates; share ids or events"),
        {"name": "ddd/no-context-cycles", "kind": "no-cycles", "granularity": "context", "severity": "error",
         "message": "bounded contexts depend on each other in a cycle"},
    ],
    "react": [
        _deny("react/component-no-api-client", {"layer": "component"}, {"layer": "api-client"}, "warn",
              "components get data through hooks/stores, not by calling the API client"),
        _deny("react/page-no-page", {"layer": "page"}, {"layer": "page"}, "warn", "pages do not import other pages"),
    ],
    "deps": [
        {"name": "deps/duplicate-libs", "kind": "duplicate-libs", "severity": "warn",
         "message": "two libraries for the same job"},
        {"name": "deps/version-drift", "kind": "version-drift", "severity": "warn",
         "message": "the same dependency with different versions across packages"},
    ],
    "naming": [
        {"name": "naming", "kind": "naming", "severity": "warn"},
    ],
    "fullstack": [
        {"name": "contracts", "kind": "contracts", "severity": "error"},
    ],
}
PRESETS["hexagonal"] = PRESETS["clean"]
PRESET_EXTENDS = {"ddd": ["clean"], "nest": ["layered"]}
PRESET_HELP = {
    "layered": "controller -> service -> repository -> model, no file cycles",
    "nest": "layered + modules talk through their public API",
    "clean": "domain <- application <- infrastructure/presentation; framework-free domain (alias: hexagonal)",
    "ddd": "clean + bounded contexts (public API, aggregate boundary, no foreign entities, no context cycles)",
    "react": "components fetch through hooks/stores; pages do not import pages",
    "deps": "duplicate libraries and version drift between packages",
    "naming": "file/class/route naming conventions (inferred from the project or set in [naming])",
    "fullstack": "front-end calls match back-end routes (method and path)",
}


def expand_presets(names: list[str]) -> list[str]:
    out: list[str] = []

    def add(n: str) -> None:
        for p in PRESET_EXTENDS.get(n, []):
            add(p)
        if n not in out:
            out.append(n)

    for n in names:
        add(n)
    return out


def detect_presets(g: Graph, a: arch_mod.Arch) -> list[str]:
    """What the project looks like -> the presets that fit it."""
    out = []
    layers = {f.layer for f in g.files.values()}
    deps = {d for p in (g.meta.get("packages") or {}).values() for d in (p.get("deps") or {})}
    nest = "@nestjs/core" in deps or any("Module" in (s.get("decorators") or []) for ss in g.symbols.values() for s in ss)
    rings_by_folder = {arch_mod.RING_OF_DIR.get(d.lower()) for f in g.files for d in f.split("/")[:-1]}
    clean = len(rings_by_folder & {"domain", "application", "infrastructure"}) >= 2
    contexts = [c for c in a.contexts() if c != "shared"]
    ddd_roles = any(r & {"aggregate", "value-object", "domain-event"} for r in a.roles.values())
    if layers & {"controller", "service", "repository"} and not clean:
        out.append("layered")
    if nest:
        out.append("nest")
    if clean:
        out.append("ddd" if (len(contexts) >= 2 or ddd_roles) else "clean")
    if layers & {"component", "page"}:
        out.append("react")
    out.append("deps")
    out.append("naming")
    if g.api_calls and any(r.kind == "api" for r in g.routes.values()):
        out.append("fullstack")
    return out


def active_rules(g: Graph, a: arch_mod.Arch, rules_cfg: dict) -> tuple[list[Rule], list[str], bool]:
    """(rules, presets, auto-detected?) after `extends`, `disable` and `[severity]` overrides."""
    auto = "extends" not in rules_cfg
    presets = expand_presets(list(rules_cfg.get("extends") or detect_presets(g, a)))
    rules = [Rule.from_dict(d, p) for p in presets for d in PRESETS.get(p, [])]
    rules += [Rule.from_dict(d) for d in rules_cfg.get("rules") or []]
    disabled = set(rules_cfg.get("disable") or [])
    sev = rules_cfg.get("severity") or {}
    out = []
    single = {}  # kinds that mean the same thing whichever preset brings them (ddd + nest public API)
    for r in rules:
        if r.name in disabled:
            continue
        if r.kind in ("public-api", "aggregate-boundary") and not r.frm and r.preset:
            prev = single.get(r.kind)
            if prev is not None:  # keep one; the ddd wording wins, the strictest severity wins
                sev_ = min(prev.severity, r.severity, key=lambda s: SEV_ORDER.get(s, 9))
                if r.preset == "ddd":
                    out[out.index(prev)] = r
                    single[r.kind] = r
                single[r.kind].severity = sev_
                continue
            single[r.kind] = r
        if r.name in sev:
            if sev[r.name] == "off":
                continue
            r.severity = str(sev[r.name])
        out.append(r)
    return out, presets, auto


# ---------------------------------------------------------------------- selectors

def _as_list(v) -> list:
    return v if isinstance(v, list) else [v]


class Matcher:
    def __init__(self, g: Graph, a: arch_mod.Arch, cfg: Config | None = None, rules_cfg: dict | None = None):
        self.g, self.a = g, a
        self.cfg, self.rules_cfg = cfg, rules_cfg or {}

    def value_hit(self, key: str, want, f: str, other: str | None) -> bool:
        g, a = self.g, self.a
        if isinstance(want, dict) and "not" in want:
            return not self.value_hit(key, want["not"], f, other)
        vals = _as_list(want)
        if key == "path":
            return any(arch_mod._glob_hit(f, str(p)) for p in vals)
        if key == "layer":
            return g.layer(f) in vals
        if key == "ring":
            return a.ring.get(f) in vals
        if key == "role":
            return bool(a.roles.get(f, set()) & set(vals))
        if key == "pkg":
            node = g.files.get(f)
            return bool(node) and any(fnmatch.fnmatch(node.pkg, str(p)) for p in vals)
        if key == "public":
            return a.is_public(f) == bool(want)
        if key == "context":
            c = a.context.get(f)
            for v in vals:
                if v == "*" and c:
                    return True
                if v == "$same" and other is not None and c is not None and c == a.context.get(other):
                    return True
                if v == "$other" and other is not None and c is not None and c != a.context.get(other):
                    return True
                if c is not None and (c == v or v == "shared" and arch_mod.is_shared(c)):
                    return True
            return False
        raise ValueError(f"unknown selector key '{key}'")

    def hit(self, sel: dict, f: str, other: str | None = None) -> bool:
        for key, want in sel.items():
            if key == "not":
                if self.hit(want, f, other):
                    return False
                continue
            if not self.value_hit(key, want, f, other):
                return False
        return True


def _excepted(r: Rule, f: str) -> bool:
    return any(arch_mod._glob_hit(f, p) for p in r.except_)


def _sources(g: Graph, m: Matcher, r: Rule) -> list[str]:
    tests = bool(r.params.get("tests"))
    return [f for f in sorted(g.files) if (tests or g.layer(f) not in SKIP_SRC_LAYERS)
            and m.hit(r.frm, f) and not _excepted(r, f)]


def _msg(r: Rule, default: str) -> str:
    return r.message or default


# ---------------------------------------------------------------------- evaluators

def _edge_kinds(r: Rule) -> set[str]:
    return set(IMPORT_KINDS) | ({"type"} if r.params.get("types") else set())


def eval_deny(g, a, m, r: Rule) -> list[Violation]:
    out = []
    kinds = _edge_kinds(r)
    for f in _sources(g, m, r):
        seen = set()
        for e in sorted(g.out.get(f, []), key=lambda e: e.line):
            if e.kind not in kinds or e.dst in seen:
                continue
            if m.hit(r.to, e.dst, f):
                seen.add(e.dst)
                out.append(Violation(r.name, r.severity, f, e.dst, e.line,
                                     _msg(r, f"{g.layer(f)} must not depend on {g.layer(e.dst)}"), r.adr))
    return out


def eval_allow_only(g, a, m, r: Rule) -> list[Violation]:
    out = []
    kinds = _edge_kinds(r)
    for f in _sources(g, m, r):
        seen = set()
        for e in sorted(g.out.get(f, []), key=lambda e: e.line):
            if e.kind not in kinds or e.dst in seen or g.layer(e.dst) in SKIP_SRC_LAYERS:
                continue
            if not m.hit(r.to, e.dst, f):
                seen.add(e.dst)
                out.append(Violation(r.name, r.severity, f, e.dst, e.line, _msg(r, "dependency outside the allowed set"),
                                     r.adr))
    return out


def eval_deny_external(g, a, m, r: Rule) -> list[Violation]:
    pats = [str(p) for p in _as_list(r.params.get("packages") or [])]
    allow = [str(p) for p in _as_list(r.params.get("allow") or [])]
    out = []
    for f in _sources(g, m, r):
        for pkg in sorted(g.externals.get(f, ())):
            if any(fnmatch.fnmatch(pkg, p) for p in pats) and not any(fnmatch.fnmatch(pkg, p) for p in allow):
                out.append(Violation(r.name, r.severity, f, f"pkg:{pkg}", g.external_lines.get((f, pkg), 0),
                                     _msg(r, f"package {pkg} is not allowed here"), r.adr))
    return out


def _unit(g: Graph, a: arch_mod.Arch, f: str, gran: str, level: int) -> str | None:
    if gran == "context":
        return a.context.get(f)
    if gran == "folder":
        parts = f.split("/")[:-1]
        return "/".join(parts[:level]) if parts else "."
    return f


def eval_no_cycles(g, a, m, r: Rule) -> list[Violation]:
    gran = str(r.params.get("granularity", "file"))
    level = int(r.params.get("level", 3))
    files = set(_sources(g, m, r))
    adj: dict[str, set[str]] = defaultdict(set)
    sample: dict[tuple[str, str], tuple[str, str, int]] = {}
    for f in files:
        uf = _unit(g, a, f, gran, level)
        if uf is None:
            continue
        for e in g.out.get(f, []):
            if e.kind not in CYCLE_KINDS or e.dst not in files:
                continue
            ud = _unit(g, a, e.dst, gran, level)
            if ud is None or ud == uf or (gran == "context" and arch_mod._nested(a, uf, ud)):
                continue
            adj[uf].add(ud)
            sample.setdefault((uf, ud), (f, e.dst, e.line))
    out = []
    for cyc in cycles_of({k: sorted(v) for k, v in adj.items()}):
        ring = cyc + cyc[:1]
        hops = [sample.get((ring[i], ring[i + 1])) for i in range(len(cyc))]
        first = hops[0] or (cyc[0], cyc[0], 0)
        involved = tuple(sorted({h[0] for h in hops if h} | {h[1] for h in hops if h}))
        label = " > ".join(ring)
        detail = "; ".join(f"{h[0]}:{h[2]} > {h[1]}" for h in hops if h) if gran != "file" else ""
        out.append(Violation(r.name, r.severity, first[0], f"cycle:{label}", first[2],
                             _msg(r, "cycle") + (f" ({detail})" if detail else ""), r.adr, involved))
    return out


def eval_public_api(g, a, m, r: Rule) -> list[Violation]:
    allow = set(_as_list(r.params.get("allow") or ["shared"]))
    out = []
    links = arch_mod.context_links(g, a, types=bool(r.params.get("types")))
    for (cs, cd), link in sorted(links.items()):
        if cd in allow or ("shared" in allow and arch_mod.is_shared(cd)):
            continue
        for src, dst, line in sorted(link.leaks):
            if m.hit(r.frm, src) and not _excepted(r, src):
                out.append(Violation(r.name, r.severity, src, dst, line,
                                     _msg(r, f"{cs} reaches into {cd}'s internals") + f" [{cs} -> {cd}]", r.adr))
    return out


def eval_aggregate_boundary(g, a, m, r: Rule) -> list[Violation]:
    allow_vo = r.params.get("allow_value_objects", True)
    out = []
    for ag in arch_mod.aggregates(g, a):
        for mem in ag.members:
            if allow_vo and "value-object" in a.roles.get(mem, set()):
                continue
            for e in g.inn.get(mem, []):
                src = e.src
                if e.kind not in IMPORT_KINDS + ("type",) or g.layer(src) in SKIP_SRC_LAYERS:
                    continue
                if src == ag.root_file or posixpath.dirname(src) == ag.folder:
                    continue
                if src in ag.members or not m.hit(r.frm, src) or _excepted(r, src):
                    continue
                out.append(Violation(r.name, r.severity, src, mem, e.line,
                                     _msg(r, "internal entity of an aggregate") + f" [{ag.root}]", r.adr))
    return out


def eval_metric(g, a, m, r: Rule) -> list[Violation]:
    limit = int(r.params.get("max", 0))
    out = []
    for f in _sources(g, m, r):
        if r.kind == "max-deps":
            n = len({e.dst for e in g.out.get(f, []) if e.kind in IMPORT_KINDS})
            what = "dependencies"
        elif r.kind == "max-importers":
            n = len(g.importers(f))
            what = "importers"
        else:
            n = g.files[f].loc
            what = "lines"
        if limit and n > limit:
            out.append(Violation(r.name, r.severity, f, None, 0, _msg(r, f"{n} {what} (max {limit})")
                                 + (f" [{n} {what}]" if r.message else ""), r.adr))
    return out


def _pkg_json(d: str) -> str:
    return f"{d}/package.json" if d else "package.json"


def eval_duplicate_libs(g, a, m, r: Rule) -> list[Violation]:
    used: dict[str, set[str]] = defaultdict(set)
    for f, pkgs in g.externals.items():
        for p in pkgs:
            used[p].add(f)
    out = []
    groups = {**LIB_GROUPS, **(r.params.get("groups") or {})}
    for group, libs in groups.items():
        present = [lib for lib in libs if used.get(lib)]
        if len(present) < 2:
            continue
        main = max(present, key=lambda lib: (len(used[lib]), lib))
        for lib in sorted(present):
            if lib == main:
                continue
            first = sorted(used[lib])[0]
            out.append(Violation(r.name, r.severity, first, f"pkg:{lib}", g.external_lines.get((first, lib), 0),
                                 _msg(r, "two libraries for the same job")
                                 + f": {group} uses {', '.join(sorted(present))} ({lib} in {len(used[lib])} files,"
                                   f" {main} in {len(used[main])})", r.adr))
    return out


def eval_version_drift(g, a, m, r: Rule) -> list[Violation]:
    if not g.meta.get("workspace") and not r.params.get("always"):
        return []  # independent repositories in one folder may differ on purpose
    pk = g.meta.get("packages") or {}
    versions: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    local = {p.get("name") for p in pk.values()}
    for d, p in pk.items():
        for dep, v in (p.get("deps") or {}).items():
            if dep in local or str(v).startswith(("workspace:", "link:", "file:")):
                continue
            versions[dep][str(v)].append(d)
    out = []
    for dep, by_v in sorted(versions.items()):
        if len(by_v) < 2:
            continue
        common = max(by_v, key=lambda v: (len(by_v[v]), v))
        for v, dirs in sorted(by_v.items()):
            if v == common:
                continue
            for d in sorted(dirs):
                out.append(Violation(r.name, r.severity, _pkg_json(d), f"pkg:{dep}", 0,
                                     _msg(r, "version drift") + f": {dep} {v} here, {common} in "
                                     + ", ".join(_pkg_json(x) for x in sorted(by_v[common])[:3]), r.adr))
    return out


def eval_naming(g, a, m, r: Rule) -> list[Violation]:
    from nimblegraph import naming
    return naming.violations(g, a, m, r)


def eval_contracts(g, a, m, r: Rule) -> list[Violation]:
    from nimblegraph import testmap
    return testmap.contract_violations(g, a, m, r)


def eval_tests_missing(g, a, m, r: Rule) -> list[Violation]:
    from nimblegraph import testmap
    return testmap.missing_test_violations(g, a, m, r)


EVALUATORS = {
    "deny": eval_deny, "allow-only": eval_allow_only, "deny-external": eval_deny_external,
    "no-cycles": eval_no_cycles, "public-api": eval_public_api, "aggregate-boundary": eval_aggregate_boundary,
    "max-deps": eval_metric, "max-importers": eval_metric, "max-loc": eval_metric,
    "duplicate-libs": eval_duplicate_libs, "version-drift": eval_version_drift,
    "naming": eval_naming, "contracts": eval_contracts, "tests-missing": eval_tests_missing,
}


# ---------------------------------------------------------------------- run + baseline

@dataclass
class CheckResult:
    violations: list[Violation]
    rules: list[Rule]
    presets: list[str]
    auto: bool
    resolved: list[dict] = field(default_factory=list)  # baseline entries that no longer happen
    errors: list[str] = field(default_factory=list)  # broken rules

    def new(self, min_sev: str = "info") -> list[Violation]:
        return [v for v in self.violations if v.state == "new" and SEV_ORDER[v.severity] <= SEV_ORDER[min_sev]]

    def counts(self, state: str | None = None) -> dict[str, int]:
        c = {s: 0 for s in SEVERITIES}
        for v in self.violations:
            if state is None or v.state == state:
                c[v.severity] += 1
        return c


def load_baseline(cfg: Config) -> dict:
    p = arch_mod.governance_path(cfg, BASELINE_FILE)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("violations", {})
    except (OSError, ValueError):
        return {}


def save_baseline(cfg: Config, violations: list[Violation]) -> str:
    p = cfg.dir / BASELINE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "created": time.strftime("%Y-%m-%d %H:%M"),
            "violations": {v.key: {"rule": v.rule, "severity": v.severity, "src": v.src, "dst": v.dst,
                                   "message": v.message} for v in sorted(violations, key=lambda v: v.key)}}
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return str(p)


def run(cfg: Config, g: Graph, rules_cfg: dict | None = None, only: list[str] | None = None) -> CheckResult:
    rules_cfg = arch_mod.load_rules(cfg) if rules_cfg is None else rules_cfg
    a = arch_mod.model(g, rules_cfg)
    m = Matcher(g, a, cfg, rules_cfg)
    rules, presets, auto = active_rules(g, a, rules_cfg)
    if only:
        rules = [r for r in rules if any(r.name == o or r.name.startswith(o.rstrip("/") + "/") or r.preset == o
                                         for o in only)]
    out: list[Violation] = []
    errors = []
    for r in rules:
        ev = EVALUATORS.get(r.kind)
        if ev is None:
            errors.append(f"{r.name}: unknown kind '{r.kind}' (kinds: {', '.join(sorted(EVALUATORS))})")
            continue
        if r.severity not in SEVERITIES:
            errors.append(f"{r.name}: severity must be one of {', '.join(SEVERITIES)}")
            continue
        try:
            out += ev(g, a, m, r)
        except ValueError as e:
            errors.append(f"{r.name}: {e}")
    sev = rules_cfg.get("severity") or {}
    disabled = set(rules_cfg.get("disable") or [])
    kept = []
    for v in out:  # overrides also reach sub-rules a kind emits (naming/file-case, contracts/no-route ...)
        if v.rule in disabled or sev.get(v.rule) == "off":
            continue
        if v.rule in sev and sev[v.rule] in SEVERITIES:
            v.severity = sev[v.rule]
        kept.append(v)
    out = kept
    uniq: dict[str, Violation] = {}
    for v in out:
        if v.key not in uniq or SEV_ORDER[v.severity] < SEV_ORDER[uniq[v.key].severity]:
            uniq[v.key] = v
    base = load_baseline(cfg)
    for v in uniq.values():
        v.state = "known" if v.key in base else "new"
    names = {r.name for r in rules} | {v.rule for v in uniq.values()}
    resolved = [dict(val, key=k) for k, val in base.items() if k not in uniq and val.get("rule") in names]
    vs = sorted(uniq.values(), key=lambda v: (SEV_ORDER[v.severity], v.rule, v.src, v.dst or "", v.line))
    return CheckResult(vs, rules, presets, auto, resolved, errors)


def in_scope(v: Violation, scope: set[str]) -> bool:
    return v.src in scope or any(f in scope for f in v.files)


# ---------------------------------------------------------------------- rules.toml / export

def init_toml(presets: list[str], a: arch_mod.Arch) -> str:
    ctx = [c for c in a.contexts() if c != "shared"]
    lines = [
        "# nimblegraph architecture rules (versioned). `nimblegraph rules` lists them, `nimblegraph check` evaluates them.",
        "# Presets: " + "; ".join(f"{k}: {v}" for k, v in PRESET_HELP.items() if k != "hexagonal"),
        f"extends = {json.dumps(presets)}",
        "",
        "# disable = [\"layered/controller-no-model\"]     # rule names, see `nimblegraph rules`",
        "",
        "# [severity]                                     # error | warn | info | off, per rule or sub-rule",
        "# \"naming/file-case\" = \"info\"",
        "",
        "# Bounded contexts. Detected now: " + (", ".join(f"{c} ({a.context_how.get(c)}: {a.context_root.get(c)})"
                                                      for c in ctx) or "none") + ".",
        "# Name them here when the folders do not say it; `public` lists their public API besides index/module files.",
        "# [contexts]",
        "# orders = [\"src/modules/orders/**\"]",
        "# [contexts.billing]",
        "# paths = [\"src/modules/billing/**\"]",
        "# public = [\"src/modules/billing/billing.facade.ts\"]",
        "",
        "# [naming]                                       # explicit conventions win over the inferred ones",
        "# files = \"kebab\"                                 # kebab | camel | pascal | snake",
        "# [naming.layers.service]",
        "# file = \"*.service.ts\"",
        "# class = \"*Service\"",
        "",
        "# [tests]",
        "# require_test_for = [\"route:api\", \"role:use-case\"]   # each one without a test is a violation",
        "",
        "# Your own rules. Selectors: layer, path (glob), context ($same/$other/*), ring, role, pkg, public, not.",
        "# Kinds: deny, allow-only, deny-external, no-cycles, public-api, aggregate-boundary, max-deps, max-importers,",
        "# max-loc, duplicate-libs, version-drift, naming, contracts, tests-missing.",
        "# [[rules]]",
        "# name = \"web never imports the server\"",
        "# kind = \"deny\"",
        "# from = { path = \"apps/web/**\" }",
        "# to = { path = \"apps/server/**\" }",
        "# severity = \"error\"",
        "# adr = \"0003\"",
        "",
    ]
    return "\n".join(lines)


def _regex_of(files: list[str], all_files: set[str]) -> str:
    """Path regex for dependency-cruiser: whole folders when every indexed file in them matches."""
    import re as _re
    by_dir: dict[str, list[str]] = defaultdict(list)
    for f in files:
        by_dir[posixpath.dirname(f)].append(f)
    parts = []
    for d, fs in sorted(by_dir.items()):
        in_dir = [x for x in all_files if posixpath.dirname(x) == d]
        if d and len(fs) == len(in_dir) and len(fs) > 1:
            parts.append(_re.escape(d + "/") + "[^/]+$")
        else:
            parts += [_re.escape(f) + "$" for f in sorted(fs)]
    return "^(" + "|".join(parts) + ")" if parts else "^$a"


def export_depcruise(g: Graph, a: arch_mod.Arch, rules: list[Rule]) -> str:
    """.dependency-cruiser.cjs `forbidden` rules for the deny / deny-external / no-cycles rules. Selectors other
    than paths become the files that match them today (re-export after big moves)."""
    m = Matcher(g, a)
    all_files = set(g.files)
    forbidden = []
    for r in rules:
        sev = {"error": "error", "warn": "warn", "info": "info"}[r.severity]
        src = [f for f in sorted(g.files) if g.layer(f) not in SKIP_SRC_LAYERS and m.hit(r.frm, f)]
        if r.kind == "deny":
            dst = [f for f in sorted(g.files) if m.hit({k: v for k, v in r.to.items() if k != "context"}, f)]
            if not src or not dst:
                continue
            forbidden.append({"name": r.name, "severity": sev, "comment": r.message,
                              "from": {"path": _regex_of(src, all_files)}, "to": {"path": _regex_of(dst, all_files)}})
        elif r.kind == "deny-external":
            pats = [p.replace("*", ".*") for p in r.params.get("packages", [])]
            forbidden.append({"name": r.name, "severity": sev, "comment": r.message,
                              "from": {"path": _regex_of(src, all_files)},
                              "to": {"dependencyTypes": ["npm", "npm-dev", "npm-peer", "npm-no-pkg", "npm-unknown"],
                                     "path": "node_modules/(" + "|".join(pats) + ")/"}})
        elif r.kind == "no-cycles" and r.params.get("granularity", "file") == "file":
            forbidden.append({"name": r.name, "severity": sev, "comment": r.message, "from": {},
                              "to": {"circular": True}})
    body = json.dumps({"forbidden": forbidden, "options": {"doNotFollow": {"path": "node_modules"},
                                                          "tsPreCompilationDeps": True}}, indent=2)
    return ("// generated by `nimblegraph rules --export depcruise` (nimblegraph). Context, aggregate and naming rules are\n"
            "// nimblegraph-only: keep `nimblegraph check` in CI for them.\nmodule.exports = " + body + ";\n")
