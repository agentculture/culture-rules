"""Durable per-rule, per-PR concurrency and consecutive attempt limits."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from culture_rules.engine.decisions import RULE_DECISIONS
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


def test_green_run_resets_consecutive_attempts():
    c = cluster(max_attempts=2)
    c.actor.on(
        ACTION_STEP,
        ("fail", "broken", False),
        ("complete", {}),
        ("fail", "broken", False),
        ("fail", "broken", False),
    )
    for n in range(1, 5):
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
