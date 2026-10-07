"""Once-per-SHA settle: one ``github.pr.checks_settled`` event per head SHA.

A GitHub check-completion event (``github.checks.suite_completed`` /
``github.checks.workflow_completed``) calls :meth:`ChecksSettler.on_check`. The settler lists
the head SHA's check suites (through the injectable ``suites`` seam; the production seam is
:class:`AppSuiteLister`, the GitHub App's read-only ``Checks: read``), drops suites from apps in
the shared variable ``ignored_check_apps``, and:

- every remaining suite ``completed`` and ``checks_settle_min_s`` elapsed since the SHA was
  armed (by its first completion) -> emit ``github.pr.checks_settled`` with
  ``settled_by: "all_completed"``;
- otherwise persist a pending settle record whose ``deadline`` is ``now +
  checks_settle_timeout_s``; :meth:`ChecksSettler.tick` (run from the node cycle) re-reads the
  suites once it is due and emits with ``settled_by: "timeout"`` if some are still running.
  The deadline lives in the store, so a node restart loses nothing.

Recovery: the webhook stores the completion event *before* it arms the SHA, and a failed
arm answers 503 - but GitHub does not redeliver failed deliveries on its own, so every
:meth:`ChecksSettler.tick` also arms SHAs from stored completion events. It reads
completions in ``(received_at, id)`` order after a watermark shared by every node (the
``recovery`` document of :data:`RECOVERY_COLLECTION`, advanced by compare-and-set only past
events it handled), at most :data:`RECOVERY_BATCH` per tick, never older than
:data:`RECOVERY_WINDOW_S` and never newer than :data:`RECOVERY_GRACE_S` ago (the grace
covers an arm still in flight and clock skew between the receiving server and the node). A
SHA that has a settle record (pending, or settled by completion or timeout) or a settled
event is left alone; a missing one is armed as of the event's ``received_at``, so the
minimum window and the timeout run as if the webhook had armed it. Arming stays
insert-once, and the poll claim and deterministic event id keep it once-per-SHA across
nodes. A store error stops the page; the watermark stays before the failed event and the
next tick retries it.

The event also carries ``conclusion``: ``"success"`` when every counted (non-ignored) suite
concluded ``success``, ``neutral`` or ``skipped`` (vacuously so with none counted),
``"timeout"`` when settled by the timeout, else ``"failure"``. A ``"success"`` is the explicit
green signal that resets a rule's attempt budget for the PR (:mod:`culture_rules.node.firing`,
"Concurrency keys").

With a ``pull`` seam the event also carries the PR facts of its first PR number
(:func:`~culture_rules.apps.github.complete_pr_facts`: ``head_repo``, ``base_repo``,
``base_branch``, ``base_sha``, ``draft``, ``pr_author``; best-effort, all omitted on failure or
when any fact is missing or malformed); ``head_sha`` stays the settled SHA and the check's own
``head_branch`` wins.

Variables (read each call through ``store.get_variable``; an absent, mistyped or non-positive
value falls back to a stated default): ``ignored_check_apps`` defaults to ``["claude"]`` and
``checks_settle_timeout_s`` to :data:`DEFAULT_TIMEOUT_S` and ``checks_settle_min_s`` (the
minimum window before ``all_completed``, so a slower app's suite can appear) to
:data:`DEFAULT_MIN_S`.

Deduplication is durable: the event id is a hash of ``repo@sha``, so the unique id of the
``events`` collection makes two nodes, a redelivered webhook or the timeout racing the last
completion emit exactly once. The settle record (``checks_settle`` collection) only carries the
deadline and the outcome; the event insert is the authority.

Placement: with a ``serves`` seam (the node passes :meth:`AppSuiteLister.serves`) a node
claims, polls and emits a pending SHA only when it can serve the repository's GitHub App
actor: the actor is placed on this node's machine (its ``machine``) - or has no ``machine``,
in which case any node may serve it - *and* this node resolves the App's private key. It
fails closed: a node without the key never claims a poll, so it cannot time a SHA out
without the PR facts, and the credentialed node emits the settled event. Recovery arming
needs no credentials and stays on every node. A failed key resolve is retried after
:data:`UNRESOLVED_RETRY_S` (a node without the key does not run ``grant get`` every cycle).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.actors.secrets import resolve as resolve_secret
from culture_rules.apps.github import (
    DEFAULT_API_BASE,
    GitHubApp,
    GitHubError,
    Transport,
    complete_pr_facts,
)
from culture_rules.events.emit import derive_envelope
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.node.actions.github import GitHubCommentPort
from culture_rules.store.port import DuplicateKeyError, StoragePort
from culture_rules.store.versioning import utc_timestamp

__all__ = [
    "CHECK_TYPES",
    "DEFAULT_IGNORED_APPS",
    "DEFAULT_MIN_S",
    "DEFAULT_TIMEOUT_S",
    "LOOKUP_WORKERS",
    "RECOVERY_BATCH",
    "RECOVERY_COLLECTION",
    "RECOVERY_GRACE_S",
    "RECOVERY_WINDOW_S",
    "SETTLED_TYPE",
    "SETTLE_COLLECTION",
    "UNRESOLVED_RETRY_S",
    "AppSuiteLister",
    "ChecksSettler",
    "settled_event_id",
]

log = logging.getLogger(__name__)

SETTLE_COLLECTION = "checks_settle"
SETTLED_TYPE = "github.pr.checks_settled"
CHECK_TYPES = frozenset(("github.checks.suite_completed", "github.checks.workflow_completed"))
DEFAULT_IGNORED_APPS: tuple[str, ...] = ("claude",)
DEFAULT_TIMEOUT_S = 900.0
DEFAULT_MIN_S = 60.0
RECOVERY_COLLECTION = "checks_settle_recovery"
RECOVERY_ID = "recovery"
RECOVERY_WINDOW_S = 86400.0
"""Completions received longer ago than this are never recovered (outages beyond it are lost)."""
RECOVERY_GRACE_S = 120.0
"""Completions younger than this are left to the webhook (an arm in flight, clock skew)."""
RECOVERY_BATCH = 100
"""The most stored completions one tick reads."""
UNRESOLVED_RETRY_S = 60.0
"""How long a node waits before retrying a failed App key resolve (the settle ``serves``)."""
POLL_BASE_S = 15.0
POLL_CAP_S = 120.0
SETTLE_HOST = "checks-settle"
SOURCE = "culture-rules://checks-settle"

SuiteLister = Callable[[str, str], list[dict[str, Any]]]
"""``(repo, sha) -> [{app_slug, status, conclusion}]``; may raise :class:`GitHubError`."""
PullLookup = Callable[[str, int], Mapping[str, Any]]
"""``(repo, number) -> the pull request document`` (optional enrichment)."""
Serves = Callable[[str], bool]
"""``repo -> whether this node can serve the repo's App actor`` (placement and key)."""


def settled_event_id(repo: str, sha: str) -> str:
    """The deterministic events id of the one settled event of ``repo@sha``."""
    digest = hashlib.sha256(f"{repo}@{sha}".lower().encode()).hexdigest()[:24]
    return f"settled_{digest}"


def _var(store: StoragePort, name: str) -> Any:
    doc = store.get_variable(name)
    return None if doc is None else doc.get("value")


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat()


def _parse(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


class ChecksSettler:
    """Decides when a head SHA's checks have settled and emits the one event for it."""

    def __init__(
        self,
        store: StoragePort,
        suites: SuiteLister,
        *,
        pull: PullLookup | None = None,
        serves: Serves | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._suites = suites
        self._pull = pull
        self._serves = serves
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ variables

    def ignored_apps(self) -> frozenset[str]:
        value = _var(self._store, "ignored_check_apps")
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            value = list(DEFAULT_IGNORED_APPS)
        return frozenset(v.casefold() for v in value)

    def timeout_s(self) -> float:
        value = _var(self._store, "checks_settle_timeout_s")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return DEFAULT_TIMEOUT_S
        return float(value)

    def min_s(self) -> float:
        value = _var(self._store, "checks_settle_min_s")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return DEFAULT_MIN_S
        return float(value)

    # ------------------------------------------------------------------ decisions

    def _check_state(self, repo: str, sha: str) -> tuple[bool, str]:
        ignored = self.ignored_apps()
        suites = [
            s
            for s in self._suites(repo, sha)
            if str(s.get("app_slug") or "").casefold() not in ignored
        ]
        done = all(s.get("status") == "completed" for s in suites)
        green = all(s.get("conclusion") in {"success", "neutral", "skipped"} for s in suites)
        return done, "success" if green else "failure"

    def on_check(self, data: Mapping[str, Any]) -> str:
        """Handle one check-completion event's data; return what happened:
        ``emitted``, ``duplicate``, ``pending``, ``ignored`` or ``error`` (listing failed; the
        pending record is already persisted, so :meth:`tick` retries and the timeout fires)."""
        repo, sha = data.get("repository"), data.get("head_sha")
        if not isinstance(repo, str) or not repo or not isinstance(sha, str) or not sha:
            return "ignored"
        if self._store.get(EVENTS_COLLECTION, settled_event_id(repo, sha)) is not None:
            return "duplicate"
        rec = self._arm(repo, sha, data)  # before the lookup: a failure must not lose the SHA
        try:
            done, conclusion = self._check_state(repo, sha)
        except GitHubError as exc:
            log.warning("checks settle: suite listing failed (%s)", exc.code)
            return "error"
        if done and self._now() >= self._window_end(rec):
            return self._emit(repo, sha, rec, "all_completed", conclusion)
        return "pending"

    def tick(self) -> int:
        """Settle pending SHAs: emit ``timeout`` past the deadline, or ``all_completed`` once
        the minimum window has passed and every counted suite is complete."""
        self._recover(self._now())
        now = self._now()
        emitted = 0
        for rec in self._store.find(SETTLE_COLLECTION, {"state": "pending"}):
            deadline = _parse(rec.get("deadline"))
            if deadline is None:
                continue
            timed_out = now >= deadline
            if not timed_out and now < self._window_end(rec):
                continue
            repo, sha = rec["repository"], rec["head_sha"]
            if not self._serves_here(repo):
                continue  # the App actor is placed elsewhere, or its key is not here
            if not self._claim_poll(rec, now, deadline):
                continue  # not due yet, or another node holds this interval's poll
            try:
                done, conclusion = self._check_state(repo, sha)
            except GitHubError as exc:
                log.warning("checks settle: suite listing failed (%s)", exc.code)
                done = False  # the timeout fires regardless of what is listed
            if not done and not timed_out:
                continue
            by = "all_completed" if done else "timeout"
            if self._emit(repo, sha, rec, by, conclusion if done else "timeout") == "emitted":
                emitted += 1
        return emitted

    def _serves_here(self, repo: Any) -> bool:
        if self._serves is None:
            return True
        if not isinstance(repo, str):
            return False
        try:
            return bool(self._serves(repo))
        except Exception:  # noqa: BLE001 - fail closed: never claim what cannot be served
            return False

    # ------------------------------------------------------------------ recovery

    def _recover(self, now: datetime) -> int:
        """Arm SHAs whose webhook arm failed, from one bounded page of stored completions
        after the shared watermark; return how many were armed. Never raises: a failure is
        logged and the unhandled events stay after the watermark for the next tick."""
        try:
            return self._recover_page(now)
        except Exception as exc:  # noqa: BLE001 - recovery must not block the pending polls
            log.warning(
                "checks settle: recovery failed (%s); retried next tick", type(exc).__name__
            )
            return 0

    def _recover_page(self, now: datetime) -> int:
        mark = self._store.get(RECOVERY_COLLECTION, RECOVERY_ID)
        seen = (mark.get("received_at"), mark.get("event_id")) if mark else (None, None)
        floor = (utc_timestamp(now - timedelta(seconds=RECOVERY_WINDOW_S)), "")
        after = floor
        if all(isinstance(part, str) for part in seen) and seen > floor:
            after = seen
        events = self._store.find_events(
            types=CHECK_TYPES,
            after=after,
            until=utc_timestamp(now - timedelta(seconds=RECOVERY_GRACE_S)),
            limit=RECOVERY_BATCH,
        )
        armed, done = 0, after
        try:
            for event in events:
                armed += self._recover_one(event, now)
                done = (event["received_at"], event["id"])
        finally:
            if done != after:
                # CAS on the mark as read: a racing node that moved it first keeps its
                # value (it handled the same page), so the mark never moves backwards
                self._store.update_if(
                    RECOVERY_COLLECTION,
                    RECOVERY_ID,
                    {"received_at": seen[0], "event_id": seen[1]},
                    {"received_at": done[0], "event_id": done[1]},
                    upsert=True,
                )
        return armed

    def _recover_one(self, event: Mapping[str, Any], now: datetime) -> int:
        envelope = event.get("envelope")
        data = envelope.get("data") if isinstance(envelope, Mapping) else None
        if not isinstance(data, Mapping):
            return 0
        repo, sha = data.get("repository"), data.get("head_sha")
        if not isinstance(repo, str) or not repo or not isinstance(sha, str) or not sha:
            return 0
        if self._store.get(SETTLE_COLLECTION, f"{repo}@{sha}".lower()) is not None:
            return 0  # armed by the webhook (or settled by completion or timeout)
        if self._store.get(EVENTS_COLLECTION, settled_event_id(repo, sha)) is not None:
            return 0
        received = _parse(event.get("received_at"))
        at = now if received is None else min(received, now)
        self._arm(repo, sha, data, at=at)
        log.info("checks settle: recovered an unarmed SHA from a stored completion")
        return 1

    # ------------------------------------------------------------------ persistence

    def _claim_poll(self, rec: Mapping[str, Any], now: datetime, deadline: datetime) -> bool:
        """Claim this SHA's poll for one interval (compare-and-set on ``next_poll_at``), so
        one node lists it per interval. The interval backs off ``POLL_BASE_S`` doubling to
        ``POLL_CAP_S`` and never runs past the deadline, so the timeout is never delayed."""
        due = _parse(rec.get("next_poll_at"))
        if due is not None and now < due:
            return False
        n = int(rec.get("polls") or 0)
        step = timedelta(seconds=min(POLL_BASE_S * 2**n, POLL_CAP_S))
        nxt = min(now + step, deadline)
        res = self._store.update_if(
            SETTLE_COLLECTION,
            rec["id"],
            {"next_poll_at": rec.get("next_poll_at"), "state": "pending"},
            {"next_poll_at": _iso(nxt), "polls": n + 1},
        )
        return res.won

    def _now(self) -> datetime:
        return self._clock()

    def _window_end(self, rec: Mapping[str, Any]) -> datetime:
        armed = _parse(rec.get("armed_at")) or self._now()
        return armed + timedelta(seconds=self.min_s())

    def _arm(
        self, repo: str, sha: str, data: Mapping[str, Any], *, at: datetime | None = None
    ) -> Mapping[str, Any]:
        """Persist the SHA as pending (first deadline and arm time stand) and merge in any PR
        facts this completion newly carries. Returns the stored record. ``at`` is the arm
        time (recovery passes the completion's receipt); it defaults to now."""
        rid = f"{repo}@{sha}".lower()
        now = self._now() if at is None else at
        numbers = [n for n in data.get("pr_numbers") or () if isinstance(n, int)]
        doc = {
            "id": rid,
            "state": "pending",
            "repository": repo,
            "head_sha": sha,
            "head_branch": data.get("head_branch"),
            "pr_numbers": numbers,
            "number": data.get("number"),
            "armed_at": _iso(now),
            "deadline": _iso(now + timedelta(seconds=self.timeout_s())),
        }
        try:
            return self._store.insert(SETTLE_COLLECTION, doc)
        except DuplicateKeyError:
            pass
        for _ in range(10):
            rec = self._store.get(SETTLE_COLLECTION, rid) or doc
            changes: dict[str, Any] = {}
            if numbers and not rec.get("pr_numbers"):
                changes["pr_numbers"] = numbers
            if data.get("number") is not None and rec.get("number") is None:
                changes["number"] = data["number"]
            if data.get("head_branch") and not rec.get("head_branch"):
                changes["head_branch"] = data["head_branch"]
            if not changes or rec.get("state") != "pending":
                return rec
            expected = {k: rec.get(k) for k in changes}
            if self._store.update_if(SETTLE_COLLECTION, rid, expected, changes).won:
                return {**rec, **changes}
        return self._store.get(SETTLE_COLLECTION, rid) or doc

    def _enrich(self, repo: str, numbers: list[int]) -> dict[str, Any]:
        """Best-effort PR facts the fixer rules' condition reads (:func:`complete_pr_facts`,
        d14); any failure, or an answer missing any valid fact, omits them all. ``head_sha``
        stays the settled SHA (the PR may have moved on)."""
        if self._pull is None or not numbers:
            return {}
        try:
            facts = complete_pr_facts(dict(self._pull(repo, numbers[0])))
        except Exception:  # noqa: BLE001 - enrichment must never block the settle
            return {}
        if facts is None:
            return {}
        facts.pop("head_sha", None)
        return facts

    def _emit(
        self, repo: str, sha: str, src: Mapping[str, Any], settled_by: str, conclusion: str
    ) -> str:
        numbers = [n for n in src.get("pr_numbers") or () if isinstance(n, int)]
        payload: dict[str, Any] = {
            "repository": repo,
            "head_sha": sha,
            "head_branch": src.get("head_branch"),
            "pr_numbers": numbers,
            "number": numbers[0] if numbers else src.get("number"),
            "settled_by": settled_by,
            "conclusion": conclusion,
        }
        for key, value in self._enrich(repo, numbers).items():
            if key != "head_branch" or not payload.get("head_branch"):
                payload[key] = value
        envelope = derive_envelope(
            None,
            type=SETTLED_TYPE,
            source=SOURCE,
            data=payload,
            id=settled_event_id(repo, sha),
        )
        try:
            self._store.insert(EVENTS_COLLECTION, event_document(envelope, host=SETTLE_HOST))
            outcome = "emitted"
        except DuplicateKeyError:
            outcome = "duplicate"
        self._store.update_if(
            SETTLE_COLLECTION,
            f"{repo}@{sha}".lower(),
            {"state": "pending"},
            {"state": "emitted", "settled_by": settled_by},
        )
        return outcome


LOOKUP_WORKERS = 2
"""The most bounded PR lookups (:meth:`AppSuiteLister.get_pull` with ``timeout_s``) running at
once; it caps the threads a stuck secret resolve or slow GitHub can hold."""


class AppSuiteLister(GitHubCommentPort):
    """The production ``suites`` / ``pull`` / ``serves`` seams: reads through the GitHub app
    actor whose ``connection.repos`` allowlist holds the repository (first match, enabled
    actors only).

    With a ``host`` (a node) only actors placed on that machine or unplaced are used, and a
    failed key resolve is not retried for ``unresolved_retry_s`` (0 retries every call - the
    API server's webhook path, which has no ``host``)."""

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secrets: Callable[[str], str] | None = None,
        api_base: str = DEFAULT_API_BASE,
        host: str | None = None,
        unresolved_retry_s: float = 0.0,
    ) -> None:
        super().__init__(
            store, transport=transport, secrets=secrets or resolve_secret, api_base=api_base
        )
        self._lookup_slots = threading.BoundedSemaphore(LOOKUP_WORKERS)
        self._host = host
        self._retry_s = unresolved_retry_s
        self._unresolved: dict[tuple[str, Any], float] = {}

    def _app(self, actor_id: str, conn: Mapping[str, Any], allowed: set[str]) -> GitHubApp | None:
        key = (actor_id, conn.get("private_key"))
        until = self._unresolved.get(key)
        if until is not None and time.monotonic() < until:
            return None  # resolved and failed recently: fail closed without another grant get
        app = super()._app(actor_id, conn, allowed)
        if app is None and self._retry_s > 0:
            self._unresolved[key] = time.monotonic() + self._retry_s
        elif app is not None:
            self._unresolved.pop(key, None)
        return app

    def _placed_elsewhere(self, doc: Mapping[str, Any]) -> bool:
        machine = doc.get("machine")
        return bool(self._host and machine and machine != self._host)

    def serves(self, repo: str) -> bool:
        """Whether this node can read ``repo`` through its App actor: one covers it, is
        placed here (or unplaced), and its private key resolves on this host."""
        try:
            self._app_for(repo)
        except GitHubError:
            return False
        return True

    def _app_for(self, repo: str) -> GitHubApp:
        for doc in self._store.find("actors"):
            if self._placed_elsewhere(doc):
                continue
            conn = self._connection(doc.get("id"))
            if conn is None or not conn.get("app_id") or not conn.get("installation_id"):
                continue
            allowed = {str(r).lower() for r in conn.get("repos") or ()}
            if repo.lower() in allowed:
                app = self._app(str(doc["id"]), conn, allowed)
                if app is not None:
                    return app
        raise GitHubError("repo_not_allowed", "no app actor covers the repo")

    def list_suites(self, repo: str, sha: str) -> list[dict[str, Any]]:
        return self._app_for(repo).list_check_suites(repo, sha)

    def get_pull(
        self, repo: str, number: int, *, timeout_s: float | None = None
    ) -> Mapping[str, Any]:
        """Read one PR (read-only ``Pull requests: read``).

        ``timeout_s`` bounds the *whole* lookup - finding the actor, resolving its private key
        (a cold ``grant get`` can take 30 s), the token exchange and the read - so the webhook
        path cannot hold a delivery past GitHub's own timeout. The lookup runs on one of at
        most :data:`LOOKUP_WORKERS` daemon threads; at the bound the caller gets a retryable
        ``deadline_exceeded`` while the worker finishes in the background (its HTTP calls are
        cut off by the same deadline), so a slow secret resolve still caches the App and the
        next lookup is warm. With every worker still busy a lookup fails at once with
        ``lookup_busy`` instead of starting another thread."""
        if timeout_s is None:
            return self._app_for(repo).get_pull(repo, number)
        deadline = datetime.now(UTC) + timedelta(seconds=timeout_s)
        if not self._lookup_slots.acquire(blocking=False):
            raise GitHubError("lookup_busy", "every lookup worker is busy", retryable=True)
        result: Future[Mapping[str, Any]] = Future()

        def work() -> None:
            try:
                app = self._app_for(repo)
                with app.deadline(deadline):
                    result.set_result(app.get_pull(repo, number))
            except BaseException as exc:  # noqa: BLE001 - handed to the waiting caller
                result.set_exception(exc)
            finally:
                self._lookup_slots.release()

        try:
            threading.Thread(target=work, name="github-pull-lookup", daemon=True).start()
        except BaseException:
            self._lookup_slots.release()
            raise
        try:
            return result.result(timeout=max(0.0, timeout_s))
        except FutureTimeout:
            raise GitHubError("deadline_exceeded", retryable=True) from None
