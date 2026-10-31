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


# --------------------------------------------------------------------------- emoji (t15)

REACTION = "discord.reaction.added"


def react(**data) -> dict:
    base = {
        "message_id": "1",
        "guild_id": "10",
        "channel_id": "20",
        "user_id": "30",
        "author_id": "30",
        "author_name": "Alice",
        "username": "Alice",
        "bot": False,
        "emoji_name": "📚",
        "emoji_id": None,
    }
    base.update(data)
    return {"kind": "event", "type": REACTION, "data": base}


def rtrig(**params) -> Trigger:
    return Trigger(kind="event", params={"type": REACTION, **params})


def test_a_reaction_with_no_emoji_filter_matches_any_emoji():
    assert trigger_matches(rtrig(), react())
    assert trigger_matches(rtrig(), react(emoji_name="save", emoji_id="99"))


def test_a_unicode_emoji_filter_matches_that_emoji_only():
    assert trigger_matches(rtrig(emoji="📚"), react())
    assert not trigger_matches(rtrig(emoji="👍"), react())
    assert trigger_matches(rtrig(emoji=["👍", "📚"]), react())


def test_a_custom_emoji_filter_matches_by_id_or_name():
    custom = react(emoji_name="save", emoji_id="99")
    assert trigger_matches(rtrig(emoji="save:99"), custom)
    assert trigger_matches(rtrig(emoji="99"), custom)
    assert trigger_matches(rtrig(emoji="save"), custom)
    # name:id needs the id: another server's emoji of the same name is not this one
    assert not trigger_matches(rtrig(emoji="save:100"), custom)
    assert not trigger_matches(rtrig(emoji="save"), react())


def test_an_emoji_filter_admits_no_event_without_an_emoji():
    assert not trigger_matches(trig(emoji="📚"), ev())


def test_the_emoji_filter_combines_with_who_and_channels():
    assert trigger_matches(rtrig(emoji="📚", users=["alice"], channels=["20"]), react())
    assert not trigger_matches(rtrig(emoji="📚", users=["bob"]), react())
    assert not trigger_matches(rtrig(emoji="📚", channels=["21"]), react())


def test_the_variation_selector_does_not_split_one_emoji():
    # review of PR #102: "❤" typed without U+FE0F must match Discord's "❤️", and back
    assert trigger_matches(rtrig(emoji="❤"), react(emoji_name="❤️"))
    assert trigger_matches(rtrig(emoji="❤️"), react(emoji_name="❤"))


def test_a_skin_tone_is_a_different_emoji():
    assert not trigger_matches(rtrig(emoji="👍"), react(emoji_name="👍🏽"))


# --- the custom_id filter picks a flow's button ----------------------------------------

CLICK = "discord.button.clicked"


def btrig(**params) -> Trigger:
    return Trigger(kind="event", params={"type": CLICK, **params})


def clicked(custom_id: str | None = "merge:o/r#3", **data) -> dict:
    base = {"custom_id": custom_id, "channel_id": "20", "author_id": "30", "bot": False}
    base.update(data)
    return {"kind": "event", "type": CLICK, "data": base}


def test_no_custom_id_filter_admits_every_click():
    assert trigger_matches(btrig(), clicked("anything"))


def test_a_custom_id_filter_is_a_prefix_match():
    assert trigger_matches(btrig(custom_id="merge:"), clicked("merge:o/r#3"))
    assert trigger_matches(btrig(custom_id=["close:", "merge:"]), clicked("merge:o/r#3"))
    assert not trigger_matches(btrig(custom_id="merge:"), clicked("close:o/r#3"))
    assert not trigger_matches(btrig(custom_id="merge:o/r#3"), clicked("merge:"))


def test_a_bare_custom_id_filter_admits_its_valued_button():
    assert trigger_matches(btrig(custom_id="merge"), clicked("merge:tok1"))


def test_a_bare_custom_id_filter_admits_the_id_itself():
    assert trigger_matches(btrig(custom_id="merge"), clicked("merge"))


def test_a_bare_custom_id_filter_does_not_admit_a_longer_name():
    assert not trigger_matches(btrig(custom_id="merge"), clicked("merge-later:tok1"))


def test_a_bare_custom_id_filter_does_not_admit_a_longer_unvalued_id():
    assert not trigger_matches(btrig(custom_id="merge"), clicked("merged"))


def test_a_custom_id_filter_ending_in_a_colon_is_a_prefix():
    assert trigger_matches(btrig(custom_id="merge:"), clicked("merge:tok1"))


def test_a_custom_id_filter_ending_in_a_colon_does_not_admit_another_name():
    assert not trigger_matches(btrig(custom_id="merge:"), clicked("merge-later:tok1"))


def test_a_custom_id_filter_ending_in_a_colon_does_not_admit_the_bare_name():
    assert not trigger_matches(btrig(custom_id="merge:"), clicked("merge"))


def test_a_custom_id_filter_is_case_sensitive():
    assert not trigger_matches(btrig(custom_id="Merge:"), clicked("merge:o/r#3"))


def test_a_custom_id_filter_admits_no_click_without_a_custom_id():
    assert not trigger_matches(btrig(custom_id="merge:"), clicked(None))


def test_a_custom_id_filter_still_honours_users_and_roles():
    trig = btrig(custom_id="merge:", roles=["r1"])
    assert trigger_matches(trig, clicked(user_roles=["r1"]))
    assert not trigger_matches(trig, clicked(user_roles=["r2"]))


# --- d12: the command filter picks a flow's command ------------------------------------

COMMAND = "discord.command.invoked"


def ctrig(**params) -> Trigger:
    return Trigger(kind="event", params={"type": COMMAND, **params})


def invoked(command: str = "save", **data) -> dict:
    base = {"command": command, "channel_id": "20", "author_id": "30", "bot": False}
    base.update(data)
    return {"kind": "event", "type": COMMAND, "data": base}


def test_no_command_filter_matches_every_command():
    assert trigger_matches(ctrig(), invoked("save"))
    assert trigger_matches(ctrig(), invoked("Save to library"))


def test_a_command_filter_matches_its_commands_only():
    assert trigger_matches(ctrig(command="save"), invoked("save"))
    assert not trigger_matches(ctrig(command="save"), invoked("digest"))
    assert trigger_matches(ctrig(command=["save", "Save to library"]), invoked("Save to library"))


def test_a_command_filter_ignores_case():
    # review of #106: slash names are lowercase; "Save" in a rule must not silently miss
    assert trigger_matches(ctrig(command="Save"), invoked("save"))
    assert trigger_matches(ctrig(command="save to library"), invoked("Save to library"))
    assert not trigger_matches(ctrig(command="sav"), invoked("save"))


def test_a_command_filter_admits_no_event_without_a_command():
    assert not trigger_matches(ctrig(command="save"), invoked(None))
