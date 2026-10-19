import copy

import pytest

from culture_rules.model.references import NoMatchingStep, find_references, repoint_workflow


def _act(actor):
    return {"builtin": "action", "action": {"kind": "llm.complete", "params": {"actor": actor}}}


def _wf():
    return {
        "id": "news",
        "enabled": True,
        "description": "uses a1 daily",
        "notes": "a1 is the model",
        "steps": [
            {
                "id": "loop",
                "config": {"items": "items"},
                "body": [
                    {
                        "id": "inner",
                        "config": {},
                        "body": [
                            {"id": "deep", "config": _act("a1"), "notes": "talks to a1", "body": []}
                        ],
                    },
                    {"id": "other", "config": _act("a2"), "body": []},
                ],
            },
            {"id": "placed", "config": {}, "placement": {"actor": "a1"}, "body": []},
            {"id": "plain", "config": _act("a1"), "placement": {"actor": "a1"}, "body": []},
        ],
    }


def _rules():
    return [
        {"id": "r1", "enabled": False, "action": {"kind": "message", "params": {"actor": "a1"}}},
        {
            "id": "r2",
            "action": {"kind": "message", "params": {"actor": "a2"}},
            "placement": {"actor": "a1"},
        },
    ]


def test_find_references_all_forms():
    refs = find_references("a1", [_wf()], _rules())
    got = {(r["kind"], r["id"], r["path"], r["via"], r["enabled"]) for r in refs}
    assert got == {
        ("workflow", "news", "loop.body[inner].body[deep]", "action", True),
        ("workflow", "news", "placed", "placement", True),
        ("workflow", "news", "plain", "action", True),
        ("workflow", "news", "plain", "placement", True),
        ("rule", "r1", "action", "action", False),
        ("rule", "r2", "placement", "placement", True),
    }


def test_repoint_rewrites_only_actions_and_does_not_mutate():
    doc = _wf()
    before = copy.deepcopy(doc)
    new, changed, warnings = repoint_workflow(doc, "a1", "b9")
    assert doc == before
    assert changed == ["loop.body[inner].body[deep]", "plain"]
    deep = new["steps"][0]["body"][0]["body"][0]
    assert deep["config"]["action"]["params"]["actor"] == "b9"
    assert new["steps"][0]["body"][1]["config"]["action"]["params"]["actor"] == "a2"
    assert new["steps"][1]["placement"] == {"actor": "a1"}
    assert new["description"] == doc["description"] and new["notes"] == doc["notes"]
    assert any("placed" in w and "placement" in w for w in warnings)
    assert any("notes" in w and "deep" in w for w in warnings)
    assert any("workflow notes" in w for w in warnings)


def test_repoint_no_match_raises_typed():
    with pytest.raises(NoMatchingStep):
        repoint_workflow(_wf(), "zzz", "b9")
    with pytest.raises(ValueError):
        repoint_workflow({"id": "x", "steps": []}, "a1", "b9")


def test_notes_warning_matches_whole_actor_ids_only():
    from culture_rules.model.references import _mentions

    assert _mentions("llm.complete on a1 (pool p)", "a1")
    assert _mentions("ends with a1.", "a1")
    assert not _mentions("llm.complete on a10", "a1")
    assert not _mentions("on cortex-spark2", "cortex-spark")
    assert not _mentions("on xa1", "a1")
