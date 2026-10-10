"""Built-ins ``queue.add`` and ``queue.progress``: a durable first-come-first-served queue (#35).

Deviations d29/d30 of the ``pr-fixer-rule`` plan. The PR fixer's work on one model server is
queued by two visible, non-agentic rules instead of racing for the actor's slot: the
trigger rules add a request (``queue.add``), and a progress rule (``queue.progress``) runs
when a request is added or a dispatched run ends, and starts the oldest request when the
pool has a free slot. The engine's ``concurrency_pool`` cap
(:mod:`culture_rules.actors.limits`) stays underneath as a safety net.

The queue document
==================

One document per queue in :data:`QUEUES_COLLECTION` (id = the queue's name, the step's
``config.queue``), changed only by compare-and-set on ``rev``, so every host sees one order:

* ``waiting`` - the requests in line, oldest first. Each: ``rid`` (unique), ``key``
  (``owner/name#number``), ``repository``, ``number``, ``head_sha``, ``retry`` (a try that
  did not pass, re-entering at the back, d30), ``attempt`` (the PR's attempt this request
  would be: its key's counted attempts plus one), ``inputs`` (what the dispatched run gets),
  ``source_run`` (the ``queue.add`` run that put it there, or last replaced it: the push
  walks a fix's lineage back through it, :func:`culture_rules.actors.lineage.fix_ancestry`),
  ``enqueued_at``, ``updated_at`` and, recomputed on every write, ``position`` (1 = next)
  and ``ahead`` (the key it waits behind: the request before it, or for the first one the
  most recently dispatched request still running).
* ``active`` - the dispatched requests whose run has not ended: ``rid``, ``key``,
  ``repository``, ``number``, ``event_id`` (the dispatch event), ``run_id`` (the run the
  dispatch rule starts on it: deterministic, :func:`~culture_rules.node.firing.run_id_for`)
  and ``dispatched_at``. An entry is the pool's slot.
* ``seq``, ``seen`` (the idempotency keys of the last adds, so a re-invoked add changes
  nothing), ``rev`` and ``updated_at``.

``queue.add``
=============

Config: ``queue`` (required), ``key_prefix`` (the PR's concurrency-key prefix, e.g.
``pr-fixer:``, so the request's key ``<prefix><repo>#<number>`` is the dispatch rule's
concurrency key). Inputs: ``repo`` and ``number`` (required), ``head_sha``, ``retry``
(boolean), ``prior_instruction`` (a retry's original task when ``task`` is empty) and any
others - all but ``retry`` and ``prior_instruction`` are the request's ``inputs``.

* A PR with a request already waiting keeps that place: the newer request replaces its
  head and inputs (``replaced``). A retry never replaces a waiting request that is not a
  retry (that one is newer); it is absorbed.
* A retry whose PR has spent its attempts (the key's ``count`` reached its ``limit``) fails
  the step non-retryably with ``attempt_budget_exhausted: <the instruction>``, so the
  rule's ``on_failure`` hands back once.

Outputs: ``queued``, ``replaced``, ``key``, ``position``, ``ahead``, ``length``, ``attempt``.

``queue.progress``
==================

Config: ``queue`` and ``dispatch_rule`` (required: the rule that turns a dispatch event into
the run), ``key_prefix``, ``cap`` (slots, default 1) or ``pool`` (an actor
``concurrency_pool``: its cap, :func:`~culture_rules.actors.limits.pool_cap`, falling back
to ``cap``), ``stale_after_s`` (default :data:`DEFAULT_STALE_AFTER_S`) and
``lookup_actor`` (the GitHub App actor the PR is read with). One pass:

1. **Free ended slots.** An active entry whose run has a terminal status is removed. The
   dispatch rule's firing **claims** its entry (:func:`claim_dispatch`, a compare-and-set
   in the trigger transaction); a claimed entry is kept until its run ends (or its start
   failed). An unclaimed one whose firing never came within ``stale_after_s`` expires,
   and a dispatch event whose entry is gone never fires afterwards (the skip
   ``dispatch_revoked``): an expiry and a late claim cannot both win.
2. **Dispatch in order.** While fewer entries are active than the cap, the oldest waiting
   request is taken, skipping one whose PR already has an active entry or whose concurrency
   key is busy (:func:`~culture_rules.engine.claims.key_state`: a live run or a chain still
   holding it - it keeps its place). A request whose PR spent its attempts is dropped
   (``attempt_budget_exhausted``); with ``lookup_actor`` the PR is read first and a closed
   PR (``pr_not_open``) or a moved head (``head_moved``) is dropped. A failed lookup
   dispatches anyway: the run's own head guard stops a stale one.
3. **Emit.** Each dispatched request becomes a root event (``hops`` 0, so the queue never
   adds to a chain's hop count) of type :data:`DISPATCH_TYPE`, id
   :func:`dispatch_event_id`, source :data:`QUEUE_SOURCE`, written straight into the
   ``events`` collection after the queue write; a pass also re-writes the event of any
   active entry whose event is missing (a node that died in between). Its ``data`` is the
   request's inputs plus ``queue``, ``request_id``, ``repository``, ``number``,
   ``head_sha``, ``retry``, ``attempt``, ``source_run`` and ``dispatch_run`` (the run the
   dispatch rule starts on it: only that run claims the slot; another rule on the event
   neither claims nor is fenced, and a dispatch rule that is disabled or gone leaves the
   slot unclaimed, so it expires). The ``rules.queue.*`` types
   and ``queue_`` ids are reserved at external ingest
   (:func:`culture_rules.events.emit.reserved_reason`).

Outputs: ``dispatched`` and ``active`` (keys), ``dropped`` (``{key, reason}``), ``waiting``
(``{key, position, ahead}``).

``queue.stop``
==============

Deviation d34 (#40): a trusted ``/stop`` or 👎 ends a PR's fixer story. Config: ``queue``
and ``key_prefix`` (required), ``lookup_actor`` (the PR's current head is read, else the
``head_sha`` input is used) and ``sweep``. Inputs: ``repo``, ``number``, ``by`` (the login
that asked), ``head_sha`` and ``story`` (a 👎's story, its root run id: once that story has
no running run and no queued request, the stop does nothing, so a late reaction never stops
a newer story). One pass:

1. **Record** the stop on the PR's concurrency key
   (:func:`~culture_rules.node.story_stop.record_stop`): from now on ``queue.add`` drops a
   request of this story, or an automatic one for the stopped head, and the push refuses
   ``story_stopped`` (:mod:`culture_rules.node.story_stop`).
2. **Unqueue** the PR's waiting request (its retry included) and revoke its dispatch no
   run claimed yet (its event then never fires: ``dispatch_revoked``), by compare-and-set.
3. **Cancel** the PR's running runs on the key whose story began no later than the stop,
   exactly as stopping a disabled rule's runs does (d17: cancelled, no push, no on_failure
   hand-back; the node then asks the bridge to cancel the agent's job).
4. **End the story**: each stopped story's status comment gets the final text
   "PR fixer stopped by @<by>." (:meth:`~culture_rules.node.status_board.StatusBoard.finish`).

``sweep: true`` records nothing: it re-runs steps 3 and 4 against the stop already
recorded, for a run of the story that started just after the first pass (a chain's next
stage). Outputs: ``stopped`` (a stop is recorded), ``key``, ``removed`` and ``cancelled``
(counts). Standard-library only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.events.emit import (
    QUEUE_EVENT_ID_PREFIX,
    QUEUE_EVENT_TYPE_PREFIX,
    QUEUE_SOURCE,
    derive_envelope,
)

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_STALE_AFTER_S",
    "DISPATCH_TYPE",
    "QUEUES_COLLECTION",
    "QUEUE_ADD_BUILTIN",
    "QUEUE_PROGRESS_BUILTIN",
    "QUEUE_SOURCE",
    "QUEUE_STOP_BUILTIN",
    "QueueAddPort",
    "QueueProgressPort",
    "QueueStopPort",
    "claim_dispatch",
    "dispatch_event_id",
    "dispatch_live",
    "is_dispatch",
]

QUEUES_COLLECTION = "queues"
QUEUE_ADD_BUILTIN = "queue.add"
QUEUE_PROGRESS_BUILTIN = "queue.progress"
QUEUE_STOP_BUILTIN = "queue.stop"
DISPATCH_TYPE = f"{QUEUE_EVENT_TYPE_PREFIX}dispatch"
DEFAULT_STALE_AFTER_S = 900.0
"""How long a dispatched request may wait for its run to appear before its slot is freed."""
_SEEN_KEEP = 256
_MAX_CAS_TRIES = 50
_EXPLAIN_MAX = 2000
_RUNS = "runs"  # culture_rules.engine.runs.RUNS_COLLECTION
_FIRES = "rule_fires"  # culture_rules.node.firing.RULE_FIRES (the firing intents)
_EVENTS = "events"  # culture_rules.events.ingest.EVENTS_COLLECTION
_RUN_DONE = ("succeeded", "failed", "cancelled", "superseded")
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_META_INPUTS = ("retry", "prior_instruction")

Clock = Callable[[], datetime]


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def dispatch_event_id(queue: str, rid: str) -> str:
    """The id of the dispatch event of request ``rid`` in ``queue`` (one per request)."""
    digest = hashlib.sha256(json.dumps([queue, rid]).encode("utf-8")).hexdigest()
    return f"{QUEUE_EVENT_ID_PREFIX}{digest[:32]}"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _queue_name(config: Mapping[str, Any]) -> str:
    name = config.get("queue")
    if not isinstance(name, str) or not name.strip():
        raise _Refused("bad_config", "config.queue must name the queue")
    return name.strip()


def _subject(input: Mapping[str, Any]) -> tuple[str, int]:
    repo, number = input.get("repo"), input.get("number")
    if not isinstance(repo, str) or not _REPO_RE.match(repo):
        raise _Refused("bad_input", "repo must be owner/name")
    if not (_is_int(number) and number > 0):
        raise _Refused("bad_input", "number must be a positive integer")
    return repo, number


def _pr_key(config: Mapping[str, Any], repo: str, number: int) -> str | None:
    prefix = config.get("key_prefix")
    return f"{prefix}{repo}#{number}" if isinstance(prefix, str) and prefix else None


def _key_state(store: Any, key: str | None, now: datetime) -> dict[str, Any]:
    if key is None:
        return {"busy": False, "count": 0, "limit": None}
    # the engine, imported lazily
    from culture_rules.engine.claims import key_state  # noqa: PLC0415

    return key_state(store, key, now)


def _exhausted(state: Mapping[str, Any]) -> bool:
    limit = state.get("limit")
    return limit is not None and state.get("count", 0) >= limit


def _positions(doc: dict[str, Any]) -> None:
    """Stamp ``position`` (1 = next) and ``ahead`` on every waiting request."""
    ahead = doc["active"][-1]["key"] if doc["active"] else None
    for i, req in enumerate(doc["waiting"], start=1):
        req["position"], req["ahead"] = i, ahead
        ahead = req["key"]


def _waiting_view(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": r["key"], "position": r["position"], "ahead": r["ahead"]} for r in doc["waiting"]
    ]


class _QueueDoc:
    """Compare-and-set access to one queue document."""

    def __init__(self, store: Any, name: str, clock: Clock) -> None:
        self.store, self.name, self.clock = store, name, clock

    def read(self) -> tuple[dict[str, Any], Any, bool]:
        current = self.store.get(QUEUES_COLLECTION, self.name)
        doc = copy.deepcopy(dict(current or {}))
        for field in ("waiting", "active", "seen"):
            doc.setdefault(field, [])
        doc.setdefault("seq", 0)
        return doc, (current or {}).get("rev"), current is not None

    def write(self, doc: dict[str, Any], rev: Any, exists: bool) -> bool:
        _positions(doc)
        changes = {
            "queue": self.name,
            "waiting": doc["waiting"],
            "active": doc["active"],
            "seq": doc["seq"],
            "seen": doc["seen"][-_SEEN_KEEP:],
            "updated_at": _iso(self.clock()),
            "rev": (rev or 0) + 1,
        }
        res = self.store.update_if(
            QUEUES_COLLECTION, self.name, {"rev": rev}, changes, upsert=not exists
        )
        return bool(res.won)


def _failed(exc: _Refused) -> InvocationResult:
    return InvocationResult.failed(str(exc), retryable=False)


class QueueAddPort:
    """``queue.add`` (module doc): put a request in line, or refresh the PR's queued one."""

    supports_idempotency_key = True

    def __init__(self, store: Any, *, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del deadline
        config = context.config or {}
        try:
            name = _queue_name(config)
            repo, number = _subject(input)
        except _Refused as exc:
            return _failed(exc)

        def stopped() -> str | None:
            return _stop_refusal(self._store, config, repo, number, input, context)

        # d34: a stopped story's request is dropped before its budget is judged, so its
        # last retry never fails (and hands back) as attempt_budget_exhausted
        if (why_not := stopped()) is not None:
            log.info("queue.add: %s#%s not queued (%s)", repo, number, why_not)
            return InvocationResult.completed(self._not_queued(name, f"{repo}#{number}"))
        try:
            retry = input.get("retry") is True
            state = _key_state(self._store, _pr_key(config, repo, number), self._clock())
            inputs = {k: copy.deepcopy(v) for k, v in input.items() if k not in _META_INPUTS}
            if retry and not inputs.get("task") and isinstance(input.get("prior_instruction"), str):
                inputs["task"] = input["prior_instruction"]
            if retry and _exhausted(state):
                why = str(inputs.get("instruction") or "")[:_EXPLAIN_MAX]
                raise _Refused(
                    "attempt_budget_exhausted",
                    f"{repo}#{number} used its {state['limit']} attempts; last: {why}",
                )
        except _Refused as exc:
            return _failed(exc)
        request = {
            "key": f"{repo}#{number}",
            "repository": repo,
            "number": number,
            "head_sha": input.get("head_sha") if isinstance(input.get("head_sha"), str) else None,
            "retry": retry,
            "attempt": int(state.get("count") or 0) + 1,
            "inputs": inputs,
            "source_run": context.run_id,
        }
        return InvocationResult.completed(self._add(name, request, idempotency_key, stopped))

    def _add(
        self,
        name: str,
        request: dict[str, Any],
        ik: str,
        stopped: Callable[[], str | None] = lambda: None,
    ) -> dict[str, Any]:
        queue = _QueueDoc(self._store, name, self._clock)
        for _ in range(_MAX_CAS_TRIES):
            doc, rev, exists = queue.read()
            if ik in doc["seen"]:
                _positions(doc)
                return self._result(doc, request["key"], queued=True, replaced=False)
            # d34 (Codex round 1 #3): judged again after each read of the queue. A stop
            # records itself, then always writes the queue document, so an add that read
            # the queue before that write loses its compare-and-set and sees the stop here.
            if stopped() is not None:
                _positions(doc)
                return self._result(doc, request["key"], queued=False, replaced=False)
            now = _iso(self._clock())
            existing = next((r for r in doc["waiting"] if r["key"] == request["key"]), None)
            replaced = False
            if existing is None:
                doc["seq"] += 1
                rid = f"{doc['seq']}-{uuid.uuid4().hex[:12]}"
                doc["waiting"].append(
                    {"rid": rid, **request, "enqueued_at": now, "updated_at": now}
                )
            elif not (request["retry"] and not existing.get("retry")):
                existing.update(request, updated_at=now)
                replaced = True
            doc["seen"].append(ik)
            if queue.write(doc, rev, exists):
                return self._result(doc, request["key"], queued=True, replaced=replaced)
        raise RuntimeError(f"queues/{name}: too much contention")

    def _not_queued(self, name: str, key: str) -> dict[str, Any]:
        """The answer for a request a story stop drops (d34): nothing queued, no failure, so
        no hand-back."""
        doc, _rev, _exists = _QueueDoc(self._store, name, self._clock).read()
        _positions(doc)
        return self._result(doc, key, queued=False, replaced=False)

    @staticmethod
    def _result(doc: Mapping[str, Any], key: str, *, queued: bool, replaced: bool) -> dict:
        req = next((r for r in doc["waiting"] if r["key"] == key), None)
        return {
            "queued": queued,
            "replaced": replaced,
            "key": key,
            "position": req["position"] if req else None,
            "ahead": req["ahead"] if req else None,
            "length": len(doc["waiting"]),
            "attempt": req["attempt"] if req else None,
        }


class QueueProgressPort:
    """``queue.progress`` (module doc): free ended slots, then dispatch in order."""

    supports_idempotency_key = True

    def __init__(self, store: Any, *, clock: Clock | None = None, pr_lookup: Any = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lookup = pr_lookup

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del input
        config = context.config or {}
        try:
            name = _queue_name(config)
            rule = config.get("dispatch_rule")
            if not isinstance(rule, str) or not rule:
                raise _Refused("bad_config", "config.dispatch_rule must name the dispatch rule")
            cap = self._cap(config)
            stale = config.get("stale_after_s", DEFAULT_STALE_AFTER_S)
            if isinstance(stale, bool) or not isinstance(stale, (int, float)) or stale <= 0:
                raise _Refused("bad_config", "config.stale_after_s must be a positive number")
        except _Refused as exc:
            return _failed(exc)
        plan = _Pass(self, name, rule, cap, float(stale), config, context, deadline)
        return InvocationResult.completed(plan.run(idempotency_key))

    def _cap(self, config: Mapping[str, Any]) -> int:
        cap = config.get("cap", 1)
        if not (_is_int(cap) and cap > 0):
            raise _Refused("bad_config", "config.cap must be a positive integer")
        pool = config.get("pool")
        if isinstance(pool, str) and pool:
            from culture_rules.actors.limits import pool_cap  # noqa: PLC0415

            return pool_cap(self._store, pool) or cap
        return cap


class _Pass:
    """One ``queue.progress`` pass over a fresh read, retried on a lost compare-and-set."""

    def __init__(
        self,
        port: QueueProgressPort,
        name: str,
        rule: str,
        cap: int,
        stale_s: float,
        config: Mapping[str, Any],
        context: InvocationContext,
        deadline: datetime,
    ) -> None:
        self.port, self.name, self.rule, self.cap = port, name, rule, cap
        self.stale = timedelta(seconds=stale_s)
        self.config, self.context, self.deadline = config, context, deadline
        self.store = port._store
        self.queue = _QueueDoc(self.store, name, port._clock)
        self.lookups: dict[tuple[str, int, Any], Mapping[str, Any] | None] = {}

    def run(self, ik: str) -> dict[str, Any]:
        for _ in range(_MAX_CAS_TRIES):
            doc, rev, exists = self.queue.read()
            now = self.port._clock()
            ended = [a for a in doc["active"] if self._ended(a, now)]
            doc["active"] = [a for a in doc["active"] if a not in ended]
            dispatched, dropped = self._admit(doc, now, ik)
            changed = bool(ended or dispatched or dropped)
            if not changed or self.queue.write(doc, rev, exists):
                _positions(doc)
                self._emit(doc)
                return {
                    "dispatched": [a["key"] for a in dispatched],
                    "dropped": dropped,
                    "active": [a["key"] for a in doc["active"]],
                    "waiting": _waiting_view(doc),
                }
        raise RuntimeError(f"queues/{self.name}: too much contention")

    def _ended(self, act: Mapping[str, Any], now: datetime) -> bool:
        """A slot is free once its run reached a terminal status. Before that: a dispatch
        its firing claimed (:func:`claim_dispatch`) holds the slot until its run ends, or its
        start failed; an unclaimed one expires after ``stale_after_s``. Expiry is a write to
        this document, and a claim is a compare-and-set on it in the firing's transaction,
        so exactly one of them wins: an expired dispatch can never fire later (Codex P1)."""
        run = self.store.get(_RUNS, act.get("run_id") or "")
        if run is not None:
            return run.get("status") in _RUN_DONE
        if act.get("claimed_at"):
            intent = self.store.get(_FIRES, _firing_key(self.rule, act["event_id"]))
            return intent is not None and intent.get("status") == "failed"
        since = _parse(act.get("dispatched_at"))
        return since is None or now - since >= self.stale

    def _admit(
        self, doc: dict[str, Any], now: datetime, ik: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        dispatched: list[dict[str, Any]] = []
        dropped: list[dict[str, str]] = []
        for req in doc["waiting"][:]:  # a copy: admitted requests leave the list
            if len(doc["active"]) >= self.cap:
                break
            if any(a["key"] == req["key"] for a in doc["active"]):
                continue  # the PR's earlier request is still running: keep the place
            key = _pr_key(self.config, req["repository"], req["number"])
            reason = _stopped_request(self.store, key, req)  # d34: never a stopped story
            if reason is not None:
                doc["waiting"].remove(req)
                dropped.append({"key": req["key"], "reason": reason})
                continue
            state = _key_state(self.store, key, now)
            reason = "attempt_budget_exhausted" if _exhausted(state) else None
            if reason is None and state["busy"]:
                continue  # its chain still holds the key: a firing now would be deduplicated
            reason = reason or self._pr_refusal(req, ik)
            doc["waiting"].remove(req)
            if reason is not None:
                dropped.append({"key": req["key"], "reason": reason})
                continue
            event_id = dispatch_event_id(self.name, req["rid"])
            act = {
                "rid": req["rid"],
                "key": req["key"],
                "repository": req["repository"],
                "number": req["number"],
                "event_id": event_id,
                "run_id": _run_id_for(self.rule, event_id),
                "dispatched_at": _iso(now),
                "request": req,
            }
            doc["active"].append(act)
            dispatched.append(act)
        return dispatched, dropped

    def _pr_refusal(self, req: Mapping[str, Any], ik: str) -> str | None:
        """``pr_not_open`` / ``head_moved`` from a read of the PR, else None (also when the
        read fails: the run's own guard stops a stale request). The PR is read once per pass
        and request head, and the **facts** are kept, never a verdict: a pass retried after a
        lost compare-and-set judges the request as it is now, and a request replaced
        meanwhile with another head (same ``rid``) is read again, so it is never dropped on
        facts older than itself (Codex P2, both rounds)."""
        actor = self.config.get("lookup_actor")
        if self.port._lookup is None or not isinstance(actor, str) or not actor:
            return None
        # keyed by the request's head too: a request replaced meanwhile with another head
        # (a push landed) is read afresh, never judged on facts older than itself
        pr = (req["repository"], req["number"], req.get("head_sha"))
        if pr not in self.lookups:
            self.lookups[pr] = self._read_pr(req, actor, ik)
        facts = self.lookups[pr]
        if facts is None:
            return None
        state = facts.get("state")
        if (isinstance(state, str) and state != "open") or facts.get("merged") is True:
            return "pr_not_open"
        if req.get("head_sha") and facts.get("head_sha") != req["head_sha"]:
            return "head_moved"
        return None

    def _read_pr(self, req: Mapping[str, Any], actor: str, ik: str) -> Mapping[str, Any] | None:
        """The PR's current facts (``state``, ``merged``, ``head_sha``), or None when the
        read failed."""
        ctx = InvocationContext(
            run_id=self.context.run_id,
            step_id=self.context.step_id,
            kind="action",
            host=self.context.host,
            actor=actor,
            config={"kind": "github.pr_head"},
        )
        lookup = {"repo": req["repository"], "number": req["number"], "actor": actor}
        try:
            res = self.port._lookup.invoke(lookup, f"{ik}/{req['rid']}", self.deadline, context=ctx)
        except Exception as exc:  # noqa: BLE001 - a failed read never blocks the queue
            log.warning("queue.progress: PR lookup failed (%s)", type(exc).__name__)
            return None
        return dict(res.output) if res.outcome == COMPLETED else None

    def _emit(self, doc: Mapping[str, Any]) -> None:
        """Write the dispatch event of every active entry that has none yet."""
        from culture_rules.events.ingest import event_document  # noqa: PLC0415
        from culture_rules.store.port import DuplicateKeyError  # noqa: PLC0415

        for act in doc["active"]:
            if self.store.get(_EVENTS, act["event_id"]) is not None:
                continue
            envelope = derive_envelope(
                None,
                type=DISPATCH_TYPE,
                source=QUEUE_SOURCE,
                data=_dispatch_data(self.name, act),
                id=act["event_id"],
                hops=0,
            )
            try:
                self.store.insert(_EVENTS, event_document(envelope, host=self.context.host))
            except DuplicateKeyError:
                pass  # another host wrote it first


def _stop_refusal(
    store: Any,
    config: Mapping[str, Any],
    repo: str,
    number: int,
    input: Mapping[str, Any],
    context: InvocationContext,
) -> str | None:
    """Why a story stop drops this request (d34,
    :func:`~culture_rules.node.story_stop.add_refusal`), or None."""
    from culture_rules.node.story_stop import add_refusal  # noqa: PLC0415

    head = input.get("head_sha") if isinstance(input.get("head_sha"), str) else None
    return add_refusal(store, _pr_key(config, repo, number), head, context.run_id)


def _stopped_request(store: Any, key: str | None, req: Mapping[str, Any]) -> str | None:
    """``story_stopped`` when the waiting request belongs to a stopped story (d34)."""
    from culture_rules.node.story_stop import request_refusal  # noqa: PLC0415

    return request_refusal(store, key, req.get("source_run"))


STOPPED_TEXT = "PR fixer stopped by @{by}. A new /fix or a push to the PR starts a new story."
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$")


class QueueStopPort:
    """``queue.stop`` (module doc): end a PR's fixer story."""

    supports_idempotency_key = True

    def __init__(self, store: Any, *, clock: Clock | None = None, pr_lookup: Any = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lookup = pr_lookup

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        from culture_rules.node.story_stop import record_stop, stop_of  # noqa: PLC0415

        config = context.config or {}
        sweep = config.get("sweep") is True
        try:
            name = _queue_name(config)
            repo, number = _subject(input)
            key = _pr_key(config, repo, number)
            if key is None:
                raise _Refused("bad_config", "config.key_prefix must name the PR's key prefix")
            by = input.get("by")
            if not sweep and (not isinstance(by, str) or not _LOGIN_RE.match(by)):
                raise _Refused("bad_input", "by must be the GitHub login that asked")
        except _Refused as exc:
            return _failed(exc)
        if sweep:
            stop = stop_of(self._store, key)
            if stop is None:
                return InvocationResult.completed(
                    {"stopped": False, "key": key, "removed": 0, "cancelled": 0}
                )
            removed: list[Mapping[str, Any]] = []
        elif not self._live(name, key, input.get("story")):
            # a 👎 names its story: once that story is over it never stops a newer one
            log.info("queue.stop: %s: story %s is over, nothing to stop", key, input["story"])
            return InvocationResult.completed(
                {"stopped": False, "key": key, "removed": 0, "cancelled": 0}
            )
        else:
            head = self._head(repo, number, input, config, context, deadline, idempotency_key)
            stop = record_stop(self._store, key, by=by, head_sha=head, at=self._clock())
            removed = self._unqueue(name, f"{repo}#{number}")
        cancelled = self._cancel(key, stop)
        self._end_stories(repo, number, stop, removed, cancelled)
        log.info(
            "queue.stop: %s stopped by %s (%d unqueued, %d cancelled)",
            key,
            stop.get("by"),
            len(removed),
            len(cancelled),
        )
        return InvocationResult.completed(
            {"stopped": True, "key": key, "removed": len(removed), "cancelled": len(cancelled)}
        )

    def _head(
        self,
        repo: str,
        number: int,
        input: Mapping[str, Any],
        config: Mapping[str, Any],
        context: InvocationContext,
        deadline: datetime,
        ik: str,
    ) -> str | None:
        """The PR's head now (read through ``lookup_actor``), else the ``head_sha`` input."""
        given = input.get("head_sha") if isinstance(input.get("head_sha"), str) else None
        actor = config.get("lookup_actor")
        if self._lookup is None or not isinstance(actor, str) or not actor:
            return given
        ctx = InvocationContext(
            run_id=context.run_id,
            step_id=context.step_id,
            kind="action",
            host=context.host,
            actor=actor,
            config={"kind": "github.pr_head"},
        )
        lookup = {"repo": repo, "number": number, "actor": actor}
        try:
            res = self._lookup.invoke(lookup, f"{ik}/head", deadline, context=ctx)
        except Exception as exc:  # noqa: BLE001 - the stop goes ahead on the given head
            log.warning("queue.stop: PR lookup failed (%s)", type(exc).__name__)
            return given
        head = res.output.get("head_sha") if res.outcome == COMPLETED else None
        return head if isinstance(head, str) and head else given

    def _live(self, name: str, key: str, story: Any) -> bool:
        """Whether ``story`` (a root run id; any story when not given) still has a running
        run on ``key`` or a request in the queue."""
        from culture_rules.node.story_stop import story_root  # noqa: PLC0415

        if not isinstance(story, str) or not story:
            return True
        for run in self._store.find(_RUNS, {"concurrency_key": key, "status": "running"}):
            if story_root(self._store, run).get("id") == story:
                return True
        doc = self._store.get(QUEUES_COLLECTION, name) or {}
        for entry in (*(doc.get("waiting") or ()), *(doc.get("active") or ())):
            req = entry.get("request") if isinstance(entry.get("request"), Mapping) else entry
            source = self._store.get(_RUNS, req.get("source_run") or "")
            if source is not None and story_root(self._store, source).get("id") == story:
                return True
        return False

    def _unqueue(self, name: str, pr: str) -> list[Mapping[str, Any]]:
        """Remove the PR's waiting requests and unclaimed dispatches; the removed ones."""
        queue = _QueueDoc(self._store, name, self._clock)
        for _ in range(_MAX_CAS_TRIES):
            doc, rev, exists = queue.read()
            waiting = [r for r in doc["waiting"] if r.get("key") == pr]
            revoked = [a for a in doc["active"] if a.get("key") == pr and not a.get("claimed_at")]
            # always written, even with nothing to remove: an add that read the queue before
            # this write then loses its compare-and-set and judges the stop again
            doc["waiting"] = [r for r in doc["waiting"] if r not in waiting]
            doc["active"] = [a for a in doc["active"] if a not in revoked]
            if queue.write(doc, rev, exists):
                return waiting + [a.get("request") or {} for a in revoked]
        raise RuntimeError(f"queues/{name}: too much contention")

    def _cancel(self, key: str, stop: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        """Cancel the key's running runs whose story began no later than ``stop`` (d17's
        cancel: no push, no hand-back); the cancelled runs."""
        from culture_rules.engine.runs import Containment, RunError  # noqa: PLC0415
        from culture_rules.node.story_stop import (  # noqa: PLC0415
            story_began_before,
            story_root,
        )

        containment: Containment | None = None
        out: list[Mapping[str, Any]] = []
        who = f"{stop.get('by')} (stop)"
        for run in self._store.find(_RUNS, {"concurrency_key": key, "status": "running"}):
            if not story_began_before(stop, story_root(self._store, run).get("created_at")):
                continue  # a new story, begun after the stop
            containment = containment or Containment(self._store, clock=self._clock)
            try:
                containment.cancel(run["id"], who, f"stopped by @{stop.get('by')}")
            except RunError as exc:
                if exc.code not in ("run_finished", "run_not_found"):
                    raise
                continue  # it finished meanwhile
            out.append(run)
        return out

    def _end_stories(
        self,
        repo: str,
        number: int,
        stop: Mapping[str, Any],
        removed: list[Mapping[str, Any]],
        cancelled: list[Mapping[str, Any]],
    ) -> None:
        """Give each stopped story's status comment its final text (best effort)."""
        from culture_rules.node.status_board import StatusBoard  # noqa: PLC0415
        from culture_rules.node.story_stop import story_root  # noqa: PLC0415

        runs = list(cancelled)
        for req in removed:
            source = self._store.get(_RUNS, req.get("source_run") or "")
            if source is not None:
                runs.append(source)
        board = StatusBoard(self._store, clock=self._clock)
        text = STOPPED_TEXT.format(by=stop.get("by"))
        seen: set[Any] = set()
        for run in runs:
            root = story_root(self._store, run)
            if root.get("id") in seen:
                continue
            seen.add(root.get("id"))
            try:
                board.finish(root, text, where=(repo, number))
            except Exception as exc:  # noqa: BLE001 - the stop itself is done
                log.warning("queue.stop: status of %s not ended (%s)", root.get("id"), exc)


def _dispatch_data(queue: str, act: Mapping[str, Any]) -> dict[str, Any]:
    req = act.get("request") or {}
    data = {k: v for k, v in (req.get("inputs") or {}).items() if k != "repo"}
    data.update(
        queue=queue,
        request_id=act["rid"],
        repository=act["repository"],
        number=act["number"],
        head_sha=req.get("head_sha"),
        retry=bool(req.get("retry")),
        attempt=req.get("attempt"),
        source_run=req.get("source_run"),
        dispatch_run=act.get("run_id"),
    )
    return data


def _firing_key(rule: str, event_id: str) -> str:
    from culture_rules.engine.claims import firing_key  # noqa: PLC0415

    return firing_key(rule, event_id)


def is_dispatch(envelope: Mapping[str, Any]) -> bool:
    """Whether ``envelope`` is one of the queue's dispatch events."""
    return envelope.get("type") == DISPATCH_TYPE


def _active_entry(ops: Any, envelope: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    data = envelope.get("data") if isinstance(envelope.get("data"), Mapping) else {}
    name = data.get("queue")
    doc = ops.get(QUEUES_COLLECTION, name) if isinstance(name, str) and name else None
    act = next(
        (a for a in (doc or {}).get("active") or () if a.get("event_id") == envelope.get("id")),
        None,
    )
    return name, doc, act


def _intended(envelope: Mapping[str, Any], run_id: str) -> bool:
    """Whether ``run_id`` is the run the queue dispatched ``envelope`` for (its
    ``dispatch_run``: the configured dispatch rule's firing). Another rule on the same
    event (a notification) is not: it neither holds nor claims the slot."""
    data = envelope.get("data") if isinstance(envelope.get("data"), Mapping) else {}
    intended = data.get("dispatch_run")
    return not isinstance(intended, str) or not intended or intended == run_id


def dispatch_live(ops: Any, envelope: Mapping[str, Any], run_id: str) -> bool:
    """Whether the firing that would start ``run_id`` on ``envelope`` may go ahead (read in
    its transaction): the intended dispatch run only while the queue still holds the slot -
    an expired dispatch must not fire; any other rule on the event always."""
    if not _intended(envelope, run_id):
        return True
    return _active_entry(ops, envelope)[2] is not None


def claim_dispatch(ops: Any, envelope: Mapping[str, Any], run_id: str, at: str) -> bool:
    """Claim ``envelope``'s active slot for the run its firing starts, by compare-and-set on
    the queue document, inside the trigger transaction that records the firing (Codex P1):
    from then on :class:`QueueProgressPort` keeps the slot until that run ends, and an expiry
    racing it loses its compare-and-set (or this transaction conflicts and re-runs, then
    finds the slot gone). False when the slot is gone; True when claimed (or already)."""
    from culture_rules.store.port import TransientStoreError  # noqa: PLC0415

    if not _intended(envelope, run_id):
        return True  # not the dispatch run: it never takes the slot (Codex round 2)
    name, doc, act = _active_entry(ops, envelope)
    if act is None:
        return False
    if act.get("claimed_at") or act.get("run_id") != run_id:
        return bool(act.get("claimed_at"))
    active = [
        {**a, "claimed_at": at, "claimed_run": run_id} if a is act else a for a in doc["active"]
    ]
    rev = doc.get("rev")
    res = ops.update_if(
        QUEUES_COLLECTION, name, {"rev": rev}, {"active": active, "rev": (rev or 0) + 1}
    )
    if not res.won:
        raise TransientStoreError(f"queues/{name}: a dispatch claim raced; retry")
    return True


def _run_id_for(rule: str, event_id: str) -> str:
    from culture_rules.node.firing import run_id_for  # noqa: PLC0415

    return run_id_for(rule, event_id)
