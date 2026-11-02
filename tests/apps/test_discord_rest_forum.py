"""Discord REST client: reading a channel and creating a forum post."""

from __future__ import annotations

import json
import socket
import urllib.error

import pytest

from culture_rules.apps.discord_rest import DiscordClient, DiscordError

TOKEN = "tok-SECRET-123"


class FakeTransport:
    def __init__(self, status=200, body=None, exc=None):
        self.calls = []
        self._resp = (status, body if body is not None else {}, {})
        self._exc = exc

    def __call__(self, url, data, headers, timeout, **kw):
        self.calls.append({"url": url, "data": data, "headers": headers, "kw": kw})
        if self._exc:
            raise self._exc
        return self._resp


def _post(t, **kw):
    return DiscordClient(TOKEN, transport=t).create_forum_post("55", "Title", "Body", ["7"], **kw)


def _fail(t):
    with pytest.raises(DiscordError) as ei:
        _post(t)
    return ei.value


def test_get_channel_parses_type_and_tags():
    raw = {
        "id": "55",
        "type": 15,
        "flags": 16,
        "name": "x",
        "available_tags": [{"id": "7", "name": "bug", "emoji_name": None}],
    }
    out = DiscordClient(TOKEN, transport=FakeTransport(body=raw)).get_channel("55")
    assert out == {
        "id": "55",
        "type": 15,
        "flags": 16,
        "available_tags": [{"id": "7", "name": "bug"}],
    }


def test_get_channel_rejects_bad_id():
    client = DiscordClient(TOKEN, transport=FakeTransport())
    with pytest.raises(DiscordError):
        client.get_channel("abc")


def test_create_forum_post_request_shape():
    t = FakeTransport(201, {"id": "900"})
    _post(t)
    assert t.calls[0]["url"] == "https://discord.com/api/v10/channels/55/threads"


def test_create_forum_post_body():
    t = FakeTransport(201, {"id": "900"})
    _post(t)
    body = json.loads(t.calls[0]["data"])
    assert body == {
        "name": "Title",
        "applied_tags": ["7"],
        "message": {"content": "Body", "allowed_mentions": {"parse": []}},
    }


def test_create_forum_post_returns_ids():
    assert _post(FakeTransport(201, {"id": "900"})) == {"thread_id": "900", "message_id": "900"}


def test_create_forum_post_uses_message_id_when_present():
    t = FakeTransport(201, {"id": "900", "message": {"id": "901"}})
    assert _post(t)["message_id"] == "901"


def test_create_forum_post_rejects_bad_channel():
    client = DiscordClient(TOKEN, transport=FakeTransport())
    with pytest.raises(DiscordError):
        client.create_forum_post("x", "n", "t", [])


def test_timeout_is_outcome_unknown():
    assert _fail(FakeTransport(exc=TimeoutError())).outcome_unknown


def test_timeout_is_not_retryable():
    assert not _fail(FakeTransport(exc=TimeoutError())).retryable


def test_a_refused_connection_is_retryable():
    exc = urllib.error.URLError(ConnectionRefusedError(111, "refused"))
    assert _fail(FakeTransport(exc=exc)).retryable


def test_a_bare_refused_connection_is_retryable():
    assert _fail(FakeTransport(exc=ConnectionRefusedError(111, "refused"))).retryable


def test_a_name_lookup_failure_is_retryable():
    exc = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    assert _fail(FakeTransport(exc=exc)).retryable


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionResetError(104, "reset"),
        urllib.error.URLError(ConnectionResetError(104, "reset")),
        urllib.error.URLError("down"),
        BrokenPipeError(32, "broken pipe"),
    ],
)
def test_another_connection_error_is_not_retryable(exc):
    assert not _fail(FakeTransport(exc=exc)).retryable


@pytest.mark.parametrize("exc", [ConnectionResetError(104, "reset"), urllib.error.URLError("down")])
def test_another_connection_error_is_an_unknown_outcome(exc):
    assert _fail(FakeTransport(exc=exc)).outcome_unknown


def test_429_is_retryable_with_retry_after():
    err = _fail(FakeTransport(429, {"retry_after": 2.5}))
    assert (err.retryable, err.retry_after) == (True, 2.5)


def test_503_is_not_retryable():
    assert not _fail(FakeTransport(503, {})).retryable


def test_503_is_an_unknown_outcome():
    assert _fail(FakeTransport(503, {})).outcome_unknown


def test_503_is_not_taken_as_refused():
    assert not _fail(FakeTransport(503, {})).refused


def test_429_is_not_an_unknown_outcome():
    assert not _fail(FakeTransport(429, {"retry_after": 1})).outcome_unknown


def test_400_is_refused_with_discord_message():
    err = _fail(FakeTransport(400, {"message": "Invalid Form Body"}))
    assert err.refused
    assert "Invalid Form Body" in str(err)


def test_not_auto_retried_on_429():
    t = FakeTransport(429, {"retry_after": 0})
    _fail(t)
    assert len(t.calls) == 1


@pytest.mark.parametrize("payload", [[{"id": "55"}], "a forum", 15])
def test_get_channel_with_a_non_object_reply_is_a_discord_error(payload):
    t = FakeTransport(body=payload)
    client = DiscordClient(TOKEN, transport=t)
    with pytest.raises(DiscordError, match="unexpected reply"):
        client.get_channel("55")


def test_get_channel_with_a_non_object_reply_is_not_retryable():
    t = FakeTransport(body=[{"id": "55"}])
    client = DiscordClient(TOKEN, transport=t)
    with pytest.raises(DiscordError) as ei:
        client.get_channel("55")
    assert not ei.value.retryable
