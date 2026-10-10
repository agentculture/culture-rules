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
