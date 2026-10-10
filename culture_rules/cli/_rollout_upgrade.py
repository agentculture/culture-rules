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
  The venv is the one the running API unit executes: its ``ExecStart`` D-Bus property
  (``busctl --user --json=short get-property``, the exact argv) runs
  ``<venv>/bin/culture-rules`` directly, or as ``grant run [--inject VAR=NAME]... --
  <venv>/bin/culture-rules``; the node unit must run the same venv, else the upgrade is
  refused naming both. Nothing assumes a default data directory.

Without ``--apply`` it prints the exact argv (``<venv>`` for the shared venv, which only an
applied run reads) and runs nothing. With ``--apply`` the step is first recorded as started
(``POST /rollouts/{version}/steps/upgrade:<host>``, admin): a token without the admin role
is refused there, before any command runs; an API without ``/rollouts`` endpoints (one that
predates the rollout CLI) answers 404 or 405 there and the upgrade exits 2 naming the
runbook's bootstrap, also before any command runs. Every machine-record field that reaches an argv
is checked again here, whatever the stored document holds: a control character or
whitespace in a one-word field, a control character in the run-as prefix, or an ssh target
that is not a plain host name refuses the upgrade (exit 1). Commands run through an
injectable runner (:func:`set_runner`; tests install a fake) as argv lists, never a shell
string; the remote command ssh carries is ``shlex``-quoted. The runner passes a minimal
environment, so the API URL and token never reach ssh, scp or the host.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess  # nosec B404 - argv lists only, never a shell
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from culture_rules import deploy_data
from culture_rules.cli import _api
from culture_rules.cli._commands import rollout
from culture_rules.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from culture_rules.cli._nounlib import seg
from culture_rules.cli.registry import Context
from culture_rules.client.http import ApiError, ApiUnreachable
from culture_rules.model.validate import has_control_char, is_install_word

__all__ = [
    "Result",
    "commands",
    "set_runner",
    "unit_object_path",
    "upgrade",
    "venv_from_exec_start",
]

SSH_OPTS = ("-o", "BatchMode=yes")
VENV = ".local/share/culture-rules/venv"
"""The node venv install.sh creates, relative to the unit user's home."""
API_UNIT = "culture-rules-api.service"
NODE_UNIT = "culture-rules-node.service"
UNITS = (API_UNIT, NODE_UNIT)
VENV_PLACEHOLDER = Path("<venv>")
"""Stands for the shared venv in a dry run (only an applied run reads the API unit)."""
_PROGRAM_RE = re.compile(r"^/[^\x00-\x1f\x7f]*/bin/culture-rules$")
_WORD_FIELDS = ("node_name", "wheelhouse", "python", "events_host")
_LIST_FIELDS = ("extras", "secrets")
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


def _shared_venv_commands(wheel: Path, venv: Path) -> list[Command]:
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


def _refuse_field(name: str, field: str, why: str) -> CliError:
    return CliError(
        EXIT_USER_ERROR,
        f"machine {name}: {field} {why}; refusing to build a command from it",
        f"fix the machine record (an admin): culture-rules machines update {name} --apply",
    )


def _check_word(name: str, field: str, value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not is_install_word(value):
        raise _refuse_field(name, field, "holds a control character or whitespace")


def check_fields(machine: Mapping[str, Any], name: str, install: Mapping[str, Any]) -> None:
    """Defense in depth: refuse an argv built from a stored field that validation should have
    refused (a control character or whitespace; an ssh target that could read as an option).
    """
    _check_word(name, "name", name)  # install.sh --node-name when node_name is unset
    for field in _WORD_FIELDS:
        _check_word(name, f"install.{field}", install.get(field))
    for field in _LIST_FIELDS:
        for i, item in enumerate(install.get(field) or ()):
            _check_word(name, f"install.{field}[{i}]", item)
    run_as = install.get("gate_run_as")
    if run_as is not None and (not isinstance(run_as, str) or has_control_char(run_as)):
        raise _refuse_field(name, "install.gate_run_as", "holds a control character")
    port = install.get("events_port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int)):
        raise _refuse_field(name, "install.events_port", "is not a port number")
    target = str(machine.get("address") or name)
    if not is_install_word(target) or target.startswith("-"):
        raise _refuse_field(name, "address", "is not a plain host name")


def commands(
    machine: Mapping[str, Any], wheel: Path, version: str, venv: Path = VENV_PLACEHOLDER
) -> list[Command]:
    """The commands that upgrade ``machine`` to ``wheel``, in order (nothing runs here);
    ``venv`` is the shared venv (``<venv>`` until an applied run resolves it)."""
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
        return _shared_venv_commands(wheel, venv)
    target = str(machine.get("address") or name)
    return _install_sh_commands(name, target, install, wheel, version)


# --------------------------------------------------------------------------- the shared venv


def unit_object_path(unit: str) -> str:
    """The systemd D-Bus object path of ``unit`` (``sd_bus_path_encode``: every byte that is
    not an ASCII letter, or a digit after the first, becomes ``_`` and two hex digits)."""
    label = "".join(
        ch if ch.isascii() and (ch.isalpha() or (ch.isdigit() and i)) else f"_{ord(ch):02x}"
        for i, ch in enumerate(unit)
    )
    return f"/org/freedesktop/systemd1/unit/{label or '_'}"


def _exec_start_entry(text: str) -> tuple[str, list[str]] | None:
    """``(path, argv)`` of the one ``ExecStart`` entry in ``busctl --json=short`` output
    (``{"type": "a(sasbttttuii)", "data": [[path, argv, ...]]}``); None otherwise."""
    try:
        data = json.loads(text).get("data")
    except (ValueError, AttributeError):
        return None
    if not isinstance(data, list) or len(data) != 1:
        return None  # no ExecStart, or several (which one runs the API is not one answer)
    entry = data[0]
    if not isinstance(entry, list) or len(entry) < 2:
        return None
    path, argv = entry[0], entry[1]
    if not isinstance(path, str) or not isinstance(argv, list) or not argv:
        return None
    if not all(isinstance(a, str) for a in argv):
        return None
    return path, argv


def _grant_run_program(argv: Sequence[str]) -> str | None:
    """The command ``grant run [--inject VAR=NAME]... -- PROGRAM ...`` runs: the argument
    after the separator ``--``. Option values are skipped (a value ``--`` is not the
    separator); any other option, or no ``--``, is not the supported wrapper (None)."""
    if len(argv) < 2 or argv[1] != "run":
        return None
    i = 2
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            return argv[i + 1] if i + 1 < len(argv) else None
        if arg == "--inject":
            i += 2
        elif arg.startswith("--inject="):
            i += 1
        else:
            return None
    return None


def venv_from_exec_start(text: str) -> Path | None:
    """The venv a unit runs, from its ``ExecStart`` D-Bus property (``busctl --json=short``).

    Why the bus property and not ``systemctl show -p ExecStart``: ``show`` prints
    ``argv[]=`` as the arguments joined by bare spaces (systemd 255), so ``/srv/my venv``
    and two arguments read alike, and the unit file's own line needs systemd's escaping,
    specifiers and drop-in resets decoded. The bus property is the parsed command itself:
    the executable ``path`` and an exact ``argv`` list.

    The executable is the entry's ``path``. If it is ``<venv>/bin/culture-rules``, that is
    the venv. If its basename is ``grant``, the argv must be the supported wrapper
    ``grant run [--inject VAR=NAME]... -- <venv>/bin/culture-rules ...``. Anything else
    (``env``, ``python``, an argument that merely names ``.../bin/culture-rules``) is None,
    which refuses the upgrade.
    """
    entry = _exec_start_entry(text)
    if entry is None:
        return None
    path, argv = entry
    program: str | None = path
    if PurePosixPath(path).name == "grant":
        program = _grant_run_program(argv)
    if program is None or not _PROGRAM_RE.match(program):
        return None
    return Path(program).parent.parent


def _unit_venv(unit: str) -> Path:
    argv = [
        "busctl",
        "--user",
        "--json=short",
        "get-property",
        "org.freedesktop.systemd1",
        unit_object_path(unit),
        "org.freedesktop.systemd1.Service",
        "ExecStart",
    ]
    result = _RUNNER(argv, 30.0)
    venv = venv_from_exec_start(result.stdout) if result.returncode == 0 else None
    if venv is None:
        raise CliError(
            EXIT_USER_ERROR,
            f"cannot resolve the shared venv: {unit}'s ExecStart does not run "
            f"<venv>/bin/culture-rules (exit {result.returncode})",
            f"check 'systemctl --user cat {unit}'; its one ExecStart must run "
            "<venv>/bin/culture-rules directly or as 'grant run [--inject VAR=NAME]... -- "
            "<venv>/bin/culture-rules ...'",
        )
    return venv


def resolve_shared_venv() -> Path:
    """The venv the running API unit executes; the node unit must run the same one."""
    api, node = _unit_venv(API_UNIT), _unit_venv(NODE_UNIT)
    if api != node:
        raise CliError(
            EXIT_USER_ERROR,
            f"the shared venv differs: {API_UNIT} runs {api}, {NODE_UNIT} runs {node}",
            "point both units at one venv (or give the host an install_sh section)",
        )
    return api


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


def _prepared(ctx: Context, host: str, wheel: str) -> tuple[Mapping[str, Any], str, Path]:
    """The machine record, its name and the wheel, every field checked (nothing runs)."""
    machine = _api.call(lambda: ctx.client.request("GET", f"/machines/{seg(host)}"))
    name = str(machine.get("name") or machine.get("id") or host)
    check_fields(machine, name, _install_section(machine, name))
    return machine, name, rollout.check_wheel(wheel)


def _resolved_commands(
    ctx: Context, machine: Mapping[str, Any], path: Path, step: str, base: Mapping[str, Any]
) -> tuple[list[Command], str | None]:
    """The commands with the shared venv resolved (``(commands, venv)``); a venv that
    cannot be resolved is recorded as the step's failure, then refused."""
    version = rollout.release_version()
    if machine["install"]["mode"] != rollout.SHARED_VENV:
        return commands(machine, path, version), None
    try:
        venv = resolve_shared_venv()
    except CliError as exc:
        rollout.record(ctx, step, {**base, "ok": False, "failure": exc.message[:1000]})
        raise
    return commands(machine, path, version, venv), str(venv)


def _dry_lines(mode: str, cmds: Sequence[Command]) -> list[str]:
    lines = ["dry-run: would run, in order:"]
    if mode == rollout.SHARED_VENV:
        lines.append(
            f"(<venv> is resolved from {API_UNIT}'s ExecStart D-Bus property with 'busctl', "
            f"and {NODE_UNIT} must run the same venv; only --apply reads it)"
        )
    return lines + [f"{c.what}: {shlex.join(c.argv)}" for c in cmds]


def upgrade(ctx: Context, host: str, wheel: str) -> dict[str, Any]:
    machine, name, path = _prepared(ctx, host, wheel)
    version = rollout.release_version()
    mode = machine["install"]["mode"]
    planned = commands(machine, path, version)
    step = f"upgrade:{name.lower()}"
    out = rollout.envelope(
        "upgrade", ctx, host=name, mode=mode, would=[list(c.argv) for c in planned]
    )
    if not ctx.apply:
        return {**out, "lines": _dry_lines(mode, planned)}
    base = {"mode": mode, "wheel": path.name}
    # recording the start is admin: a viewer or editor token is refused here, before any
    # command runs (the API is the enforcement point; the CLI's roles are only metadata)
    rollout.record(ctx, step, {"state": "started", **base})
    cmds, venv = _resolved_commands(ctx, machine, path, step, base)
    results, failure = _run_all(cmds, ctx, mode)
    outcome = _outcome(mode, path, results)
    if venv:
        outcome["venv"] = venv
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
        "would": [list(c.argv) for c in cmds],
        **({"venv": venv} if venv else {}),
        "outcome": outcome,
        "lines": [
            *(f"{c.what}: {shlex.join(c.argv)}" for c in cmds),
            f"{name}: {version} installed, {NODE_UNIT} {node_state} since "
            f"{outcome.get('ActiveEnterTimestamp')}",
        ],
    }
