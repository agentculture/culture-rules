"""Discord REST client: payload shape, mention suppression, failure classification."""

from __future__ import annotations

import json
import urllib.error

import pytest

from culture_rules.apps.discord_rest import (
    MAX_CONTENT,
    DiscordClient,
    DiscordError,
)

TOKEN = "tok-SECRET-123"


class FakeTransport:
    def __init__(self, status=200, body=None, headers=None, exc=None):
        self.calls = []
        self._resp = (status, body if body is not None else {"id": "m1"}, headers or {})
        self._exc = exc

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        if self._exc:
            raise self._exc
        return self._resp


def test_posts_json_with_bot_auth_and_suppressed_mentions():
    t = FakeTransport()
    out = DiscordClient(TOKEN, transport=t).post_message("123", "hi @everyone @here")
    assert out == {"message_id": "m1"}
    call = t.calls[0]
    assert call["url"] == "https://discord.com/api/v10/channels/123/messages"
    assert call["headers"]["Authorization"] == f"Bot {TOKEN}"
    body = json.loads(call["data"])
    assert body["content"] == "hi @everyone @here"
    assert body["allowed_mentions"] == {"parse": []}


def test_base_url_configurable_and_content_truncated():
    t = FakeTransport()
    DiscordClient(TOKEN, base_url="http://localhost:9/api/", transport=t).post_message(
        "5", "x" * 3000
    )
    assert t.calls[0]["url"] == "http://localhost:9/api/channels/5/messages"
    assert len(json.loads(t.calls[0]["data"])["content"]) == MAX_CONTENT == 2000


def test_rate_limit_is_retryable_with_retry_after():
    t = FakeTransport(429, {"retry_after": 1.5}, {})
    client = DiscordClient(TOKEN, transport=t)
    with pytest.raises(DiscordError) as ei:
        client.post_message("1", "x")
    assert ei.value.retryable
    assert ei.value.retry_after == 1.5


def test_4xx_not_retryable_5xx_and_network_retryable():
    client = DiscordClient(TOKEN, transport=FakeTransport(403, {"message": "Missing Access"}))
    with pytest.raises(DiscordError) as ei:
        client.post_message("1", "x")
    assert not ei.value.retryable
    client = DiscordClient(TOKEN, transport=FakeTransport(502, {}))
    with pytest.raises(DiscordError) as ei:
        client.post_message("1", "x")
    assert ei.value.retryable
    client = DiscordClient(TOKEN, transport=FakeTransport(exc=urllib.error.URLError("down")))
    with pytest.raises(DiscordError) as ei:
        client.post_message("1", "x")
    assert ei.value.retryable


def test_errors_never_carry_token_or_payload():
    t = FakeTransport(400, {"message": "bad"})
    client = DiscordClient(TOKEN, transport=t)
    with pytest.raises(DiscordError) as ei:
        client.post_message("1", "private text")
    assert TOKEN not in str(ei.value)
    assert "private text" not in str(ei.value)
    assert TOKEN not in repr(DiscordClient(TOKEN, transport=t))


def test_channel_id_must_be_numeric():
    client = DiscordClient(TOKEN, transport=FakeTransport())
    with pytest.raises(DiscordError) as ei:
        client.post_message("../x", "x")
    assert not ei.value.retryable
