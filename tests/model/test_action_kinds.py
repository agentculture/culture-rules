"""Action kind catalog: typed params and the params.actor convention (spec c11)."""

from __future__ import annotations

import pytest

from culture_rules.model.action import Action
from culture_rules.model.action_kinds import ACTION_KINDS
from culture_rules.model.rule import Rule
from culture_rules.model.validate import validate
from tests.model.factories import make_rule


def _errs(kind: str, params: dict) -> set[tuple[str, str]]:
    rule = make_rule(action=Action(kind=kind, params=params))
    return {(e.path, e.code) for e in validate(rule)}


def test_catalog_lists_all_kinds() -> None:
    assert {
        "noop",
        "message",
        "github.comment",
        "jira.comment",
        "http.call",
        "machine.command",
    } <= set(ACTION_KINDS)


def test_github_comment_missing_params() -> None:
    errs = _errs("github.comment", {})
    for name in ("repo", "number", "body", "actor"):
        assert (f"action.params.{name}", "action_param_required") in errs


def test_machine_command_missing_actor_command() -> None:
    errs = _errs("machine.command", {})
    assert ("action.params.actor", "action_param_required") in errs
    assert ("action.params.command", "action_param_required") in errs


def test_valid_actions() -> None:
    assert _errs("noop", {}) == set()
    assert (
        _errs("machine.command", {"actor": "runner", "command": "ls", "args": {"path": "/tmp"}})
        == set()
    )
    assert (
        _errs(
            "github.comment",
            {"actor": "gh", "repo": "o/r", "number": 3, "body": "hi {{ workflow.outputs.x }}"},
        )
        == set()
    )


def test_reference_accepted_where_number_expected() -> None:
    params = {"actor": "gh", "repo": "o/r", "number": "trigger.data.number", "body": "x"}
    assert _errs("github.comment", params) == set()
    params["number"] = {"$ref": "trigger.data.number"}
    assert _errs("github.comment", params) == set()
    params["number"] = "{{ trigger.data.number }}"
    assert _errs("github.comment", params) == set()


def test_wrong_type_rejected() -> None:
    params = {"actor": "gh", "repo": "o/r", "number": "abc", "body": "x"}
    assert ("action.params.number", "action_param_type") in _errs("github.comment", params)
    params = {"actor": "r", "command": "ls", "args": "-l"}
    assert ("action.params.args", "action_param_type") in _errs("machine.command", params)
    params["args"] = ["-l"]  # args is a mapping of declared param name -> value, not a list
    assert ("action.params.args", "action_param_type") in _errs("machine.command", params)
    params["args"] = {"path": "{{ trigger.data.path }}"}
    assert _errs("machine.command", params) == set()
    params["args"] = "{{ trigger.data.args }}"
    assert _errs("machine.command", params) == set()


def test_unknown_kind_rejected_on_validate() -> None:
    assert ("action.kind", "action_kind_unknown") in _errs("teleport", {})


def test_legacy_mesh_message_alias() -> None:
    assert _errs("mesh.message", {}) == set()


def test_message_actor_optional_for_mesh() -> None:
    assert _errs("message", {"channel": "#ops", "text": "hi"}) == set()


def test_strict_false_load_keeps_unknown_kind() -> None:
    act = Action.from_dict({"kind": "teleport"}, strict=False)
    assert act.kind == "teleport"


def _stored_rule(action: dict, trigger: dict | None = None) -> Rule:
    data = make_rule().to_dict()
    data["action"] = action
    if trigger is not None:
        data["trigger"] = trigger
    return Rule.from_dict(data, strict=False)


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "comment", "params": {"body": "hi"}},
        {"kind": "http.get", "params": {"url": "http://localhost:1"}},
        {"kind": "github.comment", "params": {"body": "hi", "repo": "o/r"}},
        {"kind": "github.comment", "params": {"number": "not-a-number"}},
    ],
)
def test_stored_mode_skips_action_catalog_checks(action: dict) -> None:
    rule = _stored_rule(action)
    assert validate(rule) != []
    assert validate(rule, stored=True) == []


@pytest.mark.parametrize(
    "trigger",
    [
        {"kind": "event", "params": {}},
        {"kind": "schedule", "params": {}},
        {"kind": "probe", "params": {"mode": "sometimes"}},
        {"kind": "webhook", "params": {}},
    ],
)
def test_stored_mode_skips_trigger_param_checks(trigger: dict) -> None:
    rule = _stored_rule({"kind": "noop"}, trigger)
    assert validate(rule) != []
    assert validate(rule, stored=True) == []


def test_stored_mode_keeps_structural_checks() -> None:
    rule = _stored_rule({"kind": "", "params": {}}, {"kind": "", "params": {}})
    errs = {(e.path, e.code) for e in validate(rule, stored=True)}
    assert ("action.kind", "empty") in errs
    assert ("trigger.kind", "empty") in errs


@pytest.mark.parametrize("kind", sorted(ACTION_KINDS))
def test_every_kind_has_spec(kind: str) -> None:
    spec = ACTION_KINDS[kind]
    assert isinstance(spec.params, dict)
