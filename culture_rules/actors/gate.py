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
appends ``env -C <worktree> -- <argv>``, so the command runs in the worktree, as that
user, as an argv list with ``shell=False`` end to end. **Unset, the gate refuses**
(``gate_runner_unconfigured``) rather than silently running as the node user. A prefix
whose first word is a remote or login shell (``ssh``, ``su``, ``sh``, ...) is refused: those
join argv into a shell command line. Trade-off: the node user can run anything as the fixer
user (that is the grant; the fixer user is the less privileged of the two, and the reverse
is impossible), and on a timeout the node can signal only ``sudo`` itself (it relays
``SIGTERM`` to the command); processes that ignore it are not the node's to kill. Commands
get the environment the prefix gives them (``sudo`` resets it), not the node's.

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
removed (so moving or reformatting an existing line is not flagged). After the guard the
worktree itself is checked through the runner: ``worktree_moved`` when its ``HEAD`` is not
``commit_sha`` and ``worktree_dirty`` when tracked files differ from it, since tests must
judge exactly the commit that would be pushed.

Re-running the gate on the same commit is harmless (it only re-judges), so the port
declares ``supports_idempotency_key``. Standard-library only (the ``yaml`` extra parses
``culture.yaml``, imported lazily).
"""

from __future__ import annotations

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

    stdout (with stderr merged when asked, else discarded) goes to ``stdout``; ``stdin``
    is closed unless given. Returns :data:`TIMED_OUT` / :data:`UNAVAILABLE`."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        timeout: float,
        stdout: IO[bytes],
        stdin: IO[bytes] | None = None,
        merge_stderr: bool = False,
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
) -> int:
    """One process group, argv list, ``shell=False``; the whole group dies on timeout."""
    try:
        proc = subprocess.Popen(  # nosec B603 - argv list, shell=False
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else _base_env(),
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
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
    ) -> int:
        full = [*self.prefix, "env", "-C", cwd, "--", *argv]
        return self._run(
            full, timeout=timeout, stdout=stdout, stdin=stdin, merge_stderr=merge_stderr
        )


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
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def is_test_path(path: str) -> bool:
    return bool(_TEST_FILE.search(path))


def _unquote(path: str) -> str:
    """git's C-style quoted path (``"a\\tb"``) back to text."""
    if not (len(path) >= 2 and path[0] == '"' and path[-1] == '"'):
        return path
    raw, out, i = path[1:-1], bytearray(), 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt in "01234567" and re.match(r"[0-7]{3}", raw[i + 1 : i + 4]):
                out.append(int(raw[i + 1 : i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES.get(nxt, ord(nxt)))
            i += 2
            continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def _strip_side(path: str) -> str:
    path = _unquote(path)
    return path[2:] if path[:2] in ("a/", "b/") else path


def _patch_lines(patch: str) -> Iterable[tuple[str, str, str]]:
    """``(path, "+"|"-", text)`` for every added/removed line of a ``-U0`` patch."""
    old = new = ""
    lines = patch.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith("--- "):
            old = "" if line[4:] == "/dev/null" else _strip_side(line[4:])
        elif line.startswith("+++ "):
            new = "" if line[4:] == "/dev/null" else _strip_side(line[4:])
        elif (m := _HUNK.match(line)) is not None:
            left = 1 if m.group(1) is None else int(m.group(1))
            right = 1 if m.group(2) is None else int(m.group(2))
            while (left > 0 or right > 0) and i < len(lines):
                body = lines[i]
                i += 1
                if body.startswith("-"):
                    left -= 1
                    yield old or new, "-", body[1:]
                elif body.startswith("+"):
                    right -= 1
                    yield new or old, "+", body[1:]
                elif body.startswith(" "):
                    left, right = left - 1, right - 1
                # "\ No newline at end of file" counts as neither side


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


def diff_guard(name_status: str, patch: str, patterns: Sequence[str]) -> list[Violation]:
    """Every diff-guard violation in one diff (``--name-status -z`` and ``-U0`` patch)."""
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
    lines = list(_patch_lines(patch))
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

    def git(self, *args: str, stdin: IO[bytes] | None = None, in_repo: bool = True) -> bytes:
        """Run git on the scratch repo; returns stdout, raising on a non-zero exit."""
        rc, out = self.git_rc(*args, stdin=stdin, in_repo=in_repo)
        if rc != 0:
            raise _Refusal("git_failed", f"git {args[0]} exited {rc}")
        return out

    def git_rc(
        self, *args: str, stdin: IO[bytes] | None = None, in_repo: bool = True
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
                env=_git_env(self.tmp),
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
    ) -> int:
        """Run ``argv`` in the worktree as the fixer user; returns its exit code.

        With ``timeout_code``, a timeout is a retryable refusal of that code instead of
        :data:`TIMED_OUT` (only a gate command's own timeout is a verdict)."""
        rc = self._run_as(
            list(argv),
            cwd=self.worktree,
            timeout=self.left(),
            stdout=out,
            stdin=stdin,
            merge_stderr=merge_stderr,
        )
        if rc == UNAVAILABLE:
            raise _Refusal("gate_runner_unavailable", "the run-as command could not start")
        if rc == TIMED_OUT and timeout_code is not None:
            self.left()
            raise _Refusal(timeout_code, "timed out", retryable=True)
        return rc

    def fixer_text(self, argv: Sequence[str]) -> tuple[int, str]:
        with tempfile.TemporaryFile(dir=self.tmp) as out:
            rc = self.fixer(argv, out, timeout_code="worktree_timeout")
            out.seek(0)
            return rc, out.read().decode("utf-8", errors="replace")


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
    ) -> None:
        self._store = store
        self._run_as = run_as
        self._why = unconfigured_reason or f"{RUN_AS_ENV} is not set"
        self._git = git
        self._bundle_dir = Path(
            os.path.expanduser(str(bundle_dir or DEFAULT_BUNDLE_DIR))
        ).absolute()
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_env(cls, store: Any, environ: Mapping[str, str] | None = None) -> GatePort:
        environ = os.environ if environ is None else environ
        run_as, why = run_as_from_env(environ)
        return cls(
            store,
            run_as=run_as,
            unconfigured_reason=why,
            bundle_dir=environ.get(BUNDLE_DIR_ENV) or None,
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
        if self._run_as is None:
            raise _Refusal("gate_runner_unconfigured", self._why)
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
            **shas,
        }
        tmp = tempfile.mkdtemp(prefix="culture-rules-gate-")
        try:
            job = _Job(tmp, worktree, self._run_as, self._git, deadline, self._clock)
            self._import(job, shas)
            spec = self._spec(job, shas["base_sha"])
            if spec is None:
                verdict["verdict"] = NO_GATE
                return verdict
            verdict["gate"] = spec.to_dict()
            violations = self._guard(job, shas, config) or self._worktree(job, shas)
            if violations:
                verdict.update(
                    verdict=GUARD,
                    rule=violations[0].rule,
                    violations=[v.to_dict() for v in violations],
                )
            else:
                self._run(job, spec, verdict, tail_bytes)
                if verdict["verdict"] == PASS:
                    verdict["bundle"] = self._bundle(job, shas["commit_sha"], context)
            verdict["instruction"] = _instruction(verdict)
            return verdict
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _import(self, job: _Job, shas: Mapping[str, str]) -> None:
        """Stream the three commits' history out of the worktree into the scratch repo."""
        revs = os.path.join(job.tmp, "revs")
        Path(revs).write_text("".join(f"{s}\n" for s in dict.fromkeys(shas.values())))
        pack = os.path.join(job.tmp, "in.pack")
        argv = ["git", "-c", "core.fsmonitor=false", "pack-objects", "--revs", "--stdout", "-q"]
        with open(revs, "rb") as stdin, open(pack, "wb") as out:
            rc = job.fixer(
                argv, out, stdin=stdin, merge_stderr=False, timeout_code="source_timeout"
            )
        if rc != 0:
            raise _Refusal(
                "source_unavailable",
                "the worktree could not supply base_sha, start_sha and commit_sha",
            )
        job.git("init", "--bare", "--quiet", job.repo, in_repo=False)
        with open(pack, "rb") as stdin:
            if job.git_rc("index-pack", "--stdin", "--strict", stdin=stdin)[0] != 0:
                raise _Refusal("source_unavailable", "the worktree's pack is invalid")
        for name, sha in shas.items():
            if job.git_rc("cat-file", "-e", f"{sha}^{{commit}}")[0] != 0:
                raise _Refusal("source_unavailable", f"{name} is not in the worktree")

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
        patterns = self._patterns(config)
        if patterns is None:
            name = config.get("protected_paths_variable", PROTECTED_PATHS_VARIABLE)
            return [Violation("protected_paths_unset", "", f"variable {name!r} is not set")]
        diff = ("diff", "--no-ext-diff", "--no-textconv", "--no-color")
        names = job.git(*diff, "--name-status", "-z", "-M", start, commit, "--")
        patch = job.git(*diff, "--text", "--no-renames", "-U0", start, commit, "--")
        return diff_guard(
            names.decode("utf-8", errors="replace"),
            patch.decode("utf-8", errors="replace"),
            patterns,
        )

    def _worktree(self, job: _Job, shas: Mapping[str, str]) -> list[Violation]:
        git = ["git", "-c", "core.fsmonitor=false"]
        rc, head = job.fixer_text([*git, "rev-parse", "--verify", "HEAD^{commit}"])
        if rc != 0:
            raise _Refusal("worktree_unavailable", "cannot read the worktree's HEAD")
        if head.strip() != shas["commit_sha"]:
            detail = f"HEAD is {head.strip()[:12]}, not commit_sha"
            return [Violation("worktree_moved", "", detail)]
        rc, dirty = job.fixer_text([*git, "status", "--porcelain", "--untracked-files=no"])
        if rc != 0:
            raise _Refusal("worktree_unavailable", "cannot read the worktree's status")
        if dirty.strip():
            return [Violation("worktree_dirty", "", dirty.strip()[:500])]
        return []

    def _run(self, job: _Job, spec: GateSpec, verdict: dict[str, Any], tail_bytes: int) -> None:
        for phase, commands in (("setup", spec.setup), ("test", spec.test)):
            for argv in commands:
                with tempfile.TemporaryFile(dir=job.tmp) as out:
                    rc = job.fixer(argv, out)
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
