"""A builtin code step's ``config.with`` (deviation d8): references onto its inputs.

A loop body's builtin reads fields of the current item (``inputs.item.content``) the way an
action step's params do; an edge's value for the same port wins.
"""

from __future__ import annotations

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.runs import Executor, step_state
from culture_rules.node.actions.markdown_render import MARKDOWN_RENDER_BUILTIN, MarkdownRenderPort
from culture_rules.node.runner import BuiltinCodePort, NoopAction
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, edge, port, rule, step, workflow

MESSAGES = [
    {"id": "101", "content": "first", "author_name": "ori"},
    {"id": "102", "content": "second", "author_name": "dana"},
]


class ListPort:
    """A builtin that outputs MESSAGES and records nothing."""

    supports_idempotency_key = True

    def invoke(self, input, key, deadline, *, context):
        return InvocationResult.completed({"items": MESSAGES, "title": "From the edge"})


class Recorder:
    """Wraps markdown.render, keeping each input it was invoked with."""

    supports_idempotency_key = True

    def __init__(self):
        self.inputs = []

    def invoke(self, input, key, deadline, *, context):
        self.inputs.append(dict(input))
        return MarkdownRenderPort().invoke(input, key, deadline, context=context)


def loop_workflow(with_, *, title_edge=False):
    render = step(
        "render",
        inputs=(port("item", "object"), port("title", "string", required=False)),
        outputs=(port("markdown", "string"), port("content_base64", "string")),
        config={"builtin": MARKDOWN_RENDER_BUILTIN, "with": with_},
    )
    each = step(
        "each",
        "for_each",
        inputs=(port("items", "array"), port("title", "string", required=False)),
        outputs=(port("markdown", "array"),),
        max_iterations=5,
        body=(render,),
    )
    listing = step(
        "list",
        outputs=(port("items", "array"), port("title", "string")),
        config={"builtin": "test.list"},
    )
    edges = [edge("list", "items", "each", "items")]
    if title_edge:
        edges += [edge("list", "title", "each", "title")]
    return workflow((listing, each), tuple(edges))


def run(wf):
    recorder = Recorder()
    code = BuiltinCodePort({"test.list": ListPort(), MARKDOWN_RENDER_BUILTIN: recorder})
    ports = {"code": code, "action:noop": NoopAction(), "action": NoopAction()}
    ex = Executor(MemoryStore(), "spark", ports, clock=Clock())
    started = ex.start(rule(), wf)
    ex.run_until_idle()
    return ex.run(started["id"]), recorder


def test_a_loop_body_builtin_reads_the_items_fields_through_with():
    doc, rec = run(
        loop_workflow(
            {
                "title": "Message {{ inputs.item.id }}",
                "text": "inputs.item.content",
                "author": "inputs.item.author_name",
            }
        )
    )
    assert doc["status"] == "succeeded", doc["steps"]
    assert [(i["title"], i["text"], i["author"]) for i in rec.inputs] == [
        ("Message 101", "first", "ori"),
        ("Message 102", "second", "dana"),
    ]
    pages = step_state(doc, "each")["outputs"]["markdown"]
    assert pages[0].startswith("# Message 101")
    assert "second" in pages[1]


def test_an_edge_value_for_the_same_port_wins_over_with():
    doc, rec = run(
        loop_workflow({"title": "inputs.item.id", "text": "inputs.item.content"}, title_edge=True)
    )
    assert doc["status"] == "succeeded", doc["steps"]
    assert {i["title"] for i in rec.inputs} == {"From the edge"}


def test_a_literal_in_with_reaches_the_builtin_as_is():
    doc, rec = run(loop_workflow({"title": "Saved", "text": "inputs.item.content"}))
    assert doc["status"] == "succeeded", doc["steps"]
    assert {i["title"] for i in rec.inputs} == {"Saved"}
