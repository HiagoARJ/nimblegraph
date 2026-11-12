"""Ruby: the Rails routes DSL (config/routes.rb: resources/resource with only/except and nested blocks,
namespace/scope, member/collection, get 'x', to: 'ctrl#action', root) and Sinatra (`get '/x' do ... end`).
Rails base classes give layer facts (ApplicationController, ApplicationRecord, ApplicationJob...)."""

from __future__ import annotations

from nimblegraph.frameworks import Context, Route, arg_str, class_file, colon_params, join_paths, prop, strs

VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "match": "ALL"}
RESOURCES = [("index", "GET", ""), ("new", "GET", "/new"), ("create", "POST", ""), ("show", "GET", "/:id"),
             ("edit", "GET", "/:id/edit"), ("update", "PATCH", "/:id"), ("update", "PUT", "/:id"),
             ("destroy", "DELETE", "/:id")]
SINGULAR = [("new", "GET", "/new"), ("create", "POST", ""), ("show", "GET", ""), ("edit", "GET", "/edit"),
            ("update", "PATCH", ""), ("update", "PUT", ""), ("destroy", "DELETE", "")]
BASE_LAYERS = {"ApplicationController": "controller", "ActionController::Base": "controller",
               "ActionController::API": "controller", "ApplicationRecord": "model", "ActiveRecord::Base": "model",
               "ApplicationJob": "service", "ActiveJob::Base": "service", "ApplicationMailer": "service",
               "ActionMailer::Base": "service", "ApplicationCable::Channel": "controller", "Grape::API": "route",
               "Sinatra::Base": "route", "ActiveRecord::Migration": "model"}


def _camel(s: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in s.replace("-", "_").split("_"))


def _singular(s: str) -> str:
    return s[:-3] + "y" if s.endswith("ies") else s[:-1] if s.endswith("s") and not s.endswith("ss") else s


def detect(ctx: Context) -> None:
    for f, rec in sorted(ctx.parsed.items()):
        for s in rec.get("symbols", []):
            ext = s.get("extends") or ""
            if ext in BASE_LAYERS or ext.startswith("ActiveRecord::Migration"):
                ctx.fact(f, BASE_LAYERS.get(ext, "model"), strong=False)
        if f.endswith("config/routes.rb") or f.startswith("config/routes/"):
            _rails(ctx, f, rec)
        elif "sinatra" in ctx.externals.get(f, set()) or "sinatra" in ctx.deps(f):
            _sinatra(ctx, f, rec)


DEVISE = {  # module -> (controller, [(action, verb, sub-path)])
    "authenticatable": ("sessions", [("new", "GET", "/sign_in"), ("create", "POST", "/sign_in"),
                                     ("destroy", "DELETE", "/sign_out")]),
    "recoverable": ("passwords", [("new", "GET", "/password/new"), ("edit", "GET", "/password/edit"),
                                  ("update", "PATCH", "/password"), ("update", "PUT", "/password"),
                                  ("create", "POST", "/password")]),
    "registerable": ("registrations", [("cancel", "GET", "/cancel"), ("new", "GET", "/sign_up"),
                                       ("edit", "GET", "/edit"), ("update", "PATCH", ""), ("update", "PUT", ""),
                                       ("destroy", "DELETE", ""), ("create", "POST", "")]),
    "confirmable": ("confirmations", [("new", "GET", "/confirmation/new"), ("show", "GET", "/confirmation"),
                                      ("create", "POST", "/confirmation")]),
    "lockable": ("unlocks", [("new", "GET", "/unlock/new"), ("show", "GET", "/unlock"), ("create", "POST", "/unlock")]),
}


def _devise_modules(ctx: Context, model: str) -> set[str]:
    """The `devise :database_authenticatable, :registerable, ...` declared in the model (User for :users)."""
    mf = class_file(ctx, model)
    out: set[str] = set()
    for c in ctx.rec(mf).get("calls", []) if mf else []:
        if c.get("callee") == "devise":
            for a in c.get("args") or []:
                if a.get("t") == "str":
                    out.add("authenticatable" if a["v"].endswith("authenticatable") else a["v"])
    return out or {"authenticatable", "registerable", "recoverable"}


def _devise(ctx: Context, f: str, name: str, opts: dict | None, prefix: str, modules: list[str], line: int) -> None:
    """devise_for :users -> the routes of the Devise modules the model enables, under path: (default the name)."""
    base = join_paths(prefix, arg_str(prop(opts, "path")) or name)
    custom = (prop(opts, "controllers") or {}).get("props", {})
    skip = set(strs(prop(opts, "skip")))
    for module in sorted(_devise_modules(ctx, _camel(_singular(name)))):
        if module not in DEVISE:
            continue
        ctrl, table = DEVISE[module]
        if ctrl in skip:
            continue
        target = arg_str(custom.get(ctrl)) if custom.get(ctrl) else None
        if target:  # controllers: { sessions: 'auth/sessions' }
            mods, cname = target.split("/")[:-1], target.split("/")[-1]
        elif class_file(ctx, "::".join([*(_camel(m) for m in modules), _camel(ctrl) + "Controller"])):
            mods, cname = modules, ctrl
        else:
            mods, cname = ["devise"], ctrl
        for action, verb, sub in table:
            hf, h = _ctrl(ctx, f, mods, cname, action)
            ctx.routes.append(Route(verb, join_paths(base) + sub if sub else join_paths(base), f, line, hf, h, "api",
                                    "devise"))


def _scoped(base: str, res: str, res_kind: str | None, kind: str) -> str:
    """member routes of `resources :users` live under /users/:id, collection ones under /users."""
    return join_paths(base, res, ":id") if kind == "member" and res_kind == "resources" else join_paths(base, res)


def _ctrl(ctx: Context, f: str, modules: list[str], name: str, action: str | None) -> tuple[str, str]:
    qual = "::".join([*(_camel(m) for m in modules), _camel(name) + "Controller"])
    cf = class_file(ctx, qual, f)
    return cf or f, f"{qual.rsplit('::', 1)[-1]}.{action}" if action else qual.rsplit("::", 1)[-1]


def _rails(ctx: Context, f: str, rec: dict) -> None:
    calls = [c for c in rec.get("calls", []) if c.get("callee")]
    # blocks: (start, end, path prefix, module, kind, resource name)
    blocks: list[tuple[int, int, str, str | None, str, str | None]] = []
    for c in calls:
        args = c.get("args") or []
        fn = next((a for a in args if a.get("t") == "fn"), None)
        if fn is None or not fn.get("line"):
            continue
        name = arg_str(args[0]) if args and args[0].get("t") == "str" else None
        opts = next((a for a in args if a.get("t") == "obj"), None)
        k = c["callee"]
        if k == "namespace" and name:
            blocks.append((fn["line"], fn["end"], "/" + name, name, "ns", None))
        elif k == "scope":
            path = name or arg_str(prop(opts, "path")) or ""
            blocks.append((fn["line"], fn["end"], path, arg_str(prop(opts, "module")), "ns", None))
        elif k in ("resources", "resource") and name:
            member = f"/{name}/:{_singular(name)}_id" if k == "resources" else f"/{name}"
            blocks.append((fn["line"], fn["end"], member, None, k, name))
        elif k in ("member", "collection"):
            blocks.append((fn["line"], fn["end"], "", None, k, None))

    def context(line: int):
        """(path prefix, controller modules, enclosing resource, its base path, its kind) at a line."""
        inside = sorted((b for b in blocks if b[0] < line <= b[1]), key=lambda b: (b[0], -b[1]))
        prefix, modules, res, res_base, res_kind = "", [], None, "", None
        for _s, _e, p, mod, kind, rname in inside:
            if kind in ("member", "collection") and res is not None:
                prefix = _scoped(res_base, res, res_kind, kind)
                continue
            if rname:
                res, res_base, res_kind = rname, prefix, kind
            prefix = join_paths(prefix, p)
            if mod:
                modules.append(mod)
        return prefix, modules, res, res_base, res_kind

    for c in calls:
        k, args = c["callee"], c.get("args") or []
        line = c.get("line", 0)
        prefix, modules, res, res_base, res_kind = context(line)
        opts = next((a for a in args if a.get("t") == "obj"), None)
        on = arg_str(prop(opts, "on"))
        if res is not None and on in ("member", "collection"):  # get :stats, on: :member
            prefix = _scoped(res_base, res, res_kind, on)
        if k in ("resources", "resource") and args and args[0].get("t") == "str":
            name = args[0]["v"]
            table = RESOURCES if k == "resources" else SINGULAR
            only = set(strs(prop(opts, "only"))) or None
            except_ = set(strs(prop(opts, "except")))
            ctrl = arg_str(prop(opts, "controller")) or (name if k == "resources" or name.endswith("s") else name + "s")
            for action, verb, sub in table:
                if (only and action not in only) or action in except_:
                    continue
                hf, h = _ctrl(ctx, f, modules, ctrl, action)
                ctx.routes.append(Route(verb, join_paths(prefix, name) + sub if sub else join_paths(prefix, name),
                                        f, line, hf, h, "api", "rails"))
            continue
        if k == "devise_for" and args and args[0].get("t") == "str":
            _devise(ctx, f, args[0]["v"], opts, prefix, modules, line)
            continue
        if k == "root":
            to = arg_str(args[0]) if args and args[0].get("t") == "str" else arg_str(prop(opts, "to"))
            if to and "#" in to:
                hf, h = _ctrl(ctx, f, modules, *to.split("#", 1))
                ctx.routes.append(Route("GET", join_paths(prefix), f, line, hf, h, "api", "rails"))
            continue
        if k not in VERBS or not args or args[0].get("t") != "str":
            continue
        path = args[0]["v"]
        to = arg_str(prop(opts, "to"))
        if to is None and "#" in path or (" => " in path):  # get 'photos/search' => 'photos#search'
            to = path
        if to and "#" in to:
            ctrl, action = to.split("#", 1)
        elif res is not None:  # get :stats inside a resource / member block
            ctrl, action = res, path.strip("/").split("/")[-1]
        else:
            seg = path.strip("/").split("/")
            ctrl, action = (seg[0], seg[1] if len(seg) > 1 else "index")
        hf, h = _ctrl(ctx, f, modules, ctrl, action)
        verbs = [VERBS[k]] if k != "match" else [v.upper() for v in strs(prop(opts, "via"))] or ["ALL"]
        for v in verbs:
            ctx.routes.append(Route(v, colon_params(join_paths(prefix, path)), f, line, hf, h, "api", "rails"))
    ctx.fact(f, "route")


def _sinatra(ctx: Context, f: str, rec: dict) -> None:
    found = False
    for c in rec.get("calls", []):
        verb = VERBS.get(c.get("callee") or "")
        args = c.get("args") or []
        if verb and args and args[0].get("t") == "str" and args[0]["v"].startswith("/") and any(a.get("t") == "fn" for a in args):
            ctx.routes.append(Route(verb, colon_params(args[0]["v"]), f, c.get("line", 0), f, c.get("sym"), "api", "sinatra"))
            found = True
    if found:
        ctx.fact(f, "route")
        ctx.entries.add(f)
