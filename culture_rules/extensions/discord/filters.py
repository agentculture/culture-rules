"""The ``discord.*`` trigger filters, read one way everywhere (discord-surfaces t2, d1, t4).

The Discord extension registers them as the ``discord`` trigger filter
(:meth:`~culture_rules.extensions.Extension.trigger_filter`): :func:`admits` is the engine's
match (``engine.matching``), :func:`problems` the save-time validation (``model.validate``)
and :func:`words` the describe words (``model.describe``). The gateway's channel selection and
the Discord steps read ``channels``, ``users``, ``roles`` and ``emoji`` through the same
helpers. Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from culture_rules.extensions.registry import Problem
from culture_rules.model.serde import join

CUSTOM_EMOJI_RE = re.compile(r"^\w{2,32}:\d{1,25}$")
"""A server's custom emoji written ``name:id`` (Discord's 2-32 character name and its id): one
grammar for the ``emoji`` trigger filter and ``discord.react``."""

BUTTON_CLICKED = "discord.button.clicked"
"""The event a click on a bot's message button records; ``custom_id`` filters it."""

__all__ = [
    "BUTTON_CLICKED",
    "CUSTOM_EMOJI_RE",
    "admits",
    "custom_id_admits",
    "discord_channels",
    "emoji_admits",
    "emoji_ok",
    "filter_values",
    "problems",
    "words",
]

COMMAND_INVOKED = "discord.command.invoked"
REACTION_ADDED = "discord.reaction.added"
_CHANNEL_ID = re.compile(r"^\d{1,25}$")
_INVALID = "trigger_param_invalid"


def filter_values(value: Any) -> list[str]:
    """A filter param as a list of strings: a list as is, a bare string or number as one item,
    anything else (absent, empty) as no items."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v) for v in value if str(v) != ""]
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (str, int)):
        return [str(value)] if str(value) != "" else []
    return []


def discord_channels(params: Mapping[str, Any]) -> frozenset[str] | None:
    """The channel ids a Discord trigger's ``channels`` param selects; ``None`` when it
    selects every channel (absent, empty, or ``"all"`` in any case)."""
    channels = filter_values(params.get("channels"))
    if not channels or any(c.casefold() == "all" for c in channels):
        return None
    return frozenset(channels)


VARIATION_SELECTOR = "\ufe0f"
"""U+FE0F: whether an emoji carries it is a keyboard's choice (❤ vs ❤️), not another emoji."""


def emoji_ok(value: Any) -> bool:
    """One emoji grammar, for the ``emoji`` trigger filter and ``discord.react``: a custom
    ``name:id`` (:data:`CUSTOM_EMOJI_RE`, matched whole), or one token with no colon, slash
    or whitespace (a unicode emoji, or for the filter a bare id or name)."""
    if not isinstance(value, str) or not value:
        return False
    if CUSTOM_EMOJI_RE.fullmatch(value):
        return True
    return not any(c in value for c in ":/") and not any(c.isspace() for c in value)


def _plain(emoji: str) -> str:
    return emoji.replace(VARIATION_SELECTOR, "")


def emoji_admits(wanted: list[str], data: Mapping[str, Any]) -> bool:
    """Whether a reaction's emoji is one of ``wanted`` (an ``emoji`` filter, discord-surfaces
    t15): a unicode emoji matches ``emoji_name``; a custom one matches by ``name:id`` (the id
    decides, so another server's emoji of the same name is not it), by its id alone, or by its
    name. U+FE0F is ignored on both sides. An event with no emoji never matches."""
    name = _plain(str(data.get("emoji_name") or ""))
    emoji_id = str(data.get("emoji_id") or "")
    for value in wanted:
        if CUSTOM_EMOJI_RE.fullmatch(value):
            if emoji_id and value.rpartition(":")[2] == emoji_id:
                return True
        elif _plain(value) in {name, emoji_id} - {""}:
            return True
    return False


def custom_id_admits(wanted: list[str], data: Mapping[str, Any]) -> bool:
    """Whether a click's ``custom_id`` is picked by one of ``wanted`` (the ``custom_id``
    filter, case-sensitive, since custom ids are opaque): a value ending in ``:`` is a prefix
    (``merge:`` admits ``merge:<token>``); any other value admits the id itself or the id
    followed by ``:`` (``merge`` admits ``merge`` and ``merge:<token>``, never
    ``merge-later:<token>``). A click with no custom id never matches."""
    custom_id = data.get("custom_id")
    if not isinstance(custom_id, str) or not custom_id:
        return False
    return any(_custom_id_picks(value, custom_id) for value in wanted)


def _custom_id_picks(value: str, custom_id: str) -> bool:
    if value.endswith(":"):
        return custom_id.startswith(value)
    return custom_id == value or custom_id.startswith(value + ":")


# --------------------------------------------------------------------------- match


def admits(params: Mapping[str, Any], data: Mapping[str, Any], own: bool = False) -> bool:
    """Discord filters: ``include_bots`` (default off), ``channels`` (ids; absent, empty or
    ``"all"`` is every channel), and who: ``users`` (ids or names, case-insensitive) and
    ``roles`` (role ids against ``data.user_roles``), ``emoji`` (a reaction's emoji,
    :func:`emoji_admits`) and ``command`` (an invoked command's name, case-insensitive,
    discord-surfaces d12) and ``custom_id`` (a button click's custom id, case-sensitive: a
    value ending in ``:`` is a prefix, any other is the id itself or the id followed by ``:``;
    :func:`custom_id_admits`). A bare string is a one-item list. With both ``users`` and
    ``roles`` set, either admits (a listed user, or anyone holding a listed role); an event with
    no author id or name never passes a ``users`` filter, and one with no emoji never passes an
    ``emoji`` filter."""
    # the bot's own post (``own``, admitted by include_self) needs no include_bots, but the
    # channel and who filters still apply to it
    if data.get("bot") is True and not own and params.get("include_bots") is not True:
        return False
    channels = discord_channels(params)
    if channels is not None and str(data.get("channel_id")) not in channels:
        return False
    emoji = filter_values(params.get("emoji"))
    if emoji and not emoji_admits(emoji, data):
        return False
    custom_ids = filter_values(params.get("custom_id"))
    if custom_ids and not custom_id_admits(custom_ids, data):
        return False
    commands = {c.casefold() for c in filter_values(params.get("command"))}
    if commands and str(data.get("command") or "").casefold() not in commands:
        return False
    users = {u.casefold() for u in filter_values(params.get("users"))}
    roles = set(filter_values(params.get("roles")))
    if not users and not roles:
        return True
    who = {str(data.get(key) or "").casefold() for key in ("author_id", "author_name")} - {""}
    if users & who:
        return True
    return bool(roles & set(filter_values(data.get("user_roles"))))


# --------------------------------------------------------------------------- validate


def problems(params: Mapping[str, Any], path: str) -> list[Problem]:
    """An event trigger's Discord filter problems, ``(path, code, message)`` each: on a
    ``discord.*`` trigger the filters' types; on any event trigger ``command`` and
    ``custom_id``, which only their own event type takes."""
    out: list[Problem] = []
    if str(params.get("type", "")).startswith("discord."):
        _check_discord_filters(params, path, out)
    _check_command_filter(params, path, out)
    _check_custom_id_filter(params, path, out)
    return out


def _filter_item(value: Any) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _check_discord_filters(params: Mapping[str, Any], pp: str, out: list[Problem]) -> None:
    """A ``discord.*`` trigger's filters: ``include_bots`` a bool; ``channels``, ``users`` and
    ``roles`` an id/name or a list of them; ``emoji`` a unicode emoji, a custom ``name:id``, an
    id or a name, or a list of them."""
    if "include_bots" in params and not isinstance(params["include_bots"], bool):
        out.append(
            (join(pp, "include_bots"), _INVALID, "params.include_bots must be true or false")
        )
    for name in ("channels", "users", "roles"):
        value = params.get(name)
        if not _filter_value_ok(value):
            out.append(
                (
                    join(pp, name),
                    _INVALID,
                    f"params.{name} must be an id or name, or a list of them",
                )
            )
            continue
        if name == "channels" and value is not None and not _channel_ids_ok(value):
            out.append(
                (
                    join(pp, name),
                    _INVALID,
                    'params.channels holds numeric Discord channel ids, or "all"',
                )
            )

    if "emoji" in params and params.get("type") != REACTION_ADDED:
        out.append(
            (
                join(pp, "emoji"),
                _INVALID,
                f"params.emoji filters reactions: only a {REACTION_ADDED} trigger takes it",
            )
        )
    elif "emoji" in params and not _emoji_list_ok(params["emoji"]):
        out.append(
            (
                join(pp, "emoji"),
                _INVALID,
                "params.emoji is an emoji (unicode, or a custom name:id), or a list of them",
            )
        )


def _check_command_filter(params: Mapping[str, Any], pp: str, out: list[Problem]) -> None:
    """``command`` (d12): one command name or a list, on a discord.command.invoked trigger."""
    if "command" not in params:
        return
    if params.get("type") != COMMAND_INVOKED:
        out.append(
            (
                join(pp, "command"),
                _INVALID,
                f"params.command picks a command: only a {COMMAND_INVOKED} trigger takes it",
            )
        )
        return
    value = params["command"]
    items = value if isinstance(value, (list, tuple)) else [value]
    if not items or not all(isinstance(i, str) and i.strip() for i in items):
        out.append(
            (join(pp, "command"), _INVALID, "params.command is a command name, or a list of them")
        )


def _check_custom_id_filter(params: Mapping[str, Any], pp: str, out: list[Problem]) -> None:
    """``custom_id``: one string or a list, on a discord.button.clicked trigger (a value ending
    in ``:`` is a prefix; any other is the id itself or the id followed by ``:``)."""
    if "custom_id" not in params:
        return
    if params.get("type") != BUTTON_CLICKED:
        out.append(
            (
                join(pp, "custom_id"),
                _INVALID,
                f"params.custom_id picks a button: only a {BUTTON_CLICKED} trigger takes it",
            )
        )
        return
    value = params["custom_id"]
    items = value if isinstance(value, (list, tuple)) else [value]
    if not items or not all(isinstance(i, str) and i for i in items):
        out.append(
            (
                join(pp, "custom_id"),
                _INVALID,
                "params.custom_id is a button's custom id (or a prefix ending in ':'), "
                "or a list of them",
            )
        )


def _emoji_list_ok(value: Any) -> bool:
    items = value if isinstance(value, (list, tuple)) else [value]
    if not items:
        return False
    return all(emoji_ok(item) for item in items)


def _filter_value_ok(value: Any) -> bool:
    """Absent, one id or name, or a list of them."""
    if value is None or _filter_item(value):
        return True
    return isinstance(value, (list, tuple)) and all(_filter_item(v) for v in value)


def _channel_ids_ok(value: Any) -> bool:
    """Every ``channels`` entry is a numeric Discord id or ``all`` (a name never matches)."""
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        text = str(item).strip()
        if text and text.casefold() != "all" and not _CHANNEL_ID.match(text):
            return False
    return True


# --------------------------------------------------------------------------- describe


def words(params: Mapping[str, Any]) -> list[str]:
    """``(in channels 1, 2; from alice or role r1; bots too)``: a discord.* trigger's filters,
    read as :func:`admits` applies them; nothing when every filter is open."""
    parts = []
    channels = discord_channels(params)
    if channels is not None:
        parts.append("in channels " + ", ".join(sorted(channels)))
    emoji = filter_values(params.get("emoji"))
    if emoji:
        parts.append("with " + " or ".join(emoji))
    buttons = filter_values(params.get("custom_id"))
    if buttons:
        parts.append("with button " + " or ".join(buttons))
    commands = filter_values(params.get("command"))
    if commands:
        parts.append("for command " + " or ".join(commands))
    who = [*filter_values(params.get("users"))]
    who += [f"role {r}" for r in filter_values(params.get("roles"))]
    if who:
        parts.append("from " + " or ".join(who))
    if params.get("include_bots") is True:
        parts.append("bots too")
    return [f"({'; '.join(parts)})"] if parts else []
