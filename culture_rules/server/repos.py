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
_SCHEME_REST = frozenset("abcdefghijklmnopqrstuvwxyz0123456789+.-")


def _has_url_scheme(location: str) -> bool:
    """``scheme://...`` with a lower-case RFC 3986 scheme (``https``, ``git+ssh``, ...)."""
    scheme, sep, _ = location.partition("://")
    return bool(
        sep and scheme and "a" <= scheme[0] <= "z" and all(ch in _SCHEME_REST for ch in scheme)
    )


def _is_scp_like(location: str) -> bool:
    """scp-style ``user@host:path``: a non-empty user, then a non-empty host, then ``:``.

    Only the leading run of characters that are neither ``/`` nor whitespace counts, so a
    path such as ``dir/user@host:x`` is not a remote. Scanned once, without backtracking.
    """
    head_len = len(location)
    for i, ch in enumerate(location):
        if ch == "/" or ch.isspace():
            head_len = i
            break
    head = location[:head_len]
    at = head.find("@", 1)  # the earliest @ with a user before it
    return at != -1 and head.rfind(":") > at + 1  # the latest : with a host before it


def _is_remote(location: str) -> bool:
    return _has_url_scheme(location) or _is_scp_like(location)


@dataclass(frozen=True)
class RepoTarget:
    """One configured repository: the name clients use and where it lives."""

    name: str
    location: str

    @property
    def is_remote(self) -> bool:
        return _is_remote(self.location)

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
    if _is_remote(location):
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
