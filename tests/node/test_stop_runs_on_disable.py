"""d17 end to end: disabling the fixer mid-run, then stopping (or not stopping) its runs.

The fixer workflow from :mod:`tests.node.test_action_step_github` runs through the real push
port; its agent step is asynchronous (accepted, completed later by an event), so the run is
still active when the operator disables the rule. Approving the stop cancels the run through
the normal cancel path: the agent's late result is ignored and nothing is pushed or commented.
Not approving leaves the run going, and the push step still refuses with ``rule_disabled``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import Containment, Executor, RunError, active_runs, step_state
from culture_rules.node.actors import ActorRouter
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, rule
from tests.node import test_github_pr_actions as gh  # skips without cryptography / git
from tests.node.test_action_step_github import fixer_workflow
from tests.node.test_github_pr_actions import FakeGitHub, RecordingGit, World, actor_doc, push_port

pem = gh.pem  # the module-scoped RSA key fixture


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


class Fixer:
    """One fixer run, parked on its asynchronous agent step, on executor host ``spark``."""

    def __init__(self, pem, world: World) -> None:
        self.world = world
        self.clock = Clock(datetime.now(UTC))
        self.store = MemoryStore(clock=self.clock)
        self.store.put("actors", actor_doc())
        self.store.put("rules", {"id": "fixer", "name": "fixer", "enabled": True})
        self.fake, self.rec = FakeGitHub(world), RecordingGit()
        self.comment = FakeActor()
        worker = FakeActor(default=lambda inp, ctx: {"verdict": "pass"})
        worker.on("agent", ("accept",))  # the agent works asynchronously
        ports = {
            "action:github.push": push_port(
                pem, world, self.fake, store=self.store, gitrec=self.rec
            ),
            "action:github.comment": self.comment,
            "action:noop": FakeActor(),
            "*": worker,
        }
        router = ActorRouter(self.store, ports=ports, clock=self.clock)
        self.ex = Executor(self.store, "spark", router, clock=self.clock)
        self.run_id = self.ex.start(rule(id="fixer"), fixer_workflow(world))["id"]
        self.ex.run_until_idle()
        assert self.ex.run(self.run_id)["status"] == "running"
        assert step_state(self.ex.run(self.run_id), "agent")["status"] == "waiting"

    def disable(self) -> None:
        doc = self.store.get("rules", "fixer")
        self.store.put("rules", {**doc, "enabled": False})

    def agent_finishes(self) -> bool:
        done = InvocationResult.completed({"commit_sha": self.world.b})
        changed = self.ex.deliver(idempotency_key(self.run_id, "agent"), done)
        self.ex.run_until_idle()
        return changed

    def nothing_pushed(self) -> bool:
        return self.world.remote_head() == self.world.a and "push" not in self.rec.verbs()


def test_disabled_rule_lists_its_active_run(pem, world):
    f = Fixer(pem, world)
    f.disable()
    listed, total = active_runs(f.store, "fixer")
    assert total == 1
    assert listed == [
        {"id": f.run_id, "status": "running", "started_at": f.ex.run(f.run_id)["created_at"]}
    ]


def test_approving_the_stop_cancels_the_run_and_nothing_is_pushed(pem, world):
    f = Fixer(pem, world)
    f.disable()
    out = Containment(f.store, clock=f.clock).stop_rule_runs("fixer", "ori", apply=True)
    assert out["cancelled"] == [f.run_id] and out["total"] == 1 and out["applied"] is True
    doc = f.ex.run(f.run_id)
    assert doc["status"] == "cancelled"
    assert doc["error"] == {"code": "cancelled", "message": "rule disabled: stopped by ori"}
    # the agent's late result is ignored: no push step, no comment, no action
    assert f.agent_finishes() is False
    doc = f.ex.run(f.run_id)
    assert doc["status"] == "cancelled"
    assert {step_state(doc, k)["status"] for k in ("push", "comment")} == {"cancelled"}
    assert f.nothing_pushed() and f.fake.calls == []
    assert f.comment.calls == []
    assert active_runs(f.store, "fixer") == ([], 0)


def test_not_approving_leaves_the_run_going_and_the_push_still_refuses(pem, world):
    f = Fixer(pem, world)
    f.disable()  # the operator dismisses the 'Stop 1 current run?' prompt
    assert f.ex.run(f.run_id)["status"] == "running"
    assert f.agent_finishes() is True
    doc = f.ex.run(f.run_id)
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "push" and doc["error"]["message"] == "rule_disabled"
    assert f.nothing_pushed() and f.fake.calls == []
    assert f.comment.calls == []


def test_stop_is_refused_while_the_rule_is_enabled(pem, world):
    f = Fixer(pem, world)
    with pytest.raises(RunError) as err:
        Containment(f.store, clock=f.clock).stop_rule_runs("fixer", "ori", apply=True)
    assert err.value.code == "rule_enabled"
    assert f.ex.run(f.run_id)["status"] == "running"


def test_a_second_stop_is_a_no_op(pem, world):
    f = Fixer(pem, world)
    f.disable()
    containment = Containment(f.store, clock=f.clock)
    assert containment.stop_rule_runs("fixer", "ori", apply=True)["cancelled"] == [f.run_id]
    again = containment.stop_rule_runs("fixer", "ori", apply=True)
    assert again == {"rule_id": "fixer", "applied": True, "runs": [], "total": 0, "cancelled": []}
    cancels = [e for e in f.store.find("audit") if e["verb"] == "runs.cancel"]
    assert len(cancels) == 1
