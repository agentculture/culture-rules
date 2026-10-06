"""Once-per-SHA settle: one ``github.pr.checks_settled`` event per head SHA.

A GitHub check-completion event (``github.checks.suite_completed`` /
``github.checks.workflow_completed``) calls :meth:`ChecksSettler.on_check`. The settler lists
the head SHA's check suites (through the injectable ``suites`` seam; the production seam is
:class:`AppSuiteLister`, the GitHub App's read-only ``Checks: read``), drops suites from apps in
the shared variable ``ignored_check_apps``, and:

- every remaining suite ``completed`` -> emit ``github.pr.checks_settled`` with
  ``settled_by: "all_completed"``;
- otherwise persist a pending settle record whose ``deadline`` is ``now +
  checks_settle_timeout_s``; :meth:`ChecksSettler.tick` (run from the node cycle) re-reads the
  suites once it is due and emits with ``settled_by: "timeout"`` if some are still running.
  The deadline lives in the store, so a node restart loses nothing.

Variables (read each call through ``store.get_variable``; an absent, mistyped or non-positive
value falls back to a stated default): ``ignored_check_apps`` defaults to ``["claude"]`` and
``checks_settle_timeout_s`` to :data:`DEFAULT_TIMEOUT_S`.

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

    # ------------------------------------------------------------------ decisions

    def _all_completed(self, repo: str, sha: str) -> bool:
        ignored = self.ignored_apps()
        suites = self._suites(repo, sha)
        return all(
            s.get("status") == "completed"
            for s in suites
            if str(s.get("app_slug") or "").casefold() not in ignored
        )

    def on_check(self, data: Mapping[str, Any]) -> str:
        """Handle one check-completion event's data; return what happened:
        ``emitted``, ``duplicate``, ``pending``, ``ignored`` or ``error`` (listing failed;
        the next completion or the redelivery retries)."""
        repo, sha = data.get("repository"), data.get("head_sha")
        if not isinstance(repo, str) or not repo or not isinstance(sha, str) or not sha:
            return "ignored"
        if self._store.get(EVENTS_COLLECTION, settled_event_id(repo, sha)) is not None:
            return "duplicate"
        try:
            done = self._all_completed(repo, sha)
        except GitHubError as exc:
            log.warning("checks settle: suite listing failed (%s)", exc.code)
            return "error"
        if done:
            return self._emit(repo, sha, data, "all_completed")
        self._arm(repo, sha, data)
        return "pending"

    def tick(self) -> int:
        """Emit the settled event of every pending SHA whose settle timeout has passed."""
        now = self._clock()
        emitted = 0
        for rec in self._store.find(SETTLE_COLLECTION, {"state": "pending"}):
            deadline = _parse(rec.get("deadline"))
            if deadline is None or now < deadline:
                continue
            repo, sha = rec["repository"], rec["head_sha"]
            try:
                done = self._all_completed(repo, sha)
            except GitHubError as exc:
                log.warning("checks settle: suite listing failed (%s)", exc.code)
                done = False  # the timeout fires regardless of what is listed
            by = "all_completed" if done else "timeout"
            if self._emit(repo, sha, rec, by) == "emitted":
                emitted += 1
        return emitted

    # ------------------------------------------------------------------ persistence

    def _arm(self, repo: str, sha: str, data: Mapping[str, Any]) -> None:
        """Record the SHA as pending with a deadline; the first completion's deadline stands."""
        rid = f"{repo}@{sha}".lower()
        doc = {
            "id": rid,
            "state": "pending",
            "repository": repo,
            "head_sha": sha,
            "head_branch": data.get("head_branch"),
            "pr_numbers": list(data.get("pr_numbers") or ()),
            "number": data.get("number"),
            "deadline": _iso(self._clock() + timedelta(seconds=self.timeout_s())),
        }
        try:
            self._store.insert(SETTLE_COLLECTION, doc)
        except DuplicateKeyError:
            pass

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

    def _emit(self, repo: str, sha: str, src: Mapping[str, Any], settled_by: str) -> str:
        numbers = [n for n in src.get("pr_numbers") or () if isinstance(n, int)]
        payload: dict[str, Any] = {
            "repository": repo,
            "head_sha": sha,
            "head_branch": src.get("head_branch"),
            "pr_numbers": numbers,
            "number": numbers[0] if numbers else src.get("number"),
            "settled_by": settled_by,
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
