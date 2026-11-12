"""Notes: describe (single, --json, --batch, --chain), limits, staleness, todo, update (+ --ai command), views."""

from __future__ import annotations

import io
import json

from nimblegraph import cli, update
from nimblegraph import tokens as tk
from nimblegraph.notes import CHAIN_LIMITS, FILE_LIMITS, chain_key, validate


def run(capsys, *argv, stdin: str | None = None, monkeypatch=None) -> tuple[int, str]:
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="cp1252"))
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_validate_and_keys():
    assert validate({"title": "Controller de usuários"}, FILE_LIMITS) == []
    errs = validate({"summary": "x" * 200, "tags": list("abcdefg"), "color": "red"}, FILE_LIMITS)
    assert any(e.startswith("summary: ~57 tok > 40 (17 over)") for e in errs)
    assert any(e.startswith("tags: 7 > 6") for e in errs) and any("unknown fields: color" in e for e in errs)
    assert validate({"flow": "a > b" * 70}, CHAIN_LIMITS)
    assert chain_key("  User ") == "user" and chain_key("get   /api/users") == "GET /api/users"


def test_describe_cycle(project_copy, capsys, monkeypatch):
    root = str(project_copy("express-api"))
    base = ["--root", root, "--engine", "python"]

    code, out = run(capsys, "todo", "--limit", "3", *base)
    assert code == 0 and "0/14 described" in out
    first = [line for line in out.splitlines() if line.startswith(" ") and "[" in line][0]
    assert "user.controller.ts [controller]" in first  # most important first

    code, out = run(capsys, "describe", "user.controller.ts", "--title", "Controller de usuários",
                    "--summary", "CRUD de usuário; delega ao UserService", "--scope", "user", "--tags", "crud,http", *base)
    assert code == 0 and "ok src/controllers/user.controller.ts" in out and "tok per read" in out

    batch = json.dumps([
        {"file": "src/services/user.service.ts", "title": "Serviço de usuários", "summary": "Hash de senha e gravação"},
        {"file": "src/services/user.service.ts", "symbol": "UserService.create", "title": "Cria usuário com hash"},
        {"file": "src/routes/user.routes.ts", "title": "Rotas", "summary": "palavra " * 40},
        {"chain": "user", "title": "Usuários", "summary": "Cadastro e consulta", "flow": "rota > controller > service > repo"},
    ], ensure_ascii=False)
    # --batch - reads bytes as UTF-8 even when the console code page is cp1252
    code, out = run(capsys, "describe", "--batch", "-", *base, stdin=batch, monkeypatch=monkeypatch)
    assert code == 1 and "3 saved, 1 rejected" in out and "REJECTED src/routes/user.routes.ts: summary" in out
    code, out = run(capsys, "describe", "--json", json.dumps([{"file": "src/lib/db.ts", "title": "Banco simulado"}]), *base)
    assert code == 0 and "1 saved" in out

    code, out = run(capsys, "analyze", "user", *base)
    assert "CHAIN user - Usuários: Cadastro e consulta" in out
    assert "user.controller.ts - Controller de usuários: CRUD de usuário; delega ao UserService" in out
    assert "NO-NOTE" in out and "user.controller.ts," not in out.split("NO-NOTE")[1]

    code, out = run(capsys, "file", "user.service.ts", *base)
    assert "NOTE Serviço de usuários" in out and "NOTE #UserService.create - Cria usuário com hash" in out

    code, out = run(capsys, "endpoint", "POST", "/api/users", *base)
    assert "UserService.create - Cria usuário com hash" in out  # symbol note wins over the file note

    code, out = run(capsys, "find", "senha", *base)
    assert "NOTES" in out and "user.service.ts - Serviço de usuários" in out

    # edit a described file -> its note and the chain become stale
    from pathlib import Path
    p = Path(root) / "src" / "services" / "user.service.ts"
    p.write_text(p.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
    code, out = run(capsys, "update", *base)
    assert "CHANGED src/services/user.service.ts" in out
    assert "STALE NOTES src/services/user.service.ts" in out and "STALE CHAINS user stale (1 changed)" in out
    code, out = run(capsys, "analyze", "user", *base)
    assert "CHAIN user* - " in out and "user.service.ts* - Serviço de usuários" in out
    code, out = run(capsys, "update", "user", *base)
    assert "chain note: stale (1 changed)" in out and "user.service.ts [service]" in out and "STALE was: Serviço" in out
    assert "CHAIN user: stale" in out

    # a new file entering the chain also makes it stale
    (Path(root) / "src" / "services" / "user.extra.ts").write_text(
        "import { UserService } from './user.service.js'\nexport const x = new UserService()\n", encoding="utf-8")
    ctl = Path(root) / "src" / "controllers" / "user.controller.ts"
    ctl.write_text("import { x } from '../services/user.extra.js'\n" + ctl.read_text(encoding="utf-8"), encoding="utf-8")
    code, out = run(capsys, "update", "user", *base)
    assert "added" in out.splitlines()[0]

    code, out = run(capsys, "status", *base)
    assert "NOTES 3/" in out and "chains 1 (1 stale)" in out


def test_note_scope_seeds_analyze(project_copy, capsys):
    root = str(project_copy("express-api"))
    base = ["--root", root, "--engine", "python"]
    run(capsys, "describe", "src/lib/hash.ts", "--title", "Hash", "--scope", "seguranca", *base)
    code, out = run(capsys, "analyze", "seguranca", *base)
    assert code == 0 and "src/lib/hash.ts - Hash" in out


def test_progressive_reduction(project_copy, capsys):
    root = str(project_copy("express-api"))
    base = ["--root", root, "--engine", "python"]
    run(capsys, "describe", "user.controller.ts", "--title", "Controller de usuários",
        "--summary", "CRUD de usuário; valida payload e delega ao UserService e ao MailService", *base)
    code, full = run(capsys, "analyze", "user", *base)
    assert "detail 3/3" in full
    for budget in (230, 160):
        code, out = run(capsys, "analyze", "user", "--budget", str(budget), *base)
        assert tk.estimate(out) <= budget + 5
        assert "detail 3/3" not in out
    code, out = run(capsys, "analyze", "user", "--budget", "230", *base)
    assert "Controller de usuários" in out and "valida payload" not in out  # titles only


def test_ai_command(monkeypatch, capsys, tmp_path):
    cmd = update.ai_command("claude", "user", "Brazilian Portuguese")
    assert cmd[:2] == ["claude", "-p"] and "Bash(nimblegraph:*)" in cmd and "Read" in cmd
    assert 'nimblegraph update "user"' in cmd[2] and "nimblegraph describe --json" in cmd[2] and "Brazilian Portuguese" in cmd[2]
    assert "nimblegraph todo --limit" in update.ai_command("claude", None, "English")[2]
    monkeypatch.setattr(update, "find_claude", lambda: None)
    assert update.run_ai(tmp_path, "user", "x") == 3
    assert "/nimblegraph update user" in capsys.readouterr().out
    monkeypatch.setattr(update, "find_claude", lambda: "C:/x/claude.exe")
    assert update.run_ai(tmp_path, "user", "x", dry_run=True) == 0
    assert capsys.readouterr().out.startswith("C:/x/claude.exe -p")


def test_find_claude_prefers_newest_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    for v in ("2.1.9", "2.1.287", "2.0.500"):
        b = tmp_path / ".vscode" / "extensions" / f"anthropic.claude-code-{v}-win32-x64" / "resources" / "native-binary"
        b.mkdir(parents=True)
        (b / "claude.exe").write_text("")
    assert "2.1.287" in update.find_claude()
