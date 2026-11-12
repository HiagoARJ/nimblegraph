"""Architecture governance: the arch model, rules + baseline, naming, DDD, metrics, tests map, contracts, review."""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from nimblegraph import arch, cli, ddd, metrics, naming, review, rules, testmap
from nimblegraph import config as config_mod
from conftest import build_fixture, load_graph

O = "src/modules/orders"
B = "src/modules/billing"


@pytest.fixture
def ddd_graph():
    cfg, _ = build_fixture("ddd-nest")
    return cfg, load_graph(cfg)


def _write(cfg, name: str, text: str) -> None:
    cfg.dir.mkdir(parents=True, exist_ok=True)
    (cfg.dir / name).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------- arch model

def test_contexts_rings_and_roles(ddd_graph):
    cfg, g = ddd_graph
    a = arch.model(g)
    assert a.context[f"{O}/domain/order.ts"] == "orders"
    assert a.context[f"{B}/presentation/billing.controller.ts"] == "billing"
    assert a.context["src/shared/domain/value-object.ts"] == "shared"
    assert a.context["src/main.ts"] is None
    assert a.context_how["orders"] == "nest" and a.context_root["orders"] == O
    assert a.ring[f"{O}/domain/order.ts"] == "domain"
    assert a.ring[f"{O}/infrastructure/typeorm-order.repository.ts"] == "infrastructure"
    assert a.ring[f"{O}/presentation/orders.controller.ts"] == "presentation"
    roles = {f.rsplit("/", 1)[1]: r for f, r in a.roles.items()}
    assert roles["order.ts"] == {"aggregate"}
    assert roles["money.vo.ts"] == {"value-object"}
    assert roles["order-placed.event.ts"] == {"domain-event"}
    assert roles["place-order.handler.ts"] == {"command-handler"}
    assert roles["place-order.command.ts"] == {"command"}
    assert roles["get-order.use-case.ts"] == {"use-case"}
    assert roles["order.repository.ts"] == {"port"}
    assert roles["typeorm-order.repository.ts"] == {"repository", "adapter"}
    assert a.is_public(f"{O}/index.ts") and not a.is_public(f"{O}/domain/order-line.ts")


def test_context_links_and_aggregates(ddd_graph):
    cfg, g = ddd_graph
    a = arch.model(g)
    links = arch.context_links(g, a)
    lk = links[("billing", "orders")]
    assert lk.imports == 3
    assert lk.leaks == [(f"{B}/domain/invoice.ts", f"{O}/domain/order-line.ts", 2)]  # Money via the index is fine
    aggs = {ag.root: ag for ag in arch.aggregates(g, a)}
    assert aggs["Order"].members == [f"{O}/domain/money.vo.ts", f"{O}/domain/order-line.ts"]
    assert g.inherits[(f"{O}/domain/order.ts", "Order")] == [("extends", "src/shared/domain/aggregate-root.ts",
                                                               "AggregateRoot", None)]
    assert ("src/modules/orders/infrastructure/typeorm-order.repository.ts", "TypeormOrderRepository", "implements") \
        in g.subclasses[(f"{O}/domain/order.repository.ts", "OrderRepository")]


def test_explicit_contexts_from_rules_toml(ddd_graph):
    cfg, g = ddd_graph
    a = arch.model(g, {"contexts": {"sales": ["src/modules/**"], "web": {"paths": ["web/**"], "public": ["web/src/api/*"]}}})
    assert a.context[f"{B}/domain/invoice.ts"] == "sales"
    assert a.context["web/src/api/orders.ts"] == "web" and a.is_public("web/src/api/orders.ts")


# ---------------------------------------------------------------------- rules

def _keys(res, state=None):
    return {(v.rule, v.src, v.dst) for v in res.violations if state is None or v.state == state}


def test_auto_presets_find_the_planted_violations(ddd_graph):
    cfg, g = ddd_graph
    res = rules.run(cfg, g)
    assert res.auto and res.presets[:4] == ["layered", "nest", "clean", "ddd"]
    assert {"deps", "naming", "fullstack"} <= set(res.presets)
    keys = _keys(res)
    assert ("clean/domain-pure", f"{O}/domain/order.ts", "pkg:typeorm") in keys
    assert ("ddd/public-api", f"{B}/domain/invoice.ts", f"{O}/domain/order-line.ts") in keys
    assert ("ddd/aggregate-boundary", f"{B}/domain/invoice.ts", f"{O}/domain/order-line.ts") in keys
    assert ("layered/controller-no-repository", f"{O}/presentation/orders.controller.ts",
            f"{O}/infrastructure/typeorm-order.repository.ts") in keys
    assert ("contracts/no-route", "web/src/api/orders.ts", "GET /billing/refunds") in keys
    assert ("contracts/method-mismatch", "web/src/api/orders.ts", "DELETE /orders/:param") in keys
    assert ("naming/route-verb", f"{B}/presentation/billing.controller.ts", "POST /billing/createInvoice") in keys
    assert any(r == "layered/no-file-cycles" for r, _s, _d in keys)
    assert not any(r == "nest/module-public-api" for r, _s, _d in keys)  # merged into ddd/public-api
    order = [rules.SEV_ORDER[v.severity] for v in res.violations]
    assert order == sorted(order)


def test_custom_rules_and_overrides(ddd_graph):
    cfg, g = ddd_graph
    rc = {"extends": ["clean"], "disable": ["clean/presentation-no-infrastructure"],
          "severity": {"clean/domain-pure": "warn"},
          "rules": [
              {"name": "billing stays out of orders", "kind": "deny", "from": {"context": "billing"},
               "to": {"context": "orders"}, "adr": "0002"},
              {"name": "only domain", "kind": "allow-only", "from": {"ring": "domain", "context": "orders"},
               "to": {"ring": "domain"}, "severity": "info"},
              {"name": "max loc", "kind": "max-loc", "max": 20, "severity": "info"},
              {"name": "context cycles", "kind": "no-cycles", "granularity": "context"},
              {"name": "folder cycles", "kind": "no-cycles", "granularity": "folder", "level": 4},
          ]}
    res = rules.run(cfg, g, rc)
    by = {}
    for v in res.violations:
        by.setdefault(v.rule, []).append(v)
    assert "clean/presentation-no-infrastructure" not in by
    assert by["clean/domain-pure"][0].severity == "warn"
    deny = by["billing stays out of orders"]
    assert {v.dst for v in deny} == {f"{O}/domain/order-line.ts", f"{O}/domain/money.vo.ts"} and deny[0].adr == "0002"
    assert {v.src for v in by["max loc"]} == {f"{O}/presentation/orders.controller.ts", f"{O}/domain/order.ts"}
    assert "context cycles" not in by
    assert "folder cycles" not in by  # the only file cycle stays inside billing/application
    assert not res.errors


def test_bad_rules_are_reported(ddd_graph):
    cfg, g = ddd_graph
    res = rules.run(cfg, g, {"extends": [], "rules": [{"name": "x", "kind": "nope"},
                                                      {"name": "y", "kind": "deny", "from": {"colour": "red"}}]})
    assert len(res.errors) == 2


def test_baseline_ratchet(ddd_graph):
    cfg, g = ddd_graph
    res = rules.run(cfg, g)
    assert res.new() and not res.resolved
    rules.save_baseline(cfg, res.violations)
    res2 = rules.run(cfg, g)
    assert not res2.new() and len(res2.violations) == len(res.violations)
    # a baseline entry that no longer happens is reported as resolved
    base = json.loads((cfg.dir / rules.BASELINE_FILE).read_text(encoding="utf-8"))
    base["violations"]["clean/domain-pure|src/gone.ts|pkg:typeorm"] = {"rule": "clean/domain-pure", "src": "src/gone.ts"}
    (cfg.dir / rules.BASELINE_FILE).write_text(json.dumps(base), encoding="utf-8")
    assert [r["src"] for r in rules.run(cfg, g).resolved] == ["src/gone.ts"]


def test_check_cli_formats_scope_and_exit_codes(ddd_graph, capsys):
    cfg, g = ddd_graph
    root = str(cfg.root)
    assert cli.main(["check", "--root", root, "--no-build"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("@check ddd-nest | auto presets") and "ERROR clean/domain-pure" in out
    assert cli.main(["check", "--root", root, "--no-build", "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["new"]["error"] >= 6 and data["violations"][0]["state"] == "new"
    cli.main(["check", "--root", root, "--no-build", "--format", "github"])
    assert "::error file=src/modules/orders/domain/order.ts,line=1,title=clean/domain-pure::" in capsys.readouterr().out
    cli.main(["check", "--root", root, "--no-build", "--format", "markdown"])
    assert "| :x: | `clean/domain-pure` |" in capsys.readouterr().out
    assert cli.main(["check", "web/src/api/orders.ts", "--root", root, "--no-build", "--format", "json"]) == 1
    srcs = {v["src"] for v in json.loads(capsys.readouterr().out)["violations"]}
    assert srcs == {"web/src/api/orders.ts"}
    assert cli.main(["check", "--root", root, "--no-build", "--rule", "naming"]) == 0  # warnings only
    capsys.readouterr()
    assert cli.main(["check", "--root", root, "--no-build", "--rule", "naming", "--strict"]) == 1
    capsys.readouterr()
    assert cli.main(["check", "--root", root, "--no-build", "--baseline"]) == 0
    assert "accepted" in capsys.readouterr().out
    assert cli.main(["check", "--root", root, "--no-build"]) == 0
    assert "OK no new violations" in capsys.readouterr().out


def test_check_hook_reports_new_errors_of_the_edited_file(project_copy, monkeypatch, capsys):
    root = project_copy("ddd-nest")
    cfg = config_mod.load(root)
    payload = {"tool_name": "Edit", "cwd": str(root),
               "tool_input": {"file_path": str(root / "src/modules/orders/domain/order.ts")}}
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    assert cli.main(["check", "--hook"]) == 2
    err = capsys.readouterr().err
    assert "clean/domain-pure" in err and "src/modules/orders/domain/order.ts" in err
    # once accepted, the same edit passes
    g = load_graph(cfg)
    rules.save_baseline(cfg, rules.run(cfg, g).violations)
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    assert cli.main(["check", "--hook"]) == 0
    # a file without errors and a non-source file pass quietly
    for f in ("src/modules/orders/domain/money.vo.ts", "README.md"):
        payload["tool_input"]["file_path"] = str(root / f)
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
        assert cli.main(["check", "--hook"]) == 0


def test_rules_init_list_and_export(ddd_graph, capsys):
    cfg, g = ddd_graph
    root = str(cfg.root)
    assert cli.main(["rules", "--root", root, "--no-build"]) == 0
    out = capsys.readouterr().out
    assert " * ddd:" in out and "ddd/aggregate-boundary [aggregate-boundary]" in out
    assert cli.main(["rules", "--root", root, "--no-build", "--init", "ddd", "deps"]) == 0
    capsys.readouterr()
    text = (cfg.dir / "rules.toml").read_text(encoding="utf-8")
    assert 'extends = ["ddd", "deps"]' in text and "orders (nest: src/modules/orders)" in text
    assert cli.main(["rules", "--root", root, "--no-build", "--init"]) == 1  # exists
    capsys.readouterr()
    res = rules.run(cfg, g)
    assert not res.auto and res.presets == ["clean", "ddd", "deps"]
    assert cli.main(["rules", "--root", root, "--no-build", "--export", "depcruise"]) == 0
    js = capsys.readouterr().out
    assert js.startswith("// generated") and '"name": "clean/domain-pure"' in js and "typeorm" in js


def test_init_guard_installs_the_hook(project_copy):
    root = project_copy("ddd-nest")
    cfg = config_mod.load(root)
    from nimblegraph import project
    out = project.init(cfg, guard=True)
    settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for e in settings["hooks"]["PostToolUse"] for h in e["hooks"]]
    assert cmds == ["nimblegraph check --hook"] and any("architecture guard" in line for line in out)
    assert "already installed" in project.install_hook(root, project.GUARD_COMMAND, "architecture guard")


# ---------------------------------------------------------------------- naming

def test_naming_configured_and_inferred(ddd_graph):
    cfg, g = ddd_graph
    convs = naming.conventions(g, {"files": "kebab", "layers": {"service": {"file": "*.service.ts", "class": "*Service"}}})
    by = {(c.what, c.group): c for c in convs}
    case = by[("file-case", "ddd-shop service")]
    assert [o[0] for o in case.outliers] == [f"{B}/application/InvoiceService.ts"] and case.configured
    assert [o[0] for o in by[("file-suffix", "ddd-shop service")].outliers] == [  # @Injectable use case: a service
        f"{B}/application/InvoiceService.ts", f"{O}/application/get-order.use-case.ts"]
    assert naming.styles_of("user-profile") == {"kebab"} and naming.styles_of("user") == {"kebab", "camel", "snake"}
    assert naming.styles_of("UserProfile") == {"pascal"} and naming.styles_of("user_profile") == {"snake"}
    smells = naming.route_smells(g)
    assert [(s[0], s[3]) for s in smells] == [("naming/route-verb", "POST /billing/createInvoice")]
    res = rules.run(cfg, g, {"extends": ["naming"], "naming": {"files": "kebab"}})
    assert ("naming/file-case", f"{B}/application/InvoiceService.ts", None) in _keys(res)


def test_glossary_terms_synonyms_and_hits(ddd_graph):
    cfg, g = ddd_graph
    a = arch.model(g)
    terms = ddd.domain_terms(g, a)
    assert set(terms) == {"Invoice", "Money", "Order", "OrderLine"}
    assert terms["Order"].contexts == {"orders"}
    assert ddd.synonym_groups({"Customer": ddd.Term("Customer"), "Client": ddd.Term("Client"),
                               "Order": ddd.Term("Order")}) == [["Client", "Customer"]]
    _write(cfg, "glossary.toml", '[terms.Bill]\nsynonyms = ["Invoice"]\n')
    hits = ddd.glossary_hits(cfg, g)
    assert {(h[0], h[3]) for h in hits} == {(f"{B}/domain/invoice.ts", "Invoice"),
                                            (f"{B}/application/InvoiceService.ts", "InvoiceService"),
                                            (f"{B}/application/invoice-calculator.ts", "invoice-calculator"),
                                            ("web/src/api/billing.ts", "getInvoice"),
                                            ("web/src/api/billing.ts", "issueInvoice"),
                                            (f"{B}/presentation/billing.controller.ts", "POST /billing/createInvoice")}
    res = rules.run(cfg, g, {"extends": ["naming"]})
    assert any(v.rule == "naming/glossary" for v in res.violations)


# ---------------------------------------------------------------------- metrics

def test_module_metrics_dsm_mermaid_adr_dead(ddd_graph):
    cfg, g = ddd_graph
    a = arch.model(g)
    mods = {m.name: m for m in metrics.module_metrics(g, a, "context")}
    assert (mods["shared"].ca, mods["shared"].ce, mods["shared"].abstractness) == (4, 0, 1.0)
    assert mods["billing"].instability == 0.75
    names, cells = metrics.dsm(g, a, "context", 3)
    assert cells[("billing", "orders")] == 3
    mm = metrics.mermaid(g, a, "context", 3)
    assert mm.startswith("flowchart LR") and "-.->|3, 1 leaks|" in mm
    adrs = metrics.adrs(cfg, g)
    assert [(x.id, x.status, len(x.files)) for x in adrs] == [("0001", "accepted", 7)]
    assert metrics.adrs_for(adrs, [f"{O}/domain/order.ts", "src/main.ts"]) == {"0001": [f"{O}/domain/order.ts"]}
    assert metrics.dead_exports(g) == []
    snap = metrics.record(cfg, g)
    assert snap["files"] == len(g.files) and snap["leaks"] == 1
    assert len(metrics.history(cfg)) == 1
    metrics.record(cfg, g)
    assert len(metrics.history(cfg)) == 1  # one per commit and day


# ---------------------------------------------------------------------- tests map / contracts / review

def test_test_map_coverage_and_tests_to_run(ddd_graph):
    cfg, g = ddd_graph
    a = arch.model(g)
    tm = testmap.test_map(g)
    assert tm["test/orders.e2e-spec.ts"].kind == "e2e" and tm[f"{O}/domain/order.spec.ts"].kind == "unit"
    cov = testmap.coverage(g, a, tm)
    tested = {r.label: (e2e, unit) for r, e2e, unit in cov.endpoints}
    assert tested["POST /orders"] == (["test/orders.e2e-spec.ts"], [])
    assert tested["GET /billing/invoices/:id"] == ([], [])
    tests, cmds = testmap.tests_to_run(g, [f"{O}/domain/money.vo.ts"], tm)
    assert tests == [f"{O}/domain/order.spec.ts", "test/orders.e2e-spec.ts"]
    assert cmds == [f"npx jest {O}/domain/order.spec.ts test/orders.e2e-spec.ts"]
    res = rules.run(cfg, g, {"extends": [], "tests": {"require_test_for": ["route:api", "role:use-case"]},
                             "rules": [{"name": "tests", "kind": "tests-missing", "severity": "warn"}]})
    missing = {v.dst for v in res.violations if v.rule == "tests/missing"}
    assert "GET /billing/invoices/:id" in missing and "POST /orders" not in missing


def test_contracts(ddd_graph):
    cfg, g = ddd_graph
    issues = {(i.sub, i.label) for i in testmap.contracts(g)}
    assert ("no-route", "GET /billing/refunds") in issues
    assert ("method-mismatch", "DELETE /orders/:param") in issues
    assert ("unused-route", "GET /orders/:id/raw") in issues
    assert ("unused-route", "POST /orders") not in issues
    # calls through a local wrapper: request(path) doing fetch(`/api${path}`)
    calls = {(c.file, c.method, c.url, bool(c.route_id)) for c in g.api_calls}
    assert ("web/src/api/billing.ts", "GET", "/api/billing/invoices/:param", True) in calls
    assert ("web/src/api/billing.ts", "POST", "/api/billing/createInvoice", True) in calls
    assert not any(c.file == "web/src/api/http.ts" for c in g.api_calls)  # the wrapper itself is not a call
    assert ("unused-route", "GET /billing/invoices/:id") not in issues


def test_review_of_explicit_files(ddd_graph):
    cfg, g = ddd_graph
    rv = review.review(cfg, g, None, files=[f"{O}/domain/order.ts", "README.md"])
    assert rv.indexed == [f"{O}/domain/order.ts"] and rv.by_context == {"orders": [f"{O}/domain/order.ts"]}
    assert [v.rule for v in rv.violations] == ["clean/domain-pure"]
    assert "POST /orders" in {r.label for r in rv.routes}
    assert rv.tests == [f"{O}/domain/order.spec.ts", "test/orders.e2e-spec.ts"]
    assert rv.adrs == {"0001": [f"{O}/domain/order.ts"]}
    assert rv.owners == {"@team-orders": [f"{O}/domain/order.ts"], "@platform": ["README.md"]}


def test_review_cli_with_git(project_copy, capsys):
    root = project_copy("ddd-nest")
    git = ["git", "-C", str(root)]
    try:
        subprocess.run(git + ["init", "-q"], check=True, capture_output=True)
        subprocess.run(git + ["-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], check=True, capture_output=True)
        subprocess.run(git + ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True,
                       capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    f = root / "src/modules/billing/presentation/billing.controller.ts"
    f.write_text(f.read_text(encoding="utf-8").replace("import { InvoiceService }",
                                                       "import { TypeormOrderRepository } from '../../orders/infrastructure/typeorm-order.repository'\nimport { InvoiceService }"),
                 encoding="utf-8")
    assert cli.main(["review", "--root", str(root), "--format", "markdown"]) == 1
    md = capsys.readouterr().out
    assert "## nimblegraph review" in md and "`ddd/public-api`" in md and "@team-billing" in md
    assert cli.main(["check", "--git", "--root", str(root), "--no-build", "--format", "json"]) == 1
    srcs = {v["src"] for v in json.loads(capsys.readouterr().out)["violations"]}
    assert srcs == {"src/modules/billing/presentation/billing.controller.ts"}
