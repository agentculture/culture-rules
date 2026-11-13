"""The ``jira.*`` trigger filters, read one way everywhere.

The Jira extension registers them as the ``jira`` trigger filter
(:meth:`~culture_rules.extensions.Extension.trigger_filter`): :func:`admits` is the engine's
match (``engine.matching``), :func:`problems` the save-time validation (``model.validate``)
and :func:`words` the describe words (``model.describe``). The one filter is ``users``: a
list of Jira accountIds matched against the event's ``data.author``. Standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.extensions.registry import Problem
from culture_rules.model.serde import join

__all__ = ["admits", "problems", "words"]

_INVALID = "trigger_param_invalid"
_PREFIX = "jira."


def _users(value: Any) -> list[str] | None:
    """``users`` as its accountIds when it is a non-empty list of non-blank strings, else
    None (the one shape :func:`problems` accepts and :func:`admits` honours)."""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if not all(isinstance(v, str) and v.strip() for v in value):
        return None
    return list(value)


# --------------------------------------------------------------------------- match


def admits(params: Mapping[str, Any], data: Mapping[str, Any], own: bool = False) -> bool:
    """Jira filters: ``users``, the accountIds whose events fire the trigger, matched exactly
    (accountIds are opaque) against ``data.author``. Without ``users`` every event passes, as
    before. With it, an event with no author never passes, nor does one the actor wrote itself
    (``data.self_authored``, or ``own``), even under ``include_self`` and even when its author
    is listed: the actor's own identity is never a user the filter picks. A malformed ``users``
    (one :func:`problems` refuses) passes nothing."""
    if "users" not in params:
        return True
    users = _users(params.get("users"))
    if users is None:
        return False
    if own or data.get("self_authored") is True:
        return False
    author = data.get("author")
    return isinstance(author, str) and author != "" and author in users


# --------------------------------------------------------------------------- validate


def problems(params: Mapping[str, Any], path: str) -> list[Problem]:
    """A ``jira.*`` trigger's filter problems, ``(path, code, message)`` each: ``users``, when
    given, is a non-empty list of accountIds (non-blank strings)."""
    if not str(params.get("type", "")).startswith(_PREFIX) or "users" not in params:
        return []
    if _users(params["users"]) is not None:
        return []
    return [
        (
            join(path, "users"),
            _INVALID,
            "params.users is a non-empty list of Jira accountIds (strings)",
        )
    ]


# --------------------------------------------------------------------------- describe


def words(params: Mapping[str, Any]) -> list[str]:
    """``(by A or B)``: a jira.* trigger's ``users`` filter, as :func:`admits` applies it;
    nothing when it is absent or malformed."""
    users = _users(params.get("users"))
    return [f"(by {' or '.join(users)})"] if users else []
