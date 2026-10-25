"""run.read: a continuation reads a step's outputs from the run that started it."""

from __future__ import annotations

from datetime import UTC, datetime

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.actions.markdown_render import MARKDOWN_RENDER_BUILTIN, MarkdownRenderPort
from culture_rules.node.actions.run_read import RUN_READ_BUILTIN, RunReadPort
from culture_rules.node.daemon import Node
from culture_rules.node.runner import BuiltinCodePort, NoopAction
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.events.fakes import FakeEventSource, envelope
from tests.node.test_node import BEATS

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def port(name, typ="string", required=True):
    return {"name": name, "type": typ, "required": required, "description": ""}


def code_step(step_id, builtin, inputs, outputs, **config):
    return {
        "id": step_id,
        "name": step_id,
        "kind": "code",
        "enabled": True,
        "config": {"builtin": builtin, **config},
        "inputs": [port(i) for i in inputs],
        "outputs": [port(o) for o in outputs],
        "body": [],
    }


MAKE = {
    "id": "make",
    "name": "make",
    "schema_version": "1.0",
    "enabled": True,
    "inputs": [port("title"), port("text")],
    "outputs": [{"name": "done", "source": "steps.page.outputs.markdown", "type": "string"}],
    "steps": [
        code_step(
            "page", MARKDOWN_RENDER_BUILTIN, ["title", "text"], ["markdown", "content_base64"]
        )
    ],
    "edges": [
        {"source": "inputs", "source_port": "title", "target": "page", "target_port": "title"},
        {"source": "inputs", "source_port": "text", "target": "page", "target_port": "text"},
    ],
}
FOLLOW = {
    "id": "follow",
    "name": "follow",
    "schema_version": "1.0",
    "enabled": True,
    "inputs": [],
    "outputs": [{"name": "seen", "source": "steps.read.outputs.content_base64", "type": "string"}],
    "steps": [
        code_step(
            "read",
            RUN_READ_BUILTIN,
            [],
            ["content_base64", "run_id"],
            step="page",
            fields=["content_base64"],
        )
    ],
    "edges": [],
}


def rule(rule_id, workflow, trigger, inputs=None, condition=None):
    return {
        "id": rule_id,
        "name": rule_id,
        "enabled": True,
        "schema_version": "1.0",
        "trigger": trigger,
        "condition": condition,
        "workflow": {"id": workflow, "inputs": inputs or {}, "version": None},
        "action": {"kind": "noop", "params": {}},
        "placement": {"machine": "spark", "actor": None, "requirement": None},
    }


class World:
    def __init__(self) -> None:
        self.clock = Clock()
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        self.base.put("workflows", MAKE)
        self.base.put("workflows", FOLLOW)
        self.base.put(
            "rules",
            rule(
                "make-on-ping",
                "make",
                {"kind": "event", "params": {"type": "demo.ping"}},
                {"title": "trigger.data.title", "text": "trigger.data.text"},
            ),
        )
        self.base.put(
            "rules",
            rule(
                "follow-make",
                "follow",
                {"kind": "event", "params": {"type": "rules.run.succeeded"}},
                condition={
                    "op": "compare",
                    "cmp": "==",
                    "left": {"field": "data.workflow_id"},
                    "right": {"literal": "make"},
                },
            ),
        )
        self.source = FakeEventSource(name="sub@spark")
        builtins = {
            MARKDOWN_RENDER_BUILTIN: MarkdownRenderPort(),
            RUN_READ_BUILTIN: RunReadPort(self.base),
        }
        self.node = Node(
            self.base.peer(),
            "spark",
            actors={"action:noop": NoopAction(), "code": BuiltinCodePort(builtins)},
            event_source=self.source,
            clock=self.clock,
            heartbeat_options=BEATS,
        )
        self.node.start()

    def ping(self) -> None:
        self.source.publish(
            envelope(1, type="demo.ping", source="test://", data={"title": "T", "text": "hello"})
        )
        for _ in range(16):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors

    def runs(self, workflow):
        return [r for r in self.base.find(RUNS_COLLECTION) if r.get("workflow_id") == workflow]


def test_a_continuation_reads_a_step_output_of_the_run_that_started_it():
    world = World()
    world.ping()
    (make,) = world.runs("make")
    (follow,) = world.runs("follow")
    assert make["status"] == "succeeded"
    assert follow["status"] == "succeeded", follow.get("error")
    page = next(s for s in make["steps"] if s["def"] == "page")
    read = next(s for s in follow["steps"] if s["def"] == "read")
    assert read["outputs"]["content_base64"] == page["outputs"]["content_base64"]
    assert read["outputs"]["run_id"] == make["id"]


def ctx(run_id="run-own", **config):
    return InvocationContext(
        run_id=run_id, step_id="read", kind="code", host="spark", config=config
    )


def store_with(*runs):
    s = MemoryStore()
    for r in runs:
        s.put(RUNS_COLLECTION, r)
    return s


DONE = {
    "id": "run-done",
    "status": "succeeded",
    "steps": [
        {"def": "render", "key": "render", "outputs": {"html": "<p>x</p>", "subject": "S"}},
        {
            "def": "send",
            "key": "loop#0/send",
            "loop": {"parent": "l", "iteration": 0},
            "outputs": {"html": "inner"},
        },
    ],
}


def test_a_named_run_is_read_by_hand():
    res = RunReadPort(store_with(DONE)).invoke(
        {"run_id": "run-done"}, "k", NOW, context=ctx(step="render", fields=["html", "absent"])
    )
    assert res.outcome == COMPLETED, res.error
    assert res.output == {"html": "<p>x</p>", "absent": None, "run_id": "run-done"}


def test_without_a_run_id_a_hand_started_run_has_no_verified_upstream():
    own = {"id": "run-own", "status": "running", "trigger": {"type": "manual"}, "steps": []}
    res = RunReadPort(store_with(DONE, own)).invoke(
        {}, "k", NOW, context=ctx(step="render", fields=["html"])
    )
    assert res.outcome == FAILED
    assert res.retryable is False
    assert "chain_unverified" in res.error


def test_a_run_that_did_not_succeed_is_refused():
    res = RunReadPort(store_with({**DONE, "status": "failed"})).invoke(
        {"run_id": "run-done"}, "k", NOW, context=ctx(step="render", fields=["html"])
    )
    assert res.outcome == FAILED
    assert "run_not_succeeded" in res.error


def test_an_unknown_run_or_step_is_refused():
    port_ = RunReadPort(store_with(DONE))
    missing = port_.invoke({"run_id": "nope"}, "k", NOW, context=ctx(step="render", fields=["x"]))
    assert "run_not_found" in missing.error
    no_step = port_.invoke({"run_id": "run-done"}, "k", NOW, context=ctx(step="zzz", fields=["x"]))
    assert "step_not_found" in no_step.error


def test_a_loop_body_step_is_not_read_as_the_step():
    res = RunReadPort(store_with(DONE)).invoke(
        {"run_id": "run-done"}, "k", NOW, context=ctx(step="send", fields=["html"])
    )
    assert "step_not_found" in res.error


def test_step_and_fields_are_required():
    port_ = RunReadPort(store_with(DONE))
    for config in ({}, {"step": "render"}, {"fields": ["html"]}, {"step": "render", "fields": []}):
        res = port_.invoke({"run_id": "run-done"}, "k", NOW, context=ctx(**config))
        assert res.outcome == FAILED
        assert "bad_params" in res.error
