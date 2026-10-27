"""discord-surfaces t8: the Discord trigger filters are validated and described."""

from __future__ import annotations

import pytest

from culture_rules.model.describe import trigger_text
from culture_rules.model.rule import Trigger
from culture_rules.model.validate import validate

TYPE = "discord.message.created"


def codes(params: dict, type_: str = TYPE) -> set[str]:
    trig = Trigger(kind="event", params={"type": type_, **params})
    return {e.code for e in validate(trig)}


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"include_bots": True},
        {"channels": ["1", "2"], "users": ["alice", "30"], "roles": ["r1"]},
        {"channels": "all"},
        {"channels": "20", "users": "alice", "roles": 7},
        {"channels": ["all"]},
        {"channels": [20, "21"]},
    ],
)
def test_well_typed_discord_filters_are_accepted(params):
    assert codes(params) == set()


@pytest.mark.parametrize(
    "params",
    [
        {"include_bots": "yes"},
        {"channels": {"id": "1"}},
        {"users": [["alice"]]},
        {"roles": [True]},
        {"channels": ["general"]},
        {"channels": "CHANNEL_ID_PLACEHOLDER"},
        {"channels": ["20", "12ab"]},
    ],
)
def test_badly_typed_discord_filters_are_refused(params):
    assert codes(params) == {"trigger_param_invalid"}


def test_other_event_types_keep_their_params_unchecked():
    assert codes({"include_bots": "yes", "channels": {"x": 1}}, "github.pr.opened") == set()


def test_trigger_text_spells_out_the_discord_filters():
    plain = trigger_text(Trigger(kind="event", params={"type": TYPE}))
    assert plain == TYPE
    text = trigger_text(
        Trigger(
            kind="event",
            params={
                "type": TYPE,
                "channels": ["1", "2"],
                "users": ["alice"],
                "roles": ["r1"],
                "include_bots": True,
            },
        )
    )
    assert text.startswith(TYPE)
    for word in ("1, 2", "alice", "r1", "bots"):
        assert word in text
    assert trigger_text(Trigger(kind="event", params={"type": TYPE, "channels": "all"})) == TYPE


@pytest.mark.parametrize("emoji", ["📚", ["📚", "save:99"], "99"])
def test_an_emoji_filter_is_an_emoji_or_a_list_of_them(emoji):
    assert codes({"emoji": emoji}, "discord.reaction.added") == set()


@pytest.mark.parametrize("emoji", [7, ["📚", 7], {"name": "save"}, "", [""], "save:abc"])
def test_a_badly_typed_emoji_filter_is_refused(emoji):
    assert codes({"emoji": emoji}, "discord.reaction.added") == {"trigger_param_invalid"}


def test_trigger_text_names_the_emoji():
    trig = Trigger(
        kind="event",
        params={"type": "discord.reaction.added", "emoji": ["📚", "save:99"], "users": "alice"},
    )
    text = trigger_text(trig)
    assert "with 📚 or save:99" in text
    assert "from alice" in text


@pytest.mark.parametrize("emoji", ["thumbs up", "save:123\n", "a/b", ":"])
def test_an_emoji_that_could_never_match_is_refused(emoji):
    # review of PR #102: one grammar (discord_filters.emoji_ok), matched whole
    assert codes({"emoji": emoji}, "discord.reaction.added") == {"trigger_param_invalid"}


def test_an_emoji_filter_belongs_to_reaction_triggers_only():
    # review of PR #102: a hidden emoji filter left on a message trigger never fires
    assert codes({"emoji": "📚"}, "discord.message.created") == {"trigger_param_invalid"}


def test_one_emoji_grammar_for_the_filter_and_the_action():
    from culture_rules.model.discord_filters import emoji_ok

    assert emoji_ok("📚")
    assert emoji_ok("save:99")
    assert emoji_ok("99")
    assert not emoji_ok("save:99\n")
    assert not emoji_ok("thumbs up")
    assert not emoji_ok("")


@pytest.mark.parametrize("command", ["save", ["save", "Save to library"]])
def test_a_command_filter_is_a_name_or_a_list_of_them(command):
    assert codes({"command": command}, "discord.command.invoked") == set()


@pytest.mark.parametrize("command", [7, "", [""], ["save", 3], {"name": "save"}])
def test_a_badly_typed_command_filter_is_refused(command):
    assert codes({"command": command}, "discord.command.invoked") == {"trigger_param_invalid"}


def test_only_a_command_trigger_takes_a_command_filter():
    assert codes({"command": "save"}) == {"trigger_param_invalid"}
    # review of #106: not on a non-Discord event either, where it would be ignored
    assert codes({"command": "deploy"}, "github.issue_comment.created") == {"trigger_param_invalid"}


def test_trigger_text_names_the_command():
    trig = Trigger(
        kind="event",
        params={"type": "discord.command.invoked", "command": ["save", "Save to library"]},
    )
    assert "for command save or Save to library" in trigger_text(trig)
