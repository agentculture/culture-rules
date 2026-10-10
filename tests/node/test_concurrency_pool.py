"""#35 / d28-d29: actors naming one ``concurrency_pool`` share one slot count, across hosts.

Two agent actors on two machines, both in pool ``qwen`` with ``max_concurrency: 1``. Two
nodes (``MemoryStore.peer`` handles) each run one rule whose step is placed on its own
actor. Only one step works at a time; the other stays ``blocked`` until the first one's
completion is delivered (which frees the pool slot through the store-only release path).
"""

from __future__ import annotations

from datetime import timedelta

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


def test_a_late_dispatch_whose_slot_expired_never_starts_a_run():
    from culture_rules.engine.actorport import InvocationContext as Ctx
    from culture_rules.engine.runs import RUNS_COLLECTION
    from culture_rules.node.actions.queue import QUEUES_COLLECTION, QueueAddPort, QueueProgressPort

    c = dispatch_cluster()
    deadline = c.clock() + timedelta(hours=1)
    cfg = {"queue": "q", "dispatch_rule": "dispatch", "stale_after_s": 900}
    ctx = Ctx(run_id="r", step_id="s", kind="code", host="spark", config=cfg)
    add, progress = QueueAddPort(c.base, clock=c.clock), QueueProgressPort(c.base, clock=c.clock)
    add.invoke({"repo": "o/a", "number": 1}, "a", deadline, context=ctx)
    add.invoke({"repo": "o/b", "number": 2}, "b", deadline, context=ctx)
    assert progress.invoke({}, "p1", deadline, context=ctx).output["dispatched"] == ["o/a#1"]
    a_run = c.base.get(QUEUES_COLLECTION, "q")["active"][0]["run_id"]
    # the nodes do not consume A's dispatch in time (a stalled feed): its slot expires
    c.clock.advance(901)
    second = progress.invoke({}, "p2", deadline, context=ctx)
    assert second.output["dispatched"] == ["o/b#2"]
    # now the nodes catch up: A's late event must not start a run beside B's
    c.cycle()
    c.cycle()
    assert c.base.get(RUNS_COLLECTION, a_run) is None
    started = [r["trigger"]["data"]["number"] for r in c.base.find(RUNS_COLLECTION)]
    assert started == [2]


def test_a_claimed_dispatch_keeps_its_slot_until_its_run_ends():
    from culture_rules.engine.actorport import InvocationContext as Ctx
    from culture_rules.engine.runs import RUNS_COLLECTION
    from culture_rules.node.actions.queue import QUEUES_COLLECTION, QueueAddPort, QueueProgressPort

    c = dispatch_cluster()
    deadline = c.clock() + timedelta(hours=1)
    cfg = {"queue": "q", "dispatch_rule": "dispatch", "stale_after_s": 900}
    ctx = Ctx(run_id="r", step_id="s", kind="code", host="spark", config=cfg)
    add, progress = QueueAddPort(c.base, clock=c.clock), QueueProgressPort(c.base, clock=c.clock)
    add.invoke({"repo": "o/a", "number": 1}, "a", deadline, context=ctx)
    add.invoke({"repo": "o/b", "number": 2}, "b", deadline, context=ctx)
    progress.invoke({}, "p1", deadline, context=ctx)
    c.cycle()  # A's dispatch fires: its firing claims the slot
    (act,) = c.base.get(QUEUES_COLLECTION, "q")["active"]
    assert act["claimed_at"]
    run = c.base.get(RUNS_COLLECTION, act["run_id"])
    assert run is not None and run["status"] == "running"
    c.clock.advance(5 * 3600)
    assert progress.invoke({}, "p2", deadline, context=ctx).output["dispatched"] == []
