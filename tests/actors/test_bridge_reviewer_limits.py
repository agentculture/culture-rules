"""d20: a read-only bridge actor stays read-only, and bound inputs are never cut silently.

* An actor whose ``params.sandbox`` is ``read-only`` (the reviewer) cannot be widened by a
  step: a step ``config.sandbox`` other than ``read-only`` fails ``sandbox_locked`` before
  anything is dispatched, and the request always carries ``sandbox: read-only``.
* A cultureagent bridge appends the non-core inputs to the prompt and cuts them at its
  ``max_bound_input_chars`` (60000 by default) without saying so. An actor that sets
  ``params.max_bound_input_chars`` gets a refusal (``bound_inputs_too_large``) instead, so a
  reviewer never judges a diff it was only partly shown.
"""

from __future__ import annotations

import json
from datetime import timedelta

from culture_rules.actors.agent import BridgeAgentActor, bound_input_chars
from culture_rules.engine.actorport import InvocationContext
from culture_rules.model.actor import Actor
from culture_rules.node.actors import default_factories
from culture_rules.store.memory import MemoryStore
from tests.actors.test_bridge_agent import CALLBACK, PR, FakeBridge
from tests.engine.run_helpers import T0, Clock

DEADLINE = T0 + timedelta(hours=1)


def reviewer(bridge, **kw):
    kw.setdefault("defaults", {"sandbox": "read-only"})
    return BridgeAgentActor(
        MemoryStore(),
        bridge_url="http://127.0.0.1:8094",
        callback_url=CALLBACK,
        token="t",
        resolve_secret=lambda ref: ref,
        transport=bridge,
        clock=Clock(),
        actor_id="codex-reviewer",
        **kw,
    )


def ctx(config=None):
    return InvocationContext("r", "fix[0]/review", "ai", "spark", 1, "codex-reviewer", config or {})


def test_a_read_only_actor_dispatches_read_only():
    bridge = FakeBridge()
    res = reviewer(bridge).invoke(PR, "k", DEADLINE, context=ctx())
    assert res.outcome == "accepted"
    assert bridge.requests[0]["body"]["input"]["sandbox"] == "read-only"
    res = reviewer(FakeBridge()).invoke(PR, "k", DEADLINE, context=ctx({"sandbox": "read-only"}))
    assert res.outcome == "accepted"


def test_a_step_cannot_widen_a_read_only_actor():
    for wider in ("workspace-write", "danger-full-access", "", None, 1):
        bridge = FakeBridge()
        res = reviewer(bridge).invoke(PR, "k", DEADLINE, context=ctx({"sandbox": wider}))
        assert (res.outcome, res.retryable) == ("failed", False), wider
        assert res.error.startswith("sandbox_locked"), res.error
        assert bridge.requests == []


def test_an_input_named_sandbox_cannot_widen_it_either():
    bridge = FakeBridge()
    res = reviewer(bridge).invoke(
        {**PR, "sandbox": "danger-full-access"}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed" and res.error.startswith("sandbox_locked")
    assert bridge.requests == []


def test_other_actors_keep_their_step_level_sandbox():
    bridge = FakeBridge()
    actor = reviewer(bridge, defaults={"sandbox": "workspace-write"})
    actor.invoke(PR, "k", DEADLINE, context=ctx({"sandbox": "danger-full-access"}))
    assert bridge.requests[0]["body"]["input"]["sandbox"] == "danger-full-access"


def test_bound_inputs_over_the_cap_are_refused_not_cut():
    big = {**PR, "diff": "+x\n" * 30000}
    bridge = FakeBridge()
    res = reviewer(bridge, max_bound_input_chars=60000).invoke(big, "k", DEADLINE, context=ctx())
    assert (res.outcome, res.retryable) == ("failed", False)
    assert res.error.startswith("bound_inputs_too_large")
    assert bridge.requests == []
    small = {**PR, "diff": "+x\n" * 100}
    res = reviewer(FakeBridge(), max_bound_input_chars=60000).invoke(
        small, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "accepted"


def test_without_the_cap_nothing_changes():
    big = {**PR, "diff": "+x\n" * 30000}
    assert reviewer(FakeBridge()).invoke(big, "k", DEADLINE, context=ctx()).outcome == "accepted"


def test_the_size_is_measured_the_way_the_bridge_serialises_it():
    payload = {
        **PR,
        "instruction": "i" * 99999,
        "model": "m",
        "sandbox": "read-only",
        "mode": "yolo",
        "async": True,
        "diff": "é\n",
        "threads": [],
    }
    extras = {"diff": "é\n", "threads": []}
    assert bound_input_chars(payload) == len(json.dumps(extras, indent=2, ensure_ascii=False))


def test_the_factory_passes_the_cap_and_the_sandbox():
    actor = Actor.from_dict(
        {
            "id": "codex-reviewer",
            "name": "reviewer",
            "kind": "agent",
            "harness": "codex",
            "params": {
                "bridge_url": "http://127.0.0.1:8094",
                "callback_url": CALLBACK,
                "sandbox": "read-only",
                "max_bound_input_chars": 60000,
            },
        },
        strict=False,
    )
    adapter = default_factories(MemoryStore())["agent"](actor)
    assert adapter.max_bound_input_chars == 60000
    res = adapter.invoke(PR, "k", DEADLINE, context=ctx({"sandbox": "workspace-write"}))
    assert res.outcome == "failed" and res.error.startswith("sandbox_locked")
