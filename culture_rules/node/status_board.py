"""The board that writes the PR fixer's status comments (d26): one writer, desired state.

:mod:`culture_rules.node.fixer_status` reads a chain and renders its comment; this module
owns the comment's record in :data:`~culture_rules.node.fixer_status.STATUS_COLLECTION` and
every call to GitHub, from the node's ``status`` stage (:meth:`StatusBoard.tick`).

**A single writer.** One process writes an App actor's status comments: the holder of
the actor's **writer lease** (:data:`WRITERS_COLLECTION`, one document per actor:
``{owner: host:pid:boot-random, until}``), taken or renewed by compare-and-set at the start
of every tick and again before every GitHub call. Only a node on the actor's **current**
placed machine tries (:meth:`StatusBoard.served_actors`, read again every
:data:`SERVED_CACHE`), and a lease another process holds is never taken before it expires.
The lease (:data:`WRITER_LEASE`) outlasts a call's hard deadline (:data:`CALL_DEADLINE_S`,
enforced by a watchdog) by a margin, so a call never outlives the lease it started under;
moving the actor to another machine transfers the writer once the old lease expires.
Records are selected by actor, so a move strands none. An App actor without a machine gets
no live status comment: ``status: true`` then falls back to a plain chain-end comment.
Other writers (the API and the chain-end action through :meth:`StatusBoard.finish`) only
change the record's *inputs*; every store write is a compare-and-set on the record's
revision ``rev``, and a lost one stops the work at hand (the next tick re-reads).

**Desired state.** Each tick the writer renders the comment the store's inputs describe -
the chain's runs, the agent's notes, the pending final text - and hashes it
(``desired_rev``). When that differs from the body GitHub last acknowledged
(``acked_rev``), it edits the comment to the desired body (``PATCH``), or posts it when
there is none yet. ``acked_rev`` is recorded only on a 2xx. A failed or ambiguous call
(5xx, 408, a timeout, a network error) changes nothing and is retried with backoff; since
the one writer always sends the *current* desired body, the last write is the newest
state, and no stale edit or older final can stay on the comment. A record is done
(``final``, ``outcome: delivered``) only once the acknowledged body is the final body.

**Posting at most once.** A post is recorded as ``posting`` before it is sent. An
ambiguous answer leaves it ``posting``; the next tick lists the PR's comments, adopts the
one this App posted (``performed_via_github_app.id``) with the chain's hidden marker, and
edits it from then on - else the record gives up silently (``unresolved``) rather than
post a second comment. A post that is refused (4xx), or never sent, goes back to ``none``.

**The end.** :meth:`StatusBoard.finish` (the chain-end action, ``status: true``) stores
the text as the pending final and returns; it resolves no credential and never fails. A
chain that ends without such an action (cancelled, superseded, idle for
:data:`~culture_rules.node.fixer_status.IDLE_END`) gets an engine-worded final the same way.

**Pacing and bounds.** Writes keep :data:`~culture_rules.node.fixer_status.EDIT_FLOOR_S`
apart; a change of the notes alone waits
:data:`~culture_rules.node.fixer_status.NOTES_EVERY_S`. Failures back off exponentially
(``retry_at``, 5 s doubling to :data:`BACKOFF_CAP`); ``http_403``, ``http_422`` and an App
that cannot serve the repo give up after :data:`GIVE_UP_TRIES`. Every pending record ends:
a final undelivered :data:`FINAL_HORIZON` after it was asked, or a record with no activity
for :data:`IDLE_HORIZON`, gives up (``gave_up``). A tick makes at most :data:`MAX_CALLS`
HTTP requests - token exchanges and listing pages included, each charged before it is sent
(``GitHubApp.request_guard``) - within :data:`MAX_SECONDS`, after the drive stage; each
call, resolving the App included, is bounded by :data:`CALL_DEADLINE_S` (connect, send and
the whole read). Work the budget stops waits for the next tick without counting a failure.

**Fair selection.** A tick reads each served actor's pending records that are due
(``retry_at`` up to now), in (``retry_at``, id) order with a composite cursor, so records
sharing a timestamp are never skipped; a written or failed record moves to the back (an
unchanged one costs no request). :func:`ensure_status_indexes` declares the indexes; final
records are dropped :data:`RETENTION` after they became final. Standard-library only.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.node.fixer_status import (
    EDIT_FLOOR_S,
    IDLE_END,
    NOTES_EVERY_S,
    STATUS_COLLECTION,
    STATUS_NOTES_KEPT,
    Chain,
    Final,
    chain_root,
    digest,
    iso,
    marker_of,
    parse_time,
    past_hold,
    pr_of,
    render,
    run_status_actor,
    stage_lines,
)
from culture_rules.store.port import DuplicateKeyError

__all__ = [
    "BACKOFF_CAP",
    "BUDGET_EXHAUSTED",
    "CALL_DEADLINE_S",
    "FINAL_HORIZON",
    "GIVE_UP_TRIES",
    "IDLE_HORIZON",
    "MAX_CALLS",
    "MAX_SECONDS",
    "RETENTION",
    "WRITERS_COLLECTION",
    "WRITER_LEASE",
    "MAX_CLOCK_SKEW_S",
    "StatusBoard",
    "ensure_status_indexes",
]

log = logging.getLogger(__name__)

CALL_DEADLINE_S = 20.0
"""The hard wall-clock bound of one GitHub call: resolving the App, connect, send, full read
(a watchdog in :mod:`culture_rules.apps.github` enforces it)."""
WRITERS_COLLECTION = "fixer_status_writers"
"""One document per App actor: the process that may write its status comments."""
MAX_CLOCK_SKEW_S = 30.0
"""The clock skew between nodes the writer lease tolerates (nodes run NTP; see
docs/operations/pr-fixer.md). Beyond it a moved writer's lease may be taken while the old
writer's last call is still in flight."""
WRITER_LEASE = timedelta(seconds=120)
"""How long a writer's lease lasts: at least twice a call's hard deadline plus the skew
bound. It is renewed before every call; a holder makes no call once its clock passes
``until - MAX_CLOCK_SKEW_S - CALL_DEADLINE_S``, and another process takes it only after
``until + MAX_CLOCK_SKEW_S`` by its own clock - so with skew within the bound a call never
outlives the lease it started under, on any clock."""
SERVED_CACHE = timedelta(seconds=10)
"""How long a node trusts its reading of which App actors are placed on it."""
BACKOFF_BASE_S = 5.0
BACKOFF_CAP = timedelta(minutes=15)
"""The longest wait between two tries after failures."""
GIVE_UP_TRIES = 3
"""Tries before a permanent refusal (403, 422, repo or actor refused) gives up."""
GIVE_UP_CODES = frozenset({"http_403", "http_422", "repo_not_allowed", "actor_not_found"})
FINAL_HORIZON = timedelta(hours=24)
"""How long a pending final is tried before the record gives up."""
IDLE_HORIZON = timedelta(days=7)
"""A pending record with no activity this long gives up."""
MAX_CALLS = 10
"""HTTP requests one tick may make."""
MAX_SECONDS = 10.0
"""Seconds one tick may spend on GitHub calls."""
RETENTION = timedelta(days=30)
"""Final records are dropped this long after they became final."""
RETENTION_EVERY = timedelta(hours=1)
QUERY_LIMIT = 200
"""Records read per page."""
BUDGET_EXHAUSTED = "budget_exhausted"
"""The tick's request budget is spent: not a failure, the work waits for the next tick."""
WRITER_LOST = "writer_lost"
"""This process no longer holds the actor's writer lease: it stops, nothing is counted."""
TRANSPORT_BUSY = "transport_busy"
"""Every watchdog worker is busy: the request was not started."""
_WAITS = frozenset({BUDGET_EXHAUSTED, WRITER_LOST, TRANSPORT_BUSY})
"""Refusals before any request was sent that are no failure: the work waits a tick."""
_UNSENT = _WAITS | frozenset({"repo_not_allowed", "bad_input"})
"""Refusals before any request was sent (the App checks them before the network)."""

_RUNS, _BRIDGE, _ACTORS, _ACTIVE = "runs", "bridge_invocations", "actors", "running"
_ID, _REV, _STATE, _FINAL, _PENDING = "id", "rev", "state", "final", "pending"
_ACTOR, _MACHINE, _REPO, _NUMBER, _KEY = "actor", "machine", "repo", "number", "concurrency_key"
_COMMENT_ID, _URL, _CREATED_AT, _FINAL_AT = "comment_id", "url", "created_at", "final_at"
_FINAL_TEXT, _FINAL_RUN, _FINAL_ASKED = "final_text", "final_run", "final_requested_at"
_FAILURES, _RETRY_AT, _OUTCOME = "failures", "retry_at", "outcome"
_ACKED_REV, _ACKED_AT, _ACKED_STAGES = "acked_rev", "acked_at", "acked_stage_sig"
_DESIRED_REV = "desired_rev"
NONE, POSTING, POSTED, UNRESOLVED = "none", "posting", "posted", "unresolved"
_FINAL_TEXT_CAP = 20_000


def ensure_status_indexes(store: Any) -> None:
    """The indexes of the board's queries, where the store has indexes (MongoDB): a
    machine's due records, final records by date, a key's runs by date, a run's bridge
    invocations."""
    ensure = getattr(store, "ensure_index", None)
    if not callable(ensure):
        return
    ensure(
        STATUS_COLLECTION,
        [(_PENDING, 1), (_ACTOR, 1), (_RETRY_AT, 1), (_ID, 1)],
        name="status_due",
    )
    ensure(STATUS_COLLECTION, [(_FINAL, 1), (_FINAL_AT, 1), (_ID, 1)], name="status_final")
    ensure(_RUNS, [(_KEY, 1), (_CREATED_AT, 1), (_ID, 1)], name="runs_key_created")
    ensure(_BRIDGE, [("run_id", 1)], name="bridge_by_run")


def _refused(code: str) -> bool:
    """GitHub refused the call (4xx but 408): it did nothing."""
    return code.startswith("http_4") and code != "http_408"


@dataclass(frozen=True)
class _Failure:
    """Why a call failed (the GitHub error's code and whether a retry may help)."""

    code: str
    retryable: bool
    sent: bool = True
    """False when no request reached GitHub (the App not resolved, the budget spent)."""


class _Budget:
    """At most ``calls`` HTTP requests (token exchanges included) within ``seconds`` (a
    monotonic clock); :meth:`charge` is the App's request guard."""

    def __init__(self, calls: int, seconds: float, monotonic: Callable[[], float]) -> None:
        self._calls = calls
        self._monotonic = monotonic
        self._end = monotonic() + seconds

    @property
    def spent(self) -> bool:
        return self._calls <= 0 or self._monotonic() >= self._end

    def charge(self) -> None:
        """Count one request, or refuse it (``budget_exhausted``) before it is sent."""
        from culture_rules.apps.github import GitHubError  # noqa: PLC0415

        if self.spent:
            raise GitHubError(BUDGET_EXHAUSTED, retryable=True)
        self._calls -= 1

    def left_s(self) -> float:
        return max(self._end - self._monotonic(), 0.5)


@dataclass(frozen=True)
class _Ctx:
    """One tick's App lookup, request budget and host."""

    apps: Callable[[str, str, datetime], Any]
    budget: _Budget
    host: str


@dataclass(frozen=True)
class _Desired:
    """The body the store's inputs describe now, and what it is made of."""

    body: str
    rev: str
    stages: str
    final: bool


class StatusBoard:
    """Writes the status comments (module doc). One per process; its lock serialises the
    tick (the single writer) and :meth:`finish`."""

    def __init__(
        self,
        store: Any,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        known: Callable[[], Iterable[str]] | None = None,
        max_calls: int = MAX_CALLS,
        max_seconds: float = MAX_SECONDS,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._known_fn = known
        self._max_calls = max_calls
        self._max_seconds = max_seconds
        self._lock = threading.RLock()
        self._roots: dict[str, str] = {}
        self._retained_at: datetime | None = None
        self._boot = secrets.token_hex(6)
        self._served: tuple[datetime, str, list[str]] | None = None

    def _known(self) -> frozenset[str]:
        if self._known_fn is not None:
            return frozenset(self._known_fn())
        from culture_rules.actors.secrets import known_values  # noqa: PLC0415

        return known_values()

    # ------------------------------------------------------------------ reading

    def root_of(self, run: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """:func:`chain_root`, remembered per run (a run's root never changes)."""
        rid = run.get(_ID)
        known = self._roots.get(rid) if isinstance(rid, str) else None
        if known is not None:
            return self._store.get(_RUNS, known)
        root = chain_root(self._store, run)
        if root is not None and isinstance(rid, str):
            if len(self._roots) > 10_000:
                self._roots.clear()
            self._roots[rid] = str(root.get(_ID))
        return root

    def chain(self, root: Mapping[str, Any]) -> Chain:
        """The chain of ``root``: the runs on its key since it, whose root it is."""
        key = root.get(_KEY)
        since = str(root.get(_CREATED_AT) or "")
        found = [root]
        start = parse_time(since)
        if key and start is not None:
            after = (start - timedelta(seconds=1)).astimezone(UTC).isoformat()
            found += self._store.find_range(
                _RUNS, {_KEY: key}, field=_CREATED_AT, after=after, limit=QUERY_LIMIT
            )
        runs = {
            str(r.get(_ID)): r
            for r in found
            if str(r.get(_CREATED_AT) or "") >= since and self._roots_to(r, root)
        }
        ordered = sorted(runs.values(), key=lambda r: (str(r.get(_CREATED_AT)), str(r.get(_ID))))
        chain = Chain(root=root, runs=ordered or [root])
        self._notes(chain)
        return chain

    def _roots_to(self, run: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
        if run.get(_ID) == root.get(_ID):
            return True
        found = self.root_of(run)
        return found is not None and found.get(_ID) == root.get(_ID)

    def _notes(self, chain: Chain) -> None:
        """The latest fix run's agent notes and last activity (its bridge invocations)."""
        fix = chain.fixes[-1] if chain.fixes else None
        if fix is None:
            return
        docs = self._store.find(_BRIDGE, {"run_id": fix.get(_ID)})
        docs.sort(key=lambda d: (d.get("attempt") or 0, str(d.get(_CREATED_AT) or "")))
        notes = [n for d in docs for n in d.get("status_notes") or () if isinstance(n, Mapping)]
        chain.notes = notes[-STATUS_NOTES_KEPT:]
        times = [d.get("last_event_at") for d in docs if isinstance(d.get("last_event_at"), str)]
        chain.last_activity = max(times) if times else None

    def machine_of(self, actor: str) -> str | None:
        """The machine the App actor is placed on (its single writer), else None."""
        doc = self._store.get(_ACTORS, actor)
        if not doc or doc.get("deleted_at") or doc.get("enabled") is False:
            return None
        machine = doc.get(_MACHINE)
        return machine if isinstance(machine, str) and machine else None

    # ------------------------------------------------------------------ the record

    def _save(self, doc: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any] | None:
        """Compare-and-set ``changes`` on the record's revision: the new record, or None when
        another writer (the API, :meth:`finish`) moved it first; the caller stops."""
        rev = doc.get(_REV, 0)
        res = self._store.update_if(
            STATUS_COLLECTION, doc[_ID], {_REV: rev}, {**changes, _REV: rev + 1}
        )
        return dict(res.document) if res.won and res.document is not None else None

    def _claim(self, root: Mapping[str, Any], target: _Target) -> dict[str, Any] | None:
        now = iso(self._clock())
        doc = {
            _ID: str(root.get(_ID)),
            _REPO: target.repo,
            _NUMBER: target.number,
            _ACTOR: target.actor,
            _MACHINE: target.machine,
            _KEY: root.get(_KEY),
            _STATE: NONE,
            _FINAL: False,
            _PENDING: True,
            _COMMENT_ID: None,
            _URL: None,
            _ACKED_REV: None,
            _REV: 0,
            _FAILURES: 0,
            _RETRY_AT: now,
            _CREATED_AT: now,
        }
        try:
            return self._store.insert(STATUS_COLLECTION, doc)
        except DuplicateKeyError:
            return None

    def _target(self, root: Mapping[str, Any] | None, actor: str | None) -> _Target | None:
        machine = self.machine_of(actor) if actor else None
        found = pr_of(root) if root is not None and machine else None
        return _Target(str(actor), machine, found[0], found[1]) if found and machine else None

    # ------------------------------------------------------------------ the tick

    def tick(self, apps: Callable[[str, str, datetime], Any], host: str) -> int:
        """The single writer's pass on ``host`` (module doc): for each App actor placed on
        this machine whose writer lease this process holds, claim the records of chains
        whose first run is past its hold, then reconcile the actor's due records within the
        tick's budget; return how many writes GitHub acknowledged. ``apps(actor_id, repo,
        deadline)`` resolves the App within the deadline."""
        ctx = _Ctx(apps, _Budget(self._max_calls, self._max_seconds, self._monotonic), host)
        done = 0
        with self._lock:
            actors = [a for a in self.served_actors(host) if self._hold(a, host) is not None]
            for run in self._store.find(_RUNS, {"status": _ACTIVE}):
                self._start(actors, run)
            for actor in actors:
                done += self._work(ctx, actor)
            self._retain()
        return done

    def _work(self, ctx: _Ctx, actor: str) -> int:
        done = 0
        for doc in self._due(actor):
            if ctx.budget.spent:
                break
            done += self._reconcile(ctx, doc)
        return done

    def served_actors(self, host: str) -> list[str]:
        """The App actors (surface ``github``) placed on ``host`` now; read again after
        :data:`SERVED_CACHE`, so a moved actor is dropped within seconds."""
        now = self._clock()
        cached = self._served
        if cached is not None and cached[1] == host and now - cached[0] < SERVED_CACHE:
            return cached[2]
        served = sorted(
            str(doc[_ID])
            for doc in self._store.find(_ACTORS, {_MACHINE: host})
            if (doc.get("params") or {}).get("surface") == "github"
            and not doc.get("deleted_at")
            and doc.get("enabled") is not False
        )
        self._served = (now, host, served)
        return served

    def _owner(self, host: str) -> str:
        return f"{host}:{os.getpid()}:{self._boot}"

    def _hold(self, actor: str, host: str) -> datetime | None:
        """Take or renew this process's writer lease on ``actor`` by compare-and-set; the
        new ``until``, or None. Another process's lease is taken only once it is over
        everywhere: ``until + MAX_CLOCK_SKEW_S`` by this clock."""
        now = self._clock()
        owner = self._owner(host)
        until = now + WRITER_LEASE
        lease = {"owner": owner, "until": iso(until)}
        doc = self._store.get(WRITERS_COLLECTION, actor)
        if doc is None:
            try:
                self._store.insert(WRITERS_COLLECTION, {_ID: actor, _REV: 0, **lease})
            except DuplicateKeyError:
                return None
            return until
        expires = parse_time(doc.get("until"))
        skew = timedelta(seconds=MAX_CLOCK_SKEW_S)
        if doc.get("owner") != owner and expires is not None and now <= expires + skew:
            return None
        rev = doc.get(_REV, 0)
        res = self._store.update_if(
            WRITERS_COLLECTION, actor, {_REV: rev}, {**lease, _REV: rev + 1}
        )
        return until if res.won else None

    def _may_call(self, actor: str, host: str) -> bool:
        """Renew the lease, then start a call only with time for it on any clock: this
        clock before ``until - MAX_CLOCK_SKEW_S - CALL_DEADLINE_S``."""
        until = self._hold(actor, host)
        margin = timedelta(seconds=MAX_CLOCK_SKEW_S + CALL_DEADLINE_S)
        return until is not None and self._clock() <= until - margin

    def _due(self, actor: str) -> Iterator[dict[str, Any]]:
        """The actor's pending records whose ``retry_at`` has come, in (``retry_at``, id)
        order, paged with a composite cursor so records sharing a timestamp are never
        skipped (each is re-read when it is worked)."""
        where = {_PENDING: True, _ACTOR: actor}
        now = iso(self._clock())
        at: str | None = None
        last: str | None = None
        while True:
            if at is not None:
                ties = self._store.find_range(
                    STATUS_COLLECTION,
                    {**where, _RETRY_AT: at},
                    field=_ID,
                    after=last,
                    limit=QUERY_LIMIT,
                )
                yield from ties
                if len(ties) == QUERY_LIMIT:
                    last = ties[-1][_ID]
                    continue
            page = self._store.find_range(
                STATUS_COLLECTION, where, field=_RETRY_AT, upto=now, after=at, limit=QUERY_LIMIT
            )
            yield from page
            if len(page) < QUERY_LIMIT:
                return
            at, last = page[-1][_RETRY_AT], page[-1][_ID]

    def _start(self, actors: list[str], run: Mapping[str, Any]) -> None:
        actor = run_status_actor(run)
        if actor is None or actor not in actors or not past_hold(run):
            return
        root = self.root_of(run)
        if root is None or self._store.get(STATUS_COLLECTION, str(root.get(_ID))):
            return
        target = self._target(root, actor)
        if target is not None:
            self._claim(root, target)

    def _reconcile(self, ctx: _Ctx, doc: dict[str, Any]) -> int:
        """Bring one record's comment to its desired state (module doc)."""
        doc = self._store.get(STATUS_COLLECTION, doc[_ID]) or doc  # the freshest inputs
        if not doc.get(_PENDING):
            return 0
        expired = self._expired(doc)
        if expired:
            log.warning("status comment of chain %s: %s", doc[_ID], expired)
            self._end(doc, "gave_up")
            return 0
        if doc.get(_STATE) == POSTING:
            return self._resolve(ctx, doc)
        root = self._store.get(_RUNS, doc[_ID])
        if root is None:
            self._end(doc, "gone")
            return 0
        chain = self.chain(root)
        doc = self._closing(doc, chain)
        if doc is None:
            return 0
        desired = self._desired(doc, chain)
        if doc.get(_ACKED_REV) == desired.rev:
            if desired.final:
                self._end(doc, "delivered")
            return 0
        if not self._write_due(doc, desired):
            return 0
        return self._write(ctx, doc, desired)

    def _expired(self, doc: Mapping[str, Any]) -> str | None:
        """Why a pending record must give up: its final undelivered past
        :data:`FINAL_HORIZON`, or no activity for :data:`IDLE_HORIZON`."""
        now = self._clock()
        asked = parse_time(doc.get(_FINAL_ASKED))
        if asked is not None and now - asked >= FINAL_HORIZON:
            return "the final was not delivered in time"
        moments = [parse_time(doc.get(k)) for k in (_CREATED_AT, _ACKED_AT, _FINAL_ASKED)]
        latest = max((m for m in moments if m is not None), default=None)
        if latest is not None and now - latest >= IDLE_HORIZON:
            return "idle past its horizon"
        return None

    def _closing(self, doc: dict[str, Any], chain: Chain) -> dict[str, Any] | None:
        """``doc`` with an engine-worded pending final when the chain ended without a
        chain-end action (cancelled, superseded, or idle); None on a lost write."""
        if doc.get(_FINAL_TEXT) is not None or not chain.ended:
            return doc
        last = chain.runs[-1]
        status = last.get("status")
        text = None
        if status == "cancelled":
            text = "PR fixer stopped: the run was cancelled."
        elif status == "superseded":
            text = "PR fixer stopped: the PR head moved; a new run takes over."
        else:
            finished = parse_time(last.get("finished_at"))
            if finished is not None and self._clock() - finished >= IDLE_END:
                text = "PR fixer: the chain ended."
        if text is None:
            return doc
        changes = {_FINAL_TEXT: text, _FINAL_RUN: last.get(_ID), _FINAL_ASKED: iso(self._clock())}
        return self._save(doc, changes)

    def _desired(self, doc: Mapping[str, Any], chain: Chain) -> _Desired:
        text = doc.get(_FINAL_TEXT)
        final = Final(str(text), doc.get(_FINAL_RUN)) if text is not None else None
        body = render(chain, final, known=self._known())
        stages = digest("\n".join(stage_lines(chain)))
        return _Desired(body, digest(body), stages, final is not None)

    def _write_due(self, doc: Mapping[str, Any], desired: _Desired) -> bool:
        """At most one write per :data:`EDIT_FLOOR_S`; a change of the notes alone waits
        :data:`NOTES_EVERY_S`."""
        acked = parse_time(doc.get(_ACKED_AT))
        since = (self._clock() - acked).total_seconds() if acked else float("inf")
        if since < EDIT_FLOOR_S:
            return False
        notes_only = not desired.final and doc.get(_ACKED_STAGES) == desired.stages
        return not notes_only or since >= NOTES_EVERY_S

    def _end(self, doc: Mapping[str, Any], outcome: str) -> None:
        now = iso(self._clock())
        self._save(doc, {_FINAL: True, _FINAL_AT: now, _OUTCOME: outcome, _PENDING: False})

    # ------------------------------------------------------------------ GitHub

    def _write(self, ctx: _Ctx, doc: dict[str, Any], desired: _Desired) -> int:
        """Send the desired body: edit the comment, or post it when there is none."""
        if doc.get(_STATE) == POSTED and doc.get(_COMMENT_ID):
            return self._patch(ctx, doc, desired)
        return self._post(ctx, doc, desired)

    def _call(self, ctx: _Ctx, doc: Mapping[str, Any], op: Callable[[Any], Any]) -> Any:
        """One GitHub operation as the record's App, the App resolved and every request
        made within :data:`CALL_DEADLINE_S` (and the tick's time left), each request
        charged to the budget before it is sent: the answer, or a :class:`_Failure`."""
        from culture_rules.apps.github import GitHubError  # noqa: PLC0415

        if ctx.budget.spent:
            return _Failure(BUDGET_EXHAUSTED, True, sent=False)
        if not self._may_call(str(doc.get(_ACTOR)), ctx.host):
            return _Failure(WRITER_LOST, True, sent=False)
        until = self._clock() + timedelta(seconds=min(CALL_DEADLINE_S, ctx.budget.left_s()))
        try:
            app = ctx.apps(str(doc.get(_ACTOR)), str(doc.get(_REPO)), until)
        except GitHubError as exc:  # resolving the App: nothing was sent
            return _Failure(exc.code, exc.retryable, sent=False)
        try:
            with _bounded(app, until, ctx.budget):
                return op(app)
        except GitHubError as exc:
            return _Failure(exc.code, exc.retryable, sent=exc.code not in _UNSENT)

    def _acked(self, desired: _Desired) -> dict[str, Any]:
        """The changes of an acknowledged write: the body GitHub has now."""
        now = self._clock()
        changes = {
            _ACKED_REV: desired.rev,
            _DESIRED_REV: desired.rev,
            _ACKED_STAGES: desired.stages,
            _ACKED_AT: iso(now),
            _FAILURES: 0,
            _RETRY_AT: iso(now + timedelta(seconds=EDIT_FLOOR_S)),
        }
        if desired.final:
            changes.update({_FINAL: True, _FINAL_AT: iso(now), _OUTCOME: "delivered"})
            changes[_PENDING] = False
        return changes

    def _post(self, ctx: _Ctx, doc: dict[str, Any], desired: _Desired) -> int:
        doc = self._save(doc, {_STATE: POSTING, _DESIRED_REV: desired.rev})
        if doc is None:
            return 0

        def post(app: Any) -> Any:
            return app.post_comment(doc[_REPO], doc[_NUMBER], desired.body)

        out = self._call(ctx, doc, post)
        if isinstance(out, _Failure):
            self._post_failed(doc, out)
            return 0
        found = {_STATE: POSTED, _COMMENT_ID: out.get(_COMMENT_ID), _URL: out.get(_URL)}
        self._save(doc, {**found, **self._acked(desired)})
        return 1

    def _post_failed(self, doc: dict[str, Any], failure: _Failure) -> None:
        """Never sent, or refused (4xx): no comment exists, ``none``; ambiguous: the post
        may have landed, it stays ``posting`` and is resolved next tick."""
        if failure.code in _WAITS:
            self._save(doc, {_STATE: NONE})
            return
        log.warning("status comment of chain %s not posted: %s", doc[_ID], failure.code)
        landed = failure.sent and not _refused(failure.code)
        self._fail(doc, failure, {_STATE: POSTING if landed else NONE})

    def _patch(self, ctx: _Ctx, doc: dict[str, Any], desired: _Desired) -> int:
        def edit(app: Any) -> Any:
            return app.update_issue_comment(doc[_REPO], doc[_COMMENT_ID], desired.body)

        out = self._call(ctx, doc, edit)
        if isinstance(out, _Failure) and out.code in _WAITS:
            return 0
        if isinstance(out, _Failure) and out.code == "http_404":
            log.info("status comment of chain %s was deleted: posting it again", doc[_ID])
            fresh = self._save(doc, {_STATE: NONE, _COMMENT_ID: None, _ACKED_REV: None})
            return self._post(ctx, fresh, desired) if fresh else 0
        if isinstance(out, _Failure):  # acked_rev unchanged: the next try sends what is due
            log.warning("status comment of chain %s not edited: %s", doc[_ID], out.code)
            self._fail(doc, out, {_DESIRED_REV: desired.rev})
            return 0
        self._save(doc, self._acked(desired))
        return 1

    def _fail(self, doc: Mapping[str, Any], failure: _Failure, changes: Mapping) -> None:
        """Back off exponentially (to the back of the queue); a permanent refusal gives up
        after :data:`GIVE_UP_TRIES`."""
        tries = int(doc.get(_FAILURES) or 0) + 1
        wait = min(BACKOFF_BASE_S * 2 ** (tries - 1), BACKOFF_CAP.total_seconds())
        now = self._clock()
        out = {
            **changes,
            _FAILURES: tries,
            "last_error": failure.code,
            _RETRY_AT: iso(now + timedelta(seconds=wait)),
        }
        if failure.code in GIVE_UP_CODES and tries >= GIVE_UP_TRIES:
            out.update({_FINAL: True, _FINAL_AT: iso(now), _OUTCOME: "gave_up", _PENDING: False})
        self._save(doc, out)

    def _resolve(self, ctx: _Ctx, doc: dict[str, Any]) -> int:
        """A post whose answer was lost: adopt the comment this App posted with the chain's
        marker (it is edited to the desired body next), else give up silently."""

        def listing(app: Any) -> Any:
            return app.list_issue_comments(doc[_REPO], doc[_NUMBER]), app.app_id

        out = self._call(ctx, doc, listing)
        if isinstance(out, _Failure) and out.code in _WAITS:
            return 0
        if isinstance(out, _Failure):
            self._fail(doc, out, {})
            return 0
        comments, app_id = out
        marker = marker_of(doc[_ID])
        mine = [c for c in comments if c.get("app_id") == app_id and marker in c.get("body", "")]
        if mine:
            found = {_COMMENT_ID: mine[0].get(_COMMENT_ID), _URL: mine[0].get(_URL)}
            self._save(doc, {_STATE: POSTED, **found, _ACKED_REV: None, _FAILURES: 0})
            return 0
        log.info("status comment of chain %s not found after a lost post: giving up", doc[_ID])
        now = iso(self._clock())
        ended = {_FINAL: True, _FINAL_AT: now, _OUTCOME: UNRESOLVED, _PENDING: False}
        self._save(doc, {_STATE: UNRESOLVED, **ended})
        return 0

    def _retain(self) -> None:
        """Drop final records older than :data:`RETENTION` (at most hourly)."""
        now = self._clock()
        if self._retained_at is not None and now - self._retained_at < RETENTION_EVERY:
            return
        self._retained_at = now
        old = self._store.find_range(
            STATUS_COLLECTION,
            {_FINAL: True},
            field=_FINAL_AT,
            upto=iso(now - RETENTION),
            limit=QUERY_LIMIT,
        )
        for doc in old:
            self._store.delete(STATUS_COLLECTION, doc[_ID])

    # ------------------------------------------------------------------ the end

    def finish(
        self,
        run: Mapping[str, Any] | None,
        text: str,
        *,
        where: tuple[str, int] | None = None,
    ) -> dict[str, Any] | None:
        """Store ``text`` (the chain-end action's body) as the pending final of ``run``'s
        chain's status comment; the single writer delivers it. Resolves no credential,
        calls no network, never fails. ``None`` when ``run`` is not in an opted-in chain on
        PR ``where``, or its App actor has no machine (the caller posts a plain comment)."""
        actor = run_status_actor(run)
        root = self.root_of(run) if run is not None and actor else None
        target = self._target(root, actor)
        if target is None or not _same_pr(target, where):
            return None
        now = iso(self._clock())
        changes = {
            _FINAL_TEXT: str(text)[:_FINAL_TEXT_CAP],
            _FINAL_RUN: run.get(_ID),
            _FINAL_ASKED: now,
            _FAILURES: 0,
            _RETRY_AT: now,
        }
        root_id = str(root.get(_ID))
        with self._lock:
            for _ in range(5):
                doc = self._store.get(STATUS_COLLECTION, root_id) or self._claim(root, target)
                if doc is None:
                    continue  # claimed meanwhile: read it
                if doc.get(_FINAL):
                    return {"status": True, "pending": False, _OUTCOME: doc.get(_OUTCOME)}
                if self._save(doc, changes) is not None:
                    return {"status": True, "pending": True}
        log.warning("status comment of chain %s: the final text was not stored", root_id)
        return {"status": True, "pending": False}


@contextlib.contextmanager
def _bounded(app: Any, until: datetime, budget: _Budget) -> Iterator[None]:
    """Every request of the block within ``until`` - under the App's watchdog, a hard bound -
    and charged to ``budget`` (an App without those hooks - a test double - is charged
    once)."""
    with contextlib.ExitStack() as stack:
        limit = getattr(app, "deadline", None)
        if callable(limit):
            stack.enter_context(limit(until))
        watchdog = getattr(app, "watchdog", None)
        if callable(watchdog):
            stack.enter_context(watchdog())
        guard = getattr(app, "request_guard", None)
        if callable(guard):
            stack.enter_context(guard(budget.charge))
        else:
            budget.charge()
        yield


@dataclass(frozen=True)
class _Target:
    """Where a chain's comment lives and who writes it: the App actor, its machine, the
    repository and the PR."""

    actor: str
    machine: str
    repo: str
    number: int


def _same_pr(target: _Target, where: tuple[str, int] | None) -> bool:
    if where is None:
        return True
    repo, number = where
    return target.repo.lower() == str(repo).lower() and target.number == number
