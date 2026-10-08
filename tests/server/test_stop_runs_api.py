"""d17 over HTTP: a disable reports the rule's active runs; ``stop-runs`` cancels them.

``POST /rules/{id}/disable`` (and a ``PUT`` that switches the rule off) answers the stored
rule plus ``active_runs`` / ``active_runs_total``. ``POST /rules/{id}/stop-runs`` is a dry-run
unless ``apply: true``, is refused (409 ``rule_enabled``) while the rule is enabled, cancels
through the normal run-cancel path, and is a no-op once nothing is active.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.policy import required_role  # noqa: E402
from culture_rules.engine.actorport import InvocationResult  # noqa: E402
from culture_rules.engine.claims import idempotency_key  # noqa: E402
from culture_rules.engine.runs import ACTION_STEP, Executor, step_state  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.engine.run_helpers import FakeActor, ports_for, rule, step, workflow  # noqa: E402
from tests.server.conftest import ALICE, dev_app, rule_body  # noqa: E402


def start(client, n=1, rule_id="r1"):
    return [client.post("/runs", json={"rule_id": rule_id}, headers=ALICE).json() for _ in range(n)]


def test_disable_reports_the_active_runs(client):
    client.post("/rules", json=rule_body())
    runs = start(client, 2)
    done = start(client)[0]
    client.post(f"/runs/{done['id']}/cancel", json={})
    r = client.post("/rules/r1/disable", headers=ALICE)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["id"] == "r1"
    assert body["active_runs_total"] == 2
    assert sorted(x["id"] for x in body["active_runs"]) == sorted(x["id"] for x in runs)
    assert all(x["status"] == "running" and x["started_at"] for x in body["active_runs"])
    # response-only: the stored rule carries neither field, and disabling stops nothing
    stored = client.get("/rules/r1").json()
    assert "active_runs" not in stored
    assert "active_runs_total" not in stored
    assert {client.get(f"/runs/{x['id']}").json()["status"] for x in runs} == {"running"}


def test_disable_with_no_runs_reports_none_and_enable_reports_nothing(client):
    client.post("/rules", json=rule_body())
    body = client.post("/rules/r1/disable").json()
    assert body["active_runs"] == []
    assert body["active_runs_total"] == 0
    assert "active_runs" not in client.post("/rules/r1/enable").json()


def test_disable_lists_at_most_fifty_but_counts_all(client):
    client.post("/rules", json=rule_body())
    start(client, 53)
    body = client.post("/rules/r1/disable").json()
    assert len(body["active_runs"]) == 50
    assert body["active_runs_total"] == 53


def test_a_put_that_switches_the_rule_off_reports_the_runs_too(client):
    client.post("/rules", json=rule_body())
    (run,) = start(client)
    body = client.put("/rules/r1", json=rule_body(enabled=False)).json()
    assert [x["id"] for x in body["active_runs"]] == [run["id"]]
    # a save of a rule that was already off reports nothing new
    again = client.put("/rules/r1", json=rule_body(enabled=False, name="renamed")).json()
    assert "active_runs" not in again
    assert "active_runs" not in client.put("/rules/r1", json=rule_body()).json()


def test_stop_runs_is_refused_while_the_rule_is_enabled(client):
    client.post("/rules", json=rule_body())
    (run,) = start(client)
    r = client.post("/rules/r1/stop-runs", json={"apply": True})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "rule_enabled"
    assert client.get(f"/runs/{run['id']}").json()["status"] == "running"


def test_stop_runs_of_an_unknown_rule_is_404(client):
    r = client.post("/rules/nope/stop-runs", json={"apply": True})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "rule_not_found"


def test_stop_runs_dry_run_lists_and_cancels_nothing(client):
    client.post("/rules", json=rule_body())
    (run,) = start(client)
    client.post("/rules/r1/disable")
    out = client.post("/rules/r1/stop-runs").json()  # no body: a dry-run
    assert out["applied"] is False
    assert out["cancelled"] == []
    assert out["total"] == 1
    assert [x["id"] for x in out["runs"]] == [run["id"]]
    assert client.get(f"/runs/{run['id']}").json()["status"] == "running"


def test_stop_runs_cancels_them_and_a_second_call_is_a_no_op(client):
    client.post("/rules", json=rule_body())
    runs = start(client, 2)
    other = client.post("/rules", json=rule_body("r2"))
    assert other.status_code == 201
    (keep,) = start(client, rule_id="r2")
    client.post("/rules/r1/disable")
    r = client.post("/rules/r1/stop-runs", json={"apply": True}, headers=ALICE)
    assert r.status_code == 200
    out = r.json()
    assert out["applied"] is True
    assert out["total"] == 2
    assert sorted(out["cancelled"]) == sorted(x["id"] for x in runs)
    for x in runs:
        doc = client.get(f"/runs/{x['id']}").json()
        assert doc["status"] == "cancelled"
        assert doc["error"]["message"] == "rule disabled: stopped by alice"
    assert client.get(f"/runs/{keep['id']}").json()["status"] == "running"  # another rule's
    again = client.post("/rules/r1/stop-runs", json={"apply": True}).json()
    assert again["cancelled"] == []
    assert again["total"] == 0
    assert again["runs"] == []
    assert client.post("/rules/r1/disable").json()["active_runs_total"] == 0


def test_stop_runs_records_a_given_reason(client):
    client.post("/rules", json=rule_body())
    (run,) = start(client)
    client.post("/rules/r1/disable")
    client.post("/rules/r1/stop-runs", json={"apply": True, "reason": "fixer misbehaving"})
    assert client.get(f"/runs/{run['id']}").json()["error"]["message"] == "fixer misbehaving"


def test_a_run_another_node_is_executing_is_stopped_too():
    """The stop is store-level: a run whose step thor dispatched is cancelled from the API."""
    store = MemoryStore()
    client = TestClient(dev_app(store))
    client.post("/rules", json=rule_body())
    actor = FakeActor().on("a", ("accept",))
    thor = Executor(store, "thor", ports_for(actor))
    run = thor.start(rule(id="r1"), workflow((step("a"),)))
    thor.run_until_idle()
    assert step_state(thor.run(run["id"]), "a")["host"] == "thor"
    client.post("/rules/r1/disable")
    out = client.post("/rules/r1/stop-runs", json={"apply": True}).json()
    assert out["cancelled"] == [run["id"]]
    # thor's late result is ignored and the rule's action never runs
    assert thor.deliver(idempotency_key(run["id"], "a"), InvocationResult.completed({})) is False
    thor.run_until_idle()
    assert thor.run(run["id"])["status"] == "cancelled"
    assert actor.calls_for(ACTION_STEP) == []


def test_stop_runs_needs_the_editor_role_like_run_cancel():
    assert required_role("POST", "/rules/r1/stop-runs") == "editor"
    assert required_role("POST", "/rules/r1/stop-runs") == required_role("POST", "/runs/x/cancel")
