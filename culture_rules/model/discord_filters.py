"""Discord trigger filter params, read one way everywhere (discord-surfaces t2, d1, t4).

The engine's matcher, the gateway's channel selection and the describe words all read a
``discord.*`` trigger's ``channels``, ``users``, ``roles`` and ``emoji`` through these helpers.
Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

CUSTOM_EMOJI_RE = re.compile(r"^\w{2,32}:\d{1,25}$")
"""A server's custom emoji written ``name:id`` (Discord's 2-32 character name and its id): one
grammar for the ``emoji`` trigger filter and ``discord.react``."""

__all__ = ["CUSTOM_EMOJI_RE", "discord_channels", "emoji_admits", "filter_values"]


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


def emoji_admits(wanted: list[str], data: Mapping[str, Any]) -> bool:
    """Whether a reaction's emoji is one of ``wanted`` (an ``emoji`` filter, discord-surfaces
    t15): a unicode emoji matches ``emoji_name``; a custom one matches by ``name:id`` (the id
    decides, so another server's emoji of the same name is not it), by its id alone, or by its
    name. An event with no emoji never matches."""
    name = str(data.get("emoji_name") or "")
    emoji_id = str(data.get("emoji_id") or "")
    for value in wanted:
        if CUSTOM_EMOJI_RE.match(value):
            if emoji_id and value.rpartition(":")[2] == emoji_id:
                return True
        elif value in {name, emoji_id} - {""}:
            return True
    return False
