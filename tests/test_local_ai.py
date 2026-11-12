"""Local model notes, against a fake Ollama / OpenAI-compatible server (no model needed)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nimblegraph import cli, local_ai
from nimblegraph import tokens as tk
from nimblegraph.config import load
from nimblegraph.notes import FILE_LIMITS, Notes

ANSWER = {
    "title": "Controller de usuários do sistema",
    "summary": "Expõe as rotas de usuário e delega a criação ao UserService, que grava no repositório e envia e-mail " * 2,
    "resp": "HTTP de entrada e saída; regras de negócio ficam no service",
    "scope": "User",
    "flow": "req > valida > service.create > 201",
    "tags": ["CRUD", "Express Route", "crud", "x" * 40],
}


class FakeServer:
    def __init__(self, kind: str):
        self.kind = kind
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, data, code=200):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if outer.kind == "ollama" and self.path == "/api/version":
                    return self._json({"version": "0.35.1"})
                if outer.kind == "ollama" and self.path == "/api/tags":
                    return self._json({"models": [{"name": "fake-model:latest"}]})
                if outer.kind == "openai" and self.path == "/v1/models":
                    return self._json({"data": [{"id": "fake-model"}]})
                return self._json({"error": "nope"}, 404)

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, **body})
                is_chain = "whole feature" in body["messages"][0]["content"]
                answer = {"title": "Usuários", "summary": "Cadastro de usuários", "flow": "rota > service"} if is_chain else ANSWER
                if self.path == "/api/chat":
                    return self._json({"message": {"content": json.dumps(answer)}, "eval_count": 50})
                if self.path == "/v1/chat/completions":
                    return self._json({"choices": [{"message": {"content": "```json\n" + json.dumps(answer) + "\n```"}}],
                                       "usage": {"completion_tokens": 50}})
                return self._json({"error": "nope"}, 404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture(params=["ollama", "openai"])
def fake(request):
    srv = FakeServer(request.param)
    yield srv
    srv.close()


def test_fit_note_respects_limits():
    fields = local_ai.fit_note(ANSWER, FILE_LIMITS)
    for k, lim in FILE_LIMITS.items():
        assert tk.estimate(fields[k]) <= lim, k
    assert fields["scope"] == "user"
    assert fields["tags"] == ["crud", "express-route"]  # lowercased, deduplicated, too-long tag dropped
    assert local_ai.parse_json('texto ```json\n{"a": 1}\n``` fim') == {"a": 1}


def test_describe_files_and_chain(fake, project_copy, capsys):
    root = project_copy("express-api")
    cfg = load(root)
    (cfg.dir).mkdir(parents=True, exist_ok=True)
    (cfg.dir / "config.toml").write_text(f'[local_ai]\nurl = "{fake.url}"\nmodel = "fake-model"\n', encoding="utf-8")
    base = ["--root", str(root), "--engine", "python"]

    assert cli.main(["local", "status", *base]) == 0
    out = capsys.readouterr().out
    assert f"{fake.kind} running" in out and "loaded  yes" in out

    assert cli.main(["local", "describe", "--limit", "3", *base]) == 0
    out = capsys.readouterr().out
    assert "[3/3] ok" in out and "@local describe | 3/3 saved" in out
    notes = Notes.load(load(root).notes_path)
    assert len(notes.files) == 3
    note = notes.files["src/controllers/user.controller.ts"]  # most important file first
    assert note["by"] == "local:fake-model" and note["hash"]
    prompt = fake.requests[0]["messages"][1]["content"]
    assert "Routes: " in prompt and "POST /api/users -> userController.create" in prompt and "--- code ---" in prompt

    # a term: the chain's files that still lack notes, then the chain note
    assert cli.main(["update", "user", "--local", *base]) == 0
    out = capsys.readouterr().out
    notes = Notes.load(load(root).notes_path)
    assert notes.chains["user"]["title"] == "Usuários" and notes.chains["user"]["by"] == "local:fake-model"
    assert "src/services/user.service.ts" in notes.files

    # describing again skips up-to-date notes; Claude rewriting a note drops `by`
    assert cli.main(["local", "describe", "--dry-run", "--limit", "50", *base]) == 0
    assert "src/controllers/user.controller.ts" not in capsys.readouterr().out
    assert cli.main(["describe", "user.controller.ts", "--title", "Controller", *base]) == 0
    assert "by" not in Notes.load(load(root).notes_path).files["src/controllers/user.controller.ts"]


def test_server_off_is_reported(project_copy, capsys):
    root = project_copy("nest-api")
    cfg = load(root)
    cfg.dir.mkdir(parents=True, exist_ok=True)
    (cfg.dir / "config.toml").write_text('[local_ai]\nurl = "http://127.0.0.1:9"\n', encoding="utf-8")
    assert cli.main(["local", "status", "--root", str(root)]) == 3
    assert "status  OFF" in capsys.readouterr().out


def test_background_run_status_and_stop(fake, project_copy, capsys):
    import time as _t
    root = project_copy("express-api")
    cfg = load(root)
    cfg.dir.mkdir(parents=True, exist_ok=True)
    (cfg.dir / "config.toml").write_text(f'[local_ai]\nurl = "{fake.url}"\nmodel = "fake-model"\n', encoding="utf-8")
    base = ["--root", str(root), "--engine", "python"]
    assert cli.main(["build", *base]) == 0
    capsys.readouterr()
    assert cli.main(["local", "describe", "--all", "--background", *base]) == 0
    assert "running in background" in capsys.readouterr().out
    log = cfg.dir / "local-describe.log"
    deadline = _t.time() + 60
    while _t.time() < deadline and not (log.is_file() and "@local describe" in log.read_text(encoding="utf-8-sig")):
        _t.sleep(0.5)
    text = log.read_bytes()
    assert text.startswith(b"\xef\xbb\xbf")  # BOM: PowerShell 5.1 shows accents right
    assert cli.main(["local", "status", *base]) == 0
    out = capsys.readouterr().out
    assert "batch   finished" in out and "written by the local model" in out
    assert cli.main(["local", "stop", *base]) == 1  # nothing running anymore
