"""One path check for the generic file-reading steps (``json.read``, ``file.read``).

:func:`resolve_under` resolves the roots and the path (symlinks followed, the entry must
exist) and only then tests containment, so a link inside a root pointing out of it is
refused. The path may be absolute, or relative to the first configured root; a path under ANY
one of the roots is accepted. A configured root that does not exist (an unmounted share) is
skipped, as long as at least one root exists; none existing is ``path_refused``.

:func:`read_under` does the same check and then reads the file through one descriptor bound
to it: the resolved path is opened with ``O_NOFOLLOW`` (and ``O_NONBLOCK``, so a FIFO swapped
in cannot hang the step), and the opened descriptor itself must still be a regular file under
a root - read from ``/proc/self/fd`` on Linux, and else by comparing the descriptor's device
and inode with those the check saw - before a byte is read. A file swapped for a symlink (or a
directory on the way swapped for one) after the check is refused, never followed out of the
roots.

It reuses the transcript port's :func:`resolve_existing`. Failures are :class:`PathProblem`
with ``code`` ``bad_params`` (a malformed root or path), ``path_refused`` (missing, outside
every root, not a regular file, a NUL byte in the path, swapped after the check) or
``too_large`` (:func:`read_under` only). Standard library only.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from culture_rules.node.actions.transcript_file import ExcerptRefused, resolve_existing

__all__ = ["PathProblem", "read_under", "resolve_under"]

_MISSING = "the file or its root does not exist"
_PATH_REFUSED = "path_refused"
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


class PathProblem(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _resolve(value: str) -> Path:
    try:
        return resolve_existing(value, _MISSING)
    except ExcerptRefused as exc:
        raise PathProblem(exc.code, exc.detail) from exc
    except ValueError as exc:  # an embedded NUL byte, which no file name holds
        raise PathProblem(_PATH_REFUSED, "the path holds a NUL byte") from exc


def _existing_roots(root_values: Sequence[Any]) -> list[Path]:
    ok = root_values and all(
        isinstance(r, str) and os.path.isabs(r) and "\0" not in r for r in root_values
    )
    if not ok:
        raise PathProblem("bad_params", "root must be an absolute directory")
    roots = []
    for value in root_values:
        try:
            roots.append(_resolve(value))
        except PathProblem:
            continue  # a root that does not exist here (an unmounted share) is skipped
    if not roots:
        raise PathProblem(_PATH_REFUSED, "no configured root exists")
    return roots


def _checked(
    path_value: Any, root_values: Sequence[Any]
) -> tuple[Path, list[Path], os.stat_result]:
    roots = _existing_roots(root_values)
    if not isinstance(path_value, str) or not path_value:
        raise PathProblem("bad_params", "path must be a non-empty string")
    if "\0" in path_value:
        raise PathProblem(_PATH_REFUSED, "the path holds a NUL byte")
    path = _resolve(os.path.join(root_values[0], path_value))  # an absolute path_value wins
    if not any(path.is_relative_to(root) for root in roots):
        raise PathProblem(_PATH_REFUSED, "the file is not under the root")
    try:
        info = os.stat(path)  # the checked file's identity, for a later bound read
    except OSError as exc:
        raise PathProblem(_PATH_REFUSED, _MISSING) from exc
    if not stat.S_ISREG(info.st_mode):
        raise PathProblem(_PATH_REFUSED, "the path is not a file")
    return path, roots, info


def resolve_under(path_value: Any, root_values: Sequence[Any]) -> Path:
    """The regular file ``path_value`` resolved, under any one of ``root_values``."""
    return _checked(path_value, root_values)[0]


def _opened_path(fd: int) -> Path | None:
    """The path the kernel holds for ``fd`` (Linux ``/proc``), or None where unavailable."""
    try:
        return Path(os.readlink(f"/proc/self/fd/{fd}"))
    except OSError:
        return None


def _bound(fd: int, roots: list[Path], checked: os.stat_result) -> os.stat_result:
    """``fd``'s stat once it is shown to be the checked regular file under a root."""
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise PathProblem(_PATH_REFUSED, "the path is not a file")
    opened = _opened_path(fd)
    if opened is not None:
        if not any(opened.is_relative_to(root) for root in roots):
            raise PathProblem(_PATH_REFUSED, "the file changed after the check")
        return info
    if (checked.st_dev, checked.st_ino) != (info.st_dev, info.st_ino):
        raise PathProblem(_PATH_REFUSED, "the file changed after the check")
    return info


def read_under(path_value: Any, root_values: Sequence[Any], cap: int) -> bytes:
    """The bytes of the regular file ``path_value`` under any one of ``root_values``, read
    through a descriptor bound to the checked file (module doc); at most ``cap`` bytes, else
    ``too_large`` (by the stated size before reading, and again if the read finds more)."""
    path, roots, checked = _checked(path_value, root_values)
    too_large = PathProblem("too_large", f"the file is over {cap} bytes")
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError as exc:  # ELOOP: the file became a symlink after the check
        raise PathProblem(_PATH_REFUSED, "the file cannot be read") from exc
    try:
        if _bound(fd, roots, checked).st_size > cap:
            raise too_large
        os.set_blocking(fd, True)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(cap + 1)
    except OSError as exc:
        raise PathProblem(_PATH_REFUSED, "the file cannot be read") from exc
    finally:
        os.close(fd)
    if len(data) > cap:
        raise too_large
    return data
