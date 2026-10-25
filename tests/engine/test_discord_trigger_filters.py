"""Discord trigger filters in trigger_matches: bots, channels, users, roles."""

from __future__ import annotations

from culture_rules.engine.matching import trigger_matches
from culture_rules.model.rule import Trigger

TYPE = "discord.message.created"


def trig(**params) -> Trigger:
    return Trigger(kind="event", params={"type": TYPE, **params})


def ev(**data) -> dict:
    base = {
        "message_id": "1",
        "guild_id": "10",
        "channel_id": "20",
        "author_id": "30",
        "author_name": "Alice",
        "bot": False,
        "content": "hi",
    }
    base.update(data)
    return {"kind": "event", "type": TYPE, "data": base}


def test_human_matches_as_before():
    assert trigger_matches(trig(), ev())


def test_bot_blocked_unless_include_bots():
    assert not trigger_matches(trig(), ev(bot=True))
    assert not trigger_matches(trig(include_bots=False), ev(bot=True))
    assert trigger_matches(trig(include_bots=True), ev(bot=True))


def test_channels_filter():
    assert trigger_matches(trig(channels=["20", "21"]), ev())
    assert not trigger_matches(trig(channels=["21"]), ev())
    assert trigger_matches(trig(channels=[20]), ev())
    assert trigger_matches(trig(channels="all"), ev())
    assert trigger_matches(trig(channels=[]), ev())


def test_users_filter_ids_or_names_case_insensitive():
    assert trigger_matches(trig(users=["30"]), ev())
    assert trigger_matches(trig(users=["alice"]), ev())
    assert trigger_matches(trig(users=["ALICE"]), ev(author_name="alice"))
    assert not trigger_matches(trig(users=["bob", "31"]), ev())


def test_roles_filter():
    assert trigger_matches(trig(roles=["r1"]), ev(user_roles=["r1", "r2"]))
    assert not trigger_matches(trig(roles=["r3"]), ev(user_roles=["r1"]))
    assert not trigger_matches(trig(roles=["r1"]), ev())


def test_users_and_roles_both_must_admit():
    t = trig(users=["alice"], roles=["r1"])
    assert trigger_matches(t, ev(user_roles=["r1"]))
    assert not trigger_matches(t, ev(user_roles=["r2"]))
    assert not trigger_matches(t, ev(author_name="bob", user_roles=["r1"]))


def test_non_discord_events_unaffected():
    t = Trigger(
        kind="event", params={"type": "github.pr.merged", "channels": ["x"], "users": ["z"]}
    )
    e = {"kind": "event", "type": "github.pr.merged", "data": {"bot": True, "channel_id": "20"}}
    assert trigger_matches(t, e)
