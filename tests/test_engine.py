"""Node detection: latest LTS via fnm first, then PATH, then any Node >= 20; cached LTS when offline."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from nimblegraph import engine


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Isolated ~/.nimblegraph cache and a fake fnm install with v20 (default) and v24."""
    monkeypatch.setattr(engine, "_cache_file", lambda: tmp_path / "node.json")
    fnm_root = tmp_path / "fnm"
    exes = {}
    for v in ("v20.20.2", "v24.21.0"):
        exe = fnm_root / "node-versions" / v / "installation" / "node.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        exes[v] = exe
    monkeypatch.setenv("FNM_DIR", str(fnm_root))
    monkeypatch.setattr(engine, "find_fnm", lambda: None)
    monkeypatch.setattr(engine.shutil, "which", lambda name: None)

    def fake_version(cmd):
        for v, exe in exes.items():
            if cmd == [str(exe)]:
                return v
        return None

    monkeypatch.setattr(engine, "_node_version", fake_version)
    return exes


def test_prefers_lts_major_over_default(fake_env, monkeypatch):
    monkeypatch.setattr(engine, "latest_lts", lambda refresh=False, offline=False: {"version": "v24.21.0"})
    rt = engine.find_node(refresh=True)
    assert rt.version == "v24.21.0" and rt.via == "fnm"
    assert rt.cmd == [str(fake_env["v24.21.0"])]


def test_older_node_used_when_lts_missing(fake_env, monkeypatch):
    fake_env["v24.21.0"].unlink()
    monkeypatch.setattr(engine, "latest_lts", lambda refresh=False, offline=False: {"version": "v24.21.0"})
    monkeypatch.setattr(engine, "_node_version", lambda cmd: "v20.20.2" if "v20" in cmd[0] else None)
    rt = engine.find_node(refresh=True)
    assert rt.version == "v20.20.2"
    monkeypatch.setattr(engine, "has_typescript", lambda root: True)
    _, warn = engine.select(Path("."), "auto")
    assert warn and "not the latest LTS" in warn


def test_latest_lts_parses_fnm_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_cache_file", lambda: tmp_path / "node.json")
    monkeypatch.setattr(engine, "find_fnm", lambda: "fnm")
    out = "v22.20.0 (Jod)\nv24.20.0 (Krypton)\nv24.21.0 (Krypton)\n"
    calls = []

    def fake_run(cmd, timeout=15, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(engine, "_run", fake_run)
    lts = engine.latest_lts(refresh=True)
    assert lts["version"] == "v24.21.0" and lts["codename"] == "Krypton"
    # within the TTL the cache answers without calling fnm again
    assert engine.latest_lts()["version"] == "v24.21.0" and len(calls) == 1


def test_latest_lts_offline_uses_cache_or_default(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_cache_file", lambda: tmp_path / "node.json")
    monkeypatch.setattr(engine, "find_fnm", lambda: None)
    assert engine.latest_lts(offline=True)["version"] == "v24"
    engine._save_cache({"lts": {"version": "v26.1.0", "codename": "X", "checked": time.time() - 30 * 86400}})
    assert engine.latest_lts(offline=True)["version"] == "v26.1.0"  # stale but offline: still used


def test_python_engine_when_forced():
    eng, warn = engine.select(Path("."), "python")
    assert eng.name == "python" and warn is None
