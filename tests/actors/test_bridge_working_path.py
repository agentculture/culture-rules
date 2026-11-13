"""A bridge step may hand an operating agent one writable ``working_path``
under the cultureagent ``operate`` sandbox (the role t8 adds). ``operate`` is an actor's own
opt-in (``params.sandbox: operate``), pinned like ``read-only``: no step can raise, lower or
switch an actor's sandbox to or from it, and a ``working_path`` exists only under it. A bad
path is refused before the bridge is called.

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

PATH = "/srv/work/frames/ABC-1"
OPERATE = {"sandbox": OPERATE_SANDBOX}
READ_ONLY = {"sandbox": "read-only"}
WORKSPACE_WRITE = {"sandbox": "workspace-write"}
UNPINNED: dict = {}
NOT_OPERATE = [
    pytest.param(UNPINNED, id="unpinned"),
    pytest.param(WORKSPACE_WRITE, id="workspace-write"),
    pytest.param(READ_ONLY, id="read-only"),
    pytest.param({"mode": "plan"}, id="plan-mode-only"),
]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def invoke(store, clock, input, *, config=None, defaults=None):
    """One attempt against a fake bridge: ``(result, bridge)``."""
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge, defaults=dict(defaults or {}))
    ctx = InvocationContext("r", "s", "actor_task", "spark", 1, None, dict(config or {}))
    return actor.invoke(input, "k", T0 + timedelta(hours=1), context=ctx), bridge


def sent(bridge: FakeBridge) -> dict:
    (req,) = bridge.requests
    return req["body"]["input"]


def refused(res, bridge, prefix: str) -> None:
    assert res.outcome == "failed"
    assert res.retryable is False
    assert res.error.startswith(prefix), res.error
    assert bridge.requests == []  # refused before any request


# ---- the contract names shared with cultureagent (t8) ----


def test_the_wire_names_are_the_cross_repo_contract():
    assert OPERATE_SANDBOX == "operate"
    assert WORKING_PATH == "working_path"
    # the bridge reads working_path itself: it is never appended to the prompt as a bound input
    assert WORKING_PATH in BRIDGE_CORE_INPUTS


# ---- an operate actor: working_path forwarded, anything but an absolute path refused ----


def test_an_absolute_working_path_input_is_forwarded_in_the_bridge_input(store, clock):
    res, bridge = invoke(store, clock, {**PR, WORKING_PATH: PATH}, defaults=OPERATE)
    assert res.outcome == "accepted"
    body = sent(bridge)
    assert body[WORKING_PATH] == PATH
    assert body["sandbox"] == OPERATE_SANDBOX
    assert body["repo"] == PR["repo"]
    assert body["head_sha"] == PR["head_sha"]
    assert bound_input_chars(body) == 0  # not counted against max_bound_input_chars


def test_a_working_path_in_the_step_config_is_forwarded_too(store, clock):
    res, bridge = invoke(store, clock, PR, config={WORKING_PATH: PATH}, defaults=OPERATE)
    assert res.outcome == "accepted"
    assert sent(bridge)[WORKING_PATH] == PATH


def test_the_input_working_path_wins_over_the_step_config(store, clock):
    res, bridge = invoke(
        store, clock, {**PR, WORKING_PATH: PATH}, config={WORKING_PATH: "/other"}, defaults=OPERATE
    )
    assert res.outcome == "accepted"
    assert sent(bridge)[WORKING_PATH] == PATH


def test_a_step_asking_for_operate_on_an_operate_actor_is_fine(store, clock):
    """Asking for what the actor is pinned to is not a change."""
    res, bridge = invoke(store, clock, {**PR, **OPERATE}, config=OPERATE, defaults=OPERATE)
    assert res.outcome == "accepted"
    assert sent(bridge)["sandbox"] == OPERATE_SANDBOX


def test_an_operate_actor_may_run_a_step_without_a_working_path(store, clock):
    """Which directory (if any) is the bridge's business: the step may name none."""
    res, bridge = invoke(store, clock, PR, defaults=OPERATE)
    assert res.outcome == "accepted"
    body = sent(bridge)
    assert body["sandbox"] == OPERATE_SANDBOX
    assert WORKING_PATH not in body


@pytest.mark.parametrize(
    "bad",
    [
        "work/frames/ABC-1",  # relative
        "./ABC-1",
        "~/frames",
        "",
        "   ",
        " /srv/x",  # not stripped: it is a path, not prose
        "/srv/work/../../etc",  # a '..' segment
        "/srv/x\0y",
        "/srv/x\\y",  # a backslash: never a POSIX path the bridge should see
        "C:\\frames",
        42,
        ["/srv/x"],
        {"path": "/srv/x"},
        True,
    ],
)
def test_a_relative_or_non_string_working_path_is_refused_before_any_call(store, clock, bad):
    res, bridge = invoke(store, clock, {**PR, WORKING_PATH: bad}, defaults=OPERATE)
    refused(res, bridge, "working_path_invalid:")


def test_a_bad_working_path_in_the_step_config_is_refused_too(store, clock):
    res, bridge = invoke(store, clock, PR, config={WORKING_PATH: "relative/dir"}, defaults=OPERATE)
    refused(res, bridge, "working_path_invalid:")


# ---- operate is the actor's own opt-in: no step raises another actor to it ----


@pytest.mark.parametrize("defaults", NOT_OPERATE)
@pytest.mark.parametrize("where", ["input", "config"])
def test_a_step_cannot_raise_another_actor_to_operate(store, clock, defaults, where):
    """Unpinned, workspace-write, read-only, or fenced by mode ``plan`` only: a step's
    ``sandbox: operate`` is refused and the message names the opt-in."""
    input = {**PR, **OPERATE} if where == "input" else PR
    config = OPERATE if where == "config" else {}
    res, bridge = invoke(store, clock, input, config=config, defaults=defaults)
    refused(res, bridge, "sandbox_locked:")
    assert "params.sandbox: operate" in res.error


def test_a_read_only_actor_refuses_sandbox_operate_as_read_only(store, clock):
    res, bridge = invoke(store, clock, PR, config=OPERATE, defaults=READ_ONLY)
    refused(res, bridge, "sandbox_locked: the actor is read-only")


@pytest.mark.parametrize("ask", ["workspace-write", "read-only", "danger-full-access"])
@pytest.mark.parametrize("where", ["input", "config"])
def test_an_operate_actor_cannot_be_switched_to_another_sandbox(store, clock, ask, where):
    """Pinned like read-only: an operate actor's steps neither widen nor narrow it."""
    input = {**PR, "sandbox": ask} if where == "input" else PR
    config = {"sandbox": ask} if where == "config" else {}
    res, bridge = invoke(store, clock, input, config=config, defaults=OPERATE)
    refused(res, bridge, "sandbox_locked: the actor is operate")


def test_an_unpinned_actor_still_takes_other_sandboxes_from_a_step(store, clock):
    """Today's behaviour for everything but operate is kept."""
    res, bridge = invoke(store, clock, PR, config=WORKSPACE_WRITE)
    assert res.outcome == "accepted"
    assert sent(bridge)["sandbox"] == "workspace-write"


# ---- a working_path exists only under operate ----


@pytest.mark.parametrize("defaults", NOT_OPERATE[:2] + NOT_OPERATE[3:])
@pytest.mark.parametrize("where", ["input", "config"])
def test_a_working_path_is_refused_for_any_sandbox_but_operate(store, clock, defaults, where):
    """The cultureagent bridge refuses a working_path on other roles; so does the step,
    before the call."""
    input = {**PR, WORKING_PATH: PATH} if where == "input" else PR
    config = {WORKING_PATH: PATH} if where == "config" else {}
    res, bridge = invoke(store, clock, input, config=config, defaults=defaults)
    refused(res, bridge, "working_path_invalid: only for sandbox operate")


def test_a_working_path_with_a_step_asking_workspace_write_is_refused(store, clock):
    res, bridge = invoke(
        store, clock, {**PR, WORKING_PATH: PATH}, config=WORKSPACE_WRITE, defaults=UNPINNED
    )
    refused(res, bridge, "working_path_invalid: only for sandbox operate")


@pytest.mark.parametrize("where", ["input", "config"])
def test_a_read_only_actor_refuses_a_working_path(store, clock, where):
    """A writable directory is what ``operate`` adds: a read-only actor never gets one,
    and the reason is its lock."""
    input = {**PR, WORKING_PATH: PATH} if where == "input" else PR
    config = {WORKING_PATH: PATH} if where == "config" else {}
    res, bridge = invoke(store, clock, input, config=config, defaults=READ_ONLY)
    refused(res, bridge, "sandbox_locked: the actor is read-only")


def test_a_read_only_actor_still_dispatches_without_either(store, clock):
    res, bridge = invoke(store, clock, PR, defaults=READ_ONLY)
    assert res.outcome == "accepted"
    body = sent(bridge)
    assert body["sandbox"] == "read-only"
    assert WORKING_PATH not in body


def test_a_step_without_a_working_path_sends_none(store, clock):
    res, bridge = invoke(store, clock, PR)
    assert res.outcome == "accepted"
    body = sent(bridge)
    assert WORKING_PATH not in body
    assert "sandbox" not in body


@pytest.mark.parametrize(
    "bad",
    [
        "/srv/work/",  # trailing slash: not what normpath gives, the bridge refuses it
        "/srv/./work",
        "/srv//work",
        "//srv/work",
        "/srv/work/.",
        "/srv/a,b",  # a comma would split the bridge's permission rules
        "/srv/a(b",
        "/srv/a)b",
        "/srv/a*",
        "/srv/a?",
        "/srv/a[1]",
        "/srv/a{b}",
        "/srv/a b",
        "/srv/a\tb",
        "/srv/a\nb",
    ],
)
def test_an_unnormalised_or_rule_metacharacter_working_path_is_refused(store, clock, bad):
    res, bridge = invoke(store, clock, {**PR, WORKING_PATH: bad}, defaults=OPERATE)
    refused(res, bridge, "working_path_invalid:")
