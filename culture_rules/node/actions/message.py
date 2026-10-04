"""The ``message`` action: post to a Discord channel or to a Culture mesh channel.

``params.actor`` set: the actor is an app actor with surface ``discord`` and the message goes
to Discord channel ``channel`` (restricted to ``connection.channels`` when that is set), with
the bot token from ``connection.bot_token`` (a ``grant:NAME`` reference resolved at call
time). No actor: the message goes to the mesh with ``culture channel message`` (argv list,
no shell) through :class:`~culture_rules.node.mesh.MeshPoster`.

Mass mentions are always suppressed on Discord (``allowed_mentions.parse`` is empty). The
token and the message text are never logged or echoed in errors.
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # nosec B404 - only for TimeoutExpired; the CLI runs in node.mesh
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors import secrets
from culture_rules.apps.discord_rest import DiscordClient, DiscordError, Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.mesh import MeshPoster

__all__ = ["ACTORS_COLLECTION", "MessageAction"]

ACTORS_COLLECTION = "actors"
log = logging.getLogger("culture_rules.node")


def _fail(error: str) -> InvocationResult:
    return InvocationResult.failed(error, retryable=False)


class MessageAction:
    """Action port for ``message`` (and its legacy alias ``mesh.message``)."""

    supports_idempotency_key = False  # neither Discord nor the mesh deduplicates

    def __init__(
        self,
        store: Any,
        *,
        resolve_secret: Callable[[str], str] | None = None,
        transport: Transport | None = None,
        discord_base_url: str | None = None,
        mesh_executable: str | None = None,
        mesh_run: Callable[..., Any] | None = None,
    ) -> None:
        self._store = store
        self._resolve = resolve_secret or secrets.resolve_or_literal
        self._transport = transport
        self._base_url = discord_base_url
        self._mesh_executable = mesh_executable
        self._mesh_run = mesh_run

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del idempotency_key, deadline
        channel, text = input.get("channel"), input.get("text")
        if not isinstance(channel, str) or not channel or not isinstance(text, str) or not text:
            return _fail("message needs a non-empty channel and text")
        if context.actor:
            return self._discord(context.actor, channel, text)
        return self._mesh(channel, text)

    def _mesh(self, channel: str, text: str) -> InvocationResult:
        executable = self._mesh_executable or shutil.which("culture")
        if not executable:
            return InvocationResult.failed("the culture CLI is not on PATH", retryable=True)
        try:
            MeshPoster(executable, run=self._mesh_run).post(channel, text)
        except subprocess.TimeoutExpired:
            # the message may have been sent: a retry could duplicate it
            log.warning("mesh message to %s timed out (outcome unknown)", channel)
            return InvocationResult.failed(
                "mesh message timed out (outcome unknown)", retryable=False
            )
        except Exception as exc:  # noqa: BLE001 - any failure to run the CLI is retryable
            log.warning("mesh message to %s failed (%s)", channel, type(exc).__name__)
            return InvocationResult.failed(
                f"mesh message failed ({type(exc).__name__})", retryable=True
            )
        log.info("mesh message posted to %s", channel)
        return InvocationResult.completed({})

    def _discord(self, actor_id: str, channel: str, text: str) -> InvocationResult:
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
            out = DiscordClient(token, **kwargs).post_message(channel, text)
        except DiscordError as exc:
            log.warning("discord message to %s failed: %s", channel, exc)
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        log.info("discord message posted to %s", channel)
        return InvocationResult.completed(out)
