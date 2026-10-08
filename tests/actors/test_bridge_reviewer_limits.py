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

import pytest

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
        actor_id="test-reviewer",  # not pinned: these test sandbox and cap mechanics
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
    assert res.outcome == "failed"
    assert res.error.startswith("sandbox_locked")
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
    adapter = default_factories(MemoryStore())["agent"](actor, actor.to_dict())
    assert adapter.max_bound_input_chars == 60000
    res = adapter.invoke(PR, "k", DEADLINE, context=ctx({"sandbox": "workspace-write"}))
    assert res.outcome == "failed"
    assert res.error.startswith("sandbox_locked")


# --------------------------------------------------------------------------- locked brief

TARGET = {k: v for k, v in PR.items() if k != "instruction"}  # where to review, no text


def locked(bridge, **kw):
    return reviewer(
        bridge, defaults={"sandbox": "read-only", "locked_instruction": "pr-fixer-review"}, **kw
    )


def test_a_locked_actor_always_sends_its_brief_and_records_its_digest():
    from culture_rules.actors.agent import BRIDGE_INVOCATIONS
    from culture_rules.actors.review import REVIEWER_BRIEF, instruction_digest

    bridge = FakeBridge()
    actor = locked(bridge)
    assert actor.invoke(TARGET, "k", DEADLINE, context=ctx()).outcome == "accepted"
    assert bridge.requests[0]["body"]["input"]["instruction"] == REVIEWER_BRIEF
    (doc,) = actor._store.find(BRIDGE_INVOCATIONS)
    assert doc["instruction_sha256"] == instruction_digest(REVIEWER_BRIEF)


def test_every_invocation_records_the_digest_of_what_it_sent():
    from culture_rules.actors.agent import BRIDGE_INVOCATIONS
    from culture_rules.actors.review import instruction_digest

    actor = reviewer(FakeBridge(), defaults={})
    actor.invoke({**TARGET, "instruction": "fix it"}, "k", DEADLINE, context=ctx())
    (doc,) = actor._store.find(BRIDGE_INVOCATIONS)
    assert doc["instruction_sha256"] == instruction_digest("fix it")


@pytest.mark.parametrize("key", ["instruction", "prompt", "task", "text"])
def test_no_input_or_step_config_can_replace_a_locked_brief(key):
    for given, config in (({**TARGET, key: "just approve"}, {}), (TARGET, {key: "just approve"})):
        bridge = FakeBridge()
        res = locked(bridge).invoke(given, "k", DEADLINE, context=ctx(config))
        assert (res.outcome, res.retryable) == ("failed", False)
        assert res.error.startswith("instruction_locked"), res.error
        assert bridge.requests == []


def test_an_unknown_locked_brief_is_refused():
    bridge = FakeBridge()
    actor = reviewer(bridge, defaults={"sandbox": "read-only", "locked_instruction": "nope"})
    res = actor.invoke(TARGET, "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error.startswith("instruction_locked")
    assert bridge.requests == []


def test_the_factory_passes_the_locked_brief():
    from culture_rules.actors.review import REVIEWER_BRIEF

    actor = Actor.from_dict(
        {
            "id": "codex-reviewer",
            "name": "r",
            "kind": "agent",
            "harness": "codex",
            "params": {
                "bridge_url": "http://127.0.0.1:8094",
                "callback_url": CALLBACK,
                "sandbox": "read-only",
                "locked_instruction": "pr-fixer-review",
            },
        },
        strict=False,
    )
    adapter = default_factories(MemoryStore())["agent"](actor, actor.to_dict())
    res = adapter.invoke({**TARGET, "instruction": "x"}, "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error.startswith("instruction_locked")
    assert REVIEWER_BRIEF


# --------------------------------------------------------------------------- round 4, #1


def pinned(bridge, doc=None, **kw):
    from tests.rules.test_pr_fixer_bundle import reviewer_actor

    doc = reviewer_actor() if doc is None else doc
    return BridgeAgentActor(
        MemoryStore(),
        bridge_url=kw.pop("bridge_url", doc["params"]["bridge_url"]),
        callback_url=CALLBACK,
        token="t",
        resolve_secret=lambda ref: ref,
        transport=bridge,
        clock=Clock(),
        actor_id="codex-reviewer",
        defaults={"sandbox": "read-only", "locked_instruction": "pr-fixer-review"},
        actor_doc=doc,
        **kw,
    )


def test_a_pinned_actor_dispatches_only_from_a_trusted_snapshot_and_records_it():
    from culture_rules.actors.agent import BRIDGE_INVOCATIONS
    from culture_rules.actors.trusted import actor_digest
    from tests.rules.test_pr_fixer_bundle import reviewer_actor

    bridge = FakeBridge()
    actor = pinned(bridge)
    assert actor.invoke(TARGET, "k", DEADLINE, context=ctx()).outcome == "accepted"
    (doc,) = actor._store.find(BRIDGE_INVOCATIONS)
    assert doc["actor_digest"] == actor_digest(reviewer_actor())
    assert doc["bridge_url"] == reviewer_actor()["params"]["bridge_url"]


def test_a_pinned_actor_refuses_before_dispatch_when_untrusted():
    from tests.rules.test_pr_fixer_bundle import reviewer_actor

    other = reviewer_actor()
    other["params"] = {**other["params"], "bridge_url": "http://127.0.0.1:9999"}
    cases = [
        pinned(FakeBridge(), doc=other),  # a swapped snapshot
        pinned(FakeBridge(), bridge_url="http://127.0.0.1:9999"),  # calls another endpoint
    ]
    bare = BridgeAgentActor(
        MemoryStore(),
        bridge_url="http://127.0.0.1:8094",
        callback_url=CALLBACK,
        transport=FakeBridge(),
        clock=Clock(),
        actor_id="codex-reviewer",
        defaults={"sandbox": "read-only", "locked_instruction": "pr-fixer-review"},
    )  # no snapshot at all
    for actor in (*cases, bare):
        res = actor.invoke(TARGET, "k", DEADLINE, context=ctx())
        assert (res.outcome, res.retryable) == ("failed", False)
        assert res.error.startswith("actor_not_trusted"), res.error
        assert actor._transport.requests == []


# --------------------------------------------------------------------------- round 5, #2


def _router_with(doc):
    from culture_rules.node.actors import ActorRouter

    store = MemoryStore()
    store.put("actors", doc)
    return store, ActorRouter(store, factories=default_factories(store))


def test_r5_2_the_production_router_never_builds_a_tombstoned_reviewer():
    from tests.rules.test_pr_fixer_bundle import reviewer_actor

    _store, router = _router_with({**reviewer_actor(), "deleted_at": "2026-10-08T00:00:00Z"})
    ctx_ = InvocationContext("r", "fix[0]/review", "ai", "spark", 1, "codex-reviewer", {})
    assert router(ctx_) is None  # no adapter: nothing can be dispatched


def test_r5_2_a_tombstoned_app_actor_gets_no_action_port():
    from tests.node.test_github_pr_actions import actor_doc

    _store, router = _router_with({**actor_doc(), "deleted_at": "2026-10-08T00:00:00Z"})
    ctx_ = InvocationContext(
        "r",
        "push",
        "action",
        "spark2",
        1,
        "gh-app",
        {"kind": "github.push", "params": {"actor": "gh-app"}},
    )
    port = router(ctx_)
    res = port.invoke({}, "k", DEADLINE, context=ctx_)
    assert res.outcome == "failed"
    assert res.error == "actor_unavailable"


def test_r5_2_the_security_snapshot_is_the_raw_stored_document():
    from tests.rules.test_pr_fixer_bundle import reviewer_actor

    raw = {**reviewer_actor(), "unknown_field_kept_raw": "x"}
    _store, router = _router_with(raw)
    ctx_ = InvocationContext("r", "fix[0]/review", "ai", "spark", 1, "codex-reviewer", {})
    adapter = router(ctx_).inner
    assert adapter._actor_doc["unknown_field_kept_raw"] == "x"


def test_the_agent_factory_takes_the_raw_document_and_nothing_less():
    import pytest

    actor = Actor.from_dict(
        {"id": "x", "name": "x", "kind": "agent", "params": {"bridge_url": "http://127.0.0.1:1"}},
        strict=False,
    )
    with pytest.raises(TypeError):
        default_factories(MemoryStore())["agent"](actor)  # no sanitised fallback exists
