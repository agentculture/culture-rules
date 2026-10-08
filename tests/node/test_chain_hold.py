"""d21 phase 2 (B): a chain of rules on one concurrency key is one unit per key.

Between two stages of a chain (a run ends, the rule that continues it fires on its
``rules.run.*`` event) the key used to be free: a fresh external event arriving in that
window took it, and the next stage was deduplicated behind the new run. The run's terminal
transition now *holds* the key for the rules that would continue it; only the continuation
is admitted on it, and the pending external event fires once the chain ends.
"""

from __future__ import annotations

from datetime import timedelta

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.chain_hold import HOLD_TTL
from culture_rules.engine.claims import (
    RULE_ATTEMPT_BUDGETS,
    budget_id,
    idempotency_key,
    release_concurrency,
    reserve_concurrency,
)
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.run_events import run_event_id
from culture_rules.store.memory import MemoryStore
from tests.node.test_run_events import (
    FAILED,
    KEY,
    cluster,
    cycles,
    decision,
    fix_rule,
    follow_rule,
    rule_is,
    settle,
    verdict_is,
)

PR_KEY = "pr:org/repo#7"


def budget(c):
    return c.base.get(RULE_ATTEMPT_BUDGETS, budget_id(PR_KEY))


def review_rule(**kw):
    return follow_rule(
        condition=rule_is("fix"), concurrency_key=KEY, counts_toward_budget=False, **kw
    )


def finish_fix(c, event="evt_1", verdict="approve"):
    """Deliver the accepted ``s1`` of the fix run: its run ends in the next drive."""
    run = c.run("fix", event)
    assert run["status"] == "running"
    node = next(iter(c.nodes.values()))
    out = {"verdict": verdict, "note": "internal"}
    assert node.deliver(idempotency_key(run["id"], "s1"), InvocationResult.completed(out))


def end_runs(c):
    """Drive the runs to their end without a node cycle (no event is delivered)."""
    next(iter(c.nodes.values())).executor.run_until_idle()


def started_fix(c, n=1, accept=True):
    if accept:
        c.actor.on("s1", ("accept",))
    settle(c, n)
    cycles(c, 2)
    assert c.run("fix", f"evt_{n}")["status"] == "running"


# --------------------------------------------------------------------------- interleaving


def test_a_fresh_event_between_two_stages_waits_for_the_chain_and_fires_after_it():
    c = cluster(fix_rule(concurrency_key=KEY), review_rule())
    started_fix(c)
    finish_fix(c)
    end_runs(c)  # the fix run ends; its own event is not delivered yet
    fix = c.run("fix", "evt_1")
    assert fix["status"] == "succeeded"
    # A fresh trigger arrives in the window: it is ingested BEFORE the fix run's own event
    # (the outbox delivers that one in this same cycle, after ingest), the phase-1 gap.
    settle(c, 2)
    cycles(c, 1)
    review = c.run("review", run_event_id(fix["id"]))
    assert review is not None, "the continuation must be admitted on the held key"
    assert c.run("fix", "evt_2") is None
    assert decision(c, "fix", "evt_2")["reason"] == "deduplicated"
    cycles(c, 4)
    # the review (the chain's end) finished: the deduplicated event fires now, once
    assert c.run("review", run_event_id(fix["id"]))["status"] == "succeeded"
    assert c.run("fix", "evt_2") is not None
    assert budget(c)["count"] == 2  # two fix runs; the review was never counted


def test_the_continuation_is_admitted_and_keeps_the_pending_event_for_the_chain_end():
    c = cluster(fix_rule(concurrency_key=KEY), review_rule())
    started_fix(c)
    settle(c, 2)  # deduplicated behind the live fix run: the key's pending event
    cycles(c, 1)
    assert budget(c)["pending_event_id"] == "evt_2"
    # the fix's own action completes; the review's stays open once it starts
    c.actor.on("@action", ("complete", {}), ("accept",))
    finish_fix(c)
    cycles(c, 2)
    fix = c.run("fix", "evt_1")
    review = c.run("review", run_event_id(fix["id"]))
    assert review is not None and review["status"] == "running"
    doc = budget(c)
    assert doc["run_id"] == review["id"]
    assert doc["pending_event_id"] == "evt_2"  # NOT coalesced away by the continuation
    assert c.run("fix", "evt_2") is None


def test_no_continuation_means_no_hold_and_the_pending_event_fires_at_once():
    c = cluster(
        fix_rule(concurrency_key=KEY),
        follow_rule(
            condition={"op": "and", "args": [rule_is("fix"), verdict_is("approve")]},
            concurrency_key=KEY,
            counts_toward_budget=False,
        ),
    )
    started_fix(c)
    settle(c, 2)
    cycles(c, 1)
    finish_fix(c, verdict="request_changes")  # the review's condition will not hold
    cycles(c, 4)
    fix = c.run("fix", "evt_1")
    assert fix["status"] == "succeeded"
    assert not budget(c).get("hold")
    assert c.run("review", run_event_id(fix["id"])) is None
    assert c.run("fix", "evt_2") is not None


def test_a_continuation_disabled_before_its_event_is_evaluated_releases_the_key():
    c = cluster(fix_rule(concurrency_key=KEY), review_rule())
    started_fix(c)
    settle(c, 2)
    cycles(c, 1)
    finish_fix(c)
    # drive the fix run to its end without delivering its event yet
    node = next(iter(c.nodes.values()))
    node.executor.run_until_idle()
    fix = c.run("fix", "evt_1")
    assert fix["status"] == "succeeded"
    hold = budget(c)["hold"]
    assert hold["run_id"] == fix["id"] and hold["rules"] == ["review"]
    # the operator disables the review rule before its event is decided
    doc = c.base.get("rules", "review")
    c.base.put("rules", {**doc, "enabled": False})
    cycles(c, 4)
    assert c.run("review", run_event_id(fix["id"])) is None
    assert not budget(c).get("hold")
    assert c.run("fix", "evt_2") is not None, "the pending event fires once the hold ends"


def test_the_hold_is_set_in_the_terminal_transition():
    c = cluster(fix_rule(concurrency_key=KEY), review_rule())
    started_fix(c)
    finish_fix(c)
    node = next(iter(c.nodes.values()))
    node.executor.run_until_idle()
    fix = c.run("fix", "evt_1")
    doc = budget(c)
    assert doc["run_id"] == fix["id"]
    assert doc["hold"]["run_id"] == fix["id"]
    assert doc["hold"]["rules"] == ["review"]
    assert doc["hold"]["since"]


def test_a_failed_run_holds_only_for_rules_on_rules_run_failed():
    c = cluster(fix_rule(concurrency_key=KEY), review_rule())
    c.actor.on("s1", ("fail", "broken", False))
    settle(c)
    cycles(c, 1)  # fired, started and failed in this cycle; its event is not out yet
    assert c.run("fix", "evt_1")["status"] == "failed"
    assert not budget(c).get("hold")  # nothing continues a failed run here
    c2 = cluster(
        fix_rule(concurrency_key=KEY),
        follow_rule(
            type=FAILED, condition=rule_is("fix"), concurrency_key=KEY, counts_toward_budget=False
        ),
    )
    c2.actor.on("s1", ("fail", "broken", False))
    settle(c2)
    cycles(c2, 1)
    fix = c2.run("fix", "evt_1")
    assert fix["status"] == "failed"
    assert budget(c2)["hold"]["rules"] == ["review"]
    cycles(c2, 3)
    assert c2.run("review", run_event_id(fix["id"]))["status"] == "succeeded"


def test_a_keyless_run_holds_nothing():
    c = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(c)
    cycles(c, 4)
    assert c.base.find(RULE_ATTEMPT_BUDGETS) == []
    fix = c.run("fix", "evt_1")
    assert c.run("review", run_event_id(fix["id"]))["status"] == "succeeded"


# --------------------------------------------------------------------------- the budget doc


def _held(store, *, since="2026-01-01T00:00:00+00:00", rules=("review",)):
    store.put("runs", {"id": "run-1", "status": "succeeded"})
    doc = store.get(RULE_ATTEMPT_BUDGETS, budget_id("k"))
    store.put(
        RULE_ATTEMPT_BUDGETS,
        {
            **doc,
            "hold": {"run_id": "run-1", "rules": list(rules), "since": since},
            "revision": doc["revision"] + 1,
        },
    )


def _run_event(run_id="run-1"):
    return {"id": run_event_id(run_id), "type": "rules.run.succeeded", "data": {"run_id": run_id}}


def test_reserve_refuses_any_other_event_while_the_hold_lasts():
    from datetime import UTC, datetime

    store = MemoryStore()
    assert reserve_concurrency(store, "fix", "k", "run-1", "i-1", 3) is None
    _held(store, since=datetime(2026, 1, 1, tzinfo=UTC).isoformat())
    now = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    other = {"id": "evt_9", "type": "github.pr.checks_settled", "data": {}}
    assert (
        reserve_concurrency(store, "fix", "k", "run-2", "i-2", 3, event=other, now=now)
        == "deduplicated"
    )
    # the continuation (an event of the held run) is admitted
    assert (
        reserve_concurrency(
            store, "review", "k", "run-3", "i-3", None, counts=False, event=_run_event(), now=now
        )
        is None
    )
    doc = store.get(RULE_ATTEMPT_BUDGETS, budget_id("k"))
    assert doc["run_id"] == "run-3" and not doc.get("hold")
    assert doc["count"] == 1


def test_an_expired_hold_no_longer_blocks_the_key():
    from datetime import UTC, datetime

    store = MemoryStore()
    assert reserve_concurrency(store, "fix", "k", "run-1", "i-1", 3) is None
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _held(store, since=t0.isoformat())
    late = t0 + HOLD_TTL + timedelta(seconds=1)
    other = {"id": "evt_9", "type": "github.pr.checks_settled", "data": {}}
    assert reserve_concurrency(store, "fix", "k", "run-2", "i-2", 3, event=other, now=late) is None


def test_the_holders_end_does_not_release_a_held_key():
    store = MemoryStore()
    assert reserve_concurrency(store, "fix", "k", "run-1", "i-1", 3) is None
    from culture_rules.engine.claims import note_deduplicated

    note_deduplicated(store, "fix", "k", "evt_2")
    _held(store, since="2999-01-01T00:00:00+00:00")
    assert release_concurrency(store, budget_id("k"), "run-1") is None  # held: nothing fires
    doc = store.get(RULE_ATTEMPT_BUDGETS, budget_id("k"))
    assert doc["pending_event_id"] == "evt_2"


def test_a_forged_event_naming_the_held_run_is_not_a_continuation():
    c = cluster(fix_rule(concurrency_key=KEY), review_rule())
    started_fix(c)
    finish_fix(c)
    node = next(iter(c.nodes.values()))
    node.executor.run_until_idle()
    fix = c.run("fix", "evt_1")
    forged = {
        "id": "evt_forged",
        "type": "rules.run.succeeded",
        "source": "app://elsewhere",
        "specversion": "1.0",
        "time": "2026-01-01T00:00:00Z",
        "data": {
            "run_id": fix["id"],
            "rule_id": "fix",
            "concurrency_key": PR_KEY,
            "repository": "org/repo",
            "number": 7,
        },
    }
    c.publish(forged)
    cycles(c, 3)
    assert c.run("review", "evt_forged") is None
    assert c.run("review", run_event_id(fix["id"])) is not None  # the genuine one continued
    assert len(c.base.find(RUNS_COLLECTION, {"rule_id": "review"})) == 1


def test_a_restore_drops_a_held_reservation_and_the_continuation_still_runs():
    from tests.node.test_run_events_review6 import _restore

    src = cluster(fix_rule(concurrency_key=KEY), review_rule())
    started_fix(src)
    finish_fix(src)
    end_runs(src)
    assert budget(src)["hold"]["rules"] == ["review"]
    c, report = _restore(src)
    assert report.reservations_dropped == 1
    doc = budget(c)
    assert doc["run_id"] is None and doc["hold"] is None
    c.start()
    cycles(c, 4)
    fix = c.run("fix", "evt_1")
    assert c.run("review", run_event_id(fix["id"]))["status"] == "succeeded"
