"""Spring MVC/Boot and JAX-RS (Java, Kotlin): @RestController + @RequestMapping prefixes, @GetMapping & co.,
@Path + @GET; stereotype annotations (@Service, @Repository, @Entity...) become layer facts; request-body DTOs
read as validation steps."""

from __future__ import annotations

from nimblegraph.frameworks import Context, Route, colon_params, join_paths, prop, strs

MAPPINGS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
            "PatchMapping": "PATCH", "RequestMapping": None}
JAXRS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
CONTROLLERS = {"RestController", "Controller", "Path"}
STEREOTYPES = {"Service": "service", "Repository": "repository", "Entity": "model", "Document": "model",
               "Table": "model", "Embeddable": "model", "Configuration": "config", "ConfigurationProperties": "config",
               "ControllerAdvice": "middleware", "RestControllerAdvice": "middleware", "Component": "module",
               "Mapper": "repository", "FeignClient": "api-client"}
REPO_BASES = ("Repository", "JpaRepository", "CrudRepository", "PagingAndSortingRepository", "MongoRepository",
              "ReactiveCrudRepository", "CoroutineCrudRepository", "JpaSpecificationExecutor")
BODY_ANNOTATIONS = {"RequestBody", "Valid", "Validated"}


def _paths(d: dict) -> list[str]:
    args = d.get("args") or []
    out: list[str] = []
    for a in args:
        if a.get("t") == "obj":
            out += strs(prop(a, "value", "path"))
        else:
            out += strs(a)
    return out or [""]


def _method_of(d: dict) -> list[str]:
    fixed = MAPPINGS.get(d["name"])
    if fixed:
        return [fixed]
    for a in d.get("args") or []:
        m = prop(a, "method")
        if m:
            items = m.get("items", [m]) if m.get("t") == "arr" else [m]
            out = [i["v"].rsplit(".", 1)[-1].upper() for i in items if i.get("t") in ("member", "id")]
            if out:
                return out
    return ["ALL"]


def detect(ctx: Context) -> None:
    for f, rec in sorted(ctx.parsed.items()):
        for s in rec.get("symbols", []):
            decos = {d["name"]: d for d in s.get("decorators") or []}
            for name, layer in STEREOTYPES.items():
                if name in decos:
                    ctx.fact(f, layer)
            if s.get("kind") == "type" and any(b in REPO_BASES for b in s.get("implements") or []):
                ctx.fact(f, "repository")
            is_ctrl = bool(CONTROLLERS & set(decos)) or ("RequestMapping" in decos and s.get("kind") == "class")
            if not is_ctrl:
                continue
            ctx.fact(f, "controller")
            prefixes = _paths(decos["RequestMapping"]) if "RequestMapping" in decos else \
                _paths(decos["Path"]) if "Path" in decos else [""]
            for m in s.get("methods", []) or []:
                mdecos = {d["name"]: d for d in m.get("decorators") or []}
                verbs: list[str] = []
                paths = [""]
                hit = next((d for n, d in mdecos.items() if n in MAPPINGS), None)
                if hit is not None:
                    verbs, paths = _method_of(hit), _paths(hit)
                elif JAXRS & set(mdecos):
                    verbs = sorted(JAXRS & set(mdecos))
                    paths = _paths(mdecos["Path"]) if "Path" in mdecos else [""]
                if not verbs:
                    continue
                mws = []
                for t in m.get("param_types") or []:
                    tgt = ctx.resolve_local(f, t)
                    if tgt and tgt[0] != f and (t.endswith(("Dto", "DTO", "Request", "Form", "Command", "Input"))):
                        mws.append((tgt[0], t, "validation"))
                for pre in prefixes:
                    for p in paths:
                        for v in verbs:
                            ctx.routes.append(Route(v, colon_params(join_paths(pre, p)), f, m.get("line", s["line"]),
                                                    f, f"{s['name']}.{m['name']}", "api", "spring",
                                                    middlewares=list(mws)))
