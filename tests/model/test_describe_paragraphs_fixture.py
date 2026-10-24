"""The web editor's Playwright fixture of the real API's describe answers (t18).

``web/e2e/fixtures/describe-paragraphs.json`` is what ``GET /workflows/{id}/describe`` answers
for the twelve committed workflows (``docs/rules``) once
``tests/fixtures/workflow_descriptions.json`` is applied, built by the same
``workflow_paragraph`` the server calls, so the e2e mock serves
the paragraph exactly as the API builds it. This test regenerates it and fails when the
committed copy has drifted; ``REGENERATE_E2E_FIXTURES=1 uv run pytest <this file>`` rewrites it.

One workflow ships with no description of its own (``daily-newspaper``), and a step with none
(``publish-fix``'s loop body step), so the editor's gaps and stand-ins (d11: the step's name,
marked ``standin``) are exercised.

Assumption c9 (checked here): none of the twelve committed workflows reads a ``fallback`` step
segment, i.e. every step kind and config in them has a joining phrase in the table.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from culture_rules.model import describe
from culture_rules.model.paragraph import workflow_paragraph

ROOT = Path(__file__).resolve().parents[2]
RULES_DOCS = ROOT / "docs" / "rules"
DESCRIPTIONS = ROOT / "tests" / "fixtures" / "workflow_descriptions.json"
FIXTURE = ROOT / "web" / "e2e" / "fixtures" / "describe-paragraphs.json"

#: Workflows the e2e fixture leaves without their own description (a visible gap in Simple).
NO_DESCRIPTION = {"daily-newspaper"}


def _fill(steps: list[dict], written: dict[str, str]) -> None:
    for step in steps:
        step["description"] = written.get(step["id"], "")
        _fill(step.get("body") or [], written)


def _all_steps(steps: list[dict]):
    for step in steps:
        yield step
        yield from _all_steps(step.get("body") or [])


def build() -> dict:
    descriptions = json.loads(DESCRIPTIONS.read_text())
    rules = [json.loads(p.read_text()) for p in sorted(RULES_DOCS.glob("*/rules/*.json"))]
    workflows: dict[str, dict] = {}
    for path in sorted(RULES_DOCS.glob("*/workflows/*.json")):
        wf = json.loads(path.read_text())
        written = descriptions[wf["id"]]
        _fill(wf["steps"], written["steps"])
        wf["description"] = "" if wf["id"] in NO_DESCRIPTION else written["description"]
        if not wf["description"]:
            del wf["description"]
        workflows[wf["id"]] = wf
    answers: dict[str, dict] = {}
    for wid, wf in workflows.items():
        entries = describe.describe_workflow(wf)
        answers[wid] = {
            "id": wid,
            "kind": "workflow",
            "lines": describe.render(entries),
            "entries": entries,
            "description": str(wf.get("description") or ""),
            "paragraph": workflow_paragraph(wf, rules),
        }
    return {
        "source": (
            "tests/model/test_describe_paragraphs_fixture.py: docs/rules workflows and rules, "
            "descriptions from tests/fixtures/workflow_descriptions.json, answered by "
            "workflow_paragraph as GET /workflows/{id}/describe does"
        ),
        "rules": rules,
        "workflows": workflows,
        "describe": answers,
    }


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_the_committed_e2e_fixture_equals_what_the_api_builds():
    expected = _dump(build())
    if os.environ.get("REGENERATE_E2E_FIXTURES"):
        FIXTURE.write_text(expected)
    assert FIXTURE.read_text() == expected, (
        "web/e2e/fixtures/describe-paragraphs.json drifted; regenerate it with "
        "REGENERATE_E2E_FIXTURES=1 uv run pytest tests/model/test_describe_paragraphs_fixture.py"
    )


def test_the_fixture_has_a_workflow_and_a_step_with_no_description():
    data = build()
    assert [wid for wid, wf in data["workflows"].items() if not wf.get("description")] == [
        "daily-newspaper"
    ]
    gaps = [
        (wid, s["id"])
        for wid, wf in data["workflows"].items()
        for s in _all_steps(wf["steps"])
        if not s["description"]
    ]
    assert gaps, "no step without a description"
    assert any(wid == "publish-fix" for wid, _ in gaps)
    segs = data["describe"]["publish-fix"]["paragraph"]["segments"]
    assert any(s.get("gap") for s in segs)
    reply = next(s for s in segs if s.get("step") == "reply")
    assert reply["standin"] is True
    assert reply["text"] == reply["name"]


def test_c9_no_committed_workflow_falls_back_to_a_generated_line():
    """c9: the twelve committed workflows all read with a joining phrase and no fallback."""
    data = build()
    assert len(data["workflows"]) == 12
    fallbacks = {
        wid: [s["step"] for s in a["paragraph"]["segments"] if s.get("fallback")]
        for wid, a in data["describe"].items()
    }
    assert {k: v for k, v in fallbacks.items() if v} == {}, fallbacks
