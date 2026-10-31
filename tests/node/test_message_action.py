"""The message action: Discord (app actor) or the mesh (no actor), mentions suppressed."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

import pytest

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
    assert no.outcome == FAILED
    assert not no.retryable


def test_discord_message_kind_posts_through_the_named_actor():
    from culture_rules.node.actions.message import DiscordMessageAction

    t = FakeTransport()
    port = DiscordMessageAction(
        store_with(), resolve_secret=lambda ref: "tok-xyz", transport=t, mesh_executable="/x"
    )
    res = port.invoke(
        {"actor": "disc", "guild": "1", "channel": "42", "text": "hi"},
        "k",
        NOW,
        context=ctx("disc"),
    )
    assert res.outcome == "completed"
    assert t.calls[0][0].endswith("/channels/42/messages")


def test_discord_message_kind_without_an_actor_never_falls_back_to_the_mesh():
    from culture_rules.node.actions.message import DiscordMessageAction

    calls = []
    port = DiscordMessageAction(
        MemoryStore(),
        resolve_secret=lambda ref: "t",
        mesh_executable="/x",
        mesh_run=lambda argv, **kw: calls.append(argv),
    )
    res = port.invoke({"channel": "42", "text": "hi"}, "k", NOW, context=ctx())
    assert res.outcome == FAILED
    assert not res.retryable
    assert calls == []


def _discord_kind(t, secrets_seen=None):
    from culture_rules.node.actions.message import DiscordMessageAction

    def resolve(ref):
        if secrets_seen is not None:
            secrets_seen.append(ref)
        return "tok-xyz"

    return DiscordMessageAction(store_with(), resolve_secret=resolve, transport=t)


def _btn(**kw):
    return {"label": "Go", "style": "success", "custom_id": "go:1", **kw}


def _send(port, **extra):
    return port.invoke(
        {"actor": "disc", "channel": "42", "text": "hi", **extra}, "k", NOW, context=ctx("disc")
    )


def test_discord_message_with_buttons_posts_components_and_keeps_message_id():
    t = FakeTransport()
    res = _send(_discord_kind(t), buttons=[_btn(), _btn(style="danger", custom_id="no")])
    assert res.outcome == "completed"
    assert res.output == {"message_id": "m9", "custom_ids": ["go:1", "no"]}
    assert t.calls[0][1]["components"] == [
        {
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": "Go", "custom_id": "go:1"},
                {"type": 2, "style": 4, "label": "Go", "custom_id": "no"},
            ],
        }
    ]


def test_discord_message_without_buttons_body_is_unchanged():
    expected = {"content": "hi", "allowed_mentions": {"parse": []}}
    for extra in ({}, {"buttons": []}, {"buttons": None}):
        t = FakeTransport()
        assert _send(_discord_kind(t), **extra).outcome == "completed"
        assert t.calls[0][1] == expected


def test_discord_message_bad_buttons_are_refused_before_any_secret_or_post():
    bad = [
        [_btn(custom_id="x" * 101)],
        [_btn(custom_id=str(i)) for i in range(6)],
        [_btn(style="purple")],
        [_btn(label="")],
        [_btn(label="y" * 81)],
        [{"label": "a", "style": "success"}],
        [_btn(), _btn()],
        ["go"],
        "go",
    ]
    for buttons in bad:
        t, seen = FakeTransport(), []
        res = _send(_discord_kind(t, seen), buttons=buttons)
        assert res.outcome == FAILED
        assert res.error.startswith("bad_params")
        assert not res.retryable
        assert t.calls == []
        assert seen == []


def test_discord_message_button_limits_are_inclusive():
    t = FakeTransport()
    buttons = [_btn(custom_id="z" * 100 if i == 0 else str(i)) for i in range(5)]
    assert _send(_discord_kind(t), buttons=buttons).outcome == "completed"


# ---------------------------------------------------------------- valued buttons (d1)

VALUE = {"repo": "o/r", "number": 7, "sha": "a" * 40}


def _valued_port(t, secrets_seen=None):
    port = _discord_kind(t, secrets_seen)
    return port, port._store


def _stored(store):
    from culture_rules.apps.discord_buttons import BUTTONS_COLLECTION

    return store.find(BUTTONS_COLLECTION)


def test_a_valued_button_posts_a_generated_id_and_stores_its_value():
    t = FakeTransport()
    port, store = _valued_port(t)
    res = _send(port, buttons=[_btn(custom_id="merge", value=VALUE), _btn(custom_id="no")])
    assert res.outcome == "completed"
    posted = [c["custom_id"] for c in t.calls[0][1]["components"][0]["components"]]
    assert posted[0].startswith("merge:")
    assert posted[1] == "no"
    assert res.output == {"message_id": "m9", "custom_ids": posted}
    (doc,) = _stored(store)
    assert doc["id"] == f"disc:{posted[0]}"
    assert (doc["actor_id"], doc["custom_id"], doc["channel_id"]) == ("disc", posted[0], "42")
    assert doc["value"] == VALUE
    assert doc["message_id"] == "m9"
    assert "value" not in json.dumps(t.calls[0][1]["components"])


def test_the_value_is_stored_before_the_post():
    port, store = _valued_port(None)
    seen = []

    def transport(url, data, headers, timeout):
        seen.append([d["value"] for d in _stored(store)])
        return 200, {"id": "m9"}, {}

    port._transport = transport
    assert _send(port, buttons=[_btn(custom_id="merge", value=VALUE)]).outcome == "completed"
    assert seen == [[VALUE]]


def test_a_refused_post_drops_the_stored_values_and_fails_as_before():
    t = FakeTransport(status=403, body={})
    port, store = _valued_port(t)
    res = _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    assert (res.outcome, res.retryable) == (FAILED, False)
    assert _stored(store) == []


def test_a_bad_request_post_drops_the_stored_values():
    port, store = _valued_port(FakeTransport(status=400, body={}))
    _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    assert _stored(store) == []


def test_a_5xx_post_keeps_the_stored_values():
    # Discord may have posted the message before the 5xx: its button keeps its meaning
    t = FakeTransport(status=500, body={})
    port, store = _valued_port(t)
    res = _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    assert (res.outcome, res.retryable) == (FAILED, True)
    assert [d["value"] for d in _stored(store)] == [VALUE]


def test_a_rate_limited_post_keeps_the_stored_values():
    port, store = _valued_port(FakeTransport(status=429, body={"retry_after": 1}))
    res = _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    assert res.retryable
    assert [d["value"] for d in _stored(store)] == [VALUE]


def _raising(exc):
    def transport(url, data, headers, timeout):
        raise exc

    return transport


def test_a_timed_out_post_keeps_the_stored_values():
    # the outcome is unknown: the message may be up, with a button that must keep its value
    port, store = _valued_port(None)
    port._transport = _raising(TimeoutError("slow"))
    res = _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    assert (res.outcome, res.retryable) == (FAILED, False)
    assert [d["value"] for d in _stored(store)] == [VALUE]


def test_an_unreachable_post_keeps_the_stored_values():
    port, store = _valued_port(None)
    port._transport = _raising(ConnectionResetError("reset"))
    res = _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    assert (res.outcome, res.retryable) == (FAILED, True)
    assert [d["value"] for d in _stored(store)] == [VALUE]


def test_an_unexpected_post_error_keeps_the_stored_values():
    port, store = _valued_port(None)
    port._transport = _raising(RuntimeError("odd"))
    with pytest.raises(RuntimeError):
        _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    assert [d["value"] for d in _stored(store)] == [VALUE]


def test_a_store_failure_before_the_post_fails_without_posting():
    t = FakeTransport()
    port, store = _valued_port(t)

    def broken(*_a, **_k):
        raise RuntimeError("store down")

    store.insert = broken
    res = _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    assert res.outcome == FAILED
    assert res.retryable
    assert t.calls == []


def test_buttons_without_value_store_nothing_and_post_their_ids():
    t = FakeTransport()
    port, store = _valued_port(t)
    res = _send(port, buttons=[_btn()])
    assert res.output == {"message_id": "m9", "custom_ids": ["go:1"]}
    assert _stored(store) == []


def test_bad_values_are_refused_before_any_secret_or_post():
    bad = [
        [_btn(value="not an object")],
        [_btn(value={"n": float("nan")})],
        [_btn(value={"big": "x" * 5000})],
        [_btn(custom_id="p" * 90, value={})],
    ]
    for buttons in bad:
        t, seen = FakeTransport(), []
        port, store = _valued_port(t, seen)
        res = _send(port, buttons=buttons)
        assert res.outcome == FAILED
        assert res.error.startswith("bad_params")
        assert not res.retryable
        assert t.calls == []
        assert seen == []
        assert _stored(store) == []


def test_the_port_ensures_the_buttons_ttl_index_once():
    from culture_rules.apps.discord_buttons import BUTTONS_COLLECTION, BUTTONS_TTL_INDEX

    t = FakeTransport()
    port, store = _valued_port(t)
    calls = []
    store.ensure_index = lambda collection, keys, **kw: calls.append((collection, kw["name"]))
    _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    _send(port, buttons=[_btn(custom_id="merge", value=VALUE)])
    assert calls == [(BUTTONS_COLLECTION, BUTTONS_TTL_INDEX)]
