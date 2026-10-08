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
from tests.model.test_describe import PR_FIXER_CHECKS, PR_FIXER_WORKFLOW  # noqa: E402

BUNDLE = Path(__file__).resolve().parents[2] / "docs" / "rules" / "pr-fixer"


def _seed(store: MemoryStore) -> None:
    wf = json.loads((BUNDLE / "workflows" / "pr-fixer.json").read_text())
    rule = json.loads((BUNDLE / "rules" / "pr-fixer-checks.json").read_text())
    store.put("workflows", wf)
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
    r = client.get("/workflows/pr-fixer/describe")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "pr-fixer"
    assert body["kind"] == "workflow"
    assert body["lines"] == PR_FIXER_WORKFLOW
    assert body["entries"][3] == {
        "label": "3.1",
        "text": "qwen-fixer (agent)",
        "depth": 1,
        "step": "agent",
    }


def test_rule_describe_reads_its_workflow(viewer):
    _, client = viewer
    body = client.get("/rules/pr-fixer-checks/describe").json()
    assert body["kind"] == "rule"
    assert body["lines"] == PR_FIXER_CHECKS
    assert all("step" not in e for e in body["entries"])  # exclude_none keeps rule entries lean


def test_rule_describe_with_a_deleted_workflow_says_not_found(viewer):
    store, client = viewer
    store.put("workflows", {**store.get("workflows", "pr-fixer"), "deleted_at": "2026-10-07"})
    lines = client.get("/rules/pr-fixer-checks/describe").json()["lines"]
    assert "Run workflow pr-fixer (not found)" in lines


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
    assert "Run workflow pr-fixer v7 (version unavailable)" in lines
