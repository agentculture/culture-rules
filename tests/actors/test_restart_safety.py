"""Adapters whose dedupe ledger is per-process must not claim cross-process idempotency (#8).

ColleagueActor, MeshAgentActor and CodeRunner remember results only in memory, so after
the engine dies mid-invoke a fresh process cannot deduplicate. They declare
``supports_idempotency_key = False``, which makes the executor refuse a resumed attempt
whose outcome is unknown (``unsafe_retry``) instead of running the side effect again,
while ordinary runs and retries after a definite failure still work.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from culture_rules.actors.agent import ColleagueActor, MeshAgentActor
from culture_rules.actors.code import CodeRunner
from culture_rules.engine.runs import Executor, step_state
from culture_rules.model.common import RetryPolicy
from culture_rules.model.workflow import Edge, Port
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, Crash, FakeActor, rule, step, workflow


class DiesAfter:
    """The engine process dies right after the adapter returned (before the result is stored)."""

    def __init__(self, inner):
        self.inner = inner
        self.supports_idempotency_key = inner.supports_idempotency_key

    def invoke(self, *args, **kwargs):
        self.inner.invoke(*args, **kwargs)
        raise Crash("engine killed")


def _command_workflow(retry: RetryPolicy | None = None):
    wf = workflow((step("s1", "code", config={}, retry=retry),))
    command = Port(name="command", type="string")
    return replace(
        wf,
        inputs=(command,),
        steps=(replace(wf.steps[0], inputs=(command,)),),
        edges=(Edge(source="inputs", source_port="command", target="s1", target_port="command"),),
    )


def _runner(commands, started):
    return CodeRunner(commands, is_admin=lambda _i: False, on_run=started.append)


@pytest.mark.parametrize(
    "adapter",
    [
        ColleagueActor(repo="/r"),
        MeshAgentActor(client=object()),
        CodeRunner({}, is_admin=lambda _i: False),
    ],
    ids=["colleague", "mesh", "code"],
)
def test_in_memory_ledgers_do_not_claim_cross_process_idempotency(adapter):
    assert adapter.supports_idempotency_key is False


def test_crash_after_the_effect_resumes_as_unsafe_retry_with_one_process():
    clock = Clock()
    store = MemoryStore(clock=clock)
    started: list[str] = []
    commands = {"deploy": {"argv": ["true"]}}
    first = Executor(
        store,
        "spark",
        {"code": DiesAfter(_runner(commands, started)), "action": FakeActor()},
        clock=clock,
    )
    run = first.start(
        rule(workflow_inputs={"command": "trigger.cmd"}),
        _command_workflow(),
        trigger={"cmd": "deploy"},
    )
    with pytest.raises(Crash):
        first.run_until_idle()
    assert len(started) == 1

    clock.advance(31)  # the lease lapses; a fresh process has a fresh adapter ledger
    second = Executor(
        store, "spark", {"code": _runner(commands, started), "action": FakeActor()}, clock=clock
    )
    second.run_until_idle()
    st = step_state(second.run(run["id"]), "s1")
    assert st["status"] == "failed"
    assert st["error"]["code"] == "unsafe_retry"
    assert len(started) == 1


def test_single_attempt_run_still_succeeds():
    clock = Clock()
    store = MemoryStore(clock=clock)
    started: list[str] = []
    ex = Executor(
        store,
        "spark",
        {"code": _runner({"ok": {"argv": ["true"]}}, started), "action": FakeActor()},
        clock=clock,
    )
    run = ex.start(
        rule(workflow_inputs={"command": "trigger.cmd"}), _command_workflow(), trigger={"cmd": "ok"}
    )
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    assert len(started) == 1


def test_retry_after_a_definite_failure_runs_again(tmp_path):
    marker = tmp_path / "attempted"
    commands = {"flaky": {"argv": ["test", "-e", str(marker)]}}  # fails until the marker exists
    clock = Clock()
    store = MemoryStore(clock=clock)
    started: list[str] = []
    runner = _runner(commands, started)
    ex = Executor(store, "spark", {"code": runner, "action": FakeActor()}, clock=clock)
    run = ex.start(
        rule(workflow_inputs={"command": "trigger.cmd"}),
        _command_workflow(RetryPolicy(max_attempts=3, backoff_s=1)),
        trigger={"cmd": "flaky"},
    )
    ex.run_until_idle()
    st = step_state(ex.run(run["id"]), "s1")
    assert st["status"] == "retry_wait"
    assert st["error"]["code"] == "actor_failed"
    marker.touch()
    clock.advance(2)
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    assert len(started) == 2
