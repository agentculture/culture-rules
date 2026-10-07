"""Webhook event sink: one typed envelope per delivery id, into the ``events`` collection.

A surface receiver (GitHub, Jira, Discord) verifies a delivery and hands it to :func:`sink`.
The sink never sees or logs secrets, and logs only ``outcome``, actor id and event type -
never the payload or the delivery id.

``actor`` is the app actor **document**: a mapping (``Actor.to_dict()`` shape) or an
:class:`~culture_rules.model.actor.Actor`; ``params.surface``, ``params.events`` and the
optional ``params.self_identity`` are read from it.

Outcomes, checked in this order:

- ``disabled``  - the actor is disabled; nothing is written;
- ``ignored``   - ``type`` is not in the actor's declared ``params.events`` (the allow-list),
  or is reserved for the engine (``rules.run.*``, d21); nothing is written;
- ``duplicate`` - an event for this surface + delivery id already exists (the deterministic
  event id hit the unique id); nothing new is written;
- ``accepted``  - the envelope was inserted; the ``events`` change feed fires triggers on it.

The event id is ``hook_<surface>_<sha256(delivery_id)[:24]>``, so redelivery by the surface (or
a retry) inserts exactly once. The stored document is the ingest shape
(:func:`~culture_rules.events.ingest.event_document`) around an events-cli wire envelope whose
``source`` is ``app://<actor id>`` and whose ``data`` is the payload plus ``delivery_id``,
``actor`` and, whenever the actor names a ``params.self_identity``, ``self_authored`` on every
event: ``true`` when ``author`` equals it (case-insensitive) and the type is not one of the
exempt check-completion types (``github.checks.suite_completed``,
``github.checks.workflow_completed``), ``false`` otherwise - so a human push is told apart from
an untagged one (the attempt budget resets only on an explicit ``false``,
:mod:`culture_rules.node.firing`). Without a ``self_identity`` the key is absent. A payload key
of those names is overwritten, never trusted.

Outcome counters live in :data:`HOOK_STATS_COLLECTION`, one document per (actor, outcome) with
a ``count`` and the ``surface`` (receiver refusals are counted by
:func:`record_outcome`), incremented by compare-and-set (``update_if``) so concurrent writers
do not lose counts. Standard-library only.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Any

from culture_rules.events.emit import derive_envelope, reserved_reason
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.store.port import DuplicateKeyError, StoragePort

__all__ = [
    "ACCEPTED",
    "BAD_REQUEST",
    "DISABLED",
    "DUPLICATE",
    "HOOK_HOST",
    "HOOK_STATS_COLLECTION",
    "IGNORED",
    "OUTCOMES",
    "REFUSALS",
    "SELF_TAG_EXEMPT_TYPES",
    "TOO_LARGE",
    "UNAUTHORIZED",
    "event_id_for",
    "record_outcome",
    "sink",
]

_log = logging.getLogger(__name__)

HOOK_STATS_COLLECTION = "hook_stats"
HOOK_HOST = "webhook"
"""The ``host`` recorded on events written by the sink (they do not come from a host's ingest)."""

ACCEPTED, DUPLICATE, IGNORED, DISABLED = "accepted", "duplicate", "ignored", "disabled"
OUTCOMES = (ACCEPTED, DUPLICATE, IGNORED, DISABLED)
UNAUTHORIZED, BAD_REQUEST, TOO_LARGE = "unauthorized", "bad_request", "too_large"
REFUSALS = (UNAUTHORIZED, BAD_REQUEST, TOO_LARGE)
"""Outcomes of deliveries refused before the sink (see :func:`record_outcome`)."""
SELF_TAG_EXEMPT_TYPES = frozenset(
    ("github.checks.suite_completed", "github.checks.workflow_completed")
)
"""Event types tagged ``self_authored`` *false* even when the author is the app itself.

For every other type a matching author sets it to *true*; a non-matching author sets it to
*false* (once ``params.self_identity`` is configured; without one the key is absent).
"""
_CAS_RETRIES = 50


def event_id_for(surface: str, delivery_id: str) -> str:
    """The deterministic events id for one delivery on one surface."""
    digest = hashlib.sha256(delivery_id.encode("utf-8")).hexdigest()[:24]
    return f"hook_{surface}_{digest}"


def _actor_view(actor: Any) -> Mapping[str, Any]:
    if isinstance(actor, Mapping):
        return actor
    to_dict = getattr(actor, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError("actor must be a mapping or an Actor")


def _count(store: StoragePort, actor_id: str, outcome: str, surface: str | None = None) -> None:
    """Best-effort observability counter; never raises into the delivery path."""
    doc_id = f"{actor_id}:{outcome}"
    try:
        for _ in range(_CAS_RETRIES):
            current = store.get(HOOK_STATS_COLLECTION, doc_id)
            if current is None:
                res = store.update_if(
                    HOOK_STATS_COLLECTION,
                    doc_id,
                    {"count": None},
                    {"count": 1, "actor": actor_id, "outcome": outcome, "surface": surface},
                    upsert=True,
                )
            else:
                n = current.get("count", 0)
                res = store.update_if(HOOK_STATS_COLLECTION, doc_id, {"count": n}, {"count": n + 1})
            if res.won:
                return
    except Exception:  # noqa: BLE001 - stats are observability only
        _log.warning("hook_stats update failed actor=%s outcome=%s", actor_id, outcome)


def record_outcome(
    store: StoragePort, surface: str, outcome: str, actor_id: str | None = None
) -> None:
    """Count a delivery refused before the sink (``unauthorized``, ``bad_request``,
    ``too_large``). With no identified actor the counter is keyed by the surface name
    (``github:unauthorized``). Records the outcome only - never a body, header or secret."""
    _count(store, actor_id or surface, outcome, surface)


def _finish(store: StoragePort, actor_id: str, type: str, outcome: str, surface: str) -> str:
    _count(store, actor_id, outcome, surface)
    _log.info("hook outcome=%s actor=%s type=%s", outcome, actor_id, type)
    return outcome


def sink(
    store: StoragePort,
    actor: Any,
    type: str,
    data: Mapping[str, Any] | None,
    delivery_id: str,
    author: str | None,
) -> str:
    """Record one verified delivery; return ``accepted|duplicate|ignored|disabled``."""
    view = _actor_view(actor)
    actor_id = view.get("id")
    params = view.get("params") or {}
    surface = params.get("surface")
    if not isinstance(actor_id, str) or not actor_id or not isinstance(surface, str):
        raise ValueError("actor must be an app actor with an id and params.surface")
    if not isinstance(delivery_id, str) or not delivery_id:
        raise ValueError("delivery_id must be a non-empty string")
    if not isinstance(type, str) or not type:
        raise ValueError("type must be a non-empty string")
    if view.get("enabled", True) is False:
        return _finish(store, actor_id, type, DISABLED, surface)
    if type not in (params.get("events") or ()) or reserved_reason({"type": type}):
        # an app may never inject the engine's own run events, even if it declares them
        return _finish(store, actor_id, type, IGNORED, surface)

    payload = dict(data or {})
    payload["delivery_id"] = delivery_id
    payload["actor"] = actor_id
    payload.pop("self_authored", None)
    me = params.get("self_identity")
    if isinstance(me, str) and me:
        payload["self_authored"] = (
            isinstance(author, str)
            and author.casefold() == me.casefold()
            and type not in SELF_TAG_EXEMPT_TYPES
        )

    envelope = derive_envelope(
        None,
        type=type,
        source=f"app://{actor_id}",
        data=payload,
        id=event_id_for(surface, delivery_id),
    )
    try:
        store.insert(EVENTS_COLLECTION, event_document(envelope, host=HOOK_HOST))
    except DuplicateKeyError:
        return _finish(store, actor_id, type, DUPLICATE, surface)
    return _finish(store, actor_id, type, ACCEPTED, surface)
