"""The board that posts and edits the PR fixer's status comments (d26).

:mod:`culture_rules.node.fixer_status` reads a chain and renders its comment; this module
owns the comment's record in :data:`~culture_rules.node.fixer_status.STATUS_COLLECTION` and
every call to GitHub, from the node's ``status`` stage (:meth:`StatusBoard.tick`).

**One record per chain, one writer at a time.** The record (keyed by the chain's root run)
carries a revision ``rev``; every write is a compare-and-set on it, and a lost one stops
the work at hand (the record is re-read next tick). A board must hold the record's
**lease** (``lease = {owner, until}``, :data:`LEASE`, taken by compare-and-set) to post,
edit, recreate or resolve, so two nodes never both post or both recreate a deleted comment.

**States.** ``none`` (no comment yet: post it), ``posting`` (a post was sent; set before
the call), ``posted`` (``comment_id`` known: edit it), ``unknown`` (the post's answer was
lost: never posted again), ``unresolved``. An ``unknown`` or ``posting`` record left by a
dead process is **resolved** first: the PR's comments are listed and the one posted by this
App (``performed_via_github_app.id``) that carries the chain's hidden marker is adopted;
if there is none the record gives up silently (``unresolved``, final) rather than risk a
second comment.

**The end.** The chain-end action (``status: true``) never calls GitHub and never fails:
:meth:`StatusBoard.finish` stores its text as the record's pending final
(``final_text``, ``final_run``) and returns. The tick delivers it like any edit (or posts
it, when the chain has no comment), and marks the record ``final`` only once GitHub
acknowledged (2xx). A chain that ends without such an action (cancelled, superseded, idle
for :data:`~culture_rules.node.fixer_status.IDLE_END`) gets an engine-worded pending final
the same way.

**Pacing.** Every write keeps :data:`~culture_rules.node.fixer_status.EDIT_FLOOR_S` after
the previous one (final ones included); notes alone wait
:data:`~culture_rules.node.fixer_status.NOTES_EVERY_S`. A failed call backs off
exponentially (``retry_at``, from 5 s up to :data:`BACKOFF_CAP`); ``http_403``,
``http_422`` and an App that cannot serve the repo give up after :data:`GIVE_UP_TRIES`; a
pending final gives up after :data:`FINAL_HORIZON`. A tick makes at most
:data:`MAX_CALLS` GitHub calls within :data:`MAX_SECONDS` (each call bounded by the time
left), after the drive stage, so it never holds up pushes for long.

**Housekeeping.** :func:`ensure_status_indexes` declares the indexes the board's queries
use; final records older than :data:`RETENTION` are dropped (live chains keep theirs).
Standard-library only.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
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
    notes_lines,
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
    "FINAL_HORIZON",
    "GIVE_UP_TRIES",
    "LEASE",
    "MAX_CALLS",
    "MAX_SECONDS",
    "RETENTION",
    "StatusBoard",
    "ensure_status_indexes",
]

log = logging.getLogger(__name__)

LEASE = timedelta(seconds=60)
"""How long a board holds a record while it posts or edits (longer than any call)."""
BACKOFF_BASE_S = 5.0
BACKOFF_CAP = timedelta(minutes=15)
"""The longest wait between two tries after failures."""
GIVE_UP_TRIES = 3
"""Tries before a permanent refusal (403, 422, repo or actor refused) gives up."""
GIVE_UP_CODES = frozenset({"http_403", "http_422", "repo_not_allowed", "actor_not_found"})
FINAL_HORIZON = timedelta(hours=24)
"""How long a pending final is tried before the record gives up."""
MAX_CALLS = 10
"""GitHub calls one tick may make."""
MAX_SECONDS = 10.0
"""Seconds one tick may spend on GitHub calls."""
RECORD_HORIZON = timedelta(days=2)
"""Open records created longer ago are not read (they are closed long before)."""
RETENTION = timedelta(days=30)
"""Final records are dropped this long after they became final."""
RETENTION_EVERY = timedelta(hours=1)
QUERY_LIMIT = 200

_RUNS = "runs"
_BRIDGE = "bridge_invocations"
_ACTIVE = "running"
_ID, _REV, _STATE, _FINAL, _LEASE = "id", "rev", "state", "final", "lease"
_ACTOR, _REPO, _NUMBER = "actor", "repo", "number"
_COMMENT_ID, _URL, _CREATED_AT, _FINAL_AT = "comment_id", "url", "created_at", "final_at"
_FINAL_TEXT, _FINAL_RUN, _FINAL_ASKED = "final_text", "final_run", "final_requested_at"
_FAILURES, _RETRY_AT, _LAST_EDIT_AT = "failures", "retry_at", "last_edit_at"
_OUTCOME, _STAGE_SIG, _NOTES_SIG, _KEY = "outcome", "stage_sig", "notes_sig", "concurrency_key"
NONE, POSTING, POSTED, UNKNOWN, UNRESOLVED = "none", "posting", "posted", "unknown", "unresolved"
_FINAL_TEXT_CAP = 20_000


def ensure_status_indexes(store: Any) -> None:
    """The indexes of the board's queries, where the store has indexes (MongoDB): open and
    final records by date, a key's runs by date, a run's bridge invocations."""
    ensure = getattr(store, "ensure_index", None)
    if not callable(ensure):
        return
    ensure(STATUS_COLLECTION, [(_FINAL, 1), (_CREATED_AT, 1), (_ID, 1)], name="status_open")
    ensure(STATUS_COLLECTION, [(_FINAL, 1), (_FINAL_AT, 1), (_ID, 1)], name="status_final")
    ensure(_RUNS, [(_KEY, 1), (_CREATED_AT, 1), (_ID, 1)], name="runs_key_created")
    ensure(_BRIDGE, [("run_id", 1)], name="bridge_by_run")


def _refused(code: str) -> bool:
    """GitHub refused the post (4xx but 408): no comment was created."""
    return code.startswith("http_4") and code != "http_408"


@dataclass(frozen=True)
class _Failure:
    """Why a call failed (the GitHub error's code and whether a retry may help)."""

    code: str
    retryable: bool


class _Budget:
    """At most ``calls`` GitHub calls within ``seconds`` (a monotonic clock)."""

    def __init__(self, calls: int, seconds: float, monotonic: Callable[[], float]) -> None:
        self._calls = calls
        self._monotonic = monotonic
        self._end = monotonic() + seconds

    @property
    def spent(self) -> bool:
        return self._calls <= 0 or self._monotonic() >= self._end

    def use(self) -> None:
        self._calls -= 1

    def left_s(self) -> float:
        return max(self._end - self._monotonic(), 1.0)


@dataclass(frozen=True)
class _Ctx:
    """One tick's App lookup and call budget."""

    apps: Callable[[str, str], Any]
    budget: _Budget


class StatusBoard:
    """Posts and edits the status comments (module doc). One per process; the reporter's
    tick and the chain-end action share its lock."""

    def __init__(
        self,
        store: Any,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        owner: str | None = None,
        known: Callable[[], Iterable[str]] | None = None,
        max_calls: int = MAX_CALLS,
        max_seconds: float = MAX_SECONDS,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self.owner = owner or f"board-{uuid.uuid4().hex[:12]}"
        self._known_fn = known
        self._max_calls = max_calls
        self._max_seconds = max_seconds
        self._lock = threading.RLock()
        self._roots: dict[str, str] = {}
        self._retained_at: datetime | None = None

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

    # ------------------------------------------------------------------ the record

    def _save(self, doc: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any] | None:
        """Compare-and-set ``changes`` on the record's revision: the new record, or None when
        another writer moved it first (the caller stops; the next tick re-reads)."""
        rev = doc.get(_REV, 0)
        res = self._store.update_if(
            STATUS_COLLECTION, doc[_ID], {_REV: rev}, {**changes, _REV: rev + 1}
        )
        return dict(res.document) if res.won and res.document is not None else None

    def _claim(self, root: Mapping[str, Any], target: _Target) -> dict[str, Any] | None:
        doc = {
            _ID: str(root.get(_ID)),
            _REPO: target.repo,
            _NUMBER: target.number,
            _ACTOR: target.actor,
            _KEY: root.get(_KEY),
            _STATE: NONE,
            _FINAL: False,
            _COMMENT_ID: None,
            _URL: None,
            _LEASE: None,
            _REV: 0,
            _FAILURES: 0,
            _RETRY_AT: None,
            _CREATED_AT: iso(self._clock()),
        }
        try:
            return self._store.insert(STATUS_COLLECTION, doc)
        except DuplicateKeyError:
            return None

    def _take_lease(self, doc: Mapping[str, Any]) -> dict[str, Any] | None:
        lease = doc.get(_LEASE) if isinstance(doc.get(_LEASE), Mapping) else {}
        until = parse_time(lease.get("until"))
        now = self._clock()
        if lease.get("owner") not in (None, self.owner) and until is not None and until > now:
            return None
        return self._save(doc, {_LEASE: {"owner": self.owner, "until": iso(now + LEASE)}})

    def _release(self, doc_id: str) -> None:
        doc = self._store.get(STATUS_COLLECTION, doc_id)
        lease = doc.get(_LEASE) if doc and isinstance(doc.get(_LEASE), Mapping) else {}
        if doc is not None and lease.get("owner") == self.owner:
            self._save(doc, {_LEASE: None})

    # ------------------------------------------------------------------ the tick

    def tick(self, apps: Callable[[str, str], Any], serves: Callable[[str], bool]) -> int:
        """Claim the records of chains whose first run is past its hold, then work the open
        records within the tick's budget; return how many posts or edits GitHub took.
        ``serves(actor_id)``: whether this node may act as that App actor."""
        ctx = _Ctx(apps, _Budget(self._max_calls, self._max_seconds, self._monotonic))
        done = 0
        with self._lock:
            for run in self._store.find(_RUNS, {"status": _ACTIVE}):
                self._start(serves, run)
            for doc in self._open():
                if ctx.budget.spent:
                    break
                done += self._work(ctx, serves, doc)
            self._retain()
        return done

    def _open(self) -> list[dict[str, Any]]:
        after = iso(self._clock() - RECORD_HORIZON)
        return self._store.find_range(
            STATUS_COLLECTION, {_FINAL: False}, field=_CREATED_AT, after=after, limit=QUERY_LIMIT
        )

    def _start(self, serves: Callable[[str], bool], run: Mapping[str, Any]) -> None:
        actor = run_status_actor(run)
        if actor is None or not serves(actor) or not past_hold(run):
            return
        root = self.root_of(run)
        if root is None or self._store.get(STATUS_COLLECTION, str(root.get(_ID))):
            return
        target = _target(root, actor)
        if target is not None:
            self._claim(root, target)

    def _work(self, ctx: _Ctx, serves: Callable[[str], bool], doc: Mapping[str, Any]) -> int:
        if not serves(str(doc.get(_ACTOR))) or not self._retry_due(doc):
            return 0
        held = self._take_lease(doc)
        if held is None:
            return 0
        try:
            return self._step(ctx, held)
        finally:
            self._release(held[_ID])

    def _retry_due(self, doc: Mapping[str, Any]) -> bool:
        at = parse_time(doc.get(_RETRY_AT))
        return at is None or self._clock() >= at

    def _step(self, ctx: _Ctx, doc: dict[str, Any]) -> int:
        if doc.get(_STATE) in (POSTING, UNKNOWN):
            return self._resolve(ctx, doc)
        root = self._store.get(_RUNS, doc[_ID])
        if root is None:
            self._close(doc, "gone")
            return 0
        chain = self.chain(root)
        doc = self._closing(doc, chain)
        if doc is None:
            return 0
        if doc.get(_FINAL_TEXT) is not None:
            return self._deliver_final(ctx, doc, chain)
        sigs = self._sigs(chain)
        if doc.get(_STATE) == POSTED and not self._due(doc, sigs):
            return 0
        return self._send(ctx, doc, render(chain, known=self._known()), sigs, final=False)

    @staticmethod
    def _sigs(chain: Chain) -> dict[str, str]:
        return {
            _STAGE_SIG: digest("\n".join(stage_lines(chain))),
            _NOTES_SIG: digest("\n".join(notes_lines(chain))),
        }

    def _since_edit(self, doc: Mapping[str, Any]) -> float:
        last = parse_time(doc.get(_LAST_EDIT_AT))
        return (self._clock() - last).total_seconds() if last else float("inf")

    def _due(self, doc: Mapping[str, Any], sigs: Mapping[str, str]) -> bool:
        if sigs[_STAGE_SIG] != doc.get(_STAGE_SIG):
            return True  # the floor is checked by _send
        return sigs[_NOTES_SIG] != doc.get(_NOTES_SIG) and self._since_edit(doc) >= NOTES_EVERY_S

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

    def _deliver_final(self, ctx: _Ctx, doc: dict[str, Any], chain: Chain) -> int:
        asked = parse_time(doc.get(_FINAL_ASKED))
        if asked is not None and self._clock() - asked >= FINAL_HORIZON:
            log.warning("status comment of chain %s: final not delivered in time", doc[_ID])
            self._close(doc, "gave_up")
            return 0
        final = Final(str(doc.get(_FINAL_TEXT)), doc.get(_FINAL_RUN))
        body = render(chain, final, known=self._known())
        return self._send(ctx, doc, body, self._sigs(chain), final=True)

    def _close(self, doc: Mapping[str, Any], outcome: str) -> None:
        self._save(doc, {_FINAL: True, _FINAL_AT: iso(self._clock()), _OUTCOME: outcome})

    # ------------------------------------------------------------------ GitHub

    def _send(self, ctx: _Ctx, doc: dict, body: str, sigs: Mapping, *, final: bool) -> int:
        """Edit the record's comment, or post it when there is none; at most once per
        :data:`EDIT_FLOOR_S`. Returns 1 when GitHub took it."""
        if self._since_edit(doc) < EDIT_FLOOR_S:
            return 0
        if doc.get(_STATE) == POSTED and doc.get(_COMMENT_ID):
            return self._patch(ctx, doc, body, sigs, final=final)
        return self._post(ctx, doc, body, sigs, final=final)

    def _done(self, sigs: Mapping[str, Any], *, final: bool) -> dict[str, Any]:
        now = iso(self._clock())
        changes = {_LAST_EDIT_AT: now, _FAILURES: 0, _RETRY_AT: None, **sigs}
        if final:
            changes.update({_FINAL: True, _FINAL_AT: now, _OUTCOME: "delivered"})
        return changes

    def _call(self, ctx: _Ctx, doc: Mapping[str, Any], op: Callable[[Any], Any]) -> Any:
        """One GitHub call as the record's App, bounded by the tick's time left; the
        answer, or a :class:`_Failure`."""
        from culture_rules.apps.github import GitHubError  # noqa: PLC0415

        ctx.budget.use()
        try:
            app = ctx.apps(str(doc.get(_ACTOR)), str(doc.get(_REPO)))
            limit = getattr(app, "deadline", None)
            until = self._clock() + timedelta(seconds=ctx.budget.left_s())
            with limit(until) if callable(limit) else contextlib.nullcontext():
                return op(app)
        except GitHubError as exc:
            return _Failure(exc.code, exc.retryable)

    def _post(self, ctx: _Ctx, doc: dict, body: str, sigs: Mapping, *, final: bool) -> int:
        doc = self._save(doc, {_STATE: POSTING})
        if doc is None:
            return 0
        out = self._call(ctx, doc, lambda app: app.post_comment(doc[_REPO], doc[_NUMBER], body))
        if isinstance(out, _Failure):
            log.warning("status comment of chain %s not posted: %s", doc[_ID], out.code)
            self._fail(doc, out, {_STATE: NONE if _refused(out.code) else UNKNOWN})
            return 0
        changes = {_STATE: POSTED, _COMMENT_ID: out.get(_COMMENT_ID), _URL: out.get(_URL)}
        self._save(doc, {**changes, **self._done(sigs, final=final)})
        return 1

    def _patch(self, ctx: _Ctx, doc: dict, body: str, sigs: Mapping, *, final: bool) -> int:
        def edit(app: Any) -> Any:
            return app.update_issue_comment(doc[_REPO], doc[_COMMENT_ID], body)

        out = self._call(ctx, doc, edit)
        if isinstance(out, _Failure) and out.code == "http_404":
            log.info("status comment of chain %s was deleted: posting it again", doc[_ID])
            fresh = self._save(doc, {_STATE: NONE, _COMMENT_ID: None})
            return self._post(ctx, fresh, body, sigs, final=final) if fresh else 0
        if isinstance(out, _Failure):
            log.warning("status comment of chain %s not edited: %s", doc[_ID], out.code)
            self._fail(doc, out, {})
            return 0
        self._save(doc, self._done(sigs, final=final))
        return 1

    def _fail(self, doc: Mapping[str, Any], failure: _Failure, changes: Mapping) -> None:
        """Record a failure: back off exponentially; a permanent refusal gives up after
        :data:`GIVE_UP_TRIES`."""
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
            out.update({_FINAL: True, _FINAL_AT: iso(now), _OUTCOME: "gave_up"})
        self._save(doc, out)

    def _resolve(self, ctx: _Ctx, doc: dict[str, Any]) -> int:
        """A post whose answer was lost: adopt the comment this App posted with the chain's
        marker, else give up silently (never a second comment)."""
        out = self._call(
            ctx, doc, lambda app: (app.list_issue_comments(doc[_REPO], doc[_NUMBER]), app.app_id)
        )
        if isinstance(out, _Failure):
            self._fail(doc, out, {})
            return 0
        comments, app_id = out
        marker = marker_of(doc[_ID])
        mine = [c for c in comments if c.get("app_id") == app_id and marker in c.get("body", "")]
        if mine:
            found = {_COMMENT_ID: mine[0].get(_COMMENT_ID), _URL: mine[0].get(_URL)}
            self._save(doc, {_STATE: POSTED, **found, _FAILURES: 0, _RETRY_AT: None})
            return 0
        log.info("status comment of chain %s not found after a lost post: giving up", doc[_ID])
        self._save(
            doc,
            {_STATE: UNRESOLVED, _FINAL: True, _FINAL_AT: iso(self._clock()), _OUTCOME: UNRESOLVED},
        )
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
        chain's status comment; the tick delivers it. Never calls GitHub and never fails.
        ``None`` when ``run`` is not in an opted-in chain on PR ``where`` (the caller posts
        a plain comment)."""
        actor = run_status_actor(run)
        root = self.root_of(run) if run is not None and actor else None
        target = _target(root, actor) if root is not None and actor else None
        if target is None or not _same_pr(target, where):
            return None
        changes = {
            _FINAL_TEXT: str(text)[:_FINAL_TEXT_CAP],
            _FINAL_RUN: run.get(_ID),
            _FINAL_ASKED: iso(self._clock()),
            _FAILURES: 0,
            _RETRY_AT: None,
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


@dataclass(frozen=True)
class _Target:
    """Where a chain's comment lives: the App actor, the repository and the PR."""

    actor: str
    repo: str
    number: int


def _target(root: Mapping[str, Any], actor: str) -> _Target | None:
    found = pr_of(root)
    return _Target(actor, found[0], found[1]) if found else None


def _same_pr(target: _Target, where: tuple[str, int] | None) -> bool:
    if where is None:
        return True
    repo, number = where
    return target.repo.lower() == str(repo).lower() and target.number == number
