"""nimblegraph init (+ CLAUDE.md, hook) and nimblegraph export."""

from __future__ import annotations

import json

from nimblegraph import cli, project
from nimblegraph.config import load


def test_init_is_idempotent(project_copy, capsys):
    root = project_copy("express-api")
    (root / ".git").mkdir()
    (root / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Projeto\nRegras do time.\n", encoding="utf-8")
    assert cli.main(["init", "--root", str(root), "--claude-md", "--hook", "--engine", "python"]) == 0
    out = capsys.readouterr().out
    assert "appended the nimblegraph section" in out and "indexed 15 files" in out
    cfg = load(root)
    assert (cfg.dir / "config.toml").is_file()
    gi = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".nimblegraph/graph.db" in gi and "node_modules" in gi
    md = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert md.startswith("# Projeto") and md.count(project.MARKER) == 1 and "nimblegraph analyze" in md
    settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "nimblegraph build --quiet"
    # second run changes nothing
    assert cli.main(["init", "--root", str(root), "--claude-md", "--hook", "--engine", "python"]) == 0
    out = capsys.readouterr().out
    assert "already has the nimblegraph section" in out and "already installed" in out
    assert (root / "CLAUDE.md").read_text(encoding="utf-8").count(project.MARKER) == 1
    assert (root / ".gitignore").read_text(encoding="utf-8").count(".nimblegraph/graph.db\n") == 1


def test_init_outside_git_leaves_gitignore_alone(project_copy, capsys):
    root = project_copy("nest-api")
    assert cli.main(["init", "--root", str(root), "--engine", "python"]) == 0
    assert "not a git repository" in capsys.readouterr().out
    assert not (root / ".gitignore").exists()


def test_export_formats(project_copy, capsys, tmp_path):
    root = str(project_copy("next-app"))
    assert cli.main(["export", "users", "--root", root, "--engine", "python"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("flowchart LR") and "users/page.tsx" in out and "-.->|API|" in out
    assert cli.main(["export", "--format", "dot", "--root", root, "--engine", "python"]) == 0
    assert capsys.readouterr().out.startswith("digraph nimblegraph {")
    target = tmp_path / "g.json"
    assert cli.main(["export", "users", "--format", "json", "--out", str(target), "--root", root, "--engine", "python"]) == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert {"source": "components/UserTable.tsx", "target": "app/api/users/route.ts", "kind": "calls_api"} in data["links"]
    assert any(r["path"] == "/api/users" for r in data["routes"])
