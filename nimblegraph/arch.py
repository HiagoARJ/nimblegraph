"""Architecture model on top of the graph: bounded context, ring (domain / application / infrastructure /
presentation) and DDD roles of every file, plus how contexts depend on each other. Computed on demand from the
loaded graph (cheap, nothing stored); `.nimblegraph/rules.toml` can name contexts and their public API."""

from __future__ import annotations

import fnmatch
import posixpath
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from nimblegraph import lang as langs
from nimblegraph.config import DIR_NAME, Config
from nimblegraph.query import IMPORT_KINDS, Graph

RULES_FILE = "rules.toml"

CONTEXT_PARENTS = {"modules", "domains", "contexts", "bounded-contexts", "boundedcontexts", "bounded_contexts",
                   "features", "subdomains"}
SHARED_NAMES = {"shared", "common", "core", "kernel", "shared-kernel", "sharedkernel", "shared_kernel"}
RINGS = ("domain", "application", "infrastructure", "presentation")
RING_DIRS = {
    "domain": {"domain", "entities", "aggregates", "value-objects", "valueobjects"},
    "application": {"application", "use-cases", "usecases", "use_cases", "app-services"},
    "infrastructure": {"infra", "infrastructure", "adapters", "persistence", "database", "db", "repositories",
                       "gateways", "providers"},
    "presentation": {"presentation", "interfaces", "controllers", "http", "rest", "graphql", "resolvers"},
}
RING_OF_DIR = {d: r for r, ds in RING_DIRS.items() for d in ds}
RING_OF_LAYER = {"controller": "presentation", "route": "presentation", "api-route": "presentation",
                 "repository": "infrastructure"}
# Nest modules named after a technical layer are not bounded contexts
TECH_MODULES = set(RING_OF_DIR) | {"api", "app", "core", "common", "config", "configs", "database", "db", "http",
                                   "modules", "features", "utils", "lib", "libs", "infra", "server", "main", "root",
                                   "health", "cqrs", "logger", "logging", "events"}
PUBLIC_DIRS = {"api", "public", "contracts", "facade", "facades"}

AGG_BASES = {"AggregateRoot", "Aggregate", "BaseAggregateRoot", "AggregateRootBase", "AbstractAggregateRoot"}
VO_BASES = {"ValueObject", "BaseValueObject", "ValueObjectBase", "AbstractValueObject"}
ENTITY_BASES = {"Entity", "BaseEntity", "AbstractEntity", "DomainEntity", "EntityBase"}
EVENT_BASES = {"DomainEvent", "IDomainEvent", "IEvent", "BaseDomainEvent", "IntegrationEvent"}
HANDLER_DECOS = {"CommandHandler": "command-handler", "QueryHandler": "query-handler",
                 "EventsHandler": "event-handler", "EventHandler": "event-handler", "Saga": "event-handler"}
HANDLER_IFACES = {"ICommandHandler": "command-handler", "IQueryHandler": "query-handler",
                  "IEventHandler": "event-handler"}
DOMAIN_ROLES = {"aggregate", "entity", "value-object", "domain-event", "domain-service", "factory", "port"}


@dataclass
class Arch:
    context: dict[str, str | None] = field(default_factory=dict)
    ring: dict[str, str | None] = field(default_factory=dict)
    roles: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # file -> roles
    sym_roles: dict[tuple[str, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    context_root: dict[str, str] = field(default_factory=dict)  # context -> folder ("" when spread)
    context_how: dict[str, str] = field(default_factory=dict)  # config | nest | folder | package | shared
    public: dict[str, list[str]] = field(default_factory=dict)  # context -> extra public globs from rules.toml
    public_files: set[str] = field(default_factory=set)  # @Module exports, DTOs / types (the published language)

    def files_of(self, ctx: str) -> list[str]:
        return sorted(f for f, c in self.context.items() if c == ctx)

    def contexts(self) -> list[str]:
        return sorted({c for c in self.context.values() if c})

    def is_public(self, f: str) -> bool:
        """Entry point of its context: index/module file at the context root, api|public|contracts folders,
        or a glob from `[contexts.<name>] public`."""
        ctx = self.context.get(f)
        if not ctx or f in self.public_files:
            return True
        root = self.context_root.get(ctx, "")
        rel = f[len(root) + 1:] if root and f.startswith(root + "/") else f
        if "/" not in rel and re.match(r"^(index|.*\.module|public-api|api)\.[cm]?[jt]sx?$", rel):
            return True
        if "/" not in rel and rel in ("__init__.py", "mod.rs", "lib.rs", "api.py", "api.go", "doc.go"):
            return True  # a Python package's / Rust module's entry file
        if set(rel.split("/")[:-1]) & PUBLIC_DIRS:
            return True
        return any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(rel, g) for g in self.public.get(ctx, []))


# ---------------------------------------------------------------------- rules.toml

def governance_path(cfg: Config, name: str) -> Path:
    """Governance files live next to the index (honours NIMBLEGRAPH_INDEX_DIR), falling back to <root>/.nimblegraph."""
    p = cfg.dir / name
    if p.is_file():
        return p
    alt = cfg.root / DIR_NAME / name
    return alt if alt.is_file() else p


def load_rules(cfg: Config) -> dict:
    p = governance_path(cfg, RULES_FILE)
    if not p.is_file():
        return {}
    with p.open("rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------- helpers

def _dirs(path: str) -> list[str]:
    return path.split("/")[:-1]


def _glob_hit(path: str, pattern: str) -> bool:
    pattern = pattern.rstrip("/")
    if fnmatch.fnmatch(path, pattern):
        return True
    prefix = pattern[:-3] if pattern.endswith("/**") else pattern
    return "*" not in prefix and path.startswith(prefix.rstrip("/") + "/")


def _glob_root(pattern: str) -> str:
    parts = []
    for seg in pattern.split("/"):
        if any(ch in seg for ch in "*?["):
            break
        parts.append(seg)
    return "/".join(parts)


def _package_dirs(g: Graph) -> list[str]:
    return sorted((g.meta.get("packages") or {}).keys())


def _nest_module_dirs(g: Graph) -> dict[str, str]:
    """dir -> context name, from @Module classes (the root AppModule only aggregates and is skipped)."""
    out: dict[str, str] = {}
    for f, syms in g.symbols.items():
        base = posixpath.basename(f)
        if not re.search(r"\.module\.[cm]?[jt]s$", base) or base.startswith(("app.", "main.", "root.")):
            continue
        if any("Module" in (s.get("decorators") or []) for s in syms if not s["parent"]):
            d = posixpath.dirname(f)
            parts = [x.lower() for x in d.split("/")]
            # technical modules (api, infrastructure...) and anything under shared/ are not bounded contexts
            if d and parts[-1] not in TECH_MODULES | {"src"} and not set(parts) & SHARED_NAMES:
                out[d] = posixpath.basename(d)
    names: dict[str, list[str]] = defaultdict(list)
    for d, n in out.items():
        names[n].append(d)
    for n, ds in names.items():  # same folder name in two packages -> parent/name
        if len(ds) > 1:
            for d in ds:
                out[d] = "/".join(d.split("/")[-2:])
    return out


def _context_of(path: str, explicit: list[tuple[str, list[str]]], nest: dict[str, str],
                pkg_dirs: list[str], multi_pkg: bool) -> tuple[str | None, str, str]:
    """(context, how, context root)."""
    for name, globs in explicit:
        for gl in globs:
            if _glob_hit(path, gl):
                return name, "config", _glob_root(gl)
    best = ""
    for d in nest:
        if path.startswith(d + "/") and len(d) > len(best):
            best = d
    if best:
        name = nest[best]
        return ("shared" if name.lower() in SHARED_NAMES else name), "nest", best
    dirs = _dirs(path)
    for i, d in enumerate(dirs[:-1] if dirs else []):
        if d.lower() in CONTEXT_PARENTS:
            name = dirs[i + 1]
            root = "/".join(dirs[:i + 2])
            return ("shared" if name.lower() in SHARED_NAMES else name), "folder", root
    pkg = _pkg_of(path, pkg_dirs) if multi_pkg else ""
    skip = len(pkg.split("/")) if pkg else 0
    for i, d in enumerate(dirs):
        # the shared kernel sits at the top of a package (shared/, src/shared/); a deeper common/ is local
        if d.lower() in SHARED_NAMES and i - skip <= 1:
            # one shared kernel per package of a monorepo (apps/api/src/shared != apps/web/src/shared)
            return (f"{posixpath.basename(pkg)}/shared" if pkg else "shared"), "shared", "/".join(dirs[:i + 1])
    if multi_pkg:
        best = None
        for d in pkg_dirs:
            if d and (path.startswith(d + "/")) and (best is None or len(d) > len(best)):
                best = d
        if best:
            name = posixpath.basename(best)
            return ("shared" if name.lower() in SHARED_NAMES else name), "package", best
    return None, "", ""


def _pkg_of(path: str, pkg_dirs: list[str]) -> str:
    best = ""
    for d in pkg_dirs:
        if d and path.startswith(d + "/") and len(d) > len(best):
            best = d
    return best


def is_shared(ctx: str | None) -> bool:
    return bool(ctx) and (ctx == "shared" or ctx.endswith("/shared"))


PUBLISHED_ROLES = {"dto", "command", "query", "domain-event"}  # what other contexts may use: the published language
RE_PUBLISHED_FILE = re.compile(r"\.(enum|enums|constants?|types?|interface|interfaces|events?|contract|contracts)\.[cm]?[jt]sx?$")


def _ring_of(path: str, layer: str) -> str | None:
    for d in _dirs(path):  # outermost ring folder wins: infra/http/controllers is infrastructure
        r = RING_OF_DIR.get(d.lower())
        if r:
            return r
    return RING_OF_LAYER.get(layer)


def _dotted(path: str) -> str:
    base = posixpath.basename(path).lower()
    return langs.strip_ext(base)


def _symbol_roles(path: str, s: dict, layer: str, ring: str | None) -> set[str]:
    name = s["name"]
    kind = s["kind"]
    decos = set(s.get("decorators") or [])
    ext = s.get("extends")
    impl = set(s.get("implements") or [])
    dirs = {d.lower() for d in _dirs(path)}
    dotted = _dotted(path)
    abstract = kind == "type" or s.get("abstract")
    roles: set[str] = set()
    if kind not in ("class", "type", "function", "const"):
        return roles
    if "Module" in decos:
        return {"module"}
    for deco, role in HANDLER_DECOS.items():
        if deco in decos:
            roles.add(role)
    for iface, role in HANDLER_IFACES.items():
        if iface in impl:
            roles.add(role)
    if roles:
        return roles
    if decos & {"Controller", "Resolver"} or layer == "controller" and kind == "class" and name.endswith("Controller"):
        return {"controller"}
    if ext in AGG_BASES or "AggregateRoot" in decos or (kind == "class" and name.endswith("Aggregate")) \
            or (kind == "class" and "aggregates" in dirs):
        roles.add("aggregate")
    elif ext in VO_BASES or re.search(r"\.(vo|value-object|valueobject|value_object)$", dotted) \
            or dirs & {"value-objects", "valueobjects", "vo", "vos"} and kind == "class":
        roles.add("value-object")
    elif (ext in EVENT_BASES or impl & EVENT_BASES) or (
            name.endswith("Event") and kind == "class" and (dirs & {"events", "event"} or dotted.endswith(".event"))):
        roles.add("domain-event")
    elif kind == "class" and ("Entity" in decos or ext in ENTITY_BASES or dotted.endswith(".entity")
                              or dirs & {"entities", "entity"}):
        roles.add("entity")
    elif kind == "class" and (name.endswith("Command") and (dirs & {"commands", "command"} or ".command" in dotted)
                              or "ICommand" in impl):
        roles.add("command")
    elif kind == "class" and (name.endswith("Query") and (dirs & {"queries", "query"} or ".query" in dotted)
                              or "IQuery" in impl):
        roles.add("query")
    elif re.search(r"(UseCase|Usecase|Interactor)$", name) or re.search(r"\.(use-case|usecase|use_case)$", dotted) \
            or dirs & {"use-cases", "usecases", "use_cases"} and kind in ("class", "function"):
        roles.add("use-case")
    elif re.search(r"(Repository|Repo)$", name) or (layer == "repository" and kind == "class"):
        roles.add("port" if abstract else "repository")
    elif abstract and (dirs & {"ports", "port"} or re.search(r"(Port|Gateway)$", name)):
        roles.add("port")
    elif name.endswith("Adapter") and kind == "class" or "adapters" in dirs and kind == "class":
        roles.add("adapter")
    elif name.endswith("Factory"):
        roles.add("factory")
    elif name.endswith("Mapper"):
        roles.add("mapper")
    elif re.search(r"(Dto|DTO)$", name) or dotted.endswith(".dto"):
        roles.add("dto")
    elif kind == "class" and (name.endswith("Service") or layer == "service"):
        roles.add("domain-service" if ring == "domain" else "service")
    return roles


# ---------------------------------------------------------------------- model

def model(g: Graph, rules: dict | None = None) -> Arch:
    cached = getattr(g, "_arch", None)
    if cached is not None and cached[0] is rules:
        return cached[1]
    rules = rules or {}
    a = Arch()
    explicit: list[tuple[str, list[str]]] = []
    for name, spec in (rules.get("contexts") or {}).items():
        globs = spec if isinstance(spec, list) else (spec.get("paths") or []) if isinstance(spec, dict) else [spec]
        explicit.append((name, [str(x) for x in globs]))
        if isinstance(spec, dict) and spec.get("public"):
            a.public[name] = [str(x) for x in spec["public"]]
    nest = _nest_module_dirs(g)
    pkg_dirs = _package_dirs(g)
    a.public_files = set(g.meta.get("module_exports") or [])
    multi = len([d for d in pkg_dirs if d]) > 1
    found: dict[str, tuple[str | None, str, str]] = {}
    roots_of: dict[str, set[str]] = defaultdict(set)
    for f, node in g.files.items():
        found[f] = _context_of(f, explicit, nest, pkg_dirs, multi)
        ctx, how, root = found[f]
        if ctx and how in ("nest", "folder", "package"):
            roots_of[ctx].add(root)
        a.ring[f] = _ring_of(f, node.layer or "module")
    # the same name in two places (api quotes module, web quotes feature) -> qualify with the package
    rename: dict[tuple[str, str], str] = {}
    for ctx, roots in roots_of.items():
        if len(roots) > 1:
            for root in roots:
                pkg = _pkg_of(root + "/x", pkg_dirs)
                qual = posixpath.basename(pkg) if pkg and pkg != root else posixpath.basename(posixpath.dirname(root))
                rename[(ctx, root)] = f"{qual}/{ctx}" if qual else ctx
    for f, (ctx, how, root) in found.items():
        ctx = rename.get((ctx, root), ctx) if ctx else ctx
        a.context[f] = ctx
        if ctx:
            a.context_how.setdefault(ctx, how)
            if ctx not in a.context_root or (how != "shared" and len(root) < len(a.context_root[ctx])):
                a.context_root[ctx] = root
    for f, syms in g.symbols.items():
        if f not in g.files:
            continue
        layer = g.layer(f)
        for s in syms:
            if s["parent"]:
                continue
            r = _symbol_roles(f, s, layer, a.ring.get(f))
            if r:
                a.sym_roles[(f, s["name"])] |= r
    # second pass: an interface / abstract class implemented outside its own ring is a port, its implementors adapters
    for (f, sym), bases in g.inherits.items():
        for _kind, rf, rs, _pkg in bases:
            if not rf or rf not in g.files:
                continue
            base = next((s for s in g.symbols.get(rf, []) if s["name"] == rs and not s["parent"]), None)
            if not base or not (base["kind"] == "type" or base.get("abstract")):
                continue
            if a.ring.get(rf) in ("domain", "application") and a.ring.get(f) not in ("domain", "application", None) \
                    or {d.lower() for d in _dirs(rf)} & {"ports", "port"}:
                a.sym_roles[(rf, rs)].discard("repository")
                a.sym_roles[(rf, rs)].add("port")
                a.sym_roles[(f, sym)].add("adapter")
    for (f, _sym), roles in a.sym_roles.items():
        a.roles[f] |= roles
    for f in g.files:  # DTOs, types, commands/queries/events, enums and constants: the published language
        if g.layer(f) == "type" or a.roles.get(f, set()) & PUBLISHED_ROLES or RE_PUBLISHED_FILE.search(f):
            a.public_files.add(f)
    for f, roles in list(a.roles.items()):
        if not roles:
            del a.roles[f]
    g._arch = (rules, a)  # type: ignore[attr-defined]
    return a


# ---------------------------------------------------------------------- context links / aggregates

@dataclass
class ContextLink:
    src: str
    dst: str
    imports: int = 0
    http: int = 0
    leaks: list[tuple[str, str, int]] = field(default_factory=list)  # (src file, internal dst file, line)
    files: set[tuple[str, str]] = field(default_factory=set)


def context_links(g: Graph, a: Arch, types: bool = False) -> dict[tuple[str, str], ContextLink]:
    """How contexts depend on each other: imports (and type imports with `types`), HTTP calls front -> back,
    and leaks (imports of another context's internal files instead of its public API)."""
    kinds = set(IMPORT_KINDS) | ({"type"} if types else set())
    out: dict[tuple[str, str], ContextLink] = {}
    for src, edges in g.out.items():
        cs = a.context.get(src)
        if not cs or g.layer(src) == "test":
            continue
        # the composition root (entries, Nest modules) wires every context together: no leak there
        composition = g.files[src].is_entry if src in g.files else False
        composition = composition or bool(a.roles.get(src, set()) & {"module"})
        for e in edges:
            if e.kind not in kinds:
                continue
            cd = a.context.get(e.dst)
            if not cd or cd == cs:
                continue
            link = out.setdefault((cs, cd), ContextLink(cs, cd))
            link.imports += 1
            link.files.add((src, e.dst))
            public = a.is_public(e.dst) or (e.via is not None and a.context.get(e.via) == cd and a.is_public(e.via))
            if not is_shared(cd) and not public and not composition and not _nested(a, cs, cd):
                link.leaks.append((src, e.dst, e.line))
    for c in g.api_calls:
        if not c.route_id or g.layer(c.file) == "test":
            continue
        r = g.routes.get(c.route_id)
        cs, cd = a.context.get(c.file), a.context.get(r.target) if r else None
        if cs and cd and cs != cd:
            link = out.setdefault((cs, cd), ContextLink(cs, cd))
            link.http += 1
            link.files.add((c.file, r.target))
    return out


def _nested(a: Arch, c1: str, c2: str) -> bool:
    """One context lives inside the other (an app package and its feature folders): parent <-> child is the app
    shell using its features or a feature using the app's common code, not a leak between siblings."""
    r1, r2 = a.context_root.get(c1, ""), a.context_root.get(c2, "")
    return bool(r1 and r2) and (r2.startswith(r1 + "/") or r1.startswith(r2 + "/"))


@dataclass
class Aggregate:
    root_file: str
    root: str
    context: str | None
    members: list[str] = field(default_factory=list)  # files of internal entities / value objects

    @property
    def folder(self) -> str:
        return posixpath.dirname(self.root_file)


def aggregates(g: Graph, a: Arch) -> list[Aggregate]:
    """Each aggregate root with the domain files it owns: what it imports from its own folder (entities, value
    objects, plain classes) that is not another aggregate, event or port."""
    roots = sorted((f, s) for (f, s), r in a.sym_roles.items() if "aggregate" in r)
    root_files = {f for f, _ in roots}
    out = []
    for f, s in roots:
        ag = Aggregate(f, s, a.context.get(f))
        folder = posixpath.dirname(f)
        for e in g.out.get(f, []):
            d = e.dst
            if e.kind not in IMPORT_KINDS + ("type",) or d in root_files or d == f:
                continue
            if not (posixpath.dirname(d) == folder or d.startswith(folder + "/")):
                continue
            if a.roles.get(d, set()) & {"domain-event", "port", "aggregate", "domain-service", "factory"}:
                continue
            if d not in ag.members:
                ag.members.append(d)
        ag.members.sort()
        out.append(ag)
    return out
