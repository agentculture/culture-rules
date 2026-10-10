"""Story stops (d34, #40): a trusted ``/stop`` or 👎 ends a PR's fixer story.

The ``queue.stop`` built-in (:mod:`culture_rules.node.actions.queue`) records the stop on
the PR's concurrency key, one document per key in :data:`STOPS_COLLECTION` (the latest
stop wins): ``key``, ``at`` (when it was recorded), ``by`` (the login that asked) and
``head_sha`` (the PR head it was stopped at, when known). Two readers honour it:

* ``queue.add`` drops a request of the stopped story - one whose story began (its chain's
  root run was created) no later than the stop - and an automatic request for the head the
  story was stopped at; a rule that resets the attempt budget (a trusted ``/fix``, d32) or
  a new head starts a new story (:func:`add_refusal`).
* ``github.push`` refuses ``story_stopped`` for a chain whose story began no later than
  the stop (:func:`chain_stopped`), so a run that slipped past the cancel never pushes.

A story's beginning is its root run (:func:`~culture_rules.node.fixer_status.chain_root`):
the run an external event started, walked back through the fixer queue. Standard-library
only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

__all__ = [
    "STOPS_COLLECTION",
    "STORY_STOPPED",
    "add_refusal",
    "chain_stopped",
    "record_stop",
    "stop_of",
    "story_began_before",
    "story_root",
]

STOPS_COLLECTION = "story_stops"
STORY_STOPPED = "story_stopped"
STOPPED_AT_HEAD = "stopped_at_head"
_RUNS = "runs"  # culture_rules.engine.runs.RUNS_COLLECTION


def _doc_id(key: str) -> str:
    return "stop:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


def _parse(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def record_stop(
    store: Any, key: str, *, by: str, head_sha: str | None, at: datetime
) -> dict[str, Any]:
    """Record (or replace) the stop of ``key``'s story; the stored document."""
    doc = {
        "id": _doc_id(key),
        "key": key,
        "by": by,
        "head_sha": head_sha,
        "at": at.isoformat(),
    }
    store.put(STOPS_COLLECTION, doc)
    return doc


def stop_of(store: Any, key: str | None) -> Mapping[str, Any] | None:
    """The latest stop recorded on ``key``, or None."""
    if not isinstance(key, str) or not key:
        return None
    return store.get(STOPS_COLLECTION, _doc_id(key))


def story_began_before(stop: Mapping[str, Any] | None, created_at: Any) -> bool:
    """Whether a story whose root run was created at ``created_at`` began no later than
    ``stop`` (so it is the stopped story). False without a stop or a readable time."""
    at, began = _parse((stop or {}).get("at")), _parse(created_at)
    return at is not None and began is not None and began <= at


def _root(store: Any, run: Mapping[str, Any]) -> Mapping[str, Any]:
    from culture_rules.node.fixer_status import chain_root  # noqa: PLC0415

    return chain_root(store, run) or run


def story_root(store: Any, run: Mapping[str, Any]) -> Mapping[str, Any]:
    """The run that began ``run``'s story (its chain root; ``run`` itself when the chain
    does not verify)."""
    return _root(store, run)


def add_refusal(store: Any, key: str | None, head_sha: Any, run_id: str | None) -> str | None:
    """Why ``queue.add`` drops a request (module doc), or None: the run ``run_id`` adding
    it belongs to the stopped story (:data:`STORY_STOPPED`), or asks automatically for the
    head the story was stopped at (``stopped_at_head``)."""
    stop = stop_of(store, key)
    if stop is None:
        return None
    run = store.get(_RUNS, run_id) if isinstance(run_id, str) and run_id else None
    if run is not None and story_began_before(stop, _root(store, run).get("created_at")):
        return STORY_STOPPED
    stopped_head = stop.get("head_sha")
    if stopped_head and head_sha == stopped_head and not _explicit(run):
        return STOPPED_AT_HEAD
    return None


def _explicit(run: Mapping[str, Any] | None) -> bool:
    """Whether ``run``'s rule starts a new story on its own (``resets_attempt_budget``: a
    trusted ``/fix``, d32)."""
    pinned = (run or {}).get("rule")
    definition = pinned.get("definition") if isinstance(pinned, Mapping) else None
    return isinstance(definition, Mapping) and definition.get("resets_attempt_budget") is True


def chain_stopped(store: Any, runs: Iterable[Mapping[str, Any]]) -> str | None:
    """:data:`STORY_STOPPED` when a stop recorded on a run's concurrency key came no
    earlier than the chain's oldest run (its story's beginning), else None."""
    runs = [r for r in runs if isinstance(r, Mapping)]
    times = [t for t in (_parse(r.get("created_at")) for r in runs) if t is not None]
    if not times:
        return None
    began = min(times).isoformat()
    for key in {r.get("concurrency_key") for r in runs}:
        if story_began_before(stop_of(store, key), began):
            return STORY_STOPPED
    return None
