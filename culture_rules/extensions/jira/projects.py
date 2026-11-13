"""One reading of a Jira App actor's ``connection``: its project allowlist and its record.

The webhook receiver, ``jira.comment`` and ``jira.read`` all limit work to
``connection.projects`` and must agree: keys compare upper-cased on both sides, and an absent
or empty list allows every project. A bare string is one project, not a list of characters,
and any other malformed value allows no project at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = ["allowed_projects", "connection_of", "project_allowed", "project_of", "well_formed"]


def project_of(issue: str) -> str:
    """The project key of an issue key: the prefix before the last ``-``."""
    return issue.rpartition("-")[0]


def well_formed(issue: Any) -> bool:
    """Whether ``issue`` is a string with a project before its last ``-``."""
    return isinstance(issue, str) and bool(project_of(issue))


def allowed_projects(connection: Mapping[str, Any]) -> frozenset[str] | None:
    """The upper-cased project keys ``connection.projects`` lists; empty allows all.

    Only an absent, ``None``, ``""`` or empty list value is empty. Any other value that is not
    a string or a list of strings (a number, a dict, a list with a non-string), or that names
    no key (``"  "``, ``[" "]``), is malformed and reads as ``None``, which allows nothing.
    """
    raw = connection.get("projects")
    if raw is None or raw == "" or raw == [] or raw == ():
        return frozenset()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list | tuple) or not all(isinstance(p, str) for p in raw):
        return None
    keys = frozenset(p.strip().upper() for p in raw if p.strip())
    return keys or None


def project_allowed(key: str, allowed: frozenset[str] | None) -> bool:
    """Whether the issue ``key`` is in ``allowed`` (always, when ``allowed`` is empty; never,
    when it is ``None``, a malformed allowlist)."""
    if allowed is None:
        return False
    return not allowed or project_of(key).upper() in allowed


def connection_of(store: Any, actor_id: str | None) -> Mapping[str, Any] | None:
    """An enabled actor of surface ``jira`` and its connection, else None."""
    if not actor_id:
        return None
    doc = store.get(ACTORS_COLLECTION, actor_id)
    if not doc or doc.get("enabled") is False:
        return None
    params = doc.get("params") or {}
    conn = params.get("connection")
    if params.get("surface") != "jira" or not isinstance(conn, Mapping):
        return None
    return conn
