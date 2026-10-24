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


def test_discord_message_needs_actor_channel_and_text() -> None:
    errs = _errs("discord.message", {})
    for name in ("actor", "channel", "text"):
        assert (f"action.params.{name}", "action_param_required") in errs
    ok = {"actor": "discord-bot", "guild": "10", "channel": "101", "text": "hi"}
    assert _errs("discord.message", ok) == set()
    assert (
        _errs("discord.message", {**ok, "guild": None})
        - {("action.params.guild", "action_param_type")}
        == set()
    )


def test_discord_message_is_catalogued_apart_from_the_mesh_message() -> None:
    assert "discord.message" in ACTION_KINDS
    assert "guild" in ACTION_KINDS["discord.message"].params
    assert ACTION_KINDS["message"].summary.lower().startswith("send a message on the culture mesh")


def test_github_push_and_review_reply_required_params() -> None:
    errs = _errs("github.push", {})
    for name in (
        "actor",
        "repo",
        "number",
        "head_branch",
        "expected_head_sha",
        "commit_sha",
        "source",
    ):
        assert (f"action.params.{name}", "action_param_required") in errs
    errs = _errs("github.review_reply", {})
    for name in ("actor", "repo", "number", "comment_id", "body"):
        assert (f"action.params.{name}", "action_param_required") in errs


def test_github_push_accepts_references_and_review_reply_resolve_is_bool() -> None:
    push = {
        "actor": "gh",
        "repo": "{{ trigger.data.repository }}",
        "number": "trigger.data.number",
        "head_branch": "{{ trigger.data.head_branch }}",
        "expected_head_sha": "{{ workflow.outputs.head_before }}",
        "commit_sha": "{{ workflow.outputs.head_after }}",
        "source": "{{ workflow.outputs.bundle }}",
    }
    assert _errs("github.push", push) == set()
    reply = {"actor": "gh", "repo": "o/r", "number": 3, "comment_id": 7, "body": "ok"}
    assert _errs("github.review_reply", {**reply, "resolve": True}) == set()
    assert ("action.params.resolve", "action_param_type") in _errs(
        "github.review_reply", {**reply, "resolve": "yes"}
    )


def test_no_merge_kind_is_catalogued() -> None:
    assert not [k for k in ACTION_KINDS if "merge" in k]


def test_github_push_has_no_mutable_ref_param() -> None:
    assert "ref" not in ACTION_KINDS["github.push"].params
    assert ACTION_KINDS["github.push"].params["commit_sha"].required


def test_a_comment_written_as_the_chains_status_comment() -> None:
    # d26: status is a boolean, and it does not combine with once_key
    params = {"actor": "gh", "repo": "o/r", "number": 3, "body": "x", "status": True}
    assert _errs("github.comment", params) == set()
    params["status"] = "yes"
    assert ("action.params.status", "action_param_type") in _errs("github.comment", params)
    params.update(status=True, once_key="k")
    assert ("action.params.status", "action_param_conflict") in _errs("github.comment", params)


# --- the daily-newspaper kinds (#53 t1) ----------------------------------------------------

EMAIL_SEND = "email.send"
READER = "reader@example.org"
SEARCH_WEB = "search.web"
LLM_COMPLETE = "llm.complete"
LIBRARY_PUSH = "library.push"

NEWSPAPER_KINDS = {
    EMAIL_SEND: {"actor": "mail", "to": [READER], "subject": "Daily", "html": "<p/>"},
    SEARCH_WEB: {"actor": "brave", "query": "jetson news", "count": 10, "freshness": "pd"},
    LLM_COMPLETE: {"actor": "cortex", "content": "results", "max_tokens": 800},
    LIBRARY_PUSH: {"actor": "xteink", "content_base64": "UEsDBA==", "title": "Daily"},
}

REQUIRED = {
    EMAIL_SEND: ("actor", "to", "subject"),
    SEARCH_WEB: ("actor", "query"),
    LLM_COMPLETE: ("actor", "content"),
    LIBRARY_PUSH: ("actor", "content_base64", "title"),
}

OPTIONAL = {
    EMAIL_SEND: ("html", "text"),
    SEARCH_WEB: ("count", "freshness", "country", "lang"),
    LLM_COMPLETE: ("max_tokens", "temperature"),
    LIBRARY_PUSH: ("author", "device_id"),
}


@pytest.mark.parametrize("kind", sorted(NEWSPAPER_KINDS))
def test_newspaper_kind_catalogued(kind: str) -> None:
    assert kind in ACTION_KINDS


@pytest.mark.parametrize("kind", sorted(NEWSPAPER_KINDS))
def test_newspaper_kind_valid_params(kind: str) -> None:
    assert _errs(kind, NEWSPAPER_KINDS[kind]) == set()


@pytest.mark.parametrize("kind", sorted(REQUIRED))
def test_newspaper_kind_required_params(kind: str) -> None:
    errs = _errs(kind, {})
    for name in REQUIRED[kind]:
        assert (f"action.params.{name}", "action_param_required") in errs


@pytest.mark.parametrize("kind", sorted(OPTIONAL))
def test_newspaper_kind_optional_params_declared(kind: str) -> None:
    params = ACTION_KINDS[kind].params
    for name in OPTIONAL[kind]:
        assert not params[name].required, name


@pytest.mark.parametrize(
    ("kind", "name", "value"),
    [
        (EMAIL_SEND, "to", READER),
        (EMAIL_SEND, "subject", 3),
        (SEARCH_WEB, "count", "ten"),
        (SEARCH_WEB, "query", ["a"]),
        (LLM_COMPLETE, "max_tokens", 1.5),
        (LIBRARY_PUSH, "title", {"t": 1}),
    ],
)
def test_newspaper_kind_wrong_type_rejected(kind: str, name: str, value) -> None:
    errs = _errs(kind, {**NEWSPAPER_KINDS[kind], name: value})
    assert (f"action.params.{name}", "action_param_type") in errs


def test_email_send_to_accepts_a_reference() -> None:
    params = {**NEWSPAPER_KINDS[EMAIL_SEND], "to": "{{ inputs.recipients }}"}
    assert _errs(EMAIL_SEND, params) == set()


def test_llm_complete_content_takes_structured_results() -> None:
    params = {**NEWSPAPER_KINDS[LLM_COMPLETE], "content": [{"title": "t", "url": "u"}]}
    assert _errs(LLM_COMPLETE, params) == set()


# --- audio.transcribe (speech-to-text on an endpoint actor) --------------------------------

AUDIO = {
    "actor": "whisper",
    "audio_path": "/data/a.wav",
    "audio_sha256": "0" * 64,
    "output_dir": "/data/out",
}


def test_audio_transcribe_params_and_required_flags() -> None:
    params = ACTION_KINDS["audio.transcribe"].params
    assert set(params) == {"actor", "audio_path", "audio_sha256", "output_dir", "language", "model"}
    for name in ("actor", "audio_path", "audio_sha256", "output_dir"):
        assert params[name].type == "str", name
        assert params[name].required, name
    for name in ("language", "model"):
        assert params[name].type == "str", name
        assert not params[name].required, name


def test_audio_transcribe_valid_and_refuses_a_missing_audio_path() -> None:
    assert _errs("audio.transcribe", AUDIO) == set()
    assert _errs("audio.transcribe", {**AUDIO, "language": "he", "model": "m"}) == set()
    missing = {k: v for k, v in AUDIO.items() if k != "audio_path"}
    assert ("action.params.audio_path", "action_param_required") in _errs(
        "audio.transcribe", missing
    )


def test_llm_complete_takes_content_or_content_path() -> None:
    base = {"actor": "cortex"}
    assert ("action.params.content", "action_param_required") in _errs(LLM_COMPLETE, base)
    for one in ({"content": "text"}, {"content_path": "/t/x.json", "content_root": "/t"}):
        assert not [e for e in _errs(LLM_COMPLETE, {**base, **one}) if e[0].endswith("content")]
