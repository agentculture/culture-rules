"""The PR fixer's test gate and diff guard: a built-in ``gate`` code step.

The fixer workflow's ``gate`` step (``kind: code``, ``config.builtin: gate``) judges the
commits the agent made before anything is pushed. Its verdict is a structured output the
workflow branches on (``retry_until`` on ``verdict``, ``github.push``'s ``gate_verdict``);
the gate itself never pushes.

Verdicts
========

``pass``
    the diff guard found nothing and every declared setup and test command exited 0.
``fail``
    a setup or test command exited non-zero (or timed out): ``phase``, ``command``,
    ``exit_code`` and ``output_tail`` (the last ``tail_bytes`` of its merged stdout and
    stderr) say which, and ``instruction`` is the text for the agent's next attempt.
``guard``
    the diff guard rejected the commits; ``rule`` names the first rule broken and
    ``violations`` lists all of them. No test command runs.
``no_gate``
    the base branch's ``culture.yaml`` has no ``gate`` section (or there is no
    ``culture.yaml``). Nothing runs. There is **no built-in default command**.

On ``pass`` and ``no_gate`` the gate also reports the change it verified, for the reviewer
agent (d20), which cannot fetch a commit that is not pushed yet: ``diff`` (``git diff
start_sha commit_sha`` in the node-owned scratch repo below, with no external diff driver,
textconv or attributes), ``diff_chars`` (its full length) and ``diff_truncated`` (the diff
was cut at ``config.diff_max_chars``, default :data:`DEFAULT_DIFF_MAX_CHARS`; the fixer's
review step then never approves).

Anything that is not a judgement of the commits (bad input, no runner configured, the
worktree cannot be read, a malformed ``gate`` section, the deadline) fails the step
instead, with a ``code: detail`` error, so a broken environment never reads as a verdict.

Inputs: ``worktree`` (absolute path of the agent's worktree on this machine, the bridge
result's ``worktree``), ``base_sha`` (the PR base commit), ``start_sha`` (the head the agent
started from, ``head_before``) and ``commit_sha`` (the commit to judge, ``head_after``),
each a full lowercase hex SHA. Config: ``protected_paths_variable`` (default
:data:`PROTECTED_PATHS_VARIABLE`) and ``tail_bytes`` (default :data:`DEFAULT_TAIL_BYTES`).

The gate is read from the base branch (r1)
==========================================

The ``gate`` section is read from ``culture.yaml`` **at** ``base_sha``
(``git cat-file blob <base_sha>:culture.yaml``), never from the PR head or the working
tree, so a PR cannot change how it is judged: an edit to its own ``gate`` section takes
effect only once merged. The shape is strict: ``gate: {setup: [[argv...], ...],
test: [[argv...], ...]}``; ``test`` is required and non-empty, ``setup`` optional, every
command a non-empty list of strings, no other keys. Anything else fails the step
``gate_invalid`` with the reason; it is never guessed at.

Repository access: a node-owned scratch repo
============================================

The agent's worktree belongs to the fixer Unix user, so git run there as the node user
trips ``safe.directory``, and the worktree's own config, hooks and attributes are the
agent's to change. The gate therefore never reads git data in place. Through the run-as
seam, as the fixer user, it streams one pack of ``base_sha``, ``start_sha`` and
``commit_sha`` (``git pack-objects --revs --stdout``, full history: a self-contained pack)
out of the worktree into a fresh, node-owned bare repository (``git index-pack --strict``,
which verifies every object's hash). Every read - the base ``culture.yaml``, ancestry and
the diff - happens there, with no system or global git config, hooks off, no replace
objects and no attributes, so nothing the agent wrote can alter what the guard sees. On
``pass`` the gate also writes a full git bundle of ``commit_sha`` into a node-owned
directory (``CULTURE_RULES_GATE_BUNDLE_DIR``, default
``~/.local/state/culture-rules/gate-bundles``; bundles older than
:data:`BUNDLE_TTL_S` are pruned) and returns its path as ``bundle``: the ``source`` for
``github.push``, which can read it where it cannot read the worktree.

Running as the fixer Unix user: the run-as seam
================================================

The node cannot become another user without privilege, and running the PR's code as the
node user would hand the agent's commits the node's credentials. So every command that
touches the worktree goes through a :data:`CommandRunner` seam. Production builds it from
``CULTURE_RULES_GATE_RUN_AS``: an operator-configured argv prefix (split with
:func:`shlex.split`, never a shell), for example ``sudo -n -u culture-fixer --``. The gate
appends ``env -C <dir> -- <argv>``, so the command runs in that directory (the fresh checkout
below), as that user, as an argv list with ``shell=False`` end to end. **Unset, the gate refuses**
(``gate_runner_unconfigured``) rather than silently running as the node user. A prefix
whose first word is a remote or login shell (``ssh``, ``su``, ``sh``, ...) is refused: those
join argv into a shell command line. Trade-off: the node user can run anything as the fixer
user (that is the grant; the fixer user is the less privileged of the two, and the reverse
is impossible), and on a timeout the node can signal only ``sudo`` itself (it relays
``SIGTERM`` to the command); processes that ignore it are not the node's to kill. Commands
get the environment the prefix gives them (``sudo`` resets it), not the node's, plus
:func:`gate_env` in front of each setup and test argv: ``TMPDIR`` and pytest's
``--basetemp`` inside the gate's workspace, so no temp path names the run-as account.

A ``sudo`` prefix cannot work in a process with the kernel's ``no_new_privs`` flag set
(systemd ``NoNewPrivileges=true``): sudo refuses before it runs anything. :meth:`GatePort.from_env`
checks for that at node start (``NoNewPrivs`` in ``/proc/self/status``), logs an error naming
the fix (``deploy/node/install.sh --gate-run-as``, or a unit drop-in with
``NoNewPrivileges=false``) and the gate then refuses ``run_as_blocked`` instead of failing
later on an opaque error. When a worktree command fails, the gate probes the run-as with
``true``: if that fails too, the run-as itself is broken (``run_as_failed``, or
``run_as_blocked`` when sudo names the flag); otherwise the worktree lacks a commit
(``source_unavailable``). Both carry a bounded, control-character-free stderr tail.

Diff guard
==========

Run over ``start_sha..commit_sha`` before any test command, in this order:

``history_rewritten``
    ``commit_sha`` does not descend from ``start_sha``.
``protected_paths_unset``
    the shared variable ``fixer_protected_paths`` is undefined or is not a list of
    non-empty strings. Fails closed: the guard never guesses a list.
``protected_path``
    a changed path (either side of a rename) matches a protected pattern: the variable's
    patterns plus the floor :data:`ALWAYS_PROTECTED` (``.github/workflows/**``). Patterns
    are gitignore-like globs: ``*`` and ``?`` stay inside one path segment, ``**`` crosses
    segments, a pattern without a ``/`` matches a basename at any depth, a leading ``/``
    anchors at the root, and a pattern that names a directory covers everything under it.
``test_deleted``
    a test file (``test_*.py``, ``*_test.py``, ``*.test.ts``, ``*.spec.js``,
    ``__tests__/``, ``*_test.go``, ...) was deleted, or renamed to a non-test path.
``test_removed``
    a test definition (Python ``def test_*`` / ``class Test*``, JS/TS ``it(``/``test(``,
    Go ``func Test*``) disappeared from the test files: removed more often than re-added,
    counted by name across the diff (moving a test between files is fine; renaming one is
    not).
``test_skipped``
    a skip or focus marker was added (``pytest.mark.skip``/``skipif``/``xfail``,
    ``pytest.skip(``, ``unittest.skip``, ``skipTest(``, ``it.skip``, ``xit(``, ``it.only``,
    ``t.Skip(``, ...).
``suppression_marker``
    a check suppression was added: ``NOSONAR``, ``# noqa``, ``type: ignore``, ``# nosec``,
    ``pylint: disable``, ``eslint-disable``, ``@ts-ignore``, ``@ts-expect-error``,
    ``@ts-nocheck``, ``pragma: no cover``, ``nolint``.

A marker counts as added when, in one file, more lines carrying it were added than
removed (so moving or reformatting an existing line is not flagged).

Where the commands run: a fresh checkout, never the worktree
============================================================

The agent controls its worktree's index and untracked files (``--skip-worktree`` edits, a
stray ``conftest.py``), so tests there would judge code other than the commit that is
bundled and pushed. Setup and test commands therefore run in a **fresh checkout of
``commit_sha``**, made as the fixer user in its own ``mktemp -d`` directory from the
node-verified pack (fed to ``git index-pack --stdin`` on stdin: the fixer never reads a
node-owned path). Every git call there ignores the fixer's own git configuration, which the
agent can edit: ``GIT_CONFIG_GLOBAL=/dev/null``, ``GIT_CONFIG_NOSYSTEM=1``, an empty
template (``--template=``, ``GIT_TEMPLATE_DIR=``), ``core.hooksPath=/dev/null`` (no
post-checkout hook), no fsmonitor. After the checkout ``HEAD`` must be ``commit_sha`` and
``git status --porcelain --ignored`` must be empty, else the step fails
``checkout_failed``. The directory is removed afterwards (``rm -rf`` as the fixer user,
with its own timeout), also on failure or timeout. The worktree is only ever the source of
the pack.

Re-running the gate on the same commit is harmless (it only re-judges), so the port
declares ``supports_idempotency_key``. Standard-library only (the ``yaml`` extra parses
``culture.yaml``, imported lazily).
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import signal
import subprocess  # nosec B404 - argv lists only, shell=False
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Protocol

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.variables import variable_values

log = logging.getLogger(__name__)

__all__ = [
    "ALWAYS_PROTECTED",
    "BUNDLE_DIR_ENV",
    "CommandRunner",
    "DEFAULT_TAIL_BYTES",
    "FAIL",
    "GUARD",
    "GateConfigError",
    "GatePort",
    "GateSpec",
    "NO_GATE",
    "PASS",
    "PROTECTED_PATHS_VARIABLE",
    "RUN_AS_ENV",
    "RunAs",
    "TIMED_OUT",
    "UNAVAILABLE",
    "VERDICTS",
    "Violation",
    "diff_guard",
    "no_new_privs",
    "gate_env",
    "gate_from_mapping",
    "parse_gate",
    "path_matches",
    "run_as_from_env",
    "run_process",
]

PASS, FAIL, GUARD, NO_GATE = "pass", "fail", "guard", "no_gate"
VERDICTS: tuple[str, ...] = (PASS, FAIL, GUARD, NO_GATE)

PROTECTED_PATHS_VARIABLE = "fixer_protected_paths"
#: Always protected, whatever the variable says (culture-nodes' scope_guard, lifted).
ALWAYS_PROTECTED: tuple[str, ...] = (".github/workflows/**",)
RUN_AS_ENV = "CULTURE_RULES_GATE_RUN_AS"
BUNDLE_DIR_ENV = "CULTURE_RULES_GATE_BUNDLE_DIR"
DEFAULT_BUNDLE_DIR = "~/.local/state/culture-rules/gate-bundles"
BUNDLE_TTL_S = 7 * 24 * 3600.0
DEFAULT_TAIL_BYTES = 8000
_MAX_TAIL_BYTES = 100_000
DEFAULT_DIFF_MAX_CHARS = 30_000
"""Characters of ``start_sha..commit_sha`` diff handed to the reviewer (d20); a longer diff
is cut and flagged ``diff_truncated``. Leaves room in a bridge's 60000-character budget for
the threads and the gate output."""
_MAX_DIFF_CHARS = 200_000
CULTURE_YAML = "culture.yaml"

#: Runner return codes outside any process's own range.
TIMED_OUT = -1000
UNAVAILABLE = -1001
_MARGIN_S = 5.0  # finish (and answer) this long before the step's deadline
_TERM_GRACE_S = 2.0
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
#: Prefix words that would join argv into a shell command line.
_SHELL_JOINING = frozenset({"ssh", "su", "sh", "bash", "dash", "zsh", "ksh", "fish", "csh"})
_LOCAL_REF = "refs/culture-rules/gate"
_CLEANUP_S = 60.0
#: The gate's workspace: no dash, so no run-as account name or ``-x`` flag look-alike can
#: reach a repo's temp paths through it (lobes-cli#302).
_CHECKOUT_PREFIX = "culture_rules_gate."
_STDERR_TAIL_BYTES = 500
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_PROC_STATUS = "/proc/self/status"
#: What fixes a sudo prefix under no_new_privs (logged at start, and in the refusal).
NO_NEW_PRIVS_FIX = (
    "re-run deploy/node/install.sh with --gate-run-as (it writes NoNewPrivileges=false), or "
    "add a drop-in ~/.config/systemd/user/culture-rules-node.service.d/gate-sudo.conf with "
    "[Service] NoNewPrivileges=false, then daemon-reload and restart the node"
)
#: The fixer user can edit its own ~/.gitconfig and git templates: ignore all of them.
_HARD_GIT_ENV = (
    "env",
    "GIT_CONFIG_GLOBAL=/dev/null",
    "GIT_CONFIG_NOSYSTEM=1",
    "GIT_TEMPLATE_DIR=",
    "GIT_ATTR_NOSYSTEM=1",
    "GIT_NO_REPLACE_OBJECTS=1",
    "GIT_TERMINAL_PROMPT=0",
)


class GateConfigError(ValueError):
    """The ``gate`` section (or the culture.yaml holding it) is not usable."""


class _Refusal(Exception):
    """Not a verdict: the step fails with ``code: detail``."""

    def __init__(self, code: str, detail: str = "", *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code, self.detail, self.retryable = code, detail, retryable

    def message(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


# --------------------------------------------------------------------------- gate section


@dataclass(frozen=True)
class GateSpec:
    """A validated ``gate`` section: setup commands, then test commands (argv tuples)."""

    setup: tuple[tuple[str, ...], ...]
    test: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict[str, list[list[str]]]:
        return {"setup": [list(a) for a in self.setup], "test": [list(a) for a in self.test]}


def _commands(value: Any, name: str, *, required: bool) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise GateConfigError(f"gate.{name} must be a list of argv lists")
    if required and not value:
        raise GateConfigError(f"gate.{name} must declare at least one command")
    out = []
    for i, argv in enumerate(value):
        where = f"gate.{name}[{i}]"
        if not isinstance(argv, list) or not argv:
            raise GateConfigError(f"{where} must be a non-empty list of strings")
        for j, arg in enumerate(argv):
            if not isinstance(arg, str):
                raise GateConfigError(f"{where}[{j}] must be a string (quote it), not {arg!r}")
            if "\x00" in arg:
                raise GateConfigError(f"{where}[{j}] contains a NUL byte")
        if not argv[0] or "=" in argv[0]:
            raise GateConfigError(f"{where}[0] must name a program (non-empty, no '=')")
        out.append(tuple(argv))
    return tuple(out)


def gate_from_mapping(value: Any) -> GateSpec:
    """Validate a ``gate`` section strictly (see the module docstring)."""
    if not isinstance(value, dict):
        raise GateConfigError("gate must be a mapping with 'test' (and optional 'setup')")
    unknown = sorted(str(k) for k in value if k not in ("setup", "test"))
    if unknown:
        raise GateConfigError(f"gate has unknown keys: {', '.join(unknown)}")
    if "test" not in value:
        raise GateConfigError("gate.test is required")
    setup = _commands(value.get("setup", []), "setup", required=False)
    return GateSpec(setup=setup, test=_commands(value["test"], "test", required=True))


def parse_gate(text: str, where: str = CULTURE_YAML) -> GateSpec | None:
    """The ``gate`` section of culture.yaml ``text``; ``None`` when it has none."""
    try:
        import yaml  # lazy: optional extra
    except ImportError as exc:
        raise GateConfigError(
            "extra_missing: PyYAML is required to read culture.yaml; install " "culture-rules[yaml]"
        ) from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GateConfigError(f"invalid YAML in {where}: {exc}") from exc
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise GateConfigError(f"{where} must contain a mapping at the top level")
    if "gate" not in raw:
        return None
    return gate_from_mapping(raw["gate"])


# --------------------------------------------------------------------------- processes


class CommandRunner(Protocol):
    """Runs ``argv`` in ``cwd`` as the fixer user; returns the exit code (or a sentinel).

    stdout (with stderr merged when asked) goes to ``stdout``; unmerged stderr goes to
    ``stderr`` when given, else is discarded. ``stdin`` is closed unless given. Returns
    :data:`TIMED_OUT` / :data:`UNAVAILABLE`."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        timeout: float,
        stdout: IO[bytes],
        stdin: IO[bytes] | None = None,
        merge_stderr: bool = False,
        stderr: IO[bytes] | None = None,
    ) -> int: ...


def _signal_group(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _wait_group_gone(pgid: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _signal_group(pgid, 0):
            return True
        time.sleep(0.05)
    return not _signal_group(pgid, 0)


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM, then SIGKILL, the process group the child leads; then reap it."""
    _signal_group(proc.pid, signal.SIGTERM)
    if not _wait_group_gone(proc.pid, _TERM_GRACE_S):
        _signal_group(proc.pid, signal.SIGKILL)
        _wait_group_gone(proc.pid, _TERM_GRACE_S)
    try:
        proc.wait(timeout=_TERM_GRACE_S)
    except subprocess.TimeoutExpired:  # pragma: no cover - the group is already gone
        proc.kill()
        proc.wait()


def run_process(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: float,
    stdout: IO[bytes],
    stdin: IO[bytes] | None = None,
    merge_stderr: bool = False,
    env: Mapping[str, str] | None = None,
    stderr: IO[bytes] | None = None,
) -> int:
    """One process group, argv list, ``shell=False``; the whole group dies on timeout."""
    try:
        proc = subprocess.Popen(  # nosec B603 - argv list, shell=False
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else _base_env(),
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT if merge_stderr else (stderr or subprocess.DEVNULL),
            shell=False,
            start_new_session=True,
        )
    except OSError:
        return UNAVAILABLE
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return TIMED_OUT
    if _signal_group(proc.pid, 0):  # a helper left behind in the group must not linger
        _signal_group(proc.pid, signal.SIGKILL)
    return code


def _base_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"}


class RunAs:
    """The production :class:`CommandRunner`: ``<prefix> env -C <cwd> -- <argv>``."""

    def __init__(self, prefix: Sequence[str], *, run: Callable[..., int] = run_process) -> None:
        prefix = list(prefix)
        if not prefix or not all(isinstance(p, str) and p for p in prefix):
            raise ValueError("the run-as prefix must be a non-empty list of words")
        if os.path.basename(prefix[0]) in _SHELL_JOINING:
            raise ValueError(
                f"the run-as prefix may not start with {prefix[0]!r}: it would run the "
                "command through a shell"
            )
        self.prefix = tuple(prefix)
        self._run = run

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        timeout: float,
        stdout: IO[bytes],
        stdin: IO[bytes] | None = None,
        merge_stderr: bool = False,
        stderr: IO[bytes] | None = None,
    ) -> int:
        full = [*self.prefix, "env", "-C", cwd, "--", *argv]
        extra = {} if stderr is None else {"stderr": stderr}
        return self._run(
            full, timeout=timeout, stdout=stdout, stdin=stdin, merge_stderr=merge_stderr, **extra
        )

    @property
    def uses_sudo(self) -> bool:
        return os.path.basename(self.prefix[0]) == "sudo"


def run_as_from_env(environ: Mapping[str, str] | None = None) -> tuple[RunAs | None, str]:
    """The configured :class:`RunAs` (or ``None``) and, when ``None``, why."""
    environ = os.environ if environ is None else environ
    value = (environ.get(RUN_AS_ENV) or "").strip()
    if not value:
        return None, (
            f"{RUN_AS_ENV} is not set; the gate never runs commands as the node user "
            "(set it to a prefix such as 'sudo -n -u culture-fixer --')"
        )
    try:
        return RunAs(shlex.split(value)), ""
    except ValueError as exc:
        return None, f"{RUN_AS_ENV}: {exc}"


def no_new_privs(status_path: str = _PROC_STATUS) -> bool | None:
    """Whether this process has the kernel's ``no_new_privs`` flag (``None``: unknown)."""
    try:
        with open(status_path, encoding="utf-8", errors="replace") as status:
            for line in status:
                if line.startswith("NoNewPrivs:"):
                    return line.split(":", 1)[1].strip() == "1"
    except OSError:
        return None
    return None


def _stderr_tail(handle: IO[bytes], limit: int = _STDERR_TAIL_BYTES) -> str:
    """The last ``limit`` bytes of ``handle`` as one printable line (no control chars)."""
    handle.flush()
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - limit))
    text = handle.read().decode("utf-8", errors="replace")
    lines = (_CONTROL_RE.sub("?", line).strip() for line in text.splitlines())
    return " | ".join(line for line in lines if line)


def _tail(handle: IO[bytes], limit: int) -> str:
    handle.flush()
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - limit))
    data = handle.read()
    text = data.decode("utf-8", errors="replace")
    if size > limit and "\n" in text:
        text = text.split("\n", 1)[1]  # start on a whole line
    return text


# --------------------------------------------------------------------------- diff guard


@dataclass(frozen=True)
class Violation:
    """One diff-guard finding."""

    rule: str
    path: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "path": self.path, "detail": self.detail}


def _glob_regex(pattern: str) -> re.Pattern[str]:
    pat = pattern.strip()
    anchored = pat.startswith("/")
    pat = pat.strip("/")
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    body = "".join(out)
    lead = "" if anchored or "/" in pat else "(?:.*/)?"
    return re.compile(f"^{lead}{body}(?:/.*)?$", re.DOTALL)


def path_matches(path: str, patterns: Iterable[str]) -> str | None:
    """The first pattern ``path`` matches (see the module docstring), or ``None``."""
    for pattern in patterns:
        if pattern.strip().strip("/") and _glob_regex(pattern).match(path):
            return pattern
    return None


_TEST_FILE = re.compile(
    r"(?:^|/)(?:test_[^/]*\.py|[^/]*_test\.py|tests\.py|[^/]*_test\.go"
    r"|[^/]*\.(?:test|spec)\.[cm]?[jt]sx?)$"
    r"|(?:^|/)__tests__/"
)
_TEST_DEFS = (
    re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\("),
    re.compile(r"^\s*class\s+(Test\w*)\b"),
    re.compile(r"^\s*func\s+(Test\w*)\s*\("),
    re.compile(r"""\b(?:it|test)\s*\(\s*(['"`])(.+?)\1"""),
)
_SKIP_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pytest.mark.skip", re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)\b")),
    ("pytest.skip", re.compile(r"pytest\.(?:skip|xfail|importorskip)\s*\(")),
    ("unittest.skip", re.compile(r"unittest\.(?:skip\w*|expectedFailure)\b")),
    ("skipTest", re.compile(r"\.skipTest\s*\(")),
    ("js.skip", re.compile(r"\b(?:it|test|describe)\.(?:skip|todo)\s*\(")),
    ("js.x", re.compile(r"\bx(?:it|test|describe)\s*\(")),
    ("js.only", re.compile(r"\b(?:it|test|describe)\.only\s*\(|\bf(?:it|describe)\s*\(")),
    ("go.skip", re.compile(r"\bt\.Skip(?:f|Now)?\s*\(")),
)
_SUPPRESSIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NOSONAR", re.compile(r"NOSONAR")),
    ("noqa", re.compile(r"#\s*noqa\b", re.IGNORECASE)),
    ("type: ignore", re.compile(r"type:\s*ignore\b")),
    ("nosec", re.compile(r"#\s*nosec\b")),
    ("pylint: disable", re.compile(r"pylint:\s*disable")),
    ("eslint-disable", re.compile(r"eslint-disable")),
    ("@ts-ignore", re.compile(r"@ts-(?:ignore|expect-error|nocheck)\b")),
    ("pragma: no cover", re.compile(r"pragma:\s*no\s*cover")),
    ("nolint", re.compile(r"//\s*nolint\b")),
)
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def is_test_path(path: str) -> bool:
    return bool(_TEST_FILE.search(path))


def _hunk_lines(patch: str) -> Iterable[tuple[str, str]]:
    """``("+"|"-", text)`` for every added/removed line of a one-file ``-U0`` patch.

    Header lines are skipped, never parsed for a path: the caller names the file (from
    ``--name-status -z``), so no quoting or tab-terminated header can misattribute it."""
    lines = patch.split("\n")
    i = 0
    while i < len(lines):
        m = _HUNK.match(lines[i])
        i += 1
        if m is None:
            continue
        left = 1 if m.group(1) is None else int(m.group(1))
        right = 1 if m.group(2) is None else int(m.group(2))
        while (left > 0 or right > 0) and i < len(lines):
            body = lines[i]
            i += 1
            if body.startswith("-"):
                left -= 1
                yield "-", body[1:]
            elif body.startswith("+"):
                right -= 1
                yield "+", body[1:]
            elif body.startswith(" "):
                left, right = left - 1, right - 1
            # "\ No newline at end of file" counts as neither side


def changed_paths(name_status: str) -> list[str]:
    """Every path ``--name-status -z`` names (both sides of a rename or copy), in order."""
    seen: dict[str, None] = {}
    for _status, paths in _name_status(name_status):
        seen.update(dict.fromkeys(paths))
    return list(seen)


def _name_status(raw: str) -> list[tuple[str, list[str]]]:
    """``(status letter, [path, (new path)])`` from ``git diff --name-status -z``."""
    parts = raw.split("\x00")
    out, i = [], 0
    while i < len(parts) and parts[i]:
        status = parts[i]
        n = 2 if status[:1] in ("R", "C") else 1
        out.append((status[:1], parts[i + 1 : i + 1 + n]))
        i += 1 + n
    return out


def _test_names(text: str) -> list[str]:
    names = []
    for regex in _TEST_DEFS:
        for m in regex.finditer(text):
            names.append(m.group(m.lastindex or 1))
    return names


def _added_markers(
    lines: list[tuple[str, str, str]], markers: tuple[tuple[str, re.Pattern[str]], ...], rule: str
) -> list[Violation]:
    counts: Counter[tuple[str, str, str]] = Counter()
    first: dict[tuple[str, str], str] = {}
    for path, side, text in lines:
        for label, regex in markers:
            hits = len(regex.findall(text))
            if hits:
                counts[(path, label, side)] += hits
                if side == "+":
                    first.setdefault((path, label), text.strip()[:200])
    found = []
    for (path, label), line in first.items():
        if counts[(path, label, "+")] > counts[(path, label, "-")]:
            found.append(Violation(rule, path, f"{label}: {line}"))
    return found


def diff_guard(
    name_status: str, patches: Mapping[str, str], patterns: Sequence[str]
) -> list[Violation]:
    """Every diff-guard violation in one diff.

    ``name_status`` is ``git diff --name-status -z -M`` output; ``patches`` maps each path
    it names (see :func:`changed_paths`) to that path's own ``-U0 --no-renames`` patch."""
    protected = (*ALWAYS_PROTECTED, *patterns)
    found: list[Violation] = []
    entries = _name_status(name_status)
    for _status, paths in entries:
        for path in paths:
            hit = path_matches(path, protected)
            if hit is not None:
                found.append(Violation("protected_path", path, f"matches {hit!r}"))
    for status, paths in entries:
        if status == "D" and is_test_path(paths[0]):
            found.append(Violation("test_deleted", paths[0], "test file deleted"))
        elif status == "R" and is_test_path(paths[0]) and not is_test_path(paths[-1]):
            found.append(Violation("test_deleted", paths[0], f"moved to {paths[-1]}"))
    lines = [
        (path, side, text)
        for path in changed_paths(name_status)
        for side, text in _hunk_lines(patches.get(path, ""))
    ]
    removed: Counter[str] = Counter()
    added: Counter[str] = Counter()
    where: dict[str, str] = {}
    for path, side, text in lines:
        if not is_test_path(path):
            continue
        for name in _test_names(text):
            (added if side == "+" else removed)[name] += 1
            if side == "-":
                where.setdefault(name, path)
    for name in sorted(removed):
        if removed[name] > added[name]:
            found.append(Violation("test_removed", where[name], f"test {name!r} removed"))
    found += _added_markers(lines, _SKIP_MARKERS, "test_skipped")
    found += _added_markers(lines, _SUPPRESSIONS, "suppression_marker")
    return found


# --------------------------------------------------------------------------- the port


def _git_env(home: str) -> dict[str, str]:
    """No system/global config, no prompts, no replace objects: the node's git, clean."""
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": home,
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_LITERAL_PATHSPECS": "1",  # a changed path is a name, never a glob or magic
    }


class _Job:
    """One gate invocation: the deadline, the scratch repo and the worktree commands."""

    def __init__(
        self,
        tmp: str,
        worktree: str,
        run_as: CommandRunner,
        git: Callable[..., int],
        deadline: datetime,
        clock: Callable[[], datetime],
    ) -> None:
        self.tmp, self.worktree = tmp, worktree
        self.repo = os.path.join(tmp, "gate.git")
        self._run_as, self._git = run_as, git
        self.deadline, self.clock = deadline, clock

    def left(self) -> float:
        left = (self.deadline - self.clock()).total_seconds() - _MARGIN_S
        if left <= 0:
            raise _Refusal("deadline_exceeded", retryable=True)
        return left

    def git(
        self,
        *args: str,
        stdin: IO[bytes] | None = None,
        in_repo: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> bytes:
        """Run git on the scratch repo; returns stdout, raising on a non-zero exit."""
        rc, out = self.git_rc(*args, stdin=stdin, in_repo=in_repo, env=env)
        if rc != 0:
            raise _Refusal("git_failed", f"git {args[0]} exited {rc}")
        return out

    def git_rc(
        self,
        *args: str,
        stdin: IO[bytes] | None = None,
        in_repo: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, bytes]:
        argv = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.quotePath=false"]
        if in_repo:
            argv += ["-C", self.repo]
        with tempfile.TemporaryFile(dir=self.tmp) as out:
            rc = self._git(
                [*argv, *args],
                timeout=self.left(),
                stdout=out,
                stdin=stdin,
                env={**_git_env(self.tmp), **(env or {})},
            )
            out.seek(0)
            data = out.read()
        if rc == TIMED_OUT:
            self.left()
            raise _Refusal("git_timeout", retryable=True)
        if rc == UNAVAILABLE:
            raise _Refusal("git_unavailable", retryable=True)
        return rc, data

    def fixer(
        self,
        argv: Sequence[str],
        out: IO[bytes],
        *,
        stdin: IO[bytes] | None = None,
        merge_stderr: bool = True,
        timeout_code: str | None = None,
        cwd: str | None = None,
        stderr: IO[bytes] | None = None,
    ) -> int:
        """Run ``argv`` as the fixer user in ``cwd`` (default: the agent's worktree).

        With ``timeout_code``, a timeout is a retryable refusal of that code instead of
        :data:`TIMED_OUT` (only a gate command's own timeout is a verdict)."""
        extra = {} if stderr is None else {"stderr": stderr}
        rc = self._run_as(
            list(argv),
            cwd=cwd or self.worktree,
            timeout=self.left(),
            stdout=out,
            stdin=stdin,
            merge_stderr=merge_stderr,
            **extra,
        )
        if rc == UNAVAILABLE:
            raise _Refusal("gate_runner_unavailable", "the run-as command could not start")
        if rc == TIMED_OUT and timeout_code is not None:
            self.left()
            raise _Refusal(timeout_code, "timed out", retryable=True)
        return rc

    def fixer_text(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        stdin: IO[bytes] | None = None,
        timeout_code: str = "checkout_timeout",
    ) -> tuple[int, str]:
        with tempfile.TemporaryFile(dir=self.tmp) as out:
            rc = self.fixer(argv, out, stdin=stdin, timeout_code=timeout_code, cwd=cwd)
            out.seek(0)
            return rc, out.read().decode("utf-8", errors="replace")

    def remove_checkout(self, path: str) -> None:
        """``rm -rf`` the fixer-owned workspace, with its own timeout (even past the deadline)."""
        with tempfile.TemporaryFile(dir=self.tmp) as out:
            self._run_as(["rm", "-rf", "--", path], cwd="/", timeout=_CLEANUP_S, stdout=out)


def _hard_git(*args: str) -> list[str]:
    """git as the fixer user, immune to the fixer's own git config, templates and hooks."""
    return [
        *_HARD_GIT_ENV,
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.quotePath=false",
        *args,
    ]


def gate_env(tmpdir: str, addopts: str | None = None) -> list[str]:
    """The ``env`` words in front of every setup and test command (lobes-cli#302).

    sudo resets the environment, so the run-as account's temp space is its own, and
    pytest's default basetemp is ``<tmp>/pytest-of-<user>``: every ``tmp_path`` would
    carry the account's name (``culture-fixer``, which contains ``-f``). ``TMPDIR`` and
    ``--basetemp`` point into the gate's workspace instead. ``PYTEST_ADDOPTS`` goes before
    pytest's command line, so a repo's own ``--basetemp`` in its command still wins; any
    ``addopts`` already given are kept in front. A ``PYTEST_ADDOPTS`` already in the run-as
    environment is replaced, not merged: under sudo (production) the environment is reset,
    so there is none; a repo's pytest options belong in its ``gate:`` command."""
    basetemp = shlex.quote(f"--basetemp={os.path.join(tmpdir, 'pytest')}")
    opts = f"{addopts} {basetemp}" if addopts else basetemp
    return ["env", f"TMPDIR={tmpdir}", f"PYTEST_ADDOPTS={opts}"]


def _sha_input(input: Mapping[str, Any], name: str) -> str:
    value = input.get(name)
    if not isinstance(value, str) or not _SHA_RE.match(value):
        raise _Refusal("bad_input", f"{name} must be a full lowercase hex commit SHA")
    return value


def _instruction(verdict: dict[str, Any]) -> str | None:
    sha = verdict["commit_sha"][:12]
    keep = (
        "Fix the underlying problem in the code. Do not delete, skip or weaken tests, and "
        "do not edit CI, lint, coverage or Sonar configuration or add suppression markers."
    )
    if verdict["verdict"] == FAIL:
        cmd = shlex.join(verdict["command"] or [])
        why = "timed out" if verdict["timed_out"] else f"exited {verdict['exit_code']}"
        return (
            f"The test gate failed on commit {sha}: the {verdict['phase']} command `{cmd}` "
            f"{why}. {keep}\n\nLast output:\n{verdict['output_tail']}"
        )
    if verdict["verdict"] == GUARD:
        lines = "\n".join(
            f"- {v['rule']}: {v['path']} {v['detail']}".rstrip() for v in verdict["violations"]
        )
        return (
            f"The diff guard rejected commit {sha} (rule {verdict['rule']}). Revert these "
            f"changes. {keep}\n\n{lines}"
        )
    return None


_PLAIN_MODES = frozenset({"000000", "100644"})
_MODE_WORDS = {"120000": "symlink", "160000": "submodule"}


def _non_text_changes(job: _Job, start: str, commit: str) -> list[str]:
    """Changes the text diff cannot show in full (Codex review #5): binary content, any
    file mode other than a plain 100644 (an executable bit, a symlink, a submodule
    pointer) or a mode change. Each is one ``"<path>: <why>"`` line; any of them makes the
    review material incomplete (``diff_truncated``), fail closed."""
    base = ("diff", "--no-ext-diff", "--no-textconv", "--no-renames", "-z")
    problems: list[str] = []
    numstat = job.git(*base, "--numstat", start, commit, "--").decode("utf-8", "replace")
    for entry in numstat.split("\x00"):
        added, _, rest = entry.partition("\t")
        deleted, _, path = rest.partition("\t")
        if path and added == "-" and deleted == "-":
            problems.append(f"{path}: binary change")
    raw = job.git(*base, "--raw", "--no-abbrev", start, commit, "--").decode("utf-8", "replace")
    fields = raw.split("\x00")
    for meta, path in zip(fields[0::2], fields[1::2]):
        parts = meta.lstrip(":").split()
        if len(parts) < 2 or not path:
            continue
        old, new = parts[0], parts[1]
        if old in _PLAIN_MODES and new in _PLAIN_MODES:
            continue
        word = _MODE_WORDS.get(new) or _MODE_WORDS.get(old) or "mode"
        problems.append(f"{path}: {word} change (mode {old} -> {new})")
    return problems


class GatePort:
    """ActorPort for the built-in ``gate`` code step (see the module docstring)."""

    supports_idempotency_key = True  # re-running only re-judges the same commit

    def __init__(
        self,
        store: Any,
        *,
        run_as: CommandRunner | None,
        unconfigured_reason: str = "",
        git: Callable[..., int] = run_process,
        bundle_dir: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        blocked_reason: str = "",
    ) -> None:
        self._store = store
        self._run_as = run_as
        self._blocked = blocked_reason
        self._why = unconfigured_reason or f"{RUN_AS_ENV} is not set"
        self._git = git
        self._bundle_dir = Path(
            os.path.expanduser(str(bundle_dir or DEFAULT_BUNDLE_DIR))
        ).absolute()
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_env(
        cls,
        store: Any,
        environ: Mapping[str, str] | None = None,
        *,
        no_new_privs: Callable[[], bool | None] = no_new_privs,
    ) -> GatePort:
        """The production port; a sudo prefix under ``no_new_privs`` is logged and blocked."""
        environ = os.environ if environ is None else environ
        run_as, why = run_as_from_env(environ)
        blocked = ""
        if run_as is not None and run_as.uses_sudo and no_new_privs():
            blocked = (
                f"{RUN_AS_ENV} starts with sudo but the node runs with no_new_privs set "
                f"(systemd NoNewPrivileges=true), so sudo refuses; {NO_NEW_PRIVS_FIX}"
            )
            log.error("gate: %s", blocked)
        return cls(
            store,
            run_as=run_as,
            unconfigured_reason=why,
            bundle_dir=environ.get(BUNDLE_DIR_ENV) or None,
            blocked_reason=blocked,
        )

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        try:
            return InvocationResult.completed(self._gate(input, deadline, context))
        except _Refusal as exc:
            return InvocationResult.failed(exc.message(), retryable=exc.retryable)

    # ------------------------------------------------------------------ the steps

    def _gate(
        self, input: Mapping[str, Any], deadline: datetime, context: InvocationContext
    ) -> dict[str, Any]:
        worktree = input.get("worktree")
        if not isinstance(worktree, str) or not os.path.isabs(worktree) or "\x00" in worktree:
            raise _Refusal("bad_input", "worktree must be an absolute path")
        shas = {n: _sha_input(input, n) for n in ("base_sha", "start_sha", "commit_sha")}
        config = context.config or {}
        tail_bytes = config.get("tail_bytes", DEFAULT_TAIL_BYTES)
        if not isinstance(tail_bytes, int) or not 0 < tail_bytes <= _MAX_TAIL_BYTES:
            raise _Refusal("bad_config", f"tail_bytes must be 1..{_MAX_TAIL_BYTES}")
        diff_cap = config.get("diff_max_chars", DEFAULT_DIFF_MAX_CHARS)
        if (
            not isinstance(diff_cap, int)
            or isinstance(diff_cap, bool)
            or not 0 < diff_cap <= _MAX_DIFF_CHARS
        ):
            raise _Refusal("bad_config", f"diff_max_chars must be 1..{_MAX_DIFF_CHARS}")
        if self._run_as is None:
            raise _Refusal("gate_runner_unconfigured", self._why)
        if self._blocked:
            raise _Refusal("run_as_blocked", self._blocked)
        verdict: dict[str, Any] = {
            "verdict": None,
            "rule": None,
            "violations": [],
            "phase": None,
            "command": None,
            "exit_code": None,
            "timed_out": False,
            "output_tail": "",
            "instruction": None,
            "gate": None,
            "bundle": None,
            "diff": None,
            "diff_chars": None,
            "diff_truncated": None,
            "diff_problems": None,
            **shas,
            "agent_commit_sha": shas["commit_sha"],
        }
        tmp = tempfile.mkdtemp(prefix="culture-rules-gate-")
        try:
            job = _Job(tmp, worktree, self._run_as, self._git, deadline, self._clock)
            self._import(job, shas)
            spec = self._spec(job, shas["base_sha"])
            if spec is None:
                verdict["verdict"] = NO_GATE
                verdict.update(self._built(job, shas, diff_cap))
                return verdict
            verdict["gate"] = spec.to_dict()
            violations = self._guard(job, shas, config)
            if violations:
                verdict.update(
                    verdict=GUARD,
                    rule=violations[0].rule,
                    violations=[v.to_dict() for v in violations],
                )
            else:
                self._judge(job, spec, verdict, tail_bytes)
                if verdict["verdict"] == PASS:
                    verdict.update(self._built(job, shas, diff_cap))
                    verdict["bundle"] = self._bundle(job, verdict["commit_sha"], context)
            verdict["instruction"] = _instruction(verdict)
            return verdict
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _built(self, job: _Job, shas: Mapping[str, str], cap: int) -> dict[str, Any]:
        """The gate-built commit (see :meth:`_build_commit`) as ``commit_sha``, and its diff."""
        built = self._build_commit(job, shas["start_sha"], shas["commit_sha"])
        return {"commit_sha": built, **self._diff(job, shas["start_sha"], built, cap)}

    @staticmethod
    def _build_commit(job: _Job, start: str, tip: str) -> str:
        """ONE commit made by the gate: the agent tip's tree on ``start``, with the tip's
        message, author and committer (and dates, so a re-run builds the same SHA). Only
        this commit is diffed, reviewed, bundled and pushed; the agent's own commits, and
        anything they added and later removed, never leave this machine. No agent commit
        (``tip == start``) pushes nothing new. Merges and odd ancestry are refused."""
        if tip == start:
            return start
        if job.git_rc("merge-base", "--is-ancestor", start, tip)[0] != 0:
            raise _Refusal("history_rewritten", "commit_sha does not descend from start_sha")
        if job.git("rev-list", "--min-parents=2", f"{start}..{tip}", "--").strip():
            raise _Refusal("merge_commit", "start_sha..commit_sha holds a merge")
        fields = "%an%x00%ae%x00%ad%x00%cn%x00%ce%x00%cd%x00%B"
        raw = job.git("log", "-1", "--date=raw", f"--format={fields}", tip, "--")
        an, ae, ad, cn, ce, cd, message = raw.decode("utf-8", "replace").split("\x00", 6)
        env = {
            "GIT_AUTHOR_NAME": an,
            "GIT_AUTHOR_EMAIL": ae,
            "GIT_AUTHOR_DATE": ad,
            "GIT_COMMITTER_NAME": cn,
            "GIT_COMMITTER_EMAIL": ce,
            "GIT_COMMITTER_DATE": cd,
        }
        tree = job.git("rev-parse", "--verify", f"{tip}^{{tree}}").decode().strip()
        msg = os.path.join(job.tmp, "message")
        Path(msg).write_bytes(message.rstrip("\n").encode("utf-8") + b"\n")
        with open(msg, "rb") as stdin:
            built = job.git("commit-tree", tree, "-p", start, "-F", "-", stdin=stdin, env=env)
        return built.decode().strip()

    @staticmethod
    def _diff(job: _Job, start: str, commit: str, cap: int) -> dict[str, Any]:
        """``start..commit`` as text, read in the node-verified scratch repo (no external
        diff, no textconv, no attributes), cut at ``cap`` characters (d20)."""
        raw = job.git(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "-M",
            start,
            commit,
            "--",
        )
        text = raw.decode("utf-8", errors="replace")
        problems = _non_text_changes(job, start, commit)
        if len(text) > cap:
            problems.insert(
                0, f"the diff is {len(text)} characters, over the {cap} the reviewer reads"
            )
        return {
            "diff": text[:cap],
            "diff_chars": len(text),
            "diff_truncated": bool(problems),
            "diff_problems": problems,
        }

    def _import(self, job: _Job, shas: Mapping[str, str]) -> None:
        """Stream the three commits' history out of the worktree into the scratch repo."""
        revs = os.path.join(job.tmp, "revs")
        Path(revs).write_text("".join(f"{s}\n" for s in dict.fromkeys(shas.values())))
        pack = os.path.join(job.tmp, "in.pack")
        argv = ["git", "-c", "core.fsmonitor=false", "pack-objects", "--revs", "--stdout", "-q"]
        with (
            open(revs, "rb") as stdin,
            open(pack, "wb") as out,
            tempfile.TemporaryFile(dir=job.tmp) as err,
        ):
            rc = job.fixer(
                argv,
                out,
                stdin=stdin,
                merge_stderr=False,
                timeout_code="source_timeout",
                stderr=err,
            )
            tail = _stderr_tail(err) if rc != 0 else ""
        if rc != 0:
            self._diagnose_run_as(job)
            raise _Refusal(
                "source_unavailable",
                "the worktree could not supply base_sha, start_sha and commit_sha"
                + (f" (git pack-objects exited {rc}: {tail})" if tail else f" (exit {rc})"),
            )
        job.git("init", "--bare", "--quiet", job.repo, in_repo=False)
        with open(pack, "rb") as stdin:
            if job.git_rc("index-pack", "--stdin", "--strict", stdin=stdin)[0] != 0:
                raise _Refusal("source_unavailable", "the worktree's pack is invalid")
        for name, sha in shas.items():
            if job.git_rc("cat-file", "-e", f"{sha}^{{commit}}")[0] != 0:
                raise _Refusal("source_unavailable", f"{name} is not in the worktree")

    @staticmethod
    def _diagnose_run_as(job: _Job) -> None:
        """After a failed worktree command: raise if the run-as itself cannot run ``true``.

        The probe runs in ``/`` so a missing worktree still reads as ``source_unavailable``;
        a sudo or env failure fails the probe too, with its own stderr."""
        with tempfile.TemporaryFile(dir=job.tmp) as out, tempfile.TemporaryFile(dir=job.tmp) as err:
            rc = job.fixer(
                ["true"],
                out,
                merge_stderr=False,
                timeout_code="source_timeout",
                cwd="/",
                stderr=err,
            )
            if rc == 0:
                return
            tail = _stderr_tail(err)
        if "no new privileges" in tail.lower():
            raise _Refusal("run_as_blocked", f"{tail}; {NO_NEW_PRIVS_FIX}")
        raise _Refusal(
            "run_as_failed",
            f"the run-as prefix ({RUN_AS_ENV}) could not run a command (exit {rc})"
            + (f": {tail}" if tail else ""),
        )

    def _spec(self, job: _Job, base: str) -> GateSpec | None:
        rc, kind = job.git_rc("cat-file", "-t", f"{base}:{CULTURE_YAML}")
        if rc != 0:
            return None  # no culture.yaml on the base branch: no gate
        if kind.strip() != b"blob":
            raise _Refusal("gate_invalid", f"{CULTURE_YAML} on the base branch is not a file")
        text = job.git("cat-file", "blob", f"{base}:{CULTURE_YAML}")
        try:
            return parse_gate(text.decode("utf-8"), f"{CULTURE_YAML}@{base[:12]}")
        except UnicodeDecodeError as exc:
            raise _Refusal("gate_invalid", f"{CULTURE_YAML} is not UTF-8") from exc
        except GateConfigError as exc:
            message = str(exc)
            if message.startswith("extra_missing"):
                raise _Refusal("extra_missing", "install culture-rules[yaml]") from exc
            raise _Refusal("gate_invalid", message) from exc

    def _patterns(self, config: Mapping[str, Any]) -> list[str] | None:
        name = config.get("protected_paths_variable", PROTECTED_PATHS_VARIABLE)
        if not isinstance(name, str):
            raise _Refusal("bad_config", "protected_paths_variable must be a variable name")
        value = variable_values(self._store, [name]).get(name)
        if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
            return None
        return value

    def _guard(
        self, job: _Job, shas: Mapping[str, str], config: Mapping[str, Any]
    ) -> list[Violation]:
        start, commit = shas["start_sha"], shas["commit_sha"]
        rc = job.git_rc("merge-base", "--is-ancestor", start, commit)[0]
        if rc == 1:
            return [Violation("history_rewritten", "", "commit_sha does not descend from start")]
        if rc != 0:
            raise _Refusal("git_failed", f"merge-base exited {rc}")
        if job.git("rev-list", "--min-parents=2", f"{start}..{commit}", "--").strip():
            return [Violation("merge_commit", "", "start_sha..commit_sha holds a merge")]
        patterns = self._patterns(config)
        if patterns is None:
            name = config.get("protected_paths_variable", PROTECTED_PATHS_VARIABLE)
            return [Violation("protected_paths_unset", "", f"variable {name!r} is not set")]
        diff = ("diff", "--no-ext-diff", "--no-textconv", "--no-color")
        raw = job.git(*diff, "--name-status", "-z", "-M", start, commit, "--")
        names = raw.decode("utf-8", errors="surrogateescape")
        patches = {}
        for path in changed_paths(names):  # one literal pathspec per file (-z names)
            arg = path.encode("utf-8", errors="surrogateescape").decode("utf-8", "replace")
            if arg != path:
                raise _Refusal("bad_path", "a changed path is not valid UTF-8")
            patch = job.git(*diff, "--text", "--no-renames", "-U0", start, commit, "--", path)
            patches[path] = patch.decode("utf-8", errors="replace")
        return diff_guard(names, patches, patterns)

    def _workspace(self, job: _Job) -> str:
        """The fixer's own fresh ``mktemp -d`` workspace; the caller removes it.

        It holds the checkout (``checkout/``) and the gate commands' temp space
        (``tmp/``), side by side so a repo's temp files never land in its tree."""
        rc, out = job.fixer_text(["mktemp", "-d", "-t", f"{_CHECKOUT_PREFIX}XXXXXXXXXX"], cwd="/")
        path = out.strip()
        if (
            rc != 0
            or "\n" in path
            or not os.path.isabs(path)
            or os.path.normpath(path) != path
            or not os.path.basename(path).startswith(_CHECKOUT_PREFIX)
        ):
            raise _Refusal("checkout_failed", "mktemp -d did not return a fresh directory")
        return path

    @staticmethod
    def _make_dirs(job: _Job, workspace: str) -> tuple[str, str]:
        checkout, tmp = os.path.join(workspace, "checkout"), os.path.join(workspace, "tmp")
        rc, out = job.fixer_text(["mkdir", "-m", "700", "--", checkout, tmp], cwd=workspace)
        if rc != 0:
            raise _Refusal("checkout_failed", f"mkdir failed: {out.strip()[:300]}")
        return checkout, tmp

    def _fill_checkout(self, job: _Job, path: str, sha: str) -> None:
        """Check ``sha`` out in ``path``, as the fixer user, from the node-verified pack.

        The pack reaches the fixer's ``git index-pack`` on stdin (a descriptor the node
        opened), so the fixer never reads a node path."""

        def step(argv: list[str], what: str, stdin: IO[bytes] | None = None) -> str:
            rc, out = job.fixer_text(argv, cwd=path, stdin=stdin)
            if rc != 0:
                raise _Refusal("checkout_failed", f"{what} failed: {out.strip()[:300]}")
            return out

        step(_hard_git("init", "--quiet", "--template=", "."), "git init")
        with open(os.path.join(job.tmp, "in.pack"), "rb") as pack:
            step(_hard_git("index-pack", "--stdin", "--strict"), "git index-pack", pack)
        step(
            _hard_git("-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", sha),
            "git checkout",
        )
        head = step(_hard_git("rev-parse", "--verify", "HEAD^{commit}"), "git rev-parse")
        if head.strip() != sha:
            raise _Refusal("checkout_failed", f"checkout HEAD is {head.strip()[:12]}")
        status = step(_hard_git("status", "--porcelain", "--ignored"), "git status")
        if status.strip():
            raise _Refusal("checkout_failed", f"checkout is not clean: {status.strip()[:300]}")

    def _judge(self, job: _Job, spec: GateSpec, verdict: dict[str, Any], tail_bytes: int) -> None:
        """Run the gate in a fresh checkout of the commit, never in the agent's worktree."""
        workspace = self._workspace(job)
        try:
            checkout, tmp = self._make_dirs(job, workspace)
            self._fill_checkout(job, checkout, verdict["commit_sha"])
            self._run(job, spec, verdict, tail_bytes, checkout, tmp)
        finally:
            job.remove_checkout(workspace)

    def _run(
        self,
        job: _Job,
        spec: GateSpec,
        verdict: dict[str, Any],
        tail_bytes: int,
        cwd: str,
        tmp: str,
    ) -> None:
        env = gate_env(tmp)
        for phase, commands in (("setup", spec.setup), ("test", spec.test)):
            for argv in commands:
                with tempfile.TemporaryFile(dir=job.tmp) as out:
                    rc = job.fixer([*env, *argv], out, cwd=cwd)
                    tail = _tail(out, tail_bytes)
                verdict["output_tail"] = tail
                if rc == TIMED_OUT:
                    job.left()  # past the deadline: no verdict, the attempt is spent
                if rc != 0:
                    verdict.update(
                        verdict=FAIL,
                        phase=phase,
                        command=list(argv),
                        exit_code=None if rc == TIMED_OUT else rc,
                        timed_out=rc == TIMED_OUT,
                    )
                    return
        verdict["verdict"] = PASS

    def _bundle(self, job: _Job, sha: str, context: InvocationContext) -> str:
        """A full bundle of ``sha`` for ``github.push`` in the node-owned bundle dir."""
        directory = self._bundle_dir
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        now = time.time()
        for old in directory.glob("*.bundle"):
            try:
                if now - old.stat().st_mtime > BUNDLE_TTL_S:
                    old.unlink()
            except OSError:
                pass
        job.git("update-ref", _LOCAL_REF, sha)
        safe_run = re.sub(r"[^A-Za-z0-9_.-]", "_", context.run_id)[:80]
        final = directory / f"{safe_run}-{sha[:12]}.bundle"
        partial = os.path.join(job.tmp, "out.bundle")
        job.git("bundle", "create", "--quiet", partial, _LOCAL_REF)
        shutil.move(partial, final)
        return str(final)
