"""Patterns found on real projects (mealie, netbox, mastodon, eShop, gitea, FastAPI template): class-based route
controllers, router subclasses, prefixes held in constants/settings/properties, DRF routers under include(),
Devise, and HTTP calls of clients and tests in every language linked to the routes."""

from __future__ import annotations

from pathlib import Path

import pytest

from nimblegraph.lang import base
from tests.conftest import build_fixture, table

pytestmark = pytest.mark.skipif(base.parser("python") is None, reason="tree-sitter grammars not available")


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def routes(cfg) -> set[tuple]:
    return set(table(cfg, "SELECT method, path, handler FROM routes"))


def calls(cfg) -> set[tuple]:
    return set(table(cfg, "SELECT c.file, c.method, r.method || ' ' || r.path FROM api_calls c "
                          "JOIN routes r ON r.id = c.route_id"))


def test_fastapi_class_controllers_router_subclass_and_settings_prefix(tmp_path):
    """mealie: @controller(router) classes, UserAPIRouter(APIRouter), prefix=PREFIX; template: settings.API_V1_STR."""
    write(tmp_path, {
        "pyproject.toml": '[project]\nname = "m"\ndependencies = ["fastapi"]\n',
        "app/__init__.py": "",
        "app/config.py": 'class Settings:\n    API_V1_STR: str = "/api/v1"\n\nsettings = Settings()\n',
        "app/routers.py": "from fastapi import APIRouter\n\nclass UserAPIRouter(APIRouter):\n    pass\n",
        "app/recipes.py": (
            "from app.routers import UserAPIRouter\n\nPREFIX = '/recipes'\nrouter = UserAPIRouter(prefix=PREFIX)\n\n"
            "@controller(router)\nclass RecipeController:\n    @router.get('/{slug}')\n    def get_one(self, slug):\n"
            "        return slug\n\n    @router.post('')\n    def create_one(self):\n        return 1\n"),
        "app/main.py": ("from fastapi import FastAPI\nfrom app.config import settings\nfrom app import recipes\n\n"
                        "app = FastAPI()\napp.include_router(recipes.router, prefix=settings.API_V1_STR)\n"),
    })
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes(cfg) == {("GET", "/api/v1/recipes/:slug", "RecipeController.get_one"),
                           ("POST", "/api/v1/recipes", "RecipeController.create_one")}


def test_drf_custom_router_under_include(tmp_path):
    """netbox: NetBoxRouter(DefaultRouter), `urlpatterns = router.urls` included under api/circuits/."""
    write(tmp_path, {
        "requirements.txt": "django\ndjangorestframework\n",
        "netbox/__init__.py": "",
        "netbox/routers.py": "from rest_framework.routers import DefaultRouter\n\nclass NetBoxRouter(DefaultRouter):\n    pass\n",
        "netbox/urls.py": ("from django.urls import include, path\n\nurlpatterns = [\n"
                           "    path('api/circuits/', include('circuits.api.urls')),\n]\n"),
        "circuits/__init__.py": "",
        "circuits/api/__init__.py": "",
        "circuits/api/views.py": "class CircuitViewSet:\n    pass\n",
        "circuits/api/urls.py": ("from netbox.routers import NetBoxRouter\nfrom . import views\n\nrouter = NetBoxRouter()\n"
                                 "router.register('circuits', views.CircuitViewSet)\n\nurlpatterns = router.urls\n"),
    })
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes(cfg) == {("ALL", "/api/circuits/circuits", "CircuitViewSet"),
                           ("ALL", "/api/circuits/circuits/:pk", "CircuitViewSet")}


def test_spring_placeholders_and_constants(tmp_path):
    write(tmp_path, {
        "pom.xml": "<project><artifactId>a</artifactId></project>",
        "src/main/resources/application.properties": "api.prefix=/api/v2\n",
        "src/main/java/a/ApiPaths.java": 'package a;\npublic final class ApiPaths {\n    public static final String USERS = "/users";\n}\n',
        "src/main/java/a/UserController.java": (
            'package a;\n@RestController\n@RequestMapping("${api.prefix}")\npublic class UserController {\n'
            '    @GetMapping(ApiPaths.USERS + "/{id}")\n    public User get(Long id) { return null; }\n}\n'),
    })
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes(cfg) == {("GET", "/api/v2/users/:id", "UserController.get")}


def test_aspnet_route_constant_and_eshop_style_client(tmp_path):
    write(tmp_path, {
        "Api/Api.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>',
        "Api/ApiRoutes.cs": 'namespace Api;\npublic static class ApiRoutes { public const string Catalog = "api/catalog"; }\n',
        "Api/CatalogController.cs": (
            "namespace Api;\n[ApiController]\n[Route(ApiRoutes.Catalog)]\npublic class CatalogController : ControllerBase\n{\n"
            '    [HttpGet("items/{id:int}")]\n    public IActionResult Item(int id) => Ok();\n}\n'),
        "Web/Web.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>',
        "Web/CatalogService.cs": (
            "namespace Web;\npublic class CatalogService(HttpClient httpClient)\n{\n"
            '    private readonly string remoteServiceBaseUrl = "api/catalog/";\n'
            "    public Task<Item?> GetItem(int id)\n    {\n        var uri = $\"{remoteServiceBaseUrl}items/{id}\";\n"
            "        return httpClient.GetFromJsonAsync<Item>(uri);\n    }\n}\n"),
    })
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes(cfg) == {("GET", "/api/catalog/items/:id", "CatalogController.Item")}
    assert calls(cfg) == {("Web/CatalogService.cs", "GET", "GET /api/catalog/items/:id")}


def test_go_const_prefix_sprintf_client_and_httptest(tmp_path):
    write(tmp_path, {
        "go.mod": "module example.com/app\n\ngo 1.22\n",
        "main.go": ('package main\n\nimport "github.com/gin-gonic/gin"\n\nconst apiPrefix = "/api/v1"\n\nfunc main() {\n'
                    '\tr := gin.Default()\n\tapi := r.Group(apiPrefix)\n\tapi.GET("/users/:id", getUser)\n}\n\n'
                    "func getUser(c *gin.Context) {}\n"),
        "client/client.go": ('package client\n\nimport (\n\t"fmt"\n\t"net/http"\n)\n\nfunc Get(base string, id int) {\n'
                             '\turl := fmt.Sprintf("%s/api/v1/users/%d", base, id)\n\thttp.Get(url)\n}\n'),
        "main_test.go": ('package main\n\nimport (\n\t"net/http/httptest"\n\t"testing"\n)\n\nfunc TestGet(t *testing.T) {\n'
                         '\treq := httptest.NewRequest("GET", apiPrefix+"/users/1", nil)\n\t_ = req\n}\n'),
    })
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes(cfg) == {("GET", "/api/v1/users/:id", "getUser")}
    assert calls(cfg) == {("client/client.go", "GET", "GET /api/v1/users/:id"),
                          ("main_test.go", "GET", "GET /api/v1/users/:id")}


def test_python_clients_and_tests_reach_routes(tmp_path):
    cfg, _ = build_fixture("python-fastapi")  # tests/test_users.py: client.get("/api/users/")
    assert ("tests/test_users.py", "GET", "GET /api/users") in calls(cfg)
    kinds = dict(table(cfg, "SELECT src, kind FROM edges WHERE kind = 'calls_api'"))
    assert kinds.get("tests/test_users.py") == "calls_api"
    write(tmp_path, {
        "requirements.txt": "flask\nrequests\n",
        "api.py": "from flask import Flask\napp = Flask(__name__)\n\n@app.get('/orders/<int:oid>')\ndef order(oid):\n    return ''\n",
        "worker.py": "import requests\n\nBASE = 'http://orders'\n\ndef fetch(oid):\n    return requests.get(f'{BASE}/orders/{oid}')\n",
        "conf.py": "d = {}\nd.get('orders/1')\n",  # not a client: no path call matched, nothing recorded
    })
    cfg, _ = build_fixture("", root=tmp_path)
    assert calls(cfg) == {("worker.py", "GET", "GET /orders/:oid")}


def test_java_mockmvc_rails_request_spec_and_laravel_test(tmp_path):
    cfg, _ = build_fixture("php-laravel")  # $this->getJson('/api/users')
    assert ("tests/Feature/UserTest.php", "GET", "GET /api/users") in calls(cfg)
    write(tmp_path / "j", {
        "pom.xml": "<project><artifactId>a</artifactId></project>",
        "src/main/java/a/UserController.java": ('package a;\n@RestController\n@RequestMapping("/api/users")\n'
                                                "public class UserController {\n    @GetMapping(\"/{id}\")\n"
                                                "    public String get(Long id) { return null; }\n}\n"),
        "src/test/java/a/UserControllerTest.java": ("package a;\nclass UserControllerTest {\n    void t() throws Exception {\n"
                                                    '        mockMvc.perform(get("/api/users/1"));\n    }\n}\n'),
    })
    cfg, _ = build_fixture("", root=tmp_path / "j")
    assert calls(cfg) == {("src/test/java/a/UserControllerTest.java", "GET", "GET /api/users/:id")}
    write(tmp_path / "r", {
        "Gemfile": "gem 'rails'\n",
        "config/routes.rb": "Rails.application.routes.draw do\n  resources :posts, only: [:show]\nend\n",
        "app/controllers/posts_controller.rb": "class PostsController < ApplicationController\n  def show; end\nend\n",
        "spec/requests/posts_spec.rb": 'RSpec.describe "Posts" do\n  it "shows" do\n    get "/posts/1"\n  end\nend\n',
    })
    cfg, _ = build_fixture("", root=tmp_path / "r")
    assert calls(cfg) == {("spec/requests/posts_spec.rb", "GET", "GET /posts/:id")}


def test_devise_routes_from_model_modules(tmp_path):
    write(tmp_path, {
        "Gemfile": "gem 'rails'\ngem 'devise'\n",
        "config/routes.rb": "Rails.application.routes.draw do\n  devise_for :users, path: 'auth'\nend\n",
        "app/models/user.rb": "class User < ApplicationRecord\n  devise :database_authenticatable, :recoverable\nend\n",
    })
    cfg, _ = build_fixture("", root=tmp_path)
    r = {(m, p) for m, p, _h in routes(cfg)}
    assert {("GET", "/auth/sign_in"), ("POST", "/auth/sign_in"), ("DELETE", "/auth/sign_out"),
            ("POST", "/auth/password"), ("GET", "/auth/password/new")} <= r
    assert not any(p.startswith("/auth/sign_up") for _m, p in r)  # :registerable is not enabled


def test_unresolved_runtime_prefix_is_dropped_not_invented(tmp_path):
    write(tmp_path, {
        "requirements.txt": "fastapi\n",
        "main.py": ("import os\nfrom fastapi import FastAPI, APIRouter\napp = FastAPI()\nrouter = APIRouter()\n\n"
                    "@router.get('/ping')\ndef ping():\n    return 1\n\n"
                    "app.include_router(router, prefix=os.environ['API_PREFIX'])\n"),
    })
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes(cfg) == {("GET", "/ping", "ping")}


def test_go_mounted_router_function_and_group_with_middleware(tmp_path):
    """gitea: r.Mount("/api/v1", apiv1.Routes()) and m.Group("/repos", func() {...}, reqToken())."""
    write(tmp_path, {
        "go.mod": "module example.com/g\n\ngo 1.22\n",
        "main.go": ('package main\n\nimport (\n\t"github.com/go-chi/chi/v5"\n\t"example.com/g/apiv1"\n)\n\n'
                    'func main() {\n\tr := chi.NewRouter()\n\tr.Mount("/api/v1", apiv1.Routes())\n}\n'),
        "apiv1/api.go": ('package apiv1\n\nimport "github.com/go-chi/chi/v5"\n\nfunc Routes() *chi.Mux {\n'
                         '\tm := chi.NewRouter()\n\tm.Group("/repos", func() {\n\t\tm.Post("/{id}/transfer", Transfer)\n'
                         '\t}, reqToken())\n\treturn m\n}\n\nfunc Transfer() {}\nfunc reqToken() {}\n'),
    })
    cfg, _ = build_fixture("", root=tmp_path)
    assert routes(cfg) == {("POST", "/api/v1/repos/:id/transfer", "Transfer")}
