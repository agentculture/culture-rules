"""Durable per-rule, per-PR concurrency and consecutive attempt limits."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.runs import ACTION_STEP, RUNS_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger
from tests.events.fakes import envelope
from tests.node.test_node import Cluster

KEY = "{trigger.data.repository}#{trigger.data.number}"
SYNC = "github.pr.synchronize"


def rule(**kw):
    return Rule(
        id="a",
        name="a",
        trigger=Trigger(kind="event", params={"type": SYNC}),
        action=Action(kind="noop"),
        **kw,
    )


def event(n, number=42, **data):
    return envelope(n, type=SYNC, data={"repository": "org/repo", "number": number, **data})


def cluster(**kw):
    c = Cluster("spark", "thor")
    c.define(rule(concurrency_key=KEY, **kw))
    c.start()
    return c


def fire(c, n, number=42, **data):
    c.publish(event(n, number, **data))
    reports = c.cycle()
    assert all(not report.errors for report in reports.values()), reports
    return c.run("a", f"evt_{n}")


def reasons(c):
    return [d["reason"] for d in c.base.find(RULE_DECISIONS)]


def test_same_key_active_run_is_deduplicated_within_a_minute():
    c = cluster()
    c.actor.on(ACTION_STEP, ("accept",))
    assert fire(c, 1)["status"] not in {"succeeded", "failed"}
    c.clock.advance(30)
    assert fire(c, 2) is None
    assert len(c.base.find(RUNS_COLLECTION)) == 1
    assert reasons(c) == ["deduplicated"]


def test_budget_exhausted_then_human_push_resets():
    c = cluster(max_attempts=2)
    c.actor.on(ACTION_STEP, *(("fail", "broken", False),) * 3)
    assert fire(c, 1)["status"] == "failed"
    assert fire(c, 2)["status"] == "failed"
    assert fire(c, 3) is None
    assert reasons(c) == ["attempt_budget_exhausted"]
    # A fresh node still sees the durable budget.
    c.nodes["spark"] = c.node("spark")
    assert fire(c, 4) is None
    assert fire(c, 5, self_authored=False)["status"] == "failed"


def test_old_rule_loads_and_fires_unchanged_without_key_or_budget():
    c = Cluster("spark")
    old = rule().to_dict()
    old.pop("concurrency_key")
    old.pop("max_attempts")
    loaded = Rule.from_dict(old)
    assert loaded.concurrency_key is None and loaded.max_attempts is None
    c.base.put("rules", old)
    c.start()
    c.actor.on(ACTION_STEP, ("accept",), ("accept",))
    assert fire(c, 1) is not None
    assert fire(c, 2) is not None
    assert reasons(c) == []


def test_attempt_counter_is_per_key():
    c = cluster(max_attempts=1)
    c.actor.on(ACTION_STEP, ("fail", "broken", False), ("fail", "broken", False))
    assert fire(c, 1)["status"] == "failed"
    assert fire(c, 2) is None
    assert fire(c, 3, number=43)["status"] == "failed"
    assert fire(c, 4, number=43) is None
    assert reasons(c) == ["attempt_budget_exhausted"] * 2


def test_explicit_green_resets_attempts():
    c = cluster(max_attempts=2)
    c.actor.on(
        ACTION_STEP,
        ("fail", "broken", False),
        ("complete", {}),
        ("fail", "broken", False),
        ("fail", "broken", False),
    )
    for n in range(1, 5):
        if n == 3:
            c.publish({**event(99, conclusion="success"), "type": "github.pr.checks_settled"})
            c.cycle()
        assert fire(c, n) is not None
    assert fire(c, 5) is None
    assert reasons(c) == ["attempt_budget_exhausted"]


def test_human_push_resets_even_while_active():
    c = cluster(max_attempts=1)
    c.actor.on(ACTION_STEP, ("accept",))
    run = fire(c, 1)
    assert fire(c, 2, self_authored=False) is None
    c.base.update_if(RUNS_COLLECTION, run["id"], {}, {"status": "failed"})
    assert fire(c, 3) is not None


def test_human_push_resets_rule_with_a_different_trigger():
    c = cluster(max_attempts=1)
    r = rule(concurrency_key=KEY, max_attempts=1).to_dict()
    r["trigger"]["params"]["type"] = "github.checks.failed"
    c.base.put("rules", r)
    c.actor.on(ACTION_STEP, ("fail", "broken", False))
    c.publish({**event(1), "type": "github.checks.failed"})
    c.cycle()
    assert c.run("a", "evt_1")["status"] == "failed"
    fire(c, 2, self_authored=False)
    c.publish({**event(3), "type": "github.checks.failed"})
    c.cycle()
    assert c.run("a", "evt_3") is not None


def test_two_nodes_racing_different_events_reserve_one_run():
    c = cluster(max_attempts=1)
    barrier = Barrier(2)

    def evaluate(host, n):
        firing = c.nodes[host].firing
        barrier.wait(timeout=5)
        with c.base.peer().transaction() as tx:
            firing._evaluate(tx, {"envelope": event(n)}, placed=False)
        return firing.start_fired()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: evaluate(*args), [("spark", 1), ("thor", 2)]))
    assert sum(map(len, results)) == 1
    assert len(c.base.find(RUNS_COLLECTION)) == 1
    assert reasons(c) == ["deduplicated"]


@pytest.mark.parametrize("status", ["failed", "cancelled", "superseded", "succeeded"])
def test_terminal_run_releases_key(status):
    c = cluster()
    c.actor.on(ACTION_STEP, ("accept",), ("accept",))
    run = fire(c, 1)
    c.base.update_if(RUNS_COLLECTION, run["id"], {}, {"status": status})
    assert fire(c, 2) is not None


def test_self_push_does_not_reset_budget():
    c = cluster(max_attempts=1)
    c.actor.on(ACTION_STEP, ("fail", "broken", False))
    assert fire(c, 1)["status"] == "failed"
    assert fire(c, 2, self_authored=True) is None
    assert fire(c, 3) is None
    assert reasons(c) == ["attempt_budget_exhausted"]


def test_reset_event_replay_does_not_grant_another_attempt():
    c = cluster(max_attempts=1)
    c.actor.on(ACTION_STEP, ("fail", "broken", False), ("fail", "broken", False))
    fire(c, 1)
    assert fire(c, 2, self_authored=False)["status"] == "failed"
    fire(c, 2, self_authored=False)
    assert fire(c, 3) is None
    assert reasons(c) == ["attempt_budget_exhausted"]


def test_pending_intent_holds_key_and_survives_node_restart():
    c = cluster(max_attempts=1)
    firing = c.nodes["spark"].firing
    for n in (1, 2):
        with c.base.transaction() as tx:
            firing._evaluate(tx, {"envelope": event(n)}, placed=False)
    assert c.base.find(RUNS_COLLECTION) == []
    assert reasons(c) == ["deduplicated"]
    replacement = c.node("thor")
    assert len(replacement.firing.start_fired()) == 1


def test_key_template_preserves_literals_and_rejects_missing_values():
    from culture_rules.engine.claims import resolve_concurrency_key

    assert resolve_concurrency_key(KEY, event(1)) == "org/repo#42"
    assert resolve_concurrency_key("fixed-key", event(1)) == "fixed-key"
    with pytest.raises(ValueError, match="missing concurrency key"):
        resolve_concurrency_key("{trigger.data.missing}", event(1))


@pytest.mark.parametrize("existing", [False, True])
def test_claim_cas_loser_rechecks_active_run_and_counter(existing):
    """Force both nodes to read the same revision outside MemoryStore transactions."""
    from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, reserve_concurrency
    from culture_rules.store.memory import MemoryStore

    store = MemoryStore()
    if existing:
        assert reserve_concurrency(store, "a", "pr", "previous", "intent0", 2) is None
        store.put(RUNS_COLLECTION, {"id": "previous", "status": "failed"})
    barrier = Barrier(2)

    class RacingOps:
        def __init__(self):
            self.first = True

        def get(self, collection, doc_id):
            doc = store.get(collection, doc_id)
            if collection == RULE_ATTEMPT_BUDGETS and self.first:
                self.first = False
                barrier.wait(timeout=5)
            return doc

        def update_if(self, *args, **kw):
            return store.update_if(*args, **kw)

    def reserve(n):
        return reserve_concurrency(RacingOps(), "a", "pr", f"run{n}", f"intent{n}", 2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, (1, 2)))
    assert results.count(None) == 1
    assert results.count("deduplicated") == 1
    (budget,) = store.find(RULE_ATTEMPT_BUDGETS)
    assert budget["count"] == (2 if existing else 1)


SETTLED = "github.pr.checks_settled"


def settled(n, conclusion="failure", **data):
    return {**event(n, conclusion=conclusion, **data), "type": SETTLED}


def settled_rule_cluster(max_attempts):
    """A fixer-shaped rule: fires on settled checks, keyed per PR, with a budget."""
    c = cluster(max_attempts=max_attempts)
    doc = rule(concurrency_key=KEY, max_attempts=max_attempts).to_dict()
    doc["trigger"]["params"]["type"] = SETTLED
    c.base.put("rules", doc)
    return c


@pytest.mark.parametrize(
    "identity,author,resets",
    [("bot", "alice", True), ("bot", "BOT", False), (None, "alice", False)],
)
def test_real_sink_human_push_resets_exhausted_budget(identity, author, resets):
    """Through the real hook sink: a human synchronize resets an exhausted budget, the
    App's own push does not, and without a self_identity (no tag at all) nothing does."""
    from culture_rules.events.hook_sink import sink

    c = settled_rule_cluster(max_attempts=1)
    c.publish(settled(1))
    c.cycle()
    assert c.run("a", "evt_1") is not None
    c.publish(settled(2))
    c.cycle()
    assert c.run("a", "evt_2") is None
    assert reasons(c) == ["attempt_budget_exhausted"]
    params = {"surface": "github", "events": [SYNC]}
    if identity is not None:
        params["self_identity"] = identity
    outcome = sink(
        c.base,
        {"id": "app", "enabled": True, "params": params},
        SYNC,
        {"repository": "org/repo", "number": 42},
        "push-1",
        author,
    )
    assert outcome == "accepted"
    c.cycle()
    c.publish(settled(3))
    c.cycle()
    assert (c.run("a", "evt_3") is not None) is resets


def test_unresolved_key_skips_and_consumer_advances():
    """A key that does not resolve (checks_settled may carry number: None) records the
    final skip for that rule only; the consumer keeps going."""
    c = cluster()
    other = rule().to_dict()
    other["id"] = "other"
    c.base.put("rules", other)
    assert fire(c, 1, number=None) is None
    (decision,) = c.base.find(RULE_DECISIONS, {"rule_id": "a"})
    assert decision["reason"] == "concurrency_key_unresolved"
    assert "number" in decision["detail"]
    assert c.run("other", "evt_1") is not None
    assert fire(c, 2) is not None
    assert c.run("other", "evt_2") is not None


def test_reset_skips_unrelated_and_unresolvable_rules():
    from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS

    c = cluster(max_attempts=1)
    for rid, typ, key in [
        ("unrelated", "timer.tick", "fixed"),
        ("missing", "github.checks.failed", "{trigger.data.absent}"),
    ]:
        doc = rule(concurrency_key=key, max_attempts=1).to_dict()
        doc["id"] = rid
        doc["trigger"]["params"]["type"] = typ
        c.base.put("rules", doc)
    assert fire(c, 1, self_authored=False) is not None
    assert {b["rule_id"] for b in c.base.find(RULE_ATTEMPT_BUDGETS)} == {"a"}


def test_app_push_cycles_stop_at_max_attempts():
    """App push -> checks settle red -> new run, repeated: every admitted run counts
    (even succeeded ones), so the loop stops at max_attempts."""
    c = settled_rule_cluster(max_attempts=2)
    for n in range(1, 5):
        fire(c, 100 + n, self_authored=True)
        c.publish(settled(n))
        c.cycle()
    runs = c.base.find(RUNS_COLLECTION)
    assert len(runs) == 2
    assert {r["status"] for r in runs} == {"succeeded"}
    assert reasons(c) == ["attempt_budget_exhausted"] * 2


@pytest.mark.parametrize("conclusion", ["failure", "timeout"])
def test_red_or_timed_out_checks_do_not_reset(conclusion):
    c = cluster(max_attempts=1)
    assert fire(c, 1) is not None
    c.publish(settled(2, conclusion=conclusion))
    c.cycle()
    assert fire(c, 3) is None
    assert reasons(c) == ["attempt_budget_exhausted"]


def test_successful_run_alone_does_not_reset():
    c = cluster(max_attempts=1)
    assert fire(c, 1)["status"] == "succeeded"
    assert fire(c, 2) is None


@pytest.mark.parametrize(
    "fields",
    [
        {"max_attempts": 0},
        {"max_attempts": -1},
        {"concurrency_key": ""},
        {"concurrency_key": "{other.path}"},
        {"concurrency_key": "{trigger}"},
        {"concurrency_key": "{trigger.data"},
        {"concurrency_key": "trigger.data}"},
        {"concurrency_key": "{trigger.data!r}"},
        {"concurrency_key": "{trigger.data:>10}"},
        {"concurrency_key": "{trigger.data.number:}"},
        {"concurrency_key": "{trigger.data[0]}"},
        {"concurrency_key": "{trigger.}"},
        {"concurrency_key": "{}"},
    ],
)
def test_invalid_budget_fields_rejected(fields):
    from culture_rules.model.validate import validate

    errors = validate(rule(**fields))
    assert errors
    assert {e.path.rsplit(".", 1)[-1] for e in errors} <= {"max_attempts", "concurrency_key"}


@pytest.mark.parametrize("template", [KEY, "fixed", "{{literal}}-{trigger.data.number}"])
def test_valid_budget_fields_accepted(template):
    from culture_rules.model.validate import validate

    assert validate(rule(concurrency_key=template, max_attempts=1)) == []


def test_latest_deduplicated_event_fires_after_holding_run_ends():
    c = cluster()
    c.actor.on(ACTION_STEP, ("accept",))
    run = fire(c, 1, head_sha="old")
    assert fire(c, 2, head_sha="middle") is None
    assert fire(c, 3, head_sha="new") is None
    details = [d["detail"] for d in c.base.find(RULE_DECISIONS)]
    assert all(run["id"] in d for d in details), details
    c.clock.advance(1)
    c.base.update_if(RUNS_COLLECTION, run["id"], {}, {"status": "superseded"})
    c.cycle()
    assert c.run("a", "evt_2") is None
    assert c.run("a", "evt_3")["trigger"]["data"]["head_sha"] == "new"
    record = c.base.get(RULE_DECISIONS, decision_key("a", "evt_3"))
    assert record["fire"] is True
    assert record["superseded"][0]["reason"] == "deduplicated"
    # The stale one stays deduplicated, and the coalesced event fired exactly once.
    assert c.base.get(RULE_DECISIONS, decision_key("a", "evt_2"))["reason"] == "deduplicated"
    c.cycle()
    assert len(c.base.find(RUNS_COLLECTION)) == 2


def test_wait_then_superseded_hands_the_key_to_the_new_sha():
    """A run sleeping in its quiet-period wait holds the key; a human push meanwhile is
    deduplicated, the stale run wakes and supersedes itself, and the new SHA is handled."""
    from culture_rules.engine.runs import step_state
    from culture_rules.model.rule import WorkflowRef
    from tests.engine.test_wait_step import SHA_A, SHA_B, Heads, guard_config, two_step

    c = Cluster("spark")
    heads = Heads()
    c.nodes["spark"].executor._head_lookup = heads
    wf = two_step(guard_config(10))
    c.define(
        wf,
        rule(
            concurrency_key=KEY,
            workflow=WorkflowRef(id=wf.id, inputs={"head_sha": "trigger.data.head_sha"}),
        ),
    )
    c.start()
    old = fire(c, 1, head_sha=SHA_A)
    assert step_state(old, "w")["status"] == "sleeping"
    heads.sha = SHA_B
    assert fire(c, 2, head_sha=SHA_B, self_authored=False) is None
    assert reasons(c) == ["deduplicated"]
    c.clock.advance(11)
    c.cycle()
    assert c.run("a", "evt_1")["status"] == "superseded"
    c.cycle()
    new = c.run("a", "evt_2")
    assert new is not None and new["trigger"]["data"]["head_sha"] == SHA_B
    c.clock.advance(11)
    c.cycle()
    assert c.run("a", "evt_2")["status"] == "succeeded"


def test_coalesced_event_waits_out_a_pause():
    from culture_rules.engine.runs import Containment

    c = cluster()
    c.actor.on(ACTION_STEP, ("accept",))
    run = fire(c, 1)
    assert fire(c, 2) is None
    containment = Containment(c.base, clock=c.clock)
    containment.pause("ops@test")
    c.base.update_if(RUNS_COLLECTION, run["id"], {}, {"status": "failed"})
    c.cycle()
    assert c.run("a", "evt_2") is None
    containment.resume("ops@test")
    c.cycle()
    c.cycle()
    assert c.run("a", "evt_2") is not None


def test_release_always_writes_the_budget():
    """The write-skew guard: releasing a key bumps the revision even with nothing pending."""
    from culture_rules.engine.claims import (
        RULE_ATTEMPT_BUDGETS,
        release_concurrency,
        reserve_concurrency,
    )
    from culture_rules.store.memory import MemoryStore

    store = MemoryStore()
    assert reserve_concurrency(store, "a", "pr", "run1", "intent1", None) is None
    (budget,) = store.find(RULE_ATTEMPT_BUDGETS)
    assert release_concurrency(store, budget["id"], "run1") is None
    assert store.get(RULE_ATTEMPT_BUDGETS, budget["id"])["revision"] == budget["revision"] + 1
    # A run that no longer holds the key releases nothing.
    assert release_concurrency(store, budget["id"], "other") is None
    assert store.get(RULE_ATTEMPT_BUDGETS, budget["id"])["revision"] == budget["revision"] + 1


@pytest.mark.parametrize("operation", ["reset", "reserve"])
def test_budget_cas_retries_are_bounded(operation):
    from types import SimpleNamespace

    from culture_rules.engine.claims import (
        RESET_MARKERS,
        reserve_concurrency,
        reset_attempt_budget,
    )
    from culture_rules.store.port import TransientStoreError
    from culture_rules.store.retry import DEFAULT_ATTEMPTS

    class Contended:
        calls = 0

        def get(self, collection, _id):
            if collection == RESET_MARKERS:
                return None  # this reset event has not been applied yet
            return {"id": "b", "count": 0, "revision": 1}

        def insert(self, *_):
            pass

        def update_if(self, *args, **kwargs):
            self.calls += 1
            assert self.calls <= DEFAULT_ATTEMPTS
            return SimpleNamespace(won=False)

    store = Contended()
    with pytest.raises(TransientStoreError):
        if operation == "reset":
            reset_attempt_budget(store, "key", "evt")
        else:
            reserve_concurrency(store, "a", "key", "run", "intent", 2)
    assert store.calls == DEFAULT_ATTEMPTS


# ---------------------------------------------------------------- d13: a global key

COMMENT = "github.comment.created"
SHARED_KEY = "pr-fixer:{trigger.data.repository}#{trigger.data.number}"


def keyed(rid, typ, key=SHARED_KEY, max_attempts=None):
    doc = rule(concurrency_key=key, max_attempts=max_attempts).to_dict()
    doc["id"] = rid
    doc["name"] = rid
    doc["trigger"]["params"]["type"] = typ
    return doc


def shared_cluster(a_max=3, b_max=3, b_key=SHARED_KEY):
    """Rule A (checks_settled) and rule B (comment) keyed alike: one key per PR."""
    c = Cluster("spark")
    c.start()
    c.base.put("rules", keyed("A", SETTLED, max_attempts=a_max))
    c.base.put("rules", keyed("B", COMMENT, key=b_key, max_attempts=b_max))
    return c


def send(c, n, typ, number=42, **data):
    c.publish({**event(n, number, **data), "type": typ})
    reports = c.cycle()
    assert all(not report.errors for report in reports.values()), reports


def test_rules_sharing_a_key_share_one_active_run():
    c = shared_cluster()
    c.actor.on(ACTION_STEP, ("accept",))
    send(c, 1, SETTLED, conclusion="failure")
    holder = c.run("A", "evt_1")
    assert holder is not None
    send(c, 2, COMMENT)
    assert c.run("B", "evt_2") is None
    (decision,) = c.base.find(RULE_DECISIONS, {"rule_id": "B"})
    assert decision["reason"] == "deduplicated"
    assert holder["id"] in decision["detail"]
    # A different PR is independent.
    send(c, 3, COMMENT, number=43)
    assert c.run("B", "evt_3") is not None


def test_distinct_templates_isolate_rules():
    c = shared_cluster(b_key="other:{trigger.data.repository}#{trigger.data.number}")
    c.actor.on(ACTION_STEP, ("accept",))
    send(c, 1, SETTLED, conclusion="failure")
    send(c, 2, COMMENT)
    assert c.run("A", "evt_1") is not None and c.run("B", "evt_2") is not None


def test_budget_is_consumed_across_rules_sharing_the_key():
    from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS

    c = shared_cluster(a_max=2, b_max=2)
    send(c, 1, SETTLED, conclusion="failure")
    send(c, 2, COMMENT)
    assert c.run("A", "evt_1") is not None and c.run("B", "evt_2") is not None
    send(c, 3, SETTLED, conclusion="failure")
    send(c, 4, COMMENT)
    assert c.run("A", "evt_3") is None and c.run("B", "evt_4") is None
    assert reasons(c) == ["attempt_budget_exhausted"] * 2
    (budget,) = c.base.find(RULE_ATTEMPT_BUDGETS)
    assert budget["count"] == 2
    # The other PR has its own budget.
    send(c, 5, COMMENT, number=43)
    assert c.run("B", "evt_5") is not None


def test_smallest_max_attempts_of_rules_sharing_the_key_wins():
    c = shared_cluster(a_max=5, b_max=1)
    send(c, 1, SETTLED, conclusion="failure")
    assert c.run("A", "evt_1") is not None
    send(c, 2, SETTLED, conclusion="failure")
    assert c.run("A", "evt_2") is None
    assert reasons(c) == ["attempt_budget_exhausted"]


def test_unbudgeted_rule_sharing_a_key_is_bounded_by_the_budgeted_one():
    c = shared_cluster(a_max=1, b_max=None)
    send(c, 1, COMMENT)
    assert c.run("B", "evt_1") is not None
    send(c, 2, COMMENT)
    assert c.run("B", "evt_2") is None


def test_coalescing_fires_the_latest_event_through_the_rule_that_recorded_it():
    c = shared_cluster()
    c.actor.on(ACTION_STEP, ("accept",))
    send(c, 1, SETTLED, conclusion="failure")
    holder = c.run("A", "evt_1")
    send(c, 2, SETTLED, conclusion="failure")
    send(c, 3, COMMENT)
    assert reasons(c) == ["deduplicated"] * 2
    c.clock.advance(1)
    c.base.update_if(RUNS_COLLECTION, holder["id"], {}, {"status": "failed"})
    c.cycle()
    c.cycle()
    assert c.run("A", "evt_2") is None
    assert c.run("B", "evt_3") is not None
    assert len(c.base.find(RUNS_COLLECTION)) == 2


def test_reset_resets_the_shared_budget_once():
    from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS

    c = shared_cluster(a_max=1, b_max=1)
    send(c, 1, SETTLED, conclusion="failure")
    send(c, 2, COMMENT)
    assert c.run("B", "evt_2") is None
    (before,) = c.base.find(RULE_ATTEMPT_BUDGETS)
    send(c, 3, SETTLED, conclusion="success")  # green: resets, and A fires on it
    (after,) = c.base.find(RULE_ATTEMPT_BUDGETS)
    # one reset write (revision +1) plus A's admission (+1): never once per rule
    assert after["revision"] == before["revision"] + 2
    assert c.run("A", "evt_3") is not None
    assert after["count"] == 1


def test_budget_id_is_the_key_alone_and_a_reset_applies_once_per_event():
    from culture_rules.engine.claims import (
        RULE_ATTEMPT_BUDGETS,
        budget_id,
        reserve_concurrency,
        reset_attempt_budget,
    )
    from culture_rules.store.memory import MemoryStore

    assert budget_id("pr#1") != budget_id("pr#2")
    assert budget_id('a","b') != budget_id("a") and budget_id("x") == budget_id("x")
    store = MemoryStore()
    assert reserve_concurrency(store, "A", "pr#1", "run1", "i1", 3) is None
    assert reserve_concurrency(store, "B", "pr#1", "run2", "i2", 3) == "deduplicated"
    (budget,) = store.find(RULE_ATTEMPT_BUDGETS)
    assert budget["id"] == budget_id("pr#1") and budget["rule_id"] == "A"
    reset_attempt_budget(store, "pr#1", "evt_9")
    store.update_if(RULE_ATTEMPT_BUDGETS, budget["id"], {}, {"count": 1})  # admitted after
    reset_attempt_budget(store, "pr#1", "evt_9")  # the same event via a second consumer
    assert store.get(RULE_ATTEMPT_BUDGETS, budget["id"])["count"] == 1
    reset_attempt_budget(store, "pr#1", "evt_10")
    assert store.get(RULE_ATTEMPT_BUDGETS, budget["id"])["count"] == 0


# ------------------------------------------- review fixes: sequencing, resets, deleted holders


def dependant_cluster(relation):
    """Keyed A (budget 1) and an unkeyed B that must / may run after A, on one event type."""
    c = Cluster("spark")
    c.start()
    c.base.put("rules", keyed("A", SYNC, max_attempts=1))
    b = rule().to_dict()
    b.update(id="B", name="B", **{relation: ["A"]})
    c.base.put("rules", b)
    return c


def test_exhausted_predecessor_settles_a_must_after_dependant():
    c = dependant_cluster("must_after")
    send(c, 1, SYNC)
    c.cycle()
    assert c.run("A", "evt_1")["status"] == "succeeded"
    assert c.run("B", "evt_1") is not None
    send(c, 2, SYNC)
    c.cycle()
    c.cycle()
    assert c.run("A", "evt_2") is None
    a = c.base.get(RULE_DECISIONS, decision_key("A", "evt_2"))
    assert a["reason"] == "attempt_budget_exhausted"
    b = c.base.get(RULE_DECISIONS, decision_key("B", "evt_2"))
    assert b["reason"] == "predecessor_failed", b
    assert b["by"] == ["A"]


def test_exhausted_predecessor_lets_a_may_after_dependant_fire():
    c = dependant_cluster("may_after")
    send(c, 1, SYNC)
    c.cycle()
    send(c, 2, SYNC)
    c.cycle()
    c.cycle()
    assert c.run("A", "evt_2") is None
    assert c.run("B", "evt_2") is not None


def test_coalesced_away_predecessor_settles_its_dependant():
    """A deduplicated predecessor waits (it may still fire as the key's newest event); once a
    newer event replaces it, it never will, so its must-after dependant is settled."""
    c = Cluster("spark")
    c.start()
    c.base.put("rules", keyed("A", SYNC))
    b = rule().to_dict()
    b.update(id="B", name="B", must_after=["A"])
    c.base.put("rules", b)
    c.actor.on(ACTION_STEP, ("accept",))
    send(c, 1, SYNC)
    holder = c.run("A", "evt_1")
    send(c, 2, SYNC)
    c.cycle()
    assert c.base.get(RULE_DECISIONS, decision_key("B", "evt_2"))["reason"] == (
        "blocked_by_predecessor"
    )
    send(c, 3, SYNC)  # replaces evt_2 as the newest deduplicated event
    c.cycle()
    b = c.base.get(RULE_DECISIONS, decision_key("B", "evt_2"))
    assert b["reason"] == "predecessor_failed", b
    # evt_3 is still the key's pending event: its dependant keeps waiting, then runs after A.
    assert c.base.get(RULE_DECISIONS, decision_key("B", "evt_3"))["reason"] == (
        "blocked_by_predecessor"
    )
    c.clock.advance(1)
    c.base.update_if(RUNS_COLLECTION, holder["id"], {}, {"status": "failed"})
    for _ in range(4):
        c.cycle()
    assert c.run("A", "evt_3") is not None
    assert c.run("B", "evt_3") is not None


def test_lagging_consumer_does_not_reset_again_after_interleaved_progress():
    """Consumer X handles resets E1 and E2 and an attempt is admitted; a lagging consumer Y
    then handles E1: no new reset signal, so no new attempt."""
    from culture_rules.engine.claims import (
        RULE_ATTEMPT_BUDGETS,
        budget_id,
        reserve_concurrency,
        reset_attempt_budget,
    )
    from culture_rules.store.memory import MemoryStore

    store = MemoryStore()
    assert reserve_concurrency(store, "A", "pr#1", "run1", "i1", 1) is None
    store.put(RUNS_COLLECTION, {"id": "run1", "status": "failed"})
    reset_attempt_budget(store, "pr#1", "E1")  # consumer X
    reset_attempt_budget(store, "pr#1", "E2")  # consumer X
    assert reserve_concurrency(store, "A", "pr#1", "run2", "i2", 1) is None
    store.put(RUNS_COLLECTION, {"id": "run2", "status": "failed"})
    reset_attempt_budget(store, "pr#1", "E1")  # consumer Y, lagging
    reset_attempt_budget(store, "pr#1", "E2")  # consumer Y
    assert store.get(RULE_ATTEMPT_BUDGETS, budget_id("pr#1"))["count"] == 1
    assert reserve_concurrency(store, "A", "pr#1", "run3", "i3", 1) == "attempt_budget_exhausted"


def test_reset_seen_before_the_budget_exists_is_not_replayed_by_a_lagging_consumer():
    from culture_rules.engine.claims import reserve_concurrency, reset_attempt_budget
    from culture_rules.store.memory import MemoryStore

    store = MemoryStore()
    from culture_rules.engine.claims import RESET_MARKERS
    from culture_rules.events.triggers import FIRES_COLLECTION

    assert RESET_MARKERS == FIRES_COLLECTION  # the engine layer names it without importing
    reset_attempt_budget(store, "pr#1", "E1")  # consumer X: no budget yet
    assert reserve_concurrency(store, "A", "pr#1", "run1", "i1", 1) is None
    store.put(RUNS_COLLECTION, {"id": "run1", "status": "failed"})
    reset_attempt_budget(store, "pr#1", "E1")  # consumer Y, lagging
    assert reserve_concurrency(store, "A", "pr#1", "run2", "i2", 1) == "attempt_budget_exhausted"


def test_placed_and_shared_consumers_interleaving_resets_grant_no_extra_attempt():
    """Through the node: a placed and an unplaced rule share a key; the shared consumer
    runs ahead over two resets and an admission, then the placed one catches up."""
    from culture_rules.model.placement import Placement

    c = Cluster("spark")
    c.start()
    c.base.put("rules", keyed("S", COMMENT, max_attempts=1))
    placed = Rule.from_dict(keyed("P", SETTLED, max_attempts=1))
    placed = Rule.from_dict({**placed.to_dict(), "placement": Placement(machine="spark").to_dict()})
    c.base.put("rules", placed.to_dict())
    firing = c.nodes["spark"].firing

    def evaluate(env, *, placed):
        with c.base.transaction() as tx:
            firing._evaluate(tx, {"envelope": env}, placed=placed)
        c.cycle()  # start the run, and let it finish

    exhausting = {**event(1), "type": COMMENT}
    evaluate(exhausting, placed=False)
    assert c.run("S", "evt_1") is not None
    resets = [{**event(n, self_authored=False), "type": SYNC} for n in (2, 3)]
    for env in resets:  # the shared consumer runs ahead
        evaluate(env, placed=False)
    evaluate({**event(4), "type": COMMENT}, placed=False)
    assert c.run("S", "evt_4") is not None  # the one attempt the resets granted
    for env in resets:  # the placed consumer catches up on the same resets
        evaluate(env, placed=True)
    evaluate({**event(5), "type": COMMENT}, placed=False)
    assert c.run("S", "evt_5") is None
    assert c.base.get(RULE_DECISIONS, decision_key("S", "evt_5"))["reason"] == (
        "attempt_budget_exhausted"
    )


@pytest.mark.parametrize("change", ["delete", "unkey"])
def test_coalesced_event_fires_after_the_holding_rule_changes(change):
    c = shared_cluster()
    c.actor.on(ACTION_STEP, ("accept",))
    send(c, 1, SETTLED, conclusion="failure")
    holder = c.run("A", "evt_1")
    send(c, 2, COMMENT)
    assert reasons(c) == ["deduplicated"]
    a = c.base.get("rules", "A")
    if change == "delete":
        c.base.put("rules", {**a, "deleted_at": "2026-10-07T00:00:00Z"})
    else:
        c.base.put("rules", {**a, "concurrency_key": None, "max_attempts": None})
    c.clock.advance(1)
    c.base.update_if(RUNS_COLLECTION, holder["id"], {}, {"status": "failed"})
    c.cycle()
    c.cycle()
    assert c.run("B", "evt_2") is not None
