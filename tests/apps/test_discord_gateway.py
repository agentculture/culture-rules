"""t26: the Discord Gateway listener supervisor (fake gateway, no network)."""

from __future__ import annotations

import logging
import subprocess
import sys
import uuid
from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest

from culture_rules.actors import secrets
from culture_rules.apps import discord_gateway as dg
from culture_rules.engine.leasekeeper import LeaseKeeper
from culture_rules.engine.named_lease import LEASES_COLLECTION
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.model.actor import Actor
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import TransientStoreError
from tests.apps.discord_fakes import FakeGateway, FakeHub, eventually, message
from tests.engine.run_helpers import Clock

ROOT = Path(__file__).resolve().parents[2]
EVENT = "discord.message.created"
TTL = timedelta(seconds=30)
SECRET_TEXT = "the-secret-plan-" + uuid.uuid4().hex


def discord_actor(
    id="bot", *, enabled=True, token="grant:DISCORD_BOT", channels=(), events=(EVENT,)
):
    return Actor(
        id=id,
        name=id,
        kind="app",
        enabled=enabled,
        params={
            "surface": "discord",
            "events": list(events),
            "connection": {"bot_token": token, "guild_id": "1", "channels": list(channels)},
            "self_identity": "rulesbot",
        },
    ).to_dict()


class Grant:
    """Stands in for the ``grant get NAME`` call behind :func:`secrets.resolve`."""

    def __init__(self) -> None:
        self.value = "tok-" + uuid.uuid4().hex  # built at runtime: never a committed literal
        self.names: list[str] = []

    def __call__(self, name: str) -> str:
        self.names.append(name)
        return self.value


class Rig:
    def __init__(self, *, keeper=None, available=True, store=None) -> None:
        self.clock = Clock()
        self.store = store or MemoryStore(clock=self.clock)
        self.hub = FakeHub()
        self.grant = Grant()
        self.sup = dg.GatewaySupervisor(
            self.store,
            "engine@spark",
            gateway=FakeGateway(self.hub, "spark", available=available),
            resolve_secret=partial(secrets.resolve, runner=self.grant),
            clock=self.clock,
            options=dg.GatewayOptions(ttl=TTL, keeper=keeper, backoff=0.01, max_backoff=0.02),
        )

    def events(self):
        return [e for e in self.store.find(EVENTS_COLLECTION) if e["envelope"].get("type") == EVENT]


@pytest.fixture
def rig():
    made: list[Rig] = []

    def make(**kw) -> Rig:
        r = Rig(**kw)
        made.append(r)
        return r

    yield make
    for r in made:
        r.sup.shutdown()
        assert not r.sup.threads_alive()


def test_message_data_is_a_compact_truncated_subset():
    msg = message(1, content="x" * 5000)
    msg["extra"] = {"nested": True}
    data = dg.message_data(msg)
    assert set(data) == {
        "guild_id",
        "channel_id",
        "message_id",
        "author_id",
        "author_name",
        "bot",
        "content",
        "created_at",
        "url",
    }
    assert len(data["content"]) == dg.MAX_CONTENT
    assert data["message_id"] == "9001"


def test_connects_when_held_and_writes_each_message_once(rig):
    r = rig()
    r.store.put("actors", discord_actor())
    assert r.sup.tick() == ["bot"]
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    for n in range(3):
        r.hub.post(message(n))
    assert eventually(lambda: len(r.events()) == 3)
    ev = r.events()[0]
    assert ev["envelope"]["source"] == "app://bot"
    assert ev["envelope"]["data"]["delivery_id"] == ev["envelope"]["data"]["message_id"]
    assert r.store.get(LEASES_COLLECTION, "discord-gateway:bot")["holder"] == "engine@spark"


def test_reconnect_and_resume_replays_land_once(rig):
    r = rig()
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1))
    r.hub.post(message(2))
    assert eventually(lambda: len(r.events()) == 2)
    r.hub.drop()  # the connection drops; on reconnect the last messages are replayed
    assert eventually(lambda: len(r.hub.connects) == 2)
    r.hub.post(message(3))
    assert eventually(lambda: len(r.events()) == 3)
    assert sorted(e["envelope"]["data"]["message_id"] for e in r.events()) == [
        "9001",
        "9002",
        "9003",
    ]
    assert r.hub.max_live == 1


def test_channel_allow_list_ignores_other_channels(rig):
    r = rig()
    r.store.put("actors", discord_actor(channels=["100"]))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1, channel="200"))
    r.hub.post(message(2, channel="100"))
    assert eventually(lambda: len(r.events()) == 1)
    assert r.events()[0]["envelope"]["data"]["channel_id"] == "100"


def test_the_token_comes_only_from_grant(rig, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-" + uuid.uuid4().hex)
    monkeypatch.setenv("DISCORD_TOKEN", "env-" + uuid.uuid4().hex)
    r = rig()
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: len(r.hub.connects) == 1)
    assert r.hub.connects[0][1] == r.grant.value
    assert r.grant.names == ["DISCORD_BOT"]


def test_a_literal_token_is_refused(rig, caplog):
    caplog.set_level(logging.DEBUG)
    literal = "lit-" + uuid.uuid4().hex
    r = rig()
    r.store.put("actors", discord_actor(token=literal))
    assert r.sup.tick() == []
    assert r.sup.tick() == []
    assert r.hub.connects == []
    assert r.grant.names == []
    assert r.store.get(LEASES_COLLECTION, "discord-gateway:bot") is None
    assert literal not in caplog.text
    assert sum("grant" in rec.getMessage() for rec in caplog.records) == 1  # warned once


def test_disabling_the_actor_disconnects_and_releases(rig):
    r = rig()
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.store.put("actors", discord_actor(enabled=False))
    assert r.sup.tick() == []
    assert r.hub.live_now() == []
    assert r.store.get(LEASES_COLLECTION, "discord-gateway:bot")["holder"] is None


def test_deleting_the_actor_disconnects(rig):
    r = rig()
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.store.delete("actors", "bot")
    assert r.sup.tick() == []
    assert r.hub.live_now() == []


def test_only_discord_apps_declaring_the_event_are_listened_for(rig):
    r = rig()
    r.store.put("actors", discord_actor(events=("discord.reaction.added",)))
    other = discord_actor("gh")
    other["params"]["surface"] = "github"
    r.store.put("actors", other)
    assert r.sup.tick() == []
    assert r.hub.connects == []


def test_fence_disconnects_before_the_lease_can_lapse(rig):
    r = rig()
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.clock.advance(TTL.total_seconds() * 2 / 3 + 1)  # no renewal: past the fence, not expiry
    assert eventually(lambda: r.hub.live_now() == [])
    r.sup.tick()  # renews and reconnects
    assert eventually(lambda: r.hub.live_now() == ["spark"])


class FlakyStore(MemoryStore):
    fail = False

    def transaction(self):
        if self.fail:
            raise TransientStoreError("write conflict")
        return super().transaction()


def test_a_failed_renewal_disconnects_immediately(rig):
    clock = Clock()
    store = FlakyStore(clock=clock)
    r = rig(store=store, keeper=lambda renew, interval: LeaseKeeper(renew, 0.01))
    r.clock = clock
    r.sup._clock = clock
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    store.fail = True
    assert eventually(lambda: r.hub.live_now() == [])
    store.fail = False
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])


def test_missing_extra_logs_once_and_takes_no_lease(rig, caplog):
    caplog.set_level(logging.INFO)
    r = rig(available=False)
    r.store.put("actors", discord_actor())
    assert r.sup.tick() == []
    assert r.sup.tick() == []
    assert r.store.get(LEASES_COLLECTION, "discord-gateway:bot") is None
    assert sum("discord extra" in rec.getMessage() for rec in caplog.records) == 1


def test_logs_never_carry_the_token_or_content(rig, caplog):
    caplog.set_level(logging.DEBUG)
    r = rig()
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1, content=SECRET_TEXT))
    r.hub.drop()
    assert eventually(lambda: len(r.hub.connects) == 2 and len(r.events()) == 1)
    r.sup.shutdown()
    assert r.grant.value not in caplog.text
    assert SECRET_TEXT not in caplog.text


def test_intents_include_message_content():
    pytest.importorskip("discord")
    intents = dg.gateway_intents()
    assert intents.message_content
    assert intents.guilds
    assert intents.guild_messages


def test_module_imports_without_the_discord_extra():
    code = (
        "import sys\nsys.modules['discord'] = None\n"
        "import culture_rules.apps.discord_gateway as m\n"
        "assert m.DiscordPyGateway().available() is False\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert proc.returncode == 0, proc.stderr
