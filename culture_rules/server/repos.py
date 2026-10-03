"""Configured definition repositories the API may read from and write to (standard-library only).

The server never takes a path or URL from a request: a client names one of the targets the
operator configured. ``CULTURE_RULES_REPOS`` holds them, separated by commas or newlines;
each entry is ``name=location`` or a bare ``location`` (a local path or a git remote), whose
name is then derived (``owner/repo`` for a remote, the directory name for a path). Unset means
no repositories.

Imports read through :func:`culture_rules.io.gitrepo.load_from_repo` (a throwaway clone, so
any target works); exports write through :func:`culture_rules.io.gitrepo.save_to_repo`, which
needs a *local working tree* (a target is ``writable`` only then).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["REPOS_ENV", "RepoTarget", "parse_repos", "repos_from_env"]

REPOS_ENV = "CULTURE_RULES_REPOS"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_REMOTE = re.compile(r"^[a-z][a-z0-9+.-]*://|^[^/\s]+@[^/\s]+:")


@dataclass(frozen=True)
class RepoTarget:
    """One configured repository: the name clients use and where it lives."""

    name: str
    location: str

    @property
    def is_remote(self) -> bool:
        return bool(_REMOTE.match(self.location))

    @property
    def writable(self) -> bool:
        """True for a local working tree (has a ``.git``), the only kind an export can write."""
        if self.is_remote:
            return False
        return (Path(self.location) / ".git").exists()

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "url": self.location, "writable": self.writable}


def _derive_name(location: str) -> str:
    trimmed = location.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[: -len(".git")]
    if _REMOTE.match(location):
        tail = re.split(r"[/:]", trimmed)
        return "/".join(p for p in tail[-2:] if p)
    return Path(trimmed).name or trimmed


def parse_repos(text: str) -> list[RepoTarget]:
    """Parse the ``CULTURE_RULES_REPOS`` format; blank entries are skipped."""
    out: list[RepoTarget] = []
    for raw in re.split(r"[,\n]", text or ""):
        entry = raw.strip()
        if not entry:
            continue
        name, sep, location = entry.partition("=")
        if sep and _NAME.match(name.strip()) and ":" not in name:
            out.append(RepoTarget(name.strip(), location.strip()))
        else:
            out.append(RepoTarget(_derive_name(entry), entry))
    return out


def repos_from_env(environ: Mapping[str, str] | None = None) -> list[RepoTarget]:
    """The targets configured in ``CULTURE_RULES_REPOS`` (default: none)."""
    env = os.environ if environ is None else environ
    return parse_repos(env.get(REPOS_ENV, ""))
