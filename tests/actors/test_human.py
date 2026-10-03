"""Human asks (t17): id-bearing ask event, exactly-once answer, timeouts via step policy.

Covers c93 / h74 and obligation o8 (schema-versioned human.ask.requested event + answer action).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pytest

from culture_rules.actors.human import (
    ASK_REQUESTED,
    ASKS_COLLECTION,
    SCHEMA_VERSION,
    AskError,
    HumanAdapter,
    answer_ask,
    redeliver,
)
from culture_rules.engine.audit import AUDIT_COLLECTION, MUTATING_VERBS
from culture_rules.engine.runs import Executor, step_state
from culture_rules.events.emit import Emitter
from culture_rules.model.common import RetryPolicy
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, edge, port, rule, step, workflow


class FakeSink:
    def __init__(self) -> None:
        self.envelopes: list[Mapping[str, Any]] = []

    def publish(self, envelope: Mapping[str, Any]) -> None:
        self.envelopes.append(envelope)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def sink() -> FakeSink:
    return FakeSink()


@pytest.fixture
def human(store, sink, clock) -> HumanAdapter:
    return HumanAdapter(store, Emitter(sink, source="culture-rules/test"), clock=clock)


@pytest.fixture
def ex(store, human, clock) -> Executor:
    return Executor(store, "spark", {"actor_task": human, "*": FakeActor()}, clock=clock)


def ask_wf(**kw):
    s = step(
        "h",
        "actor_task",
        outputs=(port("answer", "any"),),
        config={"question": "Ship it?", "options": ["yes", "no"]},
        **kw,
    )
    return workflow(
        (s, step("after", inputs=(port("answer", "any"),))),
        (edge("h", "answer", "after", "answer"),),
    )


def start(ex, **kw):
    run = ex.start(rule(), ask_wf(**kw))
    ex.run_until_idle()
    return run["id"]


def open_asks(store):
    return store.find(ASKS_COLLECTION, {"status": "open"})


# ---- criterion 1: one id-bearing event ------------------------------------------------------


def test_invoke_creates_ask_and_emits_exactly_one_event(store, sink, ex):
    run_id = start(ex, timeout_s=60)
    (ask,) = store.find(ASKS_COLLECTION)
    assert ask["id"] == ask["id"].strip()
    assert ask["id"]
    assert ask["status"] == "open"
    reqs = [e for e in sink.envelopes if e["type"] == ASK_REQUESTED]
    assert len(reqs) == 1
    data = reqs[0]["data"]
    assert data["ask_id"] == ask["id"]
    assert data["question"] == "Ship it?"
    assert data["options"] == ["yes", "no"]
    assert data["run_id"] == run_id
    assert data["step_id"] == "h"
    assert data["deadline"]
    assert data["schema_version"] == SCHEMA_VERSION
    assert reqs[0]["schemaVersion"] == "1"
    assert reqs[0]["runId"] == run_id
    assert step_state(ex.run(run_id), "h")["status"] == "waiting"


def test_reinvoke_same_attempt_is_idempotent(store, sink, ex, human):
    run_id = start(ex, timeout_s=60)
    (ask,) = store.find(ASKS_COLLECTION)
    from culture_rules.engine.actorport import InvocationContext

    ctx = InvocationContext(run_id, "h", "actor_task", "spark", 1, None, {"question": "Ship it?"})
    res = human.invoke(
        {},
        ask["idempotency_key"],
        datetime.fromisoformat(ask["deadline"].replace("Z", "+00:00")),
        context=ctx,
    )
    assert res.outcome == "accepted"
    assert len(store.find(ASKS_COLLECTION)) == 1
    assert len([e for e in sink.envelopes if e["type"] == ASK_REQUESTED]) == 1


def test_failed_publish_is_retried_and_exactly_one_event_lands(store, clock):
    class Flaky(FakeSink):
        fail = True

        def publish(self, envelope):
            if self.fail:
                self.fail = False
                raise ConnectionError("down")
            super().publish(envelope)

    sink = Flaky()
    human = HumanAdapter(store, Emitter(sink, source="x"), clock=clock)
    ex = Executor(store, "spark", {"actor_task": human, "*": FakeActor()}, clock=clock)
    ex.start(rule(), ask_wf(timeout_s=60, retry=RetryPolicy(max_attempts=2)))
    ex.run_until_idle()  # invoke raised: no ack, retried with the same key
    clock.advance(1)
    ex.run_until_idle()
    assert len([e for e in sink.envelopes if e["type"] == ASK_REQUESTED]) == 1


# ---- criterion 2: answer resumes exactly once -----------------------------------------------


def test_answer_resumes_run_once(store, ex):
    run_id = start(ex, timeout_s=60)
    (ask,) = store.find(ASKS_COLLECTION)
    out = answer_ask(store, ex, ask["id"], "yes", "alice")
    assert out["status"] == "answered"
    assert out["answer"] == "yes"
    ex.run_until_idle()
    doc = ex.run(run_id)
    assert doc["status"] == "succeeded"
    assert step_state(doc, "h")["outputs"] == {"answer": "yes"}


def test_second_answer_rejected_with_clear_error(store, ex):
    run_id = start(ex, timeout_s=60)
    (ask,) = store.find(ASKS_COLLECTION)
    answer_ask(store, ex, ask["id"], "yes", "alice")
    with pytest.raises(AskError) as e:
        answer_ask(store, ex, ask["id"], "no", "bob")
    assert e.value.code == "ask_already_answered"
    assert "already" in e.value.message
    ex.run_until_idle()
    assert step_state(ex.run(run_id), "h")["outputs"] == {"answer": "yes"}
    assert store.get(ASKS_COLLECTION, ask["id"])["answer"] == "yes"


def test_unknown_ask_and_bad_option_and_bad_schema(store, ex):
    start(ex, timeout_s=60)
    (ask,) = store.find(ASKS_COLLECTION)
    with pytest.raises(AskError) as e:
        answer_ask(store, ex, "ask_nope", "yes", "alice")
    assert e.value.code == "ask_not_found"
    with pytest.raises(AskError) as e:
        answer_ask(store, ex, ask["id"], "maybe", "alice")
    assert e.value.code == "invalid_answer"
    with pytest.raises(AskError) as e:
        answer_ask(store, ex, ask["id"], "yes", "alice", schema_version=99)
    assert e.value.code == "unsupported_schema_version"
    assert store.get(ASKS_COLLECTION, ask["id"])["status"] == "open"


def test_answer_is_audited_with_identity(store, ex):
    start(ex, timeout_s=60)
    (ask,) = store.find(ASKS_COLLECTION)
    answer_ask(store, ex, ask["id"], "no", "alice")
    (entry,) = [e for e in store.find(AUDIT_COLLECTION) if e["verb"] == "asks.answer"]
    assert entry["identity"] == "alice"
    assert entry["target"] == {"collection": ASKS_COLLECTION, "id": ask["id"]}
    assert "asks.answer" in MUTATING_VERBS


def test_rejected_answer_is_not_audited(store, ex):
    start(ex, timeout_s=60)
    (ask,) = store.find(ASKS_COLLECTION)
    answer_ask(store, ex, ask["id"], "no", "alice")
    with pytest.raises(AskError):
        answer_ask(store, ex, ask["id"], "yes", "bob")
    assert len([e for e in store.find(AUDIT_COLLECTION) if e["verb"] == "asks.answer"]) == 1


def test_redeliver_resumes_answered_ask_whose_delivery_was_lost(store, ex):
    run_id = start(ex, timeout_s=60)
    (ask,) = store.find(ASKS_COLLECTION)

    class Dead:
        _clock = ex._clock

        def deliver(self, key, result):
            raise RuntimeError("engine died")

    dead = Dead()
    with pytest.raises(RuntimeError):
        answer_ask(store, dead, ask["id"], "yes", "alice")
    assert store.get(ASKS_COLLECTION, ask["id"])["delivered"] is False
    assert redeliver(store, ex) == 1
    assert redeliver(store, ex) == 0
    ex.run_until_idle()
    assert ex.run(run_id)["status"] == "succeeded"


# ---- criterion 3: timeouts follow the step policy -------------------------------------------


def test_timeout_reasks_with_new_attempt_id_and_expires_the_old_ask(store, sink, ex, clock):
    run_id = start(ex, timeout_s=10, retry=RetryPolicy(max_attempts=2, backoff_s=0))
    (first,) = store.find(ASKS_COLLECTION)
    clock.advance(11)
    ex.run_until_idle()
    asks = store.find(ASKS_COLLECTION)
    assert len(asks) == 2
    second = next(a for a in asks if a["id"] != first["id"])
    assert second["attempt"] == 2
    assert first["attempt"] == 1
    assert store.get(ASKS_COLLECTION, first["id"])["status"] == "expired"
    assert second["status"] == "open"
    reqs = [e for e in sink.envelopes if e["type"] == ASK_REQUESTED]
    assert [r["data"]["ask_id"] for r in reqs] == [first["id"], second["id"]]
    with pytest.raises(AskError) as e:
        answer_ask(store, ex, first["id"], "yes", "alice")
    assert e.value.code == "ask_expired"
    answer_ask(store, ex, second["id"], "yes", "alice")
    ex.run_until_idle()
    assert ex.run(run_id)["status"] == "succeeded"


def test_timeout_exhausts_policy_then_step_fails_and_late_answer_rejected(store, ex, clock):
    run_id = start(ex, timeout_s=10, retry=RetryPolicy(max_attempts=1))
    (ask,) = store.find(ASKS_COLLECTION)
    clock.advance(11)
    ex.run_until_idle()
    doc = ex.run(run_id)
    assert step_state(doc, "h")["status"] == "failed"
    assert step_state(doc, "h")["error"]["code"] == "timeout"
    with pytest.raises(AskError) as e:
        answer_ask(store, ex, ask["id"], "yes", "alice")
    assert e.value.code == "ask_expired"
    assert store.get(ASKS_COLLECTION, ask["id"])["status"] == "expired"


def test_answer_after_deadline_before_housekeeping_is_expired(store, ex, clock):
    start(ex, timeout_s=10)
    (ask,) = store.find(ASKS_COLLECTION)
    clock.advance(11)
    with pytest.raises(AskError) as e:
        answer_ask(store, ex, ask["id"], "yes", "alice", clock=clock)
    assert e.value.code == "ask_expired"


def test_missing_question_fails_non_retryable(store, human, clock, sink):
    from culture_rules.engine.actorport import InvocationContext

    ctx = InvocationContext("r", "h", "actor_task", "spark", 1, None, {})
    res = human.invoke({}, "k", clock(), context=ctx)
    assert res.outcome == "failed"
    assert res.retryable is False
    assert not store.find(ASKS_COLLECTION)
    assert not sink.envelopes
