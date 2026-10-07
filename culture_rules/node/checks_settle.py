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

The event also carries ``conclusion``: ``"success"`` when every counted (non-ignored) suite
concluded ``success``, ``neutral`` or ``skipped`` (vacuously so with none counted),
``"timeout"`` when settled by the timeout, else ``"failure"``. A ``"success"`` is the explicit
green signal that resets a rule's attempt budget for the PR (:mod:`culture_rules.node.firing`,
"Concurrency keys").

Variables (read each call through ``store.get_variable``; an absent, mistyped or non-positive
value falls back to a stated default): ``ignored_check_apps`` defaults to ``["claude"]`` and
``checks_settle_timeout_s`` to :data:`DEFAULT_TIMEOUT_S` and ``checks_settle_min_s`` (the
minimum window before ``all_completed``, so a slower app's suite can appear) to
:data:`DEFAULT_MIN_S`.

Deduplication is durable: the event id is a hash of ``repo@sha``, so the unique id of the
``events`` collection makes two nodes, a redelivered webhook or the timeout racing the last
completion emit exactly once. The settle record (``checks_settle`` collection) only carries the
deadline and the outcome; the event insert is the authority.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.actors.secrets import resolve as resolve_secret
from culture_rules.apps.github import DEFAULT_API_BASE, GitHubApp, GitHubError, Transport
from culture_rules.events.emit import derive_envelope
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.node.actions.github import GitHubCommentPort
from culture_rules.store.port import DuplicateKeyError, StoragePort

__all__ = [
    "CHECK_TYPES",
    "DEFAULT_IGNORED_APPS",
    "DEFAULT_MIN_S",
    "DEFAULT_TIMEOUT_S",
    "SETTLED_TYPE",
    "SETTLE_COLLECTION",
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
POLL_BASE_S = 15.0
POLL_CAP_S = 120.0
SETTLE_HOST = "checks-settle"
SOURCE = "culture-rules://checks-settle"

SuiteLister = Callable[[str, str], list[dict[str, Any]]]
"""``(repo, sha) -> [{app_slug, status, conclusion}]``; may raise :class:`GitHubError`."""
PullLookup = Callable[[str, int], Mapping[str, Any]]
"""``(repo, number) -> the pull request document`` (optional enrichment)."""


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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._suites = suites
        self._pull = pull
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
        now = self._now()
        emitted = 0
        for rec in self._store.find(SETTLE_COLLECTION, {"state": "pending"}):
            deadline = _parse(rec.get("deadline"))
            if deadline is None:
                continue
            timed_out = now >= deadline
            if not timed_out and now < self._window_end(rec):
                continue
            if not self._claim_poll(rec, now, deadline):
                continue  # not due yet, or another node holds this interval's poll
            repo, sha = rec["repository"], rec["head_sha"]
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

    def _arm(self, repo: str, sha: str, data: Mapping[str, Any]) -> Mapping[str, Any]:
        """Persist the SHA as pending (first deadline and arm time stand) and merge in any PR
        facts this completion newly carries. Returns the stored record."""
        rid = f"{repo}@{sha}".lower()
        now = self._now()
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
        """Best-effort PR facts the fixer rule's condition reads; any failure omits them."""
        if self._pull is None or not numbers:
            return {}
        try:
            pr = self._pull(repo, numbers[0])
        except Exception:  # noqa: BLE001 - enrichment must never block the settle
            return {}
        head, base = pr.get("head") or {}, pr.get("base") or {}
        return {
            "head_repo": (head.get("repo") or {}).get("full_name"),
            "base_repo": (base.get("repo") or {}).get("full_name"),
            "base_branch": base.get("ref"),
            "draft": bool(pr.get("draft")),
            "pr_author": (pr.get("user") or {}).get("login"),
        }

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
        payload.update(self._enrich(repo, numbers))
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


class AppSuiteLister(GitHubCommentPort):
    """The production ``suites`` / ``pull`` seams: reads through the GitHub app actor whose
    ``connection.repos`` allowlist holds the repository (first match, enabled actors only)."""

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secrets: Callable[[str], str] | None = None,
        api_base: str = DEFAULT_API_BASE,
    ) -> None:
        super().__init__(
            store, transport=transport, secrets=secrets or resolve_secret, api_base=api_base
        )

    def _app_for(self, repo: str) -> GitHubApp:
        for doc in self._store.find("actors"):
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

    def get_pull(self, repo: str, number: int) -> Mapping[str, Any]:
        return self._app_for(repo).get_pull(repo, number)
