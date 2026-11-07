"""``GET /actors/{actor_id}/discord/targets``: the servers and channels a Discord bot can see.

A contract route (``api/openapi.json``) the Discord extension mounts through the registry's
API routes (:meth:`~culture_rules.extensions.Extension.api_routes`), at the point the API
mounted it before the Boundary release, so the contract is unchanged. The ``server`` extra:
imported only when the API mounts it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from culture_rules.actors import secrets
from culture_rules.extensions import ApiContext
from culture_rules.extensions.discord.rest import DiscordClient, DiscordError, guild_ids
from culture_rules.server.app import ERRORS, ErrorEnvelope, _envelope
from culture_rules.server.service import NotFound
from culture_rules.store.port import StoragePort

__all__ = ["DiscordChannel", "DiscordGuild", "DiscordTargets", "register_targets"]


class DiscordChannel(BaseModel):
    id: str
    name: str
    visible: bool = Field(description="whether the bot can open the channel (private: added?)")


class DiscordGuild(BaseModel):
    id: str
    name: str
    channels: list[DiscordChannel]


class DiscordTargets(BaseModel):
    guilds: list[DiscordGuild]


def register_targets(app: FastAPI, ctx: ApiContext) -> None:
    """Mount ``GET /actors/{actor_id}/discord/targets`` (``ctx.options["discord_transport"]``
    replaces the Discord HTTP transport, tests)."""
    store, resolve_secret = ctx.store, ctx.resolve_secret
    transport = ctx.options.get("discord_transport")

    @app.get(
        "/actors/{actor_id}/discord/targets",
        tags=["actors"],
        operation_id="discord_targets",
        responses={
            404: ERRORS[404],
            409: ERRORS[409],
            422: ERRORS[422],
            502: {"model": ErrorEnvelope, "description": "Discord answered with an error"},
        },
    )
    def discord_targets(actor_id: str) -> DiscordTargets:
        """The servers and text channels a Discord app actor's bot can post to."""
        conn = _discord_connection(store, actor_id)
        if conn is None:
            return _envelope(422, "not_discord_actor", f"actor {actor_id!r} is not a Discord app")
        try:
            token = (resolve_secret or secrets.resolve)(str(conn.get("bot_token") or ""))
        except Exception:  # noqa: BLE001 - never echo why: the text may hold a value
            return _envelope(409, "secret_unavailable", "the bot token cannot be read here")
        kwargs = {"transport": transport} if transport is not None else {}
        try:
            guilds = DiscordClient(token, **kwargs).list_targets(guild_ids(conn.get("guild_id")))
        except DiscordError as exc:
            return _envelope(502, "discord_error", str(exc))
        return DiscordTargets(guilds=guilds)


def _discord_connection(store: StoragePort, actor_id: str) -> dict[str, Any] | None:
    """The connection block of Discord app actor ``actor_id``; ``None`` when the actor is not a
    Discord app. Raises :class:`NotFound` for a missing or deleted actor."""
    doc = store.get("actors", actor_id)
    if doc is None or doc.get("deleted_at"):
        raise NotFound(f"actor {actor_id!r} does not exist")
    params = doc.get("params") if isinstance(doc.get("params"), dict) else {}
    if doc.get("kind") != "app" or params.get("surface") != "discord":
        return None
    conn = params.get("connection")
    return conn if isinstance(conn, dict) else {}
