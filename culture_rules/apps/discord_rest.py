"""A minimal Discord REST client (standard library only).

Posts a message with a bot token. ``allowed_mentions.parse`` is empty by default, so
``@everyone``, ``@here`` and role/user mentions in the text never ping anyone. The token and
the message payload are never logged and never appear in errors.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request  # nosec B404 - fixed https base URL, no file/custom schemes accepted
from collections.abc import Callable, Mapping, Sequence
from typing import Any

__all__ = ["DEFAULT_BASE_URL", "MAX_CONTENT", "DiscordClient", "DiscordError"]

DEFAULT_BASE_URL = "https://discord.com/api/v10"
MAX_CONTENT = 2000
_TIMEOUT_S = 15.0
_CHANNEL_RE = re.compile(r"^\d{1,25}$")

#: ``transport(url, data, headers, timeout) -> (status, json_body, response_headers)``
Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, Any, Mapping[str, str]]]


class DiscordError(Exception):
    """A failed Discord call. ``retryable`` follows the status; the text holds no secrets."""

    def __init__(self, message: str, *, retryable: bool, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def _urllib_transport(
    url: str, data: bytes, headers: Mapping[str, str], timeout: float
) -> tuple[int, Any, Mapping[str, str]]:
    request = urllib.request.Request(  # noqa: S310  # nosec B310 - https base URL
        url, data=data, headers=dict(headers), method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            return resp.status, _json(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, _json(exc.read()), dict(exc.headers or {})


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:  # UnicodeDecodeError is a ValueError
        return {}


class DiscordClient:
    """Posts messages to channels as a bot."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport | None = None,
        timeout: float = _TIMEOUT_S,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._transport = transport or _urllib_transport
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"DiscordClient(base_url={self._base!r})"

    def post_message(
        self,
        channel_id: str,
        text: str,
        *,
        allowed_mentions_parse: Sequence[str] = (),
    ) -> dict[str, str]:
        """Post ``text`` to ``channel_id``; returns ``{"message_id": ...}``."""
        if not isinstance(channel_id, str) or not _CHANNEL_RE.match(channel_id):
            raise DiscordError("channel must be a numeric Discord channel id", retryable=False)
        body = {
            "content": str(text)[:MAX_CONTENT],
            "allowed_mentions": {"parse": list(allowed_mentions_parse)},
        }
        headers = {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (culture-rules, 1)",
        }
        url = f"{self._base}/channels/{channel_id}/messages"
        try:
            status, payload, _ = self._transport(
                url, json.dumps(body).encode("utf-8"), headers, self._timeout
            )
        except OSError as exc:  # URLError and TimeoutError are OSErrors
            raise DiscordError(f"discord unreachable ({type(exc).__name__})", retryable=True)
        if 200 <= status < 300:
            return {"message_id": str((payload or {}).get("id", ""))}
        if status == 429:
            retry_after = _retry_after(payload)
            raise DiscordError(
                f"discord rate limited (retry_after={retry_after})",
                retryable=True,
                retry_after=retry_after,
            )
        raise DiscordError(f"discord returned HTTP {status}", retryable=status >= 500)


def _retry_after(payload: Any) -> float | None:
    value = payload.get("retry_after") if isinstance(payload, Mapping) else None
    return float(value) if isinstance(value, (int, float)) else None
