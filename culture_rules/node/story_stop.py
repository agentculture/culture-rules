"""Story stops (d34, #40): a trusted ``/stop`` or 👎 ends a PR's fixer story.

The ``queue.stop`` built-in (:mod:`culture_rules.node.actions.queue`) records the stop on
the PR's concurrency key, one document per key in :data:`STOPS_COLLECTION`: ``key``,
``at`` (when it was recorded), ``by`` (the login that asked), ``head_sha`` (the PR head it
was stopped at, when known) and ``rev``. Stops are monotonic: :func:`record_stop` writes by
compare-and-set and an older stop never replaces a newer one. Three readers honour it:

* ``queue.add`` drops a request of the stopped story - one whose story began (its chain's
  root run was created) no later than the stop - and an automatic request for the head the
  story was stopped at; a story begun by a rule that resets the attempt budget (a trusted
  ``/fix``, d32) or a new head is a new story, its retries included (:func:`add_refusal`).
* ``queue.progress`` drops a waiting request of the stopped story at dispatch
  (:func:`request_refusal`), so one that slipped in never runs.
* ``github.push`` refuses ``story_stopped`` for a chain whose story began no later than
  the stop (:func:`chain_stopped`), judged again after the approval is consumed, so a run
  that slipped past the cancel never pushes; a push past that last read was admitted
  before the stop.

A story's beginning is its root run (:func:`~culture_rules.node.fixer_status.chain_root`):
the run an external event started, walked back through the fixer queue. Standard-library
only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

__all__ = [
    "STOPS_COLLECTION",
    "STORY_STOPPED",
    "add_refusal",
    "chain_stopped",
    "record_stop",
    "request_refusal",
    "stop_op",
    "stop_of",
    "story_began_before",
    "story_root",
]

STOPS_COLLECTION = "story_stops"
STORY_STOPPED = "story_stopped"
STOPPED_AT_HEAD = "stopped_at_head"
_RUNS = "runs"  # culture_rules.engine.runs.RUNS_COLLECTION
_CAS_TRIES = 20


def _doc_id(key: str) -> str:
    return "stop:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


def _parse(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def record_stop(
    store: Any, key: str, *, by: str, head_sha: str | None, at: datetime
) -> Mapping[str, Any]:
    """Record the stop of ``key``'s story by compare-and-set, unless a stop at least as
    recent is stored; the stored (winning) document."""
    from culture_rules.store.port import DuplicateKeyError, TransientStoreError  # noqa: PLC0415

    doc_id = _doc_id(key)
    fields = {"key": key, "by": by, "head_sha": head_sha, "at": at.isoformat()}
    for _ in range(_CAS_TRIES):
        current = store.get(STOPS_COLLECTION, doc_id)
        if current is None:
            try:
                return store.insert(STOPS_COLLECTION, {"id": doc_id, **fields, "rev": 1})
            except DuplicateKeyError:
                continue  # another stop landed first: judge it
        stored = _parse(current.get("at"))
        if stored is not None and stored >= at:
            return current
        rev = current.get("rev")
        res = store.update_if(
            STOPS_COLLECTION, doc_id, {"rev": rev}, {**fields, "rev": (rev or 0) + 1}
        )
        if res.won:
            return res.document or {**current, **fields}
    raise TransientStoreError(f"story stop of {key}: too much contention")


def stop_op(
    store: Any,
    ik: str,
    *,
    key: str,
    by: str,
    head: Callable[[], str | None],
    at: Callable[[], datetime],
) -> Mapping[str, Any]:
    """The stop operation ``ik`` (an idempotency key): ``{key, by, head_sha, at}`` as its
    first invocation fixed them, so a replay resumes with the same cutoff and head."""
    from culture_rules.store.port import DuplicateKeyError  # noqa: PLC0415

    doc_id = "op:" + hashlib.sha256(ik.encode("utf-8")).hexdigest()[:40]
    found = store.get(STOPS_COLLECTION, doc_id)
    if found is not None:
        return found
    doc = {
        "id": doc_id,
        "kind": "op",
        "key": key,
        "by": by,
        "head_sha": head(),
        "at": at().isoformat(),
    }
    try:
        return store.insert(STOPS_COLLECTION, doc)
    except DuplicateKeyError:
        return store.get(STOPS_COLLECTION, doc_id) or doc


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
    head the story was stopped at (``stopped_at_head``) - unless its story began with a
    trusted ``/fix`` after the stop (its root's rule resets the attempt budget)."""
    stop = stop_of(store, key)
    if stop is None:
        return None
    run = store.get(_RUNS, run_id) if isinstance(run_id, str) and run_id else None
    root = _root(store, run) if run is not None else None
    if root is not None and story_began_before(stop, root.get("created_at")):
        return STORY_STOPPED
    stopped_head = stop.get("head_sha")
    if stopped_head and head_sha == stopped_head and not _explicit(root):
        return STOPPED_AT_HEAD
    return None


def request_refusal(store: Any, key: str | None, source_run: Any) -> str | None:
    """:data:`STORY_STOPPED` when the queued request put in line by ``source_run`` belongs
    to a stopped story (judged at dispatch), else None."""
    stop = stop_of(store, key)
    if stop is None or not isinstance(source_run, str) or not source_run:
        return None
    run = store.get(_RUNS, source_run)
    if run is not None and story_began_before(stop, _root(store, run).get("created_at")):
        return STORY_STOPPED
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
