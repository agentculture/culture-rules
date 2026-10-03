"""Code runner actor: registered commands, admin-only sandboxed inline scripts."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.actors.code import (
    CodeRunner,
    CodeRunnerError,
    InlineScriptDenied,
    bind_argv,
    check_inline_allowed,
)
from culture_rules.actors.config import ActorConfig
from culture_rules.engine.actorport import ActorPort, InvocationContext

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DEADLINE = NOW + timedelta(minutes=5)


def ctx(identity: str = "alice", **cfg) -> InvocationContext:
    return InvocationContext(
        run_id="r1", step_id="s1", kind="step", host="h", config={"identity": identity, **cfg}
    )


COMMANDS = {
    "echo": {"argv": ["echo", "{msg}"], "params": {"msg": "string"}},
    "count": {
        "argv": [sys.executable, "-c", "import sys;print(len(sys.argv)-1)", "{n}", "{flag}"],
        "params": {"n": "int", "flag": "bool"},
    },
    "sleepy": {"argv": ["sleep", "5"], "timeout": 0.3},
    "fail": {"argv": ["sh", "-c", "echo oops >&2; exit 3"]},
}


def runner(admins=("root",), **kw) -> CodeRunner:
    return CodeRunner(COMMANDS, is_admin=lambda i: i in admins, **kw)


def test_is_actor_port():
    assert isinstance(runner(), ActorPort)


def test_from_actor_config_reads_commands():
    cfg = ActorConfig(key="r", kind="runner", extras={"commands": COMMANDS})
    r = CodeRunner.from_config(cfg, is_admin=lambda i: False)
    res = r.invoke({"command": "echo", "args": {"msg": "hi"}}, "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"


def test_registered_command_runs_with_bound_args():
    res = runner().invoke(
        {"command": "echo", "args": {"msg": "hello"}}, "k1", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["stdout"].strip() == "hello"
    assert res.output["exit_code"] == 0


def test_bind_argv_typed_and_never_shell_interpreted():
    argv = bind_argv(COMMANDS["echo"], {"msg": "$(rm -rf /); `x` | y"})
    assert argv == ["echo", "$(rm -rf /); `x` | y"]
    res = runner().invoke(
        {"command": "echo", "args": {"msg": "a; echo injected"}}, "k2", DEADLINE, context=ctx()
    )
    assert res.output["stdout"].strip() == "a; echo injected"


def test_typed_binding_coerces_and_rejects():
    assert bind_argv(COMMANDS["count"], {"n": 3, "flag": True}) == [
        sys.executable,
        "-c",
        "import sys;print(len(sys.argv)-1)",
        "3",
        "true",
    ]
    with pytest.raises(CodeRunnerError):
        bind_argv(COMMANDS["count"], {"n": "three", "flag": True})
    with pytest.raises(CodeRunnerError):
        bind_argv(COMMANDS["count"], {"n": True, "flag": True})  # bool is not an int
    with pytest.raises(CodeRunnerError):
        bind_argv(COMMANDS["echo"], {})  # missing
    with pytest.raises(CodeRunnerError):
        bind_argv(COMMANDS["echo"], {"msg": "x", "extra": 1})  # undeclared
    with pytest.raises(CodeRunnerError):
        bind_argv(COMMANDS["echo"], {"msg": "a\x00b"})


def test_never_uses_shell(monkeypatch):
    import subprocess

    seen = {}
    real = subprocess.Popen

    def spy(*a, **kw):
        seen.update(kw)
        seen["args"] = a[0] if a else kw.get("args")
        return real(*a, **kw)

    monkeypatch.setattr(subprocess, "Popen", spy)
    runner().invoke({"command": "echo", "args": {"msg": "x"}}, "k3", DEADLINE, context=ctx())
    assert not seen.get("shell")
    assert isinstance(seen["args"], list)


def test_unknown_command_fails_non_retryable():
    res = runner().invoke({"command": "nope"}, "k4", DEADLINE, context=ctx())
    assert res.outcome == "failed" and res.retryable is False


def test_nonzero_exit_is_failed_with_output():
    res = runner().invoke({"command": "fail"}, "k5", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert "3" in res.error


def test_command_timeout():
    res = runner().invoke({"command": "sleepy"}, "k6", DEADLINE, context=ctx())
    assert res.outcome == "failed" and "timeout" in res.error.lower()


def test_idempotent_on_key():
    calls = []
    r = CodeRunner(
        {"c": {"argv": ["echo", "{m}"], "params": {"m": "string"}}},
        is_admin=lambda i: False,
        on_run=calls.append,
    )
    a = r.invoke({"command": "c", "args": {"m": "1"}}, "same", DEADLINE, context=ctx())
    b = r.invoke({"command": "c", "args": {"m": "2"}}, "same", DEADLINE, context=ctx())
    assert len(calls) == 1
    assert a == b
    c = r.invoke({"command": "c", "args": {"m": "2"}}, "other", DEADLINE, context=ctx())
    assert c.output["stdout"].strip() == "2"


# --- inline scripts ----------------------------------------------------------------------


def test_check_inline_allowed_pure_function():
    check_inline_allowed("root", is_admin=lambda i: i == "root")
    with pytest.raises(InlineScriptDenied) as ei:
        check_inline_allowed("alice", is_admin=lambda i: i == "root")
    assert ei.value.code == "inline_script_admin_only"
    assert ei.value.identity == "alice"


def test_check_inline_allowed_step_only_when_it_has_script():
    from culture_rules.actors.code import check_step_inline_allowed

    nonadmin = lambda i: False  # noqa: E731
    check_step_inline_allowed("alice", {"command": "echo"}, is_admin=nonadmin)
    with pytest.raises(InlineScriptDenied):
        check_step_inline_allowed("alice", {"script": "echo hi"}, is_admin=nonadmin)
    check_step_inline_allowed("root", {"script": "echo hi"}, is_admin=lambda i: True)


def test_non_admin_cannot_run_inline_script():
    res = runner().invoke({"script": "echo hi"}, "i1", DEADLINE, context=ctx("alice"))
    assert res.outcome == "failed" and res.retryable is False
    assert "admin" in res.error


def test_admin_runs_inline_sh_script():
    res = runner().invoke(
        {"script": "echo inline-$1", "interpreter": "sh", "args": ["x"]},
        "i2",
        DEADLINE,
        context=ctx("root"),
    )
    assert res.outcome == "completed", res
    assert res.output["stdout"].strip() == "inline-x"


def test_admin_runs_inline_python_script():
    res = runner().invoke(
        {"script": "print(6*7)", "interpreter": "python"}, "i3", DEADLINE, context=ctx("root")
    )
    assert res.output["stdout"].strip() == "42"


def test_unknown_interpreter_rejected():
    res = runner().invoke(
        {"script": "x", "interpreter": "perl"}, "i4", DEADLINE, context=ctx("root")
    )
    assert res.outcome == "failed" and res.retryable is False


def test_inline_sandbox_tempdir_minimal_env_cleaned_up(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_TOKEN", "leak")
    script = "pwd; env; echo > marker.txt"
    res = runner(sandbox_root=tmp_path).invoke(
        {"script": script, "interpreter": "sh"}, "i5", DEADLINE, context=ctx("root")
    )
    out = res.output["stdout"]
    cwd = out.splitlines()[0]
    assert cwd.startswith(str(tmp_path.resolve())) or cwd.startswith(str(tmp_path))
    assert "SECRET_TOKEN" not in out
    assert not os.path.exists(cwd)  # removed
    assert list(tmp_path.iterdir()) == []


def test_inline_timeout_kills_and_cleans_up(tmp_path):
    res = runner(sandbox_root=tmp_path, inline_timeout=0.3).invoke(
        {"script": "sleep 5", "interpreter": "sh"}, "i6", DEADLINE, context=ctx("root")
    )
    assert res.outcome == "failed" and "timeout" in res.error.lower()
    assert list(tmp_path.iterdir()) == []


def test_inline_cleanup_on_failure(tmp_path):
    res = runner(sandbox_root=tmp_path).invoke(
        {"script": "exit 7", "interpreter": "sh"}, "i7", DEADLINE, context=ctx("root")
    )
    assert res.outcome == "failed"
    assert list(tmp_path.iterdir()) == []


def test_bind_argv_never_rescans_a_bound_value_for_placeholders():
    spec = {
        "argv": ["git", "checkout", "{branch}", "--", "{path}"],
        "params": {"branch": "string", "path": "string"},
    }
    argv = bind_argv(spec, {"branch": "{path}", "path": "--force"})
    assert argv == ["git", "checkout", "{path}", "--", "--force"]


def test_bind_argv_leaves_unknown_braces_literal():
    spec = {"argv": ["echo", "{msg}-{other}"], "params": {"msg": "string"}}
    assert bind_argv(spec, {"msg": "{msg}"}) == ["echo", "{msg}-{other}"]
