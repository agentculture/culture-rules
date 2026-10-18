"""The server-owned ``revision`` of a stored workflow (spec c34, deviation d7).

``revision`` is store metadata, like ``updated_at`` and the tombstone fields: it sits at the
top level of the stored document, outside the :class:`~culture_rules.model.workflow.Workflow`
model (``version`` keeps its meaning: the definition version a rule may pin). Every write of
a workflow document sets it to the stored revision plus one, inside the write's
transaction, so it only ever grows; a document written before revisions existed reads as
revision 0. ``PUT /workflows/{id}`` writes only when the caller's ``expected_revision``
equals the stored one, so a stale editor tab can never overwrite a newer workflow.

Only workflows carry a revision; rules, actors and machines are unchanged. Standard-library
only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "EXPECTED_REVISION",
    "REVISION",
    "REVISIONED",
    "bump",
    "current_revision",
    "guard",
    "is_revisioned",
]

REVISION = "revision"
"""The stored field: an int >= 1 once written by a release that keeps revisions."""

EXPECTED_REVISION = "expected_revision"
"""The ``PUT /workflows/{id}`` body field naming the revision the caller read."""

REVISIONED: frozenset[str] = frozenset({"workflows"})
"""The collections whose documents carry a revision."""


def is_revisioned(collection: str) -> bool:
    return collection in REVISIONED


def current_revision(document: Mapping[str, Any] | None) -> int:
    """The stored revision of ``document``; 0 when it has none (or does not exist)."""
    if document is None:
        return 0
    value = document.get(REVISION)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def bump(collection: str, before: Mapping[str, Any] | None) -> dict[str, Any]:
    """The field to write with a change to ``collection``: ``{"revision": stored + 1}``, or
    nothing for a collection without revisions."""
    if not is_revisioned(collection):
        return {}
    return {REVISION: current_revision(before) + 1}


def guard(collection: str, before: Mapping[str, Any] | None) -> dict[str, Any]:
    """The ``update_if`` expectation that the revision is still the one read in ``before``
    (``None`` matches a document that has none), or nothing for other collections."""
    if not is_revisioned(collection) or before is None:
        return {}
    return {REVISION: before.get(REVISION)}
