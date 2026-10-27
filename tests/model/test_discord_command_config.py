"""d12: Discord application commands are actor configuration (params.commands)."""

from __future__ import annotations

import pytest

from culture_rules.model import discord_command_config as cc
from culture_rules.model.app_actor import app_param_errors

SAVE = {
    "name": "save",
    "type": "slash",
    "description": "Save a message to the library",
    "options": [
        {"name": "message", "description": "A message link or id", "kind": "message"},
    ],
}
MENU = {"name": "Save to library", "type": "message"}


def _codes(commands):
    return [(path, code) for path, code, _ in cc.command_problems(commands)]


def test_a_slash_and_a_message_command_are_valid():
    assert cc.command_problems([SAVE, MENU]) == []


def test_absent_or_empty_commands_are_valid():
    assert cc.command_problems(None) == []
    assert cc.command_problems([]) == []


def test_commands_must_be_a_list_of_mappings():
    assert _codes("save") == [("commands", "invalid_type")]
    assert _codes(["save"]) == [("commands[0]", "invalid_type")]


@pytest.mark.parametrize(
    ("command", "path"),
    [
        ({**SAVE, "type": "user"}, "commands[0].type"),
        ({**SAVE, "name": "Save"}, "commands[0].name"),  # slash names are lowercase
        ({**SAVE, "name": "x" * 33}, "commands[0].name"),
        ({**SAVE, "name": "save me"}, "commands[0].name"),
        ({**MENU, "name": "  "}, "commands[0].name"),
        ({**MENU, "name": "y" * 33}, "commands[0].name"),
        ({**SAVE, "description": ""}, "commands[0].description"),
        ({**SAVE, "description": "d" * 101}, "commands[0].description"),
        ({**MENU, "description": "menus have none"}, "commands[0].description"),
        ({**MENU, "options": []}, "commands[0].options"),
        ({**SAVE, "not_here": ""}, "commands[0].not_here"),
        # review of #106: what is registered must be exactly what is hashed and matched
        ({**MENU, "name": " Save"}, "commands[0].name"),
        ({**MENU, "name": "Save "}, "commands[0].name"),
        ({**MENU, "name": "Save, then share"}, "commands[0].name"),  # the filter splits on ,
        ({**SAVE, "description": " Save it"}, "commands[0].description"),
        ({**SAVE, "not_here": "n" * 201}, "commands[0].not_here"),
    ],
)
def test_each_command_field_is_checked(command, path):
    assert path in [p for p, _ in _codes([command])]


def _opt(**kw):
    return {"name": "text", "description": "Some text", **kw}


@pytest.mark.parametrize(
    ("options", "path"),
    [
        ([_opt(name="Text")], "commands[0].options[0].name"),
        ([_opt(name="1st")], "commands[0].options[0].name"),
        ([_opt(name="interaction")], "commands[0].options[0].name"),
        ([_opt(name="from")], "commands[0].options[0].name"),  # a Python keyword: no parameter
        ([_opt(name="class")], "commands[0].options[0].name"),
        ([_opt(description="Some text ")], "commands[0].options[0].description"),
        ([_opt(), _opt()], "commands[0].options[1].name"),  # unique
        ([_opt(description="")], "commands[0].options[0].description"),
        ([_opt(kind="number")], "commands[0].options[0].kind"),
        ([_opt(required="yes")], "commands[0].options[0].required"),
        (
            [_opt(name="a", kind="message"), _opt(name="b", kind="message")],
            "commands[0].options[1].kind",
        ),
        ([_opt(name="a"), _opt(name="b", required=True)], "commands[0].options[1].required"),
        ([_opt(name=f"o{i}") for i in range(6)], "commands[0].options"),
    ],
)
def test_each_option_field_is_checked(options, path):
    assert path in [p for p, _ in _codes([{**SAVE, "options": options}])]


def test_names_are_unique_per_type_and_counts_are_capped():
    assert "commands[1].name" in [p for p, _ in _codes([SAVE, dict(SAVE)])]
    assert _codes([SAVE, {**MENU, "name": "save"}]) == []  # a slash and a menu may share it
    many = [{**SAVE, "name": f"c{i}", "options": []} for i in range(11)]
    assert ("commands", "too_many") in _codes(many)
    menus = [{**MENU, "name": f"Menu {i}"} for i in range(6)]
    assert ("commands", "too_many") in _codes(menus)


def test_definitions_are_discords_api_shape():
    assert cc.definitions([SAVE, MENU]) == (
        {
            "type": 1,
            "name": "save",
            "description": "Save a message to the library",
            "options": [
                {
                    "type": 3,
                    "name": "message",
                    "description": "A message link or id",
                    "required": False,
                }
            ],
        },
        {"type": 3, "name": "Save to library"},
    )


def test_not_here_defaults_to_a_neutral_answer():
    assert cc.not_here(SAVE) == cc.DEFAULT_NOT_HERE
    assert "save" not in cc.DEFAULT_NOT_HERE.casefold()
    assert cc.not_here({**SAVE, "not_here": "Not in this channel."}) == "Not in this channel."


def test_the_app_actor_validates_discord_commands_and_refuses_them_elsewhere():
    discord = {
        "surface": "discord",
        "connection": {"bot_token": "grant:bot"},
        "commands": [{**SAVE, "name": "Bad"}],
    }
    assert any(p.startswith("commands[0].name") for p, _, _ in app_param_errors(discord))
    github = {
        "surface": "github",
        "connection": {"private_key": "grant:k", "webhook_secret": "grant:w"},
        "commands": [SAVE],
    }
    assert ("commands", "not_allowed") in [(p, c) for p, c, _ in app_param_errors(github)]
