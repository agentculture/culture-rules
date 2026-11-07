"""d26: the agent's free status notes, as the deployed bridge reports them.

The cultureagent bridge (0.14.0) sends a ``progress`` callback per described agent event,
``{"note": "<kind>: <title>"}``; for a shell tool call the title is the command. An agent
told to run ``echo "STATUS: <note>"`` therefore reports a note the engine reads out of the
progress note, cleans (culture_rules.extensions.github.public_text) and keeps - the last few
only - on the bridge invocation, for the PR's status comment. Runs whose rule writes a status
comment tell the agent how (a fixed hint appended to its instruction); a locked brief never changes.
"""

from __future__ import annotations

import pytest

from culture_rules.actors.agent import RECORDED, record_bridge_event
from culture_rules.model.action import Action
from culture_rules.store.memory import MemoryStore
from tests.actors.test_bridge_agent import (
    FakeBridge,
    executor,
    invocation,
    make_actor,
    pr_wf,
    token_of,
)
from tests.engine.run_helpers import Clock, rule

GHP = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def progress(seq: int, note) -> dict:
    return {
        "event_id": f"evt_{seq}",
        "sequence": seq,
        "kind": "progress",
        "payload": {"note": note},
    }


def status_rule():
    action = Action(
        kind="github.comment",
        params={
            "actor": "github-app",
            "repo": "o/r",
            "number": 7,
            "body": "done",
            "status": True,
        },
    )
    return rule(action=action)


def accepted(store, clock, the_rule=None, **actor_kw):
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge, **actor_kw))
    run = ex.start(the_rule or rule(), pr_wf(timeout_s=3600))
    ex.run_until_idle()
    return bridge, invocation(store), run["id"]


def notes(store) -> list[str]:
    return [n["text"] for n in invocation(store).get("status_notes") or ()]


def test_progress_without_a_status_note_keeps_nothing(store, clock):
    bridge, doc, _ = accepted(store, clock)
    event = progress(2, "tool_call: Shell: pytest -q")
    assert record_bridge_event(store, doc["id"], token_of(bridge), event) == RECORDED
    assert notes(store) == []


def test_a_note_with_a_token_is_never_stored(store, clock):
    bridge, doc, _ = accepted(store, clock)
    event = progress(2, f'tool_call: Shell: echo "STATUS: pushing with {GHP}"')
    assert record_bridge_event(store, doc["id"], token_of(bridge), event) == RECORDED
    assert notes(store) == []
    assert GHP not in str(invocation(store))


def test_other_runs_get_their_instruction_unchanged(store, clock):
    bridge, _, _ = accepted(store, clock)
    assert bridge.requests[0]["body"]["input"]["instruction"] == "Fix the failing check"


SECRET = "synthetic-" + "bridge-secret-value-77"


def test_the_bridge_token_is_known_once_used(store, clock, monkeypatch):
    from culture_rules.actors import secrets

    monkeypatch.setattr(secrets, "_KNOWN", set())
    accepted(store, clock)  # make_actor's token "bridge-secret" is resolved to dispatch
    assert "bridge-secret" in secrets.known_values()
