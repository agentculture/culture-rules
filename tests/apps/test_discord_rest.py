"""Discord REST client: payload shape, mention suppression, failure classification."""

from __future__ import annotations

import json
import urllib.error

import pytest

from culture_rules.apps.discord_rest import (
    MAX_CONTENT,
    DiscordClient,
    DiscordError,
    guild_ids,
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


def test_list_targets_accepts_a_list_of_guild_ids():
    t = guild_routes()
    out = DiscordClient(TOKEN, transport=t).list_targets(guild_id=["20", "10"])
    assert [g["id"] for g in out] == ["10", "20"]
    only = DiscordClient(TOKEN, transport=guild_routes()).list_targets(guild_id=frozenset({"20"}))
    assert [g["id"] for g in only] == ["20"]


@pytest.mark.parametrize("unset", [None, "", [], ()])
def test_list_targets_unset_or_empty_means_every_guild(unset):
    out = DiscordClient(TOKEN, transport=guild_routes()).list_targets(guild_id=unset)
    assert [g["id"] for g in out] == ["10", "20"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ([], None),
        ("20", frozenset({"20"})),
        (20, frozenset({"20"})),
        (["10", 20, ""], frozenset({"10", "20"})),
        (("10",), frozenset({"10"})),
    ],
)
def test_guild_ids_normalises_a_string_or_a_list(value, expected):
    assert guild_ids(value) == expected


def test_list_targets_failure_never_carries_the_token():
    client = DiscordClient(TOKEN, transport=Routes({"/users/@me/guilds": (401, {"message": "no"})}))
    with pytest.raises(DiscordError) as ei:
        client.list_targets()
    assert TOKEN not in str(ei.value)
    assert not ei.value.retryable


class SeqTransport:
    """Replies from a list, one per call; the last repeats."""

    def __init__(self, *responses):
        self.calls = []
        self._responses = list(responses)

    def __call__(self, url, data, headers, timeout):
        self.calls.append(url)
        i = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[i]


def _msg(mid, content="hi", **extra):
    return {"id": str(mid), "content": content, "author": {"id": "7", "username": "ann"}, **extra}


def test_get_message_returns_full_content_author_url_attachments():
    big = "x" * 3000
    att = {"id": "9", "filename": "a.mp3", "content_type": "audio/mpeg", "size": 5, "url": "u"}
    t = SeqTransport((200, _msg(55, big, attachments=[{**att, "extra": 1}]), {}))
    out = DiscordClient(TOKEN, transport=t).get_message("123", "55", guild_id="42")
    assert t.calls == ["https://discord.com/api/v10/channels/123/messages/55"]
    assert out["content"] == big
    assert out["author_id"] == "7"
    assert out["author_name"] == "ann"
    assert out["url"] == "https://discord.com/channels/42/123/55"
    assert out["attachments"] == [att]
    assert out["id"] == "55"
    assert out["channel_id"] == "123"


def test_get_message_url_none_without_guild_and_ids_validated():
    t = SeqTransport((200, _msg(55), {}))
    client = DiscordClient(TOKEN, transport=t)
    assert client.get_message("123", "55")["url"] is None
    assert client.get_message("123", "55", guild_id="@me")["url"] == (
        "https://discord.com/channels/@me/123/55"
    )
    for bad in (("1/../x", "5"), ("1", "5?x=1"), ("", "5")):
        with pytest.raises(DiscordError) as ei:
            client.get_message(*bad)
        assert not ei.value.retryable
    assert len(t.calls) == 2


def test_history_pages_oldest_first_and_bounded():
    page1 = [_msg(i) for i in range(300, 200, -1)]  # newest first, 100
    page2 = [_msg(i) for i in range(350, 300, -1)]  # 50, newer than page1
    t = SeqTransport((200, page1, {}), (200, page2, {}))
    out = DiscordClient(TOKEN, transport=t).history("123", "100", limit=1000)
    assert [m["id"] for m in out] == [str(i) for i in range(201, 351)]
    assert t.calls[0].endswith("/channels/123/messages?after=100&limit=100")
    assert "after=300&" in t.calls[1]
    assert len(t.calls) == 2


def test_history_respects_limit_and_page_cap():
    full = [_msg(i) for i in range(1000, 900, -1)]
    t = SeqTransport((200, full, {}))
    client = DiscordClient(TOKEN, transport=t)
    assert len(client.history("1", "0", limit=30)) == 30
    assert "limit=30" in t.calls[0]
    t.calls.clear()
    t2 = SeqTransport(
        *[(200, [_msg(10 * n + k) for k in range(100, 0, -1)], {}) for n in range(99)]
    )
    client = DiscordClient(TOKEN, transport=t2)
    out = client.history("1", "0", limit=100000, max_pages=3)
    assert len(t2.calls) == 3
    assert len(out) == 300


def test_history_rejects_bad_after():
    client = DiscordClient(TOKEN, transport=SeqTransport((200, [], {})))
    with pytest.raises(DiscordError):
        client.history("1", "x y", limit=5)


def test_get_retries_429_after_retry_after_then_succeeds():
    slept = []
    t = SeqTransport((429, {"retry_after": 0.5}, {}), (429, {"retry_after": 1}, {}), (200, [], {}))
    client = DiscordClient(TOKEN, transport=t, sleep=slept.append)
    assert client.list_targets() == []
    assert slept == [0.5, 1.0]
    assert len(t.calls) == 3


def test_get_429_exhausted_raises_retryable_and_caps_wait():
    slept = []
    t = SeqTransport((429, {"retry_after": 9999}, {}))
    client = DiscordClient(TOKEN, transport=t, sleep=slept.append)
    with pytest.raises(DiscordError) as ei:
        client.get_message("1", "2")
    assert ei.value.retryable
    assert TOKEN not in str(ei.value)
    assert TOKEN not in repr(client)
    assert slept
    assert max(slept) <= 5.0
    assert len(t.calls) == len(slept) + 1


def test_429_without_retry_after_uses_default_wait():
    slept = []
    t = SeqTransport((429, {}, {}), (200, [], {}))
    DiscordClient(TOKEN, transport=t, sleep=slept.append).list_targets()
    assert len(slept) == 1
    assert 0 < slept[0] <= 5.0


def test_channel_guild_returns_the_guild_of_a_channel():
    calls = []

    def transport(url, data, headers, timeout):
        calls.append(url)
        return 200, {"id": "42", "guild_id": "12"}, {}

    client = DiscordClient("t", transport=transport)
    assert client.channel_guild("42") == "12"
    assert calls[-1].endswith("/channels/42")
    with pytest.raises(DiscordError):
        client.channel_guild("general")


# --------------------------------------------------------------------------- add_reaction


class PutTransport:
    """Records the method too (a PUT passes ``method=``); replies from a script of statuses."""

    def __init__(self, *responses, exc=None):
        self.calls = []
        self._responses = list(responses) or [(204, {}, {})]
        self._exc = exc

    def __call__(self, url, data, headers, timeout, method=None):
        self.calls.append({"url": url, "data": data, "method": method, "headers": headers})
        if self._exc:
            raise self._exc
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def test_add_reaction_puts_a_percent_encoded_unicode_emoji_on_me():
    t = PutTransport()
    out = DiscordClient(TOKEN, transport=t).add_reaction("123", "55", "\N{THUMBS UP SIGN}")
    assert out == {"channel_id": "123", "message_id": "55", "emoji": "\N{THUMBS UP SIGN}"}
    (call,) = t.calls
    assert call["method"] == "PUT"
    assert call["url"].endswith("/channels/123/messages/55/reactions/%F0%9F%91%8D/@me")
    assert call["headers"]["Authorization"] == f"Bot {TOKEN}"


def test_add_reaction_takes_a_custom_emoji_as_name_and_id():
    t = PutTransport()
    DiscordClient(TOKEN, transport=t).add_reaction("123", "55", "party_blob:987654321")
    assert t.calls[0]["url"].endswith("/reactions/party_blob%3A987654321/@me")


@pytest.mark.parametrize("emoji", ["", "   ", "a/b", "name:notanid", 3, None])
def test_add_reaction_refuses_a_malformed_emoji(emoji):
    t = PutTransport()
    client = DiscordClient(TOKEN, transport=t)
    with pytest.raises(DiscordError) as ei:
        client.add_reaction("123", "55", emoji)
    assert not ei.value.retryable
    assert t.calls == []


def test_add_reaction_validates_ids():
    t = PutTransport()
    client = DiscordClient(TOKEN, transport=t)
    for channel, message in [("abc", "55"), ("123", "x/y")]:
        with pytest.raises(DiscordError):
            client.add_reaction(channel, message, "\N{THUMBS UP SIGN}")
    assert t.calls == []


def test_add_reaction_retries_a_429_after_retry_after_then_succeeds():
    t = PutTransport((429, {"retry_after": 0.25}, {}), (204, {}, {}))
    waits = []
    client = DiscordClient(TOKEN, transport=t, sleep=waits.append)
    client.add_reaction("123", "55", "\N{THUMBS UP SIGN}")
    assert waits == [0.25]
    assert len(t.calls) == 2


def test_add_reaction_gives_up_on_a_persistent_429_as_retryable():
    t = PutTransport((429, {"retry_after": 2}, {}))
    client = DiscordClient(TOKEN, transport=t, sleep=lambda s: None)
    with pytest.raises(DiscordError) as ei:
        client.add_reaction("123", "55", "\N{THUMBS UP SIGN}")
    assert ei.value.retryable
    assert ei.value.retry_after == 2.0
    assert len(t.calls) == 4  # one try and three retries


def test_add_reaction_errors_keep_the_retryable_flag_and_no_token():
    for status, retryable in [(403, False), (404, False), (502, True)]:
        client = DiscordClient(TOKEN, transport=PutTransport((status, {"message": "x"}, {})))
        with pytest.raises(DiscordError) as ei:
            client.add_reaction("123", "55", "\N{THUMBS UP SIGN}")
        assert ei.value.retryable is retryable
        assert TOKEN not in str(ei.value)


def test_add_reaction_network_failure_is_retryable():
    client = DiscordClient(TOKEN, transport=PutTransport(exc=urllib.error.URLError("down")))
    with pytest.raises(DiscordError) as ei:
        client.add_reaction("123", "55", "\N{THUMBS UP SIGN}")
    assert ei.value.retryable


# ------------------------------------------------------------ edit_interaction_response


def test_edit_interaction_response_patches_original_without_a_bot_token():
    t = PutTransport((200, {"id": "1"}, {}))
    client = DiscordClient(TOKEN, transport=t)
    out = client.edit_interaction_response("9001", "itok", "Saved")
    assert out == {"edited": True}
    (call,) = t.calls
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/webhooks/9001/itok/messages/@original")
    assert json.loads(call["data"]) == {"content": "Saved", "allowed_mentions": {"parse": []}}
    assert "Authorization" not in call["headers"]


def test_edit_interaction_response_cuts_long_text():
    t = PutTransport((200, {}, {}))
    DiscordClient(TOKEN, transport=t).edit_interaction_response("9001", "itok", "x" * 3000)
    content = json.loads(t.calls[0]["data"])["content"]
    assert len(content) == 2000
    assert content.endswith("…")


@pytest.mark.parametrize(
    ("status", "retryable", "expired"),
    [
        (401, False, True),
        (404, False, True),
        (429, True, False),
        (503, True, False),
        (400, False, False),
    ],
)
def test_edit_interaction_response_errors(status, retryable, expired):
    t = PutTransport((status, {"retry_after": 0}, {}))
    client = DiscordClient(TOKEN, transport=t, sleep=lambda s: None)
    with pytest.raises(DiscordError) as err:
        client.edit_interaction_response("9001", "itok", "hi")
    assert err.value.retryable is retryable
    assert getattr(err.value, "expired", False) is expired
    assert "itok" not in str(err.value)


def test_edit_interaction_response_refuses_bad_ids():
    t = PutTransport()
    client = DiscordClient(TOKEN, transport=t)
    with pytest.raises(DiscordError):
        client.edit_interaction_response("90/01", "itok", "hi")
    with pytest.raises(DiscordError):
        client.edit_interaction_response("9", "bad/tok", "hi")
    assert t.calls == []


# ------------------------------------------------------------ create_followup


def test_create_followup_posts_a_private_message_without_a_bot_token():
    t = PutTransport((200, {"id": "1"}, {}))
    out = DiscordClient(TOKEN, transport=t).create_followup("9001", "itok", "Merged")
    assert out == {"sent": True}
    (call,) = t.calls
    assert call["method"] == "POST"
    assert call["url"].endswith("/webhooks/9001/itok")
    assert json.loads(call["data"]) == {
        "content": "Merged",
        "flags": 64,
        "allowed_mentions": {"parse": []},
    }
    assert "Authorization" not in call["headers"]


def test_create_followup_cuts_long_text():
    t = PutTransport((200, {}, {}))
    DiscordClient(TOKEN, transport=t).create_followup("9001", "itok", "x" * 3000)
    content = json.loads(t.calls[0]["data"])["content"]
    assert len(content) == 2000
    assert content.endswith("…")


@pytest.mark.parametrize(
    ("status", "retryable", "expired"),
    [
        (401, False, True),
        (404, False, True),
        (429, True, False),
        (503, True, False),
        (400, False, False),
    ],
)
def test_create_followup_errors(status, retryable, expired):
    t = PutTransport((status, {"retry_after": 0}, {}))
    client = DiscordClient(TOKEN, transport=t, sleep=lambda s: None)
    with pytest.raises(DiscordError) as err:
        client.create_followup("9001", "itok", "hi")
    assert err.value.retryable is retryable
    assert getattr(err.value, "expired", False) is expired
    assert "itok" not in str(err.value)


def test_create_followup_refuses_bad_ids():
    t = PutTransport()
    client = DiscordClient(TOKEN, transport=t)
    with pytest.raises(DiscordError):
        client.create_followup("90/01", "itok", "hi")
    with pytest.raises(DiscordError):
        client.create_followup("9", "bad/tok", "hi")
    assert t.calls == []


# --------------------------------------------------------------------------- remove_reaction


def test_remove_reaction_deletes_the_bots_own_reaction():
    t = PutTransport()
    out = DiscordClient(TOKEN, transport=t).remove_reaction("123", "55", "\N{HOURGLASS}")
    assert out == {"channel_id": "123", "message_id": "55", "emoji": "\N{HOURGLASS}"}
    (call,) = t.calls
    assert call["method"] == "DELETE"
    assert call["url"].endswith("/channels/123/messages/55/reactions/%E2%8C%9B/@me")


@pytest.mark.parametrize("emoji", ["", "a/b", "name:notanid", None])
def test_remove_reaction_refuses_a_malformed_emoji(emoji):
    t = PutTransport()
    client = DiscordClient(TOKEN, transport=t)
    with pytest.raises(DiscordError):
        client.remove_reaction("123", "55", emoji)
    assert t.calls == []


def test_remove_reaction_a_429_is_retryable_and_a_404_is_not():
    rate = DiscordClient(TOKEN, transport=PutTransport((429, {"retry_after": 1}, {})))
    with pytest.raises(DiscordError) as limited:
        rate.remove_reaction("123", "55", "\N{HOURGLASS}")
    assert limited.value.retryable
    gone = DiscordClient(TOKEN, transport=PutTransport((404, {}, {})))
    with pytest.raises(DiscordError) as missing:
        gone.remove_reaction("123", "55", "\N{HOURGLASS}")
    assert not missing.value.retryable
