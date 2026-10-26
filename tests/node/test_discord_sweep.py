"""discord.sweep and cursor.advance: a scheduled history sweep with a stored cursor (t18)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.cursor import (
    CURSOR_ADVANCE_BUILTIN,
    SWEEP_CURSORS_COLLECTION,
    CursorAdvancePort,
)
from culture_rules.node.actions.discord_sweep import DiscordSweepAction
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def ctx(actor="disc", config=None):
    return InvocationContext(
        run_id="r", step_id="s", kind="action", host="h", actor=actor, config=config or {}
    )


class ChannelTransport:
    """A fake Discord: one channel's messages; honours ``after`` and ``limit`` (newest first)."""

    def __init__(self, ids=(), guild="77", bots=()):
        self.ids = [str(i) for i in ids]
        self.bots = {str(i) for i in bots}
        self.guild = guild
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append(url)
        parsed = urlparse(url)
        if parsed.path.endswith("/messages"):
            q = parse_qs(parsed.query)
            limit = int(q["limit"][0])
            if "after" in q:
                after = int(q["after"][0])
                newer = sorted((i for i in self.ids if int(i) > after), key=int)[:limit]
            else:  # no after: the newest ``limit`` messages
                newer = sorted(self.ids, key=int)[-limit:] if self.ids else []
            page = [
                {
                    "id": i,
                    "content": f"m{i}",
                    "author": {"id": "7", "username": "ori", "bot": i in self.bots},
                    "attachments": [],
                }
                for i in reversed(newer)
            ]
            return 200, page, {}
        return 200, {"id": "555", "guild_id": self.guild}, {}  # GET /channels/{id}


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


def sweep(store, transport, **inp):
    action = DiscordSweepAction(store, resolve_secret=lambda ref: "tok", transport=transport)
    body = {"channel": "555", "cursor_key": "lib", **inp}
    return action.invoke(body, "k", NOW, context=ctx())


def advance(store, **inp):
    return CursorAdvancePort(store).invoke(inp, "k", NOW, context=ctx(actor=None))


# ------------------------------------------------------------------ discord.sweep


def test_a_backfill_first_sweep_reads_from_the_start_oldest_first_with_cursor_outputs():
    s, t = store_with(), ChannelTransport([101, 103, 102])
    res = sweep(s, t, guild="77", backfill=True)
    assert res.outcome == COMPLETED, res.error
    out = res.output
    assert [m["id"] for m in out["messages"]] == ["101", "102", "103"]
    first = out["messages"][0]
    assert first["channel_id"] == "555" and first["guild_id"] == "77"
    assert first["content"] == "m101" and first["author_id"] == "7"
    assert first["author_name"] == "ori" and first["attachments"] == []
    assert first["url"] == "https://discord.com/channels/77/555/101"
    assert out["cursor"] is None and out["next_cursor"] == "103"
    assert out["cursor_key"] == "lib:555" and out["count"] == 3 and out["more"] is False
    assert "after=0" in t.calls[-1]
    assert s.get(SWEEP_CURSORS_COLLECTION, "lib:555") is None  # a sweep never writes


def test_sweep_reads_after_the_stored_cursor():
    s, t = store_with(), ChannelTransport([101, 102, 103])
    s.put(SWEEP_CURSORS_COLLECTION, {"id": "lib:555", "value": "101"})
    out = sweep(s, t).output
    assert [m["id"] for m in out["messages"]] == ["102", "103"]
    assert out["cursor"] == "101" and out["next_cursor"] == "103"
    assert "after=101" in t.calls[-1]


def test_sweep_is_bounded_per_run_and_says_more():
    s, t = store_with(), ChannelTransport(range(1000, 1300))
    out = sweep(s, t, backfill=True).output  # default limit 50
    assert out["count"] == 50 and out["more"] is True
    assert out["next_cursor"] == "1049"
    out = sweep(s, t, backfill=True, limit=500).output  # hard cap 100
    assert out["count"] == 100
    out = sweep(s, t, backfill=True, limit=3).output
    assert [m["id"] for m in out["messages"]] == ["1000", "1001", "1002"]


def test_sweep_limit_and_key_from_config():
    s, t = store_with(), ChannelTransport([5, 6, 7])
    action = DiscordSweepAction(s, resolve_secret=lambda ref: "tok", transport=t)
    res = action.invoke(
        {"channel": "555"},
        "k",
        NOW,
        context=ctx(config={"cursor_key": "flow", "limit": 2, "backfill": True}),
    )
    assert res.outcome == COMPLETED, res.error
    assert res.output["cursor_key"] == "flow:555" and res.output["count"] == 2


def test_sweep_with_no_new_messages_keeps_the_old_cursor():
    s, t = store_with(), ChannelTransport([101])
    s.put(SWEEP_CURSORS_COLLECTION, {"id": "lib:555", "value": "101"})
    out = sweep(s, t).output
    assert out["messages"] == [] and out["count"] == 0
    assert out["cursor"] == "101" and out["next_cursor"] == "101"


def test_sweep_refusals():
    s, t = store_with(channels=["999"]), ChannelTransport([1])
    res = sweep(s, t)
    assert res.outcome == FAILED and not res.retryable and "allow-list" in res.error
    s = store_with()
    for bad in ({"cursor_key": ""}, {"cursor_key": "a b"}, {"channel": ""}, {"limit": 0}):
        res = sweep(s, t, **bad)
        assert res.outcome == FAILED and not res.retryable, bad
    s.put(SWEEP_CURSORS_COLLECTION, {"id": "lib:555", "value": "not-an-id"})
    res = sweep(s, t)
    assert res.outcome == FAILED and "bad_cursor" in res.error
    no_actor = DiscordSweepAction(s, resolve_secret=lambda ref: "tok", transport=t)
    res = no_actor.invoke({"channel": "555", "cursor_key": "lib"}, "k", NOW, context=ctx(None))
    assert res.outcome == FAILED and "params.actor" in res.error


def test_sweep_honours_the_guild_restriction():
    s, t = store_with(guild_id="1"), ChannelTransport([5], guild="77")
    res = sweep(s, t, backfill=True)
    assert res.outcome == FAILED and "guilds" in res.error
    s, t = store_with(guild_id=["77"]), ChannelTransport([5], guild="77")
    out = sweep(s, t, backfill=True).output
    assert out["messages"][0]["guild_id"] == "77"
    assert out["messages"][0]["url"] == "https://discord.com/channels/77/555/5"


def test_sweep_discord_error_follows_retryable():
    s = store_with()

    def boom(url, data, headers, timeout):
        return 503, {}, {}

    res = sweep(s, boom)
    assert res.outcome == FAILED and res.retryable


# ------------------------------------------------------------------ cursor.advance


def test_builtin_name():
    assert CURSOR_ADVANCE_BUILTIN == "cursor.advance"


def test_advance_creates_then_moves_forward():
    s = MemoryStore()
    res = advance(s, key="lib:555", expected=None, value="103")
    assert res.outcome == COMPLETED and res.output == {
        "key": "lib:555",
        "value": "103",
        "previous": None,
        "advanced": True,
    }
    assert s.get(SWEEP_CURSORS_COLLECTION, "lib:555")["value"] == "103"
    res = advance(s, key="lib:555", expected="103", value="110")
    assert res.outcome == COMPLETED and res.output["advanced"] is True
    assert s.get(SWEEP_CURSORS_COLLECTION, "lib:555")["value"] == "110"


def test_advance_to_the_same_value_saves_nothing():
    s = MemoryStore()
    res = advance(s, key="k:1", expected=None, value=None)
    assert res.outcome == COMPLETED and res.output["advanced"] is False
    assert s.get(SWEEP_CURSORS_COLLECTION, "k:1") is None
    s.put(SWEEP_CURSORS_COLLECTION, {"id": "k:1", "value": "50"})
    before = s.get(SWEEP_CURSORS_COLLECTION, "k:1")["updated_at"]
    res = advance(s, key="k:1", expected="50", value="50")
    assert res.outcome == COMPLETED and res.output["advanced"] is False
    assert s.get(SWEEP_CURSORS_COLLECTION, "k:1")["updated_at"] == before


def test_advance_retry_after_it_already_landed_is_ok():
    s = MemoryStore()
    assert advance(s, key="k:1", expected=None, value="60").output["advanced"] is True
    res = advance(s, key="k:1", expected=None, value="60")  # the same step, retried
    assert res.outcome == COMPLETED and res.output["advanced"] is False


def test_advance_refuses_a_moved_cursor():
    s = MemoryStore()
    s.put(SWEEP_CURSORS_COLLECTION, {"id": "k:1", "value": "80"})
    res = advance(s, key="k:1", expected="50", value="70")  # another run moved it to 80
    assert res.outcome == FAILED and not res.retryable and "cursor_moved" in res.error
    assert s.get(SWEEP_CURSORS_COLLECTION, "k:1")["value"] == "80"
    res = advance(s, key="k:1", expected="50", value="90")  # would skip 51..80's successor
    assert res.outcome == FAILED and "cursor_moved" in res.error
    assert s.get(SWEEP_CURSORS_COLLECTION, "k:1")["value"] == "80"


def test_advance_refuses_moving_backwards():
    s = MemoryStore()
    s.put(SWEEP_CURSORS_COLLECTION, {"id": "k:1", "value": "100"})
    res = advance(s, key="k:1", expected="100", value="99")
    assert res.outcome == FAILED and not res.retryable and "cursor_backwards" in res.error
    res = advance(s, key="k:1", expected="100", value=None)
    assert res.outcome == FAILED and "cursor_backwards" in res.error
    assert s.get(SWEEP_CURSORS_COLLECTION, "k:1")["value"] == "100"


def test_advance_bad_params():
    s = MemoryStore()
    for bad in (
        {"expected": None, "value": "1"},
        {"key": "", "value": "1"},
        {"key": "k", "value": 5},
    ):
        res = advance(s, **bad)
        assert res.outcome == FAILED and not res.retryable and "bad_params" in res.error, bad


def test_concurrent_advances_from_the_same_cursor_one_wins():
    s = MemoryStore()
    s.put(SWEEP_CURSORS_COLLECTION, {"id": "k:1", "value": "10"})
    results = []
    barrier = threading.Barrier(8)

    def go(v):
        barrier.wait()
        results.append(advance(s, key="k:1", expected="10", value=str(v)))

    threads = [threading.Thread(target=go, args=(20 + i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    won = [r for r in results if r.outcome == COMPLETED and r.output["advanced"]]
    assert len(won) == 1
    assert s.get(SWEEP_CURSORS_COLLECTION, "k:1")["value"] == won[0].output["value"]


# ------------------------------------------------------------------ the two-step flow


def _pushed_ok(store, transport):
    """One scheduled run whose pushes succeed: sweep, (push), advance."""
    out = sweep(store, transport).output
    res = advance(store, key=out["cursor_key"], expected=out["cursor"], value=out["next_cursor"])
    assert res.outcome == COMPLETED, res.error
    return out


def test_a_run_after_a_failed_run_saves_what_it_missed():
    s, t = store_with(), ChannelTransport([101, 102])
    s.put(SWEEP_CURSORS_COLLECTION, {"id": "lib:555", "value": "100"})
    failed = sweep(s, t).output  # the pushes fail: the run never reaches cursor.advance
    assert [m["id"] for m in failed["messages"]] == ["101", "102"]
    t.ids.append("103")
    again = _pushed_ok(s, t)  # the next run sees the missed messages and the new one
    assert [m["id"] for m in again["messages"]] == ["101", "102", "103"]
    assert s.get(SWEEP_CURSORS_COLLECTION, "lib:555")["value"] == "103"
    assert sweep(s, t).output["messages"] == []


def test_two_runs_with_no_new_messages_save_nothing():
    s, t = store_with(), ChannelTransport([101])
    _pushed_ok(s, t)
    for _ in range(2):
        out = _pushed_ok(s, t)
        assert out["messages"] == [] and out["next_cursor"] == "101"
    assert s.get(SWEEP_CURSORS_COLLECTION, "lib:555")["value"] == "101"


def test_an_overlapping_run_cannot_rewind_or_skip():
    s, t = store_with(), ChannelTransport([101, 102])
    a = sweep(s, t).output  # run A reads 101..102
    t.ids.append("103")
    b = sweep(s, t).output  # run B overlaps: 101..103 from the same cursor
    assert advance(s, key=b["cursor_key"], expected=b["cursor"], value=b["next_cursor"]).output[
        "advanced"
    ]
    late = advance(s, key=a["cursor_key"], expected=a["cursor"], value=a["next_cursor"])
    assert late.outcome == FAILED and "cursor_moved" in late.error  # A cannot rewind to 102
    assert s.get(SWEEP_CURSORS_COLLECTION, "lib:555")["value"] == "103"


def test_cursors_are_per_flow_and_channel():
    s, t = store_with(), ChannelTransport([1, 2])
    _pushed_ok(s, t)
    other = sweep(s, t, backfill=True, cursor_key="other").output
    assert other["cursor"] is None and other["count"] == 2


# ------------------------------------------------------------------ first run, bots


def test_a_first_sweep_starts_now_and_saves_no_history():
    s, t = store_with(), ChannelTransport([101, 102, 103])
    out = sweep(s, t).output
    assert out["messages"] == [] and out["count"] == 0 and out["more"] is False
    assert out["cursor"] is None and out["next_cursor"] == "103"
    assert "after=" not in t.calls[-1] and "limit=1" in t.calls[-1]
    # once advanced, the next run reads only what came after
    assert advance(s, key="lib:555", expected=None, value="103").outcome == COMPLETED
    t.ids.append("104")
    assert [m["id"] for m in sweep(s, t).output["messages"]] == ["104"]


def test_a_first_sweep_of_an_empty_channel_starts_at_zero_so_nothing_is_missed():
    s, t = store_with(), ChannelTransport([])
    out = sweep(s, t).output
    assert out["messages"] == [] and out["next_cursor"] == "0"


def test_bot_messages_are_skipped_unless_include_bots_but_the_cursor_passes_them():
    s, t = store_with(), ChannelTransport([101, 102, 103], bots=[102, 103])
    s.put(SWEEP_CURSORS_COLLECTION, {"id": "lib:555", "value": "100"})
    out = sweep(s, t).output
    assert [m["id"] for m in out["messages"]] == ["101"]
    assert out["count"] == 1 and out["next_cursor"] == "103"
    both = sweep(s, t, include_bots=True).output
    assert [m["id"] for m in both["messages"]] == ["101", "102", "103"]
    assert both["messages"][1]["bot"] is True and both["messages"][0]["bot"] is False


def test_backfill_and_include_bots_must_be_booleans():
    s, t = store_with(), ChannelTransport([101])
    assert sweep(s, t, backfill="yes").outcome == FAILED
    assert sweep(s, t, include_bots=1).outcome == FAILED
