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
concluded ``success``, ``neutral`` or ``skipped``, ``"timeout"`` when settled by the timeout,
else ``"failure"``. No counted suite (every listed suite from an ignored app, or none listed
yet) is never ``"success"``: the SHA keeps waiting for one to appear and, if none does,
settles at the timeout with :data:`NO_CHECKS` (``"no_checks"``). A ``"success"`` is the explicit
green signal that resets a rule's attempt budget for the PR (:mod:`culture_rules.node.firing`,
"Concurrency keys").

It also carries ``failed_apps`` (d25): the app slugs, lower-cased and sorted, of the
counted suites that completed with conclusion ``failure`` (``[]`` when none did), so a rule
can single out one app's failure - the PR fixer's ``pr-fixer-secrets`` fires on
``"gitguardian" in failed_apps`` and ``pr-fixer-checks`` stays off it.

**Late failures** (d25): a counted app's suite that completes ``failure`` *after* its SHA
settled (the settle timed out while it ran, or a re-run failed) is not in that event's
``failed_apps``, and the completion is otherwise a ``duplicate``.
:meth:`ChecksSettler.on_check` then emits one :data:`LATE_TYPE` event per (repo, SHA, app)
(:func:`late_event_id`): the settled event's data (its PR facts as of the settle) with
``failed_apps`` grown by the app, ``late_app``, ``conclusion: "failure"`` and
``settled_by: "late"``. ``pr-fixer-secrets-late`` reports a GitGuardian finding from it; no
fixer rule fires on it. Clocks never decide it: the failure becomes a candidate
(:data:`LATE_COLLECTION`) that is emitted only once the App's *current* listing still shows
the app's suite for the head concluded ``failure`` (so a failure re-run green is never
late); a failed listing, or a node that cannot read the repo's checks, keeps the candidate
for the next tick (on any node that can), and a candidate older than
:data:`RECOVERY_WINDOW_S` is dropped. The recovery scan notes a candidate for a stored
completion of a settled SHA that the webhook never handled (it died before
:meth:`ChecksSettler.on_check`), when it was received no more than
:data:`LATE_SKEW_MARGIN_S` (5 minutes) before the settled event - the webhook server's
clock may lag the settler's. The fixed id keeps the late event once. Both settle types and their id
prefixes are reserved at external ingest (:func:`~culture_rules.events.emit.reserved_reason`).

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
import secrets
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
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
from culture_rules.events.emit import (
    CHECKS_LATE_TYPE,
    CHECKS_SETTLED_TYPE,
    LATE_ID_PREFIX,
    SETTLED_ID_PREFIX,
    derive_envelope,
)
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.node.actions.github import GitHubCommentPort
from culture_rules.store.port import DuplicateKeyError, StoragePort
from culture_rules.store.retry import run_transaction
from culture_rules.store.versioning import utc_timestamp

__all__ = [
    "CHECK_TYPES",
    "DEFAULT_IGNORED_APPS",
    "DEFAULT_MIN_S",
    "DEFAULT_TIMEOUT_S",
    "LOOKUP_WORKERS",
    "NO_CHECKS",
    "RECOVERY_BATCH",
    "RECOVERY_COLLECTION",
    "RECOVERY_GRACE_S",
    "RECOVERY_WINDOW_S",
    "LATE_COLLECTION",
    "LATE_SKEW_MARGIN_S",
    "LATE_TYPE",
    "SETTLED_TYPE",
    "SETTLE_COLLECTION",
    "UNRESOLVED_RETRY_S",
    "WEBHOOK_SETTLE_BUDGET_S",
    "AppSuiteLister",
    "ChecksSettler",
    "counted_suites",
    "ignored_check_apps",
    "rearm_settle",
    "late_event_id",
    "settled_event_id",
    "suites_state",
    "webhook_on_check",
]

log = logging.getLogger(__name__)

SETTLE_COLLECTION = "checks_settle"
SETTLED_TYPE = CHECKS_SETTLED_TYPE
LATE_TYPE = CHECKS_LATE_TYPE
"""A counted app's suite failed after its SHA settled (d25; module doc, "Late failures")."""
CHECK_TYPES = frozenset(("github.checks.suite_completed", "github.checks.workflow_completed"))
DEFAULT_IGNORED_APPS: tuple[str, ...] = ("claude",)
DEFAULT_TIMEOUT_S = 900.0
DEFAULT_MIN_S = 60.0
NO_CHECKS = "no_checks"
"""Conclusion of a SHA settled by the timeout with no counted suite: not green, and never a
budget reset (only ``"success"`` is)."""
RECOVERY_COLLECTION = "checks_settle_recovery"
RECOVERY_ID = "recovery"
RECOVERY_WINDOW_S = 86400.0
"""Completions received longer ago than this are never recovered (outages beyond it are lost)."""
RECOVERY_GRACE_S = 120.0
"""Completions younger than this are left to the webhook (an arm in flight, clock skew)."""
LATE_SKEW_MARGIN_S = 300.0
"""How long before its settled event a stored completion is still a late candidate: the
webhook server's clock may lag the settler's (d25). Only candidates; the App's current
listing decides."""
LATE_COLLECTION = "checks_settle_late"
"""Late-failure candidates awaiting confirmation (one per head and app; d25)."""
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


class _PullDeferred(Exception):
    """The bounded webhook path's PR read ran out of time: emit from the tick instead."""


REARM_LIMIT = 3
"""The most times one head SHA's settle can be re-armed (:func:`rearm_settle`); the rules'
per-PR attempt budget bounds the runs as well."""


def settled_event_id(repo: str, sha: str, generation: int = 0) -> str:
    """The deterministic events id of ``repo@sha``'s settled event in ``generation`` (0 is
    the first settle; a re-arm after ``base_changed`` starts the next one)."""
    key = f"{repo}@{sha}".lower() + (f"#{generation}" if generation else "")
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return f"{SETTLED_ID_PREFIX}{digest}"


def late_event_id(repo: str, sha: str, app: str) -> str:
    """The deterministic events id of ``app``'s late failure on ``repo@sha`` (once ever)."""
    key = f"{repo}@{sha}#{app}".lower()
    return LATE_ID_PREFIX + hashlib.sha256(key.encode()).hexdigest()[:24]


def _generation(rec: Mapping[str, Any] | None) -> int:
    gen = (rec or {}).get("generation")
    return gen if isinstance(gen, int) and not isinstance(gen, bool) and gen >= 0 else 0


def rearm_settle(
    store: StoragePort,
    repo: str,
    sha: str,
    *,
    reason: str,
    cause: str | None = None,
    number: int | None = None,
    head_branch: str | None = None,
    now: datetime | None = None,
) -> str:
    """Settle ``repo@sha`` again (round 5): after ``github.push`` refused ``base_changed``
    the reviewed run is over, but the head is unchanged, so no new head means no new
    settle. This moves an emitted settle to ``pending`` in the next generation (a fresh
    minimum window and deadline); the next completion or the node's poll then emits a new
    ``checks_settled`` event carrying the PR's current facts (its new base), so the gate
    and the review run again against it. Bounded by :data:`REARM_LIMIT` (and the rules'
    attempt budget).

    ``cause`` is the refusal's durable identity (the review record the push judged); it is
    recorded with the generation transition in one compare-and-set, so replaying the same
    refused push after the re-armed generation settled is a no-op (``replayed``).
    ``number`` and ``head_branch`` (the PR the push named) are kept on a record the head
    never had, so the emitted event names the PR and fetches its current facts. Returns
    ``rearmed``, ``armed`` (no settle record yet), ``pending`` (already waiting),
    ``replayed`` or ``limit``."""
    when = now or datetime.now(UTC)
    rid = f"{repo}@{sha}".lower()
    timeout = _rearm_timeout(store)
    numbers = _named_numbers(number)
    fresh = {
        "state": "pending",
        "armed_at": _iso(when),
        "deadline": _iso(when + timedelta(seconds=float(timeout))),
        "next_poll_at": None,
        "polls": 0,
        "rearmed_for": reason,
    }
    head = _RearmHead(rid, repo, sha, cause, numbers, head_branch, fresh)
    for _ in range(10):
        move = _rearm_move(store.get(SETTLE_COLLECTION, rid), head)
        if move.kind == "answer":
            return move.outcome
        if move.kind == "limit":
            log.warning("checks settle: %s re-armed %d times; not again", rid, move.generation)
            return "limit"
        if move.kind == "insert":
            try:
                store.insert(SETTLE_COLLECTION, move.changes)
                return "armed"
            except DuplicateKeyError:
                continue
        if store.update_if(SETTLE_COLLECTION, rid, move.expected, move.changes).won:
            return move.outcome
    return "pending"


def _named_numbers(number: Any) -> list[int]:
    """``[number]`` when the refusal named an int PR number (not a bool), else ``[]``."""
    return [number] if isinstance(number, int) and not isinstance(number, bool) else []


@dataclass(frozen=True)
class _RearmHead:
    """What :func:`rearm_settle` was asked: the head, the refusal and its fresh settle."""

    rid: str
    repo: str
    sha: str
    cause: str | None
    numbers: list[int]
    head_branch: str | None
    fresh: Mapping[str, Any]


@dataclass(frozen=True)
class _RearmMove:
    """One :func:`rearm_settle` decision over the record as read: ``answer`` (return
    ``outcome``, nothing written), ``limit``, ``insert`` (``changes`` is the new record) or
    ``update`` (compare-and-set ``changes`` on ``expected``; ``outcome`` when it wins)."""

    kind: str
    outcome: str = "pending"
    expected: Mapping[str, Any] | None = None
    changes: dict[str, Any] = field(default_factory=dict)
    generation: int = 0


def _rearm_move(rec: Mapping[str, Any] | None, head: _RearmHead) -> _RearmMove:
    """Decide :func:`rearm_settle`'s next write from the settle record ``rec`` as read."""
    cause = head.cause
    causes = list((rec or {}).get("rearm_causes") or [])
    if cause is not None and cause in causes:
        return _RearmMove("answer", "replayed")  # this refusal already re-armed the head once
    recorded = causes + ([cause] if cause is not None else [])
    if rec is None:
        doc = _first_settle(
            head.rid, head.repo, head.sha, head.head_branch, head.numbers, recorded, head.fresh
        )
        return _RearmMove("insert", "armed", changes=doc)
    if rec.get("state") == "pending":
        # coalesce into the settle that is still waiting: record the cause (so a replay
        # after it settles is a no-op) and fill a PR number or branch it lacks, by
        # compare-and-set on state, generation, causes and PR facts; a lost race (it
        # settled meanwhile) is retried and takes the re-arm path instead
        fill: dict[str, Any] = {}
        if cause is not None:
            fill["rearm_causes"] = recorded
        fill.update(_missing_pr_facts(rec, head.numbers, head.head_branch))
        if not fill:
            return _RearmMove("answer", "pending")
        return _RearmMove("update", "pending", _pending_guard(rec), fill)
    gen = _generation(rec)
    if gen >= REARM_LIMIT:
        return _RearmMove("limit", "limit", generation=gen)
    changes = {**head.fresh, "generation": gen + 1, "rearm_causes": recorded}
    # PR facts of a head first seen by this refusal
    changes.update(_missing_pr_facts(rec, head.numbers, head.head_branch))
    expected = {
        "state": rec.get("state"),
        "generation": rec.get("generation"),
        "rearm_causes": rec.get("rearm_causes"),
    }
    return _RearmMove("update", "rearmed", expected, changes)


def _rearm_timeout(store: StoragePort) -> Any:
    """``checks_settle_timeout_s`` when a positive number, else :data:`DEFAULT_TIMEOUT_S`."""
    timeout = _var(store, "checks_settle_timeout_s")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = DEFAULT_TIMEOUT_S
    return timeout


def _first_settle(
    rid: str,
    repo: str,
    sha: str,
    head_branch: str | None,
    numbers: list[int],
    recorded: list,
    fresh: Mapping[str, Any],
) -> dict[str, Any]:
    """The settle record :func:`rearm_settle` arms for a head that has none yet."""
    return {
        "id": rid,
        "repository": repo,
        "head_sha": sha,
        "head_branch": head_branch,
        "pr_numbers": numbers,
        "number": numbers[0] if numbers else None,
        "generation": 0,
        "rearm_causes": recorded,
        **fresh,
    }


def _missing_pr_facts(
    rec: Mapping[str, Any], numbers: list[int], head_branch: str | None
) -> dict[str, Any]:
    """The PR number and branch the refusal names that settle record ``rec`` lacks."""
    facts: dict[str, Any] = {}
    if numbers and not rec.get("pr_numbers"):
        facts.update(pr_numbers=numbers, number=numbers[0])
    if head_branch and not rec.get("head_branch"):
        facts["head_branch"] = head_branch
    return facts


def _pending_guard(rec: Mapping[str, Any]) -> dict[str, Any]:
    """The compare-and-set guard of a fill into pending ``rec``: state, generation, causes
    and PR facts as read."""
    return {
        "state": "pending",
        "generation": rec.get("generation"),
        "rearm_causes": rec.get("rearm_causes"),
        "pr_numbers": rec.get("pr_numbers"),
        "head_branch": rec.get("head_branch"),
    }


def ignored_check_apps(store: StoragePort) -> frozenset[str]:
    """The shared variable ``ignored_check_apps`` (casefolded), else
    :data:`DEFAULT_IGNORED_APPS`: the apps whose suites a settle decision leaves out."""
    value = _var(store, "ignored_check_apps")
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        value = list(DEFAULT_IGNORED_APPS)
    return frozenset(v.casefold() for v in value)


def counted_suites(
    suites: Iterable[Mapping[str, Any]], ignored: frozenset[str]
) -> list[Mapping[str, Any]]:
    """The suites a settle decision counts: those of an app not in ``ignored``."""
    return [s for s in suites if str(s.get("app_slug") or "").casefold() not in ignored]


def suites_state(suites: Iterable[Mapping[str, Any]]) -> tuple[bool, bool]:
    """``(done, green)`` of counted ``suites``: every one ``completed``, and every one
    concluded ``success``, ``neutral`` or ``skipped``. Callers treat an empty list as not
    green (``no_checks``) before asking."""
    suites = list(suites)
    done = all(s.get("status") == "completed" for s in suites)
    green = all(s.get("conclusion") in _GREEN for s in suites)
    return done, green


_GREEN = frozenset({"success", "neutral", "skipped"})


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


def _settle_verdict(done: bool, conclusion: str, timed_out: bool) -> tuple[str, str] | None:
    """How a polled SHA settles (``settled_by``, ``conclusion``), or None to keep waiting:
    ``all_completed`` once done, else ``timeout`` past the deadline (``no_checks`` when no
    suite was counted)."""
    if not done and not timed_out:
        return None
    by = "all_completed" if done else "timeout"
    if not done:
        conclusion = NO_CHECKS if conclusion == NO_CHECKS else "timeout"
    return by, conclusion


def _failed_apps(suites: list[Mapping[str, Any]]) -> list[str]:
    """The app slugs (lower-cased, sorted, once each) of the completed suites concluded
    ``failure`` (d25: a rule reads ``"gitguardian" in failed_apps``)."""
    return sorted(
        {
            str(s.get("app_slug") or "").casefold()
            for s in suites
            if s.get("status") == "completed" and s.get("conclusion") == "failure"
        }
        - {""}
    )


def _arm_changes(
    rec: Mapping[str, Any], data: Mapping[str, Any], numbers: list[int]
) -> dict[str, Any]:
    """The PR facts a completion's ``data`` carries that settle record ``rec`` lacks."""
    changes: dict[str, Any] = {}
    if numbers and not rec.get("pr_numbers"):
        changes["pr_numbers"] = numbers
    if data.get("number") is not None and rec.get("number") is None:
        changes["number"] = data["number"]
    if data.get("head_branch") and not rec.get("head_branch"):
        changes["head_branch"] = data["head_branch"]
    return changes


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
        defer_slow_pull: bool = False,
    ) -> None:
        self._store = store
        self._suites = suites
        self._pull = pull
        self._defer_slow_pull = defer_slow_pull
        """A retryable PR-read failure leaves the SHA pending instead of emitting without
        the PR facts (the bounded webhook path, :func:`webhook_on_check`)."""
        self._serves = serves
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ variables

    def ignored_apps(self) -> frozenset[str]:
        return ignored_check_apps(self._store)

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

    def _check_state(self, repo: str, sha: str) -> tuple[bool, str, list[str]]:
        """``(done, conclusion, failed_apps)`` of the head's counted suites."""
        suites = counted_suites(self._suites(repo, sha), self.ignored_apps())
        if not suites:
            # Nothing counted (only ignored apps, or no suite listed yet) is not green: keep
            # waiting for a suite to appear; the timeout settles it as ``no_checks``.
            return False, NO_CHECKS, []
        done, green = suites_state(suites)
        return done, "success" if green else "failure", _failed_apps(suites)

    def on_check(self, data: Mapping[str, Any]) -> str:
        """Handle one check-completion event's data; return what happened:
        ``emitted``, ``duplicate``, ``pending``, ``ignored`` or ``error`` (listing failed; the
        pending record is already persisted, so :meth:`tick` retries and the timeout fires)."""
        repo, sha = data.get("repository"), data.get("head_sha")
        if not isinstance(repo, str) or not repo or not isinstance(sha, str) or not sha:
            return "ignored"
        gen = _generation(self._store.get(SETTLE_COLLECTION, f"{repo}@{sha}".lower()))
        emitted = self._store.get(EVENTS_COLLECTION, settled_event_id(repo, sha, gen))
        if emitted is not None:
            return self._late(repo, sha, data, emitted)
        rec = self._arm(repo, sha, data)  # before the lookup: a failure must not lose the SHA
        try:
            done, conclusion, failed = self._check_state(repo, sha)
        except GitHubError as exc:
            log.warning("checks settle: suite listing failed (%s)", exc.code)
            return "error"
        if done and self._now() >= self._window_end(rec):
            try:
                return self._emit(repo, sha, rec, "all_completed", conclusion, failed)
            except _PullDeferred:
                return "pending"  # the node's tick emits it, with the PR facts
        return "pending"

    def _late(
        self, repo: str, sha: str, data: Mapping[str, Any], emitted: Mapping[str, Any]
    ) -> str:
        """A completion for a settled SHA. A counted app's failure the settled event does not
        name becomes a late *candidate* (:data:`LATE_COLLECTION`), confirmed at once by
        :meth:`_try_late`: ``late`` (emitted), ``pending`` (the confirmation failed; the
        tick retries it) or ``duplicate`` (not late, no longer failing, or already
        emitted)."""
        app = str(data.get("app_slug") or "").casefold()
        failed = data.get("status") == "completed" and data.get("conclusion") == "failure"
        if not app or not failed or app in self.ignored_apps():
            return "duplicate"
        prior = (emitted.get("envelope") or {}).get("data") or {}
        if app in [a for a in prior.get("failed_apps") or () if isinstance(a, str)]:
            return "duplicate"
        candidate = {
            "id": late_event_id(repo, sha, app),
            "repository": repo,
            "head_sha": sha,
            "app": app,
            "settled_id": emitted.get("id"),
            "noted_at": _iso(self._now()),
            "token": secrets.token_hex(16),
        }
        candidate = self._note_late(candidate)
        if not self._serves_here(repo):
            return "pending"  # a node that can read the repo's checks confirms it
        return self._try_late(candidate)

    def _note_late(self, fresh: dict[str, Any]) -> dict[str, Any]:
        """Insert the candidate, or give the stored one a new random ``token`` (and
        ``noted_at``) by compare-and-set on the token read. Every insert and every refresh
        draws a fresh token, so it never repeats over the candidate's life - dropped and
        noted again included - and a confirmer that read an older token can no longer drop
        it (no ABA). Returns the candidate as now stored."""
        for _ in range(10):
            try:
                return dict(self._store.insert(LATE_COLLECTION, fresh))
            except DuplicateKeyError:
                pass
            current = self._store.get(LATE_COLLECTION, fresh["id"])
            if current is None:
                fresh = {**fresh, "token": secrets.token_hex(16)}
                continue  # dropped meanwhile: insert again, with a new token
            refreshed = {"token": secrets.token_hex(16), "noted_at": fresh["noted_at"]}
            expected = {"token": current.get("token")}
            if self._store.update_if(LATE_COLLECTION, fresh["id"], expected, refreshed).won:
                return {**current, **refreshed}
        return dict(self._store.get(LATE_COLLECTION, fresh["id"]) or fresh)

    def _drop_late(self, candidate: Mapping[str, Any]) -> bool:
        """Delete the candidate iff it still carries the ``token`` it was read with (one
        transaction): a failure noted meanwhile - a refresh, or a drop and a new insert -
        keeps it."""

        def drop(tx: Any) -> bool:
            current = tx.get(LATE_COLLECTION, candidate["id"])
            if current is None or current.get("token") != candidate.get("token"):
                return False
            tx.delete(LATE_COLLECTION, candidate["id"])
            return True

        return run_transaction(self._store, drop)

    def _try_late(self, candidate: Mapping[str, Any]) -> str:
        """Confirm a late candidate from the App's *current* listing - the app's suite for
        the head still concluded ``failure`` - and emit its event; no clock decides it. The
        candidate (as read, by its ``token``) is dropped once decided; a failed listing,
        or a failure noted meanwhile (a new token), keeps it (``pending``)."""
        repo, sha, app = candidate["repository"], candidate["head_sha"], candidate["app"]
        try:
            failing = self._still_failing(repo, sha, app)
        except GitHubError as exc:
            log.warning("checks settle: late confirmation failed (%s); retried", exc.code)
            return "pending"
        emitted = self._store.get(EVENTS_COLLECTION, str(candidate.get("settled_id")))
        if failing and emitted is not None:
            outcome = self._confirm_late(candidate, self._late_event(repo, sha, app, emitted))
        else:
            outcome = "duplicate" if self._drop_late(candidate) else "stale"
        if outcome != "stale":
            return outcome
        # decided meanwhile: a newer failure keeps it (confirmed later), or another node
        # already dropped or emitted it
        exists = self._store.get(LATE_COLLECTION, candidate["id"]) is not None
        return "pending" if exists else "duplicate"

    def _still_failing(self, repo: str, sha: str, app: str) -> bool:
        return any(
            str(s.get("app_slug") or "").casefold() == app
            and s.get("status") == "completed"
            and s.get("conclusion") == "failure"
            for s in self._suites(repo, sha)
        )

    def _late_event(
        self, repo: str, sha: str, app: str, emitted: Mapping[str, Any]
    ) -> dict[str, Any]:
        """The ``events`` document of ``app``'s late failure on ``repo@sha`` (fixed id)."""
        prior = dict((emitted.get("envelope") or {}).get("data") or {})
        named = [a for a in prior.get("failed_apps") or () if isinstance(a, str)]
        payload = {
            **prior,
            "conclusion": "failure",
            "settled_by": "late",
            "failed_apps": sorted({*named, app}),
            "late_app": app,
        }
        envelope = derive_envelope(
            None, type=LATE_TYPE, source=SOURCE, data=payload, id=late_event_id(repo, sha, app)
        )
        return event_document(envelope, host=SETTLE_HOST, received_at=self._now())

    def _confirm_late(self, candidate: Mapping[str, Any], event: Mapping[str, Any]) -> str:
        """Emit and drop in ONE transaction, only while the candidate still carries the
        ``token`` it was read with: ``late`` (inserted), ``duplicate`` (the event existed;
        dropped all the same) or ``stale`` (the candidate changed or is gone: nothing
        written), so a stale failing listing never emits after a newer decision."""

        def confirm(tx: Any) -> str:
            current = tx.get(LATE_COLLECTION, candidate["id"])
            if current is None or current.get("token") != candidate.get("token"):
                return "stale"
            outcome = "duplicate"
            if tx.get(EVENTS_COLLECTION, event["id"]) is None:
                tx.insert(EVENTS_COLLECTION, event)
                outcome = "late"
            tx.delete(LATE_COLLECTION, candidate["id"])
            return outcome

        try:
            return run_transaction(self._store, confirm)
        except DuplicateKeyError:
            return "stale"  # emitted concurrently: the next confirmation drops it

    def _late_candidates(self) -> int:
        """Retry the late candidates this node can serve; drop those past the recovery
        window. Returns how many late events it emitted. A store error propagates, as for
        the pending polls (the cycle records it and the next tick retries)."""
        emitted = 0
        floor = self._now() - timedelta(seconds=RECOVERY_WINDOW_S)
        for doc in self._store.find(LATE_COLLECTION):
            noted = _parse(doc.get("noted_at"))
            if noted is None or noted < floor:
                self._drop_late(doc)  # only as read: a refresh meanwhile keeps it
            elif self._serves_here(doc.get("repository")):
                emitted += self._try_late(doc) == "late"
        return emitted

    def tick(self) -> int:
        """Settle pending SHAs: emit ``timeout`` past the deadline, or ``all_completed`` once
        the minimum window has passed and every counted suite is complete."""
        self._recover(self._now())
        self._late_candidates()
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
            done, conclusion, failed = self._polled_state(repo, sha)
            verdict = _settle_verdict(done, conclusion, timed_out)
            if verdict is None:
                continue
            if self._emit(repo, sha, rec, *verdict, failed) == "emitted":
                emitted += 1
        return emitted

    def _polled_state(self, repo: str, sha: str) -> tuple[bool, str, list[str]]:
        """:meth:`_check_state` for the tick: a failed listing is not done (``timeout``), so
        the timeout fires regardless."""
        try:
            return self._check_state(repo, sha)
        except GitHubError as exc:
            log.warning("checks settle: suite listing failed (%s)", exc.code)
            return False, "timeout", []  # the timeout fires regardless

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
        rec = self._store.get(SETTLE_COLLECTION, f"{repo}@{sha}".lower())
        if rec is not None:
            self._recover_late(repo, sha, event, rec)
            return 0  # armed by the webhook (or settled by completion or timeout)
        if self._store.get(EVENTS_COLLECTION, settled_event_id(repo, sha)) is not None:
            return 0
        received = _parse(event.get("received_at"))
        at = now if received is None else min(received, now)
        self._arm(repo, sha, data, at=at)
        log.info("checks settle: recovered an unarmed SHA from a stored completion")
        return 1

    def _recover_late(
        self, repo: str, sha: str, event: Mapping[str, Any], rec: Mapping[str, Any]
    ) -> None:
        """A stored completion of a settled SHA that the webhook never turned into a late
        candidate (d25). Clocks only bound the candidates: one received up to
        :data:`LATE_SKEW_MARGIN_S` before the settled event (the webhook server's clock may
        lag the settler's) is noted too; :meth:`_try_late` decides from the App's current
        listing, so a failure re-run green before the settle is never late."""
        emitted = self._store.get(EVENTS_COLLECTION, settled_event_id(repo, sha, _generation(rec)))
        received = _parse(event.get("received_at"))
        settled_at = _parse((emitted or {}).get("received_at"))
        if received is None or settled_at is None:
            return  # not settled yet
        if received < settled_at - timedelta(seconds=LATE_SKEW_MARGIN_S):
            return  # long before the settle: not late
        if self._late(repo, sha, event["envelope"]["data"], emitted) == "late":
            log.info("checks settle: recovered a late failure from a stored completion")

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
            # the generation too (missing = legacy 0): a stale snapshot claims nothing
            {
                "next_poll_at": rec.get("next_poll_at"),
                "state": "pending",
                "generation": rec.get("generation"),
            },
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
            changes = _arm_changes(rec, data, numbers)
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
        except GitHubError as exc:
            if self._defer_slow_pull and exc.retryable:
                raise _PullDeferred from exc
            return {}
        except Exception:  # noqa: BLE001 - enrichment must never block the settle
            return {}
        if facts is None:
            return {}
        facts.pop("head_sha", None)
        return facts

    def _emit(
        self,
        repo: str,
        sha: str,
        src: Mapping[str, Any],
        settled_by: str,
        conclusion: str,
        failed_apps: list[str] | None = None,
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
            "failed_apps": list(failed_apps or ()),
        }
        for key, value in self._enrich(repo, numbers).items():
            if key != "head_branch" or not payload.get("head_branch"):
                payload[key] = value
        envelope = derive_envelope(
            None,
            type=SETTLED_TYPE,
            source=SOURCE,
            data=payload,
            id=settled_event_id(repo, sha, _generation(src)),
        )
        try:
            doc = event_document(envelope, host=SETTLE_HOST, received_at=self._now())
            self._store.insert(EVENTS_COLLECTION, doc)
            outcome = "emitted"
        except DuplicateKeyError:
            outcome = "duplicate"
        self._store.update_if(
            SETTLE_COLLECTION,
            f"{repo}@{sha}".lower(),
            # only the generation this emit belongs to: a delayed emitter of an older one
            # (its insert a duplicate) must never mark a re-armed generation emitted
            {"state": "pending", "generation": src.get("generation")},
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

    def list_suites(
        self, repo: str, sha: str, *, timeout_s: float | None = None
    ) -> list[dict[str, Any]]:
        """Every check suite of ``repo@sha`` (read-only ``Checks: read``); ``timeout_s``
        bounds the whole lookup, paging included, as for :meth:`get_pull`."""
        if timeout_s is None:
            return self._app_for(repo).list_check_suites(repo, sha)
        return self._bounded(repo, lambda app: app.list_check_suites(repo, sha), timeout_s)

    def list_open_pulls_page(
        self, repo: str, page: int, *, timeout_s: float
    ) -> list[dict[str, Any]]:
        """One page of the open PRs of ``repo`` (read-only ``Pull requests: read``; d31's
        conflict watch), the whole lookup bounded by ``timeout_s`` as for :meth:`get_pull`."""
        return self._bounded(repo, lambda app: app.list_open_pulls_page(repo, page), timeout_s)

    def comment_reactions(
        self, repo: str, comment_id: int, *, timeout_s: float
    ) -> list[dict[str, Any]]:
        """The 👎 reactions on issue comment ``comment_id`` of ``repo`` (read-only; d34's
        reaction watch), the whole lookup bounded by ``timeout_s`` as for :meth:`get_pull`."""
        return self._bounded(
            repo, lambda app: app.list_comment_reactions(repo, comment_id), timeout_s
        )

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
        return self._bounded(repo, lambda app: app.get_pull(repo, number), timeout_s)

    def _bounded(self, repo: str, read: Callable[[GitHubApp], Any], timeout_s: float) -> Any:
        """``read`` through ``repo``'s App on a capped worker, the whole lookup bounded by
        ``timeout_s`` (see :meth:`get_pull`). No time left: ``deadline_exceeded`` at once."""
        if timeout_s <= 0:
            raise GitHubError("deadline_exceeded", retryable=True)
        deadline = datetime.now(UTC) + timedelta(seconds=timeout_s)
        if not self._lookup_slots.acquire(blocking=False):
            raise GitHubError("lookup_busy", "every lookup worker is busy", retryable=True)
        result: Future[Any] = Future()

        def work() -> None:
            try:
                app = self._app_for(repo)
                with app.deadline(deadline):
                    result.set_result(read(app))
            except Exception as exc:  # handed to the waiting caller
                result.set_exception(exc)
            except BaseException as exc:  # e.g. SystemExit: hand it over, then end the thread
                result.set_exception(exc)
                raise
            finally:
                self._lookup_slots.release()

        try:
            threading.Thread(target=work, name="github-lookup", daemon=True).start()
        except BaseException:
            self._lookup_slots.release()
            raise
        try:
            return result.result(timeout=timeout_s)
        except FutureTimeout:
            raise GitHubError("deadline_exceeded", retryable=True) from None


WEBHOOK_SETTLE_BUDGET_S = 5.0
"""The time one webhook delivery's settle may spend on GitHub (suite listing and PR read
together, secret resolve included), like the PR-comment lookup on the same route - well
inside GitHub's 10 s delivery timeout."""


def webhook_on_check(
    store: StoragePort, lister: Any, *, budget_s: float = WEBHOOK_SETTLE_BUDGET_S
) -> Callable[[Mapping[str, Any]], str]:
    """The API server's ``on_check``: :meth:`ChecksSettler.on_check` with every GitHub read
    bounded by one per-delivery budget of ``budget_s`` (``lister`` is an
    :class:`AppSuiteLister`). A suite listing past it answers ``error`` (the SHA stays
    armed); a PR read past it (or with every lookup worker busy) leaves the SHA pending
    rather than emit without the PR facts. Either way the node's tick settles it - the
    webhook answers quickly."""

    def on_check(data: Mapping[str, Any]) -> str:
        end = time.monotonic() + budget_s

        def left() -> float:
            return end - time.monotonic()

        settler = ChecksSettler(
            store,
            lambda repo, sha: lister.list_suites(repo, sha, timeout_s=left()),
            pull=lambda repo, number: lister.get_pull(repo, number, timeout_s=left()),
            defer_slow_pull=True,
        )
        return settler.on_check(data)

    return on_check
