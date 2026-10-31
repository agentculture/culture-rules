"""The ``message`` action: post to a Discord channel or to a Culture mesh channel.

``params.actor`` set: the actor is an app actor with surface ``discord`` and the message goes
to Discord channel ``channel`` (restricted to ``connection.channels`` when that is set), with
the bot token from ``connection.bot_token`` (a ``grant:NAME`` reference resolved at call
time). No actor: the message goes to the mesh with ``culture channel message`` (argv list,
no shell) through :class:`~culture_rules.node.mesh.MeshPoster`.

Mass mentions are always suppressed on Discord (``allowed_mentions.parse`` is empty). The
token and the message text are never logged or echoed in errors.

``buttons`` put one row of buttons under a Discord message. A button with a ``value`` (a JSON
object) is posted as ``<custom_id>:<token>`` and its value is kept in ``discord_buttons``
before the post (:mod:`culture_rules.apps.discord_buttons`), so the click's event carries it as
``data.value``; only this port writes it, a clicker cannot. Each stored button also records
who posted it, ``posted_by: {run_id, workflow_id}`` (the invoking run and its workflow id,
read from ``runs``; ``workflow_id`` is ``None`` when the run cannot be read), which the click
event carries as ``data.posted_by`` (deviation d2). A post Discord definitively refused
(a 4xx but 429, :attr:`~culture_rules.apps.discord_rest.DiscordError.refused`) deletes the
kept values; after a timeout, a 5xx, a 429 or a lost connection the message may be up, so they
stay (the TTL index deletes them). With buttons the output is
``{"message_id", "custom_ids"}`` (the posted ids, in button order).
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # nosec B404 - only for TimeoutExpired; the CLI runs in node.mesh
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.actors import secrets
from culture_rules.apps import discord_buttons as buttons_mod
from culture_rules.apps.discord_rest import DiscordClient, DiscordError, Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.mesh import MeshPoster

__all__ = ["ACTORS_COLLECTION", "DiscordMessageAction", "MessageAction"]

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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._indexed = False
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
            try:
                components, valued = buttons_mod.prepare_buttons(input.get("buttons"))
            except ValueError as exc:
                return _fail(f"bad_params: {exc}")
            poster = self._posted_by(context.run_id, context.step_id) if valued else None
            return self._discord(context.actor, channel, text, components, valued, poster)
        return self._mesh(channel, text)

    def _posted_by(self, run_id: str, step: str) -> dict[str, Any]:
        """``{run_id, workflow_id, step}`` of the run and step posting valued buttons; the
        workflow id is read from the run (``None`` when it cannot be read: github.merge then
        refuses the button). ``step`` is the step's key in that run, so github.merge can tell
        a workflow step (covered by the workflow's digest) from the rule's own action."""
        workflow_id = None
        try:
            run = self._store.get(RUNS_COLLECTION, run_id)
        except Exception as exc:  # noqa: BLE001 - the post goes ahead without the workflow id
            log.warning("reading the posting run failed (%s)", type(exc).__name__)
            run = None
        if isinstance(run, Mapping) and isinstance(run.get("workflow_id"), str):
            workflow_id = run["workflow_id"]
        return {"run_id": run_id, "workflow_id": workflow_id, "step": step}

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

    def _discord(
        self,
        actor_id: str,
        channel: str,
        text: str,
        components: list[dict[str, Any]],
        valued: list[tuple[str, Any]],
        posted_by: Mapping[str, Any] | None = None,
    ) -> InvocationResult:
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
        kept: list[str] = []
        if valued:
            try:
                kept = self._keep_values(actor_id, channel, valued, posted_by)
            except Exception as exc:  # noqa: BLE001 - nothing was posted: retry later
                log.warning("keeping button values failed (%s)", type(exc).__name__)
                return InvocationResult.failed(
                    f"keeping button values failed ({type(exc).__name__})", retryable=True
                )
        try:
            out = DiscordClient(token, **kwargs).post_message(channel, text, components=components)
        except DiscordError as exc:
            # only a definite refusal drops the values: after a timeout, a 5xx, a 429 or a
            # lost connection the message may be up, and its buttons keep their meaning
            # (the TTL index deletes values whose post never landed)
            if exc.refused:
                buttons_mod.forget_buttons(self._store, kept)
            log.warning("discord message to %s failed: %s", channel, exc)
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        if kept:
            try:
                buttons_mod.set_message_id(self._store, kept, out["message_id"])
            except Exception as exc:  # noqa: BLE001 - the values stand without it
                log.warning("recording the buttons' message id failed (%s)", type(exc).__name__)
        log.info("discord message posted to %s", channel)
        if components:
            out = {**out, "custom_ids": [b["custom_id"] for b in components[0]["components"]]}
        return InvocationResult.completed(out)

    def _keep_values(
        self,
        actor_id: str,
        channel: str,
        valued: list[tuple[str, Any]],
        posted_by: Mapping[str, Any] | None,
    ) -> list[str]:
        """Store the valued buttons' meaning before the post (the TTL index first, once)."""
        if not self._indexed:
            try:
                buttons_mod.ensure_buttons_ttl(self._store)
                self._indexed = True
            except Exception as exc:  # noqa: BLE001 - a missing index must not stop the post
                log.warning("ensuring the buttons TTL index failed (%s)", type(exc).__name__)
        return buttons_mod.remember_buttons(
            self._store, actor_id, channel, valued, now=self._clock(), posted_by=posted_by
        )


class DiscordMessageAction(MessageAction):
    """``discord.message``: post through the Discord app actor in ``params.actor``; never the
    mesh. ``params.guild`` is the server the channel was picked from (editor metadata)."""

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        if not context.actor:
            return _fail("discord.message needs a Discord app actor in params.actor")
        return super().invoke(input, idempotency_key, deadline, context=context)
