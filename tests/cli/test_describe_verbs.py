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
    store.put("workflows", json.loads((BUNDLE / "workflows" / "queue-add.json").read_text()))
    store.put("rules", json.loads((BUNDLE / "rules" / "pr-fixer-checks.json").read_text()))
    return store


def test_rules_describe_prints_the_lines(wire, seeded, capsys):  # noqa: F811
    rc, out, err = run(capsys, "rules", "describe", "pr-fixer-checks")
    assert rc == 0, err
    assert out.splitlines() == PR_FIXER_CHECKS


def test_workflows_describe_prints_the_lines(wire, seeded, capsys):  # noqa: F811
    rc, out, err = run(capsys, "workflows", "describe", "pr-fix")
    assert rc == 0, err
    # description, blank, paragraph, blank, then the d19 numbered lines
    head, para, lines = out.rstrip("\n").split("\n\n", 2)
    assert head == seeded.get("workflows", "pr-fix")["description"]
    assert para == jrun(capsys, "workflows", "describe", "pr-fix")["paragraph"]["text"]
    assert lines.splitlines() == PR_FIX_WORKFLOW


def test_describe_json_has_lines_and_entries(wire, seeded, capsys):  # noqa: F811
    out = jrun(capsys, "workflows", "describe", "pr-fix")
    assert out["lines"] == PR_FIX_WORKFLOW
    assert out["entries"][0]["step"] == "quiet"
    assert out["description"]
    assert out["paragraph"]["text"] and out["paragraph"]["segments"]


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


def test_mcp_workflows_describe_carries_description_and_paragraph(wire, seeded):  # noqa: F811
    from culture_rules.cli import _api

    got = call_tool("workflows_describe", {"id": "pr-fix"}, _api.make_client())
    assert got["description"]
    assert got["paragraph"]["text"]
    assert got["lines"] == PR_FIX_WORKFLOW


NEWSPAPER = Path(__file__).resolve().parents[2] / "docs" / "rules" / "newspaper"


@pytest.fixture
def newspaper(store):  # noqa: F811
    store.put(
        "workflows", json.loads((NEWSPAPER / "workflows" / "daily-newspaper.json").read_text())
    )
    store.put("rules", json.loads((NEWSPAPER / "rules" / "daily-newspaper.json").read_text()))
    return store


def test_describe_strikes_a_disabled_rule_out_without_a_tty(wire, newspaper, capsys):  # noqa: F811
    para = jrun(capsys, "workflows", "describe", "daily-newspaper")["paragraph"]
    assert para["segments"][0]["disabled"] is True
    rc, out, err = run(capsys, "workflows", "describe", "daily-newspaper")
    assert rc == 0, err
    body = out.split("\n\n")[1]
    assert body.startswith("~~It starts on cron 0 5 * * * (Asia/Jerusalem), on spark.~~ First: ")
    assert "\x1b" not in out and "isabled" not in body
    assert body.replace("~~", "") == para["text"]


def test_describe_strikes_with_ansi_on_a_tty(wire, newspaper, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    para = jrun(capsys, "workflows", "describe", "daily-newspaper")["paragraph"]
    rc, out, err = run(capsys, "workflows", "describe", "daily-newspaper")
    assert rc == 0, err
    body = out.split("\n\n")[1]
    assert body.startswith("\x1b[9mIt starts on cron")
    assert "~~" not in body
    assert body.replace("\x1b[9m", "").replace("\x1b[29m", "") == para["text"]


def test_describe_json_is_the_raw_payload_with_the_flag(wire, newspaper, capsys):  # noqa: F811
    out = jrun(capsys, "workflows", "describe", "daily-newspaper")
    assert out["paragraph"]["segments"][0]["disabled"] is True
    assert "~~" not in out["paragraph"]["text"] and "\x1b" not in out["paragraph"]["text"]
