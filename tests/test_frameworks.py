"""Framework detection: routes, mounts, middlewares, API calls, layers and entries on the fixtures."""

from __future__ import annotations

import pytest

from nimblegraph.frameworks.api_calls import normalize_url, score
from nimblegraph.frameworks.next import matches, segment_path
from conftest import build_fixture, load_graph, needs_node, table

ENGINES = [pytest.param("node", marks=needs_node), "python"]


def routes(cfg) -> dict[str, tuple]:
    g = load_graph(cfg)
    return {f"{r.method} {r.path}": r for r in g.routes.values()}


@pytest.mark.parametrize("eng", ENGINES)
def test_express_routes_mounts_and_middlewares(eng):
    cfg, _ = build_fixture("express-api", eng)
    rs = routes(cfg)
    assert {"GET /api/users", "POST /api/users", "GET /api/users/:id", "DELETE /api/users/:id", "GET /health"} == set(rs)
    post = rs["POST /api/users"]
    assert (post.handler_file, post.handler) == ("src/controllers/user.controller.ts", "userController.create")
    assert post.mounts == [("src/server.ts", "/api"), ("src/routes/index.ts", "/users"), ("src/routes/user.routes.ts", "")]
    # execution order: global (app.use in server.ts) -> router (router.use) -> route -> validation
    assert post.middlewares == [
        (None, "cors|src/server.ts", "global"),
        (None, "express.json|src/server.ts", "global"),
        ("src/middlewares/auth.ts", "requireAuth", "router"),
        ("src/middlewares/validate.ts", "validate(createUserSchema)", "route"),
        ("src/schemas/user.schema.ts", "createUserSchema", "validation"),
    ]
    # `router.route('/').get(...)` chain and the template literal path
    assert rs["GET /api/users"].handler == "userController.list"
    assert rs["DELETE /api/users/:id"].handler == "userController.remove"
    # a CommonJS router that nobody mounts keeps its own path
    assert rs["GET /health"].middlewares == [("src/middlewares/auth.ts", "requireAuth", "route")]


@pytest.mark.parametrize("eng", ENGINES)
def test_nest_routes(eng):
    cfg, _ = build_fixture("nest-api", eng)
    rs = routes(cfg)
    assert set(rs) == {"GET /api/users", "GET /api/users/:id", "POST /api/users"}
    post = rs["POST /api/users"]
    assert post.handler == "UsersController.create"
    assert post.mounts == [("src/main.ts", "/api"), ("src/users/users.module.ts", ""), ("src/users/users.controller.ts", "/users")]
    assert post.middlewares == [
        ("src/logging.interceptor.ts", "LoggingInterceptor", "interceptor"),  # useGlobalInterceptors(new ...)
        ("src/auth/auth.guard.ts", "AuthGuard", "guard"),  # class @UseGuards
        (None, "ValidationPipe|src/users/users.controller.ts", "pipe"),  # method @UsePipes(new ValidationPipe())
        ("src/users/dto/create-user.dto.ts", "CreateUserDto", "validation"),  # @Body() dto: CreateUserDto
    ]


@pytest.mark.parametrize("eng", ENGINES)
def test_next_routes(eng):
    cfg, _ = build_fixture("next-app", eng)
    rs = routes(cfg)
    assert rs["PAGE /users"].file == "app/(dashboard)/users/page.tsx"  # (group) removed
    assert rs["PAGE /users/:id"].file == "app/(dashboard)/users/[id]/page.tsx"
    assert rs["GET /api/users"].handler == "GET" and rs["POST /api/users"].handler == "POST"
    assert rs["ALL /api/health"].file == "pages/api/health.ts"
    assert rs["PAGE /"].kind == "layout"
    assert "pages/_app.tsx" not in {r.file for r in rs.values()}
    # middleware.ts matcher: /api/:path* and /users/:path*, not the root layout
    assert rs["GET /api/users"].middlewares == [("middleware.ts", "middleware", "next")]
    assert rs["PAGE /users/:id"].middlewares == [("middleware.ts", "middleware", "next")]
    assert rs["PAGE /"].middlewares == []


@pytest.mark.parametrize("eng", ENGINES)
def test_react_router_and_api_calls(eng):
    cfg, _ = build_fixture("vite-react", eng)
    g = load_graph(cfg)
    pages = {(r.path, r.kind): r for r in g.routes.values()}
    assert pages[("/", "layout")].handler_file == "src/components/Layout.tsx"
    assert pages[("/users", "page")].handler_file == "src/pages/UsersPage.tsx"
    assert pages[("/settings", "page")].handler_file == "src/pages/SettingsPage.tsx"  # lazy(() => import())
    assert len(g.routes) == 3  # the JSX tree and the createBrowserRouter tree are the same routes
    calls = {(c.file, c.method, c.url) for c in g.api_calls}
    assert ("src/hooks/useUsers.ts", "GET", "/api/users") in calls  # useSWR + fetch
    assert ("src/hooks/useUsers.ts", "GET", "/users/:param") in calls  # api.get(`/users/${id}`), axios.create instance
    assert ("src/api/client.ts", "POST", "/users") in calls


@pytest.mark.parametrize("eng", ENGINES)
def test_front_to_back_link(eng):
    cfg, _ = build_fixture("next-app", eng)
    g = load_graph(cfg)
    call = next(c for c in g.api_calls if c.file == "components/UserTable.tsx")
    assert g.routes[call.route_id].label == "GET /api/users" and call.symbol == "UserTable"
    assert ("components/UserTable.tsx", "app/api/users/route.ts", "calls_api", "GET /api/users", None) in set(
        table(cfg, "SELECT src, dst, kind, names, via FROM edges"))


def test_layers_and_entries():
    cfg, _ = build_fixture("express-api", "python")
    layers = dict(table(cfg, "SELECT path, layer FROM files"))
    assert layers["src/controllers/user.controller.ts"] == "controller"
    assert layers["src/routes/user.routes.ts"] == "route"
    assert layers["src/services/user.service.ts"] == "service"
    assert layers["src/repositories/user.repo.ts"] == "repository"
    assert layers["src/middlewares/validate.ts"] == "middleware"
    assert layers["tests/user.e2e.spec.ts"] == "test"
    assert layers["src/server.ts"] == "entry"
    entries = {p for (p,) in table(cfg, "SELECT path FROM files WHERE is_entry = 1")}
    assert entries == {"src/server.ts"}

    cfg, _ = build_fixture("nest-api", "python")
    layers = dict(table(cfg, "SELECT path, layer FROM files"))
    assert layers["src/users/users.controller.ts"] == "controller"  # @Controller
    assert layers["src/users/users.service.ts"] == "service"  # @Injectable *Service
    assert layers["src/auth/auth.guard.ts"] == "middleware"
    assert layers["src/users/users.module.ts"] == "module"

    cfg, _ = build_fixture("vite-react", "python")
    layers = dict(table(cfg, "SELECT path, layer FROM files"))
    assert layers["src/hooks/useUsers.ts"] == "hook"
    assert layers["src/api/client.ts"] == "api-client"
    assert layers["src/pages/UsersPage.tsx"] == "page"
    entries = {p for (p,) in table(cfg, "SELECT path FROM files WHERE is_entry = 1")}
    assert "src/main.tsx" in entries  # <script src="/src/main.tsx"> in index.html

    cfg, _ = build_fixture("monorepo", "python")
    layers = dict(table(cfg, "SELECT path, layer FROM files"))
    assert layers["apps/api/src/db.ts"] != "api-client"  # the package folder `api` is not a layer


def test_url_normalization_and_matching():
    assert normalize_url("https://x.com/api/users?page=2") == "/api/users"
    assert normalize_url(":param/users") == "/users"
    assert normalize_url("users/:param/") == "/users/:param"
    assert score("/api/users/:param", "/api/users/:id")[0] == 3
    assert score("/users/42", "/api/users/:id")[0] == 2  # baseURL /api omitted by the caller
    assert score("/api/users", "/users")[0] == 1
    assert score("/api/orders", "/api/users")[0] == 0


def test_next_helpers():
    assert segment_path(["(shop)", "@modal", "products", "[id]"]) == "/products/:id"
    assert segment_path(["docs", "[...slug]"]) == "/docs/*slug"
    assert segment_path(["_private", "x"]) is None
    assert matches("/api/users/:id", ["/api/:path*"])
    assert not matches("/", ["/api/:path*"])


@pytest.mark.parametrize("eng", ENGINES)
def test_tanstack_router_file_routes(eng):
    cfg, _ = build_fixture("tanstack-app", eng)
    g = load_graph(cfg)
    rs: dict[tuple, list[str]] = {}
    for r in g.routes.values():
        rs.setdefault((r.path, r.kind), []).append(r.file)
    assert sorted(rs[("/", "layout")]) == ["src/routes/__root.tsx", "src/routes/_app.tsx"]  # root + pathless layout
    assert rs[("/policies", "page")] == ["src/routes/_app/policies/index.tsx"]  # _app dropped, index route
    assert rs[("/policies/:policyId", "page")] == ["src/routes/_app/policies/$policyId.tsx"]
    layers = dict(table(cfg, "SELECT path, layer FROM files"))
    assert layers["src/routes/_app/policies/index.tsx"] == "page"
    # a source folder named `coverage` is indexed (only <package>/coverage is test output)
    assert "src/products/coverage/coverage.service.ts" in layers
