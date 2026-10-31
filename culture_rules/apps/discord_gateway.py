"""The Discord Gateway listener: one live connection per bot, held by one node mesh-wide.

A Discord ``app`` actor (``params.surface == "discord"``) that declares
``discord.message.created`` in ``params.events`` is listened to over the Discord Gateway.
Added reactions are raised from the raw event as ``discord.reaction.added`` (delivery id
``actor:message:user:emoji``) and sunk only when the actor declares that type too; they go
through the same channel selection as messages.
An actor that declares ``discord.command.invoked`` also gets the ``/save`` and ``Save to
library`` guild commands (:mod:`culture_rules.apps.discord_commands`), registered by the lease
holder through a discord.py ``CommandTree`` (synced only when the definition hash changes, so
a reconnect does not sync again). An invocation is acknowledged with an ephemeral deferral
first, then its token is kept apart (``discord_interactions``, keyed by the event id) and the
event - without the token - is sunk (delivery id ``actor:interaction:<interaction id>``),
through the same channel selection. An invocation whose event is not written (an unselected
channel) gets no reply: the deferral stays "thinking" until Discord times it out.
An actor that declares ``discord.button.clicked`` turns a click on a button of a bot message
(a component interaction; no discord.py ``View`` is needed, the client's ``on_interaction``
sees every click) into that event (:func:`button_data`, delivery id
``actor:interaction:<interaction id>``), its token kept apart the same way
(``kind: button``). The event's ``data.value`` is the value the bot's own ``discord.message``
stored for that button (:mod:`culture_rules.apps.discord_buttons`, one read by
``<actor>:<custom_id>`` inside the hand-over, so inside the acknowledgement window), or
``None``; a clicker cannot supply or change it. The click is acknowledged inside Discord's
3 s: an admitted one with ``UPDATE_MESSAGE``, its message re-rendered with every button
disabled; one no rule admits with a private :data:`BUTTON_NOT_HERE`, the message unchanged.
A click is admitted only when an enabled ``discord.button.clicked`` rule's trigger admits it
with every Discord filter (``channels``, ``users``, ``roles``, ``custom_id``,
``include_bots``; the engine's own :func:`~culture_rules.engine.matching.trigger_matches`, on
the rules the last tick read), so another user's click on a users-filtered rule writes no
event, keeps no token and leaves the buttons enabled.
A hand-over slower than :data:`BUTTON_ACK_WAIT_S` is first acknowledged as a deferred update,
then answered the same.
Every engine node runs a :class:`GatewaySupervisor` from a stage of its cycle
(:meth:`~culture_rules.node.daemon.Node.run_once`, right after the probe stage); the
supervisors coordinate through a mesh-wide :class:`~culture_rules.engine.named_lease.NamedLease`
named ``discord-gateway:<actor id>``, so exactly one node holds the connection.

Threading
=========
:meth:`GatewaySupervisor.tick` runs on the node's cycle thread. Per actor it acquires or
renews the lease (every third of the TTL) and, while it is held and the actor is enabled,
keeps one **listener thread** running; the listener owns the gateway connection (for
discord.py: its own asyncio event loop) and reconnects with a capped exponential backoff
when the connection drops. A connection that resumes - or a new holder that reconnects -
may see recent messages again; :func:`~culture_rules.events.hook_sink.sink` dedupes them on
the message id (the delivery id), so each message lands once.

While a listener runs, a :class:`~culture_rules.engine.leasekeeper.LeaseKeeper` thread
renews the lease as well, so a long synchronous stage of the node cycle (an actor
invocation in the drive stage) cannot let it lapse.

No overlap
==========
The listener is *fenced*: it disconnects as soon as the local clock passes the lease expiry
minus a third of the TTL, or as soon as a renewal fails or is refused (lost to another
node). Another node can take the lease only once it has expired, so the old holder has
stopped before the new one starts (given NTP-synchronised clocks, skew well under a third
of the TTL). Disabling or deleting the actor stops the listener and releases the lease;
:meth:`GatewaySupervisor.shutdown` (the node stopping) does the same, so another node takes
over at once.

Channel selection and retention
===============================
A message is sunk only from a channel some enabled rule with a ``discord.*`` event trigger
selects for that event type (:func:`selected_channels`, recomputed from the ``rules``
collection on every :meth:`GatewaySupervisor.tick`): a rule's ``channels`` param is read
exactly as the trigger matcher reads it
(:func:`~culture_rules.model.discord_filters.discord_channels`), so a rule with no channels
or ``"all"`` selects every channel, and with no such rule nothing is sunk. A trigger names
no actor, so the selection is one union per event type shared by every Discord actor (a
channel any rule selects is kept for each bot); the actor's own ``connection.channels``
allow-list and ``guild_id`` filter still apply on top. Sunk events expire
(:func:`~culture_rules.events.hook_sink.ensure_discord_event_ttl`, ensured by the supervisor).

Secrets and logs
================
``connection.bot_token`` must be a ``grant:<NAME>`` reference; a literal is refused (the
actor is not listened to) and never logged. The token is resolved through
:func:`~culture_rules.actors.secrets.resolve` (injectable) in the listener thread, right
before each connect: from the value grant injected into the node's environment
(``CULTURE_RULES_SECRET_<NAME>``), else ``grant get`` - never from any other variable. An
actor with a ``machine`` is listened for only by the node on that machine. Logs carry the
actor id, outcomes and exception *types* only - never the token or message content.

The ``discord`` extra (discord.py) is imported lazily, inside the listener. Without it the
supervisor logs once and does nothing - in particular it takes no lease, so a node without
the extra never starves one that has it.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from culture_rules.actors import secrets
from culture_rules.apps import discord_buttons as buttons_mod
from culture_rules.apps import discord_commands as commands_mod
from culture_rules.apps.discord_rest import guild_ids
from culture_rules.engine.leasekeeper import JOIN_TIMEOUT_S, KeeperFactory, LeaseKeeper
from culture_rules.engine.matching import trigger_matches
from culture_rules.engine.named_lease import NamedLease
from culture_rules.events.hook_sink import (
    ACCEPTED,
    DISABLED,
    DUPLICATE,
    IGNORED,
    ensure_discord_event_ttl,
    event_id_for,
    sink,
)
from culture_rules.model.discord_command_config import command_problems, definitions, not_here
from culture_rules.model.discord_filters import BUTTON_CLICKED, discord_channels, filter_values
from culture_rules.model.rule import Rule, Trigger

__all__ = [
    "ACTORS_COLLECTION",
    "BUTTON_ACK_WAIT_S",
    "BUTTON_EVENT",
    "BUTTON_NOT_HERE",
    "COMMAND_EVENT",
    "GATEWAY_EVENT",
    "LEASE_PREFIX",
    "LISTENED_EVENTS",
    "MAX_CONTENT",
    "DiscordPyGateway",
    "Gateway",
    "GatewayOptions",
    "GatewaySupervisor",
    "build_command_tree",
    "button_data",
    "gateway_intents",
    "message_data",
    "reaction_data",
    "selected_channels",
    "sync_commands",
]

log = logging.getLogger("culture_rules.apps.discord_gateway")

ACTORS_COLLECTION = "actors"
RULES_COLLECTION = "rules"
DISCORD_TYPE_PREFIX = "discord."
GATEWAY_EVENT = "discord.message.created"
REACTION_EVENT = "discord.reaction.added"
COMMAND_EVENT = "discord.command.invoked"
BUTTON_EVENT = BUTTON_CLICKED
BUTTON_NOT_HERE = "This button is not set up here."
"""The private answer to a click no rule (or filter) admits; the message stays as it was."""
BUTTON_ACK_WAIT_S = 2.0
"""How long a click waits for its event to be written before it is acknowledged as a
deferred update anyway: Discord drops an interaction not answered within 3 s."""
NOT_ADMITTED = "not_admitted"
""":meth:`GatewaySupervisor._deliver`'s answer when no rule selects the channel or the actor's
channel and guild filters keep the event out (nothing was written)."""
LISTENED_EVENTS = (GATEWAY_EVENT, REACTION_EVENT, COMMAND_EVENT, BUTTON_EVENT)
"""Declaring any of them in ``params.events`` makes an app actor listen over the Gateway."""
LEASE_PREFIX = "discord-gateway:"
MAX_CONTENT = 2000
"""Longest ``data.content`` carried on an event (Discord's own message limit)."""
_SINK_ATTEMPTS = 3

OnMessage = Callable[[Mapping[str, Any]], None]
OnReaction = Callable[[Mapping[str, Any]], None]
OnButton = Callable[[Mapping[str, Any]], "bool | None"]


class Gateway(Protocol):
    """A Discord Gateway client (the real one is :class:`DiscordPyGateway`; tests fake it)."""

    def available(self) -> bool:
        """Whether this client can run here (e.g. its library is installed)."""

    def connect(
        self,
        token: str,
        on_message: OnMessage,
        should_stop: Callable[[], bool],
        on_reaction: OnReaction | None = None,
        commands: commands_mod.CommandRegistration | None = None,
        events: Iterable[str] = LISTENED_EVENTS,
        on_button: OnButton | None = None,
    ) -> None:
        """Connect (asking Discord only for the intents ``events`` need) and call
        ``on_message`` with each created message (a mapping with ``id``,
        ``guild_id``, ``channel_id``, ``author_id``, ``author_name``, ``bot``, ``content``,
        ``created_at``, ``url``, ``user_roles``) and ``on_reaction`` with each added reaction
        (``message_id``, ``guild_id``, ``channel_id``, ``user_id``, ``username``,
        ``user_roles``, ``bot``, ``emoji_name``, ``emoji_id``; raised from the raw event, so
        the message need not be cached) until ``should_stop()`` is true - then disconnect and
        return. With ``commands``, register the commands in each of its guilds whose
        ``needs_sync`` is true (then call ``synced``) once per connect, and hand each invocation
        to ``commands.invoke`` after deferring it ephemerally. With ``on_button``, hand each
        button click (``interaction_id``, ``application_id``, ``token``, ``custom_id``,
        ``message_id``, ``channel_id``, ``guild_id``, ``user_id``, ``username``,
        ``user_roles``, ``bot``) to it and acknowledge the click by what it answers (``True``:
        the message's buttons disabled; ``False``: :data:`BUTTON_NOT_HERE`; ``None``:
        :data:`~culture_rules.apps.discord_commands.NOT_SAVED`), within Discord's 3 s. Raise
        (or return) when the connection is lost for good; the supervisor reconnects."""


def gateway_intents(events: Iterable[str] = LISTENED_EVENTS) -> Any:
    """discord.py intents: guilds, guild messages and reactions, plus the privileged message
    content only when ``events`` declares ``discord.message.created`` (a reaction-only bot
    must connect without it)."""
    import discord  # noqa: PLC0415 - the optional discord extra (imported lazily)

    intents = discord.Intents.default()
    intents.guilds = True
    intents.guild_messages = True
    intents.guild_reactions = True  # raw reaction add events (not message-cache dependent)
    # privileged: enable it for the bot in the dev portal, needed for message events only
    intents.message_content = GATEWAY_EVENT in set(events)
    return intents


def _role_ids(member: Any) -> list[str]:
    """The member's role ids as strings (empty without a member, e.g. a DM)."""
    return [str(role.id) for role in (getattr(member, "roles", None) or ())]


def _message_dict(msg: Any) -> dict[str, Any]:
    author = msg.author
    created = getattr(msg, "created_at", None)
    guild = getattr(msg, "guild", None)
    return {
        "id": str(msg.id),
        "guild_id": str(guild.id) if guild is not None else None,
        "channel_id": str(msg.channel.id),
        "author_id": str(author.id),
        "author_name": str(getattr(author, "name", "") or ""),
        "bot": bool(getattr(author, "bot", False)),
        "content": msg.content or "",
        "created_at": created.isoformat() if created is not None else None,
        "url": getattr(msg, "jump_url", None),
        "user_roles": _role_ids(author),
    }


def _reaction_dict(payload: Any) -> dict[str, Any]:
    """A raw reaction payload (``RawReactionActionEvent``) as a plain mapping."""
    member = getattr(payload, "member", None)
    emoji = payload.emoji
    emoji_id = getattr(emoji, "id", None)
    guild_id = getattr(payload, "guild_id", None)
    return {
        "message_id": str(payload.message_id),
        "guild_id": None if guild_id is None else str(guild_id),
        "channel_id": str(payload.channel_id),
        "user_id": str(payload.user_id),
        "username": str(getattr(member, "name", "") or ""),
        "user_roles": _role_ids(member),
        "bot": bool(getattr(member, "bot", False)),
        "emoji_name": getattr(emoji, "name", None),
        "emoji_id": None if emoji_id is None else str(emoji_id),
    }


def _interaction_dict(
    interaction: Any,
    registration: commands_mod.CommandRegistration,
    *,
    command: str,
    command_type: str,
    options: Mapping[str, Any],
    target_message_id: str | None,
    target_channel_id: str | None,
) -> dict[str, Any]:
    """One invocation as a plain mapping (token included: the supervisor keeps it apart)."""
    user = getattr(interaction, "user", None)
    guild_id = getattr(interaction, "guild_id", None)
    channel_id = getattr(interaction, "channel_id", None)
    app_id = getattr(interaction, "application_id", None)
    return {
        "interaction_id": str(interaction.id),
        "application_id": str(app_id) if app_id is not None else registration.application_id,
        "token": interaction.token,
        "command": command,
        "command_type": command_type,
        "options": dict(options),
        "target_message_id": target_message_id,
        "target_channel_id": target_channel_id,
        "user_id": None if user is None else str(user.id),
        "username": str(getattr(user, "name", "") or ""),
        "user_roles": _role_ids(user),
        "bot": bool(getattr(user, "bot", False)),
        "channel_id": None if channel_id is None else str(channel_id),
        "guild_id": None if guild_id is None else str(guild_id),
    }


def _is_button_click(discord: Any, interaction: Any) -> bool:
    """A component interaction on a button (not a command, a select menu or a modal)."""
    if getattr(interaction, "type", None) != discord.InteractionType.component:
        return False
    data = getattr(interaction, "data", None) or {}
    return data.get("component_type") == discord.ComponentType.button.value


def _button_dict(interaction: Any) -> dict[str, Any]:
    """One click as a plain mapping (token included: the supervisor keeps it apart)."""
    data = getattr(interaction, "data", None) or {}
    message = getattr(interaction, "message", None)
    user = getattr(interaction, "user", None)
    guild_id = getattr(interaction, "guild_id", None)
    app_id = getattr(interaction, "application_id", None)
    custom_id = data.get("custom_id")
    return {
        "interaction_id": str(interaction.id),
        "application_id": None if app_id is None else str(app_id),
        "token": interaction.token,
        "custom_id": None if custom_id is None else str(custom_id),
        "message_id": None if message is None else str(message.id),
        "channel_id": _channel(interaction),
        "guild_id": None if guild_id is None else str(guild_id),
        "user_id": None if user is None else str(user.id),
        "username": str(getattr(user, "name", "") or ""),
        "user_roles": _role_ids(user),
        "bot": bool(getattr(user, "bot", False)),
    }


def _disabled_view(discord: Any, message: Any) -> Any:
    """The message's buttons as a discord.py ``View``, each disabled and otherwise as it was
    (label, style, ``custom_id`` or url, emoji, row). The view is stopped at once, so
    discord.py does not keep it in its view store (nothing on it can be clicked)."""
    view = discord.ui.View(timeout=None)
    for row, component in enumerate(getattr(message, "components", None) or ()):
        for child in getattr(component, "children", None) or ():
            if getattr(child, "type", None) != discord.ComponentType.button:
                continue
            view.add_item(
                discord.ui.Button(
                    style=child.style,
                    label=child.label,
                    custom_id=child.custom_id,
                    url=child.url,
                    emoji=child.emoji,
                    sku_id=getattr(child, "sku_id", None),
                    disabled=True,
                    row=row,
                )
            )
    view.stop()
    return view


async def _answer_click(discord: Any, interaction: Any, written: bool | None) -> None:
    """Acknowledge a click by its hand-over's answer; after a deferred update, edit the
    message (``@original``) or send a private follow-up instead."""
    deferred = interaction.response.is_done()
    try:
        if written is True:
            view = _disabled_view(discord, interaction.message)
            if deferred:
                await interaction.edit_original_response(view=view)
            else:
                await interaction.response.edit_message(view=view)  # UPDATE_MESSAGE
            return
        answer = BUTTON_NOT_HERE if written is False else commands_mod.NOT_SAVED
        if deferred:
            await interaction.followup.send(answer, ephemeral=True)
        else:
            await interaction.response.send_message(answer, ephemeral=True)
    except Exception as exc:  # noqa: BLE001 - the answer is a courtesy; type logged
        log.warning("discord gateway: answering a button click failed (%s)", type(exc).__name__)


def _slash_target(
    command: Mapping[str, Any], given: Mapping[str, Any], interaction: Any
) -> tuple[str | None, str | None]:
    """``(target_channel_id, target_message_id)`` from the command's ``kind: message`` option:
    a link names its channel; a bare id is in the channel the command was used in."""
    for option in command.get("options") or ():
        if option.get("kind") == "message" and given.get(option["name"]) is not None:
            channel, target = commands_mod.parse_message_ref(given[option["name"]])
            if target is not None and channel is None:
                channel = _channel(interaction)
            return channel, target
    return None, None


def _slash_callback(
    discord: Any, command: Mapping[str, Any], registration: Any, hand_over: Callable
) -> Any:
    """A slash command's callback, its signature generated from the configured options:
    discord.py reads each option's name, type and whether it is required from it."""
    import inspect  # noqa: PLC0415
    from typing import Optional  # noqa: PLC0415

    async def callback(interaction: Any, **values: Any) -> None:
        given = {k: v for k, v in values.items() if v is not None}
        channel, target = _slash_target(command, given, interaction)
        raw = _interaction_dict(
            interaction,
            registration,
            command=command["name"],
            command_type="slash",
            options=given,
            target_message_id=target,
            target_channel_id=channel,
        )
        await hand_over(interaction, raw, not_here(command))

    keyword = inspect.Parameter.POSITIONAL_OR_KEYWORD
    params = [inspect.Parameter("interaction", keyword, annotation=discord.Interaction)]
    for option in command.get("options") or ():
        required = option.get("required") is True
        params.append(
            inspect.Parameter(
                option["name"],
                keyword,
                default=inspect.Parameter.empty if required else None,
                annotation=str if required else Optional[str],
            )
        )
    callback.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    callback.__annotations__ = {p.name: p.annotation for p in params}
    described = {o["name"]: o["description"] for o in command.get("options") or ()}
    if described:
        callback = discord.app_commands.describe(**described)(callback)
    return callback


def _menu_callback(
    discord: Any, command: Mapping[str, Any], registration: Any, hand_over: Callable
) -> Any:
    """A message command's callback: the right-clicked message is its target."""

    async def callback(interaction: Any, message: Any) -> None:
        raw = _interaction_dict(
            interaction,
            registration,
            command=command["name"],
            command_type="message",
            options={},
            target_message_id=str(message.id),
            target_channel_id=str(message.channel.id),
        )
        await hand_over(interaction, raw, not_here(command))

    # real classes, not strings: discord.py introspects the callback's annotations
    callback.__annotations__ = {"interaction": discord.Interaction, "message": discord.Message}
    return callback


def build_command_tree(client: Any, registration: commands_mod.CommandRegistration) -> Any:
    """A discord.py ``CommandTree`` on ``client`` holding the actor's configured commands
    (``registration.commands``, discord-surfaces d12) in each guild of ``registration``. Each
    callback defers the interaction (ephemeral, "thinking") *before* handing it to
    ``registration.invoke``; an invocation no rule takes is answered with its ``not_here``."""
    import asyncio  # noqa: PLC0415

    import discord  # noqa: PLC0415 - the optional discord extra (imported lazily)
    from discord import app_commands  # noqa: PLC0415

    async def hand_over(interaction: Any, raw: dict[str, Any], answer_not_here: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)  # within Discord's 3 s
        written = await asyncio.to_thread(registration.invoke, raw)
        if written is True:
            return  # the flow answers it (discord.reply)
        # no flow will answer it: say why now, not "thinking" until Discord drops it
        answer = answer_not_here if written is False else commands_mod.NOT_SAVED
        try:
            await interaction.edit_original_response(content=answer)
        except Exception as exc:  # noqa: BLE001 - the answer is a courtesy; type logged
            log.warning(
                "discord gateway %s: answering an unhandled command failed (%s)",
                registration.actor_id,
                type(exc).__name__,
            )

    tree = app_commands.CommandTree(client)
    guilds = [discord.Object(int(g)) for g in registration.guild_ids]
    for command in registration.commands:
        try:
            built = _app_command(app_commands, discord, command, registration, hand_over)
            tree.add_command(built, guilds=guilds)
        except Exception as exc:  # noqa: BLE001 - one bad command must not stop the listener
            log.warning(
                "discord gateway %s: command %r skipped, it cannot be built (%s)",
                registration.actor_id,
                command.get("name"),
                type(exc).__name__,
            )
    return tree


def _app_command(
    app_commands: Any, discord: Any, command: Mapping[str, Any], registration: Any, hand_over: Any
) -> Any:
    """One configured command as a discord.py command object."""
    if command["type"] == "message":
        menu = _menu_callback(discord, command, registration, hand_over)
        return app_commands.ContextMenu(name=command["name"], callback=menu)
    slash = _slash_callback(discord, command, registration, hand_over)
    return app_commands.Command(
        name=command["name"], description=command["description"], callback=slash
    )


def _channel(interaction: Any) -> str | None:
    channel_id = getattr(interaction, "channel_id", None)
    return None if channel_id is None else str(channel_id)


async def sync_commands(tree: Any, registration: commands_mod.CommandRegistration) -> None:
    """Sync the tree to each guild whose definition hash changed; a failure is logged (by
    exception type) and not recorded, so the next connect tries again."""
    import discord  # noqa: PLC0415 - the optional discord extra (imported lazily)

    actor_id = registration.actor_id
    for guild in registration.guild_ids:
        try:
            if not registration.needs_sync(guild):
                continue
            await tree.sync(guild=discord.Object(int(guild)))
            registration.synced(guild)
            log.info("discord gateway %s: commands synced to guild %s", actor_id, guild)
        except Exception as exc:  # noqa: BLE001 - the listener keeps running without commands
            log.warning(
                "discord gateway %s: syncing commands to guild %s failed (%s)",
                actor_id,
                guild,
                type(exc).__name__,
            )
    for guild in registration.withdrawn:  # the tree holds nothing there: Discord drops them
        try:
            await tree.sync(guild=discord.Object(int(guild)))
            registration.forgotten(guild)
            log.info("discord gateway %s: commands withdrawn from guild %s", actor_id, guild)
        except Exception as exc:  # noqa: BLE001 - the record stays and the next connect retries
            log.warning(
                "discord gateway %s: withdrawing commands from guild %s failed (%s)",
                actor_id,
                guild,
                type(exc).__name__,
            )


class DiscordPyGateway:
    """The production :class:`Gateway`: a discord.py ``Client`` on its own event loop.

    discord.py reconnects and resumes the session by itself; :meth:`connect` returns once
    ``should_stop()`` turns true (a watchdog task closes the client) or the client gives up.
    """

    def __init__(self, *, poll: float = 0.5) -> None:
        self._poll = poll
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:  # locate it without paying for the import
            try:
                self._available = importlib.util.find_spec("discord") is not None
            except (ImportError, ValueError):
                self._available = False
        return self._available

    @staticmethod
    def _raw_reaction_handler(on_reaction: OnReaction) -> Any:
        """The ``on_raw_reaction_add`` coroutine: it takes the raw payload, so a reaction on a
        message that is not in the client cache is still raised."""
        import asyncio  # noqa: PLC0415

        async def on_raw_reaction_add(payload: Any) -> None:
            await asyncio.to_thread(on_reaction, _reaction_dict(payload))

        return on_raw_reaction_add

    @staticmethod
    def _interaction_handler(on_button: OnButton, *, ack_wait: float = BUTTON_ACK_WAIT_S) -> Any:
        """The ``on_interaction`` coroutine: a button click is handed to ``on_button`` (in a
        thread) and acknowledged by its answer; one that takes longer than ``ack_wait`` is
        acknowledged first as a deferred update (the message unchanged), inside Discord's
        3 s. Commands (the ``CommandTree`` answers them) and other components are ignored."""
        import asyncio  # noqa: PLC0415

        import discord  # noqa: PLC0415 - the optional discord extra (imported lazily)

        async def on_interaction(interaction: Any) -> None:
            if not _is_button_click(discord, interaction):
                return
            handed = asyncio.ensure_future(asyncio.to_thread(on_button, _button_dict(interaction)))
            written: bool | None = None
            try:
                try:
                    written = await asyncio.wait_for(asyncio.shield(handed), ack_wait)
                except TimeoutError:
                    await interaction.response.defer()  # DEFERRED_UPDATE_MESSAGE
                    written = await handed
            except Exception as exc:  # noqa: BLE001 - answered "try again"; type logged
                log.warning(
                    "discord gateway: handing over a button click failed (%s)",
                    type(exc).__name__,
                )
            await _answer_click(discord, interaction, written)

        return on_interaction

    def connect(
        self,
        token: str,
        on_message: OnMessage,
        should_stop: Callable[[], bool],
        on_reaction: OnReaction | None = None,
        commands: commands_mod.CommandRegistration | None = None,
        events: Iterable[str] = LISTENED_EVENTS,
        on_button: OnButton | None = None,
    ) -> None:
        import asyncio  # noqa: PLC0415

        import discord  # noqa: PLC0415 - the optional discord extra (imported lazily)

        intents = gateway_intents(events)
        poll = self._poll

        async def main() -> None:
            if commands is None:
                client = discord.Client(intents=intents)
            else:
                client = discord.Client(
                    intents=intents, application_id=int(commands.application_id)
                )
                tree = build_command_tree(client, commands)

                async def setup_hook() -> None:  # once per connect, after login
                    await sync_commands(tree, commands)

                client.setup_hook = setup_hook

            async def on_message_event(msg: Any) -> None:
                await asyncio.to_thread(on_message, _message_dict(msg))

            client.event(_named(on_message_event, "on_message"))
            if on_reaction is not None:
                client.event(self._raw_reaction_handler(on_reaction))
            if on_button is not None:  # clicks need no application_id or View
                client.event(_named(self._interaction_handler(on_button), "on_interaction"))

            async def watchdog() -> None:
                while not should_stop():
                    await asyncio.sleep(poll)
                await client.close()

            async with client:
                guard = asyncio.create_task(watchdog())
                try:
                    await client.start(token, reconnect=True)
                finally:
                    guard.cancel()

        asyncio.run(main())


def _named(fn: Any, name: str) -> Any:
    fn.__name__ = name  # discord.py dispatches events by the coroutine's name
    return fn


def message_data(msg: Mapping[str, Any]) -> dict[str, Any]:
    """The compact event ``data`` for one gateway message (content truncated)."""

    def opt(key: str) -> str | None:
        value = msg.get(key)
        return None if value is None else str(value)

    return {
        "guild_id": opt("guild_id"),
        "channel_id": opt("channel_id"),
        "message_id": opt("id"),
        "author_id": opt("author_id"),
        "author_name": opt("author_name"),
        "bot": bool(msg.get("bot", False)),
        "content": str(msg.get("content") or "")[:MAX_CONTENT],
        "created_at": opt("created_at"),
        "url": opt("url"),
        "user_roles": _str_list(msg.get("user_roles")),
    }


def _str_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v) for v in value]
    return []


def reaction_data(reaction: Mapping[str, Any]) -> dict[str, Any]:
    """The compact event ``data`` for one added reaction. The reactor also fills
    ``author_id`` / ``author_name``, which the ``users`` trigger filter reads."""

    def opt(key: str) -> str | None:
        value = reaction.get(key)
        return None if value is None else str(value)

    return {
        "guild_id": opt("guild_id"),
        "channel_id": opt("channel_id"),
        "message_id": opt("message_id"),
        "emoji_name": opt("emoji_name"),
        "emoji_id": opt("emoji_id"),
        "user_id": opt("user_id"),
        "username": opt("username"),
        "user_roles": _str_list(reaction.get("user_roles")),
        "bot": bool(reaction.get("bot", False)),
        "author_id": opt("user_id"),
        "author_name": opt("username"),
    }


def button_data(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The event ``data`` for one button click: only these keys, never the token. The clicker
    also fills ``author_id`` / ``author_name``, which the ``users`` trigger filter reads.
    ``value`` and ``posted_by`` are always ``None`` here (never read from the click); the
    gateway fills them from the button's stored document."""

    def opt(key: str) -> str | None:
        value = raw.get(key)
        return None if value is None else str(value)

    return {
        "custom_id": opt("custom_id"),
        "message_id": opt("message_id"),
        "channel_id": opt("channel_id"),
        "guild_id": opt("guild_id"),
        "interaction_id": opt("interaction_id"),
        "user_id": opt("user_id"),
        "username": opt("username"),
        "author_id": opt("user_id"),
        "author_name": opt("username"),
        "user_roles": _str_list(raw.get("user_roles")),
        "bot": bool(raw.get("bot", False)),
        "value": None,  # filled from discord_buttons by the gateway, never from the click
        "posted_by": None,  # likewise: the run and workflow that posted the button (d2)
    }


def _discord_rule(doc: Mapping[str, Any], event_type: str | None) -> Rule | None:
    """The enabled ``discord.*`` event rule ``doc`` holds (of ``event_type`` when given), or
    None for a deleted, disabled, unparseable or other rule (one that never fires here)."""
    if doc.get("deleted_at"):
        return None
    try:
        rule = Rule.from_dict(doc, strict=False)
    except (ValueError, TypeError, AttributeError):  # unparseable (or trigger: null)
        return None
    wanted = rule.trigger.params.get("type")
    if not rule.enabled or rule.trigger.kind != "event":
        return None
    if not isinstance(wanted, str) or not wanted.startswith(DISCORD_TYPE_PREFIX):
        return None
    return rule if event_type is None or wanted == event_type else None


def selected_channels(
    rule_docs: Iterable[Mapping[str, Any]], event_type: str | None = None
) -> frozenset[str] | None:
    """The channels the enabled ``discord.*`` event rules select, as one union: ``None`` when
    any of them selects every channel, an empty set when there is no such rule. With
    ``event_type`` only the rules on that exact type count (a reaction rule never selects
    channels for messages). Deleted, disabled and unparseable rules select nothing."""
    union: set[str] = set()
    for doc in rule_docs:
        rule = _discord_rule(doc, event_type)
        if rule is None:
            continue
        channels = discord_channels(rule.trigger.params)
        if channels is None:
            return None
        union |= channels
    return frozenset(union)


GATEWAY_STATE_COLLECTION = "gateway_state"
"""One ``{id: actor id, connected, last_event_at}`` document per gateway actor (observability)."""
_EVENT_STAMP_EVERY_S = 10.0
"""``last_event_at`` is rewritten at most this often, however many messages arrive."""


@dataclass(frozen=True, kw_only=True)
class GatewayOptions:
    """Lease and reconnect tuning (tests shorten the backoff and drop the keeper thread)."""

    ttl: timedelta = timedelta(seconds=30)
    """The lease TTL; renewed every third of it, the listener is fenced at two thirds."""
    keeper: KeeperFactory | None = LeaseKeeper
    """Builds the renewal thread run beside a listener (``None``: renew from ticks only)."""
    backoff: float = 1.0
    """First reconnect pause, seconds (doubles per consecutive failure)."""
    max_backoff: float = 60.0
    """Longest reconnect pause, seconds."""


def _view(doc: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    params = doc.get("params") if isinstance(doc.get("params"), Mapping) else {}
    connection = doc.get("connection") or params.get("connection") or {}
    return params, connection if isinstance(connection, Mapping) else {}


_CommandKey = tuple[str, frozenset[str], str]


def _command_key(doc: Mapping[str, Any]) -> _CommandKey:
    """What an actor's commands are and where they go: (application id, guilds, its
    ``params.commands`` as canonical JSON). An actor not declaring ``discord.command.invoked``
    has no guilds and ``""`` for the JSON. The id is ``""`` when unset or not numeric."""
    params, connection = _view(doc)
    app_id = str(connection.get("application_id") or "").strip()
    app_id = app_id if app_id.isdigit() else ""
    if COMMAND_EVENT not in (params.get("events") or ()):
        # no commands, but the id is kept: commands synced before are withdrawn with it
        return (app_id, frozenset(), "")
    guilds = commands_mod.command_guild_ids(
        connection.get("guild_id"), connection.get("command_guild_id")
    )
    configured = json.dumps(params.get("commands"), sort_keys=True, default=str)
    return (app_id, guilds, configured)


class _Session:
    """One actor's lease, listener thread and keeper on this node."""

    def __init__(self, actor_id: str, lease: NamedLease, doc: Mapping[str, Any]) -> None:
        self.actor_id = actor_id
        self.lease = lease
        self.lock = threading.Lock()
        self.renew_lock = threading.Lock()
        self.doc: Mapping[str, Any] = doc
        self.valid_until: datetime | None = None
        self.renewed_at: datetime | None = None
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.keeper: Any = None
        self.stamped_at: float | None = None
        # where this connection registered commands (set on connect): the one source for
        # which guilds an invocation is answered from, and what a config edit is compared to
        self.command_key: _CommandKey | None = None

    def snapshot(self) -> Mapping[str, Any]:
        with self.lock:
            return self.doc

    def listening(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and not self.stop.is_set()


class GatewaySupervisor:
    """Holds the Discord Gateway connections this node owns (see the module docstring)."""

    def __init__(
        self,
        store: Any,
        identity: str,
        *,
        gateway: Gateway | None = None,
        resolve_secret: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        options: GatewayOptions | None = None,
        host: str | None = None,
    ) -> None:
        self._store = store
        self.identity = identity
        self.host = host
        self._gateway = gateway if gateway is not None else DiscordPyGateway()
        self._resolve = resolve_secret or secrets.resolve
        self._clock = clock or (lambda: datetime.now(UTC))
        self._options = options or GatewayOptions()
        self._sessions: dict[str, _Session] = {}
        self._stragglers: list[threading.Thread] = []
        self._warned: set[str] = set()
        self._command_warned: set[tuple[str, str]] = set()
        self._unavailable_logged = False
        # per event type; a type absent here selects nothing (until the first tick)
        self._selection: dict[str, frozenset[str] | None] = {}
        # the enabled discord.button.clicked rules' triggers: a click is admitted only when
        # one of them admits it with every Discord filter (none until the first tick)
        self._click_triggers: tuple[Trigger, ...] = ()
        for ensure in (
            ensure_discord_event_ttl,
            commands_mod.ensure_interaction_ttl,
            buttons_mod.ensure_buttons_ttl,
        ):
            try:
                ensure(store)
            except Exception as exc:  # noqa: BLE001 - a missing index must not stop the node
                log.warning("discord gateway: ensuring a TTL index failed (%s)", type(exc).__name__)

    # ------------------------------------------------------------------ cycle

    def tick(self) -> list[str]:
        """Reconcile with the actors: renew leases, start/stop listeners; return the ids
        of the actors this node is listening for."""
        wanted = self._wanted()
        if wanted:  # no Discord listener here: nobody reads the selection, skip the scan
            self._refresh_selection()
        if wanted and not self._gateway.available():
            if not self._unavailable_logged:
                log.info("discord extra not installed: the discord gateway stage is idle")
                self._unavailable_logged = True
            wanted = {}  # take no lease: a node with the extra must be able to hold it
        for actor_id in [a for a in self._sessions if a not in wanted]:
            self._end(actor_id, release=True)
        failure: Exception | None = None
        for actor_id, doc in wanted.items():
            try:
                self._tick_one(actor_id, doc)
            except Exception as exc:  # noqa: BLE001 - other actors still get their turn
                failure = failure or exc
        if failure is not None:
            raise failure
        return self.listening()

    def listening(self) -> list[str]:
        return sorted(a for a, s in self._sessions.items() if s.listening())

    def shutdown(self) -> None:
        """Stop every listener and release every lease this node holds."""
        while self._sessions:  # _end pops each session, so drain until empty
            self._end(next(iter(self._sessions)), release=True)

    def threads_alive(self) -> bool:
        """Whether any listener or keeper thread is still running (tests: no leaks)."""
        threads = [s.thread for s in self._sessions.values()] + self._stragglers
        keepers = [s.keeper for s in self._sessions.values()]
        return any(t is not None and t.is_alive() for t in threads) or any(
            k is not None and getattr(k, "alive", False) for k in keepers
        )

    # ------------------------------------------------------------------ reconcile

    def _refresh_selection(self) -> None:
        """Recompute the channel selection; a failed read keeps the last one."""
        try:
            rules = list(self._store.find(RULES_COLLECTION))
            selection = {t: selected_channels(rules, t) for t in LISTENED_EVENTS}
            clicks = (_discord_rule(doc, BUTTON_EVENT) for doc in rules)
            self._click_triggers = tuple(r.trigger for r in clicks if r is not None)
            self._selection = selection
        except Exception as exc:  # noqa: BLE001 - the listeners keep the last selection
            log.warning("discord gateway: reading the rules failed (%s)", type(exc).__name__)

    def _click_admitted(self, data: Mapping[str, Any]) -> bool:
        """Whether an enabled ``discord.button.clicked`` rule's trigger admits this click with
        the matcher's full Discord filter set (``channels``, ``users``, ``roles``,
        ``custom_id``, ``include_bots``): a pure in-memory check on the rules the last tick
        read, so it fits the acknowledgement window."""
        event = {"kind": "event", "type": BUTTON_EVENT, "data": data}
        return any(trigger_matches(t, event) for t in self._click_triggers)

    def _selected(self, event_type: str, channel_id: str | None) -> bool:
        selection = self._selection.get(event_type, frozenset())  # tick replaces the dict whole
        return selection is None or channel_id in selection

    def _listens_here(self, doc: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
        """An enabled Discord app declaring a gateway event (:data:`LISTENED_EVENTS`), on this
        node's machine (its bot token is injected on its own machine only)."""
        if (doc.get("surface") or params.get("surface")) != "discord":
            return False
        if not set(LISTENED_EVENTS) & set(params.get("events") or ()):
            return False
        if doc.get("enabled", True) is False:
            return False
        machine = doc.get("machine")
        return not (machine and self.host and machine != self.host)

    def _wanted(self) -> dict[str, Mapping[str, Any]]:
        out: dict[str, Mapping[str, Any]] = {}
        for doc in self._store.find(ACTORS_COLLECTION, {"kind": "app"}):
            params, connection = _view(doc)
            actor_id = doc.get("id")
            if not self._listens_here(doc, params):
                continue
            if not secrets.is_secret_ref(connection.get("bot_token")):
                if actor_id not in self._warned:
                    self._warned.add(actor_id)
                    log.warning(
                        "discord gateway %s: connection.bot_token must be a grant:<NAME> "
                        "reference; not listening",
                        actor_id,
                    )
                continue
            self._warned.discard(actor_id)
            out[actor_id] = doc
        return out

    def _tick_one(self, actor_id: str, doc: Mapping[str, Any]) -> None:
        session = self._sessions.get(actor_id)
        if session is None:
            lease = NamedLease(
                self._store,
                LEASE_PREFIX + actor_id,
                self.identity,
                ttl=self._options.ttl,
                clock=self._clock,
            )
            session = self._sessions[actor_id] = _Session(actor_id, lease, doc)
        with session.lock:
            session.doc = doc
            due = session.renewed_at is None or self._clock() >= (
                session.renewed_at + self._options.ttl / 3
            )
            registered = session.command_key
        if registered is not None and session.listening() and _command_key(doc) != registered:
            log.info("discord gateway %s: commands moved; reconnecting", actor_id)
            self._stop_listener(session)
        held = self._renew(session) if due else not self._fenced(session)
        if held and not session.listening():
            self._start(session)
        elif not held and session.thread is not None:
            self._stop_listener(session)

    def _renew(self, session: _Session) -> bool:
        """Acquire/renew the lease; on refusal or failure, fence the listener at once."""
        with session.renew_lock:
            try:
                expires = session.lease.acquire()
            except Exception as exc:  # noqa: BLE001 - a failed renewal means: disconnect now
                log.warning(
                    "discord gateway %s: lease renewal failed (%s); disconnecting",
                    session.actor_id,
                    type(exc).__name__,
                )
                expires = None
            with session.lock:
                session.valid_until = expires
                session.renewed_at = self._clock() if expires is not None else None
            if expires is None:
                session.stop.set()
            return expires is not None

    def _fenced(self, session: _Session) -> bool:
        with session.lock:
            until = session.valid_until
        return until is None or self._clock() >= until - self._options.ttl / 3

    def _start(self, session: _Session) -> None:
        self._stop_listener(session)
        stop = session.stop = threading.Event()
        session.thread = threading.Thread(
            target=self._listen,
            args=(session, stop),
            name=f"discord-gateway-{session.actor_id}",
            daemon=True,
        )
        session.thread.start()
        factory = self._options.keeper
        if factory is not None:
            keeper = factory(lambda: self._renew(session), self._options.ttl.total_seconds() / 3)
            keeper.__enter__()
            session.keeper = keeper
        log.info("discord gateway %s: listening (lease held)", session.actor_id)

    def _stop_listener(self, session: _Session) -> None:
        session.stop.set()
        keeper, session.keeper = session.keeper, None
        if keeper is not None:
            keeper.__exit__(None, None, None)
        thread, session.thread = session.thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(JOIN_TIMEOUT_S)
            if thread.is_alive():
                self._stragglers.append(thread)
                log.warning("discord gateway %s: listener did not stop in time", session.actor_id)

    def _end(self, actor_id: str, *, release: bool) -> None:
        session = self._sessions.pop(actor_id)
        was_listening = session.thread is not None
        self._stop_listener(session)
        if release:
            try:
                session.lease.release()
            except Exception as exc:  # noqa: BLE001 - it lapses on its own anyway
                log.warning(
                    "discord gateway %s: lease release failed (%s)", actor_id, type(exc).__name__
                )
        if was_listening:
            log.info("discord gateway %s: stopped listening", actor_id)

    # ------------------------------------------------------------------ observability

    def _state(self, session: _Session, **fields: Any) -> None:
        """Best-effort write of the gateway state doc (``connected``, ``last_event_at``)."""
        try:
            current = self._store.get(GATEWAY_STATE_COLLECTION, session.actor_id) or {}
            doc = {k: v for k, v in current.items() if k in ("connected", "last_event_at")}
            doc.update(fields)
            doc["id"] = session.actor_id
            self._store.put(GATEWAY_STATE_COLLECTION, doc)
        except Exception as exc:  # noqa: BLE001 - observability must never break the listener
            log.warning(
                "discord gateway %s: state write failed (%s)", session.actor_id, type(exc).__name__
            )

    # ------------------------------------------------------------------ listener thread

    def _listen(self, session: _Session, stop: threading.Event) -> None:
        def should_stop() -> bool:
            return stop.is_set() or self._fenced(session)

        failures = 0
        while not should_stop():
            _, connection = _view(session.snapshot())
            try:
                bot_auth = self._resolve(str(connection.get("bot_token")))
            except Exception as exc:  # noqa: BLE001 - logged by type only; retried
                failures += 1
                log.warning(
                    "discord gateway %s: cannot resolve the bot token (%s)",
                    session.actor_id,
                    type(exc).__name__,
                )
            else:
                self._state(session, connected=True)
                try:
                    self._gateway.connect(
                        bot_auth,
                        lambda msg: self._on_message(session, msg),
                        should_stop,
                        lambda rx: self._on_reaction(session, rx),
                        commands=self._registration(session),
                        events=self._declared(session),
                        on_button=lambda raw: self._on_button(session, raw),
                    )
                    failures = 0
                except Exception as exc:  # noqa: BLE001 - reconnect; the type only is logged
                    failures += 1
                    log.warning(
                        "discord gateway %s: connection lost (%s); reconnecting",
                        session.actor_id,
                        type(exc).__name__,
                    )
                finally:
                    del bot_auth
                    self._state(session, connected=False)
            if should_stop():
                break
            stop.wait(self._backoff(failures))

    def _warn_commands_once(self, actor_id: str, missing: str) -> None:
        if (actor_id, missing) not in self._command_warned:
            self._command_warned.add((actor_id, missing))
            what = (
                "params.commands lists none, or is invalid"
                if missing == "commands"
                else f"connection.{missing} is unset or not an id"
            )
            log.warning(
                "discord gateway %s: discord.command.invoked is declared but %s; "
                "commands are not registered",
                actor_id,
                what,
            )

    def _registration(self, session: _Session) -> commands_mod.CommandRegistration | None:
        """The commands to register on this connect: only for an actor declaring
        ``discord.command.invoked`` with valid ``params.commands`` (d12) and
        ``connection.application_id`` and ``guild_id`` set
        (a missing or non-numeric one is logged once), in the guilds ``command_guild_id``
        narrows them to."""
        doc = session.snapshot()
        key = _command_key(doc)
        with session.lock:
            session.command_key = key
        app_id, guilds, configured = key
        declared = configured != ""
        params, connection = _view(doc)
        actor_id = session.actor_id
        if not app_id:  # unset, or not an id discord.py can take: nothing can be synced
            if declared:
                self._warn_commands_once(actor_id, "application_id")
            return None
        commands = (params.get("commands") or []) if declared else []
        if declared and not guilds:
            narrowed = guild_ids(connection.get("command_guild_id")) is not None
            self._warn_commands_once(actor_id, "command_guild_id" if narrowed else "guild_id")
            commands = []
        elif declared and (not commands or command_problems(commands)):
            self._warn_commands_once(actor_id, "commands")
            commands = []
        if not commands:
            guilds = frozenset()  # none here; any registered before are withdrawn
        withdrawn = commands_mod.synced_guilds(self._store, actor_id) - guilds
        if not commands and not withdrawn:
            return None
        digest = commands_mod.definition_hash(app_id, definitions(commands))
        return commands_mod.CommandRegistration(
            actor_id=actor_id,
            application_id=app_id,
            guild_ids=tuple(sorted(guilds)),
            commands=tuple(commands),
            invoke=lambda raw: self._on_command(session, raw),
            needs_sync=lambda g: commands_mod.sync_needed(self._store, actor_id, g, digest),
            synced=lambda g: commands_mod.record_sync(
                self._store, actor_id, g, digest, now=self._clock()
            ),
            withdrawn=tuple(sorted(withdrawn)),
            forgotten=lambda g: commands_mod.forget_sync(self._store, actor_id, g),
        )

    def _backoff(self, failures: int) -> float:
        opts = self._options
        return min(opts.max_backoff, opts.backoff * (2 ** max(0, failures - 1)))

    @staticmethod
    def _declared(session: _Session) -> list[str]:
        """The gateway events the session's actor declares in ``params.events``."""
        params, _ = _view(session.snapshot())
        return [e for e in LISTENED_EVENTS if e in (params.get("events") or ())]

    def _on_message(self, session: _Session, msg: Mapping[str, Any]) -> None:
        if GATEWAY_EVENT not in self._declared(session):
            return  # undeclared: no stamp, no sink
        data = message_data(msg)
        if data["message_id"]:
            self._deliver(
                session,
                GATEWAY_EVENT,
                data,
                f"{session.actor_id}:{data['message_id']}",
            )

    def _on_reaction(self, session: _Session, reaction: Mapping[str, Any]) -> None:
        if REACTION_EVENT not in self._declared(session):
            return  # undeclared: no stamp, no sink
        data = reaction_data(reaction)
        emoji = data["emoji_id"] or data["emoji_name"]
        if data["message_id"] and data["user_id"] and emoji:
            delivery = f"{session.actor_id}:{data['message_id']}:{data['user_id']}:{emoji}"
            self._deliver(session, REACTION_EVENT, data, delivery)

    def _on_command(self, session: _Session, raw: Mapping[str, Any]) -> bool | None:
        """Keep the invocation's token apart (keyed by the event id), then sink the event
        without it; the token is dropped again when the event is not written. Says whether
        the event was written (a duplicate counts): ``True``; ``False`` when nothing admits it
        (no rule selects the channel, the actor's filters, an undeclared type, a disabled
        actor); ``None`` when every write failed, so the answer does not blame the channel."""
        raw = dict(raw)
        token = raw.pop("token", None)
        app_id = raw.pop("application_id", None)
        data = commands_mod.command_data(raw)
        if not data["interaction_id"]:
            return False
        key = session.command_key
        if key is None or not key[0] or data["guild_id"] not in key[1]:  # e.g. not yet dropped
            return False
        return self._sink_interaction(
            session, COMMAND_EVENT, data, token, app_id, commands_mod.COMMAND_INTERACTION
        )

    def _on_button(self, session: _Session, raw: Mapping[str, Any]) -> bool | None:
        """A button click, as :meth:`_on_command` takes an invocation: its token kept apart
        (``kind: button``), the event sunk without it; ``True`` when written (a duplicate
        counts), ``False`` when nothing admits it (an undeclared type included), ``None`` when
        every write failed."""
        if BUTTON_EVENT not in self._declared(session):
            return False  # undeclared: no token, no stamp, no sink
        raw = dict(raw)
        token = raw.pop("token", None)
        app_id = raw.pop("application_id", None)
        data = button_data(raw)
        if not data["interaction_id"] or not data["custom_id"]:
            return False
        stored = self._stored_button(session, data["custom_id"])
        data["value"] = stored["value"] if stored else None
        data["posted_by"] = stored["posted_by"] if stored else None
        return self._sink_interaction(
            session, BUTTON_EVENT, data, token, app_id, commands_mod.BUTTON_INTERACTION
        )

    def _stored_button(self, session: _Session, custom_id: str) -> dict[str, Any] | None:
        """``{value, posted_by}`` the bot's ``discord.message`` stored for this button (one
        read by id), or ``None``: none stored, expired, or the read failed (logged; the click
        still lands)."""
        try:
            return buttons_mod.stored_button(
                self._store, session.actor_id, custom_id, now=self._clock()
            )
        except Exception as exc:  # noqa: BLE001 - never fail a click over its value
            log.warning(
                "discord gateway %s: reading a button value failed (%s)",
                session.actor_id,
                type(exc).__name__,
            )
            return None

    def _sink_interaction(
        self,
        session: _Session,
        event_type: str,
        data: dict[str, Any],
        token: Any,
        app_id: Any,
        kind: str,
    ) -> bool | None:
        """Sink an interaction's event (delivery id ``actor:interaction:<id>``), keeping its
        token apart once admitted and dropping it again when the event is not written."""
        delivery = f"{session.actor_id}:interaction:{data['interaction_id']}"
        event_id = event_id_for("discord", delivery)
        kept = False

        def keep() -> None:
            nonlocal kept
            if not isinstance(token, str) or not token:
                return
            try:
                commands_mod.remember_interaction(
                    self._store,
                    event_id,
                    token,
                    str(app_id or ""),
                    session.actor_id,
                    now=self._clock(),
                    kind=kind,
                )
                kept = True
            except Exception as exc:  # noqa: BLE001 - the event still lands; no reply then
                log.warning(
                    "discord gateway %s: keeping an interaction token failed (%s)",
                    session.actor_id,
                    type(exc).__name__,
                )

        outcome = self._deliver(session, event_type, data, delivery, before_sink=keep)
        written = outcome in (ACCEPTED, DUPLICATE)
        if kept and not written:
            try:
                commands_mod.forget_interaction(self._store, event_id)
            except Exception as exc:  # noqa: BLE001 - the TTL index deletes it anyway
                log.warning(
                    "discord gateway %s: dropping an interaction token failed (%s)",
                    session.actor_id,
                    type(exc).__name__,
                )
        if written:
            return True
        return False if outcome in (NOT_ADMITTED, IGNORED, DISABLED) else None

    def _admitted(
        self, connection: Mapping[str, Any], event_type: str, data: Mapping[str, Any]
    ) -> bool:
        """Whether the event type's rules select the channel and the actor's channel and guild
        filters admit it; a button click also needs a click rule whose filters all admit it
        (:meth:`_click_admitted`), so a click no rule would fire on changes nothing."""
        if not self._selected(event_type, data["channel_id"]):
            return False  # no enabled discord.* rule selects this channel: never written
        if event_type == BUTTON_EVENT and not self._click_admitted(data):
            return False  # e.g. another user's click on a users-filtered rule
        channels = filter_values(connection.get("channels"))  # a bare id is one channel
        if channels and data["channel_id"] not in channels:
            return False
        guilds = guild_ids(connection.get("guild_id"))
        return guilds is None or data["guild_id"] is None or data["guild_id"] in guilds

    def _deliver(
        self,
        session: _Session,
        event_type: str,
        data: dict[str, Any],
        delivery_id: str,
        *,
        before_sink: Callable[[], None] | None = None,
    ) -> str | None:
        """Sink one event if its channel is selected and the actor's filters admit it; the
        sink's outcome, :data:`NOT_ADMITTED` when it was not admitted, or ``None`` when every
        attempt failed.
        ``before_sink`` runs once, after admission and before the first write."""
        doc = session.snapshot()
        _, connection = _view(doc)
        if not self._admitted(connection, event_type, data):
            return NOT_ADMITTED
        if before_sink is not None:
            before_sink()
        author = data["author_name"] or data["author_id"]
        now = time.monotonic()
        if session.stamped_at is None or now - session.stamped_at >= _EVENT_STAMP_EVERY_S:
            session.stamped_at = now
            self._state(session, last_event_at=self._clock().isoformat())
        for attempt in range(1, _SINK_ATTEMPTS + 1):
            try:
                return sink(
                    self._store, doc, event_type, data, delivery_id=delivery_id, author=author
                )
            except Exception as exc:  # noqa: BLE001 - retried then dropped (type logged)
                log.warning(
                    "discord gateway %s: writing an event failed (%s), attempt %d",
                    session.actor_id,
                    type(exc).__name__,
                    attempt,
                )
                time.sleep(0.05 * attempt)
        return None
