"""Conflict watch (d31): one ``github.pr.conflicting`` event per PR head and base pair.

GitHub sends no webhook when a PR turns CONFLICTING: its base branch moves, its head does
not, so no check completes and nothing settles. :meth:`ConflictWatcher.tick` (run from the
node cycle) therefore looks, at most once every ``conflict_watch_interval_s`` seconds (a
variable; default :data:`DEFAULT_INTERVAL_S`), at the open PRs of each repository in the
shared variable ``fixer_repos`` (less ``fixer_excluded_repos``) that this node can read
through its GitHub App actor (the ``serves`` seam, as for the checks settle). It lists the
open PRs (``list_pulls``) and reads each one (``get_pull``) for GitHub's ``mergeable``; a PR
GitHub reports ``mergeable: false`` with ``mergeable_state: "dirty"`` (a conflict with its
base) emits :data:`~culture_rules.events.emit.PR_CONFLICTING_TYPE` carrying the PR facts
(:func:`~culture_rules.apps.github.complete_pr_facts`: ``head_sha``, ``head_branch``,
``head_repo``, ``base_repo``, ``base_branch``, ``base_sha``, ``draft``, ``pr_author``,
``state``) plus ``repository``, ``number``, ``pr_numbers`` and ``mergeable_state``. A PR
whose facts are incomplete is skipped. GitHub computes ``mergeable`` lazily (``null`` on
the first read), so a fresh conflict is seen at the next sweep.

The event id is a hash of ``repo#number@head:base`` (:func:`conflict_event_id`), so the
unique id of the ``events`` collection emits it once per head and base pair, across nodes
and sweeps: the same conflict is never requested twice, and a new base (or a new head that
is still conflicting) is a new request. The ``pr-fixer-conflict`` rule turns it into a
fixer request, like a checks settle does. The type and the ``conflict_`` id prefix are
reserved at external ingest (:func:`~culture_rules.events.emit.reserved_reason`).

Read-only towards GitHub; a failed listing or read skips that repository or PR until the
next sweep and never stops the others. Standard-library only.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.apps.github import GitHubError, complete_pr_facts
from culture_rules.events.emit import CONFLICT_ID_PREFIX, PR_CONFLICTING_TYPE, derive_envelope
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.store.port import DuplicateKeyError, StoragePort

log = logging.getLogger(__name__)

__all__ = [
    "CONFLICT_HOST",
    "ConflictWatcher",
    "DEFAULT_INTERVAL_S",
    "INTERVAL_VARIABLE",
    "SOURCE",
    "conflict_event_id",
]

DEFAULT_INTERVAL_S = 600.0
INTERVAL_VARIABLE = "conflict_watch_interval_s"
CONFLICT_HOST = "conflict-watch"
SOURCE = "culture-rules://conflict-watch"


def conflict_event_id(repo: str, number: int, head_sha: str, base_sha: str) -> str:
    """The deterministic id of one PR head and base pair's conflict event."""
    key = f"{repo.lower()}#{int(number)}@{head_sha}:{base_sha}"
    return CONFLICT_ID_PREFIX + hashlib.sha256(key.encode()).hexdigest()[:40]


def _var(store: StoragePort, name: str) -> Any:
    doc = store.get_variable(name)
    return None if doc is None else doc.get("value")


def _repos(store: StoragePort, name: str) -> list[str]:
    value = _var(store, name)
    if not isinstance(value, list):
        return []
    return [r for r in value if isinstance(r, str) and "/" in r]


class ConflictWatcher:
    """See the module docstring. ``list_pulls(repo)`` and ``get_pull(repo, number)`` read
    through the repo's App; ``serves(repo)`` says whether this node can."""

    def __init__(
        self,
        store: StoragePort,
        list_pulls: Callable[[str], list[Mapping[str, Any]]],
        get_pull: Callable[[str, int], Mapping[str, Any]],
        *,
        serves: Callable[[str], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._list = list_pulls
        self._get = get_pull
        self._serves = serves
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last: datetime | None = None

    def interval_s(self) -> float:
        value = _var(self._store, INTERVAL_VARIABLE)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return DEFAULT_INTERVAL_S
        return float(value)

    def tick(self) -> int:
        """One sweep when it is due; returns the number of events emitted."""
        now = self._clock()
        if self._last is not None and (now - self._last).total_seconds() < self.interval_s():
            return 0
        self._last = now
        excluded = {r.lower() for r in _repos(self._store, "fixer_excluded_repos")}
        emitted = 0
        for repo in _repos(self._store, "fixer_repos"):
            if repo.lower() in excluded:
                continue
            if self._serves is not None and not self._serves(repo):
                continue
            emitted += self._sweep(repo, now)
        return emitted

    def _sweep(self, repo: str, now: datetime) -> int:
        try:
            pulls = self._list(repo)
        except GitHubError as exc:
            log.info("conflict watch: listing %s failed (%s)", repo, exc.code)
            return 0
        emitted = 0
        for listed in pulls:
            number = listed.get("number")
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                continue
            try:
                pull = dict(self._get(repo, number))
            except GitHubError as exc:
                log.info("conflict watch: reading %s#%s failed (%s)", repo, number, exc.code)
                continue
            if self._emit(repo, number, pull, now):
                emitted += 1
        return emitted

    def _emit(self, repo: str, number: int, pull: Mapping[str, Any], now: datetime) -> bool:
        if pull.get("mergeable") is not False or pull.get("mergeable_state") != "dirty":
            return False
        facts = complete_pr_facts(dict(pull))
        if facts is None or facts.get("state") != "open":
            return False
        payload = {
            **facts,
            "repository": repo,
            "number": number,
            "pr_numbers": [number],
            "mergeable_state": "dirty",
        }
        envelope = derive_envelope(
            None,
            type=PR_CONFLICTING_TYPE,
            source=SOURCE,
            data=payload,
            id=conflict_event_id(repo, number, facts["head_sha"], facts["base_sha"]),
        )
        try:
            self._store.insert(
                EVENTS_COLLECTION, event_document(envelope, host=CONFLICT_HOST, received_at=now)
            )
        except DuplicateKeyError:
            return False
        log.info("conflict watch: %s#%s conflicts with its base", repo, number)
        return True
