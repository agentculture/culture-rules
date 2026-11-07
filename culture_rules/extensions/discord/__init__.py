"""The ``discord`` extension: Discord bots as app actors.

* action kinds ``discord.message`` (:mod:`~culture_rules.extensions.discord.message`, which
  also serves core's ``message`` when it names an actor), ``discord.read``, ``discord.react``,
  ``discord.reply`` and ``discord.sweep`` (one module each), over the REST client
  :mod:`~culture_rules.extensions.discord.rest`;
* the built-in ``attachment.fetch`` (:mod:`~culture_rules.extensions.discord.attachment_fetch`,
  through the ``http`` extension's vetted opener);
* the Gateway listener (:mod:`~culture_rules.extensions.discord.gateway`, with
  :mod:`~culture_rules.extensions.discord.buttons` and
  :mod:`~culture_rules.extensions.discord.commands`), its four event types and its state
  collection;
* the contract route ``GET /actors/{id}/discord/targets``
  (:mod:`~culture_rules.extensions.discord.api`).

The ``discord.*`` trigger filters stay core's (:mod:`culture_rules.model.discord_filters`):
the engine matches and validates them. Registering imports nothing but the kinds' typed
params; every port, listener and route imports its module when it is built.
"""

from __future__ import annotations

from typing import Any

from culture_rules.extensions.registry import ApiContext, Extension, Lazy, NodeContext
from culture_rules.model.action_kinds import INT, REQ_STR, STR, ParamSpec, kind

__all__ = ["EVENT_TYPES", "KINDS", "register"]

KINDS = (
    kind(
        "discord.message",
        "Post a message on Discord through a Discord app actor",
        actor=REQ_STR,
        guild=STR,
        channel=REQ_STR,
        text=REQ_STR,
        buttons=ParamSpec("list"),
        thread_name=STR,  # set: channel is a forum and the text starts a post titled so
        tags=ParamSpec("list"),  # forum tag names, with thread_name only
    ),
    kind(
        "discord.read",
        "Read one Discord message in full (text and attachments) through a Discord app actor",
        actor=REQ_STR,
        guild=STR,  # fills the message url when given
        channel=REQ_STR,
        message=REQ_STR,
    ),
    kind(
        "discord.react",
        "React to one Discord message (a unicode emoji or a custom name:id) through a Discord"
        " app actor, and take back the bot's own earlier reactions (remove)",
        any_of=(("emoji", "remove"),),
        actor=REQ_STR,
        guild=STR,
        channel=REQ_STR,
        message=REQ_STR,
        emoji=STR,
        remove=ParamSpec("list"),
    ),
    kind(
        "discord.reply",
        "Answer a Discord slash or message command privately through a Discord app actor",
        actor=REQ_STR,
        event_id=REQ_STR,
        text=REQ_STR,
    ),
    kind(
        "discord.sweep",
        "Read a Discord channel's messages newer than a stored cursor through a Discord app"
        " actor",
        actor=REQ_STR,
        guild=STR,
        channel=REQ_STR,
        cursor_key=REQ_STR,
        limit=INT,
        include_bots=ParamSpec("bool"),
        backfill=ParamSpec("bool"),
    ),
)

EVENT_TYPES = (
    "discord.message.created",
    "discord.reaction.added",
    "discord.command.invoked",
    "discord.button.clicked",
)


def _port(module: str, attr: str) -> Lazy:
    return Lazy(f"{__name__}.{module}", attr, store=True)


class _GatewayListener:
    """The Discord Gateway supervisor as a node listener: names it listens for go on the
    cycle report; closing stops the threads and releases the leases."""

    def __init__(self, supervisor: Any) -> None:
        self.supervisor = supervisor

    def tick(self, report: Any) -> None:
        report.listening += self.supervisor.tick()

    def close(self) -> None:
        self.supervisor.shutdown()


def discord_gateway(ctx: NodeContext) -> _GatewayListener:
    """The ``discord.gateway`` listener (``options``: ``discord_gateway``,
    ``gateway_options``)."""
    from culture_rules.extensions.discord.gateway import GatewaySupervisor  # noqa: PLC0415

    return _GatewayListener(
        GatewaySupervisor(
            ctx.store,
            ctx.identity,
            gateway=ctx.options.get("discord_gateway"),
            resolve_secret=ctx.resolve_secret,
            clock=ctx.clock,
            options=ctx.options.get("gateway_options"),
            host=ctx.host,
        )
    )


def discord_collections() -> tuple[str, ...]:
    from culture_rules.extensions.discord.gateway import (  # noqa: PLC0415
        GATEWAY_STATE_COLLECTION,
    )

    return (GATEWAY_STATE_COLLECTION,)


def discord_targets(app: Any, ctx: ApiContext) -> None:
    """``GET /actors/{id}/discord/targets`` (the ``server`` extra)."""
    from culture_rules.extensions.discord.api import register_targets  # noqa: PLC0415

    register_targets(app, ctx)


def register(ext: Extension) -> None:
    """Discord: the message/read/react/reply/sweep kinds, ``attachment.fetch``, the Gateway
    listener and its four event types, and the targets route."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_port("discord.message", _port("message", "DiscordMessageAction"))
    ext.action_port("discord.read", _port("read", "DiscordReadAction"))
    ext.action_port("discord.react", _port("react", "DiscordReactAction"))
    ext.action_port("discord.reply", _port("reply", "DiscordReplyAction"))
    ext.action_port("discord.sweep", _port("sweep", "DiscordSweepAction"))
    ext.builtin("attachment.fetch", Lazy(__name__ + ".attachment_fetch", "AttachmentFetchPort"))
    for event in EVENT_TYPES:
        ext.event_type(event)
    ext.node_listener("discord.gateway", discord_gateway, long_lived=True)
    ext.node_collections(discord_collections)
    ext.api_routes("discord.targets", discord_targets)
