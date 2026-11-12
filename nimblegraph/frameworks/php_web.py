"""PHP: Laravel route files (`Route::get('/x', [Ctrl::class, 'm'])`, 'Ctrl@m', resource/apiResource, prefix
groups by chain or array, routes/api.php under /api) and Symfony `#[Route]` attributes. Base classes give layer
facts (Controller, Model, FormRequest, Middleware, Job...)."""

from __future__ import annotations

from nimblegraph.frameworks import (Context, Route, arg_str, class_file, colon_params, enclosing, join_paths, prop,
                                  strs)

VERBS = {"get": ["GET"], "post": ["POST"], "put": ["PUT"], "patch": ["PATCH"], "delete": ["DELETE"],
         "options": ["OPTIONS"], "any": ["ALL"], "match": None}
RESOURCE = [("index", "GET", ""), ("create", "GET", "/create"), ("store", "POST", ""), ("show", "GET", "/:{p}"),
            ("edit", "GET", "/:{p}/edit"), ("update", "PUT", "/:{p}"), ("update", "PATCH", "/:{p}"),
            ("destroy", "DELETE", "/:{p}")]
API_ACTIONS = {"index", "store", "show", "update", "destroy"}
BASE_LAYERS = {"Controller": "controller", "AbstractController": "controller", "Model": "model",
               "Authenticatable": "model", "FormRequest": "type", "JsonResource": "type", "Job": "service",
               "ServiceProvider": "config", "Middleware": "middleware", "Command": "entry", "Migration": "model",
               "Seeder": "model", "Factory": "model", "Mailable": "service", "Notification": "service"}


def _singular(name: str) -> str:
    base = name.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
    if base.endswith("ies"):
        return base[:-3] + "y"
    return base[:-1] if base.endswith("s") and not base.endswith("ss") else base


def _handler(ctx: Context, f: str, a: dict | None) -> tuple[str, str | None]:
    if not a:
        return f, None
    if a.get("t") == "arr" and a.get("items"):  # [UserController::class, 'show']
        items = a["items"]
        cls = items[0].get("v") if items[0].get("t") == "id" else None
        meth = arg_str(items[1]) if len(items) > 1 else None
        if cls:
            hit = ctx.resolve_local(f, cls)
            cf = hit[0] if hit else class_file(ctx, cls, f)
            return (cf or f), (f"{cls}.{meth}" if meth else cls)
    if a.get("t") == "id":  # invokable controller: Route::get('/x', ShowUser::class)
        hit = ctx.resolve_local(f, a["v"])
        cf = hit[0] if hit else class_file(ctx, a["v"], f)
        return (cf or f), a["v"]
    s = arg_str(a)
    if s and "@" in s:  # 'UserController@show'
        cls, meth = s.split("@", 1)
        cls = cls.rsplit("\\", 1)[-1]
        return (class_file(ctx, cls, f) or f), f"{cls}.{meth}"
    if s and s[:1].isupper():  # Route::resource('articles', 'ArticleController') (Laravel <= 7)
        cls = s.rsplit("\\", 1)[-1]
        return (class_file(ctx, cls, f) or f), cls
    return f, None


def _group_prefix(c: dict) -> str | None:
    """Prefix of a Route::prefix('x')->group(fn) / Route::group(['prefix' => 'x'], fn) call."""
    if c.get("prop") != "group" or not c.get("args") or c["args"][-1].get("t") != "fn":
        return None
    pre = ""
    for link in c.get("chain") or []:
        if link.get("prop") == "prefix":
            pre = join_paths(pre, arg_str((link.get("args") or [None])[0]) or "")
    if c.get("obj") == "Route" and c["args"][0].get("t") == "obj":
        pre = join_paths(pre, arg_str(prop(c["args"][0], "prefix")) or "")
    return pre


def detect(ctx: Context) -> None:
    for f, rec in sorted(ctx.parsed.items()):
        for s in rec.get("symbols", []):
            if s.get("extends") in BASE_LAYERS:
                ctx.fact(f, BASE_LAYERS[s["extends"]], strong=False)
        _symfony(ctx, f, rec)
        _laravel(ctx, f, rec)


def _laravel(ctx: Context, f: str, rec: dict) -> None:
    calls = rec.get("calls", [])
    if not any(c.get("obj") == "Route" or (c.get("chain") or [{}])[0].get("obj") == "Route" for c in calls):
        return
    base = "/api" if f.endswith("routes/api.php") else ""
    blocks = []
    for c in calls:
        pre = _group_prefix(c)
        if pre is not None:
            fn = c["args"][-1]
            blocks.append((fn["line"], fn["end"], pre))
    found = False
    for c in calls:
        chain = c.get("chain") or []
        via_route = c.get("obj") == "Route" or (chain and chain[0].get("obj") == "Route")
        verb = c.get("prop") or ""
        if not via_route or not c.get("args"):
            continue
        chain_pre = "".join(join_paths(arg_str((l.get("args") or [None])[0]) or "") for l in chain
                            if l.get("prop") == "prefix")
        outer = join_paths(base, *enclosing(blocks, c.get("line", 0)), chain_pre)
        path = arg_str(c["args"][0])
        if verb in ("resource", "apiResource") and path is not None:
            hf, ctrl = _handler(ctx, f, c["args"][1] if len(c["args"]) > 1 else None)
            p = _singular(path)
            opts = c["args"][2] if len(c["args"]) > 2 and c["args"][2].get("t") == "obj" else None
            only = set(strs(prop(opts, "only")))
            except_ = set(strs(prop(opts, "except")))
            for action, method, sub in RESOURCE:
                if verb == "apiResource" and action not in API_ACTIONS or (only and action not in only) \
                        or action in except_:
                    continue
                ctx.routes.append(Route(method, colon_params(join_paths(outer, path.replace(".", "/")) + sub.format(p=p)),
                                        f, c.get("line", 0), hf, f"{ctrl}.{action}" if ctrl else None, "api", "laravel"))
            found = True
            continue
        if verb not in VERBS or path is None:
            continue
        methods = VERBS[verb] or [m.upper() for m in strs(c["args"][0])]
        if verb == "match":  # Route::match(['get', 'post'], '/x', ...)
            path = arg_str(c["args"][1]) if len(c["args"]) > 1 else None
            if path is None:
                continue
        hf, h = _handler(ctx, f, c["args"][-1] if len(c["args"]) > 1 else None)
        for m in methods:
            ctx.routes.append(Route(m, colon_params(join_paths(outer, path)), f, c.get("line", 0), hf, h, "api",
                                    "laravel"))
        found = True
        if hf != f:
            ctx.fact(hf, "controller")
    if found:
        ctx.fact(f, "route")


def _symfony(ctx: Context, f: str, rec: dict) -> None:
    for s in rec.get("symbols", []):
        decos = {d["name"]: d for d in s.get("decorators") or []}
        prefix = ""
        if "Route" in decos:
            prefix = arg_str((decos["Route"].get("args") or [None])[0]) or arg_str(
                prop((decos["Route"].get("args") or [None])[-1], "path")) or ""
        hit = False
        for m in s.get("methods", []) or []:
            for d in m.get("decorators") or []:
                if d["name"] not in ("Route", "Get", "Post", "Put", "Patch", "Delete"):
                    continue
                args = d.get("args") or []
                path = arg_str(args[0]) if args else None
                path = path if path is not None else arg_str(prop(args[-1] if args else None, "path")) or ""
                methods = [x.upper() for x in strs(prop(args[-1] if args else None, "methods"))] or \
                    ([d["name"].upper()] if d["name"] != "Route" else ["ALL"])
                for verb in methods:
                    ctx.routes.append(Route(verb, colon_params(join_paths(prefix, path)), f, m.get("line", s["line"]), f,
                                            f"{s['name']}.{m['name']}", "api", "symfony"))
                hit = True
        if hit:
            ctx.fact(f, "controller")
