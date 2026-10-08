"""d19 on the CLI and MCP: ``rules describe`` / ``workflows describe`` over the API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.mcp.tools import call_tool, tool_specs  # noqa: E402
from tests.cli.test_nouns_api import jrun, run, store, wire  # noqa: E402,F401
from tests.model.test_describe import PR_FIX_WORKFLOW, PR_FIXER_CHECKS  # noqa: E402

BUNDLE = Path(__file__).resolve().parents[2] / "docs" / "rules" / "pr-fixer"


@pytest.fixture
def seeded(store):  # noqa: F811
    store.put("workflows", json.loads((BUNDLE / "workflows" / "pr-fix.json").read_text()))
    store.put("rules", json.loads((BUNDLE / "rules" / "pr-fixer-checks.json").read_text()))
    return store


def test_rules_describe_prints_the_lines(wire, seeded, capsys):  # noqa: F811
    rc, out, err = run(capsys, "rules", "describe", "pr-fixer-checks")
    assert rc == 0, err
    assert out.splitlines() == PR_FIXER_CHECKS


def test_workflows_describe_prints_the_lines(wire, seeded, capsys):  # noqa: F811
    rc, out, err = run(capsys, "workflows", "describe", "pr-fix")
    assert rc == 0, err
    assert out.splitlines() == PR_FIX_WORKFLOW


def test_describe_json_has_lines_and_entries(wire, seeded, capsys):  # noqa: F811
    out = jrun(capsys, "workflows", "describe", "pr-fix")
    assert out["lines"] == PR_FIX_WORKFLOW
    assert out["entries"][0]["step"] == "quiet"


def test_describe_unknown_id_is_a_user_error(wire, seeded, capsys):  # noqa: F811
    rc, out, err = run(capsys, "rules", "describe", "nope", "--json")
    assert rc == 1
    assert out == ""
    assert "nope" in json.loads(err)["message"]


def test_describe_is_a_read_only_viewer_verb_and_an_mcp_tool(wire, seeded):  # noqa: F811
    from culture_rules.cli import _api

    names = {t["name"] for t in tool_specs()}
    assert {"rules_describe", "workflows_describe"} <= names
    for noun in ("rules", "workflows"):
        verb = REGISTRY.get(noun, "describe")
        assert not verb.mutating
        assert verb.role == "viewer"
    client = _api.make_client()
    assert call_tool("rules_describe", {"id": "pr-fixer-checks"}, client)["lines"] == (
        PR_FIXER_CHECKS
    )
    assert call_tool("workflows_describe", {"id": "pr-fix"}, client)["lines"] == (PR_FIX_WORKFLOW)
