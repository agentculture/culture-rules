"""GET /actors/{id}/discord/targets: the servers and channels a Discord app actor can post to."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.apps.test_discord_rest import guild_routes
from tests.server.conftest import ALICE, dev_app

TOKEN = "tok-" + uuid.uuid4().hex  # built at runtime: never a committed literal


def discord_actor(store, actor_id="disc", **connection):
    store.put(
        "actors",
        {
            "id": actor_id,
            "name": actor_id,
            "kind": "app",
            "params": {
                "surface": "discord",
                "events": ["discord.message.created"],
                "actions": ["discord.message"],
                "connection": {"bot_token": "grant:DISCORD_BOT", **connection},
            },
            "schema_version": "1.0",
        },
    )


def client_for(store, transport=None, resolve=None):
    resolved = []

    def fake_resolve(ref):
        resolved.append(ref)
        return TOKEN

    app = dev_app(store, resolve_secret=resolve or fake_resolve, discord_transport=transport)
    return TestClient(app), resolved


def test_lists_the_servers_and_channels_the_bot_can_post_to(store):
    discord_actor(store)
    t = guild_routes()
    client, resolved = client_for(store, t)
    r = client.get("/actors/disc/discord/targets", headers=ALICE)
    assert r.status_code == 200
    body = r.json()
    assert [g["id"] for g in body["guilds"]] == ["10", "20"]
    assert body["guilds"][0]["channels"][0] == {"id": "103", "name": "culture", "visible": False}
    assert resolved == ["grant:DISCORD_BOT"]
    assert TOKEN not in r.text


def test_a_guild_id_on_the_actor_limits_the_servers(store):
    discord_actor(store, guild_id="20")
    client, _ = client_for(store, guild_routes())
    body = client.get("/actors/disc/discord/targets", headers=ALICE).json()
    assert [g["id"] for g in body["guilds"]] == ["20"]


def test_unknown_actor_is_404_and_a_non_discord_actor_is_422(store):
    store.put("actors", {"id": "gh", "name": "gh", "kind": "app", "params": {"surface": "github"}})
    client, resolved = client_for(store, guild_routes())
    assert client.get("/actors/nope/discord/targets", headers=ALICE).status_code == 404
    r = client.get("/actors/gh/discord/targets", headers=ALICE)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "not_discord_actor"
    assert resolved == []


def test_an_unresolvable_token_is_a_guided_error_without_text(store):
    discord_actor(store)

    def boom(ref):
        raise RuntimeError("secret detail that must not leak")

    client, _ = client_for(store, guild_routes(), resolve=boom)
    r = client.get("/actors/disc/discord/targets", headers=ALICE)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "secret_unavailable"
    assert "must not leak" not in r.text


def test_a_discord_failure_is_502_discord_error(store):
    from tests.apps.test_discord_rest import Routes

    discord_actor(store)
    client, _ = client_for(store, Routes({"/users/@me/guilds": (401, {"message": "no"})}))
    r = client.get("/actors/disc/discord/targets", headers=ALICE)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "discord_error"
