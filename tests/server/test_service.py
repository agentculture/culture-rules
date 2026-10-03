"""Definition verbs behind the API: validation, rule-set checks, audit (stdlib-only layer)."""

from __future__ import annotations

import pytest

from culture_rules.engine.audit import AUDIT_COLLECTION, MUTATING_VERBS
from culture_rules.server.service import Conflict, Definitions, Invalid, NotFound
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import rule, step, workflow


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def defs(store):
    return Definitions(store)


def rb(id="r1", **changes):
    body = rule(id=id, workflow_id=None).to_dict()
    body.update(changes)
    return body


def test_create_then_get_roundtrips_and_audits(defs, store):
    doc = defs.create("rules", rb(), "alice")
    assert doc["id"] == "r1"
    assert defs.get("rules", "r1")["name"] == "r1"
    entry = store.find(AUDIT_COLLECTION)[0]
    assert entry["identity"] == "alice" and entry["verb"] == "definitions.create"


def test_create_twice_conflicts_and_missing_update_is_not_found(defs):
    defs.create("rules", rb(), "alice")
    with pytest.raises(Conflict):
        defs.create("rules", rb(), "alice")
    with pytest.raises(NotFound):
        defs.update("rules", "nope", rb("nope"), "alice")


def test_invalid_definition_reports_errors_and_writes_nothing(defs, store):
    bad = rb()
    bad["trigger"] = {"kind": "", "params": {}}
    with pytest.raises(Invalid) as exc:
        defs.create("rules", bad, "alice")
    assert exc.value.errors
    assert store.find("rules") == [] and store.find(AUDIT_COLLECTION) == []


def test_path_id_must_match_body_id(defs):
    defs.create("rules", rb(), "alice")
    with pytest.raises(Invalid):
        defs.update("rules", "r1", rb("other"), "alice")


def test_rule_save_runs_validate_rule_set_cycle(defs):
    defs.create("rules", rb("a", must_after=["b"]), "alice")
    with pytest.raises(Invalid) as exc:
        defs.create("rules", rb("b", must_after=["a"]), "alice")
    assert any(e["code"] == "predecessor_cycle" for e in exc.value.errors)
    assert defs.list("rules") == [defs.get("rules", "a")]


def test_rule_update_runs_rule_set_check_too(defs):
    defs.create("rules", rb("a"), "alice")
    defs.create("rules", rb("b", must_after=["a"]), "alice")
    with pytest.raises(Invalid) as exc:
        defs.update("rules", "a", rb("a", must_after=["b"]), "alice")
    assert any(e["code"] == "predecessor_cycle" for e in exc.value.errors)


def test_enabled_toggle_and_listing_hides_deleted(defs):
    defs.create("rules", rb(), "alice")
    assert defs.set_enabled("rules", "r1", False, "alice")["enabled"] is False
    assert defs.set_enabled("rules", "r1", True, "alice")["enabled"] is True


def test_machines_are_keyed_by_name(defs):
    defs.create("machines", {"name": "thor", "roles": ["engine_node"]}, "alice")
    assert defs.get("machines", "thor")["id"] == "thor"


def test_import_dry_run_changes_nothing_apply_commits_with_one_audit_entry(defs, store):
    files = {"rules/r1.json": __import__("json").dumps(rb())}
    plan = defs.import_files(files, "alice")
    assert plan["applied"] is False and plan["changes"][0]["action"] == "add"
    assert store.find("rules") == []
    plan = defs.import_files(files, "alice", apply=True)
    assert plan["applied"] is True and store.get("rules", "r1")
    assert len(store.find(AUDIT_COLLECTION)) == 1
    again = defs.import_files(files, "alice")
    assert again["changes"][0]["action"] == "unchanged"


def test_import_refuses_rule_set_errors(defs):
    import json

    files = {
        "rules/a.json": json.dumps(rb("a", must_after=["b"])),
        "rules/b.json": json.dumps(rb("b", must_after=["a"])),
    }
    with pytest.raises(Invalid) as exc:
        defs.import_files(files, "alice", apply=True)
    assert any(e["code"] == "predecessor_cycle" for e in exc.value.errors)


def test_new_mutating_verbs_are_registered():
    for verb in (
        "definitions.create",
        "definitions.update",
        "definitions.set_enabled",
        "definitions.import",
    ):
        assert verb in MUTATING_VERBS


def test_workflow_kind_validates(defs):
    defs.create("workflows", workflow((step("a"),), id="wf").to_dict(), "alice")
    with pytest.raises(Invalid):
        defs.create("workflows", {"id": "x"}, "alice")


def test_malformed_stored_rule_does_not_block_unrelated_saves(defs, store):
    store.put("rules", {"id": "junk", "name": "no trigger"})
    assert defs.create("rules", rb("fine"), "alice")["id"] == "fine"
