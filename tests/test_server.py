"""UI API: JSON endpoints and live updates (SSE) with the server running in a thread."""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request

import pytest

from nimblegraph import config, server, update


@pytest.fixture
def live(project_copy, monkeypatch):
    monkeypatch.setattr(update, "find_claude", lambda: None)
    root = project_copy("next-app")
    srv, app = server.serve(config.load(root), port=48700, open_browser=False, engine_pref="python", watch=True)
    app.watcher.interval = 0.2
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield root, app, f"http://127.0.0.1:{srv.server_address[1]}"
    app.watcher.stop()
    srv.shutdown()
    srv.server_close()


def get(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def post(url: str, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def test_graph_file_chain_endpoint_search(live):
    root, app, base = live
    g = get(base + "/api/graph")
    ids = {n["id"]: n for n in g["nodes"]}
    assert ids["components/UserTable.tsx"]["layer"] == "component"
    assert {"source": "components/UserTable.tsx", "target": "app/api/users/route.ts", "kinds": ["calls_api"]} in g["links"]
    assert any(r["method"] == "GET" and r["path"] == "/api/users" for r in g["routes"])
    assert g["stats"]["files"] == len(g["nodes"]) and g["claude"] is False

    f = get(base + "/api/file?path=components/UserTable.tsx")
    assert f["calls"][0]["target"] == "app/api/users/route.ts" and f["importers"][0]["path"].endswith("users/page.tsx")
    assert get(base + "/api/file?path=UserTable")["path"] == "components/UserTable.tsx"

    ch = get(base + "/api/chain?target=users")
    assert "app/api/users/route.ts" in ch["files"]
    assert any(s["path"] == "components/UserTable.tsx" for s in ch["flow"])
    assert any(lk["label"] == "GET /api/users" for lk in ch["links"])

    ep = get(base + "/api/endpoint?route=GET%20/api/users")
    steps = [s["step"] for s in ep["steps"]]
    assert steps[0] == "middleware" and "handler" in steps and ep["front"][0][-1]["file"] == "components/UserTable.tsx"
    assert "matches" in get(base + "/api/endpoint?route=users")

    s = get(base + "/api/search?q=user")
    assert "components/UserTable.tsx" in s["files"]

    a = get(base + "/api/arch")
    assert "react" in a["presets"] and a["metrics"]["modules"] and "endpoints" in a["tests"]
    assert {"context", "ring", "roles"} <= set(ids["components/UserTable.tsx"])


def test_update_and_live_events(live):
    root, app, base = live
    events: queue.Queue = queue.Queue()

    def listen():
        with urllib.request.urlopen(base + "/api/events", timeout=30) as r:
            for raw in r:
                line = raw.decode("utf-8").strip()
                if line.startswith("event:"):
                    events.put(line.split(":", 1)[1].strip())

    threading.Thread(target=listen, daemon=True).start()
    assert events.get(timeout=5) == "hello"

    res = post(base + "/api/update", {"target": "users"})
    assert "components/UserTable.tsx" in res["to_describe"] and res["chain_state"] == "missing"
    assert res["text"].startswith("@update users")
    res = post(base + "/api/update", {"target": "users", "ai": True})
    assert res["ai"]["started"] is False and "/nimblegraph update users" in res["ai"]["command"]

    # editing a file triggers a rebuild and a `nimblegraph` event; writing notes triggers `notes`
    p = root / "lib" / "users.ts"
    p.write_text(p.read_text(encoding="utf-8") + "\nexport const extra = 1\n", encoding="utf-8")
    deadline = time.time() + 10
    got = set()
    while time.time() < deadline and "graph" not in got:
        try:
            got.add(events.get(timeout=1))
        except queue.Empty:
            pass
    assert "graph" in got
    from nimblegraph import notes as notes_mod
    n = notes_mod.Notes.load(app.cfg.notes_path)
    n.set_file("lib/users.ts", {"title": "Dados de usuários"}, "x")
    n.save()
    deadline = time.time() + 10
    while time.time() < deadline and "notes" not in got:
        try:
            got.add(events.get(timeout=1))
        except queue.Empty:
            pass
    assert "notes" in got
    g = get(base + "/api/graph")
    node = next(x for x in g["nodes"] if x["id"] == "lib/users.ts")
    assert node["note"]["title"] == "Dados de usuários" and node["stale"] is True  # hash "x" != file hash


def test_static_fallback(live):
    _root, _app, base = live
    with urllib.request.urlopen(base + "/some/client/route", timeout=10) as r:
        assert r.status == 200 and "text/html" in r.headers["Content-Type"]
