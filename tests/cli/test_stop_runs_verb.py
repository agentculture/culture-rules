"""d17 on the CLI: a disabling write reports the active runs; ``rules stop-runs`` stops them."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.mcp.tools import call_tool, tool_specs  # noqa: E402
from tests.cli.test_nouns_api import jrun, run, store, wire, write_body  # noqa: E402,F401
from tests.server.conftest import rule_body  # noqa: E402

DEV = {"X-Culture-Identity": "alice"}


def seeded(w, n=2):
    w.tc.post("/rules", json=rule_body("r1"), headers=DEV)
    ids = [w.tc.post("/runs", json={"rule_id": "r1"}, headers=DEV).json()["id"] for _ in range(n)]
    w.calls.clear()
    return ids


def statuses(st, ids):
    return {st.get("runs", i)["status"] for i in ids}


def test_disable_reports_the_active_runs_and_hints_stop_runs(wire, store, capsys):  # noqa: F811
    ids = seeded(wire)
    out = jrun(capsys, "rules", "disable", "r1", "--apply")
    assert out["result"]["active_runs_total"] == 2
    assert sorted(r["id"] for r in out["result"]["active_runs"]) == sorted(ids)
    assert "culture-rules rules stop-runs r1 --apply" in out["hint"]
    assert statuses(store, ids) == {"running"}  # disabling stops nothing
    rc, text, err = run(capsys, "rules", "disable", "r1", "--apply")
    assert rc == 0, err
    assert text.rstrip().splitlines()[-1].startswith("hint: 2 current run(s) of r1")


def test_an_update_that_disables_reports_them_too(wire, capsys, tmp_path):  # noqa: F811
    seeded(wire, 1)
    body = write_body(tmp_path, rule_body("r1", enabled=False))
    out = jrun(capsys, "rules", "update", "r1", "--body", f"@{body}", "--apply")
    assert out["result"]["active_runs_total"] == 1 and "stop-runs r1" in out["hint"]


def test_disable_without_runs_has_no_hint(wire, capsys):  # noqa: F811
    seeded(wire, 0)
    out = jrun(capsys, "rules", "disable", "r1", "--apply")
    assert out["result"]["active_runs_total"] == 0 and "hint" not in out


def test_stop_runs_dry_run_lists_then_apply_cancels_then_no_op(wire, store, capsys):  # noqa: F811
    ids = seeded(wire)
    jrun(capsys, "rules", "disable", "r1", "--apply")
    dry = jrun(capsys, "rules", "stop-runs", "r1")
    assert dry["dry_run"] is True and dry["applied"] is False
    assert dry["result"]["cancelled"] == [] and dry["result"]["total"] == 2
    assert statuses(store, ids) == {"running"}
    done = jrun(capsys, "rules", "stop-runs", "r1", "--apply")
    assert done["applied"] is True and sorted(done["result"]["cancelled"]) == sorted(ids)
    assert statuses(store, ids) == {"cancelled"}
    reason = store.get("runs", ids[0])["error"]["message"]
    assert reason == "rule disabled: stopped by alice"  # the token's name
    again = jrun(capsys, "rules", "stop-runs", "r1", "--apply")
    assert again["result"]["cancelled"] == [] and again["result"]["total"] == 0


def test_stop_runs_on_an_enabled_rule_is_a_user_error(wire, store, capsys):  # noqa: F811
    ids = seeded(wire, 1)
    rc, out, err = run(capsys, "rules", "stop-runs", "r1", "--apply", "--json")
    assert rc == 1 and out == ""
    assert "rule_enabled" in json.loads(err)["message"]
    assert statuses(store, ids) == {"running"}


def test_stop_runs_is_an_mcp_tool_with_the_same_contract(wire, store):  # noqa: F811
    from culture_rules.cli import _api

    ids = seeded(wire, 1)
    wire.tc.post("/rules/r1/disable", headers=DEV)
    names = {t["name"] for t in tool_specs()}
    assert "rules_stop-runs" in names
    verb = REGISTRY.get("rules", "stop-runs")
    assert verb.mutating and verb.role == "editor"
    client = _api.make_client()
    dry = call_tool("rules_stop-runs", {"id": "r1"}, client)
    assert dry["dry_run"] is True and statuses(store, ids) == {"running"}
    done = call_tool("rules_stop-runs", {"id": "r1", "apply": True}, client)
    assert done["result"]["cancelled"] == ids and statuses(store, ids) == {"cancelled"}
