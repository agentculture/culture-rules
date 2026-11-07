"""The ``discord`` extension: Discord bots as app actors.

* action kinds ``discord.message`` (:mod:`~culture_rules.extensions.discord.message`, which
  also serves core's ``message`` when it names an actor), ``discord.read``, ``discord.react``,
  ``discord.reply`` and ``discord.sweep`` (one module each), over the REST client
  :mod:`~culture_rules.extensions.discord.rest`;
* the built-in ``attachment.fetch`` (:mod:`~culture_rules.extensions.discord.attachment_fetch`,
  through the ``http`` extension's vetted opener);
* the Gateway listener (:mod:`~culture_rules.extensions.discord.gateway`, with
  :mod:`~culture_rules.extensions.discord.buttons` and
  :mod:`~culture_rules.extensions.discord.commands`), its four event types, its state
  collection and its ``gateways`` health section
  (:mod:`~culture_rules.extensions.discord.health`);
* the contract route ``GET /actors/{id}/discord/targets``
  (:mod:`~culture_rules.extensions.discord.api`).

* the ``discord.*`` trigger filters (:mod:`~culture_rules.extensions.discord.filters`): the
  engine's match, the save-time checks and the describe words.

Registering imports nothing but the kinds' typed params and the filters (standard library,
called on every Discord event); every port, listener and route imports its module when it is
built.
"""

from __future__ import annotations

from typing import Any

from culture_rules.extensions.discord import filters
from culture_rules.extensions.registry import ApiContext, Extension, Lazy, NodeContext
from culture_rules.model.action_kinds import INT, REQ_STR, REQ_TEXT, STR, ParamSpec, kind

__all__ = ["EVENT_TYPES", "KINDS", "register"]

KINDS = (
    kind(
        "discord.message",
        "Post a message on Discord through a Discord app actor",
        label="Post a message on Discord",
        declarable_by_apps=True,
        actor=REQ_STR,
        guild=ParamSpec("str", widget="discord_guild"),
        channel=ParamSpec("str", True, widget="discord_channel"),
        text=REQ_TEXT,
        buttons=ParamSpec("list", widget="buttons"),
        thread_name=STR,  # set: channel is a forum and the text starts a post titled so
        tags=ParamSpec("list"),  # forum tag names, with thread_name only
    ),
    kind(
        "discord.read",
        "Read one Discord message in full (text and attachments) through a Discord app actor",
        label="Read a Discord message in full",
        declarable_by_apps=True,
        actor=REQ_STR,
        guild=ParamSpec("str", widget="discord_guild"),  # fills the message url when given
        channel=ParamSpec("str", True, widget="discord_channel"),
        message=REQ_STR,
    ),
    kind(
        "discord.react",
        "React to one Discord message (a unicode emoji or a custom name:id) through a Discord"
        " app actor, and take back the bot's own earlier reactions (remove)",
        label="React to a Discord message",
        declarable_by_apps=True,
        any_of=(("emoji", "remove"),),
        actor=REQ_STR,
        guild=ParamSpec("str", widget="discord_guild"),
        channel=ParamSpec("str", True, widget="discord_channel"),
        message=REQ_STR,
        emoji=STR,
        remove=ParamSpec("list"),
    ),
    kind(
        "discord.reply",
        "Answer a Discord slash or message command privately through a Discord app actor",
        label="Answer a Discord command privately",
        declarable_by_apps=True,
        actor=REQ_STR,
        event_id=REQ_STR,
        text=REQ_TEXT,
    ),
    kind(
        "discord.sweep",
        "Read a Discord channel's messages newer than a stored cursor through a Discord app"
        " actor",
        label="Read a Discord channel since the last run",
        declarable_by_apps=True,
        actor=REQ_STR,
        guild=ParamSpec("str", widget="discord_guild"),
        channel=ParamSpec("str", True, widget="discord_channel"),
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


_IN_CHANNEL = ("channel", "in {}")

#: What describe says for each Discord kind after its name and actor.
PHRASES = {
    "discord.message": (("channel", "to {}"),),
    "discord.read": (_IN_CHANNEL, ("message", "message {}")),
    "discord.react": (
        ("emoji", "with {}"),
        ("remove", "removing {}"),
        _IN_CHANNEL,
        ("message", "on message {}"),
    ),
    "discord.reply": (("text", '"{}"'),),
    "discord.sweep": (_IN_CHANNEL, ("limit", "up to {} messages")),
}
ATTACHMENT_FETCH_PHRASE = (
    "attachment.fetch: pdf, epub and Markdown attachments saved as files on the node"
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


def discord_health(store: Any, now: Any) -> list[dict[str, Any]]:
    """The ``gateways`` health section: each gateway's lease holder, connection and last
    event (:mod:`~culture_rules.extensions.discord.health`)."""
    from culture_rules.extensions.discord.health import gateways  # noqa: PLC0415

    return gateways(store, now)


def discord_targets(app: Any, ctx: ApiContext) -> None:
    """``GET /actors/{id}/discord/targets`` (the ``server`` extra)."""
    from culture_rules.extensions.discord.api import register_targets  # noqa: PLC0415

    register_targets(app, ctx)


def register(ext: Extension) -> None:
    """Discord: the message/read/react/reply/sweep kinds, ``attachment.fetch``, the Gateway
    listener and its four event types, and the targets route."""
    for k in KINDS:
        ext.action_kind(k)
        ext.action_phrases(k.name, words=PHRASES[k.name])
    ext.action_port("discord.message", _port("message", "DiscordMessageAction"))
    ext.action_port("discord.read", _port("read", "DiscordReadAction"))
    ext.action_port("discord.react", _port("react", "DiscordReactAction"))
    ext.action_port("discord.reply", _port("reply", "DiscordReplyAction"))
    ext.action_port("discord.sweep", _port("sweep", "DiscordSweepAction"))
    ext.builtin(
        "attachment.fetch",
        Lazy(__name__ + ".attachment_fetch", "AttachmentFetchPort"),
        phrase=ATTACHMENT_FETCH_PHRASE,
    )
    for event in EVENT_TYPES:
        ext.event_type(event)
    ext.trigger_filter(
        "discord", match=filters.admits, validate=filters.problems, describe=filters.words
    )
    ext.node_listener("discord.gateway", discord_gateway, long_lived=True)
    ext.node_collections(discord_collections)
    ext.health_section("gateways", discord_health, empty=list)
    ext.api_routes("discord.targets", discord_targets)
