"""Criterion 3 (c71/h53): with one host stopped, >= 99% of 200 API requests succeed and
100 events produce exactly 100 action executions - 0 duplicates, 0 lost.

thor is killed *abruptly* mid-run: its next action invocation performs the side effect and
then the host dies before recording it (claim held, step left ``dispatching``), and its API
listener goes away. The surviving hosts must reclaim the orphaned step after the lease and
re-invoke it with the same idempotency key, so the side effect still happens exactly once.
Requests go through a failover client standing in for the edge (Cloudflare) routing to live
origins only on connection failure; HTTP errors are never retried.
"""

from __future__ import annotations

import threading
import time

from culture_rules.engine.runs import ACTION_STEP, RUNS_COLLECTION
from culture_rules.model.rule import Rule
from tests.events.fakes import envelope
from tests.multihost.harness import HOSTS, FailoverClient, event_rule, run_id_for

EVENTS = 100
REQUESTS = 200


def test_one_host_stopped_keeps_serving_and_actions_run_exactly_once(cluster):
    chaos = event_rule("chaos").to_dict()
    chaos["trigger"]["params"]["max_fires_per_hour"] = 2 * EVENTS  # not the cap under test
    cluster.define(Rule.from_dict(chaos))
    cluster.start(*HOSTS)
    apis = [cluster.serve(h) for h in HOSTS]
    client = FailoverClient([a.url for a in apis], headers=apis[0].headers)

    def publish() -> None:
        for n in range(EVENTS):
            cluster.publish(envelope(n))
            time.sleep(0.01)

    publisher = threading.Thread(target=publish)
    publisher.start()
    cluster.wait_until(lambda: len(cluster.ledger.effects()) >= 10, timeout=60)
    cluster.kill("thor")  # dies mid-action; its API listener is stopped too

    # the premise: thor really died holding an in-flight action, so reclaiming it is what is
    # under test. Survivors cannot take it before its lease lapses, so it is still held here.
    victim = cluster.host("thor")
    assert victim.killed, "thor died without an action"
    assert victim.crashed_key is not None, "thor died without an action"
    performed = [e for e in cluster.ledger.log if e.key == victim.crashed_key]
    assert [e.host for e in performed] == ["thor"], "thor did not perform the side effect"
    orphan = cluster.store.get(RUNS_COLLECTION, performed[0].run_id)
    held = [s for s in orphan["steps"] if s["key"] == ACTION_STEP]
    assert [(s["status"], s["host"]) for s in held] == [("dispatching", "thor")], held

    outcomes = client.mixed_load(REQUESTS)  # thor is down for all of them
    publisher.join()
    runs = [cluster.wait_run("chaos", f"evt_{n}", timeout=120) for n in range(EVENTS)]

    succeeded = sum(1 for ok in outcomes if ok)
    assert succeeded >= 0.99 * REQUESTS, f"only {succeeded}/{REQUESTS} requests succeeded"
    assert cluster.host("thor").killed, "thor was not stopped abruptly"

    assert all(r["status"] == "succeeded" for r in runs)
    assert sorted(cluster.fires("chaos")) == sorted(f"evt_{n}" for n in range(EVENTS))
    effects = cluster.ledger.effects(step=ACTION_STEP)  # idempotency key -> side effects
    expected = {
        cluster.ledger.key(run_id_for("chaos", f"evt_{n}"), ACTION_STEP) for n in range(EVENTS)
    }
    duplicates = {k: c for k, c in effects.items() if c > 1}
    lost = expected - set(effects)
    assert sum(effects.values()) == EVENTS
    assert duplicates == {}
    assert lost == set()
    assert set(effects) == expected
    acted_on = sorted(e.input["event"] for e in cluster.ledger.log if e.step == ACTION_STEP)
    assert acted_on == sorted(f"evt_{n}" for n in range(EVENTS))  # each event's own action
    # the failover was real: survivors finished work thor had started or never reached
    after_kill = cluster.ledger.hosts_after(cluster.host("thor").killed_at, step=ACTION_STEP)
    assert after_kill
    assert "thor" not in after_kill
    # the engine re-invoked a key only where an ack was really lost: the step thor died in
    # (reclaimed after the lease) plus any store hiccup a host loop recorded
    crashed = cluster.host("thor").crashed_key
    assert cluster.ledger.invocation_counts(step=ACTION_STEP)[crashed] >= 2
    retried = {k for k, n in cluster.ledger.invocation_counts(step=ACTION_STEP).items() if n > 1}
    assert len(retried) <= 1 + len(cluster.errors()), (retried, cluster.errors()[:5])
    kinds = sorted({type(e).__name__ for e in cluster.errors()})
    print(
        f"[{cluster.backend}] ok={succeeded}/{REQUESTS} failovers={client.failovers} "
        f"effects={sum(effects.values())} re-invoked={len(retried)} "
        f"host-errors={kinds}x{len(cluster.errors())} "
        f"by-host={cluster.ledger.effects_by_host(ACTION_STEP)}"
    )
