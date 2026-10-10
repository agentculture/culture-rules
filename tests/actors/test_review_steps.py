"""#39 t5: the verdict step's ``review_steps`` and a Qwen reviewer's read-only mode.

* ``config.review_steps`` names one review step per reviewer; the review is the one that
  succeeded (none: ``review_missing``; two: ``review_invalid``, fail closed). The single
  ``config.review_step`` of the d20/d21 workflows still works; both at once, an empty or a
  repeated list is ``bad_config``.
* ``REVIEWER_BACKENDS`` holds codex and qwen. A reviewer on qwen is read-only only in mode
  ``plan`` (an approval policy, not a sandbox) with the read-only sandbox, and a step asking
  for another mode is refused; the bridge adapter locks a ``plan`` actor's mode as it locks
  a ``read-only`` sandbox (``mode_locked``).
* An actor not flagged as a reviewer is refused as such (``reviewer_not_allowed``) before
  its read-only settings are looked at.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from culture_rules.actors.agent import BridgeAgentActor
from culture_rules.actors.review import (
    READ_ONLY_MODES,
    REVIEWER_BACKENDS,
    ReviewError,
    _read_only_reviewer,
    _step_names,
    _the_review,
)
from culture_rules.engine.actorport import InvocationContext
from culture_rules.store.memory import MemoryStore
from tests.actors.test_bridge_agent import CALLBACK, PR, FakeBridge
from tests.actors.test_review_characterization import _actors, _code, _review_state, _run
from tests.engine.run_helpers import T0, Clock

DEADLINE = T0 + timedelta(hours=1)
REVIEW_CODEX = "review_codex"
REVIEW_QWEN = "review_qwen"
REVIEWS = (REVIEW_CODEX, REVIEW_QWEN)


# --------------------------------------------------------------------------- review_steps


def test_review_steps_name_every_review_step():
    names = _step_names({"builtin": "review", "review_steps": list(REVIEWS)})
    assert names == {"gate": "gate", "reviews": REVIEWS}


@pytest.mark.parametrize(
    ("config", "reviews"),
    [({}, ("review",)), ({"review_step": "check"}, ("check",))],
)
def test_the_single_review_step_still_works(config, reviews):
    assert _step_names(config)["reviews"] == reviews


@pytest.mark.parametrize(
    "config",
    [
        {"review_steps": []},
        {"review_steps": REVIEW_CODEX},
        {"review_steps": [REVIEW_CODEX, REVIEW_CODEX]},
        {"review_steps": [REVIEW_CODEX, ""]},
        {"review_steps": [REVIEW_CODEX, 3]},
        {"review_steps": list(REVIEWS), "review_step": "review"},
        {"review_step": ""},
        {"gate_step": None},
    ],
)
def test_malformed_review_steps_are_bad_config(config):
    with pytest.raises(ReviewError) as exc:
        _step_names(config)
    assert exc.value.code == "bad_config"


def _states(*statuses: str | None) -> dict:
    steps = [
        {"key": sid, "status": status}
        for sid, status in zip(REVIEWS, statuses, strict=True)
        if status is not None
    ]
    return {"id": "run-1", "steps": steps}


def _pick(run: dict):
    return _the_review(run, REVIEWS, str, lambda sid: {"id": sid})


@pytest.mark.parametrize(
    ("statuses", "picked"),
    [
        (("succeeded", "skipped"), REVIEW_CODEX),
        (("skipped", "succeeded"), REVIEW_QWEN),
        (("failed", "succeeded"), REVIEW_QWEN),
    ],
)
def test_the_review_is_the_one_step_that_succeeded(statuses, picked):
    state, definition = _pick(_states(*statuses))
    assert (state["key"], definition["id"]) == (picked, picked)


@pytest.mark.parametrize(
    "statuses",
    [("skipped", "skipped"), ("failed", "skipped"), (None, None), ("running", None)],
)
def test_no_review_step_that_succeeded_is_no_review(statuses):
    assert _pick(_states(*statuses)) == (None, {})


def test_two_review_steps_that_succeeded_fail_closed():
    with pytest.raises(ReviewError) as exc:
        _pick(_states("succeeded", "succeeded"))
    assert (exc.value.code, exc.value.detail) == (
        "review_invalid",
        "more than one review step reviewed this commit",
    )


# --------------------------------------------------------------------------- qwen reviewer


def test_qwen_is_a_reviewer_backend_and_its_read_only_is_plan_mode():
    assert REVIEWER_BACKENDS == frozenset({"codex", "qwen"})
    assert READ_ONLY_MODES == {"qwen": "plan"}


QWEN_READ_ONLY = {"sandbox": "read-only", "mode": "plan", "reviewer": True}


@pytest.mark.parametrize(
    ("harness", "params", "config"),
    [
        ("qwen", QWEN_READ_ONLY, None),
        ("Qwen ", QWEN_READ_ONLY, {"mode": "plan", "sandbox": "read-only"}),
        ("codex", {"sandbox": "read-only"}, None),
        ("codex", {"sandbox": "read-only", "mode": "yolo"}, None),  # mode is not codex's
    ],
)
def test_a_read_only_reviewer(harness, params, config):
    reviewer = {"harness": harness, "params": params}
    assert _read_only_reviewer(reviewer, {"config": config} if config else {}) is True


@pytest.mark.parametrize(
    ("params", "config"),
    [
        ({**QWEN_READ_ONLY, "mode": "yolo"}, None),
        ({**QWEN_READ_ONLY, "mode": None}, None),
        ({k: v for k, v in QWEN_READ_ONLY.items() if k != "mode"}, None),
        ({**QWEN_READ_ONLY, "sandbox": None}, None),  # plan mode alone is not enough
        (QWEN_READ_ONLY, {"mode": "yolo"}),
        (QWEN_READ_ONLY, {"mode": "auto-edit"}),
        (QWEN_READ_ONLY, {"sandbox": "workspace-write"}),
    ],
)
def test_a_qwen_reviewer_outside_plan_mode_is_not_read_only(params, config):
    reviewer = {"harness": "qwen", "params": params}
    assert _read_only_reviewer(reviewer, {"config": config} if config else {}) is False


def test_a_flagged_qwen_reviewer_not_in_plan_mode_is_refused():
    store = MemoryStore()
    _actors(store, {"mode": "yolo"}, "qwen")
    store.put("actors", {"id": "impl", "params": {}, "harness": "codex"})
    run = _run()
    run["steps"][0]["outputs"]["backend"] = "codex"
    assert _code(store, run, _review_state()) == ("reviewer_not_read_only", "rev is not read-only")


def test_an_actor_not_flagged_as_a_reviewer_is_refused_as_such_first():
    """The order (#39): allowed (flag and backend), then read-only. The qwen fixer placed
    as a reviewer is not a reviewer at all, whatever its mode."""
    store = MemoryStore()
    _actors(store, {"reviewer": None, "mode": "yolo", "sandbox": None}, "qwen")
    store.put("actors", {"id": "impl", "params": {}, "harness": "codex"})
    run = _run()
    run["steps"][0]["outputs"]["backend"] = "codex"
    code, _detail = _code(store, run, _review_state())
    assert code == "reviewer_not_allowed"


# --------------------------------------------------------------------------- the adapter


def qwen_reviewer(bridge: FakeBridge) -> BridgeAgentActor:
    return BridgeAgentActor(
        MemoryStore(),
        bridge_url="http://127.0.0.1:8093",
        callback_url=CALLBACK,
        token="t",
        resolve_secret=lambda ref: ref,
        defaults={"sandbox": "read-only", "mode": "plan"},
        transport=bridge,
        clock=Clock(),
        actor_id="test-qwen-reviewer",  # not pinned: these test the mode lock
    )


def ctx(config=None) -> InvocationContext:
    return InvocationContext("r", REVIEW_QWEN, "ai", "spark2", 1, "qwen-reviewer", config or {})


def test_a_plan_mode_actor_dispatches_in_plan_mode_and_read_only():
    for config in (None, {"mode": "plan"}):
        bridge = FakeBridge()
        res = qwen_reviewer(bridge).invoke(PR, "k", DEADLINE, context=ctx(config))
        assert res.outcome == "accepted"
        given = bridge.requests[0]["body"]["input"]
        assert (given["mode"], given["sandbox"]) == ("plan", "read-only")


@pytest.mark.parametrize("other", ["yolo", "auto-edit", "default", "", None])
def test_a_step_cannot_move_a_plan_mode_actor_to_another_mode(other):
    bridge = FakeBridge()
    res = qwen_reviewer(bridge).invoke(PR, "k", DEADLINE, context=ctx({"mode": other}))
    assert (res.outcome, res.retryable) == ("failed", False)
    assert res.error == f"mode_locked: the actor is plan; the step config asks for mode {other!r}"
    assert bridge.requests == []


def test_an_input_named_mode_cannot_move_it_either():
    bridge = FakeBridge()
    res = qwen_reviewer(bridge).invoke({**PR, "mode": "yolo"}, "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error.startswith("mode_locked: the actor is plan; an input asks for mode")
    assert bridge.requests == []


def test_other_actors_keep_their_step_level_mode():
    bridge = FakeBridge()
    actor = BridgeAgentActor(
        MemoryStore(),
        bridge_url="http://127.0.0.1:8093",
        callback_url=CALLBACK,
        token="t",
        resolve_secret=lambda ref: ref,
        defaults={"mode": "yolo"},
        transport=bridge,
        clock=Clock(),
        actor_id="test-qwen-fixer",
    )
    res = actor.invoke(PR, "k", DEADLINE, context=ctx({"mode": "auto-edit"}))
    assert res.outcome == "accepted"
    assert bridge.requests[0]["body"]["input"]["mode"] == "auto-edit"
