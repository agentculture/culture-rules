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
import time
import urllib.error
import urllib.parse
import urllib.request  # nosec B404 - fixed https base URL, no file/custom schemes accepted
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from culture_rules.model.discord_filters import emoji_ok

__all__ = [
    "DEFAULT_BASE_URL",
    "MAX_CONTENT",
    "TEXT_CHANNEL_TYPES",
    "DiscordClient",
    "DiscordError",
    "guild_ids",
]

DEFAULT_BASE_URL = "https://discord.com/api/v10"
MAX_CONTENT = 2000
_TIMEOUT_S = 15.0
_CHANNEL_RE = re.compile(r"^\d{1,25}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{1,512}$")
#: A GET that gets a 429 is retried this many times, waiting ``retry_after`` (capped) each time.
_MAX_RETRIES = 3
_MAX_WAIT_S = 5.0
_DEFAULT_WAIT_S = 1.0
_PAGE_SIZE = 100
_MAX_PAGES = 10


def guild_ids(value: Any) -> frozenset[str] | None:
    """A Discord connection's ``guild_id`` as a set of guild ids, or ``None`` for every guild.

    ``value`` is one id (a string or a number, as before) or a list/tuple/set of ids. Unset
    (``None``), an empty string and an empty list all mean *every guild the bot is in* and give
    ``None``; blank entries inside a list are dropped, so a list of only blanks is unset too.
    """
    if value is None:
        return None
    items = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    ids = frozenset(text for text in (str(v).strip() for v in items if v is not None) if text)
    return ids or None


#: ``transport(url, data, headers, timeout) -> (status, json_body, response_headers)``;
#: ``data`` is ``None`` for a GET.
#: A PUT (``add_reaction``) passes ``method="PUT"`` as a fifth keyword argument; a GET or POST
#: does not, so a four-argument transport still serves them.
Transport = Callable[..., tuple[int, Any, Mapping[str, str]]]

#: Channel types a bot message can go to: a text channel and an announcement channel.
TEXT_CHANNEL_TYPES = (0, 5)


class DiscordError(Exception):
    """A failed Discord call. ``retryable`` follows the status; the text holds no secrets."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        retry_after: float | None = None,
        expired: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        #: an interaction webhook Discord no longer accepts (401/404): the token ran out
        self.expired = expired


def _urllib_transport(
    url: str,
    data: bytes | None,
    headers: Mapping[str, str],
    timeout: float,
    method: str | None = None,
) -> tuple[int, Any, Mapping[str, str]]:
    request = urllib.request.Request(  # noqa: S310  # nosec B310 - https base URL
        url,
        data=data,
        headers=dict(headers),
        method=method or ("GET" if data is None else "POST"),
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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._transport = transport or _urllib_transport
        self._timeout = timeout
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"DiscordClient(base_url={self._base!r})"

    def _headers(self, *, bot: bool = True) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (culture-rules, 1)",
        }
        if bot:  # an interaction webhook is authorised by its own token, never the bot's
            headers["Authorization"] = f"Bot {self._token}"
        return headers

    def _get(self, path: str) -> tuple[int, Any]:
        """GET ``path``; a 429 is retried after its ``retry_after`` (each wait capped at
        ``_MAX_WAIT_S``) up to ``_MAX_RETRIES`` times, then it is returned as is."""
        return self._send(path)

    def _send(
        self,
        path: str,
        method: str | None = None,
        *,
        body: bytes = b"",
        bot: bool = True,
    ) -> tuple[int, Any]:
        """GET (or, with ``method``, that verb with ``body``, empty by default) ``path`` with
        the 429 retry of :meth:`_get`; ``bot=False`` leaves the bot token out of the headers."""
        extra = {"method": method} if method else {}
        for attempt in range(_MAX_RETRIES + 1):
            try:
                status, payload, _ = self._transport(
                    self._base + path,
                    body if method else None,
                    self._headers(bot=bot),
                    self._timeout,
                    **extra,
                )
            except OSError as exc:
                raise DiscordError(f"discord unreachable ({type(exc).__name__})", retryable=True)
            if status != 429 or attempt == _MAX_RETRIES:
                return status, payload
            wait = _retry_after(payload)
            self._sleep(min(_DEFAULT_WAIT_S if wait is None else max(wait, 0.0), _MAX_WAIT_S))
        raise AssertionError("unreachable")  # pragma: no cover

    def _get_ok(self, path: str) -> Any:
        status, payload = self._get(path)
        if 200 <= status < 300:
            return payload
        raise DiscordError(
            f"discord returned HTTP {status}", retryable=status >= 500 or status == 429
        )

    def list_targets(self, guild_id: Any = None) -> list[dict[str, Any]]:
        """The servers the bot is in (only those ``guild_id`` names when given: one id or a
        collection of ids, see :func:`guild_ids`), each with its text and announcement channels
        in Discord order; ``visible`` is whether the bot can open the channel (a private channel
        it was not added to is not)."""
        wanted = guild_ids(guild_id)
        guilds = self._get_ok("/users/@me/guilds") or []
        out = []
        for guild in guilds:
            gid = str(guild.get("id", ""))
            if wanted is not None and gid not in wanted:
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

    def channel_guild(self, channel_id: str) -> str | None:
        """The id of the guild a channel belongs to (``None`` for a DM)."""
        _check_id(channel_id, "channel")
        payload = self._get_ok(f"/channels/{channel_id}") or {}
        guild = payload.get("guild_id") if isinstance(payload, Mapping) else None
        return str(guild) if guild else None

    def get_message(
        self, channel_id: str, message_id: str, *, guild_id: str | None = None
    ) -> dict[str, Any]:
        """One message, untruncated: ``id``, ``channel_id``, ``content``, ``author_id``,
        ``author_name``, ``url`` and ``attachments`` (``id``, ``filename``, ``content_type``,
        ``size``, ``url``). Discord's GET does not reliably return the guild, so the jump
        ``url`` is built from the ``guild_id`` keyword (``"@me"`` for a DM) and is ``None``
        when it is not given."""
        _check_id(channel_id, "channel")
        _check_id(message_id, "message")
        raw = self._get_ok(f"/channels/{channel_id}/messages/{message_id}")
        return _shape_message(raw, channel_id, guild_id)

    def history(
        self,
        channel_id: str,
        after: str,
        limit: int = 100,
        *,
        guild_id: str | None = None,
        max_pages: int = _MAX_PAGES,
    ) -> list[dict[str, Any]]:
        """Messages after message ``after``, oldest first, at most ``limit`` of them and at
        most ``max_pages`` requests (each page is up to 100 messages)."""
        _check_id(channel_id, "channel")
        _check_id(after, "message")
        out: list[dict[str, Any]] = []
        cursor = after
        for _ in range(max(max_pages, 0)):
            want = min(_PAGE_SIZE, limit - len(out))
            if want <= 0:
                break
            page = self._get_ok(f"/channels/{channel_id}/messages?after={cursor}&limit={want}")
            if not isinstance(page, list) or not page:
                break
            shaped = [_shape_message(m, channel_id, guild_id) for m in page]
            shaped.sort(key=lambda m: int(m["id"]) if m["id"].isdigit() else 0)
            out.extend(shaped)
            cursor = shaped[-1]["id"]
            if len(page) < want or not cursor.isdigit():
                break
        return out[:limit]

    def latest(self, channel_id: str) -> str | None:
        """The id of the channel's newest message, or None for an empty channel."""
        _check_id(channel_id, "channel")
        page = self._get_ok(f"/channels/{channel_id}/messages?limit=1")
        if not isinstance(page, list) or not page or not isinstance(page[0], Mapping):
            return None
        mid = str(page[0].get("id", ""))
        return mid if mid.isdigit() else None

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> dict[str, str]:
        """React to a message as the bot. ``emoji`` is a unicode emoji as is, or a custom one
        as ``name:id``; it is percent-encoded into the URL. A 429 is retried like a GET
        (reacting twice with the same emoji is a no-op, so a retry is safe)."""
        return self._reaction("PUT", channel_id, message_id, emoji)

    def remove_reaction(self, channel_id: str, message_id: str, emoji: str) -> dict[str, str]:
        """Take back the bot's own reaction ``emoji`` (as :meth:`add_reaction` takes it). Taking
        back one the bot never added changes nothing, so a retry is safe."""
        return self._reaction("DELETE", channel_id, message_id, emoji)

    def _reaction(
        self, method: str, channel_id: str, message_id: str, emoji: str
    ) -> dict[str, str]:
        """``PUT`` or ``DELETE`` the bot's own reaction ``emoji`` on a message."""
        _check_id(channel_id, "channel")
        _check_id(message_id, "message")
        emoji = emoji.strip() if isinstance(emoji, str) else ""
        if not emoji_ok(emoji):
            raise DiscordError("emoji must be a unicode emoji or name:id", retryable=False)
        path = (
            f"/channels/{channel_id}/messages/{message_id}/reactions/"
            f"{urllib.parse.quote(emoji, safe='')}/@me"
        )
        status, payload = self._send(path, method)
        if 200 <= status < 300:
            return {"channel_id": channel_id, "message_id": message_id, "emoji": emoji}
        if status == 429:
            retry_after = _retry_after(payload)
            raise DiscordError(
                f"discord rate limited (retry_after={retry_after})",
                retryable=True,
                retry_after=retry_after,
            )
        raise DiscordError(f"discord returned HTTP {status}", retryable=status >= 500)

    def edit_interaction_response(
        self, application_id: str, interaction_token: str, text: str
    ) -> dict[str, bool]:
        """Replace the deferred (ephemeral) original response of a command interaction with
        ``text`` (cut to 2000 characters with an ellipsis). The interaction token authorises
        the call, so no bot token is sent, and it is never logged or put in an error. Discord
        answering 401 or 404 means the token ran out: a non-retryable error with ``expired``
        set. A 429 is waited out and a 5xx is retryable."""
        _check_id(application_id, "application")
        if not isinstance(interaction_token, str) or not _TOKEN_RE.match(interaction_token):
            raise DiscordError("malformed interaction token", retryable=False)
        text = str(text)
        if len(text) > MAX_CONTENT:
            text = text[: MAX_CONTENT - 1] + "\u2026"
        body = {"content": text, "allowed_mentions": {"parse": []}}
        path = f"/webhooks/{application_id}/{interaction_token}/messages/@original"
        status, payload = self._send(
            path, "PATCH", body=json.dumps(body).encode("utf-8"), bot=False
        )
        if 200 <= status < 300:
            return {"edited": True}
        if status in (401, 404):
            raise DiscordError("discord interaction expired", retryable=False, expired=True)
        if status == 429:
            retry_after = _retry_after(payload)
            raise DiscordError(
                f"discord rate limited (retry_after={retry_after})",
                retryable=True,
                retry_after=retry_after,
            )
        raise DiscordError(f"discord returned HTTP {status}", retryable=status >= 500)

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


def _check_id(value: Any, what: str) -> None:
    if not isinstance(value, str) or not _CHANNEL_RE.match(value):
        raise DiscordError(f"{what} must be a numeric Discord id", retryable=False)


def _shape_message(raw: Any, channel_id: str, guild_id: str | None) -> dict[str, Any]:
    raw = raw if isinstance(raw, Mapping) else {}
    author = raw.get("author") if isinstance(raw.get("author"), Mapping) else {}
    mid = str(raw.get("id", ""))
    atts = raw.get("attachments")
    return {
        "id": mid,
        "channel_id": channel_id,
        "content": str(raw.get("content") or ""),
        "author_id": str(author.get("id", "")),
        "author_name": str(author.get("username", "")),
        "bot": author.get("bot") is True,
        "url": (
            f"https://discord.com/channels/{guild_id}/{channel_id}/{mid}"
            if guild_id and mid
            else None
        ),
        "attachments": [
            {k: a.get(k) for k in ("id", "filename", "content_type", "size", "url")}
            for a in (atts if isinstance(atts, list) else [])
            if isinstance(a, Mapping)
        ],
    }
