"""Workflow.notes and Step.notes: free text beside description, empty by default."""

from __future__ import annotations

from pathlib import Path

import pytest

from culture_rules.model.workflow import Step, Workflow
from tests.model.factories import make_step, make_workflow

ROOT = Path(__file__).resolve().parents[2]


def test_notes_default_empty() -> None:
    assert make_workflow().notes == ""
    assert make_step().notes == ""


def test_notes_round_trip_keeps_description() -> None:
    step = make_step(description="Posts the result", notes="uses http.call, retries twice")
    wf = make_workflow(description="Daily digest", notes="owner: ops", steps=(step,))
    again = Workflow.from_dict(wf.to_dict())
    assert again == wf
    assert (again.description, again.notes) == ("Daily digest", "owner: ops")
    assert (again.steps[0].description, again.steps[0].notes) == (
        "Posts the result",
        "uses http.call, retries twice",
    )
    assert Step.from_dict(step.to_dict()).notes == step.notes


def test_notes_in_generated_schemas() -> None:
    import json

    for name in ("workflow", "step"):
        schema = json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text())
        assert "notes" in schema["properties"]
        assert "notes" not in schema.get("required", [])


@pytest.mark.parametrize("path", sorted((ROOT / "docs" / "rules").glob("*/workflows/*.json")))
def test_committed_workflows_load_unchanged(path: Path) -> None:
    import json

    data = json.loads(path.read_text())
    wf = Workflow.from_dict(data)
    assert wf.notes == ""
    assert Workflow.from_dict(wf.to_dict()) == wf
