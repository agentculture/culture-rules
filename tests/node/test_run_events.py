"""d21 phase 1: run-lifecycle events, rules firing on them, the hop cap and the budget flag."""

from __future__ import annotations

import copy

import pytest

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.runs import ACTION_STEP, RUNS_COLLECTION, Containment
from culture_rules.events.emit import MAX_EVENT_HOPS, derive_envelope, event_hops
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.events.triggers import FIRES_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.workflow import Output
from culture_rules.node import run_events as run_events_module
from culture_rules.node.firing import run_id_for
from culture_rules.node.run_events import (
    RUN_EVENT_TYPES,
    RUN_EVENTS_CONSUMER,
    build_run_event,
    run_event_id,
)
from culture_rules.store.port import CURSOR_COLLECTION, cursor_id
from tests.engine.run_helpers import Crash, edge, port, step, workflow
from tests.events.fakes import envelope
from tests.node.test_node import Cluster

PR = {"repository": "org/repo", "number": 7, "head_sha": "abc123", "body": "free text"}
SETTLED = "github.pr.checks_settled"
SUCCEEDED = "rules.run.succeeded"
FAILED = "rules.run.failed"
OPS = "ops@test"


def fix_workflow():
    """One step producing ``verdict`` and ``note``; only ``verdict`` is exported."""
    return workflow(
        (step("s1", outputs=(port("verdict", "string"), port("note", "string"))),),
        id="fix",
        outputs=(Output(name="verdict", type="string", source="steps.s1.outputs.verdict"),),
    )


def fix_rule(**kw):
    return Rule(
        id="fix",
        name="fix",
        trigger=Trigger(kind="event", params={"type": SETTLED}),
        workflow=WorkflowRef(id="fix"),
        action=Action(kind="noop"),
        **kw,
    )


def follow_rule(id="review", type=SUCCEEDED, condition=None, **kw):
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params={"type": type}),
        condition=condition,
        action=Action(kind="noop", params={"event": "trigger.id"}),
        **kw,
    )


def rule_is(*ids):
    return {
        "op": "in",
        "value": {"field": "data.rule_id"},
        "items": {"literal": list(ids)},
    }


def verdict_is(value):
    return {
        "op": "compare",
        "cmp": "==",
        "left": {"field": "data.outputs.verdict"},
        "right": {"literal": value},
    }


def cluster(*rules, hosts=("spark",), verdict="approve"):
    c = Cluster(*hosts)
    c.actor.default = lambda inp, ctx: (
        {"verdict": verdict, "note": "internal"} if ctx.step_id == "s1" else {}
    )
    c.define(fix_workflow(), *rules)
    c.start()
    return c


def settle(c, n=1, **data):
    c.publish(envelope(n, type=SETTLED, data={**PR, **data}))


def cycles(c, n=3, *hosts):
    for _ in range(n):
        reports = c.cycle(*hosts)
        assert all(not r.errors for r in reports.values()), reports


def run_events(c):
    return [
        e["envelope"]
        for e in c.base.find(EVENTS_COLLECTION)
        if e["envelope"]["type"].startswith("rules.run.")
    ]


def decision(c, rule_id, event_id):
    return c.base.get(RULE_DECISIONS, decision_key(rule_id, event_id))


# --------------------------------------------------------------------------- the event


def test_a_succeeded_run_emits_exactly_one_event_with_its_data_verbatim():
    c = cluster(fix_rule(concurrency_key="pr:{trigger.data.repository}#{trigger.data.number}"))
    settle(c)
    cycles(c)
    run = c.run("fix", "evt_1")
    assert run["status"] == "succeeded"
    (env,) = run_events(c)
    assert env["id"] == run_event_id(run["id"])
    assert env["type"] == SUCCEEDED
    assert env["source"] == "culture-rules://runs"
    assert env["time"] == run["finished_at"]
    assert env["causationId"] == "evt_1"
    assert env["correlationId"] == "evt_1"
    assert env["runId"] == run["id"]
    assert env["hops"] == 1
    assert env["data"] == {
        "repository": "org/repo",
        "number": 7,
        "head_sha": "abc123",
        "run_id": run["id"],
        "rule_id": "fix",
        "workflow_id": "fix",
        "workflow_version": 1,
        "status": "succeeded",
        "concurrency_key": "pr:org/repo#7",
        "outputs": {"verdict": "approve"},
        "error_code": None,
        "error_message": None,
        "trigger_event_id": "evt_1",
        "trigger_type": SETTLED,
    }
    assert "body" not in env["data"]  # free text of the trigger is never copied


def test_a_failed_run_emits_rules_run_failed_with_its_error_code():
    c = cluster(fix_rule())
    c.actor.on(ACTION_STEP, ("fail", "broken", False))
    settle(c)
    cycles(c)
    run = c.run("fix", "evt_1")
    assert run["status"] == "failed"
    (env,) = run_events(c)
    assert env["type"] == FAILED
    assert env["data"]["status"] == "failed"
    assert env["data"]["error_code"] == run["error"]["code"]
    assert env["data"]["error_message"] == run["error"]["message"]


def test_only_explicitly_exported_outputs_are_carried():
    c = cluster(fix_rule())
    settle(c)
    cycles(c)
    run = copy.deepcopy(c.run("fix", "evt_1"))
    run["outputs"] = {"verdict": "approve", "secret_scratch": "x"}
    assert build_run_event(run)["data"]["outputs"] == {"verdict": "approve"}


@pytest.mark.parametrize("status", sorted(RUN_EVENT_TYPES))
def test_each_terminal_status_has_its_own_type(status):
    c = cluster(fix_rule())
    settle(c)
    cycles(c)
    run = {**c.run("fix", "evt_1"), "status": status}
    assert build_run_event(run)["type"] == f"rules.run.{status}"
    assert build_run_event({**run, "status": "running"}) is None


def test_a_cancelled_run_emits_cancelled_and_fires_no_failed_or_succeeded_rule():
    c = cluster(
        fix_rule(),
        follow_rule("on-ok", SUCCEEDED, rule_is("fix")),
        follow_rule("on-fail", FAILED, rule_is("fix")),
    )
    c.actor.on("s1", ("accept",))
    settle(c)
    cycles(c)
    run = c.run("fix", "evt_1")
    assert run["status"] == "running"
    Containment(c.base).cancel(run["id"], OPS)
    cycles(c)
    (env,) = run_events(c)
    assert env["type"] == "rules.run.cancelled"
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "on-ok"}) == []
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "on-fail"}) == []


def test_a_superseded_run_fires_no_failed_or_succeeded_rule():
    c = cluster(
        fix_rule(),
        follow_rule("on-ok", SUCCEEDED, rule_is("fix")),
        follow_rule("on-fail", FAILED, rule_is("fix")),
    )
    c.actor.on("s1", ("accept",))
    settle(c)
    cycles(c)
    run = c.run("fix", "evt_1")
    # the guard's own path is tested in test_wait_step; here: whatever ends a run superseded
    c.base.update_if(
        RUNS_COLLECTION,
        run["id"],
        {"rev": run["rev"]},
        {"status": "superseded", "finished_at": "2026-10-03T12:05:00+00:00", "rev": run["rev"] + 1},
    )
    cycles(c)
    (env,) = run_events(c)
    assert env["type"] == "rules.run.superseded"
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "on-ok"}) == []
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "on-fail"}) == []


# --------------------------------------------------------------------------- firing on it


def test_a_rule_fires_on_a_finished_run_and_reads_its_exported_outputs():
    review_wf = workflow(
        (step("s1", inputs=(port("verdict", "string"), port("number", "integer"))),),
        (edge("inputs", "verdict", "s1", "verdict"), edge("inputs", "number", "s1", "number")),
        id="review-wf",
        inputs=(port("verdict", "string"), port("number", "integer")),
    )
    review = Rule(
        id="review",
        name="review",
        trigger=Trigger(kind="event", params={"type": SUCCEEDED}),
        condition={"op": "and", "args": [rule_is("fix"), verdict_is("approve")]},
        workflow=WorkflowRef(
            id="review-wf",
            inputs={"verdict": "trigger.data.outputs.verdict", "number": "trigger.data.number"},
        ),
        action=Action(kind="noop"),
    )
    c = Cluster("spark")
    c.actor.default = lambda inp, ctx: (
        {"verdict": "approve", "note": ""} if ctx.step_id == "s1" else {}
    )
    c.define(fix_workflow(), review_wf, fix_rule(), review)
    c.start()
    settle(c)
    cycles(c, 4)
    fix_run = c.run("fix", "evt_1")
    run = c.run("review", run_event_id(fix_run["id"]))
    assert run is not None and run["status"] == "succeeded"
    assert run["inputs"] == {"verdict": "approve", "number": 7}
    # the review's own finish emits a second-hop event naming the same PR
    second = c.base.get(EVENTS_COLLECTION, run_event_id(run["id"]))["envelope"]
    assert second["hops"] == 2
    assert second["causationId"] == run_event_id(fix_run["id"])
    assert second["correlationId"] == "evt_1"
    assert (second["data"]["repository"], second["data"]["number"]) == ("org/repo", 7)


def test_a_condition_on_the_outputs_decides():
    c = cluster(fix_rule(), follow_rule("publish", SUCCEEDED, verdict_is("approve")), verdict="no")
    settle(c)
    cycles(c, 4)
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "publish"}) == []


def test_a_downstream_key_template_resolves_on_the_run_event():
    key = "pr:{trigger.data.repository}#{trigger.data.number}"
    c = cluster(
        fix_rule(concurrency_key=key), follow_rule(condition=rule_is("fix"), concurrency_key=key)
    )
    settle(c)
    cycles(c, 4)
    (review,) = c.base.find(RUNS_COLLECTION, {"rule_id": "review"})
    assert review["concurrency_key"] == "pr:org/repo#7"


# --------------------------------------------------------------------------- exactly once


def test_two_nodes_emit_one_event_per_run():
    c = cluster(fix_rule(), hosts=("spark", "thor"))
    settle(c)
    cycles(c, 4)
    assert len(run_events(c)) == 1


def test_a_restarted_node_does_not_emit_again():
    c = cluster(fix_rule())
    settle(c)
    cycles(c)
    c.nodes["spark"] = c.node("spark")
    c.nodes["spark"].start()
    cycles(c)
    assert len(run_events(c)) == 1


def test_a_node_that_dies_before_emitting_leaves_it_to_another_node():
    c = cluster(fix_rule(), hosts=("spark", "thor"))
    settle(c)
    # spark ingests, fires, starts and finishes the run, then dies before its next cycle
    cycles(c, 1, "spark")
    assert c.run("fix", "evt_1")["status"] == "succeeded"
    assert run_events(c) == []
    cycles(c, 1, "thor")
    assert len(run_events(c)) == 1
    cycles(c, 2)
    assert len(run_events(c)) == 1


def test_a_crash_inside_the_emit_transaction_rolls_back_and_the_next_poll_emits_once(
    monkeypatch,
):
    c = cluster(fix_rule())
    real = run_events_module.emit_run_event
    calls = []

    def crash_after_insert(tx, run):
        calls.append(run["id"])
        real(tx, run)  # the insert happens inside the transaction ...
        if len(calls) == 1:
            raise Crash("node died before commit")  # ... and is rolled back with it

    import culture_rules.node.firing as firing

    monkeypatch.setattr(firing, "emit_run_event", crash_after_insert)
    settle(c)
    with pytest.raises(Crash):
        cycles(c, 2)
    assert run_events(c) == []
    assert c.base.find(FIRES_COLLECTION, {"consumer": RUN_EVENTS_CONSUMER}) == []
    c.nodes["spark"] = c.node("spark")  # restarted process
    cycles(c, 2)
    assert len(run_events(c)) == 1


def test_a_stale_cursor_replays_without_a_duplicate_even_without_the_marker():
    c = cluster(fix_rule())
    c.base.save_cursor(RUN_EVENTS_CONSUMER, RUNS_COLLECTION, c.base.head(RUNS_COLLECTION))
    start = c.base.get(CURSOR_COLLECTION, cursor_id(RUN_EVENTS_CONSUMER, RUNS_COLLECTION))
    settle(c)
    cycles(c)
    assert len(run_events(c)) == 1
    # a stale node replays from the old token: the marker absorbs it
    c.base.save_cursor(RUN_EVENTS_CONSUMER, RUNS_COLLECTION, start["token"])
    cycles(c)
    assert len(run_events(c)) == 1
    # even with the markers gone, the deterministic id absorbs it
    for marker in c.base.find(FIRES_COLLECTION, {"consumer": RUN_EVENTS_CONSUMER}):
        c.base.delete(FIRES_COLLECTION, marker["id"])
    c.base.save_cursor(RUN_EVENTS_CONSUMER, RUNS_COLLECTION, start["token"])
    cycles(c)
    assert len(run_events(c)) == 1


def test_runs_finished_before_the_consumer_existed_are_not_backfilled():
    c = cluster(fix_rule())
    settle(c)
    cycles(c)
    assert len(run_events(c)) == 1
    # a brand-new consumer name (as on the first node of this version) pins the head
    c.base.delete(CURSOR_COLLECTION, cursor_id(RUN_EVENTS_CONSUMER, RUNS_COLLECTION))
    for e in c.base.find(EVENTS_COLLECTION):
        if e["envelope"]["type"].startswith("rules.run."):
            c.base.delete(EVENTS_COLLECTION, e["id"])
    cycles(c)
    assert run_events(c) == []


def test_emission_waits_out_a_pause_and_then_happens_once():
    c = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    c.actor.on("s1", ("accept",))
    settle(c)
    cycles(c)
    run = c.run("fix", "evt_1")
    ops = Containment(c.base)
    ops.pause(OPS)
    ops.cancel(run["id"], OPS)
    cycles(c, 2)
    assert run_events(c) == []
    ops.resume(OPS)
    cycles(c, 2)
    (env,) = run_events(c)
    assert env["type"] == "rules.run.cancelled"


# --------------------------------------------------------------------------- forged events


def _forged(c, **changes):
    real = build_run_event(c.run("fix", "evt_1"))
    real.update(changes)
    return real


def test_a_forged_run_event_from_the_bus_fires_nothing_and_is_recorded():
    c = cluster(follow_rule("publish", SUCCEEDED, verdict_is("approve")))
    forged = derive_envelope(
        None,
        type=SUCCEEDED,
        source="culture-rules://runs",
        data={"run_id": "run-nope", "rule_id": "fix", "outputs": {"verdict": "approve"}},
        id="evt_forged",
    )
    c.publish(forged)
    cycles(c)
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "publish"}) == []
    record = decision(c, "publish", "evt_forged")
    assert record["reason"] == "run_event_unverified"
    assert "does not exist" in record["detail"]


def _pre_empt(c, mutate):
    """Finish fix's run in one cycle (its event is not emitted until the next poll), then
    publish a tampered copy under the real event id before the engine emits it."""
    settle(c)
    cycles(c, 1)
    run = c.run("fix", "evt_1")
    assert run["status"] == "succeeded" and run_events(c) == []
    forged = build_run_event(run)
    mutate(forged)
    c.publish(forged)
    cycles(c, 2)
    return forged


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e["data"]["outputs"].update(verdict="approve"),
        lambda e: e.update(hops=0),
        lambda e: e.update(causationId="evt_other"),
        lambda e: e.update(type=SUCCEEDED, time="2030-01-01T00:00:00Z"),
        lambda e: e["data"].update(rule_id="trusted-rule"),
    ],
)
def test_a_tampered_event_under_the_real_id_is_refused(mutate):
    on_fix_runs = {
        "op": "compare",
        "cmp": "==",
        "left": {"field": "data.trigger_type"},
        "right": {"literal": SETTLED},
    }
    c = cluster(fix_rule(), follow_rule("publish", SUCCEEDED, on_fix_runs), verdict="no")
    forged = _pre_empt(c, mutate)
    (stored,) = run_events(c)
    assert stored == forged  # the forgery won the id; the genuine insert was absorbed
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "publish"}) == []
    assert decision(c, "publish", forged["id"])["reason"] == "run_event_unverified"


def test_a_faithful_copy_under_the_real_id_fires_exactly_once():
    c = cluster(fix_rule(), follow_rule("publish", SUCCEEDED, verdict_is("approve")))
    _pre_empt(c, lambda e: None)
    assert len(c.base.find(RUNS_COLLECTION, {"rule_id": "publish"})) == 1


def test_a_verified_publish_rule_is_refused_when_only_the_outputs_were_forged():
    c = cluster(fix_rule(), follow_rule("publish", SUCCEEDED, rule_is("fix")), verdict="no")
    forged = _pre_empt(c, lambda e: e["data"]["outputs"].update(verdict="approve"))
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "publish"}) == []
    record = decision(c, "publish", forged["id"])
    assert record["reason"] == "run_event_unverified"
    assert "data differs" in record["detail"]


# --------------------------------------------------------------------------- hop limit


def test_event_hops_reads_absent_as_zero_and_malformed_as_none():
    assert event_hops({"id": "e"}) == 0
    assert event_hops({"hops": 3}) == 3
    for bad in ("3", True, -1, 1.5, None):
        assert event_hops({"hops": bad}) is None


def test_derived_envelopes_count_hops_and_a_malformed_cause_passes_the_cap():
    root = derive_envelope(None, type="t", source="s")
    assert "hops" not in root
    one = derive_envelope(root, type="t", source="s")
    assert one["hops"] == 1
    assert derive_envelope(one, type="t", source="s")["hops"] == 2
    bad = derive_envelope({**one, "hops": "x"}, type="t", source="s")
    assert bad["hops"] == MAX_EVENT_HOPS + 1
    with pytest.raises(ValueError):
        derive_envelope(None, type="t", source="s", hops=-1)


def test_a_ping_pong_of_two_rules_stops_at_the_hop_cap_and_records_why():
    seed = Rule(
        id="seed",
        name="seed",
        trigger=Trigger(kind="event", params={"type": SETTLED}),
        action=Action(kind="noop"),
    )
    ping = follow_rule("ping", SUCCEEDED, rule_is("seed", "pong"))
    pong = follow_rule("pong", SUCCEEDED, rule_is("ping"))
    c = cluster(seed, ping, pong)
    settle(c)
    for _ in range(40):
        cycles(c, 1)
    runs = c.base.find(RUNS_COLLECTION)
    # seed on the external event (hop 0), then one run per event of hop 1 .. MAX_EVENT_HOPS
    assert len(runs) == 1 + MAX_EVENT_HOPS
    hops = sorted(e["hops"] for e in run_events(c))
    assert hops == list(range(1, MAX_EVENT_HOPS + 2))
    (last,) = [e for e in run_events(c) if e["hops"] == MAX_EVENT_HOPS + 1]
    refused = [
        d
        for d in c.base.find(RULE_DECISIONS)
        if d["event_id"] == last["id"] and d["reason"] == "hop_limit"
    ]
    assert len(refused) == 1
    assert f"cap {MAX_EVENT_HOPS}" in refused[0]["detail"]
    assert refused[0]["message"].startswith("hop limit:")
    # it stays quiet
    cycles(c, 3)
    assert len(c.base.find(RUNS_COLLECTION)) == 1 + MAX_EVENT_HOPS


def test_an_external_event_with_a_malformed_hop_count_fires_nothing():
    c = cluster(fix_rule())
    c.publish(envelope(1, type=SETTLED, data=dict(PR), hops="0"))
    cycles(c)
    assert c.run("fix", "evt_1") is None
    assert decision(c, "fix", "evt_1")["reason"] == "hop_limit"


# --------------------------------------------------------------------------- budget flag

KEY = "pr:{trigger.data.repository}#{trigger.data.number}"


def _budget(c):
    return c.base.get(RULE_ATTEMPT_BUDGETS, budget_id("pr:org/repo#7"))


def test_a_rule_outside_the_budget_is_not_counted_nor_refused_but_shares_the_key():
    c = cluster(
        fix_rule(concurrency_key=KEY, max_attempts=1),
        follow_rule(condition=rule_is("fix"), concurrency_key=KEY, counts_toward_budget=False),
    )
    settle(c)
    cycles(c, 4)
    review = c.base.find(RUNS_COLLECTION, {"rule_id": "review"})
    assert len(review) == 1 and review[0]["status"] == "succeeded"
    assert _budget(c)["count"] == 1  # only the fix counted
    # the budget is spent: another fix is refused, the review rule never would be
    settle(c, 2)
    cycles(c, 3)
    assert c.run("fix", "evt_2") is None
    assert decision(c, "fix", "evt_2")["reason"] == "attempt_budget_exhausted"


def test_a_rule_outside_the_budget_still_waits_for_the_keys_active_run():
    c = cluster(
        fix_rule(concurrency_key=KEY),
        Rule(
            id="other",
            name="other",
            trigger=Trigger(kind="event", params={"type": SETTLED}),
            action=Action(kind="noop"),
            concurrency_key=KEY,
            counts_toward_budget=False,
        ),
    )
    c.actor.on("s1", ("accept",))
    settle(c)
    cycles(c)
    assert c.run("fix", "evt_1")["status"] == "running"
    assert c.run("other", "evt_1") is None
    assert decision(c, "other", "evt_1")["reason"] == "deduplicated"


def test_a_failed_start_of_an_uncounted_rule_gives_back_nothing():
    from culture_rules.engine.claims import reserve_concurrency
    from culture_rules.store.memory import MemoryStore

    store = MemoryStore()
    assert reserve_concurrency(store, "fix", "k", "run-1", "i-1", 3) is None
    store.put("runs", {"id": "run-1", "status": "succeeded"})
    assert reserve_concurrency(store, "rev", "k", "run-2", "i-2", None, counts=False) is None
    doc = store.get(RULE_ATTEMPT_BUDGETS, budget_id("k"))
    assert (doc["count"], doc["counted"]) == (1, False)
    store.put("rule_fires", {"id": "i-2", "status": "failed"})
    assert reserve_concurrency(store, "fix", "k", "run-3", "i-3", 3) is None
    assert store.get(RULE_ATTEMPT_BUDGETS, budget_id("k"))["count"] == 2


def test_run_id_for_still_names_the_review_run():
    # the run a rule starts on a run event is keyed like any other: (rule, event)
    assert run_id_for("review", run_event_id("run-x")).startswith("run-")
