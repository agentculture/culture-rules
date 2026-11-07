"""One path check for the generic file-reading steps (``json.read``, ``file.read``).

:func:`resolve_under` resolves the roots and the path (symlinks followed, the entry must
exist) and only then tests containment, so a link inside a root pointing out of it is
refused. The path may be absolute, or relative to the first root; a path under ANY one of the
roots is accepted. It reuses the transcript port's :func:`resolve_existing`. Failures are
:class:`PathProblem` with ``code`` ``bad_params`` (a malformed root or path) or
``path_refused`` (missing, outside every root, not a regular file). Standard library only.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from culture_rules.node.actions.transcript_file import ExcerptRefused, resolve_existing

__all__ = ["PathProblem", "resolve_under"]

_MISSING = "the file or its root does not exist"


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


def resolve_under(path_value: Any, root_values: Sequence[Any]) -> Path:
    """The regular file ``path_value`` resolved, under any one of ``root_values``."""
    if not root_values or not all(isinstance(r, str) and os.path.isabs(r) for r in root_values):
        raise PathProblem("bad_params", "root must be an absolute directory")
    if not isinstance(path_value, str) or not path_value:
        raise PathProblem("bad_params", "path must be a non-empty string")
    roots = [_resolve(r) for r in root_values]
    path = _resolve(os.path.join(roots[0], path_value))  # an absolute path_value wins
    if not any(path.is_relative_to(root) for root in roots):
        raise PathProblem("path_refused", "the file is not under the root")
    if not path.is_file():
        raise PathProblem("path_refused", "the path is not a file")
    return path
