"""d12 end to end: ``github.push`` as a built-in action step, through the real push port.

The executor routes the step through :class:`~culture_rules.node.actors.ActorRouter` to the
real :class:`~culture_rules.node.actions.github_pr.GitHubPushPort` (fake GitHub API, local git
remote), so the port's own refusals apply to a step exactly as to a rule action:
``gate_not_passed`` and the disabled source rule both refuse with nothing pushed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest

from culture_rules.actors import trusted
from culture_rules.engine.runs import Executor, step_state
from culture_rules.node.actors import ActorRouter
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, edge, port, rule, step, workflow
from tests.node import test_github_pr_actions as gh  # skips without cryptography / git
from tests.node.test_github_pr_actions import trusted_test_app  # noqa: F401
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


GATE_OUT = ("verdict", "commit_sha", "start_sha", "base_sha", "bundle")


def fixer_workflow(world: World) -> Any:
    """fix(agent -> gate) -> push (action step) -> comment (action step): the single fixer's
    shape (push verifies the commit against the fix loop's last gate, d21)."""
    agent = step("agent", "ai", outputs=(port("commit_sha", "string"),))
    gate = step(
        "gate",
        "code",
        config={"builtin": "gate"},
        outputs=tuple(port(n, "string") for n in GATE_OUT),
    )
    fix = step(
        "fix",
        "retry_until",
        body=(agent, gate),
        max_iterations=1,
        config={"until": {"op": "exists", "arg": {"field": "verdict"}}},
        outputs=(port("verdict", "string"),),
    )
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
        (fix, push, comment),
        (
            edge("agent", "commit_sha", "push", "commit_sha"),
            edge("gate", "verdict", "push", "verdict"),
            edge("push", "head_after", "comment", "head_after"),
        ),
        inputs=(port("repo", "string"), port("number", "integer"), port("head_branch", "string")),
    )


def gate_outputs(world: World, verdict: str) -> dict:
    """What the fixer's gate reports for the agent's commit ``world.b`` on ``world.a``."""
    return {
        "verdict": verdict,
        "commit_sha": world.b,
        "start_sha": world.a,
        "base_sha": gh.PR_BASE_SHA,
        "bundle": str(world.agent),
    }


def fixer_rule():
    """The fixer rule: its run names the PR it fixes (the push checks it against them)."""
    pr = {
        "repo": {"$literal": REPO},
        "number": {"$literal": 3},
        "head_branch": {"$literal": "fix"},
    }
    return rule(id="fixer", workflow_inputs=pr)


def run_fixer(
    pem, world, *, verdict: str, rule_enabled: bool = True, reviewed: bool = True, trusted_wf=True
):
    clock = Clock(datetime.now(UTC))
    store = MemoryStore(clock=clock)
    store.put("actors", actor_doc())
    store.put("rules", {"id": "fixer", "name": "fixer", "enabled": rule_enabled})
    fake, rec = FakeGitHub(world), RecordingGit()
    comment = FakeActor(default=lambda inp, ctx: {})
    gate_out = gate_outputs(world, verdict)
    worker = FakeActor(
        default=lambda inp, ctx: (
            {"commit_sha": world.b} if ctx.step_id.endswith("/agent") else gate_out
        )
    )
    ports = {
        "action:github.push": push_port(
            pem, world, fake, store=store, gitrec=rec, review=False, gate=False
        ),
        "action:github.comment": comment,
        "action:noop": FakeActor(),
        "*": worker,
    }
    ex = Executor(store, "spark", ActorRouter(store, ports=ports, clock=clock), clock=clock)
    wf = fixer_workflow(world)
    run = ex.start(fixer_rule(), wf)
    if reviewed:  # d20: this hand-built workflow has no review step; record the approval
        gh.approve_review(store, world.b, run_id=run["id"], start=world.a)
    roles = dict(trusted.TRUSTED_WORKFLOWS)
    if trusted_wf:
        roles[trusted.ROLE_SINGLE] = roles[trusted.ROLE_SINGLE] | {trusted.workflow_digest(wf)}
    with mock.patch.object(trusted, "TRUSTED_WORKFLOWS", roles):
        ex.run_until_idle()
    return ex.run(run["id"]), fake, rec, comment


def test_push_step_pushes_after_a_passing_gate_and_feeds_the_comment(pem, world):
    doc, _fake, rec, comment = run_fixer(pem, world, verdict="pass")
    assert doc["status"] == "succeeded", doc["error"]
    assert world.remote_head() == world.b
    assert "push" in rec.verbs()
    assert step_state(doc, "push")["outputs"]["pushed"] is True
    assert [c[1]["body"] for c in comment.calls] == [f"pushed {world.b}"]


def test_push_step_of_an_untrusted_workflow_is_refused(pem, world):
    doc, fake, rec, comment = run_fixer(pem, world, verdict="pass", trusted_wf=False)
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "push" and doc["error"]["message"] == "workflow_not_trusted"
    assert world.remote_head() == world.a and fake.calls == [] and rec.calls == []


def test_push_step_without_an_approving_review_is_refused(pem, world):
    doc, fake, rec, comment = run_fixer(pem, world, verdict="pass", reviewed=False)
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "push" and doc["error"]["message"] == "review_missing"
    assert world.remote_head() == world.a
    assert fake.calls == [] and rec.calls == []  # refused before any network or git
    assert comment.calls == []


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
