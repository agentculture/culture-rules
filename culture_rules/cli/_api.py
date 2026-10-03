"""Builds the HTTP API client the CLI talks through (the CLI's only data path).

Base URL: ``--api-url`` or ``CULTURE_RULES_API_URL`` (default ``http://127.0.0.1:8765``).
Credentials: ``CULTURE_RULES_TOKEN`` is a bearer service token, or a ``grant:<NAME>``
reference resolved through :mod:`culture_rules.actors.secrets` (the token itself is never
accepted on argv). ``CULTURE_RULES_IDENTITY`` sets the dev identity header, which the server
honours only when run with its insecure dev identity enabled.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from culture_rules.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from culture_rules.client.http import DEFAULT_API_URL, ApiClient, ApiError, ApiUnreachable

__all__ = ["call", "make_client"]

ENV_URL = "CULTURE_RULES_API_URL"
ENV_TOKEN = "CULTURE_RULES_TOKEN"  # nosec B105 - the name of the variable, not a secret
ENV_IDENTITY = "CULTURE_RULES_IDENTITY"


def _token() -> str | None:
    raw = (os.environ.get(ENV_TOKEN) or "").strip()
    if not raw:
        return None
    from culture_rules.actors import secrets  # noqa: PLC0415

    if secrets.is_secret_ref(raw):
        try:
            return secrets.resolve(raw)
        except secrets.SecretError as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"cannot resolve {ENV_TOKEN}: {exc}",
                remediation="check the grant reference with: grant get <NAME>",
            ) from exc
    return raw


def make_client(api_url: str | None = None) -> ApiClient:
    """The API client for this invocation (tests replace this function with a fake transport)."""
    url = api_url or os.environ.get(ENV_URL) or DEFAULT_API_URL
    return ApiClient(url, token=_token(), identity=os.environ.get(ENV_IDENTITY) or None)


def call(fn: Callable[[], Any]) -> Any:
    """Run an API call, translating client failures into :class:`CliError`."""
    try:
        return fn()
    except ApiUnreachable as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=str(exc),
            remediation=f"start the API with 'culture-rules serve' or set {ENV_URL}",
        ) from exc
    except ApiError as exc:
        detail = "; ".join(
            f"{e.get('path') or '-'}: {e.get('message', '')}" for e in exc.errors[:5]
        )
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{exc.message} ({exc.code}, HTTP {exc.status})"
            + (f" [{detail}]" if detail else ""),
            remediation=_hint(exc),
        ) from exc


def _hint(exc: ApiError) -> str:
    if exc.status in (401, 403):
        return f"check {ENV_TOKEN}; the verb needs a higher role"
    if exc.status == 404:
        return "list what exists with the noun's 'list' verb"
    if exc.status == 409:
        return "the item is in a different state; inspect it with 'show'"
    if exc.status == 422:
        return "fix the listed fields and retry; see 'culture-rules explain <noun> <verb>'"
    return "see 'culture-rules explain <noun> <verb>'"
