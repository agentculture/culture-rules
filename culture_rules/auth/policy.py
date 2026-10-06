"""Which role each HTTP route needs (viewer < editor < admin). Pure; the server enforces it.

- viewer: every read (``GET``/``HEAD``), except the service-token list; this includes a
  rule's contextual history (``GET /rules/{id}/history``) and the live feed;
- editor: create/update/enable/disable/delete/restore of definitions, import, export into a
  configured repository (``POST /export``, dry-run included), runs (start/cancel, and a direct
  workflow run) and answering asks;
- admin: purge, the data migrations (typeless rules, run-id backfill), service tokens, and
  engine/machine containment (pause/resume/drain/undrain), and writing a shared variable
  (``PUT /variables/{name}``, via the fail-closed default); variable reads are viewer.

Saving a workflow step that carries inline script text is admin-only too, and so is adding or
changing a runner actor's command registry (``params.commands``), but those depend on the body
(and the stored version), so the save routes check them (``culture_rules.auth.guards``).
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
    re.compile(r"^/rules/migrate-typeless$"),
    re.compile(r"^/runs/backfill-ids$"),
)
# read-only operations that take a request body (no write happens)
_VIEWER_POSTS = (re.compile(r"^/replay$"),)
_EDITOR_PATTERNS = {
    "POST": (
        re.compile(rf"^/({_KINDS})$"),
        re.compile(rf"^/({_KINDS})/[^/]+/(enable|disable|restore)$"),
        re.compile(r"^/import$"),
        re.compile(r"^/export$"),
        re.compile(r"^/runs$"),
        re.compile(r"^/workflows/[^/]+/run$"),
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
    if method == "POST" and any(p.match(path) for p in _VIEWER_POSTS):
        return "viewer"
    if any(p.match(path) for p in _EDITOR_PATTERNS.get(method, ())):
        return "editor"
    return "admin"
