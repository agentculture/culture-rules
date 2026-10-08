"""t7: variable references in rules - the model, matching and save-time checks (c25, h17, c34).

Pure-layer tests; the node-level behaviour lives in ``tests/node/test_shared_variables.py``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine.matching import (
    CONDITION_FALSE,
    FIRE,
    REASONS,
    VARIABLE_UNDEFINED,
    VARIABLES_UNSUPPORTED,
    match,
)
from culture_rules.engine.variables import VARIABLES_CAPABILITY
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION, OFFLINE_AFTER_S
from culture_rules.model.action import Action
from culture_rules.model.refs import resolve_refs
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.validate import validate, variable_ref_errors
from culture_rules.server.service import Definitions, Invalid
from culture_rules.store.memory import MemoryStore

EVENT = {"id": "e1", "type": "pr.comment", "data": {"author": "qodo"}}
IN_X = {"op": "in", "value": {"field": "data.author"}, "items": {"var": "x"}}
NOT_IN_X = {"op": "not", "arg": IN_X}


def rule(id: str = "r", condition: dict | None = None, inputs: dict | None = None) -> Rule:
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params={"type": "pr.comment"}),
        condition=condition,
        workflow=WorkflowRef(id="wf", inputs=inputs) if inputs is not None else None,
        action=Action(kind="noop"),
    )


# --------------------------------------------------------------------------- matching


def test_match_reads_variables_into_the_condition():
    (d,) = match(EVENT, [rule(condition=IN_X)], variables={"x": ["qodo"]})
    assert d.fire
    assert d.reason == FIRE
    (d,) = match(EVENT, [rule(condition=IN_X)], variables={"x": ["someone"]})
    assert not d.fire
    assert d.reason == CONDITION_FALSE


def test_without_variable_support_a_negated_reference_does_not_fire():
    (d,) = match(EVENT, [rule(condition=NOT_IN_X)], variables={}, variables_supported=False)
    assert not d.fire
    assert d.reason == VARIABLES_UNSUPPORTED
    assert "variables" in d.message


def test_an_undefined_variable_never_evaluates_as_missing():
    (d,) = match(EVENT, [rule(condition=NOT_IN_X)], variables={})
    assert not d.fire
    assert d.reason == VARIABLE_UNDEFINED
    assert "x" in d.message


def test_rules_without_references_are_unaffected_by_missing_support():
    (d,) = match(EVENT, [rule()], variables_supported=False)
    assert d.fire


def test_an_input_only_reference_needs_support_too():
    r = rule(inputs={"authors": {"$var": "x"}})
    (d,) = match(EVENT, [r], variables={"x": []}, variables_supported=False)
    assert d.reason == VARIABLES_UNSUPPORTED


def test_the_new_reasons_are_known():
    assert {VARIABLES_UNSUPPORTED, VARIABLE_UNDEFINED} <= set(REASONS)


# --------------------------------------------------------------------------- refs


def test_var_inputs_resolve_against_the_variables_namespace():
    ctx = {"trigger": {}, "variables": {"x": ["a"]}}
    assert resolve_refs({"$var": "x"}, ctx) == ["a"]
    assert resolve_refs({"$var": "missing"}, ctx) is None


def test_var_objects_pass_through_where_variables_are_not_resolved():
    # Action params have no variables namespace: the object stays as written.
    assert resolve_refs({"$var": "x"}, {"trigger": {}}) == {"$var": "x"}


def test_resolved_variable_values_are_copies():
    value = ["a"]
    out = resolve_refs({"$var": "x"}, {"variables": {"x": value}})
    out.append("b")
    assert value == ["a"]


# --------------------------------------------------------------------------- validate


def test_workflow_ref_inputs_accept_a_var_mapping():
    assert validate(rule(inputs={"authors": {"$var": "trusted_authors"}})) == []


@pytest.mark.parametrize("bad", [{"$var": ""}, {"$var": 3}, {"$var": "Bad-Name"}])
def test_a_malformed_var_mapping_is_refused(bad):
    errors = validate(rule(inputs={"authors": bad}))
    assert [e.code for e in errors] == ["invalid_input_mapping"]


def test_variable_ref_errors_name_each_undefined_variable():
    r = rule(condition=NOT_IN_X, inputs={"authors": {"$var": "y"}, "z": {"$var": "x"}})
    errors = variable_ref_errors(r, {"x"})
    assert [(e.path, e.code) for e in errors] == [("workflow.inputs.authors", "variable_undefined")]
    assert "'y'" in errors[0].message
    errors = variable_ref_errors(r, set())
    assert [e.path for e in errors] == [
        "condition",
        "workflow.inputs.authors",
        "workflow.inputs.z",
    ]


# --------------------------------------------------------------------------- save


def _body(condition: dict) -> dict:
    return rule("guarded", condition=condition).to_dict()


def test_saving_a_rule_that_references_an_undefined_variable_is_refused():
    store = MemoryStore()
    defs = Definitions(store)
    cond = {"op": "in", "value": {"field": "data.author"}, "items": {"var": "missing"}}
    with pytest.raises(Invalid) as exc:
        defs.create("rules", _body(cond), "alice")
    (err,) = exc.value.errors
    assert err["code"] == "variable_undefined"
    assert "missing" in err["message"]
    assert store.find("rules") == []

    store.put_variable("missing", ["qodo"], updated_by="alice")
    assert defs.create("rules", _body(cond), "alice")["id"] == "guarded"


def test_importing_a_rule_that_references_an_undefined_variable_is_refused():
    store = MemoryStore()
    defs = Definitions(store)
    files = {"rules/guarded.json": json.dumps(_body(NOT_IN_X))}
    with pytest.raises(Invalid) as exc:
        defs.import_files(files, "alice", apply=True)
    assert any(
        e["code"] == "variable_undefined" and "'x'" in e["message"] for e in exc.value.errors
    )
    assert store.find("rules") == []


# --------------------------------------------------------------------------- d7: old nodes

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


def _beat(store: MemoryStore, machine: str, at: datetime, capabilities=None) -> None:
    doc = {"id": machine, "machine": machine, "ts": at.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if capabilities is not None:
        doc["capabilities"] = list(capabilities)
    store.put(HEARTBEAT_COLLECTION, doc)


def _defs_with_trusted() -> tuple[MemoryStore, Definitions]:
    store = MemoryStore()
    store.put_variable("x", ["qodo"], updated_by="alice")
    return store, Definitions(store, clock=lambda: NOW)


def test_an_online_node_without_variable_support_blocks_a_variable_rule_save():
    store, defs = _defs_with_trusted()
    _beat(store, "spark", NOW, capabilities=[VARIABLES_CAPABILITY])
    _beat(store, "orin", NOW)  # a 0.12.0 heartbeat: no capabilities field at all
    with pytest.raises(Invalid) as exc:
        defs.create("rules", _body(NOT_IN_X), "alice")
    (err,) = exc.value.errors
    assert err["code"] == "variables_unsupported_nodes"
    assert "orin" in err["message"]
    assert "spark" not in err["message"]
    assert store.find("rules") == []

    _beat(store, "orin", NOW, capabilities=[VARIABLES_CAPABILITY])  # upgraded
    assert defs.create("rules", _body(NOT_IN_X), "alice")["id"] == "guarded"


def test_a_stale_node_without_variable_support_does_not_block():
    store, defs = _defs_with_trusted()
    _beat(store, "orin", NOW - timedelta(seconds=OFFLINE_AFTER_S))  # offline by the status rule
    assert defs.create("rules", _body(NOT_IN_X), "alice")["id"] == "guarded"


def test_an_old_node_blocks_update_and_import_of_a_variable_rule_too():
    store, defs = _defs_with_trusted()
    defs.create("rules", _body(NOT_IN_X), "alice")
    _beat(store, "orin", NOW, capabilities=[])
    with pytest.raises(Invalid) as exc:
        defs.update("rules", "guarded", _body(IN_X), "alice")
    assert [e["code"] for e in exc.value.errors] == ["variables_unsupported_nodes"]
    assert store.get("rules", "guarded")["condition"] == NOT_IN_X  # unchanged
    files = {"rules/other.json": json.dumps(rule("other", condition=IN_X).to_dict())}
    with pytest.raises(Invalid) as exc:
        defs.import_files(files, "alice", apply=True)
    assert any(
        e["code"] == "variables_unsupported_nodes" and "orin" in e["message"]
        for e in exc.value.errors
    )


def test_a_variable_free_rule_saves_regardless_of_old_nodes():
    store, defs = _defs_with_trusted()
    _beat(store, "orin", NOW)
    assert defs.create("rules", rule("plain").to_dict(), "alice")["id"] == "plain"
    files = {"rules/plain2.json": json.dumps(rule("plain2").to_dict())}
    assert defs.import_files(files, "alice", apply=True)["applied"] is True


def test_an_old_node_blocks_enabling_a_disabled_variable_rule_but_never_disabling():
    store, defs = _defs_with_trusted()
    body = {**_body(NOT_IN_X), "enabled": False}
    defs.create("rules", body, "alice")  # the fixer ships disabled (t17)
    _beat(store, "orin", NOW)  # an old node comes online
    with pytest.raises(Invalid) as exc:
        defs.set_enabled("rules", "guarded", True, "alice")
    (err,) = exc.value.errors
    assert err["code"] == "variables_unsupported_nodes"
    assert "orin" in err["message"]
    assert store.get("rules", "guarded")["enabled"] is False

    _beat(store, "orin", NOW, capabilities=[VARIABLES_CAPABILITY])
    assert defs.set_enabled("rules", "guarded", True, "alice")["enabled"] is True
    _beat(store, "orin", NOW)  # rolled back to the old binary
    assert defs.set_enabled("rules", "guarded", False, "alice")["enabled"] is False


def test_saving_a_disabled_variable_rule_is_checked_too():
    store, defs = _defs_with_trusted()
    _beat(store, "orin", NOW)
    body = {**_body(NOT_IN_X), "enabled": False}
    with pytest.raises(Invalid) as exc:
        defs.create("rules", body, "alice")
    assert [e["code"] for e in exc.value.errors] == ["variables_unsupported_nodes"]


def test_enabling_a_variable_free_rule_is_never_blocked():
    store, defs = _defs_with_trusted()
    defs.create("rules", {**rule("plain").to_dict(), "enabled": False}, "alice")
    _beat(store, "orin", NOW)
    assert defs.set_enabled("rules", "plain", True, "alice")["enabled"] is True


# --------------------------------------------------------------------------- review: supersede


def _plain(id: str, **kw) -> Rule:
    return replace(rule(id), **kw)


def test_a_refused_superseder_refuses_what_it_supersedes():
    never = {"op": "compare", "cmp": "==", "left": {"literal": 1}, "right": {"literal": 2}}
    a = replace(rule("a", condition=IN_X), supersedes=("b", "m"))
    b = _plain("b")
    m = _plain("m", supersedes=("c",), condition=never)  # does not match: chain passes on
    c = _plain("c")
    out = {d.rule_id: d for d in match(EVENT, [a, b, m, c], variables_supported=False)}
    assert out["a"].reason == VARIABLES_UNSUPPORTED
    for rid in ("b", "c"):  # directly and transitively: a matched a would suppress both
        assert not out[rid].fire
        assert out[rid].reason == VARIABLES_UNSUPPORTED
        assert out[rid].by == ("a",)
        assert "a" in out[rid].message
    out = {d.rule_id: d for d in match(EVENT, [a, b, m, c], variables={})}
    assert out["b"].reason == VARIABLE_UNDEFINED
    assert not out["b"].fire


def test_an_evaluable_superseder_is_unchanged():
    a = replace(rule("a", condition=IN_X), supersedes=("b",))
    out = {d.rule_id: d for d in match(EVENT, [a, _plain("b")], variables={"x": ["nobody"]})}
    assert out["a"].reason == CONDITION_FALSE
    assert out["b"].fire  # a really did not match: b fires as before


def test_a_refused_group_member_that_could_win_blocks_the_winner():
    hi = replace(rule("hi", condition=IN_X), exclusive_group="g", priority=9)
    lo = _plain("lo", exclusive_group="g", priority=1)
    out = {d.rule_id: d for d in match(EVENT, [hi, lo], variables_supported=False)}
    assert not out["lo"].fire
    assert out["lo"].reason == VARIABLES_UNSUPPORTED
    assert out["lo"].by == ("hi",)
    # a refused member that would lose anyway does not block the winner
    weak = replace(hi, priority=0)
    out = {d.rule_id: d for d in match(EVENT, [weak, lo], variables_supported=False)}
    assert out["lo"].fire


def test_a_refused_rule_superseded_by_a_matched_rule_is_no_group_rival():
    # a (refused) can never win: matched b supersedes it whatever x holds, so c wins.
    a = replace(rule("a", condition=IN_X), exclusive_group="g", priority=9)
    b = _plain("b", supersedes=("a",))
    c = _plain("c", exclusive_group="g", priority=1)
    for kw in ({"variables_supported": False}, {"variables": {}}):
        out = {d.rule_id: d for d in match(EVENT, [a, b, c], **kw)}
        assert not out["a"].fire
        assert out["b"].fire
        assert out["c"].fire, out["c"]


# --------------------------------------------------------------------------- d7: restore (wave-3)


def _deleted_guarded(enabled: bool = True):
    from culture_rules.engine.audit import AuditLog
    from culture_rules.engine.lifecycle import Lifecycle

    store, defs = _defs_with_trusted()
    defs.create("rules", {**_body(NOT_IN_X), "enabled": enabled}, "alice")
    life = Lifecycle(store, AuditLog(), clock=lambda: NOW)
    life.soft_delete("rules", "guarded", "alice")
    return store, defs, life


def test_restoring_an_enabled_variable_rule_is_refused_while_an_old_node_is_online():
    store, defs, life = _deleted_guarded()
    _beat(store, "orin", NOW)  # an old node came online after the delete
    with pytest.raises(Invalid) as exc:
        defs.restore("rules", "guarded", "alice", life)
    (err,) = exc.value.errors
    assert err["code"] == "variables_unsupported_nodes"
    assert "orin" in err["message"]
    assert store.get("rules", "guarded")["deleted_at"]  # still tombstoned: never went live

    _beat(store, "orin", NOW, capabilities=[VARIABLES_CAPABILITY])  # upgraded
    doc = defs.restore("rules", "guarded", "alice", life)
    assert not doc.get("deleted_at")
    assert doc["enabled"] is True


def test_restoring_a_disabled_variable_rule_or_a_plain_rule_is_not_blocked():
    store, defs, life = _deleted_guarded(enabled=False)
    _beat(store, "orin", NOW)
    assert not defs.restore("rules", "guarded", "alice", life).get("deleted_at")
    with pytest.raises(Invalid):  # enabling it is still where d7 refuses
        defs.set_enabled("rules", "guarded", True, "alice")
    defs.create("rules", rule("plain").to_dict(), "alice")
    life.soft_delete("rules", "plain", "alice")
    assert not defs.restore("rules", "plain", "alice", life).get("deleted_at")
