"""jira-devague t7: a bridge step may hand an operating agent one writable ``working_path``
and ask for the cultureagent ``operate`` sandbox (the role t8 adds); the read-only lock still
holds, and a bad path is refused before the bridge is called.

No network: the bridge is the :class:`FakeBridge` transport double.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from culture_rules.actors.agent import (
    BRIDGE_CORE_INPUTS,
    OPERATE_SANDBOX,
    WORKING_PATH,
    bound_input_chars,
)
from culture_rules.engine.actorport import InvocationContext
from culture_rules.store.memory import MemoryStore
from tests.actors.test_bridge_agent import PR, FakeBridge, make_actor
from tests.engine.run_helpers import T0, Clock

PATH = "/srv/devague/frames/ABC-1"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def invoke(store, clock, input, *, config=None, defaults=None):
    """One attempt against a fake bridge: ``(result, bridge)``."""
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge, defaults=defaults or {})
    ctx = InvocationContext("r", "s", "actor_task", "spark", 1, None, dict(config or {}))
    return actor.invoke(input, "k", T0 + timedelta(hours=1), context=ctx), bridge


def sent(bridge: FakeBridge) -> dict:
    (req,) = bridge.requests
    return req["body"]["input"]


# ---- the contract names shared with cultureagent (t8) ----


def test_the_wire_names_are_the_cross_repo_contract():
    assert OPERATE_SANDBOX == "operate"
    assert WORKING_PATH == "working_path"
    # the bridge reads working_path itself: it is never appended to the prompt as a bound input
    assert WORKING_PATH in BRIDGE_CORE_INPUTS


# ---- working_path: an absolute path is forwarded, anything else refused before any call ----


def test_an_absolute_working_path_input_is_forwarded_in_the_bridge_input(store, clock):
    res, bridge = invoke(store, clock, {**PR, WORKING_PATH: PATH})
    assert res.outcome == "accepted"
    body = sent(bridge)
    assert body[WORKING_PATH] == PATH
    assert body["repo"] == PR["repo"] and body["head_sha"] == PR["head_sha"]
    assert bound_input_chars(body) == 0  # not counted against max_bound_input_chars


def test_a_working_path_in_the_step_config_is_forwarded_too(store, clock):
    res, bridge = invoke(store, clock, PR, config={WORKING_PATH: PATH})
    assert res.outcome == "accepted"
    assert sent(bridge)[WORKING_PATH] == PATH


def test_the_input_working_path_wins_over_the_step_config(store, clock):
    res, bridge = invoke(store, clock, {**PR, WORKING_PATH: PATH}, config={WORKING_PATH: "/other"})
    assert res.outcome == "accepted"
    assert sent(bridge)[WORKING_PATH] == PATH


@pytest.mark.parametrize(
    "bad",
    [
        "devague/frames/ABC-1",  # relative
        "./ABC-1",
        "~/frames",
        "",
        "   ",
        " /srv/x",  # not stripped: it is a path, not prose
        "/srv/devague/../../etc",  # a '..' segment
        "/srv/x\0y",
        42,
        ["/srv/x"],
        {"path": "/srv/x"},
        True,
    ],
)
def test_a_relative_or_non_string_working_path_is_refused_before_any_call(store, clock, bad):
    res, bridge = invoke(store, clock, {**PR, WORKING_PATH: bad})
    assert res.outcome == "failed"
    assert res.retryable is False
    assert res.error.startswith("working_path_invalid:")
    assert bridge.requests == []


def test_a_bad_working_path_in_the_step_config_is_refused_too(store, clock):
    res, bridge = invoke(store, clock, PR, config={WORKING_PATH: "relative/dir"})
    assert res.outcome == "failed"
    assert res.error.startswith("working_path_invalid:")
    assert bridge.requests == []


def test_a_step_without_a_working_path_sends_none(store, clock):
    res, bridge = invoke(store, clock, PR)
    assert res.outcome == "accepted"
    assert WORKING_PATH not in sent(bridge)


# ---- the read-only lock still holds ----


@pytest.mark.parametrize("where", ["input", "config"])
def test_a_read_only_actor_refuses_sandbox_operate(store, clock, where):
    ask = {"sandbox": OPERATE_SANDBOX}
    input = {**PR, **ask} if where == "input" else PR
    config = ask if where == "config" else {}
    res, bridge = invoke(store, clock, input, config=config, defaults={"sandbox": "read-only"})
    assert res.outcome == "failed"
    assert res.retryable is False
    assert res.error.startswith("sandbox_locked: the actor is read-only")
    assert bridge.requests == []


@pytest.mark.parametrize("where", ["input", "config"])
def test_a_read_only_actor_refuses_a_working_path(store, clock, where):
    """A writable directory is what ``operate`` adds: a read-only actor never gets one,
    even with no sandbox asked (the lock would keep ``read-only`` on the wire, but a step
    handing a writable path to a read-only agent is a mistake, refused rather than ignored)."""
    input = {**PR, WORKING_PATH: PATH} if where == "input" else PR
    config = {WORKING_PATH: PATH} if where == "config" else {}
    res, bridge = invoke(store, clock, input, config=config, defaults={"sandbox": "read-only"})
    assert res.outcome == "failed"
    assert res.error.startswith("sandbox_locked: the actor is read-only")
    assert bridge.requests == []


def test_a_read_only_actor_still_dispatches_without_either(store, clock):
    res, bridge = invoke(store, clock, PR, defaults={"sandbox": "read-only"})
    assert res.outcome == "accepted"
    body = sent(bridge)
    assert body["sandbox"] == "read-only"
    assert WORKING_PATH not in body


# ---- an actor allowing operate forwards it ----


def test_an_actor_whose_sandbox_default_is_operate_forwards_it(store, clock):
    """The opt-in is the actor's ``sandbox`` default (``params.sandbox``), the way the
    read-only lock is configured: every step of such an actor runs in ``operate``."""
    res, bridge = invoke(store, clock, {**PR, WORKING_PATH: PATH}, defaults={"sandbox": "operate"})
    assert res.outcome == "accepted"
    body = sent(bridge)
    assert body["sandbox"] == OPERATE_SANDBOX
    assert body[WORKING_PATH] == PATH


def test_an_unpinned_actor_forwards_a_step_asking_for_operate(store, clock):
    """Today's behaviour kept: an actor pinning no sandbox takes the step's (as it does
    ``workspace-write``)."""
    res, bridge = invoke(
        store, clock, {**PR, WORKING_PATH: PATH}, config={"sandbox": OPERATE_SANDBOX}
    )
    assert res.outcome == "accepted"
    body = sent(bridge)
    assert body["sandbox"] == OPERATE_SANDBOX
    assert body[WORKING_PATH] == PATH


def test_an_operate_actor_may_run_a_step_without_a_working_path(store, clock):
    """Which directory (if any) is the bridge's business: the step may name none."""
    res, bridge = invoke(store, clock, PR, defaults={"sandbox": "operate"})
    assert res.outcome == "accepted"
    assert sent(bridge)["sandbox"] == OPERATE_SANDBOX
