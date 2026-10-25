"""library items t7: the new builtins run in a workflow on a node's registered ports.

One node (spark) runs a small workflow on MemoryStore with ``runner.default_ports``: ``read``
(json.read) takes a list key from a JSON file under a root, ``merge`` (checklist.merge) stores the
given items under it, ``render`` (checklist.render) writes the Markdown and its revision, and
``bind`` (checklist.bind) records a pushed item at that revision. A second workflow flips one item
with checklist.set. Synthetic data only.
"""

from __future__ import annotations

import json

import pytest

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.model.validate import validate
from culture_rules.model.workflow import Workflow
from culture_rules.node import runner
from culture_rules.node.daemon import Node
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.node.test_node import BEATS


def _port(name, typ, required=True):
    return {"name": name, "type": typ, "required": required, "description": ""}


def _edge(source, source_port, target, target_port):
    return {
        "source": source,
        "source_port": source_port,
        "target": target,
        "target_port": target_port,
    }


def _code(step_id, builtin, inputs, outputs, **config):
    return {
        "id": step_id,
        "name": step_id,
        "kind": "code",
        "inputs": inputs,
        "outputs": outputs,
        "config": {"builtin": builtin, **config},
    }


def _items_workflow(root: str) -> dict:
    return {
        "id": "list-items",
        "name": "List items",
        "inputs": [
            _port("path", "string"),
            _port("items", "array"),
            _port("item_id", "string"),
        ],
        "steps": [
            _code(
                "read",
                "json.read",
                [_port("path", "string")],
                [_port("key", "string")],
                root=root,
                fields={"key": "list.key"},
            ),
            _code(
                "merge",
                "checklist.merge",
                [_port("list_key", "string"), _port("items", "array")],
                [_port("revision", "integer"), _port("total", "integer")],
                prefix="todo",
                title="Things to do",
            ),
            _code(
                "render",
                "checklist.render",
                [_port("list_key", "string"), _port("revision", "integer", required=False)],
                [_port("markdown", "string"), _port("revision", "integer")],
            ),
            _code(
                "bind",
                "checklist.bind",
                [
                    _port("list_key", "string"),
                    _port("item_id", "string"),
                    _port("revision", "integer"),
                ],
                [_port("bound", "boolean"), _port("stale", "boolean")],
            ),
        ],
        "edges": [
            _edge("inputs", "path", "read", "path"),
            _edge("read", "key", "merge", "list_key"),
            _edge("inputs", "items", "merge", "items"),
            _edge("read", "key", "render", "list_key"),
            _edge("merge", "revision", "render", "revision"),
            _edge("read", "key", "bind", "list_key"),
            _edge("inputs", "item_id", "bind", "item_id"),
            _edge("render", "revision", "bind", "revision"),
        ],
        "outputs": [
            {"name": "markdown", "source": "steps.render.outputs.markdown", "type": "string"},
            {"name": "bound", "source": "steps.bind.outputs.bound", "type": "boolean"},
            {"name": "stale", "source": "steps.bind.outputs.stale", "type": "boolean"},
        ],
    }


FLIP = {
    "id": "flip-item",
    "name": "Flip one item",
    "inputs": [
        _port("list_key", "string"),
        _port("id", "string"),
        _port("done", "boolean"),
        _port("at", "number"),
        _port("by", "string"),
    ],
    "steps": [
        _code(
            "set",
            "checklist.set",
            [
                _port("list_key", "string"),
                _port("id", "string"),
                _port("done", "boolean"),
                _port("at", "number"),
                _port("by", "string"),
            ],
            [_port("applied", "boolean"), _port("reason", "string", required=False)],
        )
    ],
    "edges": [
        _edge("inputs", name, "set", name) for name in ("list_key", "id", "done", "at", "by")
    ],
    "outputs": [{"name": "applied", "source": "steps.set.outputs.applied", "type": "boolean"}],
}


class World:
    def __init__(self, root: str) -> None:
        self.clock = Clock()
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        self.base.put("workflows", _items_workflow(root))
        self.base.put("workflows", FLIP)
        self.node = Node(
            self.base.peer(),
            "spark",
            actors=runner.default_ports(self.base, "spark"),
            clock=self.clock,
            heartbeat_options=BEATS,
        )
        self.node.start()

    def run(self, workflow_id: str, inputs: dict) -> dict:
        started = self.node.executor.start_workflow(workflow_id, inputs, "test")
        for _ in range(12):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors
        return self.base.get(RUNS_COLLECTION, started["id"])


@pytest.fixture
def world(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"list": {"key": "rec-1"}}))
    return World(str(tmp_path))


@pytest.mark.parametrize("definition", [_items_workflow("/data"), FLIP])
def test_the_workflows_validate(definition):
    assert validate(Workflow.from_dict(definition)) == []


def test_the_items_reach_markdown_and_the_pushed_item_is_bound(world):
    run = world.run(
        "list-items", {"path": "meta.json", "items": ["Call the plumber"], "item_id": "7"}
    )
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])
    markdown = run["outputs"]["markdown"]
    assert "# Things to do" in markdown
    assert "- [ ] Call the plumber <!-- todo:todo.rec-1.1 -->" in markdown
    assert (run["outputs"]["bound"], run["outputs"]["stale"]) == (True, False)


def test_a_flip_marks_the_item_done_in_the_next_render(world):
    world.run("list-items", {"path": "meta.json", "items": ["Call the plumber"], "item_id": "7"})
    flip = world.run(
        "flip-item",
        {
            "list_key": "rec-1",
            "id": "todo.rec-1.1",
            "done": True,
            "at": 1792872123.9,
            "by": "operator",
        },
    )
    assert flip["status"] == "succeeded", (flip.get("error"), flip["steps"])
    assert flip["outputs"]["applied"] is True
    again = world.run(
        "list-items", {"path": "meta.json", "items": ["call the plumber"], "item_id": "7"}
    )
    assert "- [x] Call the plumber <!-- todo:todo.rec-1.1 done_at=" in again["outputs"]["markdown"]
