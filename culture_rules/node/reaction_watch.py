"""Reaction watch (d34, #40): a 👎 on a fixer story's comments stops the story.

GitHub sends no webhook for reactions, so a thumbs-down on the ``/fix`` comment that
started a story, or on the fixer's status comment, produces no event. While a story is
live, :meth:`ReactionWatcher.tick` (run from the node cycle) therefore reads those two
comments' 👎 reactions at most once every ``reaction_watch_interval_s`` seconds (a
variable; default :data:`DEFAULT_INTERVAL_S`) and emits one
:data:`~culture_rules.events.emit.REACTION_ADDED_TYPE` event per reaction. The
``pr-fixer-stop-reaction`` rule decides whose 👎 counts (``vars.trusted_authors``).

**Live stories.** A story is live while its request waits in (or was dispatched by) a fixer
queue (:data:`~culture_rules.node.actions.queue.QUEUES_COLLECTION`: the entry's
``source_run``) or while its status comment is not final
(:data:`~culture_rules.node.fixer_status.STATUS_COLLECTION`, keyed by the story's root
run). A story's root (:func:`~culture_rules.node.fixer_status.chain_root`) started by an
issue comment (``github.comment.created`` with a ``comment_id``) watches that comment; a
posted status comment is watched too. Review comments live under another endpoint and are
not read. With no live story, no request is made.

**Bounds.** A sweep queues each watched comment once; reads share one budget per node
cycle - at most :data:`REQUESTS_PER_TICK` requests within :data:`TICK_BUDGET_S` seconds,
each bounded by the time left (at most :data:`REQUEST_TIMEOUT_S`) - and the rest carries
over to the next cycle; a new sweep starts only once the last one is done. A failed read
skips that comment until the next sweep.

**Exactly once.** The event id is a hash of ``repo:comment_id:reaction_id``
(:func:`reaction_event_id`), so the unique id of the ``events`` collection emits each
reaction once, across nodes and sweeps. The type and the ``reaction_`` id prefix are
reserved at external ingest (:func:`~culture_rules.events.emit.reserved_reason`).

Event data: ``repository``, ``number``, ``comment_id``, ``comment`` (``fix`` or
``status``), ``content`` (``-1``), ``author`` (the reacting login), ``reaction_id``,
``story`` (the root run id) and ``head_sha`` (the story's request head, when known).
Read-only towards GitHub. Standard-library only.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culture_rules.apps.github import GitHubError
from culture_rules.events.emit import REACTION_ADDED_TYPE, REACTION_ID_PREFIX, derive_envelope
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.store.port import DuplicateKeyError, StoragePort

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_INTERVAL_S",
    "INTERVAL_VARIABLE",
    "REACTION_HOST",
    "REQUESTS_PER_TICK",
    "REQUEST_TIMEOUT_S",
    "SOURCE",
    "TICK_BUDGET_S",
    "ReactionWatcher",
    "reaction_event_id",
]

DEFAULT_INTERVAL_S = 60.0
REQUESTS_PER_TICK = 10
"""The most GitHub requests (one per watched comment) one node cycle makes."""
TICK_BUDGET_S = 10.0
"""The longest one cycle's requests may run; the rest wait for the next cycle."""
REQUEST_TIMEOUT_S = 5.0
"""The bound on one request, secret resolve included (less when the budget is nearly spent)."""
INTERVAL_VARIABLE = "reaction_watch_interval_s"
REACTION_HOST = "reaction-watch"
SOURCE = "culture-rules://reaction-watch"
THUMBS_DOWN = "-1"
_FIX_COMMENT_TYPE = "github.comment.created"
_QUEUES = "queues"  # culture_rules.node.actions.queue.QUEUES_COLLECTION
_STATUS = "fixer_status_comments"  # culture_rules.node.fixer_status.STATUS_COLLECTION
_RUNS = "runs"


def reaction_event_id(repo: str, comment_id: int, reaction_id: Any) -> str:
    """The deterministic id of one reaction's event."""
    key = f"{repo.lower()}:{int(comment_id)}:{reaction_id}"
    return REACTION_ID_PREFIX + hashlib.sha256(key.encode()).hexdigest()[:40]


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True)
class _Watched:
    """One comment to read: where it is, which of the story's comments, and the story."""

    repo: str
    number: int
    comment_id: int
    comment: str
    story: str
    head_sha: str | None


class ReactionWatcher:
    """See the module docstring. ``list_reactions(repo, comment_id, timeout_s)`` reads the
    👎 reactions on one issue comment through the repo's App, bounded by ``timeout_s``."""

    def __init__(
        self,
        store: StoragePort,
        list_reactions: Callable[[str, int, float], list[Mapping[str, Any]]],
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._list = list_reactions
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._last: datetime | None = None
        self._queue: deque[_Watched] = deque()

    def interval_s(self) -> float:
        doc = self._store.get_variable(INTERVAL_VARIABLE)
        value = None if doc is None else doc.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return DEFAULT_INTERVAL_S
        return float(value)

    def tick(self) -> int:
        """Start a sweep when one is due and the last is done, then spend this cycle's
        budget on it; returns the events emitted."""
        now = self._clock()
        due = self._last is None or (now - self._last).total_seconds() >= self.interval_s()
        if not self._queue and due:
            self._last = now
            self._queue.extend(self._watched())
        return self._spend(now)

    # ------------------------------------------------------------------ live stories

    def _watched(self) -> list[_Watched]:
        out: dict[int, _Watched] = {}
        for root, head in self._live_roots():
            for item in self._comments_of(root, head):
                out.setdefault(item.comment_id, item)
        return list(out.values())

    def _live_roots(self) -> Iterator[tuple[Mapping[str, Any], str | None]]:
        """Each live story's root run once, with its request head when queued."""
        seen: set[Any] = set()
        yield from self._queued_roots(seen)
        yield from self._unfinished_roots(seen)

    def _queued_roots(self, seen: set[Any]) -> Iterator[tuple[Mapping[str, Any], str | None]]:
        """The root run of each waiting or active queue request, by its source run."""
        for req in self._queued_requests():
            root = self._root_of(req)
            if root is None or root.get("id") in seen:
                continue
            seen.add(root.get("id"))
            head = req.get("head_sha")
            yield root, head if isinstance(head, str) else None

    def _queued_requests(self) -> Iterator[Mapping[str, Any]]:
        """Every waiting or active request of every queue (an active entry wraps its own)."""
        for queue in self._store.find(_QUEUES):
            for entry in (*(queue.get("waiting") or ()), *(queue.get("active") or ())):
                yield entry.get("request") if isinstance(entry.get("request"), Mapping) else entry

    def _root_of(self, req: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """The root run of the chain ``req`` came from, or ``None``."""
        from culture_rules.node.fixer_status import chain_root  # noqa: PLC0415

        source = self._store.get(_RUNS, req.get("source_run") or "")
        return chain_root(self._store, source) if source is not None else None

    def _unfinished_roots(self, seen: set[Any]) -> Iterator[tuple[Mapping[str, Any], str | None]]:
        """The root run of each status comment not yet final, not already seen queued."""
        for record in self._store.find(_STATUS, {"final": False}):
            if record.get("id") in seen:
                continue
            root = self._store.get(_RUNS, record.get("id") or "")
            if root is not None:
                seen.add(record.get("id"))
                yield {**root, "_status": record}, None

    def _comments_of(self, root: Mapping[str, Any], head: str | None) -> list[_Watched]:
        trigger = root.get("trigger") if isinstance(root.get("trigger"), Mapping) else {}
        data = trigger.get("data") if isinstance(trigger.get("data"), Mapping) else {}
        repo, number = data.get("repository"), data.get("number")
        if not isinstance(repo, str) or "/" not in repo or not _positive_int(number):
            return []
        head = head or (data.get("head_sha") if isinstance(data.get("head_sha"), str) else None)
        story = str(root.get("id"))
        out: list[_Watched] = []
        if trigger.get("type") == _FIX_COMMENT_TYPE and _positive_int(data.get("comment_id")):
            out.append(_Watched(repo, number, data["comment_id"], "fix", story, head))
        record = root.get("_status") or self._store.get(_STATUS, story) or {}
        if not record.get("final") and _positive_int(record.get("comment_id")):
            out.append(_Watched(repo, number, record["comment_id"], "status", story, head))
        return out

    # ------------------------------------------------------------------ reading

    def _spend(self, now: datetime) -> int:
        started = self._monotonic()
        emitted = requests = 0
        while self._queue and requests < REQUESTS_PER_TICK:
            left = TICK_BUDGET_S - (self._monotonic() - started)
            if left <= 0:
                break
            item = self._queue.popleft()
            requests += 1
            emitted += self._read(item, min(REQUEST_TIMEOUT_S, left), now)
        return emitted

    def _read(self, item: _Watched, timeout: float, now: datetime) -> int:
        try:
            reactions = self._list(item.repo, item.comment_id, timeout)
        except GitHubError as exc:
            level = logging.DEBUG if exc.code == "repo_not_allowed" else logging.INFO
            log.log(
                level,
                "reaction watch: reading %s comment %s failed (%s)",
                item.repo,
                item.comment_id,
                exc.code,
            )
            return 0
        return sum(self._emit(item, r, now) for r in reactions if isinstance(r, Mapping))

    def _emit(self, item: _Watched, reaction: Mapping[str, Any], now: datetime) -> int:
        user = reaction.get("user") if isinstance(reaction.get("user"), Mapping) else {}
        login, rid = user.get("login"), reaction.get("id")
        if reaction.get("content") != THUMBS_DOWN or not isinstance(login, str) or not login:
            return 0
        if not _positive_int(rid):
            return 0
        payload = {
            "repository": item.repo,
            "number": item.number,
            "comment_id": item.comment_id,
            "comment": item.comment,
            "content": THUMBS_DOWN,
            "author": login,
            "reaction_id": rid,
            "story": item.story,
            "head_sha": item.head_sha,
        }
        envelope = derive_envelope(
            None,
            type=REACTION_ADDED_TYPE,
            source=SOURCE,
            data=payload,
            id=reaction_event_id(item.repo, item.comment_id, rid),
        )
        try:
            self._store.insert(
                EVENTS_COLLECTION, event_document(envelope, host=REACTION_HOST, received_at=now)
            )
        except DuplicateKeyError:
            return 0
        log.info(
            "reaction watch: a thumbs-down on %s#%s (%s comment)",
            item.repo,
            item.number,
            item.comment,
        )
        return 1
