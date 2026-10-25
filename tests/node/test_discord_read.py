"""The discord.read action: one Discord message, untruncated, with attachments."""

from __future__ import annotations

from datetime import UTC, datetime

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.discord_read import DiscordReadAction
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LONG = "x" * 5000


def ctx(actor="disc"):
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor=actor)


class FakeTransport:
    def __init__(self, status=200, body=None):
        self.calls = []
        self._r = (status, body if body is not None else self.default(), {})

    @staticmethod
    def default():
        return {
            "id": "900",
            "content": LONG,
            "author": {"id": "7", "username": "ori"},
            "attachments": [
                {
                    "id": "5",
                    "filename": "a.png",
                    "content_type": "image/png",
                    "size": 3,
                    "url": "https://cdn/a.png",
                    "proxy_url": "nope",
                }
            ],
        }

    def __call__(self, url, data, headers, timeout):
        self.calls.append((url, data, headers))
        return self._r


def store_with(surface="discord", **connection):
    s = MemoryStore()
    s.put(
        "actors",
        {
            "id": "disc",
            "name": "Disc",
            "kind": "service",
            "surface": surface,
            "connection": {"bot_token": "grant:DISCORD_BOT", **connection},
        },
    )
    return s


def action(store, transport):
    return DiscordReadAction(store, resolve_secret=lambda ref: "tok-xyz", transport=transport)


def test_reads_message_fields_and_attachments_with_the_actors_token():
    t = FakeTransport()
    res = action(store_with(), t).invoke(
        {"channel": "42", "message": "900", "guild": "11"}, "k", NOW, context=ctx()
    )
    assert res.outcome == COMPLETED
    assert res.output["content"] == LONG
    assert res.output["author_id"] == "7"
    assert res.output["url"] == "https://discord.com/channels/11/42/900"
    assert res.output["attachments"] == [
        {
            "id": "5",
            "filename": "a.png",
            "content_type": "image/png",
            "size": 3,
            "url": "https://cdn/a.png",
        }
    ]
    url, data, headers = t.calls[0]
    assert url.endswith("/channels/42/messages/900")
    assert data is None
    assert headers["Authorization"] == "Bot tok-xyz"


def test_without_guild_url_is_none():
    res = action(store_with(), FakeTransport()).invoke(
        {"channel": "42", "message": "900"}, "k", NOW, context=ctx()
    )
    assert res.outcome == COMPLETED
    assert res.output["url"] is None


def test_non_discord_actor_fails_non_retryable():
    t = FakeTransport()
    res = action(store_with(surface="github"), t).invoke(
        {"channel": "42", "message": "900"}, "k", NOW, context=ctx()
    )
    assert res.outcome == FAILED
    assert res.retryable is False
    assert "not a discord" in res.error
    assert t.calls == []


def test_channel_outside_connection_channels_fails_non_retryable():
    t = FakeTransport()
    res = action(store_with(channels=["1", "2"]), t).invoke(
        {"channel": "42", "message": "900"}, "k", NOW, context=ctx()
    )
    assert res.outcome == FAILED
    assert res.retryable is False
    assert "allow-list" in res.error
    assert t.calls == []


def test_empty_channels_means_every_channel():
    res = action(store_with(channels=[]), FakeTransport()).invoke(
        {"channel": "42", "message": "900"}, "k", NOW, context=ctx()
    )
    assert res.outcome == COMPLETED


def test_missing_actor_unknown_actor_and_bad_input_fail_non_retryable():
    a = action(store_with(), FakeTransport())
    for c, inp in [
        (ctx(None), {"channel": "42", "message": "900"}),
        (ctx("nope"), {"channel": "42", "message": "900"}),
        (ctx(), {"channel": "42"}),
    ]:
        res = a.invoke(inp, "k", NOW, context=c)
        assert res.outcome == FAILED
        assert res.retryable is False


def test_discord_error_keeps_retryable_flag_and_no_token():
    for status, retryable in [(404, False), (503, True)]:
        res = action(store_with(), FakeTransport(status=status, body={})).invoke(
            {"channel": "42", "message": "900"}, "k", NOW, context=ctx()
        )
        assert res.outcome == FAILED
        assert res.retryable is retryable
        assert "tok-xyz" not in res.error


class RoutedTransport:
    """GET /channels/<id> answers the channel's guild; any other GET the message."""

    def __init__(self, guild):
        self.calls = []
        self.guild = guild

    def __call__(self, url, data, headers, timeout):
        self.calls.append(url)
        if url.rstrip("/").endswith("/channels/42"):
            return 200, {"id": "42", "guild_id": self.guild}, {}
        return 200, FakeTransport.default(), {}


def test_a_channel_outside_the_actors_guilds_is_refused():
    """PR #96 review: guild_id restricts reads like it restricts the gateway and targets."""
    t = RoutedTransport(guild="99")
    res = action(store_with(guild_id=["11", "12"]), t).invoke(
        {"channel": "42", "message": "900"}, "k", NOW, context=ctx()
    )
    assert res.outcome == FAILED
    assert res.retryable is False
    assert "guild" in res.error
    assert not any("/messages/" in u for u in t.calls)


def test_a_channel_in_the_actors_guild_is_read_and_its_url_filled():
    t = RoutedTransport(guild="12")
    res = action(store_with(guild_id=["11", "12"]), t).invoke(
        {"channel": "42", "message": "900"}, "k", NOW, context=ctx()
    )
    assert res.outcome == COMPLETED
    assert res.output["url"] == "https://discord.com/channels/12/42/900"


def test_no_guild_id_reads_without_a_channel_lookup():
    t = RoutedTransport(guild="99")
    res = action(store_with(), t).invoke(
        {"channel": "42", "message": "900"}, "k", NOW, context=ctx()
    )
    assert res.outcome == COMPLETED
    assert not any(u.rstrip("/").endswith("/channels/42") for u in t.calls)
