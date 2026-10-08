"""d12: the built-in ``action`` code step runs action kinds through the rule-action routing.

A ``code`` step with ``config = {"builtin": "action", "action": {"kind", "params"}}`` is
dispatched as an ``"action"`` invocation: the ``action:<kind>`` port, through the
:class:`~culture_rules.node.actors.ActorRouter` (so ``params.actor`` engages that actor's
LimitedActor and an unknown/disabled actor fails ``actor_unavailable``), with params resolved
from the step's input ports and the step's own (per-iteration) idempotency key.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

import pytest

from culture_rules.actors.limits import USAGE_COLLECTION, LimitedActor
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import (
    ACTION_STEP,
    ACTOR_UNAVAILABLE,
    RUNS_COLLECTION,
    Executor,
    RunError,
    step_key,
    step_state,
)
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.common import RetryPolicy
from culture_rules.model.refs import resolve_refs
from culture_rules.node.actors import ACTORS_COLLECTION, ActorRouter
from culture_rules.node.runner import BuiltinCodePort
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import (
    Clock,
    Crash,
    FakeActor,
    edge,
    enrol_online,
    machine,
    port,
    rule,
    step,
    workflow,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
REPO = "acme/widgets"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> MemoryStore:
    s = MemoryStore(clock=clock)
    enrol_online(s, clock, machine("spark"))
    return s


def put_app(store: MemoryStore, *, enabled: bool = True, **params: Any) -> None:
    store.put(
        ACTORS_COLLECTION,
        {
            "id": "gh-app",
            "name": "gh",
            "kind": "app",
            "enabled": enabled,
            "params": {"surface": "github", **params},
            "schema_version": "1.0",
        },
    )


def action_step(sid: str, kind: str, params: dict, *, inputs=(), outputs=(), **kw) -> Any:
    config = {"builtin": "action", "action": {"kind": kind, "params": params}}
    return step(sid, "code", inputs=inputs, outputs=outputs, config=config, **kw)


def push_step(**kw) -> Any:
    return action_step(
        "push",
        "github.push",
        {
            "actor": "gh-app",
            "repo": "inputs.repo",
            "number": "inputs.number",
            "head_branch": "fix",
            "expected_head_sha": SHA_A,
            "commit_sha": "inputs.commit_sha",
            "source": "/tmp/agent-worktree",
        },
        inputs=(port("repo", "string"), port("number", "integer"), port("commit_sha", "string")),
        outputs=(port("head_after", "string"),),
        **kw,
    )


def push_then_comment_workflow() -> Any:
    agent = step("agent", "ai", outputs=(port("commit_sha", "string"),))
    comment = action_step(
        "comment",
        "github.comment",
        {
            "actor": "gh-app",
            "repo": "inputs.repo",
            "number": "inputs.number",
            "body": "pushed {{ inputs.head_after }} to #{{ inputs.number }}",
        },
        inputs=(port("repo", "string"), port("number", "integer"), port("head_after", "string")),
    )
    return workflow(
        (agent, push_step(), comment),
        (
            edge("inputs", "repo", "push", "repo"),
            edge("inputs", "number", "push", "number"),
            edge("agent", "commit_sha", "push", "commit_sha"),
            edge("inputs", "repo", "comment", "repo"),
            edge("inputs", "number", "comment", "number"),
            edge("push", "head_after", "comment", "head_after"),
        ),
        inputs=(port("repo", "string"), port("number", "integer")),
    )


def pr_rule(**kw) -> Any:
    return rule(workflow_inputs={"repo": {"$literal": REPO}, "number": {"$literal": 3}}, **kw)


class Ports:
    def __init__(self, store: MemoryStore, clock: Clock, *, reply_dedups: bool = False) -> None:
        self.agent = FakeActor(default=lambda inp, ctx: {"commit_sha": SHA_B})
        self.push = FakeActor(default=lambda inp, ctx: {"head_after": inp["commit_sha"]})
        self.comment = FakeActor(default=lambda inp, ctx: {"id": 9})
        self.reply = FakeActor(idempotent=reply_dedups, default=lambda inp, ctx: {"id": 1})
        self.noop = FakeActor()
        self.mapping = {
            "action:github.push": self.push,
            "action:github.comment": self.comment,
            "action:github.review_reply": self.reply,
            "action:noop": self.noop,
            "code": BuiltinCodePort({}),
            "*": self.agent,
        }
        self.store, self.clock = store, clock

    def executor(self, *, router: bool = True, **kw: Any) -> Executor:
        ports: Any = self.mapping
        if router:
            ports = ActorRouter(self.store, ports=self.mapping, clock=self.clock)
        return Executor(self.store, "spark", ports, clock=self.clock, **kw)


# ------------------------------------------------------------------ two action steps


def test_two_action_steps_run_in_order_with_params_from_earlier_outputs(store, clock):
    put_app(store)
    p = Ports(store, clock)
    ex = p.executor()
    run = ex.start(pr_rule(), push_then_comment_workflow())
    ex.run_until_idle()

    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert len(p.push.calls) == 1
    assert len(p.comment.calls) == 1
    key, push_in, push_ctx, _ = p.push.calls[0]
    assert push_in == {
        "actor": "gh-app",
        "repo": REPO,
        "number": 3,
        "head_branch": "fix",
        "expected_head_sha": SHA_A,
        "commit_sha": SHA_B,  # the agent step's output, through the edge and inputs.*
        "source": "/tmp/agent-worktree",
    }
    assert key == idempotency_key(run["id"], "push")
    # routed exactly like a rule action: kind "action", the literal actor, unresolved params
    assert (push_ctx.kind, push_ctx.actor, push_ctx.step_id) == ("action", "gh-app", "push")
    assert push_ctx.config["kind"] == "github.push"
    assert push_ctx.config["params"]["commit_sha"] == "inputs.commit_sha"
    _, comment_in, comment_ctx, _ = p.comment.calls[0]
    assert comment_in["body"] == f"pushed {SHA_B} to #3"
    assert comment_in["number"] == 3
    assert comment_ctx.config["kind"] == "github.comment"
    order = [h["step"] for h in doc["history"] if h["event"] == "succeeded"]
    assert order.index("push") < order.index("comment")
    # resolved params are the step's persisted inputs; port results its outputs
    assert step_state(doc, "push")["inputs"]["commit_sha"] == SHA_B
    assert step_state(doc, "push")["outputs"] == {"head_after": SHA_B}
    # the rule's own terminal action still runs last
    assert step_state(doc, ACTION_STEP)["status"] == "succeeded"
    assert p.agent.calls[0][2].kind == "ai"


def test_action_steps_route_through_a_plain_port_mapping_too(store, clock):
    p = Ports(store, clock)
    ex = p.executor(router=False)  # Executor's own mapping routing: action:<kind>
    run = ex.start(pr_rule(), push_then_comment_workflow())
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    assert len(p.push.calls) == 1
    assert len(p.comment.calls) == 1


def test_resolution_reuses_resolve_refs_with_the_inputs_namespace():
    inputs = {"item": {"comment_id": 7, "body": "done"}, "n": 3}
    ctx = {"inputs": inputs}
    assert resolve_refs("inputs.item.comment_id", ctx) == 7
    assert resolve_refs("re: {{ inputs.item.body }} ({{inputs.n}})", ctx) == "re: done (3)"
    assert resolve_refs({"$ref": "inputs.n"}, ctx) == 3
    assert resolve_refs({"$literal": "inputs.n"}, ctx) == "inputs.n"
    # without an inputs namespace (a rule action's params) such strings stay literal
    assert resolve_refs("inputs.n", {"trigger": {}}) == "inputs.n"
    assert resolve_refs("{{ inputs.n }}", {"trigger": {}}) == "{{ inputs.n }}"


# ------------------------------------------------------------------ for_each of replies


def replies_workflow(**reply_kw: Any) -> Any:
    reply = action_step(
        "reply",
        "github.review_reply",
        {
            "actor": "gh-app",
            "repo": REPO,
            "number": 3,
            "comment_id": "inputs.item.comment_id",
            "body": "{{ inputs.item.body }}",
            "resolve": True,
        },
        inputs=(port("item", "object"),),
        outputs=(port("id", "integer"),),
        **reply_kw,
    )
    loop = step(
        "replies",
        "for_each",
        inputs=(port("items", "array"),),
        outputs=(port("id", "array"),),
        config={"items": "items"},
        max_iterations=10,
        body=(reply,),
    )
    return workflow(
        (loop,),
        (edge("inputs", "threads", "replies", "items"),),
        inputs=(port("threads", "array"),),
    )


THREADS = [
    {"comment_id": 101, "body": "fixed the typo"},
    {"comment_id": 102, "body": "added the test"},
    {"comment_id": 103, "body": "renamed it"},
]


def replies_rule() -> Any:
    return rule(workflow_inputs={"threads": {"$literal": THREADS}})


def test_for_each_posts_one_reply_per_item_with_distinct_keys(store, clock):
    put_app(store)
    p = Ports(store, clock)
    ex = p.executor()
    run = ex.start(replies_rule(), replies_workflow())
    ex.run_until_idle()

    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert [c[1]["comment_id"] for c in p.reply.calls] == [101, 102, 103]
    assert [c[1]["body"] for c in p.reply.calls] == [t["body"] for t in THREADS]
    keys = [c[0] for c in p.reply.calls]
    assert keys == [idempotency_key(run["id"], step_key("replies", i, "reply")) for i in range(3)]
    assert len(set(keys)) == 3
    assert {c[2].kind for c in p.reply.calls} == {"action"}
    assert step_state(doc, "replies")["outputs"] == {"id": [1, 1, 1]}
    usage = store.get(USAGE_COLLECTION, "gh-app")  # every reply went through the actor limits
    assert sorted(usage["done"]) == sorted(keys)


def test_crash_mid_loop_resumes_without_invoking_any_reply_twice(store, clock):
    """A port that deduplicates on the key (like github.push): the resumed attempt reuses
    the iteration's key, so the crashed reply happens once and the loop finishes."""
    put_app(store)
    p = Ports(store, clock, reply_dedups=True)
    key1 = step_key("replies", 1, "reply")
    p.reply.on(key1, ("crash", {"id": 1}))
    ex = p.executor(lease=timedelta(seconds=30))
    run = ex.start(replies_rule(), replies_workflow())
    with pytest.raises(Crash):
        ex.run_until_idle()
    assert step_state(store.get(RUNS_COLLECTION, run["id"]), key1)["status"] == "dispatching"

    clock.advance(31)
    restarted = Ports(store, clock, reply_dedups=True)
    restarted.reply = p.reply  # the remote side outlives the engine
    restarted.mapping["action:github.review_reply"] = p.reply
    again = restarted.executor()
    again.run_until_idle()

    doc = again.run(run["id"])
    assert doc["status"] == "succeeded"
    keys = [idempotency_key(run["id"], step_key("replies", i, "reply")) for i in range(3)]
    assert [p.reply.effects[k] for k in keys] == [1, 1, 1]  # no reply posted twice
    assert [c[0] for c in p.reply.calls].count(keys[1]) == 2  # re-asked with the same key
    again.run_until_idle()  # a finished run is never re-dispatched
    assert len(p.reply.calls) == 4


def test_crash_mid_loop_on_a_port_that_cannot_dedupe_fails_instead_of_reposting(store, clock):
    """github.review_reply cannot deduplicate: as for a rule action, an unknown outcome is
    never retried (``unsafe_retry``) and later items are not posted."""
    put_app(store)
    p = Ports(store, clock, reply_dedups=False)
    key1 = step_key("replies", 1, "reply")
    p.reply.on(key1, ("crash", {"id": 1}))
    ex = p.executor(lease=timedelta(seconds=30))
    run = ex.start(replies_rule(), replies_workflow(retry=RetryPolicy(max_attempts=3)))
    with pytest.raises(Crash):
        ex.run_until_idle()
    clock.advance(31)
    again = p.executor()
    again.run_until_idle()

    doc = again.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, key1)["error"]["code"] == "unsafe_retry"
    assert [c[1]["comment_id"] for c in p.reply.calls] == [101, 102]  # 102 once, 103 never


def test_idempotent_flag_on_the_action_spec_allows_a_blind_retry(store, clock):
    put_app(store)
    p = Ports(store, clock, reply_dedups=False)
    wf = replies_workflow()
    body = wf.steps[0].body[0]
    body.config["action"]["idempotent"] = True
    key1 = step_key("replies", 1, "reply")
    p.reply.on(key1, ("crash", {"id": 1}))
    ex = p.executor(lease=timedelta(seconds=30))
    run = ex.start(replies_rule(), wf)
    with pytest.raises(Crash):
        ex.run_until_idle()
    clock.advance(31)
    again = p.executor()
    again.run_until_idle()
    assert again.run(run["id"])["status"] == "succeeded"


@pytest.mark.parametrize("flag", ["false", "true", 1, None])
def test_start_refuses_a_non_boolean_idempotent_on_the_action_spec(store, clock, flag):
    put_app(store)
    wf = replies_workflow()
    wf.steps[0].body[0].config["action"]["idempotent"] = flag
    with pytest.raises(RunError) as exc:
        Ports(store, clock).executor().start(replies_rule(), wf)
    paths = {(e["path"], e["code"]) for e in exc.value.details}
    assert ("steps[0].body[0].config.action.idempotent", "type") in paths


@pytest.mark.parametrize("flag", ["false", "true", 1])
def test_a_non_boolean_idempotent_that_reached_a_run_fails_closed(store, clock, flag):
    """A pinned definition that slipped past validation (an older or hand-written doc):
    only ``true`` itself allows a blind retry, so the crashed reply is not re-posted."""
    put_app(store)
    p = Ports(store, clock, reply_dedups=False)
    key1 = step_key("replies", 1, "reply")
    p.reply.on(key1, ("crash", {"id": 1}))
    ex = p.executor(lease=timedelta(seconds=30))
    run = ex.start(replies_rule(), replies_workflow(retry=RetryPolicy(max_attempts=3)))
    doc = store.get(RUNS_COLLECTION, run["id"])
    doc["workflow"]["definition"]["steps"][0]["body"][0]["config"]["action"]["idempotent"] = flag
    store.put(RUNS_COLLECTION, doc)
    with pytest.raises(Crash):
        ex.run_until_idle()
    clock.advance(31)
    again = p.executor()
    again.run_until_idle()

    doc = again.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, key1)["error"]["code"] == "unsafe_retry"
    assert [c[1]["comment_id"] for c in p.reply.calls] == [101, 102]  # 102 posted once


# ------------------------------------------------------------------ actor limits


def test_named_actor_at_its_concurrency_cap_blocks_the_step_not_fails_it(store, clock):
    put_app(store, max_concurrency=1)
    store.put(
        USAGE_COLLECTION,
        {
            "id": "gh-app",
            "actor": "gh-app",
            "day": clock().strftime("%Y-%m-%d"),
            "tokens": 0,
            "inflight": [{"key": "another-run", "until": "2999-01-01T00:00:00+00:00"}],
            "done": [],
            "rev": 1,
        },
    )
    p = Ports(store, clock)
    ex = p.executor()
    run = ex.start(pr_rule(), push_then_comment_workflow())
    ex.run_until_idle()

    doc = ex.run(run["id"])
    st = step_state(doc, "push")
    assert doc["status"] == "running"
    assert st["status"] == "blocked"  # try later, no attempt failed
    assert st["error"]["code"] == "blocked"
    assert p.push.calls == []

    # the other run releases its slot: the blocked step is asked again and completes
    usage = store.get(USAGE_COLLECTION, "gh-app")
    store.put(USAGE_COLLECTION, {**usage, "inflight": []})
    clock.advance(6)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert len(p.push.calls) == 1


def test_router_wraps_an_action_step_in_the_named_actors_limited_actor(store, clock):
    put_app(store, max_concurrency=1)
    p = Ports(store, clock)
    router = ActorRouter(store, ports=p.mapping, clock=clock)
    seen: list[Any] = []
    real_call = router.__call__

    def spy(ctx):
        port_ = real_call(ctx)
        seen.append((ctx, port_))
        return port_

    ex = Executor(store, "spark", spy, clock=clock)
    ex.start(pr_rule(), push_then_comment_workflow())
    ex.run_until_idle()
    push = [pt for ctx, pt in seen if ctx.step_id == "push"]
    assert push
    assert isinstance(push[0], LimitedActor)
    assert push[0].inner is p.push
    assert push[0].actor == "gh-app"
    assert push[0].limits.max_concurrency == 1


@pytest.mark.parametrize("make", ["disabled", "unknown"])
def test_unknown_or_disabled_actor_fails_actor_unavailable(store, clock, make):
    if make == "disabled":
        put_app(store, enabled=False)
    p = Ports(store, clock)
    ex = p.executor()
    run = ex.start(pr_rule(), push_then_comment_workflow())
    ex.run_until_idle()

    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "push"
    assert doc["error"]["code"] == ACTOR_UNAVAILABLE
    assert "gh-app" in doc["error"]["message"]
    assert p.push.calls == []
    assert p.comment.calls == []


def test_action_step_runs_on_the_machine_its_actor_lives_on(store, clock):
    enrol_online(store, clock, machine("thor"))
    put_app(store)
    app = store.get(ACTORS_COLLECTION, "gh-app")
    store.put(ACTORS_COLLECTION, {**app, "machine": "spark"})
    p = Ports(store, clock)
    thor = Executor(store, "thor", ActorRouter(store, ports=p.mapping, clock=clock), clock=clock)
    run = thor.start(pr_rule(), push_then_comment_workflow())
    thor.run_until_idle()
    assert p.push.calls == []  # thor ran the agent step but never the push
    p.executor().run_until_idle()
    doc = thor.run(run["id"])
    assert doc["status"] == "succeeded"
    assert [c[2].host for c in p.push.calls] == ["spark"]


def _beat(store: MemoryStore, clock: Clock, name: str) -> None:
    store.put(
        HEARTBEAT_COLLECTION,
        {"id": name, "machine": name, "ts": clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
    )


def _crash_on_spark_then_spark_goes_offline(store, clock, p: Ports, rule_, wf, key: str) -> Any:
    """``key`` crashes mid-call on spark (the actor's machine); the lease lapses and spark
    stops beating while thor stays online. Returns the run."""
    enrol_online(store, clock, machine("thor"))
    put_app(store)
    app = store.get(ACTORS_COLLECTION, "gh-app")
    store.put(ACTORS_COLLECTION, {**app, "machine": "spark"})
    p.push.on(key, ("crash", {"head_after": SHA_B}))
    ex = p.executor(lease=timedelta(seconds=30), holder_offline_after=timedelta(seconds=60))
    run = ex.start(rule_, wf)
    with pytest.raises(Crash):
        ex.run_until_idle()
    assert step_state(store.get(RUNS_COLLECTION, run["id"]), key)["host"] == "spark"
    clock.advance(90)  # the lease lapsed and spark's last beat is stale
    _beat(store, clock, "thor")
    return run


def _thor(store, clock, p: Ports) -> Executor:
    router = ActorRouter(store, ports=p.mapping, clock=clock)
    return Executor(store, "thor", router, clock=clock, holder_offline_after=timedelta(seconds=60))


def test_resumed_action_step_stays_on_its_actors_machine_after_a_crash(store, clock):
    """Recovery of an unplaced action step keeps the actor-derived placement: thor (which
    lacks the actor's host-local credentials and files) never re-invokes the push, even
    with spark offline; spark resumes it with the same key when it comes back."""
    p = Ports(store, clock)
    run = _crash_on_spark_then_spark_goes_offline(
        store, clock, p, pr_rule(), push_then_comment_workflow(), "push"
    )
    _thor(store, clock, p).run_until_idle()
    assert [c[2].host for c in p.push.calls] == ["spark"]  # thor did not take it over

    _beat(store, clock, "spark")  # spark is back
    p.executor(holder_offline_after=timedelta(seconds=60)).run_until_idle()
    doc = store.get(RUNS_COLLECTION, run["id"])
    assert doc["status"] == "succeeded"
    assert [c[2].host for c in p.push.calls] == ["spark", "spark"]
    assert p.push.effects[idempotency_key(run["id"], "push")] == 1


def test_resumed_rule_action_stays_on_its_actors_machine_after_a_crash(store, clock):
    """The same restriction for a rule's terminal action routed by its actor."""
    action = Action(
        kind="github.push",
        params={
            "actor": "gh-app",
            "repo": REPO,
            "number": 3,
            "head_branch": "fix",
            "expected_head_sha": SHA_A,
            "commit_sha": SHA_B,
            "source": "/tmp/agent-worktree",
        },
    )
    p = Ports(store, clock)
    run = _crash_on_spark_then_spark_goes_offline(
        store, clock, p, rule(workflow_id=None, action=action), None, ACTION_STEP
    )
    _thor(store, clock, p).run_until_idle()
    assert [c[2].host for c in p.push.calls] == ["spark"]

    _beat(store, clock, "spark")
    p.executor(holder_offline_after=timedelta(seconds=60)).run_until_idle()
    assert store.get(RUNS_COLLECTION, run["id"])["status"] == "succeeded"
    assert [c[2].host for c in p.push.calls] == ["spark", "spark"]


# ------------------------------------------------------------------ outcomes


def test_port_failure_maps_to_the_step_outcome(store, clock):
    put_app(store)
    p = Ports(store, clock)
    p.push.on("push", ("fail", "gate_not_passed", False))
    ex = p.executor()
    run = ex.start(pr_rule(), push_then_comment_workflow())
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "push")["error"] == {
        "code": "actor_failed",
        "message": "gate_not_passed",
    }
    assert p.comment.calls == []


def test_retryable_port_failure_retries_with_the_same_key(store, clock):
    put_app(store)
    p = Ports(store, clock)
    p.push.on("push", ("fail", "remote_unreachable", True))
    wf = push_then_comment_workflow()
    steps = (wf.steps[0], push_step(retry=RetryPolicy(max_attempts=2, backoff_s=1)), wf.steps[2])
    wf = workflow(steps, wf.edges, inputs=wf.inputs)
    ex = p.executor()
    run = ex.start(pr_rule(), wf)
    ex.run_until_idle()
    assert step_state(ex.run(run["id"]), "push")["status"] == "retry_wait"
    clock.advance(2)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert len({c[0] for c in p.push.calls}) == 1
    assert len(p.push.calls) == 2


def test_builtin_code_port_refuses_an_unrouted_action_step(clock):
    from culture_rules.engine.actorport import InvocationContext

    ctx = InvocationContext("r", "s", "code", "spark", 1, None, {"builtin": "action"})
    res = BuiltinCodePort({}).invoke({}, "k", clock(), context=ctx)
    assert (res.outcome, res.retryable) == ("failed", False)
    assert res.error.startswith("action_step_unrouted")
