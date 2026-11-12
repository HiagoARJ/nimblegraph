"""Non-JS languages (tree-sitter extractors, resolution, routes, layers) on small fixtures per framework."""

from __future__ import annotations

import pytest

from nimblegraph import lang as langs
from nimblegraph.lang import base
from tests.conftest import build_fixture, edges_of, load_graph, table

pytestmark = pytest.mark.skipif(base.parser("python") is None, reason="tree-sitter grammars not available")


def routes_of(cfg) -> set[tuple]:
    return set(table(cfg, "SELECT method, path, handler_file, handler FROM routes"))


def layers_of(cfg) -> dict[str, str]:
    return dict(table(cfg, "SELECT path, layer FROM files"))


def simple_edges(cfg) -> set[tuple[str, str]]:
    return {(s, d) for s, d, *_ in edges_of(cfg)}


# ---------------------------------------------------------------------- registry
def test_registry_extensions_and_tests():
    assert langs.name_of("a/b.go") == "go" and langs.name_of("X.cs") == "csharp" and langs.name_of("a.tsx") == "javascript"
    assert langs.strip_ext("user.service.ts") == "user.service" and langs.strip_ext("UserService.java") == "UserService"
    assert langs.is_test("internal/users/service_test.go")
    assert langs.is_test("src/test/java/a/UserTest.java") and not langs.is_test("src/main/java/a/Latest.java")
    assert langs.is_test("tests/test_users.py") and not langs.is_test("app/contest.py")
    assert langs.is_test("Shop.Api.Tests/UsersControllerTests.cs")
    assert {lg.name for lg in langs.manifest_langs("pom.xml")} == {"java", "kotlin"}
    assert [lg.name for lg in langs.manifest_langs("Shop.Api.csproj")] == ["csharp"]


# ---------------------------------------------------------------------- Python
def test_python_fastapi_graph():
    cfg, res = build_fixture("python-fastapi")
    assert "tree-sitter: Python" in res.engine and res.unresolved == 0
    e = simple_edges(cfg)
    assert ("app/main.py", "app/routers/users.py") in e  # from app.routers import users (submodule)
    assert ("app/routers/users.py", "app/services/user_service.py") in e  # relative import
    assert ("app/services/user_service.py", "app/repositories/user_repo.py") in e
    assert ("app/repositories/user_repo.py", "app/models.py") in e
    assert routes_of(cfg) == {
        ("GET", "/api/users", "app/routers/users.py", "list_users"),
        ("POST", "/api/users", "app/routers/users.py", "create_user"),
        ("GET", "/api/users/:user_id", "app/routers/users.py", "get_user"),
        ("GET", "/api/orders/:order_id", "app/routers/orders.py", "get_order"),
    }
    lay = layers_of(cfg)
    assert lay["app/services/user_service.py"] == "service" and lay["app/repositories/user_repo.py"] == "repository"
    assert lay["tests/test_users.py"] == "test" and lay["app/models.py"] == "model"
    assert set(table(cfg, "SELECT DISTINCT pkg FROM externals")) == {("fastapi",), ("pydantic",)}
    refs = set(table(cfg, "SELECT symbol, ref_file, ref_symbol FROM symbol_refs"))
    assert ("UserService.get", "app/repositories/user_repo.py", "UserRepository.find") in refs  # self.repo.find


def test_python_django_urls():
    cfg, _ = build_fixture("python-django")
    assert routes_of(cfg) == {
        ("ALL", "/api/posts", "blog/views.py", "post_list"),
        ("ALL", "/api/posts/:pk", "blog/views.py", "PostDetail"),
    }
    assert layers_of(cfg)["blog/views.py"] == "controller"
    assert ("blog/urls.py", "blog/views.py") in simple_edges(cfg)  # from . import views


def test_python_flask_blueprints(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")
    (tmp_path / "app.py").write_text(
        "from flask import Flask\nfrom views.users import bp\n\napp = Flask(__name__)\n"
        "app.register_blueprint(bp, url_prefix='/api')\n")
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "__init__.py").write_text("")
    (tmp_path / "views" / "users.py").write_text(
        "from flask import Blueprint\n\nbp = Blueprint('users', __name__, url_prefix='/users')\n\n"
        "@bp.route('/<int:uid>', methods=['GET', 'DELETE'])\ndef user(uid):\n    return ''\n\n"
        "@bp.post('/')\ndef create():\n    return ''\n")
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes_of(cfg) == {
        ("GET", "/api/users/:uid", "views/users.py", "user"),
        ("DELETE", "/api/users/:uid", "views/users.py", "user"),
        ("POST", "/api/users", "views/users.py", "create"),
    }


# ---------------------------------------------------------------------- Go
def test_go_gin_graph():
    cfg, res = build_fixture("go-gin")
    assert "Go" in res.engine and res.unresolved == 0
    files = set(layers_of(cfg))
    assert not any(f.startswith("vendor/") for f in files)  # vendor/ at the go.mod root is skipped
    e = simple_edges(cfg)
    assert ("cmd/api/main.go", "internal/handlers/user_handler.go") in e
    assert ("internal/handlers/user_handler.go", "internal/users/service.go") in e  # users.Service
    assert ("internal/users/service.go", "internal/users/repo.go") in e  # same package, no import
    assert routes_of(cfg) == {
        ("GET", "/api/users", "internal/handlers/user_handler.go", "UserHandler.List"),
        ("POST", "/api/users", "internal/handlers/user_handler.go", "UserHandler.Create"),
        ("GET", "/api/users/:id", "internal/handlers/user_handler.go", "UserHandler.Get"),
        ("GET", "/health", "cmd/api/main.go", "health"),  # Go 1.22 "GET /health" pattern
    }
    lay = layers_of(cfg)
    assert lay["internal/handlers/user_handler.go"] == "controller" and lay["internal/users/service_test.go"] == "test"
    assert lay["cmd/api/main.go"] == "entry"
    refs = set(table(cfg, "SELECT symbol, ref_file, ref_symbol FROM symbol_refs"))
    assert ("UserHandler.Get", "internal/users/service.go", "Service.Get") in refs  # h.svc.Get through the field type
    assert ("github.com/gin-gonic/gin",) in set(table(cfg, "SELECT pkg FROM externals"))


def test_go_chi_closures(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/svc\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text(
        'package main\n\nimport "github.com/go-chi/chi/v5"\n\nfunc main() {\n\tr := chi.NewRouter()\n'
        '\tr.Route("/api", func(r chi.Router) {\n\t\tr.Get("/items", listItems)\n'
        '\t\tr.Route("/admin", func(r chi.Router) {\n\t\t\tr.Delete("/items/{id}", deleteItem)\n\t\t})\n\t})\n'
        '\tr.Get("/", home)\n}\n\nfunc listItems() {}\nfunc deleteItem() {}\nfunc home() {}\n')
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes_of(cfg) == {
        ("GET", "/api/items", "main.go", "listItems"),
        ("DELETE", "/api/admin/items/:id", "main.go", "deleteItem"),
        ("GET", "/", "main.go", "home"),
    }


def test_go_groups_passed_to_functions(tmp_path):
    """Gin RealWorld style: users.Register(v1.Group("/users")) -> routes declared on the parameter."""
    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text(
        'package main\n\nimport (\n\t"github.com/gin-gonic/gin"\n\t"example.com/app/users"\n)\n\n'
        'func main() {\n\tr := gin.Default()\n\tv1 := r.Group("/api")\n\tusers.Register(v1.Group("/users"))\n}\n')
    (tmp_path / "users").mkdir()
    (tmp_path / "users" / "routers.go").write_text(
        'package users\n\nimport "github.com/gin-gonic/gin"\n\n'
        'func Register(router *gin.RouterGroup) {\n\trouter.POST("/login", Login)\n\trouter.GET("/:id", Get)\n}\n\n'
        'func Login(c *gin.Context) {}\nfunc Get(c *gin.Context) {}\n')
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes_of(cfg) == {("POST", "/api/users/login", "users/routers.go", "Login"),
                              ("GET", "/api/users/:id", "users/routers.go", "Get")}


def test_route_parameter_forms():
    from nimblegraph.frameworks import colon_params
    assert colon_params(r"^articles/(?P<slug>[-\w-]+)/comments/(?P<pk>[\d]+)/?$") == "articles/:slug/comments/:pk"
    assert colon_params("/users/{id:int}") == "/users/:id" and colon_params("<int:pk>/") == ":pk/"
    assert colon_params("/files/{*path}") == "/files/:path"


# ---------------------------------------------------------------------- Java
J = "src/main/java/com/acme/shop/users/"


def test_java_spring_graph():
    cfg, res = build_fixture("java-spring")
    assert "Java" in res.engine and res.unresolved == 0
    files = set(layers_of(cfg))
    assert not any(f.startswith("target/") for f in files)  # target/ at the pom.xml root is skipped
    e = simple_edges(cfg)
    assert (J + "UserController.java", J + "UserService.java") in e  # same package, no import
    assert (J + "UserController.java", J + "dto/CreateUserRequest.java") in e  # explicit import
    assert (J + "UserService.java", J + "UserRepository.java") in e
    assert ("src/test/java/com/acme/shop/users/UserControllerTest.java", J + "UserController.java") in e
    assert routes_of(cfg) == {
        ("GET", "/api/users", J + "UserController.java", "UserController.list"),
        ("GET", "/api/users/:id", J + "UserController.java", "UserController.get"),
        ("POST", "/api/users", J + "UserController.java", "UserController.create"),
        ("DELETE", "/api/users/:id", J + "UserController.java", "UserController.delete"),
    }
    mws = set(table(cfg, "SELECT r.method, m.file, m.symbol, m.scope FROM route_middlewares m JOIN routes r ON r.id = m.route_id"))
    assert ("POST", J + "dto/CreateUserRequest.java", "CreateUserRequest", "validation") in mws
    lay = layers_of(cfg)
    assert lay[J + "UserService.java"] == "service" and lay[J + "UserRepository.java"] == "repository"
    assert lay[J + "User.java"] == "model" and lay["src/main/java/com/acme/shop/ShopApplication.java"] == "entry"
    assert ("org.springframework",) in set(table(cfg, "SELECT pkg FROM externals"))


# ---------------------------------------------------------------------- C#
def test_csharp_aspnet_graph():
    cfg, res = build_fixture("csharp-aspnet")
    assert "C#" in res.engine and res.unresolved == 0
    files = set(layers_of(cfg))
    assert "Shop.Api/obj/Debug/Generated.cs" not in files  # obj/ next to the .csproj is skipped
    e = simple_edges(cfg)
    assert ("Shop.Api/Controllers/UsersController.cs", "Shop.Api/Services/IUserService.cs") in e  # using namespace
    assert ("Shop.Api/Services/UserService.cs", "Shop.Api/Data/UserRepository.cs") in e
    assert ("Shop.Api/Data/UserRepository.cs", "Shop.Api/Data/AppDbContext.cs") in e  # same namespace
    assert routes_of(cfg) == {
        ("GET", "/api/users", "Shop.Api/Controllers/UsersController.cs", "UsersController.List"),
        ("GET", "/api/users/:id", "Shop.Api/Controllers/UsersController.cs", "UsersController.Get"),
        ("POST", "/api/users", "Shop.Api/Controllers/UsersController.cs", "UsersController.Create"),
        ("GET", "/api/orders/:id", "Shop.Api/Endpoints/OrderEndpoints.cs", "OrderEndpoints.Get"),  # MapGroup
        ("GET", "/health", "Shop.Api/Program.cs", None),
    }
    lay = layers_of(cfg)
    assert lay["Shop.Api/Data/AppDbContext.cs"] == "repository" and lay["Shop.Api/Program.cs"] == "entry"
    assert lay["Shop.Api.Tests/UsersControllerTests.cs"] == "test"
    pkgs = dict(table(cfg, "SELECT path, pkg FROM files"))
    assert pkgs["Shop.Api/Program.cs"] == "Shop.Api" and pkgs["Shop.Api.Tests/UsersControllerTests.cs"] == "Shop.Api.Tests"


# ---------------------------------------------------------------------- PHP
def test_php_laravel_graph():
    cfg, res = build_fixture("php-laravel")
    assert "PHP" in res.engine and res.unresolved == 0
    assert not any(f.startswith("vendor/") for f in layers_of(cfg))
    e = simple_edges(cfg)
    assert ("routes/api.php", "app/Http/Controllers/UserController.php") in e  # use + ::class
    assert ("app/Http/Controllers/UserController.php", "app/Services/UserService.php") in e
    assert ("app/Models/Order.php", "app/Models/User.php") in e  # same namespace, User::class
    r = routes_of(cfg)
    assert ("GET", "/api/users/:user", "app/Http/Controllers/UserController.php", "UserController.show") in r
    assert ("POST", "/api/users", "app/Http/Controllers/UserController.php", "UserController.store") in r
    assert ("DELETE", "/api/admin/orders/:order", "app/Http/Controllers/OrderController.php",
            "OrderController.destroy") in r  # prefix group + apiResource
    assert ("GET", "/", "app/Http/Controllers/HomeController.php", "HomeController.index") in r  # 'Ctrl@method'
    assert len(r) == 10
    lay = layers_of(cfg)
    assert lay["app/Http/Requests/StoreUserRequest.php"] == "type" and lay["app/Models/User.php"] == "model"
    assert lay["tests/Feature/UserTest.php"] == "test" and lay["routes/api.php"] == "route"


def test_php_symfony_attributes(tmp_path):
    (tmp_path / "composer.json").write_text('{"name": "a/b", "autoload": {"psr-4": {"App\\\\": "src/"}}}')
    (tmp_path / "src" / "Controller").mkdir(parents=True)
    (tmp_path / "src" / "Controller" / "BlogController.php").write_text(
        "<?php\nnamespace App\\Controller;\n\nuse Symfony\\Component\\Routing\\Attribute\\Route;\n\n"
        "#[Route('/blog')]\nclass BlogController extends AbstractController\n{\n"
        "    #[Route('/{slug}', name: 'post', methods: ['GET', 'HEAD'])]\n    public function show(string $slug) {}\n\n"
        "    #[Route('', methods: ['POST'])]\n    public function create() {}\n}\n")
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes_of(cfg) == {
        ("GET", "/blog/:slug", "src/Controller/BlogController.php", "BlogController.show"),
        ("HEAD", "/blog/:slug", "src/Controller/BlogController.php", "BlogController.show"),
        ("POST", "/blog", "src/Controller/BlogController.php", "BlogController.create"),
    }


# ---------------------------------------------------------------------- Ruby
def test_ruby_rails_graph():
    cfg, res = build_fixture("ruby-rails")
    assert "Ruby" in res.engine and res.unresolved == 0
    e = simple_edges(cfg)
    assert ("app/controllers/api/users_controller.rb", "app/models/user.rb") in e  # autoloaded constant
    assert ("app/controllers/api/users_controller.rb", "app/services/users/creator.rb") in e  # Users::Creator
    assert ("spec/models/user_spec.rb", "spec/rails_helper.rb") in e  # require through spec/ on the load path
    assert routes_of(cfg) == {
        ("GET", "/", "app/controllers/home_controller.rb", "HomeController.index"),
        ("GET", "/api/users", "app/controllers/api/users_controller.rb", "UsersController.index"),
        ("POST", "/api/users", "app/controllers/api/users_controller.rb", "UsersController.create"),
        ("GET", "/api/users/:id", "app/controllers/api/users_controller.rb", "UsersController.show"),
        ("POST", "/api/users/:id/activate", "app/controllers/api/users_controller.rb", "UsersController.activate"),
        ("GET", "/api/users/:user_id/posts", "app/controllers/api/posts_controller.rb", "PostsController.index"),
        ("GET", "/health", "app/controllers/health_controller.rb", "HealthController.show"),
    }
    lay = layers_of(cfg)
    assert lay["app/models/user.rb"] == "model" and lay["app/controllers/home_controller.rb"] == "controller"
    assert lay["spec/models/user_spec.rb"] == "test" and lay["config/routes.rb"] == "route"


def test_ruby_sinatra(tmp_path):
    (tmp_path / "Gemfile").write_text("gem 'sinatra'\n")
    (tmp_path / "app.rb").write_text(
        "require 'sinatra'\n\nget '/hi/:name' do\n  'hi'\nend\n\npost '/items' do\n  'ok'\nend\n")
    cfg, _ = build_fixture("", root=tmp_path)
    assert {(m, p) for m, p, _f, _h in routes_of(cfg)} == {("GET", "/hi/:name"), ("POST", "/items")}


# ---------------------------------------------------------------------- Rust
def test_rust_axum_graph():
    cfg, res = build_fixture("rust-axum")
    assert "Rust" in res.engine and res.unresolved == 0
    assert not any(f.startswith("target/") for f in layers_of(cfg))
    e = simple_edges(cfg)
    assert ("src/main.rs", "src/handlers/mod.rs") in e  # mod handlers; -> handlers/mod.rs
    assert ("src/handlers/mod.rs", "src/handlers/users.rs") in e  # pub mod users;
    assert ("src/handlers/users.rs", "src/db.rs") in e  # use crate::db::UserRepo
    assert routes_of(cfg) == {
        ("GET", "/health", "src/handlers/mod.rs", "health"),
        ("GET", "/api/users", "src/handlers/users.rs", "list"),  # .nest("/api", api())
        ("POST", "/api/users", "src/handlers/users.rs", "create"),
        ("GET", "/api/users/:id", "src/handlers/users.rs", "show"),
    }
    assert ("axum",) in set(table(cfg, "SELECT pkg FROM externals"))
    assert dict(table(cfg, "SELECT path, layer FROM files"))["tests/api.rs"] == "test"


def test_rust_actix_and_rocket(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "svc"\n\n[dependencies]\nrocket = "0.5"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text(
        '#[macro_use] extern crate rocket;\n\n#[get("/<id>")]\nfn item(id: u32) -> String { id.to_string() }\n\n'
        '#[post("/", data = "<body>")]\nfn create(body: String) -> String { body }\n\n'
        '#[launch]\nfn rocket() -> _ {\n    rocket::build().mount("/items", routes![item, create])\n}\n')
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes_of(cfg) == {("GET", "/items/:id", "src/main.rs", "item"), ("POST", "/items", "src/main.rs", "create")}


# ---------------------------------------------------------------------- Kotlin
def test_kotlin_ktor_graph():
    cfg, res = build_fixture("kotlin-ktor")
    assert "Kotlin" in res.engine and res.unresolved == 0
    k = "src/main/kotlin/com/acme/"
    e = simple_edges(cfg)
    assert (k + "plugins/Routing.kt", k + "users/UserService.kt") in e  # explicit import
    assert (k + "users/UserService.kt", k + "users/UserRepository.kt") in e  # same package
    assert (k + "Application.kt", k + "plugins/Routing.kt") in e  # import of a top-level extension function
    assert {(m, p) for m, p, _f, _h in routes_of(cfg)} == {
        ("GET", "/health"), ("GET", "/api/users"), ("GET", "/api/users/:id"), ("POST", "/api/users")}
    assert dict(table(cfg, "SELECT path, layer FROM files"))[
        "src/test/kotlin/com/acme/users/UserServiceTest.kt"] == "test"


def test_kotlin_spring(tmp_path):
    (tmp_path / "build.gradle.kts").write_text('dependencies { implementation("org.springframework.boot:spring-boot-starter-web") }\n')
    d = tmp_path / "src" / "main" / "kotlin" / "a"
    d.mkdir(parents=True)
    (d / "UserController.kt").write_text(
        "package a\n\nimport org.springframework.web.bind.annotation.*\n\n@RestController\n@RequestMapping(\"/api/users\")\n"
        "class UserController(private val service: UserService) {\n    @GetMapping(\"/{id}\")\n"
        "    fun get(@PathVariable id: Long) = service.find(id)\n}\n")
    (d / "UserService.kt").write_text("package a\n\nimport org.springframework.stereotype.Service\n\n@Service\n"
                                      "class UserService {\n    fun find(id: Long) = id\n}\n")
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes_of(cfg) == {("GET", "/api/users/:id", "src/main/kotlin/a/UserController.kt", "UserController.get")}
    assert layers_of(cfg)["src/main/kotlin/a/UserService.kt"] == "service"
    refs = set(table(cfg, "SELECT symbol, ref_file, ref_symbol FROM symbol_refs"))
    assert ("UserController.get", "src/main/kotlin/a/UserService.kt", "UserService.find") in refs


# ---------------------------------------------------------------------- C / C++
def test_c_includes():
    cfg, res = build_fixture("c-cmake")
    assert "tree-sitter: C" in res.engine
    assert "build/generated.c" not in layers_of(cfg)  # build/ next to CMakeLists.txt is skipped
    e = simple_edges(cfg)
    assert ("src/store.c", "include/kv/store.h") in e  # include_directories(include)
    assert ("include/kv/store.h", "include/kv/hash.h") in e
    assert ("tests/test_hash.c", "include/kv/hash.h") in e
    kinds = {(s, d, k) for s, d, k, *_ in edges_of(cfg)}
    assert ("include/kv/hash.h", "src/hash.c", "implements") in kinds  # header -> its implementation, not an import
    assert not table(cfg, "SELECT 1 FROM edges WHERE src = 'src/hash.c' AND dst = 'include/kv/hash.h' AND kind = 'implements'")
    assert set(table(cfg, "SELECT file, spec FROM unresolved")) == {("src/main.c", "missing_config.h")}
    assert {"stdio", "stdlib", "stddef"} <= {p for (p,) in table(cfg, "SELECT pkg FROM externals")}
    syms = set(table(cfg, "SELECT file, name, kind FROM symbols"))
    assert ("include/kv/store.h", "store_put", "function") in syms  # prototype
    assert ("include/kv/store.h", "Store", "class") in syms  # typedef struct
    assert ("src/store.c", "grow", "function") in syms
    assert dict(table(cfg, "SELECT path, layer FROM files"))["tests/test_hash.c"] == "test"
    assert ("src/main.c",) in set(table(cfg, "SELECT path FROM files WHERE is_entry = 1"))


def test_cpp_classes_and_includes():
    cfg, res = build_fixture("cpp-game")
    assert "C++" in res.engine and res.unresolved == 0
    e = simple_edges(cfg)
    assert ("src/player.hpp", "src/entity.hpp") in e and ("src/main.cpp", "src/world.hpp") in e
    assert ("tests/world_test.cpp", "src/world.hpp") in e  # "../src/world.hpp"
    inh = set(table(cfg, "SELECT file, symbol, kind, ref_file, ref_symbol FROM inherits"))
    assert ("src/player.hpp", "Player", "extends", "src/entity.hpp", "Entity") in inh  # through the include
    syms = {(f, n) for f, n, _k in table(cfg, "SELECT file, name, kind FROM symbols")}
    assert ("src/world.cpp", "World.step") in syms  # out-of-class definition of a class declared in a header
    assert ("src/player.hpp", "Player.update") in syms


# ---------------------------------------------------------------------- Swift
def test_swift_vapor_graph():
    cfg, res = build_fixture("swift-vapor")
    assert "Swift" in res.engine and res.unresolved == 0
    assert not any(f.startswith(".build/") for f in layers_of(cfg))
    e = simple_edges(cfg)
    a = "Sources/App/"
    assert (a + "routes.swift", a + "Controllers/TodoController.swift") in e  # same module, no import
    assert (a + "Controllers/TodoController.swift", a + "Services/TodoService.swift") in e
    assert (a + "Controllers/TodoController.swift", a + "routes.swift") not in e  # `routes:` is a parameter
    assert ("Tests/AppTests/AppTests.swift", a + "Services/TodoService.swift") in e  # @testable import App
    assert routes_of(cfg) == {
        ("GET", "/health", a + "routes.swift", "routes"),
        ("GET", "/api/todos", a + "Controllers/TodoController.swift", "TodoController.index"),  # register(collection:)
        ("POST", "/api/todos", a + "Controllers/TodoController.swift", "TodoController.create"),
        ("DELETE", "/api/todos/:todoID", a + "Controllers/TodoController.swift", "TodoController.delete"),
    }
    assert layers_of(cfg)["Tests/AppTests/AppTests.swift"] == "test"


# ---------------------------------------------------------------------- queries on top
@pytest.mark.parametrize("name, controller, service", [
    ("python-fastapi", "app/routers/users.py", ("app/services/user_service.py", "UserService.create")),
    ("go-gin", "internal/handlers/user_handler.go", ("internal/users/service.go", "Service.Create")),
    ("java-spring", J + "UserController.java", (J + "UserService.java", "UserService.create")),
    ("csharp-aspnet", "Shop.Api/Controllers/UsersController.cs", ("Shop.Api/Services/IUserService.cs", "IUserService.Create")),
])
def test_analyze_and_endpoint(name, controller, service):
    from nimblegraph import query
    cfg, _ = build_fixture(name)
    g = load_graph(cfg)
    ch = query.analyze(g, "user", depth=4)
    assert controller in ch.files and service[0] in ch.files
    v = query.endpoint(g, g.find_routes("POST /api/users")[0], depth=4)
    assert service in [(f, s) for _d, f, s in v.down]  # the handler's own call, by method


def test_regex_fallback_without_tree_sitter(monkeypatch):
    monkeypatch.setattr(base, "parser", lambda grammar: None)
    cfg, res = build_fixture("java-spring")
    assert "regex: Java" in res.engine
    assert any("tree-sitter not available" in w for w in res.warnings)
    e = simple_edges(cfg)
    assert (J + "UserController.java", J + "dto/CreateUserRequest.java") in e  # explicit imports still resolve
