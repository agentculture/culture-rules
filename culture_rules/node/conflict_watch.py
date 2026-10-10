"""Conflict watch (d31): one ``github.pr.conflicting`` event per PR head and base pair.

GitHub sends no webhook when a PR turns CONFLICTING: its base branch moves, its head does
not, so no check completes and nothing settles. :meth:`ConflictWatcher.tick` (run from the
node cycle) therefore looks, at most once every ``conflict_watch_interval_s`` seconds (a
variable; default :data:`DEFAULT_INTERVAL_S`), at the open PRs of each repository in the
shared variable ``fixer_repos`` (less ``fixer_excluded_repos``) that this node can read
through its GitHub App actor: the node finds that out inside the bounded listing itself
(``repo_not_allowed``), so even a cold secret resolve stays within the budget below; an
optional ``serves`` seam skips a repository before any request. A sweep lists
the open PRs one page at a time (``list_page``, 100 a page) and queues the same-repo,
non-draft ones, then reads each (``get_pull``) for GitHub's ``mergeable``. Listing pages and
reads share one budget per node cycle: at most :data:`REQUESTS_PER_TICK` requests within
:data:`TICK_BUDGET_S` seconds, each bounded by the time left (at most
:data:`REQUEST_TIMEOUT_S`), so a slow or failing GitHub never holds the node's cycle. What
is left - pages and PRs alike - carries over to the next cycle, and a new sweep starts only
once the last one is done. A request refused for rate (``http_403`` / ``http_429``) drops
the rest of that repository's sweep until the next one. A PR GitHub reports
``mergeable: false`` with ``mergeable_state: "dirty"`` (a conflict with its base), still
same-repo and not a draft, emits :data:`~culture_rules.events.emit.PR_CONFLICTING_TYPE`
carrying the PR facts
(:func:`~culture_rules.apps.github.complete_pr_facts`: ``head_sha``, ``head_branch``,
``head_repo``, ``base_repo``, ``base_branch``, ``base_sha``, ``draft``, ``pr_author``,
``state``) plus ``repository``, ``number``, ``pr_numbers`` and ``mergeable_state``. A PR
whose facts are incomplete is skipped. GitHub computes ``mergeable`` lazily (``null`` on
the first read), so a fresh conflict is seen at the next sweep.

The event id is a hash of ``repo#number@head:base`` (:func:`conflict_event_id`), so the
unique id of the ``events`` collection emits it once per head and base pair, across nodes
and sweeps: the same conflict is never requested twice, and a new base (or a new head that
is still conflicting) is a new request.

Once means once **heard** (d39, #44). A pair is emitted again at a later sweep as the next
generation (``generation`` in the id; generation 0 is the d31 id) only when:

* no stored, not deleted rule triggered by
  :data:`~culture_rules.events.emit.PR_CONFLICTING_TYPE` consumed *any* of its stored
  generations: no firing intent and no recorded decision (``condition_false``,
  ``disabled`` and ``paused`` are never recorded);
* its last generation was **evaluated**: the fire marker (``event_fires``) of the trigger
  consumer that decides each enabled listener exists - the shared consumer for an
  unplaced rule, ``triggers@<machine>`` for a rule placed on a machine. The marker commits
  with the decisions, so what that evaluation decided is final; an event still waiting
  for its first poll, or deferred for a drained or offline host, is pending and is never
  sent twice. A rule placed by actor or requirement has no consumer to name, so its pair
  is not requested again;
* at least one interval has passed since the last was written, such a rule is enabled and
  the engine is not paused (a trigger event evaluated during a pause is dropped);

and at most :data:`MAX_GENERATIONS` events per pair. The listeners and the pause flag are
read once per node cycle. A conflict seen while
``pr-fixer-conflict`` was disabled (a bundle import lands every rule disabled) or the engine
paused is so requested once it can be heard. The check reads the store only, never GitHub,
so the request budget below is unchanged. The ``pr-fixer-conflict`` rule turns it into a
fixer request, like a checks settle does. The type and the ``conflict_`` id prefix are
reserved at external ingest (:func:`~culture_rules.events.emit.reserved_reason`).

Read-only towards GitHub; a failed listing or read skips that repository or PR until the
next sweep and never stops the others. Standard-library only.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import deque
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
    "MAX_GENERATIONS",
    "REQUESTS_PER_TICK",
    "REQUEST_TIMEOUT_S",
    "TICK_BUDGET_S",
    "SOURCE",
    "conflict_event_id",
]

DEFAULT_INTERVAL_S = 600.0
MAX_GENERATIONS = 3
"""d39: the most events one head and base pair is emitted as (the first and 2 re-requests)."""
REQUESTS_PER_TICK = 10
"""The most GitHub requests (listing pages and PR reads) one node cycle makes."""
TICK_BUDGET_S = 10.0
"""The longest one cycle's requests may run; the rest wait for the next cycle."""
REQUEST_TIMEOUT_S = 5.0
"""The bound on one request, secret resolve included (less when the budget is nearly spent)."""
_PAGE_SIZE = 100
_MAX_PAGES = 50
_RATE_LIMITED = frozenset({"http_403", "http_429"})
INTERVAL_VARIABLE = "conflict_watch_interval_s"
CONFLICT_HOST = "conflict-watch"
SOURCE = "culture-rules://conflict-watch"


def conflict_event_id(
    repo: str, number: int, head_sha: str, base_sha: str, *, generation: int = 0
) -> str:
    """The deterministic id of one PR head and base pair's conflict event; ``generation``
    (d39) numbers a pair's re-requests, and generation 0 keeps the d31 id."""
    key = f"{repo.lower()}#{int(number)}@{head_sha}:{base_sha}"
    if generation:
        key += f"/g{int(generation)}"
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
    """See the module docstring. ``list_page(repo, page, timeout_s)`` and
    ``get_pull(repo, number, timeout_s)`` read through the repo's App, each bounded by
    ``timeout_s``; ``serves(repo)`` says whether this node can."""

    def __init__(
        self,
        store: StoragePort,
        list_page: Callable[[str, int, float], list[Mapping[str, Any]]],
        get_pull: Callable[[str, int, float], Mapping[str, Any]],
        *,
        serves: Callable[[str], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._list = list_page
        self._get = get_pull
        self._serves = serves
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._last: datetime | None = None
        self._pages: deque[tuple[str, int]] = deque()  # (repo, page) still to list
        self._queue: deque[tuple[str, int]] = deque()  # (repo, number) still to read
        self._facts: tuple[list[Mapping[str, Any]], bool] | None = None

    def interval_s(self) -> float:
        value = _var(self._store, INTERVAL_VARIABLE)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return DEFAULT_INTERVAL_S
        return float(value)

    def tick(self) -> int:
        """Start a sweep when one is due and the last is done, then spend this cycle's
        budget on it (PR reads first, then listing pages); returns the events emitted."""
        now = self._clock()
        self._facts = None  # d39: listeners and the pause flag, read once per cycle
        due = self._last is None or (now - self._last).total_seconds() >= self.interval_s()
        if not (self._queue or self._pages) and due:
            self._last = now
            self._pages.extend((repo, 1) for repo in self._repos())
        return self._spend(now)

    def _repos(self) -> list[str]:
        excluded = {r.lower() for r in _repos(self._store, "fixer_excluded_repos")}
        return [r for r in _repos(self._store, "fixer_repos") if r.lower() not in excluded]

    def _spend(self, now: datetime) -> int:
        started = self._monotonic()
        emitted = requests = 0
        while (self._queue or self._pages) and requests < REQUESTS_PER_TICK:
            if self._queue:
                left = TICK_BUDGET_S - (self._monotonic() - started)
                if left <= 0:
                    break
                repo, number = self._queue.popleft()
                requests += 1
                emitted += self._read(repo, number, min(REQUEST_TIMEOUT_S, left), now)
                continue
            repo, page = self._pages[0]
            if page == 1 and self._serves is not None and not self._serves(repo):
                self._pages.popleft()
                continue  # not this node's repo: no request made
            left = TICK_BUDGET_S - (self._monotonic() - started)  # after any serves()
            if left <= 0:
                break
            self._pages.popleft()
            requests += 1
            self._list_page(repo, page, min(REQUEST_TIMEOUT_S, left))
        return emitted

    def _list_page(self, repo: str, page: int, timeout: float) -> None:
        try:
            pulls = self._list(repo, page, timeout)
        except GitHubError as exc:
            # repo_not_allowed: this node has no App for the repo (the node wires no
            # ``serves``: the bounded listing finds that out, secret resolve included)
            level = logging.DEBUG if exc.code == "repo_not_allowed" else logging.INFO
            log.log(level, "conflict watch: listing %s failed (%s)", repo, exc.code)
            self._drop(repo)
            return
        for listed in pulls:
            number = listed.get("number")
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                continue
            if listed.get("draft") is not False or not _same_repo(listed):
                continue  # never a request: skipped before any read (Codex #3)
            self._queue.append((repo, number))
        if len(pulls) >= _PAGE_SIZE and page < _MAX_PAGES:
            self._pages.append((repo, page + 1))

    def _read(self, repo: str, number: int, timeout: float, now: datetime) -> int:
        try:
            pull = dict(self._get(repo, number, timeout))
        except GitHubError as exc:
            log.info("conflict watch: reading %s#%s failed (%s)", repo, number, exc.code)
            if exc.code in _RATE_LIMITED:
                self._drop(repo)
            return 0
        return int(self._emit(repo, number, pull, now))

    def _drop(self, repo: str) -> None:
        """Forget the rest of ``repo``'s sweep (a failed listing, a rate limit)."""
        self._queue = deque(q for q in self._queue if q[0] != repo)
        self._pages = deque(p for p in self._pages if p[0] != repo)

    def _emit(self, repo: str, number: int, pull: Mapping[str, Any], now: datetime) -> bool:
        if pull.get("mergeable") is not False or pull.get("mergeable_state") != "dirty":
            return False
        facts = complete_pr_facts(dict(pull))
        if facts is None or facts.get("state") != "open" or facts.get("draft") is not False:
            return False
        if facts["head_repo"].lower() != facts["base_repo"].lower():
            return False  # a fork's PR is never the fixer's: no event to consume (Codex #3)
        payload = {
            **facts,
            "repository": repo,
            "number": number,
            "pr_numbers": [number],
            "mergeable_state": "dirty",
        }
        event_id = self._next_id(repo, number, facts["head_sha"], facts["base_sha"])
        if event_id is None:
            return False
        envelope = derive_envelope(
            None,
            type=PR_CONFLICTING_TYPE,
            source=SOURCE,
            data=payload,
            id=event_id,
        )
        try:
            self._store.insert(
                EVENTS_COLLECTION,
                event_document(envelope, host=CONFLICT_HOST, received_at=self._clock()),
            )
        except DuplicateKeyError:
            return False
        log.info("conflict watch: %s#%s conflicts with its base", repo, number)
        return True

    # ------------------------------------------------------------------ d39 delivery

    def _next_id(self, repo: str, number: int, head: str, base: str) -> str | None:
        """The id to emit this pair's conflict as, or ``None`` to emit nothing (d39).

        Generation 0 when none is stored. Otherwise the next generation only when no
        listener heard *any* stored generation, the last one was **evaluated** by the
        consumer of every enabled listener and is at least one interval old, a listener is
        enabled and the engine is not paused; ``None`` once :data:`MAX_GENERATIONS` are
        stored. Store reads only: no GitHub request."""
        stored: list[Mapping[str, Any]] = []
        for gen in range(MAX_GENERATIONS):
            event_id = conflict_event_id(repo, number, head, base, generation=gen)
            doc = self._store.get(EVENTS_COLLECTION, event_id)
            if doc is None:
                return event_id if not stored or self._redeliver(stored) else None
            stored.append(doc)
        return None

    def _redeliver(self, stored: list[Mapping[str, Any]]) -> bool:
        """Whether a pair whose events are ``stored`` (oldest first) is requested again."""
        last = stored[-1]
        sent = _parse(last.get("received_at") or "")
        if sent is None or (self._clock() - sent).total_seconds() < self.interval_s():
            return False
        listeners, paused = self._sweep_facts()
        enabled = [r for r in listeners if r.get("enabled") is True]
        if not enabled or paused:
            return False  # nobody would hear it now either: wait, keep the generation
        if any(self._heard(str(doc.get("id") or ""), listeners) for doc in stored):
            return False  # a consumed pair is never requested again, whichever generation
        return all(self._evaluated(str(last.get("id") or ""), rule) for rule in enabled)

    def _sweep_facts(self) -> tuple[list[Mapping[str, Any]], bool]:
        """The listeners (stored, not deleted rules triggered by
        :data:`PR_CONFLICTING_TYPE`) and the engine's pause flag, read once per cycle."""
        if self._facts is None:
            from culture_rules.engine.runs import RULES_COLLECTION, is_paused  # noqa: PLC0415

            listeners = [
                r
                for r in self._store.find(RULES_COLLECTION)
                if not r.get("deleted_at")
                and ((r.get("trigger") or {}).get("params") or {}).get("type")
                == PR_CONFLICTING_TYPE
            ]
            self._facts = (listeners, is_paused(self._store))
        return self._facts

    def _heard(self, event_id: str, listeners: list[Mapping[str, Any]]) -> bool:
        """Whether any listener consumed ``event_id``: a firing intent, or a recorded
        decision (a skip such as ``deduplicated`` or a waiting chain is the rule hearing
        it)."""
        from culture_rules.engine.claims import firing_key  # noqa: PLC0415
        from culture_rules.engine.decisions import RULE_DECISIONS  # noqa: PLC0415
        from culture_rules.node.firing import RULE_FIRES  # noqa: PLC0415

        for rule in listeners:
            key = firing_key(str(rule.get("id")), event_id)  # = decision_key
            if self._store.get(RULE_FIRES, key) or self._store.get(RULE_DECISIONS, key):
                return True
        return False

    def _evaluated(self, event_id: str, rule: Mapping[str, Any]) -> bool:
        """Whether the trigger consumer that decides ``rule`` has evaluated ``event_id``
        (its fire marker, committed with the decisions and the cursor: whatever it
        decided is final). An unplaced rule's consumer is the shared one; a rule placed on
        a machine is decided by that machine's. A rule placed by actor or requirement
        cannot be mapped to one consumer here, so its pair is never requested again (never
        twice is the safe side)."""
        from culture_rules.events.triggers import FIRES_COLLECTION  # noqa: PLC0415
        from culture_rules.node.firing import SHARED_CONSUMER, placed_consumer  # noqa: PLC0415

        placement = rule.get("placement")
        if not placement:
            consumer = SHARED_CONSUMER
        else:
            machine = placement.get("machine") if isinstance(placement, Mapping) else None
            if not isinstance(machine, str) or not machine:
                return False
            consumer = placed_consumer(machine)
        return self._store.get(FIRES_COLLECTION, f"{consumer}/{event_id}") is not None


def _parse(text: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _same_repo(pull: Mapping[str, Any]) -> bool:
    head, base = pull.get("head"), pull.get("base")
    names = [
        ((side or {}).get("repo") or {}).get("full_name") if isinstance(side, Mapping) else None
        for side in (head, base)
    ]
    return all(isinstance(n, str) and n for n in names) and names[0].lower() == names[1].lower()
