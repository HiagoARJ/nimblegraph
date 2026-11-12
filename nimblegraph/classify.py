"""Layer of each file. Priority: config.toml override > strong framework facts > path/name patterns,
with weak framework facts (usage-based) used when the path says nothing more specific."""

from __future__ import annotations

import fnmatch
import posixpath
import re

from nimblegraph import lang as langs

LAYERS = ("page", "layout", "component", "hook", "store", "api-client", "api-route", "route", "middleware",
          "controller", "service", "repository", "model", "util", "type", "config", "test", "entry", "module")

# display order for flows: front to back
FLOW_ORDER = {l: i for i, l in enumerate((
    "entry", "page", "layout", "route", "component", "hook", "store", "api-client", "api-route", "middleware",
    "controller", "service", "repository", "model", "util", "type", "config", "module", "test"))}

RE_TEST = re.compile(r"(^|/)(__tests__|__mocks__|tests?|e2e|cypress|playwright)/|\.(test|spec|e2e|cy|stories)\.[cm]?[jt]sx?$")
RE_CONFIG = re.compile(r"\.config\.[cm]?[jt]s$|(^|/)\.?[\w-]*rc\.[cm]?[jt]s$")
ENTRY_STEMS = {"main", "index", "server", "app", "bootstrap", "entry", "cli"}
GENERIC = {"module", "util", "component", "service", "entry"}


def _dirs(path: str) -> set[str]:
    return {d.lower() for d in path.split("/")[:-1]}


RE_OTHER = [  # (layer, file-stem suffix regex, folder names) for the non-JS languages, in priority order
    ("middleware", r"(middleware|filter|interceptor|guard|interceptors|middlewares)$",
     {"middleware", "middlewares", "filters", "interceptors", "guards"}),
    ("controller", r"(controller|controllers|handler|handlers|endpoint|endpoints|resource|views|viewset|viewsets|api)$",
     {"controllers", "controller", "handlers", "handler", "endpoints", "views", "resources", "http"}),
    ("route", r"^(routes?|router|routers|urls|routing|web)$", {"routes", "routers", "routing"}),
    ("repository", r"(repository|repositories|repo|repos|dao|daos|store)$",
     {"repositories", "repository", "repos", "repo", "dao", "persistence"}),
    ("model", r"(model|models|entity|entities|record)$", {"models", "model", "entities", "entity"}),
    ("service", r"(service|services|usecase|use_case|usecases|interactor|manager)$",
     {"services", "service", "usecases", "use_cases", "usecase", "application"}),
    ("api-client", r"(client|clients|gateway|sdk)$", {"clients", "client", "gateways"}),
    ("type", r"(dto|dtos|request|response|schema|schemas|types|contract|contracts|interfaces)$",
     {"dto", "dtos", "types", "schemas", "contracts", "interfaces", "requests", "responses"}),
]
RE_OTHER = [(layer, re.compile(rx), dirs) for layer, rx, dirs in RE_OTHER]
OTHER_CONFIG = {"config", "configs", "settings", "conf", "env", "environment", "appsettings", "configuration"}
OTHER_UTIL = {"utils", "util", "lib", "libs", "helpers", "helper", "shared", "common", "core", "pkg", "support",
              "extensions", "tools"}


def _by_pattern_other(path: str, rec: dict, is_entry: bool, orig: str) -> str:
    base = posixpath.basename(path)
    stem = langs.strip_ext(base).lower().replace("-", "_")
    flat = stem.replace("_", "")
    dirs = _dirs(path)
    if langs.is_test(orig):
        return "test"
    if stem in OTHER_CONFIG or dirs & {"config", "configs", "settings", "configuration"} or flat.endswith(("config", "settings")):
        return "config"
    for layer, rx, _names in RE_OTHER:  # the file name first: Http/Requests/StoreUserRequest.php is a type
        if rx.search(stem) or rx.search(flat):
            return layer
    for layer, _rx, names in RE_OTHER:
        if dirs & names:
            return layer
    if is_entry:
        return "entry"
    if dirs & OTHER_UTIL or "util" in stem or "helper" in stem:
        return "util"
    syms = rec.get("symbols", [])
    if syms and {s["kind"] for s in syms} <= {"type"}:
        return "type"
    return "module"


def by_pattern(path: str, rec: dict, is_entry: bool, has_api_calls: bool, pkg_dir: str = "") -> str:
    """Patterns look at the path inside its package, so a package folder named `api` is not a layer."""
    orig = path
    if pkg_dir and path.startswith(pkg_dir + "/"):
        path = path[len(pkg_dir) + 1:]
    lg = langs.of(orig)
    if lg is not None and lg is not langs.JS:
        return _by_pattern_other(path, rec, is_entry, orig)
    low = path.lower()
    base = posixpath.basename(low)
    stem = base.split(".")[0]
    name = posixpath.basename(path).split(".")[0]
    dotted = base.rsplit(".", 1)[0]  # user.controller
    dirs = _dirs(path)
    syms = rec.get("symbols", [])
    kinds = {s["kind"] for s in syms}

    if RE_TEST.search(low):
        return "test"
    if RE_CONFIG.search(base) or dirs & {"config", "configs"} or stem in ("config", "env"):
        return "config"
    if dirs & {"types", "typings", "interfaces", "dto", "dtos", "schemas"} or re.search(r"\.(types?|dto|interface|schema)$", dotted) \
            or (syms and kinds <= {"type"}):
        return "type"
    if dirs & {"middleware", "middlewares", "guards", "interceptors", "pipes"} or re.search(r"(middleware|guard|interceptor|pipe)$", dotted):
        return "middleware"
    if "controller" in dotted or dirs & {"controllers", "handlers"}:
        return "controller"
    if re.search(r"(^|[.-])(routes?|router)$", dotted) or dirs & {"routes", "routers"} and not path.endswith((".tsx", ".jsx")):
        return "route"
    if re.search(r"(repo|repository|dao)$", dotted) or dirs & {"repositories", "repos", "dao"}:
        return "repository"
    if re.search(r"\.(model|entity)$", dotted) or dirs & {"models", "entities", "entity"} or stem in ("prisma", "schema.prisma"):
        return "model"
    if dirs & {"store", "stores", "redux", "slices", "state", "atoms", "contexts", "context"} or re.search(r"(store|slice|context)$", stem):
        return "store"
    if dirs & {"hooks"} or re.match(r"use[A-Z0-9]", name):
        return "hook"
    if has_api_calls and not kinds & {"component"}:
        return "api-client"
    if dirs & {"api", "apis", "clients", "http", "requests", "queries"} and not path.endswith((".tsx", ".jsx")):
        return "api-client"
    if "service" in dotted or dirs & {"services"}:
        return "service"
    if dirs & {"layouts"} or stem.endswith("layout"):
        return "layout"
    if dirs & {"pages", "views", "screens"} and path.endswith((".tsx", ".jsx", ".vue")):
        return "page"
    if is_entry and stem in ENTRY_STEMS:
        return "entry"
    if "component" in kinds or path.endswith((".tsx", ".jsx")):
        return "component"
    if dirs & {"utils", "util", "lib", "libs", "helpers", "helper", "shared", "common", "core"} or "util" in stem or "helper" in stem:
        return "util"
    if is_entry:
        return "entry"
    return "module"


def classify(files: list[str], parsed: dict[str, dict], facts: dict[str, tuple[str, bool]], overrides: dict[str, str],
             entries: set[str], api_call_files: set[str], pkg_dir_of=lambda f: "") -> dict[str, str]:
    out = {}
    for f in files:
        layer = None
        for pattern, ov in overrides.items():
            if fnmatch.fnmatch(f, pattern):
                layer = ov
                break
        if layer is None:
            fact = facts.get(f)
            pat = by_pattern(f, parsed.get(f) or {}, f in entries, f in api_call_files, pkg_dir_of(f))
            if fact and fact[1]:
                layer = fact[0]
            elif fact and pat in GENERIC and not (pat == "entry" and fact[0] == "route"):
                layer = fact[0]
            else:
                layer = pat
        out[f] = layer
    return out
