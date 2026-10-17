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
PLAIN = json.loads(
    (
        Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "workflow_descriptions.json"
    ).read_text(encoding="utf-8")
)


def _plain(doc: dict) -> dict:
    """t12: the committed descriptions are empty (their text is in notes), so the describe
    prose comes from the plain-text fixture (tests/fixtures/workflow_descriptions.json)."""
    entry = PLAIN[doc["id"]]
    out = json.loads(json.dumps(doc))
    out["description"] = entry["description"]

    def walk(steps):
        for step in steps or ():
            if isinstance(step, dict):
                if step.get("id") in entry["steps"]:
                    step["description"] = entry["steps"][step["id"]]
                walk(step.get("steps") or step.get("body"))

    walk(out.get("steps"))
    return out


@pytest.fixture
def seeded(store):  # noqa: F811
    store.put("workflows", _plain(json.loads((BUNDLE / "workflows" / "pr-fix.json").read_text())))
    store.put(
        "workflows", _plain(json.loads((BUNDLE / "workflows" / "queue-add.json").read_text()))
    )
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
    paragraph = jrun(capsys, "workflows", "describe", "pr-fix")["paragraph"]
    # d11: one sentence per line, each stand-in for a missing step description in [ ]
    assert len(para.splitlines()) == paragraph["text"].count("\n") + 1
    assert not any(s.get("standin") for s in paragraph["segments"])  # all written here
    assert _unmarked(para, paragraph) == paragraph["text"]
    assert lines.splitlines() == PR_FIX_WORKFLOW


def _unmarked(text: str, paragraph: dict) -> str:
    """The CLI's paragraph with its marks taken out: strikes, and each stand-in's [ ]."""
    for seg in paragraph["segments"]:
        if seg.get("standin"):
            text = text.replace(f"[{seg['shown']}]", seg["shown"], 1)
    return text.replace("~~", "").replace("\x1b[9m", "").replace("\x1b[29m", "")


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
        "workflows",
        _plain(json.loads((NEWSPAPER / "workflows" / "daily-newspaper.json").read_text())),
    )
    store.put("rules", json.loads((NEWSPAPER / "rules" / "daily-newspaper.json").read_text()))
    return store


def test_describe_strikes_a_disabled_rule_out_without_a_tty(wire, newspaper, capsys):  # noqa: F811
    para = jrun(capsys, "workflows", "describe", "daily-newspaper")["paragraph"]
    assert para["segments"][0]["disabled"] is True
    rc, out, err = run(capsys, "workflows", "describe", "daily-newspaper")
    assert rc == 0, err
    body = out.split("\n\n")[1]
    assert body.startswith(
        "~~It starts on cron 0 5 * * * (Asia/Jerusalem), on spark.~~\nFirst reads "
    )
    assert "\x1b" not in out and "isabled" not in body
    assert _unmarked(body, para) == para["text"]


def test_describe_strikes_with_ansi_on_a_tty(wire, newspaper, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    para = jrun(capsys, "workflows", "describe", "daily-newspaper")["paragraph"]
    rc, out, err = run(capsys, "workflows", "describe", "daily-newspaper")
    assert rc == 0, err
    body = out.split("\n\n")[1]
    assert body.startswith("\x1b[9mIt starts on cron")
    assert "~~" not in body
    assert _unmarked(body, para) == para["text"]


def test_describe_json_is_the_raw_payload_with_the_flag(wire, newspaper, capsys):  # noqa: F811
    out = jrun(capsys, "workflows", "describe", "daily-newspaper")
    assert out["paragraph"]["segments"][0]["disabled"] is True
    assert "~~" not in out["paragraph"]["text"] and "\x1b" not in out["paragraph"]["text"]


@pytest.mark.parametrize("tty", [False, True])
def test_describe_puts_each_stand_in_in_brackets(
    wire, store, capsys, monkeypatch, tty  # noqa: F811
):
    """d11: the bundle's pr-fix with no step descriptions: each step stands in by its name,
    in [ ] (on a terminal too); without the marks the output is exactly the paragraph."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: tty, raising=False)
    store.put("workflows", json.loads((BUNDLE / "workflows" / "pr-fix.json").read_text()))
    para = jrun(capsys, "workflows", "describe", "pr-fix")["paragraph"]
    rc, out, err = run(capsys, "workflows", "describe", "pr-fix")
    assert rc == 0, err
    body = out.split("\n\n")[0]
    assert body.splitlines()[:2] == [
        "No rule starts it yet.",
        "First [quiet period], waiting 5 minutes, and the run stops if the PR head moves;"
        " then [hold while GitGuardian fails]; then [trusted review threads].",
    ]
    assert body.count("[") == sum(1 for s in para["segments"] if s.get("standin"))
    assert _unmarked(body, para) == para["text"]
