"""The message action: Discord (app actor) or the mesh (no actor), mentions suppressed."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

from culture_rules.engine.actorport import FAILED, InvocationContext
from culture_rules.node.actions.message import MessageAction
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def ctx(actor=None):
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor=actor)


class FakeTransport:
    def __init__(self, status=200, body=None):
        self.calls = []
        self._r = (status, body if body is not None else {"id": "m9"}, {})

    def __call__(self, url, data, headers, timeout):
        self.calls.append((url, json.loads(data), headers))
        return self._r


def store_with(**connection):
    s = MemoryStore()
    s.put(
        "actors",
        {
            "id": "disc",
            "name": "Disc",
            "kind": "service",
            "surface": "discord",
            "connection": {"bot_token": "grant:DISCORD_BOT", **connection},
        },
    )
    return s


def action(store, transport=None, run=None):
    return MessageAction(
        store,
        resolve_secret=lambda ref: "tok-xyz",
        transport=transport,
        mesh_executable="/usr/bin/culture",
        mesh_run=run,
    )


def test_discord_message_with_everyone_is_suppressed():
    t = FakeTransport()
    res = action(store_with(), t).invoke(
        {"channel": "42", "text": "hey @everyone"}, "k", NOW, context=ctx("disc")
    )
    assert res.outcome == "completed"
    assert res.output == {"message_id": "m9"}
    url, body, headers = t.calls[0]
    assert url.endswith("/channels/42/messages")
    assert body["allowed_mentions"] == {"parse": []}
    assert headers["Authorization"] == "Bot tok-xyz"


def test_mesh_target_runs_culture_channel_message_argv():
    calls = []
    res = action(MemoryStore(), run=lambda argv, **kw: calls.append((argv, kw))).invoke(
        {"channel": "#ops", "text": "hello mesh"}, "k", NOW, context=ctx()
    )
    assert res.outcome == "completed"
    argv, kw = calls[0]
    assert argv == ["/usr/bin/culture", "channel", "message", "#ops", "hello mesh"]
    assert isinstance(argv, list)
    assert not kw.get("shell")


def test_mesh_failure_is_retryable_failed():
    def boom(argv, **kw):
        raise OSError("nope")

    res = action(MemoryStore(), run=boom).invoke(
        {"channel": "#c", "text": "t"}, "k", NOW, context=ctx()
    )
    assert res.outcome == FAILED
    assert res.retryable


def test_channel_allow_list_enforced_non_retryable():
    t = FakeTransport()
    res = action(store_with(channels=["1"]), t).invoke(
        {"channel": "2", "text": "x"}, "k", NOW, context=ctx("disc")
    )
    assert res.outcome == FAILED
    assert not res.retryable
    assert not t.calls


def test_unknown_actor_and_non_discord_actor_fail_non_retryable():
    s = store_with()
    s.put("actors", {"id": "bob", "name": "Bob", "kind": "human"})
    for actor in ("ghost", "bob"):
        res = action(s, FakeTransport()).invoke(
            {"channel": "1", "text": "x"}, "k", NOW, context=ctx(actor)
        )
        assert res.outcome == FAILED
        assert not res.retryable


def test_discord_429_and_4xx_classification_without_leaks():
    res = action(store_with(), FakeTransport(429, {"retry_after": 2})).invoke(
        {"channel": "1", "text": "secret body"}, "k", NOW, context=ctx("disc")
    )
    assert res.outcome == FAILED
    assert res.retryable
    res = action(store_with(), FakeTransport(404, {})).invoke(
        {"channel": "1", "text": "secret body"}, "k", NOW, context=ctx("disc")
    )
    assert not res.retryable
    assert "secret body" not in res.error
    assert "tok-xyz" not in res.error


def test_missing_text_or_channel_non_retryable():
    res = action(MemoryStore()).invoke({"channel": "#c"}, "k", NOW, context=ctx())
    assert res.outcome == FAILED
    assert not res.retryable


def test_a_mesh_timeout_is_an_unknown_outcome_and_not_retryable():
    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout", 15))

    res = action(MemoryStore(), run=slow).invoke(
        {"channel": "#c", "text": "t"}, "k", NOW, context=ctx()
    )
    assert res.outcome == FAILED
    assert not res.retryable


def test_a_single_channel_string_allow_list_is_one_channel():
    t = FakeTransport()
    store = store_with(channels="42")
    ok = action(store, t).invoke({"channel": "42", "text": "x"}, "k", NOW, context=ctx("disc"))
    assert ok.outcome == "completed"
    no = action(store, t).invoke({"channel": "4", "text": "x"}, "k", NOW, context=ctx("disc"))
    assert no.outcome == FAILED and not no.retryable
