"""#35 / d28-d29: actors naming one ``concurrency_pool`` share one slot count, across hosts.

Two agent actors on two machines, both in pool ``qwen`` with ``max_concurrency: 1``. Two
nodes (``MemoryStore.peer`` handles) each run one rule whose step is placed on its own
actor. Only one step works at a time; the other stays ``blocked`` until the first one's
completion is delivered (which frees the pool slot through the store-only release path).
"""

from __future__ import annotations

from culture_rules.actors.limits import USAGE_COLLECTION, pool_doc_id
from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import BLOCKED_RETRY_S, step_state
from culture_rules.model.actor import Actor
from culture_rules.model.placement import Placement
from tests.engine.run_helpers import FakeActor, port, step, workflow
from tests.events.fakes import envelope
from tests.node.test_node import Cluster, event_rule


def pool_cluster(*, pool_b: str | None = "qwen"):
    c = Cluster("spark", "thor")
    inner = FakeActor().on("s1", ("accept",), ("accept",))
    for actor_id, host, pool in (("qwen-a", "spark", "qwen"), ("qwen-b", "thor", pool_b)):
        params = {"max_concurrency": 1}
        if pool:
            params["concurrency_pool"] = pool
        c.base.put(
            "actors",
            Actor(id=actor_id, name=actor_id, kind="agent", machine=host, params=params).to_dict(),
        )
    for h in ("spark", "thor"):
        c.nodes[h] = c.node(h, adapters={"agent": lambda actor: inner})
    for wf_id, actor_id in (("wf-a", "qwen-a"), ("wf-b", "qwen-b")):
        c.define(
            workflow(
                (
                    step(
                        "s1",
                        "actor_task",
                        outputs=(port("n", "any", required=False),),
                        placement=Placement(actor=actor_id),
                        timeout_s=3600,
                    ),
                ),
                id=wf_id,
            )
        )
    c.define(event_rule("ra", "wf-a"), event_rule("rb", "wf-b"))
    c.start()
    c.publish(envelope(1))
    c.cycle()
    return c, inner


def statuses(c):
    return {r: step_state(c.run(r, "evt_1"), "s1")["status"] for r in ("ra", "rb")}


def test_two_actors_in_one_pool_on_two_hosts_work_one_at_a_time():
    c, inner = pool_cluster()
    got = statuses(c)
    assert sorted(got.values()) == ["blocked", "waiting"]
    holder = next(r for r, s in got.items() if s == "waiting")
    waiter = "rb" if holder == "ra" else "ra"
    slots = c.base.get(USAGE_COLLECTION, pool_doc_id("qwen"))
    assert len(slots["inflight"]) == 1

    c.clock.advance(BLOCKED_RETRY_S + 1)
    c.cycle()
    assert step_state(c.run(waiter, "evt_1"), "s1")["status"] == "blocked"  # still held

    run = c.run(holder, "evt_1")
    key = idempotency_key(run["id"], "s1")
    host = "spark" if holder == "ra" else "thor"
    assert c.nodes[host].deliver(key, InvocationResult.completed({"n": 1}))
    assert c.base.get(USAGE_COLLECTION, pool_doc_id("qwen"))["inflight"] == []

    c.clock.advance(BLOCKED_RETRY_S * 4)
    c.cycle()
    assert step_state(c.run(waiter, "evt_1"), "s1")["status"] == "waiting"
    assert len(c.base.get(USAGE_COLLECTION, pool_doc_id("qwen"))["inflight"]) == 1


def test_an_actor_outside_the_pool_keeps_its_own_cap():
    c, _ = pool_cluster(pool_b=None)
    assert statuses(c) == {"ra": "waiting", "rb": "waiting"}
    assert len(c.base.get(USAGE_COLLECTION, pool_doc_id("qwen"))["inflight"]) == 1
    assert len(c.base.get(USAGE_COLLECTION, "qwen-b")["inflight"]) == 1


# ---- Codex #35 P1: an expired dispatch is fenced before its slot is reassigned -------------


def dispatch_cluster():
    """Two nodes; a rule on the queue's dispatch event runs a one-step workflow."""
    from culture_rules.model.action import Action
    from culture_rules.model.rule import Rule, Trigger, WorkflowRef

    c = Cluster("spark", "thor")
    inner = FakeActor().on("s1", ("accept",), ("accept",))
    for h in ("spark", "thor"):
        c.nodes[h] = c.node(h, actors={"*": inner})
    c.define(workflow((step("s1", outputs=(port("n", "any", required=False),)),), id="wf-q"))
    c.define(
        Rule(
            id="dispatch",
            name="dispatch",
            trigger=Trigger(kind="event", params={"type": "rules.queue.dispatch"}),
            workflow=WorkflowRef(id="wf-q", inputs={}),
            action=Action(kind="noop"),
        )
    )
    c.start()
    return c


# ---- Codex #35 P2: a slot is released where it was taken, whatever the pool is now ----------


def test_a_slot_is_freed_in_the_pool_that_admitted_it_after_the_actor_moved_pools():
    c, _ = pool_cluster()
    holder = next(r for r, s in statuses(c).items() if s == "waiting")
    actor_id = "qwen-a" if holder == "ra" else "qwen-b"
    doc = c.base.get("actors", actor_id)
    moved = {**doc["params"], "concurrency_pool": "elsewhere"}
    c.base.put("actors", {**doc, "params": moved})  # moved while its work runs
    run = c.run(holder, "evt_1")
    host = "spark" if holder == "ra" else "thor"
    key = idempotency_key(run["id"], "s1")
    assert c.nodes[host].deliver(key, InvocationResult.completed({"n": 1}))
    assert c.base.get(USAGE_COLLECTION, pool_doc_id("qwen"))["inflight"] == []


def test_a_slot_is_freed_after_the_actor_left_its_pool():
    c, _ = pool_cluster()
    holder = next(r for r, s in statuses(c).items() if s == "waiting")
    actor_id = "qwen-a" if holder == "ra" else "qwen-b"
    doc = c.base.get("actors", actor_id)
    params = {k: v for k, v in doc["params"].items() if k != "concurrency_pool"}
    c.base.put("actors", {**doc, "params": params})
    run = c.run(holder, "evt_1")
    host = "spark" if holder == "ra" else "thor"
    assert c.nodes[host].deliver(
        idempotency_key(run["id"], "s1"), InvocationResult.completed({"n": 1})
    )
    assert c.base.get(USAGE_COLLECTION, pool_doc_id("qwen"))["inflight"] == []


# ---- Codex #35 round 2: only the intended dispatch run claims the slot ----------------------


def _notify_rule():
    from culture_rules.model.action import Action
    from culture_rules.model.rule import Rule, Trigger

    return Rule(
        id="notify",
        name="notify",
        trigger=Trigger(kind="event", params={"type": "rules.queue.dispatch"}),
        action=Action(kind="noop"),
    )
