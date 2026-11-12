"""Extraction: Python parser specifics and Node <-> Python parity on the fixtures."""

from __future__ import annotations

import json

import pytest

from nimblegraph import config, engine, scanner
from nimblegraph.parse import ts as pyparse
from conftest import ALL_FIXTURES, fixture_root, needs_node


def py(name: str, rel: str) -> dict:
    return pyparse.extract_path(fixture_root(name), rel)


def norm(rec: dict) -> dict:
    r = json.loads(json.dumps(rec))
    for i in r["imports"]:
        i.pop("resolved", None)
        i.pop("external", None)
    r["imports"].sort(key=lambda x: (x["line"], x["spec"], x["kind"]))
    r["exports"].sort(key=lambda x: (x["name"], x["kind"]))
    r["calls"].sort(key=lambda x: (x["line"], str(x["prop"]), str(x["callee"]), json.dumps(x["args"])))
    return r


@needs_node
@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_parity_node_python(name):
    root = fixture_root(name)
    files = sorted(scanner.scan(config.load(root)).files)
    out = engine.NodeEngine(engine.find_node()).extract(root, files)
    assert not out.failed
    for f in files:
        assert norm(out.records[f]) == norm(pyparse.extract_path(root, f)), f


def test_imports_kinds_and_names():
    r = py("vite-react", "src/components/Layout.tsx")
    assert [(i["spec"], i["kind"], i["names"]) for i in r["imports"]] == [
        ("./ui", "import", [["Button", "Button"], ["Card", "Card"]]),
        ("./ui", "type", [["ButtonProps", "ButtonProps"]]),
    ]
    barrel = py("vite-react", "src/components/ui/index.ts")
    assert [(i["kind"], i["names"]) for i in barrel["imports"]] == [
        ("reexport", [["Button", "Button"]]), ("reexport", [["ButtonProps", "ButtonProps"]]), ("reexport-all", []),
    ]
    main = py("vite-react", "src/main.tsx")
    assert ("./index.css", "side-effect") in [(i["spec"], i["kind"]) for i in main["imports"]]


def test_commonjs_and_jsx_in_js():
    r = py("vite-react", "src/lib/legacy.js")
    assert r["imports"] == [{"spec": "./cn", "kind": "require", "names": [["cn", "cn"]], "line": 2}]
    assert {e["name"] for e in r["exports"]} == {"Banner", "cn"}
    assert r["symbols"][0]["kind"] == "component" and r["jsx"] == []  # <div> is not a component tag
    cjs = py("express-api", "src/legacy.cjs")
    assert cjs["vars"] == {"express": "require", "health": "express.Router"}
    assert {"name": "default", "kind": "cjs", "local": "health", "line": 5} in cjs["exports"]


def test_symbols_kinds_and_refs():
    r = py("vite-react", "src/App.tsx")
    syms = {s["name"]: s for s in r["symbols"]}
    assert syms["App"]["kind"] == "component"
    assert syms["SettingsPage"]["kind"] == "component"  # lazy(() => import(...))
    assert "Layout" in syms["App"]["refs"] and "SettingsPage" in syms["App"]["refs"]
    assert r["dyn_locals"]["SettingsPage"] == "./pages/SettingsPage"
    hooks = {s["name"]: s["kind"] for s in py("vite-react", "src/hooks/useUsers.ts")["symbols"]}
    assert hooks == {"BASE": "const", "useUsers": "hook", "useUser": "hook"}


def test_route_objects_array_and_jsx():
    r = py("vite-react", "src/App.tsx")
    ro = r["route_objects"]
    assert ro[0] == {"path": "/", "comps": ["Layout"], "lazy": None,
                     "children": [{"path": "users", "comps": ["UsersPage"], "lazy": None, "children": []}]}
    assert ro[1]["path"] == "/settings" and ro[1]["lazy"] == "./pages/SettingsPage"
    jsx_root = ro[2]
    assert jsx_root["path"] == "/" and [c["path"] for c in jsx_root["children"]] == ["users", "settings"]
    assert jsx_root["children"][1]["comps"] == ["SettingsPage"]


def test_calls_templates_and_chains():
    calls = py("vite-react", "src/hooks/useUsers.ts")["calls"]
    swr = [c for c in calls if c["callee"] == "useSWR"]
    assert swr[0]["args"][0] == {"t": "str", "v": "/api/users"}
    assert swr[1]["args"][0] == {"t": "tpl", "v": "/users/:param"}  # leading ${BASE} dropped
    api_get = next(c for c in calls if c["prop"] == "get")
    assert api_get["obj"] == "api" and api_get["args"] == [{"t": "tpl", "v": "/users/:param"}]
    assert api_get["sym"] == "useUser"
    routes = py("express-api", "src/routes/user.routes.ts")["calls"]
    got = [(c["prop"], c["args"][0]["v"], len(c["args"])) for c in routes if c["prop"] != "route"]
    assert ("get", "/", 2) in got and ("post", "/", 3) in got and ("delete", "/:id", 2) in got
    post = next(c for c in routes if c["prop"] == "post")
    assert post["args"][1] == {"t": "call", "callee": "validate", "args": [{"t": "id", "v": "createUserSchema"}]}
    assert post["args"][2] == {"t": "member", "v": "userController.create"}


def test_nest_decorators_and_injection():
    r = py("nest-api", "src/users/users.controller.ts")
    cls = r["symbols"][0]
    assert cls["decorators"] == [{"name": "Controller", "args": [{"t": "str", "v": "users"}]},
                                 {"name": "UseGuards", "args": [{"t": "id", "v": "AuthGuard"}]}]
    assert cls["ctor_types"] == {"users": "UsersService"}
    create = next(m for m in cls["methods"] if m["name"] == "create")
    assert create["param_types"] == ["CreateUserDto"]
    assert create["this_refs"] == ["users"]
    assert [d["name"] for d in create["decorators"]] == ["Post", "UsePipes"]
    main = py("nest-api", "src/main.ts")
    assert main["vars"]["app"] == "NestFactory.create"
    assert [(c["prop"], c["sym"]) for c in main["calls"]] == [("setGlobalPrefix", "bootstrap"), ("useGlobalInterceptors", "bootstrap")]


def test_next_directives_and_consts():
    assert py("next-app", "components/UserTable.tsx")["directives"] == ["use client"]
    mw = py("next-app", "middleware.ts")
    assert mw["consts"]["config"] == {"t": "obj", "props": {"matcher": {"t": "arr", "items": [
        {"t": "str", "v": "/api/:path*"}, {"t": "str", "v": "/users/:path*"}]}}}


@pytest.mark.parametrize("src,expect_imports", [
    # regex literal vs division, JSX text with an apostrophe, generic arrow in TSX
    ("const r = /'/g; const d = a / b / c\nimport x from './x'\n", ["./x"]),
    ("export const A = () => <p>Don't {\"stop\"}</p>\nimport y from './y'\n", ["./y"]),
    ("const id = <T,>(v: T) => v\nimport z from './z'\n", ["./z"]),
    ("const s = `a ${`b ${c}`} d`\nimport w from './w'\n", ["./w"]),
    ("obj.delete<T>(1)\nimport v from './v'\n", ["./v"]),
])
def test_lexer_edge_cases(src, expect_imports):
    r = pyparse.extract_source(src, "x.tsx")
    assert [i["spec"] for i in r["imports"]] == expect_imports


HERITAGE_SRC = """import request from 'supertest'
export abstract class Repo<T extends { id: string }> implements base.Port<T>, Other {
  abstract find(id: string): Promise<T>
}
export class Order extends AggregateRoot<OrderProps> implements IOrder { pay() { return 1 } }
class Mixed extends Mixin(Base) implements Z {}
export interface IOrder extends Base<X>, ns.Other { id: string }
interface Plain { a: number }
class NoHeritage {}
request(app).post('/x')
request.agent(app).get('/y')
new X().get('/z')
foo().then(x)
"""


def _heritage(rec: dict) -> dict:
    return {s["name"]: (s.get("extends"), s.get("implements"), s.get("abstract")) for s in rec["symbols"]}


def test_heritage_and_call_objects():
    r = pyparse.extract_source(HERITAGE_SRC, "a.spec.ts")
    assert _heritage(r) == {
        "Repo": (None, ["Port", "Other"], True), "Order": ("AggregateRoot", ["IOrder"], False),
        "Mixed": (None, ["Z"], False), "IOrder": (None, ["Base", "Other"], None), "Plain": (None, None, None),
        "NoHeritage": (None, [], False)}
    assert [(c["obj"], c["prop"]) for c in r["calls"]] == [("request()", "post"), ("request.agent()", "get")]


@needs_node
def test_heritage_parity(tmp_path):
    (tmp_path / "a.spec.ts").write_text(HERITAGE_SRC, encoding="utf-8")
    out = engine.NodeEngine(engine.find_node()).extract(tmp_path, ["a.spec.ts"])
    assert norm(out.records["a.spec.ts"]) == norm(pyparse.extract_path(tmp_path, "a.spec.ts"))
