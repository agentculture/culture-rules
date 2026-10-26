"""The ``discord.react`` action: the bot adds one reaction to a Discord message.

``params.actor`` is an app actor with surface ``discord``; the reaction is added with that
actor's own bot token (``connection.bot_token``, a ``grant:NAME`` reference resolved at call
time). The input is ``channel`` and ``message`` (numeric ids) and ``emoji``: a unicode emoji
as is, or a custom one as ``name:id``. ``connection.channels`` restricts the channels as
:class:`~culture_rules.node.actions.discord_read.DiscordReadAction` does (unset or empty means
every channel), and ``connection.guild_id`` (one id or a list) confines it to those servers:
the channel's server is looked up first and a channel elsewhere is refused. A 429 is waited
out by :class:`DiscordClient`. Reacting twice with the same emoji changes nothing, so the
action is safe to retry. The token is never logged or echoed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors import secrets
from culture_rules.apps.discord_rest import DiscordClient, DiscordError, Transport, guild_ids
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.discord_read import DiscordReadAction, _fail, _nonempty

__all__ = ["DiscordReactAction"]

log = logging.getLogger("culture_rules.node")


class DiscordReactAction(DiscordReadAction):
    """Action port for ``discord.react``; resolves its actor as ``discord.read`` does."""

    supports_idempotency_key = True  # a repeated reaction is a no-op

    def __init__(
        self,
        store: Any,
        *,
        resolve_secret: Callable[[str], str] | None = None,
        transport: Transport | None = None,
        discord_base_url: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(
            store,
            resolve_secret=resolve_secret,
            transport=transport,
            discord_base_url=discord_base_url,
        )
        self._sleep = sleep

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del idempotency_key, deadline
        actor_id = context.actor
        if not actor_id:
            return _fail("discord.react needs a Discord app actor in params.actor")
        channel, message, emoji = input.get("channel"), input.get("message"), input.get("emoji")
        if not _nonempty(channel) or not _nonempty(message):
            return _fail("discord.react needs a channel and a message id")
        if not _nonempty(emoji):
            return _fail("discord.react needs an emoji")
        connection = self._connection(actor_id, channel)
        if isinstance(connection, InvocationResult):
            return connection
        ref = connection.get("bot_token")
        try:
            token = self._resolve(ref)
        except secrets.SecretError as exc:
            return InvocationResult.failed(str(exc), retryable=True)
        kwargs: dict[str, Any] = {"transport": self._transport, "sleep": self._sleep}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        guilds = guild_ids(connection.get("guild_id"))
        try:
            client = DiscordClient(token, **kwargs)
            if guilds is not None and client.channel_guild(channel) not in guilds:
                # guild_id confines reactions as it does reads and the gateway
                return _fail(f"channel {channel!r} is not in actor {actor_id!r} guilds (guild_id)")
            out = client.add_reaction(channel, message, emoji)
        except DiscordError as exc:
            log.warning("discord react on %s/%s failed: %s", channel, message, exc)
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        return InvocationResult.completed(
            {"channel": channel, "message": message, "emoji": out["emoji"], "reacted": True}
        )
