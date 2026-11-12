"""`nimblegraph ui`: stdlib HTTP server for the React UI (ui/dist) and its JSON API, with live updates over SSE.

GET  /api/meta                 root, engine, whether Claude Code is available
GET  /api/graph                nodes, links, routes, chain notes, scopes
GET  /api/file?path=           one file: note, exports, imports, importers, routes, API calls
GET  /api/chain?target=        the chain of a term/route/file (same algorithm as `nimblegraph analyze`/`flow`)
GET  /api/endpoint?route=      the files of an endpoint in request order (same as `nimblegraph endpoint`)
GET  /api/search?q=            paths, symbols, routes and notes
POST /api/update {target, ai}  rebuild + what to describe; with ai=true runs `claude -p` in the background
GET  /api/events               SSE: graph | notes | ai | error
"""

from __future__ import annotations

import json
import mimetypes
import queue
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from nimblegraph import __version__, api, build, query, update
from nimblegraph import notes as notes_mod
from nimblegraph.config import Config
from nimblegraph.query import Graph
from nimblegraph.render import finish
from nimblegraph.store import Store

UI_DIST = Path(__file__).parent / "ui" / "dist"
NO_UI = """<!doctype html><meta charset="utf-8"><title>nimblegraph</title>
<body style="font:14px system-ui;background:#1e1e1e;color:#ddd;padding:40px">
<h2>nimblegraph UI is not built yet</h2><p>Run <code>nimblegraph setup</code> (needs Node) and reload. The API is live at
<a style="color:#4fc1ff" href="/api/graph">/api/graph</a>.</p></body>"""


class App:
    def __init__(self, cfg: Config, engine_pref: str = "auto", depth: int = 4):
        self.cfg = cfg
        self.engine_pref = engine_pref
        self.depth = depth
        self.lock = threading.RLock()
        self.subscribers: list[queue.Queue] = []
        self.ai: dict = {"status": "idle", "target": None, "lines": []}
        self.g: Graph | None = None
        self.notes: notes_mod.Notes | None = None
        self._graph_cache: bytes | None = None
        build.run(cfg, engine_pref=engine_pref)
        self.reload()

    def reload(self) -> None:
        store = Store(self.cfg.db_path)
        try:
            g = Graph.load(store)
        finally:
            store.close()
        n = notes_mod.load_for(self.cfg, g)
        with self.lock:
            self.g, self.notes, self._graph_cache = g, n, None

    def publish(self, event: str, data: dict) -> None:
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            q.put((event, data))

    def graph_json(self) -> bytes:
        with self.lock:
            if self._graph_cache is None:
                from nimblegraph import arch
                payload = api.graph_payload(self.g, self.notes, self.cfg.root.name, arch.load_rules(self.cfg))
                payload["claude"] = update.find_claude() is not None
                payload["local"] = self.local_ready()
                self._graph_cache = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            return self._graph_cache

    # ------------------------------------------------------------ local model
    def local_ready(self) -> bool:
        """Local model server up and the model loaded (cached for 30 s, it is asked on every graph reload)."""
        now = time.time()
        if now - getattr(self, "_local_checked", 0) > 30:
            from nimblegraph import local_ai
            client = local_ai.client_for(self.cfg)
            client.timeout = 2
            try:
                client.detect()
                self._local_ok = client.has_model()
            except local_ai.LocalAIError:
                self._local_ok = False
            self._local_checked = now
        return self._local_ok

    def start_local(self, target: str | None) -> dict:
        from nimblegraph import local_ai
        with self.lock:
            if self.ai["status"] == "running":
                return {"started": False, "message": "notes are already being written"}
            self.ai = {"status": "running", "target": target, "lines": [], "started": time.time(), "engine": "local"}
        g, notes = self.g, self.notes
        if target:
            files = query.chain_files(g, target, self.depth, notes)
            paths = local_ai.select_files(g, notes, files)
            chains = [notes_mod.chain_key(target)]
        else:
            paths, chains = local_ai.select_files(g, notes)[:15], []
        threading.Thread(target=self._run_local, args=(paths, chains, target), daemon=True).start()
        self.publish("ai", {"status": "running", "target": target, "engine": "local"})
        return {"started": True, "message": f"local model describing {len(paths)} files"}

    def _run_local(self, paths, chains, target) -> None:
        from nimblegraph import local_ai
        client = local_ai.client_for(self.cfg)
        status = "done"

        def on_event(ev):
            line = f"[{ev['i']}/{ev['total']}] {'ok' if ev['ok'] else 'ERR'} {ev['path']} - {ev['title'] or ev['error']}"
            self.ai["lines"].append(line)
            self.publish("ai", {"status": "running", "target": target, "engine": "local", "line": line,
                                "i": ev["i"], "total": ev["total"], "eta": ev["eta"]})
        try:
            client.detect()
            res = local_ai.run_batch(self.cfg, self.g, self.notes, paths, client, self.cfg["notes_language"], on_event,
                                     None, chains)
            if res["ok"] < res["done"]:
                status = "error" if not res["ok"] else "done"
        except local_ai.LocalAIError as e:
            status = "error"
            self.ai["lines"].append(str(e))
        self.ai["status"] = status
        self.reload()
        self.publish("notes", {"at": time.time()})
        self.publish("ai", {"status": status, "target": target, "engine": "local", "lines": self.ai["lines"][-5:]})

    # ------------------------------------------------------------ update / AI
    def do_update(self, target: str | None, ai: bool, local: bool = False) -> dict:
        r = build.run(self.cfg, engine_pref=self.engine_pref)
        if r.dirty:
            self.reload()
        g, notes = self.g, self.notes
        if target:
            files = query.chain_files(g, target, self.depth, notes)
            key = (notes.find_chain(target) or (notes_mod.chain_key(target), None))[0]
            state = notes.chain_state(key, files)
            view = update.target_view(g, notes, target, files, key, state)
            todo = update.todo_items(g, notes, files)
        else:
            view = update.todo_view(g, notes, 15)
            todo = update.todo_items(g, notes)[:15]
            state = None
        out = {"text": finish(view, 3000), "to_describe": todo, "chain_state": state, "ai": None}
        if local:
            out["ai"] = self.start_local(target)
        elif ai:
            out["ai"] = self.start_ai(target)
        return out

    def start_ai(self, target: str | None) -> dict:
        claude = update.find_claude()
        if not claude:
            return {"started": False, "message": update.manual_instruction(target),
                    "command": f"/nimblegraph update {target}" if target else "/nimblegraph update"}
        with self.lock:
            if self.ai["status"] == "running":
                return {"started": False, "message": "Claude is already updating notes"}
            self.ai = {"status": "running", "target": target, "lines": [], "started": time.time()}
        cmd = update.ai_command(claude, target, self.cfg["notes_language"])
        threading.Thread(target=self._run_ai, args=(cmd, target), daemon=True).start()
        self.publish("ai", {"status": "running", "target": target})
        return {"started": True, "message": "Claude Code is writing the notes"}

    def _run_ai(self, cmd: list[str], target: str | None) -> None:
        import os
        env = dict(os.environ)
        env.setdefault("MSYS_NO_PATHCONV", "1")
        status = "done"
        try:
            proc = subprocess.Popen(cmd, cwd=self.cfg.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.ai["lines"].append(line)
                    self.publish("ai", {"status": "running", "target": target, "line": line[:300]})
            if proc.wait() != 0:
                status = "error"
        except OSError as e:
            status = "error"
            self.ai["lines"].append(str(e))
        self.ai["status"] = status
        self.publish("ai", {"status": status, "target": target, "lines": self.ai["lines"][-5:]})


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8", cache: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200):
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/api/meta":
                    return self._json({"root": str(app.cfg.root), "name": app.cfg.root.name, "version": __version__,
                                       "engine": app.g.meta.get("engine"), "claude": update.find_claude() is not None,
                                       "ai": {k: v for k, v in app.ai.items() if k != "lines"}})
                if u.path == "/api/graph":
                    return self._send(200, app.graph_json())
                if u.path == "/api/file":
                    data = api.file_payload(app.g, app.notes, q.get("path", ""))
                    return self._json(data) if data else self._json({"error": "file not found"}, 404)
                if u.path == "/api/chain":
                    return self._json(api.chain_payload(app.g, app.notes, q.get("target", ""), app.depth))
                if u.path == "/api/endpoint":
                    return self._json(api.endpoint_payload(app.g, app.notes, q.get("route", ""), app.depth))
                if u.path == "/api/arch":
                    with app.lock:
                        g = app.g
                    return self._json(api.arch_payload(app.cfg, g))
                if u.path == "/api/search":
                    return self._json(api.search_payload(app.g, app.notes, q.get("q", "")))
                if u.path == "/api/events":
                    return self._events()
                return self._static(u.path)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return None
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)}, 500)

        def do_POST(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path != "/api/update":
                return self._json({"error": "not found"}, 404)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            try:
                return self._json(app.do_update(body.get("target") or None, bool(body.get("ai")),
                                                bool(body.get("local"))))
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)}, 500)

        def _events(self):
            q: queue.Queue = queue.Queue()
            with app.lock:
                app.subscribers.append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(b"event: hello\ndata: {}\n\n")
                self.wfile.flush()
                while True:
                    try:
                        event, data = q.get(timeout=15)
                        msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        msg = ": ping\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                with app.lock:
                    if q in app.subscribers:
                        app.subscribers.remove(q)
            self.close_connection = True

        def _static(self, path: str):
            if not UI_DIST.is_dir():
                return self._send(200, NO_UI.encode("utf-8"), "text/html; charset=utf-8")
            rel = path.lstrip("/") or "index.html"
            target = (UI_DIST / rel).resolve()
            if not str(target).startswith(str(UI_DIST.resolve())) or not target.is_file():
                target = UI_DIST / "index.html"  # SPA fallback
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            return self._send(200, target.read_bytes(), ctype, cache="/assets/" in path)

    return Handler


def serve(cfg: Config, port: int = 4777, open_browser: bool = True, engine_pref: str = "auto", watch: bool = True,
          host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, App]:
    from nimblegraph.watch import Watcher
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    app = App(cfg, engine_pref)
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer((host, p), make_handler(app))
            break
        except OSError:
            continue
    if server is None:
        raise OSError(f"no free port between {port} and {port + 19}")
    server.daemon_threads = True
    if watch:
        app.watcher = Watcher(app)
        app.watcher.start()
    url = f"http://{host}:{server.server_address[1]}/"
    app.url = url
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    return server, app
