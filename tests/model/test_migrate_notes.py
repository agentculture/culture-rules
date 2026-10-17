"""t11: migrate_notes, the pure description -> notes move (c17, h14, c35, h32)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from culture_rules.actors.trusted import workflow_role
from culture_rules.model.migrate_notes import migrate_notes, notes_changes

RULES_DIR = Path(__file__).resolve().parents[2] / "docs" / "rules"


def _wf(**over):
    return {
        "id": "wf",
        "description": "  Why this exists.\n\tTabs, trailing space \n",
        "notes": "",
        "steps": [
            {"id": "a", "description": "step a", "notes": ""},
            {
                "id": "loop",
                "kind": "for_each",
                "description": "",
                "notes": "",
                "body": [{"id": "inner", "description": "inner text", "notes": ""}],
            },
        ],
        **over,
    }


def test_moves_workflow_steps_and_loop_bodies_byte_for_byte():
    src = _wf()
    out = migrate_notes(src)
    assert out["notes"] == src["description"] and out["description"] == ""
    assert out["steps"][0]["notes"] == "step a" and out["steps"][0]["description"] == ""
    inner = out["steps"][1]["body"][0]
    assert inner["notes"] == "inner text" and inner["description"] == ""
    assert notes_changes(src) == ["workflow", "a", "inner"]


def test_a_node_with_notes_set_is_left_alone_per_node():
    src = _wf(notes="mine")
    src["steps"][0]["notes"] = "keep"
    out = migrate_notes(src)
    assert out["notes"] == "mine" and out["description"] == src["description"]
    assert out["steps"][0]["notes"] == "keep" and out["steps"][0]["description"] == "step a"
    assert out["steps"][1]["body"][0]["notes"] == "inner text"  # other nodes still move
    assert notes_changes(src) == ["inner"]


def test_idempotent_and_input_not_mutated():
    src = _wf()
    before = copy.deepcopy(src)
    once = migrate_notes(src)
    assert src == before
    assert migrate_notes(once) == once
    assert notes_changes(once) == []


def test_missing_fields_and_empty_description_are_untouched():
    src = {"id": "x", "steps": [{"id": "s"}, {"id": "t", "description": ""}]}
    assert migrate_notes(src) == src


def test_committed_pr_fixer_workflows_keep_their_role():
    for name in ("pr-fix", "review-commit", "publish-fix"):
        doc = json.loads((RULES_DIR / "pr-fixer" / "workflows" / f"{name}.json").read_text())
        role = workflow_role({"workflow": {"definition": doc}})
        assert role == name
        assert workflow_role({"workflow": {"definition": migrate_notes(doc)}}) == role
