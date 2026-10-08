"""The chaos harness's abrupt kill must not depend on the victim winning a claim race.

``Cluster.kill`` arms a crash that fires on the victim's *next* action invocation. Unplaced
work goes to whichever host claims it first, and on MongoDB the three host loops can settle
into a phase where one host (thor, busy committing the shared chain markers) loses every
action race for the whole run: it never invokes an action, never dies, and the chaos test
timed out after 30 s in ``kill`` with every run already succeeded exactly once. This test
reproduces that starvation deterministically on the MemoryStore: thor's executor only
drives after a pause, so the survivors always claim new actions first, and the kill is
armed while a survivor is mid-drive with a backlog of started runs (in the MongoDB failure
a drive in progress at arming took all the remaining work). ``kill`` must still get thor
to die mid-action, by holding the survivors' executors - including a drive already in
progress - until it does.
"""

from __future__ import annotations

import threading
import time

import pytest

from culture_rules.engine.runs import ACTION_STEP
from culture_rules.store.memory import MemoryStore
from tests.events.fakes import envelope
from tests.multihost.harness import HOSTS, Cluster, event_rule, run_id_for

pytestmark = pytest.mark.multihost

LAG_S = 2.0
"""How long thor waits before each drive: a slow phase that loses every race to the others."""
BURST = 30


def _lagging(host) -> None:
    executor = host.node.executor
    drive = executor.run_until_idle

    def lagging(*args, **kwargs):
        time.sleep(LAG_S)
        return drive(*args, **kwargs)

    executor.run_until_idle = lagging


def _active_burst_runs(cluster: Cluster) -> int:
    ids = {run_id_for("starve", f"evt_{n}") for n in range(3, 3 + BURST)}
    return sum(1 for d in cluster.store.find("runs") if d["id"] in ids)


def test_kill_lands_mid_action_even_when_the_victim_never_wins_an_action_race():
    base = MemoryStore()
    cluster = Cluster(base.peer, backend="memory")
    try:
        cluster.define(event_rule("starve"))
        cluster.start(*HOSTS)
        _lagging(cluster.host("thor"))
        for n in range(3):  # the survivors take all of these
            cluster.publish(envelope(n))
        for n in range(3):
            cluster.wait_run("starve", f"evt_{n}", timeout=10)
        assert "thor" not in cluster.ledger.effects_by_host(ACTION_STEP)  # thor is starved

        # The kill is armed while a survivor is mid-drive (inside an action invocation), and
        # that survivor resumes its drive with the rest of the burst already started as runs:
        # if it kept driving it would take them all before thor's next (lagging) drive.
        killer: dict[str, object] = {}
        arming = threading.Lock()  # two survivors may race to arm; only one kill thread
        invoke = cluster.ledger.invoke

        def kill() -> None:
            try:
                cluster.kill("thor", timeout=15)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the test thread
                killer["error"] = exc

        def arming_invoke(input, idempotency_key, deadline, *, context):
            result = invoke(input, idempotency_key, deadline, context=context)
            if context.host == "thor":
                return result
            with arming:
                if "thread" in killer:
                    return result
                thread = threading.Thread(target=kill, daemon=True)
                thread.start()  # started before it is published, so join() never sees it unstarted
                killer["thread"] = thread
            cluster.wait_until(lambda: cluster.holding(context.host), 5, "the kill to arm")
            killer["armed_at"] = time.time()
            cluster.wait_until(
                lambda: _active_burst_runs(cluster) >= BURST // 2, 10, "the burst to start"
            )
            return result

        cluster.ledger.invoke = arming_invoke
        for n in range(3, 3 + BURST):
            cluster.publish(envelope(n))
        cluster.wait_until(lambda: "thread" in killer, 15, "a survivor to take a burst action")
        killer["thread"].join(timeout=30)
        if "error" in killer:
            raise killer["error"]

        thor = cluster.host("thor")
        assert thor.killed
        assert thor.crashed_key is not None
        # while the kill was pending no survivor took new work (at most the one action it
        # was already dispatching when the hold began)
        window = [
            host
            for at, host, _, step_id, _ in cluster.ledger.invocations
            if killer["armed_at"] <= at < thor.killed_at and step_id == ACTION_STEP
        ]
        assert all(window.count(h) <= 1 for h in ("spark", "spark2")), window
        # the side effect thor performed before dying is recorded once, by thor
        assert cluster.ledger.effects(step=ACTION_STEP)[thor.crashed_key] == 1
        # every burst run still finishes (a survivor reclaims thor's step after the lease)
        for n in range(3 + BURST):
            assert cluster.wait_run("starve", f"evt_{n}", timeout=30)["status"] == "succeeded"
        assert cluster.ledger.invocation_counts(step=ACTION_STEP)[thor.crashed_key] >= 2
        effects = cluster.ledger.effects(step=ACTION_STEP)
        assert sum(effects.values()) == 3 + BURST
        assert {k: c for k, c in effects.items() if c > 1} == {}
    finally:
        cluster.close()
