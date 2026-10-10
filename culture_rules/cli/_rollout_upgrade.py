"""``culture-rules rollout upgrade --host NAME --wheel PATH``: upgrade one host's engine node.

The host's machine record (``GET /machines/{name}``) says how, in its ``install`` section:

* ``install_sh`` (thor, orin, spark2): over ssh and scp (``-o BatchMode=yes``), make
  ``culture-rules-deploy-<version>/`` in the remote home, copy the named wheel and this
  wheel's ``install.sh`` there, run ``install.sh --apply`` with the record's flags and
  ``--wheel`` pointing at that copy, then read the installed version and the node unit's
  ``ActiveState`` and ``ActiveEnterTimestamp``;
* ``shared_venv`` (spark, where the API and the node share one venv and hand-edited units):
  locally, ``uv pip install --reinstall-package culture-rules --no-deps WHEEL`` into that venv
  and restart ``culture-rules-api`` and ``culture-rules-node``. ``install.sh`` never runs.

Without ``--apply`` it prints the exact argv and opens no connection. Commands run through an
injectable runner (:func:`set_runner`; tests install a fake) as argv lists, never a shell
string; the remote command ssh carries is ``shlex``-quoted. The runner passes a minimal
environment, so the API URL and token never reach ssh, scp or the host.
"""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess  # nosec B404 - argv lists only, never a shell
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from culture_rules import deploy_data
from culture_rules.cli import _api
from culture_rules.cli._commands import rollout
from culture_rules.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from culture_rules.cli._nounlib import seg
from culture_rules.cli.registry import Context
from culture_rules.client.http import ApiError, ApiUnreachable

__all__ = ["Result", "commands", "set_runner", "upgrade"]

SSH_OPTS = ("-o", "BatchMode=yes")
VENV = ".local/share/culture-rules/venv"
"""The node venv install.sh creates, relative to the unit user's home."""
UNITS = ("culture-rules-api.service", "culture-rules-node.service")
NODE_UNIT = "culture-rules-node.service"
_SHOW = ("-p", "Id", "-p", "ActiveState", "-p", "ActiveEnterTimestamp")
_ENV_KEEP = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "SSH_AUTH_SOCK",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)
"""The only variables a command sees: never CULTURE_RULES_TOKEN or CULTURE_RULES_API_URL."""
_API_WAIT_S = 60


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str], float], Result]


@dataclass(frozen=True)
class Command:
    """One command of an upgrade: what it does, its argv and how long it may take."""

    what: str
    argv: tuple[str, ...]
    timeout: float = 60.0


def _minimal_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}


def _subprocess_runner(argv: Sequence[str], timeout: float) -> Result:
    exe = shutil.which(argv[0])
    if exe is None:
        raise CliError(EXIT_ENV_ERROR, f"{argv[0]} is not on PATH", f"install {argv[0]}")
    try:
        done = subprocess.run(  # nosec B603 - an argv list, the executable resolved by which
            [exe, *argv[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_minimal_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Result(124, "", f"timed out after {timeout:.0f}s")
    return Result(done.returncode, done.stdout, done.stderr)


_RUNNER: Runner = _subprocess_runner
_SLEEP: Callable[[float], None] = time.sleep
_HOSTNAME: Callable[[], str] = socket.gethostname


def set_runner(runner: Runner) -> Runner:
    """Install the command runner (tests pass a fake); returns the previous one."""
    global _RUNNER
    previous, _RUNNER = _RUNNER, runner
    return previous


# --------------------------------------------------------------------------- the commands


def _ssh(target: str, remote: str, what: str, timeout: float = 60.0) -> Command:
    return Command(what, ("ssh", *SSH_OPTS, target, remote), timeout)


def _install_sh_commands(
    name: str, target: str, install: Mapping[str, Any], wheel: Path, version: str
) -> list[Command]:
    deploy = f"culture-rules-deploy-{version}"
    flags = rollout.install_flags(name, install)
    flags[2:2] = ["--wheel", f"{deploy}/{wheel.name}"]  # right after --node-name NAME
    script = str(deploy_data.install_script())
    return [
        _ssh(target, shlex.join(["mkdir", "-p", deploy]), "make the deploy directory"),
        Command(
            "copy the wheel and install.sh",
            ("scp", *SSH_OPTS, "-q", script, str(wheel), f"{target}:{deploy}/"),
            300.0,
        ),
        _ssh(
            target,
            shlex.join(["bash", f"{deploy}/install.sh", *flags, "--apply"]),
            "run install.sh",
            900.0,
        ),
        _ssh(target, shlex.join([f"{VENV}/bin/culture-rules", "--version"]), "read the version"),
        _ssh(
            target,
            "XDG_RUNTIME_DIR=/run/user/$(id -u) "
            + shlex.join(["systemctl", "--user", "show", NODE_UNIT, *_SHOW]),
            "read the node unit's state",
        ),
    ]


def _shared_venv_commands(wheel: Path) -> list[Command]:
    venv = Path.home() / VENV
    return [
        Command(
            "install the wheel into the shared venv",
            (
                "uv",
                "pip",
                "install",
                "--python",
                str(venv / "bin" / "python"),
                "--reinstall-package",
                "culture-rules",
                "--no-deps",
                str(wheel),
            ),
            600.0,
        ),
        Command("restart the API and the node", ("systemctl", "--user", "restart", *UNITS)),
        Command("read the version", (str(venv / "bin" / "culture-rules"), "--version")),
        Command("read the units' state", ("systemctl", "--user", "show", *UNITS, *_SHOW)),
    ]


def _install_section(machine: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    install = machine.get("install")
    if not install:
        raise CliError(
            EXIT_USER_ERROR,
            f"machine {name} has no install section: it cannot be upgraded",
            f"add an install section to the machine record: culture-rules machines update {name}",
        )
    if install.get("mode") not in (rollout.INSTALL_SH, rollout.SHARED_VENV):
        raise CliError(
            EXIT_USER_ERROR,
            f"machine {name}: unknown install mode {install.get('mode')!r}",
            "set install.mode to install_sh or shared_venv",
        )
    return install


def commands(machine: Mapping[str, Any], wheel: Path, version: str) -> list[Command]:
    """The commands that upgrade ``machine`` to ``wheel``, in order (nothing runs here)."""
    name = str(machine.get("name") or machine.get("id"))
    install = _install_section(machine, name)
    if install["mode"] == rollout.SHARED_VENV:
        here = _HOSTNAME().split(".")[0]
        if here != name:
            raise CliError(
                EXIT_USER_ERROR,
                f"machine {name} is shared_venv: it is upgraded locally, but this host is {here}",
                f"run 'culture-rules rollout upgrade --host {name}' on {name}",
            )
        return _shared_venv_commands(wheel)
    target = str(machine.get("address") or name)
    return _install_sh_commands(name, target, install, wheel, version)


# --------------------------------------------------------------------------- reading results


def _installed_version(text: str) -> str | None:
    words = text.split()
    return words[-1] if words else None


def _unit_states(text: str) -> dict[str, dict[str, str]]:
    """``systemctl show`` blocks (``Key=value`` lines, blank-line separated) keyed by unit Id."""
    units: dict[str, dict[str, str]] = {}
    for block in text.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        units[props.get("Id", NODE_UNIT)] = props
    return units


def _wait_for_api(ctx: Context) -> None:
    """After restarting the API (shared_venv) wait until it answers again."""
    for _ in range(_API_WAIT_S):
        try:
            ctx.client.request("GET", "/health")
            return
        except (ApiUnreachable, ApiError):
            _SLEEP(1.0)
    raise CliError(
        EXIT_ENV_ERROR,
        f"the API did not answer within {_API_WAIT_S}s of its restart",
        "check 'systemctl --user status culture-rules-api.service'",
    )


def _outcome(mode: str, wheel: Path, results: Mapping[str, Result]) -> dict[str, Any]:
    out: dict[str, Any] = {"mode": mode, "wheel": wheel.name}
    version = results.get("read the version")
    out["installed_version"] = _installed_version(version.stdout) if version else None
    state = results.get("read the node unit's state") or results.get("read the units' state")
    for unit, props in _unit_states(state.stdout if state else "").items():
        prefix = "" if unit == NODE_UNIT else f"{unit.split('.')[0]}."
        out[f"{prefix}ActiveState"] = props.get("ActiveState")
        out[f"{prefix}ActiveEnterTimestamp"] = props.get("ActiveEnterTimestamp")
    return out


def _run_all(cmds: Sequence[Command], ctx: Context, mode: str) -> tuple[dict, str | None]:
    """Run each command in order; stop at the first that fails. ``(results, failure)``."""
    results: dict[str, Result] = {}
    for cmd in cmds:
        result = _RUNNER(list(cmd.argv), cmd.timeout)
        results[cmd.what] = result
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip()[-300:]
            return results, f"{cmd.what} failed (exit {result.returncode}): {tail}"
        if mode == rollout.SHARED_VENV and cmd.what == "restart the API and the node":
            _wait_for_api(ctx)  # the CLI talks to the API it just restarted
    return results, None


# --------------------------------------------------------------------------- the verb


def upgrade(ctx: Context, host: str, wheel: str) -> dict[str, Any]:
    machine = _api.call(lambda: ctx.client.request("GET", f"/machines/{seg(host)}"))
    name = str(machine.get("name") or machine.get("id") or host)
    _install_section(machine, name)
    path = rollout.check_wheel(wheel)
    version = rollout.release_version()
    cmds = commands(machine, path, version)
    mode = machine["install"]["mode"]
    step = f"upgrade:{name.lower()}"
    out = rollout.envelope(
        "upgrade",
        ctx,
        host=name,
        mode=mode,
        would=[list(c.argv) for c in cmds],
    )
    lines = [f"{c.what}: {shlex.join(c.argv)}" for c in cmds]
    if not ctx.apply:
        return {**out, "lines": ["dry-run: would run, in order:", *lines]}
    results, failure = _run_all(cmds, ctx, mode)
    outcome = _outcome(mode, path, results)
    node_state = outcome.get("ActiveState")
    if failure is None and outcome["installed_version"] != version:
        failure = f"installed version is {outcome['installed_version']}, not {version}"
    if failure is None and node_state != "active":
        failure = f"{NODE_UNIT} is {node_state}"
    outcome["ok"] = failure is None
    if failure:
        outcome["failure"] = failure[:1000]
    rollout.record(ctx, step, outcome)
    if failure:
        raise CliError(
            EXIT_ENV_ERROR,
            f"upgrade {name}: {failure}",
            f"fix the host, then re-run 'culture-rules rollout upgrade --host {name} --apply'",
        )
    return {
        **out,
        "outcome": outcome,
        "lines": [
            *lines,
            f"{name}: {version} installed, {NODE_UNIT} {node_state} since "
            f"{outcome.get('ActiveEnterTimestamp')}",
        ],
    }
