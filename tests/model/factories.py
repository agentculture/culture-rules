"""Factories: one fully-populated instance of every model, overridable per field."""

from __future__ import annotations

from culture_rules.model.action import Action
from culture_rules.model.actor import Actor
from culture_rules.model.common import RetryPolicy
from culture_rules.model.machine import Machine
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.workflow import Edge, Output, Port, Step, Variable, Workflow


def make_action(**kw) -> Action:
    base = dict(
        kind="github.comment",
        name="comment on PR",
        params={
            "actor": "github-app",
            "body": "done: {{ workflow.outputs.summary }}",
            "number": 7,
            "repo": "agentculture/x",
        },
        timeout_s=30.0,
        retry=RetryPolicy(max_attempts=3, backoff_s=2.0, backoff_multiplier=2.0),
        idempotent=True,
    )
    base.update(kw)
    return Action(**base)


def make_rule(**kw) -> Rule:
    base = dict(
        id="r-review",
        name="Review merged PRs",
        description="When a PR merges, summarise it",
        trigger=Trigger(kind="event", params={"type": "github.pr.merged"}),
        condition={
            "op": "compare",
            "cmp": "==",
            "left": {"field": "data.base"},
            "right": {"literal": "main"},
        },
        workflow=WorkflowRef(id="wf-summary", version=2, inputs={"pr": "trigger.data.number"}),
        action=make_action(),
        must_after=("r-build",),
        may_after=("r-lint",),
        supersedes=("r-generic",),
        exclusive_group="pr-merge",
        priority=10,
        enabled=True,
    )
    base.update(kw)
    return Rule(**base)


def make_step(**kw) -> Step:
    base = dict(
        id="summarise",
        name="Summarise",
        kind="ai",
        inputs=(Port(name="diff", type="string"),),
        outputs=(Port(name="summary", type="string"),),
        placement=Placement(requirement=("gpu",)),
        timeout_s=120.0,
        retry=RetryPolicy(max_attempts=2),
        config={"prompt": "Summarise {{ inputs.diff }}", "model": "qwen"},
    )
    base.update(kw)
    return Step(**base)


def make_workflow(**kw) -> Workflow:
    fetch = Step(
        id="fetch",
        name="Fetch diff",
        kind="code",
        inputs=(Port(name="pr", type="integer"),),
        outputs=(Port(name="diff", type="string"),),
        placement=Placement(machine="spark"),
        config={"entrypoint": "tools.fetch:main"},
    )
    loop = Step(
        id="each_file",
        name="Each file",
        kind="for_each",
        inputs=(Port(name="items", type="array"),),
        outputs=(Port(name="results", type="array"),),
        max_iterations=50,
        config={"over": "inputs.items"},
        body=(
            Step(
                id="lint_file",
                name="Lint file",
                kind="code",
                inputs=(Port(name="item", type="any"),),
                outputs=(Port(name="ok", type="boolean"),),
            ),
        ),
    )
    ask = Step(
        id="approve",
        name="Approve",
        kind="actor_task",
        inputs=(Port(name="summary", type="string"),),
        outputs=(Port(name="approved", type="boolean"),),
        placement=Placement(actor="ori"),
        timeout_s=86400.0,
    )
    retry = Step(
        id="poll",
        name="Poll CI",
        kind="retry_until",
        outputs=(Port(name="green", type="boolean"),),
        max_iterations=10,
        config={"until": {"==": [{"var": "steps.poll.outputs.green"}, True]}},
    )
    gate = Step(
        id="gate",
        name="Gate",
        kind="logic",
        inputs=(Port(name="approved", type="boolean"),),
        outputs=(Port(name="go", type="boolean"),),
        config={"expr": {"var": "inputs.approved"}},
    )
    base = dict(
        id="wf-summary",
        name="PR summary",
        description="Summarise a PR and ask for approval",
        version=2,
        inputs=(Port(name="pr", type="integer"), Port(name="files", type="array", required=False)),
        variables=(Variable(name="attempts", type="integer", default=0),),
        steps=(fetch, make_step(), ask, loop, retry, gate),
        edges=(
            Edge(source="inputs", source_port="pr", target="fetch", target_port="pr"),
            Edge(source="fetch", source_port="diff", target="summarise", target_port="diff"),
            Edge(
                source="summarise", source_port="summary", target="approve", target_port="summary"
            ),
            Edge(source="inputs", source_port="files", target="each_file", target_port="items"),
            Edge(source="approve", source_port="approved", target="gate", target_port="approved"),
        ),
        outputs=(Output(name="summary", type="string", source="steps.summarise.outputs.summary"),),
    )
    base.update(kw)
    return Workflow(**base)


def make_actor(**kw) -> Actor:
    base = dict(
        id="spark-culture",
        name="culture agent on spark",
        kind="agent",
        capabilities=("code-review", "python"),
        harness="claude",
        model="claude-opus",
        machine="spark",
        config_source="repo",
        repo="agentculture/culture",
        params={"nick": "spark-culture"},
        enabled=True,
    )
    base.update(kw)
    return Actor(**base)


def make_machine(**kw) -> Machine:
    base = dict(
        name="spark",
        address="spark.tailnet.internal",
        platform="linux-aarch64",
        capabilities=("gpu", "docker"),
        roles=("store_member", "engine_node", "runner"),
        enabled=True,
    )
    base.update(kw)
    return Machine(**base)
