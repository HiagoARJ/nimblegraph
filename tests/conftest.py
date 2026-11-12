from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nimblegraph import build, config, engine
from nimblegraph.query import Graph
from nimblegraph.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
ALL_FIXTURES = ["vite-react", "express-api", "nest-api", "next-app", "monorepo", "tanstack-app", "ddd-nest"]


def node_ready() -> bool:
    rt = engine.find_node()
    return bool(rt) and engine.tool_typescript() is not None


needs_node = pytest.mark.skipif(not node_ready(), reason="Node engine not available (run `nimblegraph setup`)")


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    """Indexes go to a temp dir, never into tests/fixtures."""
    monkeypatch.setenv("NIMBLEGRAPH_INDEX_DIR", str(tmp_path / "index"))


def fixture_root(name: str) -> Path:
    return FIXTURES / name


def build_fixture(name: str, engine_pref: str = "auto", full: bool = True, root: Path | None = None):
    cfg = config.load(root or fixture_root(name))
    res = build.run(cfg, engine_pref=engine_pref, full=full)
    return cfg, res


def edges_of(cfg) -> set[tuple]:
    st = Store(cfg.db_path)
    try:
        return set(st.query("SELECT src, dst, kind, names, via FROM edges"))
    finally:
        st.close()


def table(cfg, sql: str) -> list[tuple]:
    st = Store(cfg.db_path)
    try:
        return st.query(sql)
    finally:
        st.close()


def load_graph(cfg) -> Graph:
    st = Store(cfg.db_path)
    try:
        return Graph.load(st)
    finally:
        st.close()


@pytest.fixture
def project_copy(tmp_path):
    """Copies a fixture to a temp folder (for tests that edit files)."""

    def make(name: str) -> Path:
        dst = tmp_path / "proj" / name
        shutil.copytree(fixture_root(name), dst)
        return dst

    return make
