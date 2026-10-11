"""Resend email client: send one email (``POST /emails``) with an Idempotency-Key.

Standard library only (:mod:`urllib` through the GitHub client's transport), redirects are
never followed, and the API key is held in memory and never logged, repr'd or put in an error
message. A response body is never echoed into an error either: a failure names an HTTP status
code, nothing else.

Auth is ``Authorization: Bearer <key>``. When the caller passes an idempotency key it goes out
as ``Idempotency-Key``, so Resend sends a retried request with the same key at most once.

The transport is injectable: ``transport(method, url, headers, body, timeout) ->
(status, body_bytes)``; the default is the GitHub client's urllib transport.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from culture_rules.apps.github import Transport, urllib_transport

__all__ = ["RESEND_EMAILS_URL", "ResendClient", "ResendError"]

log = logging.getLogger(__name__)

RESEND_EMAILS_URL = "https://api.resend.com/emails"
_TIMEOUT_S = 15


class ResendError(Exception):
    """A Resend call failed. ``code`` is machine-readable; messages never hold secrets."""

    def __init__(self, code: str, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.retryable = retryable


class ResendClient:
    """One Resend API key: ``send_email``."""

    def __init__(self, *, token: str, transport: Transport | None = None) -> None:
        self._token = token
        self._transport = transport or urllib_transport

    def __repr__(self) -> str:  # never expose the key
        return f"ResendClient(url={RESEND_EMAILS_URL!r})"

    def send_email(
        self,
        *,
        sender: str,
        to: list[str],
        subject: str,
        html: str | None = None,
        text: str | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Send one email; returns ``{message_id}``. Only the given body parts are sent."""
        if not self._token:
            raise ResendError("not_configured", "no API key")
        payload: dict[str, Any] = {"from": sender, "to": list(to), "subject": subject}
        if html is not None:
            payload["html"] = html
        if text is not None:
            payload["text"] = text
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "culture-rules",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = self._post(headers, json.dumps(payload).encode())
        message_id = data.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise ResendError("bad_response", retryable=True)
        return {"message_id": message_id}

    def _post(self, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        try:
            status, raw = self._transport("POST", RESEND_EMAILS_URL, headers, body, _TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - network failure is retryable; text withheld
            raise ResendError("network_error", type(exc).__name__, retryable=True) from None
        if status >= 400:
            log.warning("resend send failed: http %s", status)
            raise ResendError(f"http_{status}", retryable=status >= 500 or status == 429)
        try:
            data = json.loads(raw.decode() or "{}")
        except ValueError:
            raise ResendError("bad_response", retryable=True) from None
        return data if isinstance(data, dict) else {}
