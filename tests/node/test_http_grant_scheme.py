"""http.call: a header ``<word> grant:<NAME>`` resolves to ``<word> <value>``, nothing looser."""

from __future__ import annotations

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED

from .test_http_call import FakeResponse, _call, _invoke, _port, _store

SECRET = "s3cr3t-value"


def _runner(seen):
    def secret_runner(name):
        seen.append(name)
        return SECRET

    return secret_runner


def test_word_then_grant_on_the_actor_policy_is_sent_with_the_word() -> None:
    port, calls, _ = _port(
        _store(headers={"Authorization": "Bearer grant:API_TOKEN"}),
        response=FakeResponse(200, b"echo " + SECRET.encode()),
        secret_runner=_runner([]),
    )
    res = _invoke(port, _call())
    assert res.outcome == COMPLETED
    assert calls[0]["request"].get_header("Authorization") == f"Bearer {SECRET}"


def test_word_then_grant_on_the_step_is_sent_with_the_word() -> None:
    port, calls, _ = _port(_store(), secret_runner=_runner([]))
    res = _invoke(port, _call(headers={"Authorization": "Bearer grant:API_TOKEN"}))
    assert res.outcome == COMPLETED
    assert calls[0]["request"].get_header("Authorization") == f"Bearer {SECRET}"


def test_word_then_grant_resolves_the_name_after_the_prefix() -> None:
    seen: list[str] = []
    port, _, _ = _port(_store(), secret_runner=_runner(seen))
    _invoke(port, _call(headers={"Authorization": "Token grant:API_TOKEN"}))
    assert seen == ["API_TOKEN"]


def test_resolved_value_never_appears_in_the_step_output() -> None:
    port, _, _ = _port(
        _store(),
        response=FakeResponse(200, b"echo " + SECRET.encode()),
        secret_runner=_runner([]),
    )
    res = _invoke(port, _call(headers={"Authorization": "Bearer grant:API_TOKEN"}))
    assert SECRET not in res.output["body"]


@pytest.mark.parametrize(
    "value",
    [
        "x grant:A grant:B",
        "Bearer extra grant:A",
        "Bearer grant:A ",
        "Bearer  grant:A",
        "1Bearer grant:A",
        "Bearer grant:A trailing",
    ],
)
def test_other_shapes_are_sent_unchanged_and_never_resolved(value) -> None:
    seen: list[str] = []
    port, calls, _ = _port(_store(), secret_runner=_runner(seen))
    res = _invoke(port, _call(headers={"X-Odd": value}))
    assert res.outcome == COMPLETED
    assert calls[0]["request"].get_header("X-odd") == value
    assert seen == []


def test_bare_grant_is_still_resolved_without_a_word() -> None:
    port, calls, _ = _port(_store(), secret_runner=_runner([]))
    _invoke(port, _call(headers={"X-Key": "grant:API_TOKEN"}))
    assert calls[0]["request"].get_header("X-key") == SECRET


def test_unresolvable_reference_after_a_word_fails_naming_the_header() -> None:
    def secret_runner(name):
        raise RuntimeError("nope")

    port, calls, _ = _port(_store(), secret_runner=secret_runner)
    res = _invoke(port, _call(headers={"Authorization": "Bearer grant:MISSING"}))
    assert res.outcome == FAILED
    assert res.error.startswith("secret_unresolved")
    assert "Authorization" in res.error
    assert calls == []
