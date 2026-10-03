"""Code runner actor: registered commands, plus admin-only sandboxed inline scripts.

A *runner* actor's config carries a registry of named commands (``extras["commands"]``)::

    {"echo": {"argv": ["echo", "{msg}"], "params": {"msg": "string"}, "timeout": 30}}

A step invokes one by name with typed arguments (``{"command": "echo", "args": {...}}``).
Arguments are validated against the declared param types and substituted into the argv
list as whole-or-embedded string values; the process is started with an argv list and
``shell=False`` always, so argument text is never interpreted by a shell.

Inline script text (``{"script": "...", "interpreter": "sh"|"python"}``) is admin-only.
:func:`check_inline_allowed` / :func:`check_step_inline_allowed` are the pure save-side
rules the API calls; :meth:`CodeRunner.invoke` re-checks at run time (defence in depth).
An ``is_admin(identity)`` callable is injected until the principal type lands. The
invoking identity is read from ``context.config["identity"]``.

Inline scripts run in a fresh temporary directory (cwd), with a minimal environment and a
timeout, in their own process group (killed on timeout), and the directory is always
removed. Registered commands run in the same kind of directory so they cannot litter the
runner host. This is process-level containment, not a security boundary against a
malicious admin. Invocation is idempotent on the idempotency key (in-memory ledger; the
runner host owns it). The attempt ``deadline`` is advisory; the command/inline timeout
bounds the run. Standard-library only.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess  # nosec B404 - argv lists only, shell=False
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from culture_rules.actors.config import ActorConfig
from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = [
    "CodeRunner",
    "CodeRunnerError",
    "InlineScriptDenied",
    "bind_argv",
    "check_inline_allowed",
    "check_step_inline_allowed",
]

DEFAULT_TIMEOUT = 60.0
DEFAULT_INLINE_TIMEOUT = 30.0
_MINIMAL_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
_INTERPRETERS: dict[str, list[str]] = {
    "sh": ["/bin/sh"],
    "python": [sys.executable, "-I"],
}
_MAX_OUTPUT = 1_000_000
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class CodeRunnerError(ValueError):
    """A command could not be resolved or its arguments could not be bound."""


class InlineScriptDenied(PermissionError):
    """A non-admin tried to save or run inline script text."""

    code = "inline_script_admin_only"

    def __init__(self, identity: str):
        super().__init__(f"inline script text is admin-only; {identity!r} is not an admin")
        self.identity = identity

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "identity": self.identity, "message": str(self)}


def check_inline_allowed(identity: str, *, is_admin: Callable[[str], bool]) -> None:
    """Raise :class:`InlineScriptDenied` unless ``identity`` is an admin."""
    if not isinstance(identity, str) or not identity.strip() or not is_admin(identity):
        raise InlineScriptDenied(str(identity))


def check_step_inline_allowed(
    identity: str, step: Mapping[str, Any], *, is_admin: Callable[[str], bool]
) -> None:
    """Save-side rule: a step carrying inline ``script`` text requires an admin."""
    if "script" in step and step["script"] is not None:
        check_inline_allowed(identity, is_admin=is_admin)


def _coerce(name: str, kind: str, value: Any) -> str:
    if kind == "string":
        if not isinstance(value, str):
            raise CodeRunnerError(f"argument {name!r} must be a string")
        text = value
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise CodeRunnerError(f"argument {name!r} must be an int")
        text = str(value)
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise CodeRunnerError(f"argument {name!r} must be a number")
        text = repr(value)
    elif kind == "bool":
        if not isinstance(value, bool):
            raise CodeRunnerError(f"argument {name!r} must be a bool")
        text = "true" if value else "false"
    else:
        raise CodeRunnerError(f"argument {name!r} has unknown type {kind!r}")
    if "\x00" in text:
        raise CodeRunnerError(f"argument {name!r} contains a NUL byte")
    return text


def bind_argv(spec: Mapping[str, Any], args: Mapping[str, Any] | None) -> list[str]:
    """Bind typed ``args`` into the spec's argv template; returns the argv list."""
    template = spec.get("argv")
    if not isinstance(template, list | tuple) or not template:
        raise CodeRunnerError("command has no argv template")
    params: Mapping[str, str] = spec.get("params") or {}
    args = dict(args or {})
    extra = sorted(set(args) - set(params))
    if extra:
        raise CodeRunnerError(f"undeclared arguments: {', '.join(extra)}")
    missing = sorted(set(params) - set(args))
    if missing:
        raise CodeRunnerError(f"missing arguments: {', '.join(missing)}")
    bound = {name: _coerce(name, params[name], args[name]) for name in params}
    # One pass per template part: a bound value is inserted verbatim and never re-scanned,
    # so a value that looks like a placeholder ("{path}") cannot pull in another argument.
    return [
        _PLACEHOLDER.sub(lambda m: bound.get(m.group(1), m.group(0)), str(part))
        for part in template
    ]


def _run(argv: list[str], cwd: str, timeout: float) -> tuple[int | None, str, str]:
    """Run ``argv`` (never via a shell); returns (exit code or None on timeout, out, err)."""
    proc = subprocess.Popen(  # nosec B603 - argv list, shell=False
        argv,
        cwd=cwd,
        env=dict(_MINIMAL_ENV),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return None, "", ""
    return proc.returncode, out[:_MAX_OUTPUT], err[:_MAX_OUTPUT]


class CodeRunner:
    """ActorPort adapter for a runner actor (see the module docstring)."""

    supports_idempotency_key = True

    def __init__(
        self,
        commands: Mapping[str, Mapping[str, Any]],
        *,
        is_admin: Callable[[str], bool],
        sandbox_root: str | Path | None = None,
        inline_timeout: float = DEFAULT_INLINE_TIMEOUT,
        on_run: Callable[[str], None] | None = None,
    ):
        self._commands = dict(commands)
        self._is_admin = is_admin
        self._sandbox_root = str(sandbox_root) if sandbox_root else None
        self._inline_timeout = inline_timeout
        self._on_run = on_run
        self._ledger: dict[str, InvocationResult] = {}

    @classmethod
    def from_config(cls, config: ActorConfig, **kw: Any) -> CodeRunner:
        return cls(config.extras.get("commands") or {}, **kw)

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        if idempotency_key in self._ledger:
            return self._ledger[idempotency_key]
        result = self._execute(input, context)
        self._ledger[idempotency_key] = result
        return result

    def _execute(self, input: Mapping[str, Any], context: InvocationContext) -> InvocationResult:
        try:
            if input.get("script") is not None:
                identity = context.config.get("identity", "")
                check_inline_allowed(identity, is_admin=self._is_admin)
                return self._run_inline(input)
            return self._run_registered(input)
        except InlineScriptDenied as exc:
            return InvocationResult.failed(str(exc), retryable=False)
        except CodeRunnerError as exc:
            return InvocationResult.failed(str(exc), retryable=False)

    def _run_registered(self, input: Mapping[str, Any]) -> InvocationResult:
        name = input.get("command")
        spec = self._commands.get(name) if isinstance(name, str) else None
        if spec is None:
            raise CodeRunnerError(f"unknown command {name!r}")
        argv = bind_argv(spec, input.get("args"))
        return self._sandboxed(argv, float(spec.get("timeout", DEFAULT_TIMEOUT)))

    def _run_inline(self, input: Mapping[str, Any]) -> InvocationResult:
        interpreter = input.get("interpreter", "sh")
        base = _INTERPRETERS.get(interpreter)
        if base is None:
            raise CodeRunnerError(f"unknown interpreter {interpreter!r}")
        args = input.get("args") or []
        if not isinstance(args, list | tuple) or not all(isinstance(a, str) for a in args):
            raise CodeRunnerError("inline args must be a list of strings")
        script = input["script"]
        if not isinstance(script, str):
            raise CodeRunnerError("script must be text")
        return self._sandboxed(
            lambda workdir: [*base, str(Path(workdir) / "script"), *args],
            self._inline_timeout,
            script=script,
        )

    def _sandboxed(
        self,
        argv: list[str] | Callable[[str], list[str]],
        timeout: float,
        *,
        script: str | None = None,
    ) -> InvocationResult:
        workdir = tempfile.mkdtemp(prefix="culture-run-", dir=self._sandbox_root)
        try:
            if script is not None:
                (Path(workdir) / "script").write_text(script, encoding="utf-8")
            if callable(argv):
                argv = argv(workdir)
            if self._on_run:
                self._on_run(" ".join(argv))
            try:
                code, out, err = _run(argv, workdir, timeout)
            except OSError as exc:
                return InvocationResult.failed(f"cannot start command: {exc}", retryable=False)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if code is None:
            return InvocationResult.failed(f"timeout after {timeout}s")
        if code != 0:
            return InvocationResult.failed(f"exit code {code}: {err.strip()[:500]}")
        return InvocationResult.completed({"stdout": out, "stderr": err, "exit_code": code})
