"""The ``discord.reply`` action: an ephemeral reply to a Discord command or button click.

A slash or context command is acknowledged with a deferred, ephemeral "thinking" response
(see ``apps/discord_commands.py``). This action replaces that response with ``text`` by
editing ``@original`` of the interaction webhook, so only the invoking user sees it and no
second message is left behind. The interaction token is read from the store by the id of
the ``discord.command.invoked`` event (never from the event) and authorises the call: no bot
token is used. It is never logged or echoed.

A ``discord.button.clicked`` click is acknowledged differently: the gateway answers it with
``UPDATE_MESSAGE`` (the clicked message, its buttons disabled), so its ``@original`` is the
prompt itself. A token kept for a click (``kind: button``) is therefore answered with a new
ephemeral follow-up message instead, seen only by the clicker; the prompt is left as it is.

Input: ``event_id`` (the triggering event's id; a rule maps it from ``trigger.id``) and
``text`` (at most 2000 characters, longer is cut with an ellipsis). ``params.actor`` is the
Discord app actor and must be the actor the interaction came in on, so a flow on one bot
cannot answer another bot's interaction. Output: ``{replied: true, event_id}``.

Failures: no stored token, an expired one, or Discord answering 401/404 is the non-retryable
``interaction_expired``; 429 and 5xx are retryable; any other refusal is not.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.apps.discord_commands import BUTTON_INTERACTION, interaction_token
from culture_rules.apps.discord_rest import DiscordClient, DiscordError, Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = ["DiscordReplyAction"]

log = logging.getLogger("culture_rules.node")

_EXPIRED = "interaction_expired: the command's interaction can no longer be answered"


def _fail(error: str) -> InvocationResult:
    return InvocationResult.failed(error, retryable=False)


class DiscordReplyAction:
    """Action port for ``discord.reply``."""

    # editing @original to the same text again changes nothing, so a retry is safe (a retried
    # button follow-up may show twice, privately, to the clicker alone)
    supports_idempotency_key = True

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        discord_base_url: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport
        self._base_url = discord_base_url
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(UTC))

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
            return _fail("discord.reply needs a Discord app actor in params.actor")
        event_id, text = input.get("event_id"), input.get("text")
        if not isinstance(event_id, str) or not event_id.strip():
            return _fail("discord.reply needs the triggering event_id")
        if not isinstance(text, str) or not text.strip():
            return _fail("discord.reply needs text")
        stored = interaction_token(self._store, event_id, now=self._clock())
        if stored is None:
            return _fail(_EXPIRED)
        if stored.get("actor") != actor_id:
            return _fail(f"discord.reply: actor {actor_id!r} did not receive that interaction")
        secret = stored.get("token")  # a credential: never logged or put in an error
        application_id = stored.get("application_id")
        kwargs: dict[str, Any] = {"transport": self._transport, "sleep": self._sleep}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = DiscordClient("", **kwargs)
        answer = (
            client.create_followup
            if stored.get("kind") == BUTTON_INTERACTION
            else client.edit_interaction_response
        )
        try:
            answer(str(application_id), str(secret), text)
        except DiscordError as exc:
            log.warning("discord reply to event %s failed: %s", event_id, exc)
            if exc.expired:
                return _fail(_EXPIRED)
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        return InvocationResult.completed({"replied": True, "event_id": event_id})
