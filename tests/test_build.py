"""Incremental build, parse cache invalidation and engine fallback."""

from __future__ import annotations

from nimblegraph import config, engine
from nimblegraph import build as builder
from conftest import build_fixture, edges_of, table


def test_incremental_only_reparses_changed(project_copy):
    root = project_copy("express-api")
    cfg, first = build_fixture("express-api", root=root)
    assert first.parsed == first.files == 15
    again = builder.run(cfg)
    assert again.parsed == 0 and not again.dirty

    svc = root / "src" / "services" / "mail.service.ts"
    svc.write_text(svc.read_text() + "\nexport const extra = 1\n", encoding="utf-8")
    (root / "src" / "lib" / "new.ts").write_text("import { db } from './db.js'\nexport const n = db\n", encoding="utf-8")
    (root / "src" / "lib" / "hash.ts").unlink()
    r = builder.run(cfg)
    assert r.parsed == 2
    assert r.changed == ["src/services/mail.service.ts"]
    assert r.added == ["src/lib/new.ts"]
    assert r.removed == ["src/lib/hash.ts"]
    e = edges_of(cfg)
    assert ("src/lib/new.ts", "src/lib/db.ts", "import", "db", None) in e
    # the import of the deleted file is now unresolved
    assert ("src/services/user.service.ts", "../lib/hash.js") in table(cfg, "SELECT file, spec FROM unresolved")


def test_config_change_invalidates_cache(project_copy):
    root = project_copy("nest-api")
    cfg, _ = build_fixture("nest-api", root=root)
    tsconfig = root / "tsconfig.json"
    tsconfig.write_text(tsconfig.read_text().replace('"baseUrl": "./"', '"baseUrl": "./src"'), encoding="utf-8")
    r = builder.run(cfg)
    assert r.parsed == r.files  # resolution inputs changed: everything is parsed again


def test_full_flag_reparses(project_copy):
    root = project_copy("next-app")
    cfg, _ = build_fixture("next-app", root=root)
    assert builder.run(cfg, full=True).parsed == builder.run(cfg, full=True).files


def test_falls_back_to_python_without_node(project_copy, monkeypatch):
    root = project_copy("vite-react")
    monkeypatch.setattr(engine, "find_node", lambda refresh=False: None)
    cfg, r = build_fixture("vite-react", root=root)
    assert r.engine.startswith("python")
    assert any("no usable Node" in w for w in r.warnings)
    assert ("src/components/ui/Button.tsx", "src/lib/cn.ts", "import", "cn", None) in edges_of(cfg)


def test_node_crash_falls_back_per_file(project_copy, monkeypatch):
    """A Node process that dies produces no records: those files go to the Python parser."""
    import sys
    root = project_copy("express-api")
    dead = engine.NodeRuntime([sys.executable, "-c", "import sys; sys.exit(3)"], "v24.0.0", "test")
    monkeypatch.setattr(engine, "select", lambda r, prefer="auto": (engine.NodeEngine(dead), None))
    cfg = config.load(root)
    r = builder.run(cfg)
    assert r.parsed == r.files
    assert any("node engine" in w for w in r.warnings)
    engines_used = {e for (e,) in table(cfg, "SELECT DISTINCT engine FROM parsed")}
    assert engines_used == {"python"}
    assert ("src/server.ts", "src/routes/index.ts", "import", "default", None) in edges_of(cfg)


def test_gitignore_and_default_ignores(project_copy):
    root = project_copy("express-api")
    (root / ".gitignore").write_text("generated/\n*.gen.ts\n", encoding="utf-8")
    for rel in ("generated/a.ts", "src/b.gen.ts", "dist/c.js", "node_modules/x/index.js", "src/d.d.ts", "src/e.min.js"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("export const x = 1\n", encoding="utf-8")
    (root / "src" / "sub").mkdir()
    (root / "src" / "sub" / ".gitignore").write_text("skip.ts\n", encoding="utf-8")
    (root / "src" / "sub" / "skip.ts").write_text("export {}\n", encoding="utf-8")
    (root / "src" / "sub" / "keep.ts").write_text("export {}\n", encoding="utf-8")
    cfg, r = build_fixture("express-api", root=root)
    files = {p for (p,) in table(cfg, "SELECT path FROM files")}
    assert "src/sub/keep.ts" in files
    assert not files & {"generated/a.ts", "src/b.gen.ts", "dist/c.js", "node_modules/x/index.js", "src/d.d.ts",
                        "src/e.min.js", "src/sub/skip.ts"}


def test_find_root(tmp_path, monkeypatch):
    from nimblegraph.config import RootError, find_root
    import pytest as _pt
    mono = tmp_path / "home" / "work" / "mono"
    (mono / "apps" / "web" / "src").mkdir(parents=True)
    (mono / "package.json").write_text('{"workspaces": ["apps/*"]}', encoding="utf-8")
    (mono / "apps" / "web" / "package.json").write_text('{"name": "web"}', encoding="utf-8")
    (tmp_path / "home" / ".git").mkdir()  # dotfiles repo in the home folder
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    assert find_root(mono / "apps" / "web" / "src") == mono.resolve()
    (mono / "apps" / "web" / ".nimblegraph").mkdir()
    assert find_root(mono / "apps" / "web" / "src") == (mono / "apps" / "web").resolve()
    with _pt.raises(RootError):
        find_root(tmp_path / "home")
