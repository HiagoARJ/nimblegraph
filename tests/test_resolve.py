"""Import resolution: tsconfig paths/references, barrels, workspaces, aliases; same graph from both engines."""

from __future__ import annotations

import pytest

from nimblegraph import config, scanner
from nimblegraph.resolve import Resolver, read_jsonc
from conftest import ALL_FIXTURES, build_fixture, edges_of, fixture_root, needs_node, table

ENGINES = [pytest.param("node", marks=needs_node), "python"]


@pytest.mark.parametrize("eng", ENGINES)
def test_vite_react_graph(eng):
    cfg, res = build_fixture("vite-react", eng)
    e = edges_of(cfg)
    ui = "src/components/ui/index.ts"
    # tsconfig references -> tsconfig.app.json paths (@/*)
    assert ("src/pages/UsersPage.tsx", "src/hooks/useUsers.ts", "import", "useUsers", None) in e
    # barrel followed to the real file, with the barrel kept as `via` (export {..} and export *)
    assert ("src/components/Layout.tsx", "src/components/ui/Button.tsx", "import", "Button", ui) in e
    assert ("src/components/Layout.tsx", "src/components/ui/Card.tsx", "import", "Card", ui) in e
    assert ("src/components/Layout.tsx", "src/components/ui/Button.tsx", "type", "ButtonProps", ui) in e
    # vite alias + ESM `.js` specifier pointing to a .ts file
    assert ("src/components/ui/Button.tsx", "src/lib/cn.ts", "import", "cn", None) in e
    assert ("src/components/ui/Card.tsx", "src/lib/cn.ts", "import", "cn", None) in e
    # lazy() local renders the dynamic target
    assert ("src/App.tsx", "src/pages/SettingsPage.tsx", "dynamic", "", None) in e
    assert ("src/App.tsx", "src/pages/SettingsPage.tsx", "renders", "SettingsPage", None) in e
    assert res.unresolved == 0
    ext = {p for (p,) in table(cfg, "SELECT DISTINCT pkg FROM externals")}
    assert {"react", "react-router-dom", "axios", "swr", "path"} <= ext
    assert "~lib" not in ext


@pytest.mark.parametrize("eng", ENGINES)
def test_monorepo_workspaces(eng):
    cfg, _ = build_fixture("monorepo", eng)
    e = edges_of(cfg)
    barrel = "packages/shared/src/index.ts"
    # package.json main/exports -> dist -> mapped back to src, then through the barrel
    assert ("apps/web/src/components/Price.tsx", "packages/shared/src/money.ts", "import", "formatMoney", barrel) in e
    # `export { slugify as toSlug }` keeps the imported name
    assert ("apps/web/src/components/Price.tsx", "packages/shared/src/utils/strings.ts", "import", "toSlug", barrel) in e
    # exports pattern "./utils/*"
    assert ("apps/web/src/components/Price.tsx", "packages/shared/src/utils/dates.ts", "import", "today", None) in e
    # package.json "imports" (#db) and directory index
    assert ("apps/api/src/index.ts", "apps/api/src/db.ts", "import", "db", None) in e
    assert ("apps/api/src/index.ts", "apps/api/src/helpers/index.ts", "import", "helper", None) in e
    unresolved = table(cfg, "SELECT file, spec FROM unresolved")
    assert unresolved == [("apps/web/src/components/Price.tsx", "@/lib/missing")]
    pkgs = {p for (p,) in table(cfg, "SELECT DISTINCT pkg FROM files")}
    assert {"@mono/web", "@mono/api", "@mono/shared"} <= pkgs


@pytest.mark.parametrize("eng", ENGINES)
def test_backend_fixtures(eng):
    cfg, _ = build_fixture("express-api", eng)
    e = edges_of(cfg)
    assert ("src/server.ts", "src/routes/index.ts", "import", "default", None) in e  # NodeNext './x.js'
    assert ("src/legacy.cjs", "src/middlewares/auth.ts", "import", "requireAuth", None) in e  # require()
    assert ("tests/user.e2e.spec.ts", "src/server.ts", "import", "default", None) in e
    cfg, _ = build_fixture("nest-api", eng)
    e = edges_of(cfg)
    assert ("src/main.ts", "src/logging.interceptor.ts", "import", "LoggingInterceptor", None) in e  # baseUrl
    assert ("src/users/users.service.ts", "src/users/dto/create-user.dto.ts", "type", "CreateUserDto", None) in e


@needs_node
@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_engines_build_same_graph(name):
    cfg_n, _ = build_fixture(name, "node")
    node_edges = edges_of(cfg_n)
    node_ext = set(table(cfg_n, "SELECT * FROM externals"))
    cfg_p, _ = build_fixture(name, "python")
    assert edges_of(cfg_p) == node_edges
    assert set(table(cfg_p, "SELECT * FROM externals")) == node_ext


def test_resolver_units(tmp_path):
    root = fixture_root("monorepo")
    sr = scanner.scan(config.load(root))
    r = Resolver(root, set(sr.files), sr.packages)
    assert r.resolve("@mono/shared", "apps/api/src/index.ts").target == "packages/shared/src/index.ts"
    assert r.resolve("react", "apps/web/src/main.tsx").kind == "ext"
    assert r.resolve("node:fs", "apps/web/src/main.tsx").target == "fs"
    assert r.resolve("./styles.css", "apps/web/src/main.tsx").kind == "asset"
    assert r.resolve("./nope", "apps/web/src/main.tsx").kind == "unresolved"
    assert r.map_build_to_src("packages/shared/dist/utils/strings.js") == "packages/shared/src/utils/strings.ts"
    p = tmp_path / "tsconfig.json"
    p.write_text('{\n // comment\n "compilerOptions": { "paths": { "@/*": ["./src/*",], }, /* x */ },\n}', encoding="utf-8")
    assert read_jsonc(p) == {"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}


@pytest.mark.parametrize("eng", ["python", pytest.param("node", marks=needs_node)])
def test_config_alias_wins_over_installed_package(tmp_path, eng):
    # a package installed in node_modules (built dist) but served by a sibling source folder in the workspace:
    # the [aliases] of config.toml must win, otherwise the Node engine marks the import external
    root = tmp_path / "ws"
    files = {
        ".nimblegraph/config.toml": '[aliases]\n"@acme/ui" = "ui/src"\n',
        "app/package.json": '{"name": "app", "dependencies": {"@acme/ui": "1.0.0"}}',
        "app/tsconfig.json": '{"compilerOptions": {"module": "esnext", "moduleResolution": "bundler"}}',
        "app/src/main.ts": 'import { Button } from "@acme/ui";\nexport const b = Button;\n',
        "app/node_modules/@acme/ui/package.json": '{"name": "@acme/ui", "types": "./dist/index.d.ts"}',
        "app/node_modules/@acme/ui/dist/index.d.ts": "export declare const Button: string;\n",
        "ui/package.json": '{"name": "@acme/ui-src"}',
        "ui/src/index.ts": 'export const Button = "button";\n',
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    cfg, _ = build_fixture("", eng, root=root)
    assert ("app/src/main.ts", "ui/src/index.ts") in {(s, d) for s, d, *_ in edges_of(cfg)}
