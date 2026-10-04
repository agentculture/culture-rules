"""machine.command action port: delegates to the bound runner actor's CodeRunner (t24)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.actors import code as code_mod
from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.machine import MachineCommandPort
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

HOSTILE = "; rm -rf / $(x) | `id` && echo pwned > /tmp/x"

COMMANDS = {
    "show": {"argv": ["printf", "[%s]\\n", "{msg}"], "params": {"msg": "string"}, "timeout": 10},
    "pair": {
        "argv": ["printf", "%s|%s\\n", "{a}", "n={b}"],
        "params": {"a": "string", "b": "int"},
    },
}


def _store(kind: str = "runner", enabled: bool = True) -> MemoryStore:
    store = MemoryStore()
    store.put(
        ACTORS_COLLECTION,
        {
            "id": "r1",
            "name": "runner one",
            "kind": kind,
            "enabled": enabled,
            "params": {"commands": COMMANDS},
        },
    )
    return store


def _ctx(actor: str | None = "r1") -> InvocationContext:
    return InvocationContext(
        run_id="run1",
        step_id="action",
        kind="action",
        host="h1",
        actor=actor,
        config={"kind": "machine.command"},
    )


def _deadline() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=30)


def _invoke(port, params, key="k1", actor="r1"):
    return port.invoke(params, key, _deadline(), context=_ctx(actor))


@pytest.fixture
def captured(monkeypatch):
    """Capture the argv CodeRunner would start, without starting anything."""
    seen: list[list[str]] = []

    def fake_run(argv, cwd, timeout):
        seen.append(list(argv))
        return 0, "ok\n", ""

    monkeypatch.setattr(code_mod, "_run", fake_run)
    return seen


def test_shell_metacharacters_reach_argv_as_one_typed_argument(captured) -> None:
    port = MachineCommandPort(_store())
    res = _invoke(port, {"actor": "r1", "command": "show", "args": {"msg": HOSTILE}})
    assert res.outcome == COMPLETED
    assert captured == [["printf", "[%s]\\n", HOSTILE]]


def test_typed_mapping_args_bind_into_the_template(captured) -> None:
    port = MachineCommandPort(_store())
    res = _invoke(port, {"actor": "r1", "command": "pair", "args": {"a": HOSTILE, "b": 7}})
    assert res.outcome == COMPLETED
    assert captured == [["printf", "%s|%s\\n", HOSTILE, "n=7"]]


@pytest.mark.parametrize("args", [[HOSTILE, 7], HOSTILE, 7])
def test_non_mapping_args_are_refused(captured, args) -> None:
    port = MachineCommandPort(_store())
    res = _invoke(port, {"actor": "r1", "command": "pair", "args": args})
    assert res.outcome == FAILED and res.retryable is False
    assert captured == []


def test_missing_or_undeclared_args_are_refused(captured) -> None:
    port = MachineCommandPort(_store())
    res = _invoke(port, {"actor": "r1", "command": "pair", "args": {"a": "x"}})
    assert res.outcome == FAILED and res.retryable is False
    res = _invoke(port, {"actor": "r1", "command": "pair", "args": {"a": "x", "b": 1, "c": 2}})
    assert res.outcome == FAILED and res.retryable is False
    assert captured == []


def test_type_mismatch_is_refused_without_running(captured) -> None:
    port = MachineCommandPort(_store())
    res = _invoke(port, {"actor": "r1", "command": "pair", "args": {"a": "x", "b": "7; ls"}})
    assert res.outcome == FAILED and res.retryable is False
    assert captured == []


@pytest.mark.skipif(shutil.which("printf") is None, reason="needs printf")
def test_hostile_payload_is_inert_when_really_executed() -> None:
    port = MachineCommandPort(_store())
    res = _invoke(port, {"actor": "r1", "command": "show", "args": {"msg": HOSTILE}})
    assert res.outcome == COMPLETED, res.error
    assert res.output["stdout"] == f"[{HOSTILE}]\n"
    assert res.output["exit_code"] == 0


def test_wrong_actor_kind_is_refused(captured) -> None:
    port = MachineCommandPort(_store(kind="agent"))
    res = _invoke(port, {"actor": "r1", "command": "show", "args": {"msg": "hi"}})
    assert res.outcome == FAILED and res.retryable is False
    assert res.error.startswith("actor_kind_mismatch")
    assert captured == []


def test_unknown_or_disabled_actor_is_refused(captured) -> None:
    port = MachineCommandPort(MemoryStore())
    res = _invoke(port, {"actor": "r1", "command": "show", "args": {"msg": "hi"}})
    assert res.outcome == FAILED and res.retryable is False
    assert res.error.startswith("actor_not_found")
    port = MachineCommandPort(_store(enabled=False))
    res = _invoke(port, {"actor": "r1", "command": "show", "args": {"msg": "hi"}})
    assert res.outcome == FAILED and res.error.startswith("actor_disabled")
    assert captured == []


def test_context_actor_is_used_when_params_omit_it(captured) -> None:
    port = MachineCommandPort(_store())
    res = _invoke(port, {"command": "show", "args": {"msg": "hi"}})
    assert res.outcome == COMPLETED
    res = _invoke(port, {"command": "show", "args": {"msg": "hi"}}, key="k2", actor=None)
    assert res.outcome == FAILED and res.error.startswith("actor_missing")


def test_unregistered_command_and_inline_script_are_refused(captured) -> None:
    port = MachineCommandPort(_store())
    res = _invoke(port, {"actor": "r1", "command": "rm", "args": {}})
    assert res.outcome == FAILED and res.retryable is False
    res = _invoke(
        port, {"actor": "r1", "command": "show", "args": {"msg": "x"}, "script": "id"}, key="k3"
    )
    # The inline ``script`` param is never forwarded: only the registered command runs.
    assert res.outcome == COMPLETED
    assert captured == [["printf", "[%s]\\n", "x"]]


def test_completed_result_replays_for_the_same_key(captured) -> None:
    port = MachineCommandPort(_store())
    params = {"actor": "r1", "command": "show", "args": {"msg": "once"}}
    first = _invoke(port, params)
    second = _invoke(port, params)
    assert first == second and len(captured) == 1
    assert port.supports_idempotency_key is False
