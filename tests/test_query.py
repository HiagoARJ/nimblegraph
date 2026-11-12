"""Target lookup, trees, compact rendering and the CLI commands of phase 1."""

from __future__ import annotations

from nimblegraph import cli
from nimblegraph import tokens as tk
from nimblegraph.query import tree
from nimblegraph.render import Out, choose_aliases, finish
from conftest import build_fixture, fixture_root, load_graph


def test_find_files():
    cfg, _ = build_fixture("vite-react", "python")
    g = load_graph(cfg)
    assert g.find_files("src/App.tsx") == ["src/App.tsx"]
    assert g.find_files("ui/Button.tsx") == ["src/components/ui/Button.tsx"]
    assert g.find_files("UsersPage") == ["src/pages/UsersPage.tsx"]
    assert g.find_files("components/ui") == ["src/components/ui/Button.tsx", "src/components/ui/Card.tsx",
                                            "src/components/ui/index.ts"]
    assert g.find_files("nothing-here") == []


def test_tree_marks_repeats_and_depth():
    cfg, _ = build_fixture("express-api", "python")
    g = load_graph(cfg)
    top, seen = tree(g, "src/routes/user.routes.ts", depth=1)
    assert [c.path for c in top.children] == ["src/controllers/user.controller.ts", "src/middlewares/validate.ts",
                                              "src/schemas/user.schema.ts"]
    assert top.children[0].more == 3  # three services cut by depth
    top, seen = tree(g, "src/routes/user.routes.ts", depth=5)
    assert "src/lib/db.ts" in seen
    flat = []

    def walk(n):
        for c in n.children:
            flat.append((c.path, c.repeat))
            walk(c)

    walk(top)
    assert ("src/lib/db.ts", True) in flat  # reached twice: second time is a ^ref
    rtop, rseen = tree(g, "src/lib/db.ts", depth=6, reverse=True)
    assert "src/server.ts" in rseen and "tests/user.e2e.spec.ts" in rseen


def test_aliases_only_when_they_pay():
    paths = {f"src/components/ui/{n}.tsx" for n in "ABCDEFG"}
    text = "\n".join(f" > {p}" for p in sorted(paths))
    aliases = choose_aliases(text, paths)
    assert aliases and aliases[0][1] == "src/components/ui/"
    assert choose_aliases(" > src/a/x.ts", {"src/a/x.ts"}) == []


def test_finish_respects_budget():
    o = Out("deps big")
    for i in range(400):
        o.add(f" > src/features/module{i}/component{i}.tsx {{useThing{i}}}")
    o.hint = "nimblegraph deps big --depth 1"
    text = finish(o, budget=300)
    assert tk.estimate(text) <= 300
    assert "> nimblegraph deps big --depth 1" in text and "cut " in text.splitlines()[-1]


def test_cli_file_and_deps(capsys, monkeypatch):
    root = str(fixture_root("express-api"))
    assert cli.main(["file", "user.controller", "--root", root, "--engine", "python"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("@file src/controllers/user.controller.ts")
    assert "EXPORTS userController(const)" in out
    assert "METHODS userController: list:11, create:14, getById:19, remove:22" in out
    assert "IMPORTED-BY 1" in out and "< src/routes/user.routes.ts" in out
    assert out.rstrip().endswith("]") and "reading all:" in out

    assert cli.main(["deps", "src/server.ts", "--root", root, "--engine", "python", "--depth", "9"]) == 0
    out = capsys.readouterr().out
    assert "> ^" in out  # shared files are shown once
    assert "EXT " in out and "express" in out

    assert cli.main(["rdeps", "db.ts", "--root", root, "--engine", "python"]) == 0
    assert "< " in capsys.readouterr().out

    assert cli.main(["file", "services", "--root", root, "--engine", "python"]) == 1  # 3 files: ask to narrow
    assert "matches 3 files" in capsys.readouterr().out
