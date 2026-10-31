"""Discord application commands: their hash, the guilds, the event data and the token.

Which commands a bot offers is configuration (discord-surfaces d12): a Discord ``app`` actor
that declares ``discord.command.invoked`` in ``params.events`` registers the commands its
``params.commands`` lists (:mod:`culture_rules.model.discord_command_config`), as **guild**
commands (they appear at once; global ones can take an hour), through the node holding its
gateway lease (:mod:`culture_rules.apps.discord_gateway`). No command is built in.

They need ``connection.application_id`` (the bot's application id, a literal) and
``connection.guild_id`` (one id or a list); without either, or without commands, nothing is
registered. The
optional ``connection.command_guild_id`` (one id or a list) narrows the guilds commands are
registered in and answered from (:func:`command_guild_ids`); an invocation from any other
guild is not sunk and is answered with the command's ``not_here``. A guild left out that had
them is synced with no commands, so Discord removes them there, and its sync record is
dropped. Changing
``application_id``, ``command_guild_id``, ``guild_id`` or ``params.commands`` in a way that
changes the commands reconnects the gateway, so the change applies at once.

Sync state
==========
A guild is synced only when :func:`definition_hash` (over the commands in Discord's API shape
and the application id) differs from the hash recorded for it in :data:`COMMAND_SYNC_COLLECTION`
(one ``{id: "<actor>:<guild>", actor, guild_id, hash, synced_at}`` document per actor and
guild), so a reconnect does not sync again. A command deleted by hand in Discord is not
restored until the definitions change (or the document is deleted).

Event data
==========
:func:`command_data` is the ``data`` of ``discord.command.invoked``: ``command`` (its name, as
configured; the ``command`` trigger filter matches it), ``command_type`` (``slash`` or
``message``), ``options`` (the given options by name), ``target_message_id`` and
``target_channel_id`` (the right-clicked message; for a slash command, its ``kind: message``
option parsed by :func:`parse_message_ref`), ``interaction_id``,
``user_id``, ``username``, ``user_roles``, ``bot``, ``channel_id``, ``guild_id``, and
``author_id`` / ``author_name`` (the invoking user, which the ``users`` trigger filter reads).

The interaction token
=====================
The token is **never** in the event. It is kept in :data:`INTERACTIONS_COLLECTION` as
``{id: <event id>, token, application_id, actor, kind, expires_at}`` - ``kind`` is
:data:`COMMAND_INTERACTION` or :data:`BUTTON_INTERACTION` (a ``discord.button.clicked`` click,
whose ``@original`` is the clicked message, so ``discord.reply`` answers it with a follow-up;
a token kept without one reads as a command) - and the event id is
:func:`~culture_rules.events.hook_sink.event_id_for` ``("discord", <delivery id>)`` - with
``expires_at`` :data:`INTERACTION_TOKEN_TTL` (Discord's own 15 minutes) after it was written;
the TTL index :data:`INTERACTIONS_TTL_INDEX` deletes it then, and :func:`interaction_token`
treats a passed ``expires_at`` as gone (MongoDB's TTL monitor runs about once a minute).
Standard-library only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.apps.discord_rest import guild_ids

__all__ = [
    "BUTTON_INTERACTION",
    "CHAT_INPUT",
    "COMMAND_INTERACTION",
    "COMMAND_SYNC_COLLECTION",
    "INTERACTIONS_COLLECTION",
    "INTERACTIONS_TTL_INDEX",
    "INTERACTION_TOKEN_TTL",
    "MESSAGE",
    "NOT_SAVED",
    "CommandRegistration",
    "command_data",
    "command_guild_ids",
    "definition_hash",
    "forget_sync",
    "synced_guilds",
    "ensure_interaction_ttl",
    "forget_interaction",
    "interaction_token",
    "parse_message_ref",
    "record_sync",
    "remember_interaction",
    "sync_needed",
]

CHAT_INPUT, MESSAGE = 1, 3
"""Discord application command types: a slash command and a message context-menu command."""

COMMAND_SYNC_COLLECTION = "discord_command_sync"
INTERACTIONS_COLLECTION = "discord_interactions"
INTERACTIONS_TTL_INDEX = "discord_interactions_ttl"
INTERACTION_TOKEN_TTL = timedelta(minutes=15)
_EXPIRES_AT = "expires_at"
COMMAND_INTERACTION, BUTTON_INTERACTION = "command", "button"
"""The ``kind`` a kept token carries: a command invocation, or a message button's click."""

_LINK = re.compile(
    r"^https?://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/(?:\d+|@me)/(\d+)/(\d+)/?$"
)
_ID = re.compile(r"^\d{1,25}$")


NOT_SAVED = "Could not take this right now. Try again in a minute."
"""The private answer to a command whose event was admitted but could not be written (the
store failed every attempt): not the channel's fault, so not the command's ``not_here``."""


@dataclass(frozen=True, kw_only=True)
class CommandRegistration:
    """What a gateway needs to register the commands and hand over invocations.

    ``invoke`` takes one raw invocation (a mapping with the :func:`command_data` keys plus
    ``token`` and ``application_id``) and says whether its event was written: ``True``,
    ``False`` when no rule or filter admits it (answered at once with the command's
    ``not_here``), or ``None`` when writing it failed (answered with :data:`NOT_SAVED`);
    ``needs_sync(guild)`` says whether the guild's commands must be synced, and
    ``synced(guild)`` records that they were. ``commands`` are the actor's valid
    ``params.commands``. ``withdrawn`` are guilds synced before but no longer among
    ``guild_ids``: each is synced with no commands (Discord drops them there), then
    ``forgotten(guild)`` deletes its record. ``actor_id`` is for logs."""

    actor_id: str
    application_id: str
    guild_ids: tuple[str, ...]
    invoke: Callable[[Mapping[str, Any]], bool | None]
    needs_sync: Callable[[str], bool]
    synced: Callable[[str], None]
    commands: tuple[Mapping[str, Any], ...] = ()
    withdrawn: tuple[str, ...] = ()
    forgotten: Callable[[str], None] = lambda _guild: None


def definition_hash(application_id: str, definitions: Iterable[Mapping[str, Any]]) -> str:
    """The sha256 of the definitions and the application id, in a canonical JSON form."""
    body = json.dumps(
        {"application_id": str(application_id), "commands": list(definitions)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _sync_id(actor_id: str, guild_id: str) -> str:
    return f"{actor_id}:{guild_id}"


def command_guild_ids(guild_id: Any, command_guild_id: Any) -> frozenset[str]:
    """The guilds commands are registered in and answered from: ``connection.command_guild_id``
    (one id or a list) within ``connection.guild_id``; unset, every guild in ``guild_id``. An id
    outside ``guild_id`` and a non-numeric one are ignored; nothing is a guild when both are
    unset (commands are guild commands, never global)."""
    listened, wanted = guild_ids(guild_id), guild_ids(command_guild_id)
    if wanted is None:
        chosen = listened or frozenset()
    else:
        chosen = wanted if listened is None else wanted & listened
    return frozenset(g for g in chosen if g.isdigit())


def sync_needed(store: Any, actor_id: str, guild_id: str, digest: str) -> bool:
    """Whether the guild's commands must be synced: no hash recorded, or another one."""
    doc = store.get(COMMAND_SYNC_COLLECTION, _sync_id(actor_id, guild_id))
    return doc is None or doc.get("hash") != digest


def record_sync(store: Any, actor_id: str, guild_id: str, digest: str, *, now: datetime) -> None:
    """Record that the guild's commands are synced at ``digest``."""
    store.put(
        COMMAND_SYNC_COLLECTION,
        {
            "id": _sync_id(actor_id, guild_id),
            "actor": actor_id,
            "guild_id": guild_id,
            "hash": digest,
            "synced_at": now.isoformat(),
        },
    )


def synced_guilds(store: Any, actor_id: str) -> frozenset[str]:
    """The guilds the actor's commands were synced to (one record each)."""
    docs = store.find(COMMAND_SYNC_COLLECTION, {"actor": actor_id})
    return frozenset(str(d["guild_id"]) for d in docs if d.get("guild_id"))


def forget_sync(store: Any, actor_id: str, guild_id: str) -> None:
    """Drop the guild's sync record once its commands are withdrawn."""
    store.delete(COMMAND_SYNC_COLLECTION, _sync_id(actor_id, guild_id))


def parse_message_ref(value: Any) -> tuple[str | None, str | None]:
    """``(channel_id, message_id)`` from a message link, ``(None, id)`` from a bare id, and
    ``(None, None)`` from anything else."""
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    link = _LINK.match(text)
    if link:
        return link.group(1), link.group(2)
    if _ID.match(text):
        return None, text
    return None, None


def _opt(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    return None if value is None else str(value)


def command_data(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The event ``data`` for one invocation: only the documented keys, never the token."""
    options = raw.get("options")
    roles = raw.get("user_roles")
    return {
        "command": _opt(raw, "command"),
        "command_type": _opt(raw, "command_type"),
        "options": (
            {str(k): v for k, v in options.items() if v is not None}
            if isinstance(options, Mapping)
            else {}
        ),
        "target_message_id": _opt(raw, "target_message_id"),
        "target_channel_id": _opt(raw, "target_channel_id"),
        "interaction_id": _opt(raw, "interaction_id"),
        "user_id": _opt(raw, "user_id"),
        "username": _opt(raw, "username"),
        "user_roles": [str(r) for r in roles] if isinstance(roles, (list, tuple)) else [],
        "bot": bool(raw.get("bot", False)),
        "channel_id": _opt(raw, "channel_id"),
        "guild_id": _opt(raw, "guild_id"),
        "author_id": _opt(raw, "user_id"),
        "author_name": _opt(raw, "username"),
    }


def ensure_interaction_ttl(store: Any) -> None:
    """Create :data:`INTERACTIONS_TTL_INDEX` where the store supports indexes (MongoDB)."""
    ensure = getattr(store, "ensure_index", None)
    if callable(ensure):
        ensure(
            INTERACTIONS_COLLECTION,
            [(_EXPIRES_AT, 1)],
            name=INTERACTIONS_TTL_INDEX,
            ttl_seconds=0,
        )


def remember_interaction(
    store: Any,
    event_id: str,
    token: str,
    application_id: str,
    actor_id: str,
    *,
    now: datetime,
    kind: str = COMMAND_INTERACTION,
) -> None:
    """Keep an interaction's token for the reply action, keyed by the event id; ``kind`` says
    whether it answers a command or a button click."""
    store.put(
        INTERACTIONS_COLLECTION,
        {
            "id": event_id,
            "token": token,
            "application_id": application_id,
            "actor": actor_id,
            "kind": kind,
            _EXPIRES_AT: now + INTERACTION_TOKEN_TTL,  # a date: the TTL index deletes it then
        },
    )


def forget_interaction(store: Any, event_id: str) -> None:
    """Drop a kept token (its event was not written)."""
    store.delete(INTERACTIONS_COLLECTION, event_id)


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def interaction_token(
    store: Any, event_id: str, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """``{token, application_id, actor, kind}`` for the event's interaction, or ``None`` when
    there is none or it has expired."""
    doc = store.get(INTERACTIONS_COLLECTION, event_id)
    if doc is None:
        return None
    expires = _as_utc(doc.get(_EXPIRES_AT))
    if expires is None or (now or datetime.now(UTC)) >= expires:
        return None
    return {
        "token": doc.get("token"),
        "application_id": doc.get("application_id"),
        "actor": doc.get("actor"),
        "kind": doc.get("kind") or COMMAND_INTERACTION,
    }
