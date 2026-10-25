"""Discord trigger filter params, read one way everywhere (discord-surfaces t2, d1, t4).

The engine's matcher, the gateway's channel selection and the describe words all read a
``discord.*`` trigger's ``channels``, ``users`` and ``roles`` through these helpers.
Standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["discord_channels", "filter_values"]


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
