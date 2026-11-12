"""endpoint / analyze / flow / impact / cycles / orphans / find."""

from __future__ import annotations

from nimblegraph import cli, query
from nimblegraph import tokens as tk
from conftest import build_fixture, fixture_root, load_graph


def graph(name: str, eng: str = "python"):
    cfg, _ = build_fixture(name, eng)
    return load_graph(cfg)


def test_find_routes_by_url_method_and_term():
    g = graph("express-api")
    assert sorted(r.label for r in g.find_routes("/api/users/42")) == ["DELETE /api/users/:id", "GET /api/users/:id"]
    assert [r.label for r in g.find_routes("POST /api/users")] == ["POST /api/users"]
    assert [r.label for r in g.find_routes("delete /api/users/7")] == ["DELETE /api/users/:id"]
    assert len(g.find_routes("users")) == 4
    assert g.find_routes("/nothing") == []


def test_endpoint_follows_only_what_the_handler_uses():
    g = graph("express-api")
    r = g.find_routes("POST /api/users")[0]
    v = query.endpoint(g, r, depth=4)
    down = [(f, s) for _d, f, s in v.down]
    # the controller imports three services; create uses two of them, by method
    assert ("src/services/user.service.ts", "UserService.create") in down
    assert ("src/services/mail.service.ts", "MailService.welcome") in down
    assert not any(f == "src/services/audit.service.ts" for f, _ in down)
    assert ("src/repositories/user.repo.ts", "userRepo.insert") in down
    assert ("src/lib/db.ts", "db.insert") in down
    assert v.tests == {"tests/user.e2e.spec.ts"}

    remove = query.endpoint(g, g.find_routes("DELETE /api/users/1")[0], depth=4)
    assert [f for _d, f, _s in remove.down] == ["src/services/audit.service.ts", "src/lib/db.ts"]


def test_endpoint_nest_injection_and_front(capsys):
    g = graph("nest-api")
    v = query.endpoint(g, g.find_routes("POST /api/users")[0], depth=4)
    assert [(f, s) for _d, f, s in v.down] == [("src/users/users.service.ts", "UsersService.create")]

    g = graph("next-app")
    v = query.endpoint(g, g.find_routes("GET /api/users")[0], depth=4)
    assert v.front == [[("app/(dashboard)/users/page.tsx", None), ("components/UserTable.tsx", "UserTable")]]
    assert ("lib/users.ts", "listUsers") in [(f, s) for _d, f, s in v.down]


def expand(out: str) -> str:
    """Undo path aliases ($s=src/services) so assertions do not depend on them."""
    lines = out.splitlines()
    if len(lines) > 1 and lines[1].startswith("$"):
        for pair in lines[1].split():
            alias, _, prefix = pair.partition("=")
            out = out.replace(alias + "/", prefix + "/")
    return out


def test_endpoint_cli_output(capsys):
    root = str(fixture_root("express-api"))
    assert cli.main(["endpoint", "POST", "/api/users", "--root", root, "--engine", "python"]) == 0
    out = expand(capsys.readouterr().out)
    assert out.startswith("@endpoint POST /api/users")
    lines = out.splitlines()
    order = [next(i for i, line in enumerate(lines) if line.startswith(h))
             for h in ("MOUNT", "MIDDLEWARE", "VALIDATION", "HANDLER", "DOWN", "TESTS", "EXT")]
    assert order == sorted(order)
    assert "MOUNT src/server.ts /api > src/routes/index.ts /users > src/routes/user.routes.ts" in out
    assert " src/server.ts: cors, express.json" in out
    # Git Bash rewrites /api/users into C:/Program Files/Git/api/users
    assert cli.main(["endpoint", "C:/Program Files/Git/api/users/42", "--root", root, "--engine", "python"]) == 0
    assert "@endpoint GET /api/users/:id" in capsys.readouterr().out
    assert cli.main(["endpoint", "POST", "/api/users", "--files", "--root", root, "--engine", "python"]) == 0
    files = capsys.readouterr().out.splitlines()[1:-1]
    assert files[0] == "src/server.ts" and "src/controllers/user.controller.ts" in files and "src/lib/db.ts" in files


def test_analyze_crosses_front_and_back():
    g = graph("next-app")
    ch = query.analyze(g, "users", depth=4)
    assert {"app/(dashboard)/users/page.tsx", "components/UserTable.tsx", "app/api/users/route.ts"} <= ch.files
    assert ("app/api/users/route.ts", "GET /api/users") in ch.links["components/UserTable.tsx"]

    g = graph("express-api")
    seeds, how = query.seed_files(g, "user")  # singular term matches users.*, user.* files
    assert "src/controllers/user.controller.ts" in seeds and how.startswith("name")
    assert "tests/user.e2e.spec.ts" not in seeds


def test_analyze_cli_budget(capsys):
    root = str(fixture_root("express-api"))
    assert cli.main(["analyze", "user", "--root", root, "--engine", "python"]) == 0
    out = capsys.readouterr().out
    assert "ROUTES" in out and "GET,POST /api/users" in out and "FLOW" in out
    assert cli.main(["analyze", "user", "--root", root, "--engine", "python", "--budget", "120"]) == 0
    assert tk.estimate(capsys.readouterr().out) <= 125


def test_flow_from_page():
    g = graph("next-app")
    ch = query.flow(g, "/users", depth=4)
    assert {"app/(dashboard)/users/page.tsx", "components/UserTable.tsx", "app/api/users/route.ts", "lib/users.ts"} <= ch.files


def test_impact():
    g = graph("express-api")
    res = query.impact(g, ["src/lib/db.ts"])
    assert {r.label for r in res["routes"]} == {"GET /api/users", "POST /api/users", "GET /api/users/:id",
                                                "DELETE /api/users/:id"}
    assert res["tests"] == {"tests/user.e2e.spec.ts"}
    g = graph("next-app")
    res = query.impact(g, ["lib/users.ts"])
    assert {r.path for r in res["pages"]} == {"/users", "/users/:id"}


def test_cycles_and_orphans(project_copy):
    root = project_copy("vite-react")
    (root / "src" / "lib" / "a.ts").write_text("import { b } from './b'\nexport const a = () => b\n", encoding="utf-8")
    (root / "src" / "lib" / "b.ts").write_text("import { a } from './a'\nexport const b = () => a\n", encoding="utf-8")
    cfg, _ = build_fixture("vite-react", "python", root=root)
    g = load_graph(cfg)
    assert query.cycles(g) == [["src/lib/a.ts", "src/lib/b.ts"]]
    orph = query.orphans(g)
    assert "src/lib/legacy.js" in orph
    assert "src/components/ui/index.ts" not in orph  # a barrel whose imports were routed through it is used
    assert "src/main.tsx" not in orph  # entry


def test_find_cli(capsys):
    root = str(fixture_root("nest-api"))
    assert cli.main(["find", "create", "--root", root, "--engine", "python"]) == 0
    out = capsys.readouterr().out
    assert "UsersController.create method" in out and "UsersService.create method" in out
    assert cli.main(["routes", "--root", root, "--engine", "python"]) == 0
    out = capsys.readouterr().out
    assert " POST /api/users > src/users/users.controller.ts#UsersController.create" in out


def test_analyze_too_broad_shows_areas(monkeypatch, capsys):
    monkeypatch.setattr(query, "MAX_SEEDS", 2)
    root = str(fixture_root("express-api"))
    assert cli.main(["analyze", "user", "--root", root, "--engine", "python"]) == 0
    out = capsys.readouterr().out
    assert "too broad for one chain" in out and "AREAS" in out and "NARROW" in out
    assert "FLOW" not in out


def test_words_ignore_accents_and_portuguese_plurals():
    assert query.tokens("agênciaCotação.ts") == {"agencia", "cotacao"}
    assert "cotacoes" in query.word_variants("Cotação") and "cotacao" in query.word_variants("cotações")
    assert query.fold("Análise") == "analise"
