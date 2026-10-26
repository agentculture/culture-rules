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
