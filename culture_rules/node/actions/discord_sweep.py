"""The ``discord.sweep`` action: a channel's messages newer than a stored cursor, for a loop.

A scheduled flow saves what a channel said since its last run. The sweep is read-only and the
cursor is saved by a later step, ``cursor.advance``
(:mod:`culture_rules.node.actions.cursor`), placed after the steps that act on the messages.
So the cursor moves only when a run's pushes succeed; a failed run leaves it where it was and
the next run reads the same messages again::

    sweep:   discord.sweep   channel, cursor_key        -> messages, cursor, next_cursor, ...
    push:    a loop over messages (library.push, ...)
    advance: cursor.advance  key=cursor_key, expected=cursor, value=next_cursor

``params.actor`` is an app actor with surface ``discord``, resolved as
:class:`~culture_rules.node.actions.discord_read.DiscordReadAction` does: its own bot token
(``connection.bot_token``), its ``connection.channels`` allow-list and its
``connection.guild_id`` guild restriction (the channel's guild is looked up first and a channel
elsewhere is refused; that lookup also fills ``guild_id`` and the jump ``url``).

Inputs (``cursor_key`` and ``limit`` may instead sit in ``config``; the input wins):

``channel`` (required)
    the numeric channel id;
``cursor_key`` (required)
    names the flow, ``[A-Za-z0-9_.-]``, up to 80 characters; the cursor is stored per flow and
    channel under ``<cursor_key>:<channel>``;
``limit``
    how many messages one run reads, 1-100, default 50;
``guild``
    the channel's guild id, which fills ``guild_id`` and ``url`` when the actor has no
    ``guild_id`` restriction to look it up by;
``include_bots``
    ``true`` to return messages from bots too; off by default, as on a ``discord.*`` trigger
    (the cursor still moves past them);
``backfill``
    ``true`` to start a flow with no stored cursor at the channel's oldest message. Off by
    default: a first run starts now (``next_cursor`` is the newest message, or ``"0"`` for an
    empty channel) and returns nothing, so enabling a flow on a busy channel files no history.

Outputs:
``messages`` (oldest first, at most ``limit``; each ``id``, ``channel_id``, ``guild_id``,
``content``, ``author_id``, ``author_name``, ``url`` and ``attachments`` as ``discord.read``
gives them), ``count``, ``more`` (the run hit ``limit``; more may be waiting), ``cursor``
(the stored value it read from, or null), ``next_cursor`` (the newest message id returned, or
``cursor`` when there are none) and ``cursor_key`` (the stored key, for ``cursor.advance``).
Final failures: a bad input, a stored cursor that is not a message id (``bad_cursor``), and
the actor refusals of ``discord.read``; a Discord error follows its ``retryable``. The token is
never logged or echoed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors import secrets
from culture_rules.apps.discord_rest import DiscordClient, DiscordError, guild_ids
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.cursor import read_cursor
from culture_rules.node.actions.discord_read import DiscordReadAction, _fail, _nonempty

__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "SWEEP_KIND", "DiscordSweepAction"]

log = logging.getLogger("culture_rules.node")

SWEEP_KIND = "discord.sweep"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_ID_RE = re.compile(r"^\d{1,25}$")


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    # a null input is unset and falls back to config (options, unlike cursor.advance's
    # expected, where null means "no cursor yet")
    value = input.get(key)
    return config.get(key) if value is None else value


def _limit(value: Any) -> int | None:
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return min(value, MAX_LIMIT)


class DiscordSweepAction(DiscordReadAction):
    """Action port for ``discord.sweep``; resolves its actor as ``discord.read`` does."""

    supports_idempotency_key = False  # a read changes nothing

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del idempotency_key, deadline
        config = context.config if isinstance(context.config, Mapping) else {}
        actor_id = context.actor
        if not actor_id:
            return _fail("discord.sweep needs a Discord app actor in params.actor")
        channel = input.get("channel")
        if not _nonempty(channel):
            return _fail("discord.sweep needs a channel id")
        flow = _param(input, config, "cursor_key")
        if not isinstance(flow, str) or not _KEY_RE.match(flow):
            return _fail("discord.sweep needs a cursor_key of letters, digits, '_', '.' or '-'")
        limit = _limit(_param(input, config, "limit"))
        if limit is None:
            return _fail(f"discord.sweep limit must be an integer from 1 to {MAX_LIMIT}")
        include_bots = _param(input, config, "include_bots")
        backfill = _param(input, config, "backfill")
        if not all(v is None or isinstance(v, bool) for v in (include_bots, backfill)):
            return _fail("discord.sweep include_bots and backfill must be true or false")
        key = f"{flow}:{channel}"
        cursor = read_cursor(self._store, key)
        if cursor is not None and not (isinstance(cursor, str) and _ID_RE.match(cursor)):
            return _fail(f"bad_cursor: stored cursor {key!r} is not a message id")
        connection = self._connection(actor_id, channel)
        if isinstance(connection, InvocationResult):
            return connection
        ref = connection.get("bot_token")
        try:
            token = self._resolve(ref)
        except secrets.SecretError as exc:
            return InvocationResult.failed(str(exc), retryable=True)
        guild = input.get("guild")
        guild_id = str(guild) if guild not in (None, "") else None
        kwargs: dict[str, Any] = {"transport": self._transport}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        guilds = guild_ids(connection.get("guild_id"))
        try:
            client = DiscordClient(token, **kwargs)
            if guilds is not None:  # guild_id restricts sweeps as it does reads
                found = client.channel_guild(channel)
                if found not in guilds:
                    return _fail(
                        f"channel {channel!r} is not in actor {actor_id!r} guilds (guild_id)"
                    )
                guild_id = guild_id or found
            if cursor is None and backfill is not True:
                start = client.latest(channel) or "0"  # a first run starts now
                return InvocationResult.completed(
                    {
                        "messages": [],
                        "count": 0,
                        "more": False,
                        "cursor": None,
                        "next_cursor": start,
                        "cursor_key": key,
                    }
                )
            read = client.history(channel, cursor or "0", limit, guild_id=guild_id, max_pages=1)
        except DiscordError as exc:
            log.warning("discord sweep of %s failed: %s", channel, exc)
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        for message in read:
            message["guild_id"] = guild_id
        messages = [m for m in read if include_bots is True or m.get("bot") is not True]
        return InvocationResult.completed(
            {
                "messages": messages,
                "count": len(messages),
                "more": len(read) >= limit,
                "cursor": cursor,
                "next_cursor": read[-1]["id"] if read else cursor,
                "cursor_key": key,
            }
        )
