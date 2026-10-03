"""Runtime refusal of registered commands that evaluate inline code (defence in depth).

Inline script text is admin-only (:func:`culture_rules.actors.code.check_inline_allowed`).
A registered command such as ``["/bin/sh", "-c", "{script}"]`` would sidestep that rule:
the step's *argument* becomes shell text. :func:`inline_eval_reason` inspects the bound
argv a :class:`~culture_rules.actors.code.CodeRunner` is about to start and names the
problem when the command is a shell or interpreter told to evaluate code from its
arguments:

* shells (``sh``, ``bash``, ``zsh``, ``dash``, ``ksh``, ...) with ``-c``, alone or in a
  cluster such as ``-ec``;
* interpreters with their eval flag: ``python*`` ``-c``, ``node`` ``-e``/``-p``/
  ``--eval``/``--print``, ``perl`` ``-e``/``-E``, ``ruby`` ``-e``, ``php`` ``-r``,
  ``lua`` ``-e``;
* the same behind wrappers (``env``, ``sudo``, ``nice``, ``timeout``, ...), plus
  ``env -S`` (which splits a string into a command line).

The check runs on the *bound* argv, so an executable or an eval flag supplied through an
argument is caught too. It is deliberately fail-closed: an eval flag anywhere after the
interpreter refuses the command, even where the interpreter would pass it on to a script
(give such a script a shebang and register it directly instead). Standard library only.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

__all__ = ["inline_eval_reason"]

_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "csh", "tcsh"})

#: Interpreter -> (short eval letters, long eval flags).
_EVAL_FLAGS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    **{name: (frozenset("c"), frozenset({"--command"})) for name in _SHELLS},
    "python": (frozenset("c"), frozenset()),
    "pypy": (frozenset("c"), frozenset()),
    "node": (frozenset("ep"), frozenset({"--eval", "--print"})),
    "nodejs": (frozenset("ep"), frozenset({"--eval", "--print"})),
    "bun": (frozenset("ep"), frozenset({"--eval", "--print"})),
    "perl": (frozenset("eE"), frozenset()),
    "ruby": (frozenset("e"), frozenset()),
    "php": (frozenset("r"), frozenset()),
    "lua": (frozenset("e"), frozenset()),
    "su": (frozenset("c"), frozenset({"--command", "--session-command"})),
    "runuser": (frozenset("c"), frozenset({"--command", "--session-command"})),
}

#: Commands that run another command from their arguments.
_WRAPPERS = frozenset(
    {
        "env",
        "sudo",
        "doas",
        "nice",
        "nohup",
        "setsid",
        "timeout",
        "stdbuf",
        "ionice",
        "chrt",
        "taskset",
        "time",
        "command",
        "exec",
        "busybox",
        "xargs",
        "flock",
        "chroot",
    }
)


def _is_version_char(ch: str) -> bool:
    return ch == "." or ch.isdecimal()


def _strip_version(base: str) -> str:
    """``base`` without its trailing run of digits and dots, in linear time.

    Mirrors ``re.sub(r"[\\d.]+$", "", base)`` (``\\d`` is any Unicode decimal digit, and
    ``$`` also matches before one trailing newline) without that pattern's quadratic
    backtracking on a long digit run that does not end the string.
    """
    head, newline = (base[:-1], "\n") if base.endswith("\n") else (base, "")
    end = len(head)
    while end and _is_version_char(head[end - 1]):
        end -= 1
    return head[:end] + newline


def _name(arg: str) -> str:
    """``/usr/bin/python3.12`` -> ``python``: basename without a version suffix."""
    base = os.path.basename(arg)
    return _strip_version(base) or base


def _is_eval_flag(arg: str, letters: frozenset[str], longs: frozenset[str]) -> bool:
    if arg.startswith("--"):
        return arg.split("=", 1)[0] in longs
    if arg.startswith("-") and len(arg) > 1:
        return any(letter in letters for letter in arg[1:])
    return False


def _env_splits(args: Sequence[str]) -> bool:
    """Whether ``env``'s leading options include ``-S``/``--split-string``."""
    for arg in args:
        if not arg.startswith("-") or arg == "--":
            return False
        if arg.startswith("--split-string") or (not arg.startswith("--") and "S" in arg):
            return True
    return False


def _interpreter_at(argv: Sequence[str]) -> int | None:
    """Index of the interpreter the command runs (directly or behind wrappers), if any."""
    if _name(argv[0]) in _EVAL_FLAGS:
        return 0
    if _name(argv[0]) not in _WRAPPERS:
        return None
    for index, arg in enumerate(argv[1:], start=1):
        if _name(arg) in _EVAL_FLAGS:
            return index
    return None


def inline_eval_reason(argv: Sequence[str]) -> str | None:
    """Why ``argv`` evaluates inline code, or None when it does not."""
    if not argv:
        return None
    if _name(argv[0]) in _WRAPPERS:
        for at, arg in enumerate(argv):
            if _name(arg) == "env" and _env_splits(argv[at + 1 :]):
                return f"{arg!r} -S splits its argument into a command line"
    index = _interpreter_at(argv)
    if index is None:
        return None
    letters, longs = _EVAL_FLAGS[_name(argv[index])]
    for arg in argv[index + 1 :]:
        if _is_eval_flag(arg, letters, longs):
            return f"{argv[index]!r} {arg.split('=', 1)[0]!r} evaluates its argument as code"
    return None
