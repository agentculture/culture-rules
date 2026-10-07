"""d12 end to end: ``github.push`` as a built-in action step, through the real push port.

The executor routes the step through :class:`~culture_rules.node.actors.ActorRouter` to the
real :class:`~culture_rules.node.actions.github_pr.GitHubPushPort` (fake GitHub API, local git
remote), so the port's own refusals apply to a step exactly as to a rule action:
``gate_not_passed`` and the disabled source rule both refuse with nothing pushed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from culture_rules.engine.runs import Executor, step_state
from culture_rules.node.actors import ActorRouter
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, edge, port, rule, step, workflow
from tests.node import test_github_pr_actions as gh  # skips without cryptography / git
from tests.node.test_github_pr_actions import (
    REPO,
    FakeGitHub,
    RecordingGit,
    World,
    actor_doc,
    push_port,
)

pem = gh.pem  # the module-scoped RSA key fixture


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def fixer_workflow(world: World) -> Any:
    """agent (commit) -> gate (verdict) -> push (action step) -> comment (action step)."""
    agent = step("agent", "ai", outputs=(port("commit_sha", "string"),))
    gate = step("gate", "logic", outputs=(port("verdict", "string"),))
    push = step(
        "push",
        "code",
        inputs=(port("commit_sha", "string"), port("verdict", "string")),
        outputs=(port("head_after", "string"), port("pushed", "boolean")),
        config={
            "builtin": "action",
            "action": {
                "kind": "github.push",
                "params": {
                    "actor": "gh-app",
                    "repo": REPO,
                    "number": 3,
                    "head_branch": "fix",
                    "expected_head_sha": world.a,
                    "commit_sha": "inputs.commit_sha",
                    "source": str(world.agent),
                    "gate_verdict": "inputs.verdict",
                },
            },
        },
    )
    comment = step(
        "comment",
        "code",
        inputs=(port("head_after", "string"),),
        config={
            "builtin": "action",
            "action": {
                "kind": "github.comment",
                "params": {
                    "actor": "gh-app",
                    "repo": REPO,
                    "number": 3,
                    "body": "pushed {{ inputs.head_after }}",
                },
            },
        },
    )
    return workflow(
        (agent, gate, push, comment),
        (
            edge("agent", "commit_sha", "push", "commit_sha"),
            edge("gate", "verdict", "push", "verdict"),
            edge("push", "head_after", "comment", "head_after"),
        ),
    )


def run_fixer(pem, world, *, verdict: str, rule_enabled: bool = True):
    clock = Clock(datetime.now(UTC))
    store = MemoryStore(clock=clock)
    store.put("actors", actor_doc())
    store.put("rules", {"id": "fixer", "name": "fixer", "enabled": rule_enabled})
    fake, rec = FakeGitHub(world), RecordingGit()
    comment = FakeActor(default=lambda inp, ctx: {})
    worker = FakeActor(
        default=lambda inp, ctx: (
            {"commit_sha": world.b} if ctx.step_id == "agent" else {"verdict": verdict}
        )
    )
    ports = {
        "action:github.push": push_port(pem, world, fake, store=store, gitrec=rec),
        "action:github.comment": comment,
        "action:noop": FakeActor(),
        "*": worker,
    }
    ex = Executor(store, "spark", ActorRouter(store, ports=ports, clock=clock), clock=clock)
    run = ex.start(rule(id="fixer"), fixer_workflow(world))
    ex.run_until_idle()
    return ex.run(run["id"]), fake, rec, comment


def test_push_step_pushes_after_a_passing_gate_and_feeds_the_comment(pem, world):
    doc, _fake, rec, comment = run_fixer(pem, world, verdict="pass")
    assert doc["status"] == "succeeded", doc["error"]
    assert world.remote_head() == world.b
    assert "push" in rec.verbs()
    assert step_state(doc, "push")["outputs"]["pushed"] is True
    assert [c[1]["body"] for c in comment.calls] == [f"pushed {world.b}"]


@pytest.mark.parametrize("verdict", ["fail", "guard", "no_gate"])
def test_push_step_with_a_failed_gate_is_refused_and_nothing_is_pushed(pem, world, verdict):
    doc, fake, rec, comment = run_fixer(pem, world, verdict=verdict)
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "push"
    assert doc["error"]["message"] == "gate_not_passed"
    assert world.remote_head() == world.a
    assert fake.calls == [] and rec.calls == []  # refused before any network or git
    assert comment.calls == []


def test_push_step_of_a_disabled_source_rule_is_refused(pem, world):
    doc, fake, rec, comment = run_fixer(pem, world, verdict="pass", rule_enabled=False)
    assert doc["status"] == "failed"
    assert doc["error"]["message"] == "rule_disabled"
    assert world.remote_head() == world.a
    assert "push" not in rec.verbs() and fake.calls == []
    assert comment.calls == []
