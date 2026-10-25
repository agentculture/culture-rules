"""The ``discord.read`` action: fetch one Discord message, untruncated, with its attachments.

``params.actor`` is an app actor with surface ``discord``; the read uses that actor's own bot
token (``connection.bot_token``, a ``grant:NAME`` reference resolved at call time). The input
is ``channel`` and ``message`` (numeric ids) and an optional ``guild`` (the gateway event's
``data.guild_id``), which only fills the jump ``url``. ``connection.channels`` restricts the
channels the same way :class:`~culture_rules.node.actions.message.MessageAction` does: unset or
empty means every channel. The token is never logged or echoed in errors.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors import secrets
from culture_rules.apps.discord_rest import DiscordClient, DiscordError, Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.message import ACTORS_COLLECTION

__all__ = ["DiscordReadAction"]

log = logging.getLogger("culture_rules.node")


def _fail(error: str) -> InvocationResult:
    return InvocationResult.failed(error, retryable=False)


class DiscordReadAction:
    """Action port for ``discord.read``."""

    supports_idempotency_key = False  # a read changes nothing

    def __init__(
        self,
        store: Any,
        *,
        resolve_secret: Callable[[str], str] | None = None,
        transport: Transport | None = None,
        discord_base_url: str | None = None,
    ) -> None:
        self._store = store
        self._resolve = resolve_secret or secrets.resolve_or_literal
        self._transport = transport
        self._base_url = discord_base_url

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
            return _fail("discord.read needs a Discord app actor in params.actor")
        channel, message, guild = input.get("channel"), input.get("message"), input.get("guild")
        if not isinstance(channel, str) or not channel or not isinstance(message, str):
            return _fail("discord.read needs a channel and a message id")
        if not message:
            return _fail("discord.read needs a channel and a message id")
        guild_id = str(guild) if guild not in (None, "") else None

        actor = self._store.get(ACTORS_COLLECTION, actor_id)
        if actor is None:
            return _fail(f"actor {actor_id!r} not found")
        params = actor.get("params") if isinstance(actor.get("params"), Mapping) else {}
        surface = actor.get("surface") or params.get("surface")
        if surface != "discord":
            return _fail(f"actor {actor_id!r} is not a discord app actor")
        connection = actor.get("connection") or params.get("connection") or {}
        allowed = connection.get("channels")
        if isinstance(allowed, str):  # one channel, not a set of characters
            allowed = [allowed]
        if allowed and channel not in {str(c) for c in allowed}:
            return _fail(f"channel {channel!r} is not in actor {actor_id!r} allow-list")
        ref = connection.get("bot_token")
        if not isinstance(ref, str) or not ref:
            return _fail(f"actor {actor_id!r} has no bot_token reference")
        try:
            token = self._resolve(ref)
        except secrets.SecretError as exc:
            return InvocationResult.failed(str(exc), retryable=True)
        kwargs: dict[str, Any] = {"transport": self._transport}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        try:
            out = DiscordClient(token, **kwargs).get_message(channel, message, guild_id=guild_id)
        except DiscordError as exc:
            log.warning("discord read of %s/%s failed: %s", channel, message, exc)
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        return InvocationResult.completed(out)
