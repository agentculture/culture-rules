"""d19: GET /rules/{id}/describe and GET /workflows/{id}/describe."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.model.test_describe import PR_FIX_WORKFLOW, PR_FIXER_CHECKS  # noqa: E402

BUNDLE = Path(__file__).resolve().parents[2] / "docs" / "rules" / "pr-fixer"


def _seed(store: MemoryStore) -> None:
    wf = json.loads((BUNDLE / "workflows" / "pr-fix.json").read_text())
    rule = json.loads((BUNDLE / "rules" / "pr-fixer-checks.json").read_text())
    store.put("workflows", wf)
    # #35: pr-fixer-checks now runs queue-add (pr-fixer-dispatch runs pr-fix)
    store.put("workflows", json.loads((BUNDLE / "workflows" / "queue-add.json").read_text()))
    store.put("rules", rule)


@pytest.fixture
def viewer():
    store = MemoryStore()
    _seed(store)
    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    token = ServiceTokens(store).issue("root", name="v", roles=["viewer"]).token
    client.headers["Authorization"] = f"Bearer {token}"
    return store, client


def test_workflow_describe_is_viewer_readable_and_matches_the_library(viewer):
    _, client = viewer
    r = client.get("/workflows/pr-fix/describe")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "pr-fix"
    assert body["kind"] == "workflow"
    assert body["lines"] == PR_FIX_WORKFLOW
    assert body["entries"][5] == {
        "label": "5.1",
        "text": 'qwen-fixer (agent, must commit), when fixer missing or fixer ∈ {"", qwen-fixer}',
        "depth": 1,
        "step": "agent_qwen",
    }


def test_rule_describe_reads_its_workflow(viewer):
    _, client = viewer
    body = client.get("/rules/pr-fixer-checks/describe").json()
    assert body["kind"] == "rule"
    assert body["lines"] == PR_FIXER_CHECKS
    assert all("step" not in e for e in body["entries"])  # exclude_none keeps rule entries lean


def test_rule_describe_with_a_deleted_workflow_says_not_found(viewer):
    store, client = viewer
    gone = {**store.get("workflows", "queue-add"), "deleted_at": "2026-10-07"}
    store.put("workflows", gone)
    lines = client.get("/rules/pr-fixer-checks/describe").json()["lines"]
    assert "Run workflow queue-add (not found)" in lines


@pytest.mark.parametrize("path", ["/rules/nope/describe", "/workflows/nope/describe"])
def test_unknown_id_is_404(viewer, path):
    _, client = viewer
    r = client.get(path)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_rule_pinned_to_another_workflow_version_says_unavailable(viewer):
    store, client = viewer
    rule = store.get("rules", "pr-fixer-checks")
    store.put("rules", {**rule, "workflow": {**rule["workflow"], "version": 7}})
    lines = client.get("/rules/pr-fixer-checks/describe").json()["lines"]
    assert "Run workflow queue-add v7 (version unavailable)" in lines


# ---------------------------------------------------------------- characterization
# (the Sonar S3776 split of _register_runs: the rule's workflow lookup, pinned)


def test_rule_describe_without_a_workflow_reads_none(viewer, monkeypatch):
    from culture_rules.model import describe

    store, client = viewer
    seen = []
    real = describe.describe_rule

    def spy(rule, workflow):
        seen.append(workflow)
        return real(rule, workflow)

    monkeypatch.setattr(describe, "describe_rule", spy)
    rule = store.get("rules", "pr-fixer-checks")
    for ref in (None, {"id": ""}, "pr-fix"):
        store.put("rules", {**rule, "workflow": ref})
        assert client.get("/rules/pr-fixer-checks/describe").status_code == 200
    store.put("rules", {**rule, "workflow": {"id": "missing"}})
    client.get("/rules/pr-fixer-checks/describe")
    store.put("rules", rule)
    client.get("/rules/pr-fixer-checks/describe")
    assert seen[:4] == [None, None, None, {}]
    assert seen[4]["id"] == "queue-add"


def test_workflow_describe_has_description_and_paragraph(viewer):
    from culture_rules.model.paragraph import workflow_paragraph

    store, client = viewer
    body = client.get("/workflows/pr-fix/describe").json()
    wf = store.get("workflows", "pr-fix")
    assert body["description"] == (wf.get("description") or "")
    want = workflow_paragraph(wf, store.find("rules", {}))
    assert body["paragraph"]["text"] == want["text"]  # None-valued keys are omitted on the wire
    assert body["paragraph"]["segments"] == [
        {k: v for k, v in seg.items() if v is not None} for seg in want["segments"]
    ]
    assert body["paragraph"]["segments"]
    assert body["paragraph"]["text"]
    assert "paragraph" not in client.get("/rules/pr-fixer-checks/describe").json()


def test_workflow_describe_paragraph_ignores_deleted_rules(viewer):
    store, client = viewer
    rule = dict(store.get("rules", "pr-fixer-checks"))
    with_rule = client.get("/workflows/queue-add/describe").json()["paragraph"]["text"]
    store.put("rules", {**rule, "deleted_at": "2026-01-01T00:00:00Z"})
    without = client.get("/workflows/queue-add/describe").json()["paragraph"]["text"]
    assert with_rule != without


def test_empty_workflow_describes(viewer):
    store, client = viewer
    store.put("workflows", {"id": "empty", "name": "empty", "steps": [], "edges": []})
    body = client.get("/workflows/empty/describe").json()
    assert body["description"] == ""
    assert body["lines"] == []
    assert isinstance(body["paragraph"]["text"], str)


def test_disabled_survives_on_the_wire_only_when_true(viewer):
    store, client = viewer
    wf = store.get("workflows", "pr-fix")
    first = wf["steps"][0]["id"]
    store.put(
        "workflows", {**wf, "steps": [{**wf["steps"][0], "enabled": False}, *wf["steps"][1:]]}
    )
    segs = client.get("/workflows/pr-fix/describe").json()["paragraph"]["segments"]
    off = [s for s in segs if s.get("disabled")]
    assert [s["step"] for s in off] == [first] and off[0]["part"] == "step"
    assert all(s.get("disabled") is None for s in segs if s not in off)
    assert "isabled" not in client.get("/workflows/pr-fix/describe").json()["paragraph"]["text"]
