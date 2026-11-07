"""The GitHub event types and id prefixes only the engine writes, and their reservations.

The checks settler (:mod:`~culture_rules.extensions.github.checks_settle`) writes
``github.pr.checks_settled`` and ``github.pr.checks_failed_late`` under deterministic ids
(``settled_*``, ``late_*``); ``github.pr.conflicting`` (d31, ids ``conflict_*``) and
``github.reaction.added`` (d34, ids ``reaction_*``: GitHub sends no webhook for reactions) are
reserved the same way. A copy from the bus or a webhook could fire a rule or squat a
deterministic id, so the extension registers :func:`reaction_reserved` and
:func:`settle_reserved` as event reservations
(:meth:`~culture_rules.extensions.Extension.event_reservation`), which
``events.emit.reserved_reason`` checks at external ingest (d25). Standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "CHECKS_LATE_TYPE",
    "CHECKS_SETTLED_TYPE",
    "CONFLICT_ID_PREFIX",
    "LATE_ID_PREFIX",
    "PR_CONFLICTING_TYPE",
    "REACTION_ADDED_TYPE",
    "REACTION_ID_PREFIX",
    "SETTLED_ID_PREFIX",
    "SETTLE_ID_PREFIXES",
    "SETTLE_TYPES",
    "SETTLE_TYPE_NAMES",
    "reaction_reserved",
    "settle_reserved",
]

CHECKS_SETTLED_TYPE = "github.pr.checks_settled"
CHECKS_LATE_TYPE = "github.pr.checks_failed_late"
PR_CONFLICTING_TYPE = "github.pr.conflicting"
SETTLE_TYPES = frozenset((CHECKS_SETTLED_TYPE, CHECKS_LATE_TYPE, PR_CONFLICTING_TYPE))
SETTLE_TYPE_NAMES = (CHECKS_SETTLED_TYPE, CHECKS_LATE_TYPE, PR_CONFLICTING_TYPE)
"""The checks settler's event types and ``github.pr.conflicting`` (d31): written only by the
engine, from its internal sources; reserved at external ingest (d25)."""
REACTION_ADDED_TYPE = "github.reaction.added"
REACTION_ID_PREFIX = "reaction_"
"""Reaction events (d34): only the engine may write these; the type and the id prefix are
reserved at external ingest, so no copy can squat them."""
SETTLED_ID_PREFIX = "settled_"
LATE_ID_PREFIX = "late_"
CONFLICT_ID_PREFIX = "conflict_"
SETTLE_ID_PREFIXES = (SETTLED_ID_PREFIX, LATE_ID_PREFIX, CONFLICT_ID_PREFIX)
"""The settler's deterministic event id prefixes; reserved so no copy can squat them (d25)."""


def reaction_reserved(envelope: Mapping[str, Any]) -> str | None:
    """Why a type or id in the reaction namespace is refused (d34), or ``None``."""
    kind, eid = envelope.get("type"), envelope.get("id")
    if kind == REACTION_ADDED_TYPE:
        return f"type {REACTION_ADDED_TYPE} is reserved for the engine's reaction watch"
    if isinstance(eid, str) and eid.startswith(REACTION_ID_PREFIX):
        return f"id prefix {REACTION_ID_PREFIX} is reserved for the engine's reaction watch"
    return None


def settle_reserved(envelope: Mapping[str, Any]) -> str | None:
    """Why an id or type in the checks settler's namespace is refused (d25), or ``None``."""
    kind, eid = envelope.get("type"), envelope.get("id")
    if kind in SETTLE_TYPE_NAMES:
        return f"type {kind} is reserved for the engine's checks settle"
    if isinstance(eid, str) and eid.startswith(SETTLE_ID_PREFIXES):
        return "id prefixes settled_, late_ and conflict_ are reserved for the engine"
    return None
