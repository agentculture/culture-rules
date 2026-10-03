"""Which role each HTTP route needs (viewer < editor < admin). Pure; the server enforces it.

- viewer: every read (``GET``/``HEAD``), except the service-token list;
- editor: create/update/enable/disable/delete/restore of definitions, import, runs
  (start/cancel) and answering asks;
- admin: purge, service tokens, and engine/machine containment (pause/resume/drain/undrain).

Saving a workflow step that carries inline script text is admin-only too, but that depends on
the body, so the save route checks it (``culture_rules.actors.code.check_step_inline_allowed``).
Any mutation not listed here needs admin: the matrix fails closed.
"""

from __future__ import annotations

import re

__all__ = ["required_role"]

_KINDS = "rules|workflows|actors|machines"
_ADMIN_PATTERNS = (
    re.compile(rf"^/({_KINDS})/[^/]+/purge$"),
    re.compile(r"^/service-tokens(/[^/]+)?$"),
    re.compile(r"^/machines/[^/]+/(drain|undrain)$"),
    re.compile(r"^/controls/(pause|resume)$"),
)
_EDITOR_PATTERNS = {
    "POST": (
        re.compile(rf"^/({_KINDS})$"),
        re.compile(rf"^/({_KINDS})/[^/]+/(enable|disable|restore)$"),
        re.compile(r"^/import$"),
        re.compile(r"^/runs$"),
        re.compile(r"^/runs/[^/]+/cancel$"),
        re.compile(r"^/asks/[^/]+/answer$"),
    ),
    "PUT": (re.compile(rf"^/({_KINDS})/[^/]+$"),),
    "DELETE": (re.compile(rf"^/({_KINDS})/[^/]+$"),),
}


def required_role(method: str, path: str) -> str:
    """The lowest role allowed to call ``method path``."""
    method = method.upper()
    path = path.rstrip("/") or "/"
    if any(p.match(path) for p in _ADMIN_PATTERNS):
        return "admin"
    if method in ("GET", "HEAD", "OPTIONS"):
        return "viewer"
    if any(p.match(path) for p in _EDITOR_PATTERNS.get(method, ())):
        return "editor"
    return "admin"
