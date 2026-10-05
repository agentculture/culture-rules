"""A minimal Discord REST client (standard library only).

Posts a message with a bot token, and lists the servers (guilds) and text channels the bot can
post to (:meth:`DiscordClient.list_targets`, for the editor's server and channel pickers).
``allowed_mentions.parse`` is empty by default, so ``@everyone``, ``@here`` and role/user
mentions in the text never ping anyone. The token and
the message payload are never logged and never appear in errors.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request  # nosec B404 - fixed https base URL, no file/custom schemes accepted
from collections.abc import Callable, Mapping, Sequence
from typing import Any

__all__ = [
    "DEFAULT_BASE_URL",
    "MAX_CONTENT",
    "TEXT_CHANNEL_TYPES",
    "DiscordClient",
    "DiscordError",
]

DEFAULT_BASE_URL = "https://discord.com/api/v10"
MAX_CONTENT = 2000
_TIMEOUT_S = 15.0
_CHANNEL_RE = re.compile(r"^\d{1,25}$")

#: ``transport(url, data, headers, timeout) -> (status, json_body, response_headers)``;
#: ``data`` is ``None`` for a GET.
Transport = Callable[
    [str, bytes | None, Mapping[str, str], float], tuple[int, Any, Mapping[str, str]]
]

#: Channel types a bot message can go to: a text channel and an announcement channel.
TEXT_CHANNEL_TYPES = (0, 5)


class DiscordError(Exception):
    """A failed Discord call. ``retryable`` follows the status; the text holds no secrets."""

    def __init__(self, message: str, *, retryable: bool, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def _urllib_transport(
    url: str, data: bytes | None, headers: Mapping[str, str], timeout: float
) -> tuple[int, Any, Mapping[str, str]]:
    request = urllib.request.Request(  # noqa: S310  # nosec B310 - https base URL
        url, data=data, headers=dict(headers), method="GET" if data is None else "POST"
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

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (culture-rules, 1)",
        }

    def _get(self, path: str) -> tuple[int, Any]:
        try:
            status, payload, _ = self._transport(
                self._base + path, None, self._headers(), self._timeout
            )
        except OSError as exc:
            raise DiscordError(f"discord unreachable ({type(exc).__name__})", retryable=True)
        return status, payload

    def _get_ok(self, path: str) -> Any:
        status, payload = self._get(path)
        if 200 <= status < 300:
            return payload
        raise DiscordError(
            f"discord returned HTTP {status}", retryable=status >= 500 or status == 429
        )

    def list_targets(self, guild_id: str | None = None) -> list[dict[str, Any]]:
        """The servers the bot is in (only ``guild_id`` when given), each with its text and
        announcement channels in Discord order; ``visible`` is whether the bot can open the
        channel (a private channel it was not added to is not)."""
        guilds = self._get_ok("/users/@me/guilds") or []
        out = []
        for guild in guilds:
            gid = str(guild.get("id", ""))
            if guild_id and gid != str(guild_id):
                continue
            channels = [
                c
                for c in self._get_ok(f"/guilds/{gid}/channels") or []
                if c.get("type") in TEXT_CHANNEL_TYPES
            ]
            channels.sort(key=lambda c: (c.get("position", 0), str(c.get("name", ""))))
            out.append(
                {
                    "id": gid,
                    "name": str(guild.get("name", "")),
                    "channels": [
                        {
                            "id": str(c["id"]),
                            "name": str(c.get("name", "")),
                            "visible": 200 <= self._get(f"/channels/{c['id']}")[0] < 300,
                        }
                        for c in channels
                    ],
                }
            )
        return out

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
        headers = self._headers()
        url = f"{self._base}/channels/{channel_id}/messages"
        try:
            status, payload, _ = self._transport(
                url, json.dumps(body).encode("utf-8"), headers, self._timeout
            )
        except OSError as exc:  # URLError and TimeoutError are OSErrors
            if isinstance(exc, TimeoutError) or isinstance(
                getattr(exc, "reason", None), TimeoutError
            ):
                # the post may have landed: a retry could duplicate the message
                raise DiscordError("discord timed out (outcome unknown)", retryable=False)
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
