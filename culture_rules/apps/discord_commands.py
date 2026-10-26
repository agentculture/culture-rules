"""Discord application commands: the definitions, their hash, the event data and the token.

A Discord ``app`` actor that declares ``discord.command.invoked`` in ``params.events`` gets two
**guild** commands (guild commands appear at once, global ones can take an hour), registered
by the node holding its gateway lease (:mod:`culture_rules.apps.discord_gateway`):

- the slash command ``/save``, with one optional string option ``message`` (a message link or
  a bare message id); without it the event's ``target_message_id`` is ``None``;
- the message context-menu command ``Save to library`` (right-click a message, Apps).

Both need ``connection.application_id`` (the bot's application id, a literal) and
``connection.guild_id`` (one id or a list); without either nothing is registered.

Sync state
==========
A guild is synced only when :func:`definition_hash` (over :data:`DEFINITIONS` and the
application id) differs from the hash recorded for it in :data:`COMMAND_SYNC_COLLECTION`
(one ``{id: "<actor>:<guild>", actor, guild_id, hash, synced_at}`` document per actor and
guild), so a reconnect does not sync again. A command deleted by hand in Discord is not
restored until the definitions change (or the document is deleted).

Event data
==========
:func:`command_data` is the ``data`` of ``discord.command.invoked``: ``command`` (``save`` or
``Save to library``), ``command_type`` (``slash`` or ``message``), ``options`` (the given
options by name), ``target_message_id`` and ``target_channel_id`` (the right-clicked message;
for ``/save`` parsed from the option, :func:`parse_message_ref`), ``interaction_id``,
``user_id``, ``username``, ``user_roles``, ``bot``, ``channel_id``, ``guild_id``, and
``author_id`` / ``author_name`` (the invoking user, which the ``users`` trigger filter reads).

The interaction token
=====================
The token is **never** in the event. It is kept in :data:`INTERACTIONS_COLLECTION` as
``{id: <event id>, token, application_id, actor, expires_at}`` - the event id is
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

__all__ = [
    "CHAT_INPUT",
    "COMMAND_SYNC_COLLECTION",
    "DEFINITIONS",
    "INTERACTIONS_COLLECTION",
    "INTERACTIONS_TTL_INDEX",
    "INTERACTION_TOKEN_TTL",
    "MESSAGE",
    "NOT_HERE",
    "SAVE_COMMAND",
    "SAVE_MESSAGE_COMMAND",
    "CommandRegistration",
    "command_data",
    "definition_hash",
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
_STRING_OPTION = 3

SAVE_COMMAND = "save"
SAVE_MESSAGE_COMMAND = "Save to library"
MESSAGE_OPTION = "message"

DEFINITIONS: tuple[Mapping[str, Any], ...] = (
    {
        "type": CHAT_INPUT,
        "name": SAVE_COMMAND,
        "description": "Save a message to the library",
        "options": [
            {
                "type": _STRING_OPTION,
                "name": MESSAGE_OPTION,
                "description": "A message link or id",
                "required": False,
            }
        ],
    },
    {"type": MESSAGE, "name": SAVE_MESSAGE_COMMAND},
)
"""The commands registered per guild, in Discord's API shape (the hash is taken over this)."""

COMMAND_SYNC_COLLECTION = "discord_command_sync"
INTERACTIONS_COLLECTION = "discord_interactions"
INTERACTIONS_TTL_INDEX = "discord_interactions_ttl"
INTERACTION_TOKEN_TTL = timedelta(minutes=15)
_EXPIRES_AT = "expires_at"

_LINK = re.compile(
    r"^https?://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/(?:\d+|@me)/(\d+)/(\d+)/?$"
)
_ID = re.compile(r"^\d{1,25}$")


NOT_HERE = "This channel is not set up to save messages."
"""The private answer to a command whose event was not written (no rule selects the channel,
or the bot keeps out of it), instead of leaving it "thinking" until Discord drops it."""


@dataclass(frozen=True, kw_only=True)
class CommandRegistration:
    """What a gateway needs to register the commands and hand over invocations.

    ``invoke`` takes one raw invocation (a mapping with the :func:`command_data` keys plus
    ``token`` and ``application_id``) and says whether its event was written; one that was not
    is answered at once with :data:`NOT_HERE`; ``needs_sync(guild)`` says whether the guild's
    commands must be synced, and ``synced(guild)`` records that they were. ``actor_id`` is for
    logs."""

    actor_id: str
    application_id: str
    guild_ids: tuple[str, ...]
    invoke: Callable[[Mapping[str, Any]], bool | None]
    needs_sync: Callable[[str], bool]
    synced: Callable[[str], None]
    definitions: tuple[Mapping[str, Any], ...] = DEFINITIONS


def definition_hash(
    application_id: str, definitions: Iterable[Mapping[str, Any]] = DEFINITIONS
) -> str:
    """The sha256 of the definitions and the application id, in a canonical JSON form."""
    body = json.dumps(
        {"application_id": str(application_id), "commands": list(definitions)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _sync_id(actor_id: str, guild_id: str) -> str:
    return f"{actor_id}:{guild_id}"


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
) -> None:
    """Keep an interaction's token for the reply action, keyed by the event id."""
    store.put(
        INTERACTIONS_COLLECTION,
        {
            "id": event_id,
            "token": token,
            "application_id": application_id,
            "actor": actor_id,
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
    """``{token, application_id, actor}`` for the event's interaction, or ``None`` when there
    is none or it has expired."""
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
    }
