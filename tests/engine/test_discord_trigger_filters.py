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


def test_non_discord_events_unaffected():
    t = Trigger(
        kind="event", params={"type": "github.pr.merged", "channels": ["x"], "users": ["z"]}
    )
    e = {"kind": "event", "type": "github.pr.merged", "data": {"bot": True, "channel_id": "20"}}
    assert trigger_matches(t, e)


# deviation d1: users OR roles; a bare string is a one-item list; no author never admits


def test_users_or_roles_either_admits():
    t = trig(users=["alice"], roles=["r1"])
    assert trigger_matches(t, ev(user_roles=["r1"]))
    assert trigger_matches(t, ev(user_roles=["r2"]))
    assert trigger_matches(t, ev(author_name="bob", user_roles=["r1"]))
    assert not trigger_matches(t, ev(author_name="bob", author_id="31", user_roles=["r2"]))


def test_bare_string_is_a_one_item_list():
    assert trigger_matches(trig(channels="20"), ev())
    assert not trigger_matches(trig(channels="21"), ev())
    assert trigger_matches(trig(users="Alice"), ev())
    assert not trigger_matches(trig(users="bob"), ev())
    assert trigger_matches(trig(roles="r1"), ev(user_roles=["r1"]))
    assert not trigger_matches(trig(roles="r1"), ev(user_roles=["r2"]))


def test_channels_all_and_empty_still_admit_every_channel():
    assert trigger_matches(trig(channels="all"), ev(channel_id="99"))
    assert trigger_matches(trig(channels="ALL"), ev(channel_id="99"))
    assert trigger_matches(trig(channels=""), ev(channel_id="99"))


def test_users_filter_never_admits_an_event_without_an_author():
    e = ev()
    del e["data"]["author_id"]
    del e["data"]["author_name"]
    assert not trigger_matches(trig(users=["alice", ""]), e)
    assert not trigger_matches(trig(users=["alice"]), e)
    # a users list of blanks only is no users filter (like an empty guild_id list)
    assert trigger_matches(trig(users=[""]), e)


# PR #96 review: a bot's own message (self_authored) still passes the Discord filters


def test_include_self_still_applies_the_channel_and_user_filters():
    own = ev(bot=True, self_authored=True, channel_id="22", author_name="Culture.dev")
    assert not trigger_matches(trig(include_self=True, channels=["111"]), own)
    assert not trigger_matches(trig(include_self=True, users=["alice"]), own)
    assert trigger_matches(trig(include_self=True, channels=["22"]), own)
    # include_self admits the bot's own post without also needing include_bots
    assert trigger_matches(trig(include_self=True), own)
    assert not trigger_matches(trig(channels=["22"]), own)
