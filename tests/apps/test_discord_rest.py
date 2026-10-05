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


@pytest.mark.parametrize(
    "exc", [TimeoutError("read timed out"), urllib.error.URLError(TimeoutError("timed out"))]
)
def test_a_timeout_is_an_unknown_outcome_and_not_retryable(exc):
    """The post may have landed; a retry would duplicate the message."""
    client = DiscordClient(TOKEN, transport=FakeTransport(exc=exc))
    with pytest.raises(DiscordError) as ei:
        client.post_message("1", "x")
    assert not ei.value.retryable
    assert "outcome unknown" in str(ei.value)


class Routes:
    """A GET/POST fake keyed by URL path: ``{path: (status, body)}``; records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        path = url.split("/api/v10", 1)[-1]
        self.calls.append(("POST" if data is not None else "GET", path, headers))
        status, body = self.routes.get(path, (404, {"message": "Unknown"}))
        return status, body, {}


def guild_routes():
    return Routes(
        {
            "/users/@me/guilds": (
                200,
                [{"id": "10", "name": "Lab"}, {"id": "20", "name": "Other"}],
            ),
            "/guilds/10/channels": (
                200,
                [
                    {"id": "101", "name": "general", "type": 0, "position": 1},
                    {"id": "102", "name": "voice", "type": 2, "position": 2},
                    {"id": "103", "name": "culture", "type": 0, "position": 0},
                    {"id": "104", "name": "news", "type": 5, "position": 3},
                ],
            ),
            "/guilds/20/channels": (200, [{"id": "201", "name": "x", "type": 0, "position": 0}]),
            "/channels/101": (200, {"id": "101"}),
            "/channels/103": (403, {"message": "Missing Access", "code": 50001}),
            "/channels/104": (200, {"id": "104"}),
            "/channels/201": (200, {"id": "201"}),
        }
    )


def test_list_targets_gives_text_channels_per_guild_with_visibility():
    t = guild_routes()
    out = DiscordClient(TOKEN, transport=t).list_targets()
    assert [g["id"] for g in out] == ["10", "20"]
    lab = out[0]
    assert lab["name"] == "Lab"
    assert lab["channels"] == [
        {"id": "103", "name": "culture", "visible": False},
        {"id": "101", "name": "general", "visible": True},
        {"id": "104", "name": "news", "visible": True},
    ]
    assert all(method == "GET" for method, _, _ in t.calls)
    assert all(h["Authorization"] == f"Bot {TOKEN}" for _, _, h in t.calls)


def test_list_targets_can_be_limited_to_one_guild():
    t = guild_routes()
    out = DiscordClient(TOKEN, transport=t).list_targets(guild_id="20")
    assert [g["id"] for g in out] == ["20"]
    assert not any(path == "/guilds/10/channels" for _, path, _ in t.calls)


def test_list_targets_failure_never_carries_the_token():
    t = Routes({"/users/@me/guilds": (401, {"message": "401: Unauthorized"})})
    with pytest.raises(DiscordError) as ei:
        DiscordClient(TOKEN, transport=t).list_targets()
    assert TOKEN not in str(ei.value)
    assert not ei.value.retryable
