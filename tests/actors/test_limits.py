"""Per-actor budgets and concurrency caps (LimitedActor over the ActorPort seam)."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from culture_rules.actors.config import ActorConfig
from culture_rules.actors.limits import (
    USAGE_COLLECTION,
    ActorLimits,
    LimitedActor,
    limits_from_config,
    parse_limit_error,
    pool_cap,
    pool_doc_id,
)
from culture_rules.engine.actorport import (
    BLOCKED,
    COMPLETED,
    FAILED,
    InvocationContext,
    InvocationResult,
)
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import T0, Clock, FakeActor

DEADLINE = T0.replace(hour=13)


def ctx(step: str = "s") -> InvocationContext:
    return InvocationContext(run_id="r1", step_id=step, kind="step", host="h1", actor="bot")


def make(limits: ActorLimits, inner: FakeActor | None = None, store=None, clock=None):
    inner = inner or FakeActor(default=lambda i, c: {"tokens": 10})
    store = store or MemoryStore()
    clock = clock or Clock()
    return LimitedActor(inner, "bot", limits, store, clock=clock), inner, store, clock


def test_under_limits_passes_through_and_counts_tokens():
    actor, inner, store, _ = make(ActorLimits(token_budget=100, max_concurrency=2))
    res = actor.invoke({}, "k1", DEADLINE, context=ctx())
    assert res.outcome == COMPLETED
    assert inner.effects["k1"] == 1
    assert actor.usage()["tokens"] == 10
    assert actor.usage()["in_flight"] == 0
    assert store.get(USAGE_COLLECTION, "bot") is not None


def test_concurrency_cap_blocks_then_releases():
    inner = FakeActor().on("a", ("accept",))
    actor, inner, _, _ = make(ActorLimits(max_concurrency=1), inner)
    first = actor.invoke({}, "k1", DEADLINE, context=ctx("a"))
    assert first.outcome == "accepted"
    second = actor.invoke({}, "k2", DEADLINE, context=ctx("b"))
    assert second.outcome == BLOCKED
    assert inner.effects["k2"] == 0  # queued, not run
    actor.release("k1")
    third = actor.invoke({}, "k2", DEADLINE, context=ctx("b"))
    assert third.outcome == COMPLETED


def test_same_key_does_not_take_two_slots():
    inner = FakeActor().on("a", ("accept",))
    actor, inner, _, _ = make(ActorLimits(max_concurrency=1), inner)
    assert actor.invoke({}, "k1", DEADLINE, context=ctx("a")).outcome == "accepted"
    again = actor.invoke({}, "k1", DEADLINE, context=ctx("a"))
    assert again.outcome != BLOCKED
    assert actor.usage()["in_flight"] == 1


def test_slot_released_on_failure_and_on_exception():
    inner = FakeActor().on("a", ("fail", "boom", True), ("lose_ack", {}))
    actor, inner, _, _ = make(ActorLimits(max_concurrency=1), inner)
    assert actor.invoke({}, "k1", DEADLINE, context=ctx("a")).outcome == FAILED
    assert actor.usage()["in_flight"] == 0
    context = ctx("a")
    with pytest.raises(ConnectionError):
        actor.invoke({}, "k2", DEADLINE, context=context)
    assert actor.usage()["in_flight"] == 0


def test_abandoned_accepted_slot_expires_at_deadline():
    inner = FakeActor().on("a", ("accept",))
    actor, _, _, clock = make(ActorLimits(max_concurrency=1), inner)
    actor.invoke({}, "k1", DEADLINE, context=ctx("a"))
    assert actor.invoke({}, "k2", DEADLINE, context=ctx("b")).outcome == BLOCKED
    clock.now = DEADLINE.replace(minute=1)
    assert actor.invoke({}, "k2", DEADLINE.replace(hour=14), context=ctx("b")).outcome == COMPLETED


def test_over_budget_refuses_with_structured_non_retryable_error():
    actor, inner, _, _ = make(ActorLimits(token_budget=15))
    assert actor.invoke({}, "k1", DEADLINE, context=ctx()).outcome == COMPLETED
    assert actor.invoke({}, "k2", DEADLINE, context=ctx()).outcome == COMPLETED  # 20 >= 15
    res = actor.invoke({}, "k3", DEADLINE, context=ctx())
    assert res.outcome == FAILED
    assert res.retryable is False
    assert inner.effects["k3"] == 0
    err = parse_limit_error(res.error)
    assert err["code"] == "over_budget"
    assert err["actor"] == "bot"
    assert err["token_budget"] == 15
    assert err["tokens_used"] == 20
    assert json.loads(res.error) == err


def test_replay_of_completed_key_is_not_refused_when_over_budget():
    actor, inner, _, _ = make(ActorLimits(token_budget=5))
    assert actor.invoke({}, "k1", DEADLINE, context=ctx()).outcome == COMPLETED
    again = actor.invoke({}, "k1", DEADLINE, context=ctx())  # lost-ack retry
    assert again.outcome == COMPLETED
    assert inner.effects["k1"] == 1
    assert actor.usage()["tokens"] == 10  # counted once


def test_budget_resets_next_utc_day():
    actor, _, _, clock = make(ActorLimits(token_budget=5))
    actor.invoke({}, "k1", DEADLINE, context=ctx())
    assert actor.invoke({}, "k2", DEADLINE, context=ctx()).outcome == FAILED
    clock.advance(24 * 3600)
    assert actor.invoke({}, "k2", DEADLINE.replace(day=4), context=ctx()).outcome == COMPLETED


def test_no_limits_never_blocks():
    actor, _, _, _ = make(ActorLimits())
    for i in range(20):
        assert actor.invoke({}, f"k{i}", DEADLINE, context=ctx()).outcome == COMPLETED


def test_caps_hold_across_hosts_sharing_a_store():
    store = MemoryStore()
    inner = FakeActor().on("a", ("accept",), ("accept",))
    a, _, _, _ = make(ActorLimits(max_concurrency=1), inner, store)
    b, _, _, _ = make(ActorLimits(max_concurrency=1), FakeActor(), store.peer())
    assert a.invoke({}, "k1", DEADLINE, context=ctx("a")).outcome == "accepted"
    assert b.invoke({}, "k2", DEADLINE, context=ctx("b")).outcome == BLOCKED


def test_concurrent_invocations_never_exceed_cap():
    store = MemoryStore()
    inners = [FakeActor().on("a", ("accept",)) for _ in range(8)]
    actors = [
        LimitedActor(inners[i], "bot", ActorLimits(max_concurrency=3), store.peer(), clock=Clock())
        for i in range(8)
    ]

    def go(i):
        return actors[i].invoke({}, f"k{i}", DEADLINE, context=ctx("a")).outcome

    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(go, range(8)))
    assert outcomes.count("accepted") == 3
    assert outcomes.count(BLOCKED) == 5


def test_record_tokens_for_accepted_work_completed_later():
    inner = FakeActor().on("a", ("accept",))
    actor, _, _, _ = make(ActorLimits(token_budget=100), inner)
    actor.invoke({}, "k1", DEADLINE, context=ctx("a"))
    actor.release("k1", tokens=40)
    assert actor.usage()["tokens"] == 40
    assert actor.usage()["in_flight"] == 0


def test_warn_flag_follows_warn_pct():
    actor, _, _, _ = make(ActorLimits(token_budget=100, token_budget_warn_pct=50))
    assert actor.usage()["budget_warning"] is False
    actor.release("x", tokens=60)
    assert actor.usage()["budget_warning"] is True
    assert actor.usage()["over_budget"] is False


# ---- alignment with culture.yaml field names ------------------------------


def test_limits_align_with_culture_yaml_token_budget_fields():
    cfg = ActorConfig(
        key="x", extras={"token_budget": 5000, "token_budget_warn_pct": 90, "max_concurrency": 2}
    )
    lim = limits_from_config(cfg)
    assert lim == ActorLimits(token_budget=5000, token_budget_warn_pct=90, max_concurrency=2)
    assert ActorLimits().token_budget_warn_pct == 80  # culture's default
    assert limits_from_config(ActorConfig(key="y")) == ActorLimits()


def test_limits_from_culture_yaml_text():
    pytest.importorskip("yaml")
    from culture_rules.actors.config import parse_culture_yaml

    text = "agents:\n- suffix: a\n  backend: claude\n  token_budget: 1234\n"
    assert limits_from_config(parse_culture_yaml(text)[0]).token_budget == 1234


@pytest.mark.parametrize("bad", [0, -1, True, "5", 1.5])
def test_invalid_budget_degrades_like_culture(bad):
    # culture warns and degrades an invalid token_budget to unset; so do we
    cfg = ActorConfig(key="x", extras={"token_budget": bad})
    assert limits_from_config(cfg).token_budget is None


def test_invalid_warn_pct_degrades_to_default():
    cfg = ActorConfig(key="x", extras={"token_budget": 10, "token_budget_warn_pct": 101})
    assert limits_from_config(cfg).token_budget_warn_pct == 80


# ------------------------------------------------- #3 retries after a failed attempt


def _retrying(limits: ActorLimits, *behaviours):
    inner = FakeActor().on("k", *behaviours).on("hold", ("accept",))
    return make(limits, inner)


def test_retry_after_failure_is_blocked_at_the_cap():
    actor, inner, _, _ = _retrying(
        ActorLimits(max_concurrency=1), ("fail", "transient", True), ("complete", {})
    )
    assert actor.invoke({}, "K", DEADLINE, context=ctx("k")).outcome == FAILED
    assert actor.invoke({}, "J", DEADLINE, context=ctx("hold")).outcome == "accepted"
    retry = actor.invoke({}, "K", DEADLINE, context=ctx("k"))
    assert retry.outcome == BLOCKED
    assert inner.effects["K"] == 0


def test_retry_after_failure_is_refused_over_budget():
    actor, inner, _, _ = _retrying(ActorLimits(token_budget=10), ("fail", "transient", True))
    assert actor.invoke({}, "K", DEADLINE, context=ctx("k")).outcome == FAILED
    actor.release("other", tokens=10)
    retry = actor.invoke({}, "K", DEADLINE, context=ctx("k"))
    assert retry.outcome == FAILED
    assert parse_limit_error(retry.error)["code"] == "over_budget"
    assert inner.effects["K"] == 0


def test_retry_after_failure_counts_its_tokens():
    actor, _, _, _ = _retrying(
        ActorLimits(token_budget=1000), ("fail", "transient", True), ("complete", {"tokens": 500})
    )
    actor.invoke({}, "K", DEADLINE, context=ctx("k"))
    assert actor.invoke({}, "K", DEADLINE, context=ctx("k")).outcome == COMPLETED
    assert actor.usage()["tokens"] == 500
    assert actor.usage()["in_flight"] == 0


def test_failed_attempt_frees_its_slot_and_is_not_remembered_as_done():
    actor, _, store, _ = _retrying(ActorLimits(max_concurrency=1), ("fail", "transient", True))
    actor.invoke({}, "K", DEADLINE, context=ctx("k"))
    doc = store.get(USAGE_COLLECTION, "bot")
    assert doc["inflight"] == []
    assert "K" not in doc["done"]


def test_release_marks_done_only_for_completed_work():
    actor, _, store, _ = make(ActorLimits())
    actor.release("failed-later", tokens=3)
    actor.release("completed-later", tokens=4, completed=True)
    doc = store.get(USAGE_COLLECTION, "bot")
    assert doc["done"] == ["completed-later"]
    assert doc["tokens"] == 7


class _Scripted:
    """An inner port returning scripted results per key (one per call)."""

    def __init__(self, **script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []

    def invoke(self, input, idempotency_key, deadline, *, context):
        self.calls.append(idempotency_key)
        return self.script[idempotency_key].pop(0)


def test_tokens_spent_by_a_failed_attempt_are_counted():
    spent = InvocationResult(FAILED, {"tokens": 30}, error="transient")
    inner = _Scripted(K=[spent, InvocationResult.completed({"tokens": 20})])
    actor = LimitedActor(inner, "bot", ActorLimits(token_budget=1000), MemoryStore(), clock=Clock())
    actor.invoke({}, "K", DEADLINE, context=ctx())
    assert actor.usage()["tokens"] == 30
    actor.invoke({}, "K", DEADLINE, context=ctx())
    assert actor.usage()["tokens"] == 50
    assert inner.calls == ["K", "K"]


# ------------------------------------------------- #35 concurrency_pool (d28/d29 safety net)


def pooled(actor_id: str, store, *, cap=1, budget=None, inner=None, clock=None):
    inner = inner or FakeActor().on("a", ("accept",), ("accept",)).on("b", ("accept",))
    limits = ActorLimits(token_budget=budget, max_concurrency=cap, concurrency_pool="qwen")
    return LimitedActor(inner, actor_id, limits, store, clock=clock or Clock()), inner


def test_concurrency_pool_is_read_from_config_and_invalid_values_degrade():
    cfg = ActorConfig(key="x", extras={"max_concurrency": 1, "concurrency_pool": "qwen-spark2"})
    assert limits_from_config(cfg).concurrency_pool == "qwen-spark2"
    for bad in ("", "  ", 3, True, None):
        cfg = ActorConfig(key="x", extras={"concurrency_pool": bad})
        assert limits_from_config(cfg).concurrency_pool is None


def test_two_actors_in_one_pool_share_one_slot():
    store = MemoryStore()
    a, _ = pooled("qwen-a", store)
    b, inner_b = pooled("qwen-b", store)
    assert a.invoke({}, "k1", DEADLINE, context=ctx("a")).outcome == "accepted"
    assert b.invoke({}, "k2", DEADLINE, context=ctx("b")).outcome == BLOCKED
    assert inner_b.effects["k2"] == 0
    slots = store.get(USAGE_COLLECTION, pool_doc_id("qwen"))
    assert [s["key"] for s in slots["inflight"]] == ["k1"]
    assert slots["pool"] == "qwen"
    a.release("k1", tokens=7, completed=True)
    assert store.get(USAGE_COLLECTION, pool_doc_id("qwen"))["inflight"] == []
    assert b.invoke({}, "k2", DEADLINE, context=ctx("b")).outcome == "accepted"


def test_pool_holds_across_hosts_sharing_a_store():
    store = MemoryStore()
    a, _ = pooled("qwen-a", store.peer())
    b, _ = pooled("qwen-b", store.peer())
    assert a.invoke({}, "k1", DEADLINE, context=ctx("a")).outcome == "accepted"
    assert b.invoke({}, "k2", DEADLINE, context=ctx("b")).outcome == BLOCKED


def test_token_budgets_stay_per_actor_in_a_pool():
    store = MemoryStore()
    a, _ = pooled("qwen-a", store, cap=None, budget=10)
    b, _ = pooled("qwen-b", store, cap=None, budget=10)
    a.invoke({}, "k1", DEADLINE, context=ctx("a"))
    a.release("k1", tokens=10, completed=True)
    assert store.get(USAGE_COLLECTION, "qwen-a")["tokens"] == 10
    assert a.invoke({}, "k3", DEADLINE, context=ctx("x")).outcome == FAILED  # a is over
    assert b.invoke({}, "k4", DEADLINE, context=ctx("x")).outcome == COMPLETED  # b is not
    assert store.get(USAGE_COLLECTION, "qwen-b")["tokens"] == 0
    assert a.usage()["concurrency_pool"] == "qwen"


def test_pool_replays_a_completed_key_and_counts_its_tokens_once():
    store = MemoryStore()
    inner = FakeActor(default=lambda i, c: {"tokens": 5})
    a, _ = pooled("qwen-a", store, inner=inner)
    assert a.invoke({}, "k1", DEADLINE, context=ctx("x")).outcome == COMPLETED
    assert a.invoke({}, "k1", DEADLINE, context=ctx("x")).outcome == COMPLETED
    assert inner.effects["k1"] == 1
    assert store.get(USAGE_COLLECTION, "qwen-a")["tokens"] == 5
    a.release("k1", tokens=5, completed=True)  # a repeated delivery counts nothing twice
    assert store.get(USAGE_COLLECTION, "qwen-a")["tokens"] == 5


def test_pool_slot_of_an_exception_is_dropped_and_expires_at_its_deadline():
    store, clock = MemoryStore(), Clock()
    inner = FakeActor().on("a", ("lose_ack", {}), ("accept",))
    a, _ = pooled("qwen-a", store, inner=inner, clock=clock)
    b, _ = pooled("qwen-b", store, clock=clock)
    with pytest.raises(ConnectionError):
        a.invoke({}, "k1", DEADLINE, context=ctx("a"))
    assert a.usage()["in_flight"] == 0
    assert a.invoke({}, "k2", DEADLINE, context=ctx("a")).outcome == "accepted"
    assert b.invoke({}, "k3", DEADLINE, context=ctx("b")).outcome == BLOCKED
    clock.now = DEADLINE.replace(minute=1)
    later = DEADLINE.replace(hour=14)
    assert b.invoke({}, "k3", later, context=ctx("b")).outcome == "accepted"


def test_concurrent_pool_invocations_never_exceed_the_pool_cap():
    store = MemoryStore()
    actors = [
        LimitedActor(
            FakeActor().on("a", ("accept",)),
            f"qwen-{i % 3}",
            ActorLimits(max_concurrency=2, concurrency_pool="qwen"),
            store.peer(),
            clock=Clock(),
        )
        for i in range(8)
    ]

    def go(i):
        return actors[i].invoke({}, f"k{i}", DEADLINE, context=ctx("a")).outcome

    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(go, range(8)))
    assert outcomes.count("accepted") == 2
    assert outcomes.count(BLOCKED) == 6


def _actor_doc(id_, **params):
    return {"id": id_, "name": id_, "kind": "agent", "enabled": True, "params": params}


def test_pool_cap_is_the_smallest_max_concurrency_among_enabled_members():
    store = MemoryStore()
    store.put("actors", _actor_doc("a", concurrency_pool="qwen", max_concurrency=2))
    store.put("actors", _actor_doc("b", concurrency_pool="qwen", max_concurrency=1))
    store.put("actors", _actor_doc("c", concurrency_pool="qwen"))  # no cap of its own
    store.put("actors", _actor_doc("d", concurrency_pool="other", max_concurrency=5))
    assert pool_cap(store, "qwen") == 1
    assert pool_cap(store, "other") == 5
    assert pool_cap(store, "nobody") is None
    store.put(
        "actors", {**_actor_doc("b", concurrency_pool="qwen", max_concurrency=1), "enabled": False}
    )
    assert pool_cap(store, "qwen") == 2
    store.put(
        "actors",
        {
            **_actor_doc("a", concurrency_pool="qwen", max_concurrency=2),
            "deleted_at": "2026-10-10T00:00:00Z",
        },
    )
    assert pool_cap(store, "qwen") is None
