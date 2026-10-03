"""Serve the packaged web build (``culture_rules/web_dist``) from the API. No Node at runtime.

When the build exists, ``install`` (called by ``create_app``) adds:

- an ASGI shim that lets the same-origin ``/api`` prefix the web app uses reach the API
  (``/api/whoami`` -> ``/whoami``), applied before authentication so the role check sees the
  real route path; the route paths in ``api/openapi.json`` do not change;
- browser navigations (``Accept: text/html``) to an unprefixed path are routed to the UI even
  where the path is also an API route (``/rules``, ``/rules/x``, ``/workflows``, ...); API
  clients (curl, the CLI, ``fetch`` under ``/api``) never send that Accept and keep the API;
- a catch-all ``GET`` that serves files from the build and falls back to ``index.html`` for
  client-side routes (``/rules/x``, ``/workflows``, ...), kept out of the OpenAPI document.

Everything, static files included, sits behind the same authentication as the API (the
Cloudflare edge attaches the credential to every same-origin request). Imported lazily, with
the ``server`` extra.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

__all__ = ["API_PREFIX", "default_web_dist", "install"]

API_PREFIX = "/api"
_FLAG = "culture_rules_api_prefixed"
_UI = "/_ui"


def default_web_dist() -> Path:
    """Where the wheel ships the build (``culture_rules/web_dist``)."""
    return Path(__file__).resolve().parent.parent / "web_dist"


class _ApiPrefix:
    """Strip ``/api`` from the request path and mark the scope so unknown paths 404."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
                scope = dict(scope)
                scope["path"] = path[len(API_PREFIX) :] or "/"
                raw = scope.get("raw_path")
                if raw and raw.startswith(API_PREFIX.encode()):
                    scope["raw_path"] = raw[len(API_PREFIX) :] or b"/"
                scope[_FLAG] = True
            elif scope["type"] == "http" and scope.get("method") in ("GET", "HEAD"):
                if b"text/html" in dict(scope.get("headers", ())).get(b"accept", b""):
                    scope = dict(scope)
                    scope["path"] = _UI + path
                    scope["raw_path"] = _UI.encode() + (scope.get("raw_path") or path.encode())
        await self.app(scope, receive, send)


def install(app: FastAPI, web_dist: Path | None) -> bool:
    """Mount ``web_dist`` on ``app`` if it holds an ``index.html``; True when mounted."""
    root = (web_dist or default_web_dist()).resolve()
    index = root / "index.html"
    if not index.is_file():
        return False

    @app.get(_UI + "/{path:path}", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        if request.scope.get(_FLAG):
            return JSONResponse(
                {"error": {"code": "not_found", "message": "no such API route", "errors": []}},
                status_code=404,
            )
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        if path and "text/html" not in request.headers.get("accept", ""):
            # not a browser navigation and not a built file: an API client asked for a path
            # that is no route, so answer like the API does instead of with the SPA page
            return JSONResponse(
                {"error": {"code": "not_found", "message": "no such route", "errors": []}},
                status_code=404,
            )
        return FileResponse(index)

    app.add_middleware(_ApiPrefix)
    return True
