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
)
from culture_rules.engine.actorport import (
    BLOCKED,
    COMPLETED,
    FAILED,
    InvocationContext,
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
    with pytest.raises(ConnectionError):
        actor.invoke({}, "k2", DEADLINE, context=ctx("a"))
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
    assert res.outcome == FAILED and res.retryable is False
    assert inner.effects["k3"] == 0
    err = parse_limit_error(res.error)
    assert err["code"] == "over_budget"
    assert err["actor"] == "bot" and err["token_budget"] == 15 and err["tokens_used"] == 20
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
    assert actor.usage()["tokens"] == 40 and actor.usage()["in_flight"] == 0


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
