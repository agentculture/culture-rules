"""Human asks: an id-bearing ``human.ask.requested`` event and an exactly-once answer action.

A human is an actor whose work is long-running and asynchronous. The :class:`HumanAdapter`
is an :class:`~culture_rules.engine.actorport.ActorPort`: ``invoke`` stores one *ask*
(collection ``asks``, document id = ask id), emits exactly one ``human.ask.requested``
event and returns ``accepted``. :func:`answer_ask` is the *answer ask* action (the API
answer endpoint calls it): it compare-and-sets the ask ``open -> answered`` exactly once,
audits it, and resumes the waiting run through ``Executor.deliver``.

Obligation o8 (both schema-versioned, ``schema_version`` = :data:`SCHEMA_VERSION`):

- event ``human.ask.requested`` data: ``schema_version``, ``ask_id``, ``question``,
  ``options`` (list or null), ``run_id``, ``step_id``, ``deadline`` (RFC 3339), ``attempt``;
- action ``answer_ask(store, executor, ask_id, answer, identity, schema_version=1)``.

Timeouts are the step's: the executor times out accepted work at the deadline and retries
per the step's retry policy. A retry invokes again with a higher ``context.attempt``; the
ask id includes the attempt, so a re-ask is a *new* ask and the previous one is marked
``expired`` (answering it fails ``ask_expired``). An ask whose deadline has passed can no
longer be answered either. Standard-library only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.audit import AUDIT_COLLECTION, AuditLog, mutating_verb, require_identity
from culture_rules.events.emit import Emitter
from culture_rules.store.port import Document, DuplicateKeyError

__all__ = [
    "ASKS_COLLECTION",
    "ASK_REQUESTED",
    "SCHEMA_VERSION",
    "AskError",
    "HumanAdapter",
    "answer_ask",
    "ask_id_for",
    "ensure_collections",
    "redeliver",
]

ASKS_COLLECTION = "asks"
ASK_REQUESTED = "human.ask.requested"
SCHEMA_VERSION = 1
OPEN, ANSWERED, EXPIRED = "open", "answered", "expired"

Clock = Callable[[], datetime]


class AskError(ValueError):
    """An ask operation was refused; ``code`` is the stable, machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def ensure_collections(store: Any) -> None:
    """Create the asks collection up front where the adapter needs it (MongoDB)."""
    ensure = getattr(store, "ensure_collections", None)
    if callable(ensure):
        ensure(ASKS_COLLECTION, AUDIT_COLLECTION)


def ask_id_for(idempotency_key: str, attempt: int) -> str:
    """Deterministic ask id: stable for a (step key, attempt), new for each re-ask."""
    digest = hashlib.sha256(f"{idempotency_key}:{attempt}".encode()).hexdigest()[:20]
    return f"ask_{digest}"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class HumanAdapter:
    """ActorPort for human steps: ``accepted`` now, completed later by :func:`answer_ask`."""

    supports_idempotency_key = True

    def __init__(self, store: Any, emitter: Emitter, *, clock: Clock | None = None) -> None:
        self._store = store
        self._emitter = emitter
        self._clock = clock or (lambda: datetime.now(UTC))
        ensure_collections(store)

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        question = context.config.get("question") or input.get("question")
        if not isinstance(question, str) or not question.strip():
            return InvocationResult.failed("a human ask needs a question", retryable=False)
        options = context.config.get("options") or input.get("options")
        if options is not None and not isinstance(options, (list, tuple)):
            return InvocationResult.failed("options must be a list", retryable=False)
        ask_id = ask_id_for(idempotency_key, context.attempt)
        self._expire_previous(idempotency_key, context.attempt)
        doc = {
            "id": ask_id,
            "schema_version": SCHEMA_VERSION,
            "idempotency_key": idempotency_key,
            "run_id": context.run_id,
            "step_id": context.step_id,
            "attempt": context.attempt,
            "question": question,
            "options": list(options) if options is not None else None,
            "deadline": _iso(deadline),
            "status": OPEN,
            "requested_emitted": False,
            "asked_at": _iso(self._clock()),
        }
        try:
            ask: Document = self._store.insert(ASKS_COLLECTION, doc)
        except DuplicateKeyError:
            ask = self._store.get(ASKS_COLLECTION, ask_id)
            if ask["status"] == ANSWERED:  # the answer beat a re-delivery of this invoke
                return InvocationResult.completed({"answer": ask["answer"]})
        if not ask["requested_emitted"]:
            self._emit(ask)
        return InvocationResult.accepted()

    def _emit(self, ask: Mapping[str, Any]) -> None:
        self._emitter.emit(
            ASK_REQUESTED,
            {
                "schema_version": SCHEMA_VERSION,
                "ask_id": ask["id"],
                "question": ask["question"],
                "options": ask["options"],
                "run_id": ask["run_id"],
                "step_id": ask["step_id"],
                "deadline": ask["deadline"],
                "attempt": ask["attempt"],
            },
            run_id=ask["run_id"],
        )
        self._store.update_if(ASKS_COLLECTION, ask["id"], {}, {"requested_emitted": True})

    def _expire_previous(self, key: str, attempt: int) -> None:
        for old in self._store.find(ASKS_COLLECTION, {"idempotency_key": key, "status": OPEN}):
            if old["attempt"] < attempt:
                self._store.update_if(ASKS_COLLECTION, old["id"], {"status": OPEN}, _expired())


def _expired() -> dict[str, Any]:
    return {"status": EXPIRED}


@mutating_verb("asks.answer", "Answer an open human ask, resuming the waiting run exactly once")
def answer_ask(
    store: Any,
    executor: Any,
    ask_id: str,
    answer: Any,
    identity: str,
    *,
    schema_version: int = SCHEMA_VERSION,
    audit: AuditLog | None = None,
    clock: Clock | None = None,
) -> Document:
    """Answer ``ask_id`` once; a second answer fails ``ask_already_answered``."""
    require_identity(identity)
    if schema_version != SCHEMA_VERSION:
        raise AskError(
            "unsupported_schema_version",
            f"schema_version {schema_version!r} is not supported (expected {SCHEMA_VERSION})",
        )
    clock = clock or getattr(executor, "_clock", None) or (lambda: datetime.now(UTC))
    audit = audit or AuditLog(clock=clock)
    ensure_collections(store)
    before = store.get(ASKS_COLLECTION, ask_id)
    if before is None:
        raise AskError("ask_not_found", f"ask {ask_id!r} does not exist")
    _require_open(before)
    now = clock()
    if now >= _parse(before["deadline"]):
        store.update_if(ASKS_COLLECTION, ask_id, {"status": OPEN}, _expired())
        raise AskError("ask_expired", f"ask {ask_id!r} passed its deadline {before['deadline']}")
    options = before.get("options")
    if options is not None and answer not in options:
        raise AskError("invalid_answer", f"answer must be one of {list(options)!r}")
    changes = {
        "status": ANSWERED,
        "answer": answer,
        "answered_by": identity,
        "answered_at": _iso(now),
        "delivered": False,
    }
    with store.transaction() as tx:
        res = tx.update_if(ASKS_COLLECTION, ask_id, {"status": OPEN}, changes)
        if res.won:
            audit.write(
                tx,
                identity=identity,
                verb="asks.answer",
                collection=ASKS_COLLECTION,
                target_id=ask_id,
                before=before,
                after=res.document,
            )
    if not res.won:
        _require_open(store.get(ASKS_COLLECTION, ask_id) or before)
        raise AskError("ask_already_answered", f"ask {ask_id!r} was already answered")
    return _deliver(store, executor, res.document)


def _require_open(ask: Mapping[str, Any]) -> None:
    if ask["status"] == ANSWERED:
        raise AskError(
            "ask_already_answered",
            f"ask {ask['id']!r} was already answered; a second answer is rejected",
        )
    if ask["status"] == EXPIRED:
        raise AskError("ask_expired", f"ask {ask['id']!r} expired (timed out or re-asked)")


def _deliver(store: Any, executor: Any, ask: Mapping[str, Any]) -> Document:
    executor.deliver(ask["idempotency_key"], InvocationResult.completed({"answer": ask["answer"]}))
    store.update_if(ASKS_COLLECTION, ask["id"], {"status": ANSWERED}, {"delivered": True})
    return store.get(ASKS_COLLECTION, ask["id"])


def redeliver(store: Any, executor: Any) -> int:
    """Resume runs for answered asks whose delivery was lost (crash between answer and deliver).

    ``Executor.deliver`` ignores steps that already finished, so this is safe to repeat.
    Returns how many asks were re-delivered.
    """
    pending = [a for a in store.find(ASKS_COLLECTION, {"status": ANSWERED}) if not a["delivered"]]
    for ask in pending:
        _deliver(store, executor, ask)
    return len(pending)
