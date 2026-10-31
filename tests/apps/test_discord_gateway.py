"""t26: the Discord Gateway listener supervisor (fake gateway, no network)."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading
import time
import types
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
from culture_rules.model.action import Action
from culture_rules.model.actor import Actor
from culture_rules.model.rule import Rule, Trigger
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import TransientStoreError
from tests.apps.discord_fakes import FakeGateway, FakeHub, click, eventually, message, reaction
from tests.engine.run_helpers import Clock

ROOT = Path(__file__).resolve().parents[2]
EVENT = "discord.message.created"
REACTION = "discord.reaction.added"
TTL = timedelta(seconds=30)
SECRET_TEXT = "the-secret-plan-" + uuid.uuid4().hex


def discord_actor(
    id="bot", *, enabled=True, token="grant:DISCORD_BOT", channels=(), events=(EVENT,), guild="1"
):
    return Actor(
        id=id,
        name=id,
        kind="app",
        enabled=enabled,
        params={
            "surface": "discord",
            "events": list(events),
            "connection": {"bot_token": token, "guild_id": guild, "channels": list(channels)},
            "self_identity": "rulesbot",
        },
    ).to_dict()


def discord_rule(id="every-message", *, enabled=True, type=EVENT, **params):
    """An enabled rule on a discord.* trigger; with no ``channels`` it selects every channel."""
    return Rule(
        id=id,
        name=id,
        enabled=enabled,
        trigger=Trigger(kind="event", params={"type": type, **params}),
        action=Action(kind="noop"),
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
    def __init__(self, *, keeper=None, available=True, store=None, host=None, rule=True) -> None:
        self.clock = Clock()
        self.store = store or MemoryStore(clock=self.clock)
        if rule:  # the gateway sinks only channels an enabled discord.* rule selects
            self.store.put("rules", discord_rule())
            self.store.put("rules", discord_rule("every-reaction", type=REACTION))
        self.hub = FakeHub()
        self.grant = Grant()
        self.sup = dg.GatewaySupervisor(
            self.store,
            "engine@spark",
            gateway=FakeGateway(self.hub, "spark", available=available),
            resolve_secret=partial(secrets.resolve, runner=self.grant),
            clock=self.clock,
            options=dg.GatewayOptions(ttl=TTL, keeper=keeper, backoff=0.01, max_backoff=0.02),
            host=host,
        )

    def events(self, type=EVENT):
        return [e for e in self.store.find(EVENTS_COLLECTION) if e["envelope"].get("type") == type]


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
        "user_roles",
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
    data = ev["envelope"]["data"]
    assert data["delivery_id"] == "bot:" + data["message_id"]
    assert r.store.get(LEASES_COLLECTION, "discord-gateway:bot")["holder"] == "engine@spark"


def test_two_actors_sinking_the_same_message_record_two_events(rig):
    r = rig()
    other = Rig(store=r.store)  # a second supervisor and fake gateway on the same store
    try:
        r.store.put("actors", discord_actor("bot-a"))
        r.sup.tick()
        assert eventually(lambda: r.hub.live_now() == ["spark"])
        r.store.put("actors", discord_actor("bot-a", enabled=False))
        b = discord_actor("bot-b")
        b["params"]["self_identity"] = "otherbot"
        r.store.put("actors", b)
        other.sup.tick()
        assert eventually(lambda: other.hub.live_now() == ["spark"])
        r.store.put("actors", discord_actor("bot-a"))
        r.sup.tick()
        # the same Discord message (author "rulesbot") is seen by both bots; post it until
        # both listeners are ready (under CI load one could still be starting) - a repeat is
        # the same delivery id, a duplicate, so exactly two events prove one per bot
        same = message(1)
        same["author_name"] = "rulesbot"

        def both_seen() -> bool:
            r.hub.post(same)
            other.hub.post(same)
            return len(r.events()) >= 2

        assert eventually(both_seen)
        assert len(r.events()) == 2
        by_actor = {e["envelope"]["data"]["actor"]: e for e in r.events()}
        assert set(by_actor) == {"bot-a", "bot-b"}
        for actor_id, ev in by_actor.items():
            assert ev["envelope"]["source"] == f"app://{actor_id}"
            assert ev["envelope"]["data"]["message_id"] == "9001"
            assert ev["envelope"]["data"]["delivery_id"] == f"{actor_id}:9001"
        assert by_actor["bot-a"]["envelope"]["data"]["self_authored"] is True
        assert by_actor["bot-b"]["envelope"]["data"]["self_authored"] is False
    finally:
        other.sup.shutdown()


def test_one_actors_redelivery_of_a_message_is_a_duplicate(rig):
    r = rig()
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1))
    assert eventually(lambda: len(r.events()) == 1)
    r.hub.post(message(1))
    r.hub.drop()
    assert eventually(lambda: len(r.hub.connects) == 2)
    r.hub.post(message(2))
    assert eventually(lambda: len(r.events()) == 2)
    assert len(r.events()) == 2


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


def test_a_guild_id_string_ignores_other_guilds(rig):
    r = rig()
    r.store.put("actors", discord_actor(guild="1"))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1, guild="2"))
    r.hub.post(message(2, guild="1"))
    assert eventually(lambda: len(r.events()) == 1)
    assert r.events()[0]["envelope"]["data"]["guild_id"] == "1"


def test_a_guild_id_list_takes_messages_from_each_listed_guild(rig):
    r = rig()
    r.store.put("actors", discord_actor(guild=["1", "3"]))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1, guild="2"))
    r.hub.post(message(2, guild="1"))
    r.hub.post(message(3, guild="3"))
    assert eventually(lambda: len(r.events()) == 2)
    assert sorted(e["envelope"]["data"]["guild_id"] for e in r.events()) == ["1", "3"]


@pytest.mark.parametrize("unset", [None, "", []])
def test_an_unset_or_empty_guild_id_takes_every_guild(rig, unset):
    r = rig()
    r.store.put("actors", discord_actor(guild=unset))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1, guild="2"))
    r.hub.post(message(2, guild="1"))
    assert eventually(lambda: len(r.events()) == 2)


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


def test_only_discord_apps_declaring_a_gateway_event_are_listened_for(rig):
    r = rig()
    r.store.put("actors", discord_actor(events=("discord.thread.created",)))
    other = discord_actor("gh")
    other["params"]["surface"] = "github"
    r.store.put("actors", other)
    assert r.sup.tick() == []
    assert r.hub.connects == []


def test_an_actor_declaring_only_reactions_is_listened_for(rig):
    r = rig()
    r.store.put("actors", discord_actor(events=("discord.reaction.added",)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])


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


def test_an_actor_on_another_machine_is_not_listened_for(rig):
    """Its bot token is injected only on its own machine, so only that node takes the lease."""
    r = rig(host="spark")
    r.store.put("actors", {**discord_actor(), "machine": "thor"})
    assert r.sup.tick() == []
    assert r.hub.connects == []
    r.store.put("actors", {**discord_actor(), "machine": "spark"})
    r.sup.tick()
    assert eventually(lambda: len(r.hub.connects) == 1)


# discord-surfaces t4 (R1): only channels an enabled discord.* rule selects are sunk


def _ids(r):
    return sorted(e["envelope"]["data"]["message_id"] for e in r.events())


def test_no_enabled_discord_rule_sinks_nothing(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule("off", enabled=False))
    r.store.put("rules", discord_rule("gh", type="github.pr.merged"))
    r.store.put("actors", discord_actor())
    r.sup.tick()
    session = r.sup._sessions["bot"]
    r.sup._on_message(session, message(1))  # delivered synchronously: no listener race
    assert r.events() == []
    r.store.put("rules", discord_rule("on"))
    r.sup.tick()  # the selection is refreshed each tick
    r.sup._on_message(session, message(2))
    assert _ids(r) == ["9002"]


def test_a_channel_no_rule_selects_is_never_written(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule("a", channels=["100"]))
    r.store.put("rules", discord_rule("b", channels="300"))  # a bare string: one channel
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1, channel="200"))
    r.hub.post(message(2, channel="100"))
    r.hub.post(message(3, channel="300"))
    assert eventually(lambda: len(r.events()) == 2)
    assert sorted(e["envelope"]["data"]["channel_id"] for e in r.events()) == ["100", "300"]


@pytest.mark.parametrize("channels", ["all", "ALL", ["all"], [], None])
def test_a_rule_with_no_channels_or_all_selects_every_channel(rig, channels):
    r = rig(rule=False)
    r.store.put("rules", discord_rule("narrow", channels=["100"]))
    wide = {} if channels is None else {"channels": channels}
    r.store.put("rules", discord_rule("wide", **wide))
    r.store.put("actors", discord_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1, channel="200"))
    r.hub.post(message(2, channel="100"))
    assert eventually(lambda: len(r.events()) == 2)


def test_a_wide_reaction_rule_does_not_widen_the_message_selection(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule("narrow", channels=["100"]))
    r.store.put("rules", discord_rule("wide", type=REACTION))
    r.store.put("actors", discord_actor(events=(EVENT, REACTION)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1, channel="200"))
    r.hub.post(message(2, channel="100"))
    r.hub.react(reaction(3, channel="200"))
    assert eventually(lambda: len(r.events(REACTION)) == 1)
    assert eventually(lambda: len(r.events()) == 1)
    time.sleep(0.1)
    assert [e["envelope"]["data"]["channel_id"] for e in r.events()] == ["100"]


def test_a_message_rule_does_not_select_reaction_channels(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule("msgs"))
    r.store.put("rules", discord_rule("narrow", type=REACTION, channels=["100"]))
    r.store.put("actors", discord_actor(events=(EVENT, REACTION)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.react(reaction(1, channel="200"))
    r.hub.react(reaction(2, channel="100"))
    assert eventually(lambda: len(r.events(REACTION)) == 1)
    time.sleep(0.1)
    assert [e["envelope"]["data"]["channel_id"] for e in r.events(REACTION)] == ["100"]


def test_the_actor_allow_list_still_applies_inside_the_selection(rig):
    r = rig()  # the catch-all rule selects every channel
    r.store.put("actors", discord_actor(channels=["100"]))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1, channel="200"))
    r.hub.post(message(2, channel="100"))
    assert eventually(lambda: len(r.events()) == 1)
    assert _ids(r) == ["9002"]


def test_selected_channels_is_the_union_over_enabled_discord_rules():
    every = dg.selected_channels([discord_rule("a", channels=["1"]), discord_rule("b")])
    assert every is None  # None: every channel
    assert dg.selected_channels([]) == frozenset()
    assert dg.selected_channels(
        [
            discord_rule("a", channels=["1", 2]),
            discord_rule("b", channels="3"),
            discord_rule("off", enabled=False),
            discord_rule("gh", type="github.pr.merged"),
            {**discord_rule("gone"), "deleted_at": "2026-10-01T00:00:00Z"},
            {"id": "broken", "trigger": "not a trigger"},
        ]
    ) == frozenset({"1", "2", "3"})


def test_the_supervisor_ensures_the_discord_ttl_index(monkeypatch):
    calls = []
    monkeypatch.setattr(dg, "ensure_discord_event_ttl", calls.append)
    store = MemoryStore()
    dg.GatewaySupervisor(store, "engine@spark", gateway=FakeGateway(FakeHub(), "spark"))
    assert calls == [store]


def test_a_failed_rules_read_keeps_the_last_selection(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule(channels=["20"]))
    r.sup._refresh_selection()
    assert r.sup._selection[EVENT] == frozenset({"20"})

    def broken(*_a, **_k):
        raise OSError("mongo down")

    r.store.find = broken
    r.sup._refresh_selection()
    assert r.sup._selection[EVENT] == frozenset({"20"})


def test_a_failing_ttl_index_does_not_stop_the_supervisor(monkeypatch, caplog):
    def broken(_store):
        raise RuntimeError("index options conflict")

    monkeypatch.setattr(dg, "ensure_discord_event_ttl", broken)
    sup = dg.GatewaySupervisor(
        MemoryStore(), "engine@spark", gateway=FakeGateway(FakeHub(), "spark")
    )
    assert sup.listening() == []
    assert "ttl" in caplog.text.lower()


def test_no_discord_actor_means_no_rules_scan(rig):
    r = rig(rule=False)
    reads = []
    real = r.store.find
    r.store.find = lambda coll, *a, **k: (reads.append(coll), real(coll, *a, **k))[1]
    r.sup.tick()
    assert "rules" not in reads


def test_a_bare_string_connection_channels_is_one_channel(rig):
    r = rig()
    actor = discord_actor()
    actor["params"]["connection"]["channels"] = "20"  # one id, not the characters 2 and 0
    r.store.put("actors", actor)
    r.sup.tick()
    session = r.sup._sessions["bot"]
    r.sup._on_message(session, message(1, channel="20"))
    r.sup._on_message(session, message(2, channel="2"))
    assert _ids(r) == ["9001"]


# ------------------------------------------------------------------ reactions (R2)


def test_message_events_carry_user_roles(rig):
    r = rig()
    r.store.put("actors", discord_actor())
    msg = message(1)
    msg["user_roles"] = ["7", 8]
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(msg)
    assert eventually(lambda: len(r.events()) == 1)
    assert r.events()[0]["envelope"]["data"]["user_roles"] == ["7", "8"]
    assert dg.message_data(message(2))["user_roles"] == []


def test_a_reaction_is_sunk_as_discord_reaction_added(rig):
    r = rig()
    r.store.put("actors", discord_actor(events=(EVENT, REACTION)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.react(reaction(1))
    assert eventually(lambda: len(r.events(REACTION)) == 1)
    env = r.events(REACTION)[0]["envelope"]
    assert env["type"] == "discord.reaction.added"
    d = env["data"]
    assert d["delivery_id"] == "bot:9001:42:\N{THUMBS UP SIGN}"
    assert (d["guild_id"], d["channel_id"], d["message_id"]) == ("1", "100", "9001")
    assert (d["emoji_name"], d["emoji_id"]) == ("\N{THUMBS UP SIGN}", None)
    assert (d["user_id"], d["username"], d["bot"]) == ("42", "alice", False)
    assert d["user_roles"] == ["7", "8"]
    # the users filter reads author_id / author_name: the reactor fills them
    assert (d["author_id"], d["author_name"]) == ("42", "alice")


def test_a_custom_emoji_is_keyed_by_its_id(rig):
    r = rig()
    r.store.put("actors", discord_actor(events=(EVENT, REACTION)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.react(reaction(1, emoji="party", emoji_id="555"))
    assert eventually(lambda: len(r.events(REACTION)) == 1)
    d = r.events(REACTION)[0]["envelope"]["data"]
    assert (d["emoji_name"], d["emoji_id"]) == ("party", "555")
    assert d["delivery_id"] == "bot:9001:42:555"


def test_a_repeated_reaction_is_a_duplicate_but_another_emoji_or_user_is_not(rig):
    r = rig()
    r.store.put("actors", discord_actor(events=(EVENT, REACTION)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    for rx in (reaction(1), reaction(1), reaction(1, emoji="x"), reaction(1, user="43")):
        r.hub.react(rx)
    assert eventually(lambda: len(r.events(REACTION)) == 3)
    time.sleep(0.1)
    assert len(r.events(REACTION)) == 3


def test_a_reaction_goes_through_channel_selection(rig):
    r = rig(rule=False)
    r.store.put("actors", discord_actor(events=(EVENT, REACTION)))
    r.store.put("rules", discord_rule(type=REACTION, channels=["100"]))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.react(reaction(1, channel="999"))  # unselected: never written
    r.hub.react(reaction(2, channel="100"))
    assert eventually(lambda: len(r.events(REACTION)) == 1)
    time.sleep(0.1)
    assert [e["envelope"]["data"]["channel_id"] for e in r.events(REACTION)] == ["100"]


def test_selected_channels_for_one_event_type_ignores_other_types():
    docs = [discord_rule("m", channels=["1"]), discord_rule("r", type=REACTION)]
    assert dg.selected_channels(docs, EVENT) == frozenset({"1"})
    assert dg.selected_channels(docs, REACTION) is None
    assert dg.selected_channels(docs[:1], REACTION) == frozenset()


def test_an_undeclared_event_type_is_dropped_before_the_stats_and_the_sink(rig, monkeypatch):
    r = rig()  # the Release 1 actor: declares only message.created
    sunk = []
    monkeypatch.setattr(dg, "sink", lambda *a, **k: sunk.append(a))
    r.store.put("actors", discord_actor(events=(EVENT,)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.react(reaction(1))
    r.hub.post(message(1))
    assert eventually(lambda: len(sunk) == 1)
    assert sunk[0][2] == EVENT
    r.hub.react(reaction(2))
    time.sleep(0.1)
    assert len(sunk) == 1


def test_a_reaction_only_actor_drops_messages_without_a_stats_write(rig, monkeypatch):
    r = rig()
    sunk = []
    monkeypatch.setattr(dg, "sink", lambda *a, **k: sunk.append(a))
    r.store.put("actors", discord_actor(events=(REACTION,)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.post(message(1))
    r.hub.react(reaction(1))
    assert eventually(lambda: len(sunk) == 1)
    assert sunk[0][2] == REACTION


def test_the_gateway_is_told_the_declared_events(rig):
    r = rig()
    seen = []
    real = r.sup._gateway.connect  # noqa: SLF001

    def spy(*args, **kwargs):
        seen.append(kwargs.get("events"))
        return real(*args, **kwargs)

    r.sup._gateway.connect = spy  # noqa: SLF001
    r.store.put("actors", discord_actor(events=(REACTION,)))
    r.sup.tick()
    assert eventually(lambda: len(seen) == 1)
    assert list(seen[0]) == [REACTION]


def test_message_content_is_requested_only_for_message_actors():
    pytest.importorskip("discord")
    reaction_only = dg.gateway_intents([REACTION])
    assert reaction_only.message_content is False
    assert reaction_only.guild_reactions is True
    assert dg.gateway_intents([EVENT, REACTION]).message_content is True
    assert dg.gateway_intents([EVENT]).message_content is True


def test_the_reactions_intent_is_on():
    pytest.importorskip("discord")
    intents = dg.gateway_intents()
    assert intents.reactions
    assert intents.guild_reactions


def test_raw_reaction_payload_not_in_cache_raises_the_event():
    pytest.importorskip("discord")
    import discord

    got = []
    gw = dg.DiscordPyGateway()
    handler = gw._raw_reaction_handler(got.append)  # noqa: SLF001
    payload = types.SimpleNamespace(
        message_id=9001,
        channel_id=100,
        guild_id=1,
        user_id=42,
        member=types.SimpleNamespace(
            name="alice",
            bot=False,
            roles=[types.SimpleNamespace(id=1), types.SimpleNamespace(id=7)],
        ),
        emoji=discord.PartialEmoji(name="party", id=555),
    )
    asyncio.run(handler(payload))
    assert got == [
        {
            "message_id": "9001",
            "channel_id": "100",
            "guild_id": "1",
            "user_id": "42",
            "username": "alice",
            "user_roles": ["1", "7"],
            "bot": False,
            "emoji_name": "party",
            "emoji_id": "555",
        }
    ]
    payload.member = None  # no member (DM, or a member not resolved): still raised
    payload.emoji = discord.PartialEmoji(name="\N{THUMBS UP SIGN}")
    asyncio.run(handler(payload))
    assert got[1]["user_id"] == "42"
    assert got[1]["user_roles"] == []
    assert got[1]["emoji_id"] is None


# ---------------------------------------------------------------- button clicks

BUTTON = "discord.button.clicked"
MERGE_ID = "merge:agentculture/culture-rules:12:abc123"


def _click_rig(rig, **rule_params):
    r = rig(rule=False)
    r.store.put("rules", discord_rule("on-click", type=BUTTON, **rule_params))
    r.store.put("actors", discord_actor(events=(BUTTON,)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    return r


def _kept(r):
    from culture_rules.apps.discord_commands import INTERACTIONS_COLLECTION

    return list(r.store.find(INTERACTIONS_COLLECTION))


def test_the_button_event_is_a_listened_event():
    assert dg.BUTTON_EVENT == BUTTON
    assert BUTTON in dg.LISTENED_EVENTS


def test_an_actor_declaring_only_clicks_is_listened_for(rig):
    r = rig(rule=False)
    r.store.put("actors", discord_actor(events=(BUTTON,)))
    assert r.sup.tick() == ["bot"]


def test_a_click_becomes_one_button_event_and_its_token_is_kept_apart(rig):
    r = _click_rig(rig)
    secret = "itok-" + uuid.uuid4().hex
    r.hub.click(click(1, token=secret))
    assert eventually(lambda: len(r.events(BUTTON)) == 1)
    assert r.hub.answers == [True]
    event = r.events(BUTTON)[0]
    d = event["envelope"]["data"]
    assert d["delivery_id"] == "bot:interaction:5001"
    assert d["custom_id"] == MERGE_ID
    assert d["message_id"] == "9001"
    assert (d["channel_id"], d["guild_id"]) == ("100", "1")
    assert (d["author_id"], d["author_name"]) == ("42", "alice")
    assert d["user_roles"] == ["7", "8"]
    assert secret not in repr(event)
    assert event["id"] == dg.event_id_for("discord", "bot:interaction:5001")
    (kept,) = _kept(r)
    assert kept["id"] == event["id"]
    assert kept["token"] == secret
    assert kept["application_id"] == "777000"
    assert kept["kind"] == "button"


def test_button_data_carries_only_the_documented_keys():
    raw = click(1)
    raw["extra"] = "x"
    data = dg.button_data(raw)
    assert set(data) == {
        "custom_id",
        "message_id",
        "channel_id",
        "guild_id",
        "interaction_id",
        "user_id",
        "username",
        "author_id",
        "author_name",
        "user_roles",
        "bot",
        "value",
    }
    assert data["value"] is None  # never from the click itself, only from the store
    assert "token" not in data
    assert "application_id" not in data


def test_a_clicker_cannot_supply_the_value():
    raw = click(1)
    raw["value"] = {"number": 666}
    assert dg.button_data(raw)["value"] is None


def _store_value(r, custom_id, value):
    from culture_rules.apps.discord_buttons import remember_buttons

    remember_buttons(r.store, "bot", "100", [(custom_id, value)], now=r.sup._clock())


def test_a_click_carries_the_stored_value_as_data_value(rig):
    r = _click_rig(rig)
    value = {"repo": "o/r", "number": 12, "sha": "abc123"}
    _store_value(r, "merge:tok1", value)
    r.hub.click(click(1, custom_id="merge:tok1"))
    assert eventually(lambda: len(r.events(BUTTON)) == 1)
    assert r.events(BUTTON)[0]["envelope"]["data"]["value"] == value


def test_a_click_without_a_stored_value_carries_none(rig):
    r = _click_rig(rig)
    _store_value(r, "merge:other", {"n": 1})
    r.hub.click(click(1, custom_id="merge:tok1"))
    assert eventually(lambda: len(r.events(BUTTON)) == 1)
    assert r.events(BUTTON)[0]["envelope"]["data"]["value"] is None


def test_a_failed_value_lookup_still_records_the_click(rig, monkeypatch, caplog):
    r = _click_rig(rig)

    def broken(*_a, **_k):
        raise RuntimeError("store down")

    monkeypatch.setattr(dg.buttons_mod, "button_value", broken)
    r.hub.click(click(1, custom_id="merge:tok1"))
    assert eventually(lambda: len(r.events(BUTTON)) == 1)
    assert r.hub.answers == [True]
    assert r.events(BUTTON)[0]["envelope"]["data"]["value"] is None
    assert "button value" in caplog.text


def test_the_supervisor_ensures_the_buttons_ttl_index(monkeypatch):
    calls = []
    monkeypatch.setattr(dg.buttons_mod, "ensure_buttons_ttl", calls.append)
    store = MemoryStore()
    dg.GatewaySupervisor(store, "engine@spark", gateway=FakeGateway(FakeHub(), "spark"))
    assert calls == [store]


def test_a_repeated_click_delivery_is_a_duplicate(rig):
    r = _click_rig(rig)
    r.hub.click(click(1))
    r.hub.click(click(1))
    assert eventually(lambda: len(r.hub.answers) == 2)
    assert r.hub.answers == [True, True]
    assert len(r.events(BUTTON)) == 1


def test_a_click_no_rule_admits_writes_neither_event_nor_token(rig):
    r = _click_rig(rig, channels=["100"])
    r.hub.click(click(1, channel="999"))
    assert eventually(lambda: len(r.hub.answers) == 1)
    assert r.hub.answers == [False]
    assert r.events(BUTTON) == []
    assert _kept(r) == []


def test_another_users_click_on_a_users_filtered_rule_is_answered_privately(rig):
    r = _click_rig(rig, users=["42"])
    r.hub.click(click(1, user="43"))
    assert eventually(lambda: len(r.hub.answers) == 1)
    assert r.hub.answers == [False]  # private "not set up here", the buttons left enabled


def test_another_users_click_on_a_users_filtered_rule_writes_no_event(rig):
    r = _click_rig(rig, users=["42"])
    r.hub.click(click(1, user="43"))
    assert eventually(lambda: len(r.hub.answers) == 1)
    assert r.events(BUTTON) == []


def test_another_users_click_on_a_users_filtered_rule_keeps_no_token(rig):
    r = _click_rig(rig, users=["42"])
    r.hub.click(click(1, user="43"))
    assert eventually(lambda: len(r.hub.answers) == 1)
    assert _kept(r) == []


def test_the_listed_users_click_on_a_users_filtered_rule_is_admitted(rig):
    r = _click_rig(rig, users=["42"])
    r.hub.click(click(1, user="42"))
    assert eventually(lambda: len(r.events(BUTTON)) == 1)
    assert r.hub.answers == [True]


def test_a_click_a_roles_filtered_rule_admits_by_role_is_admitted(rig):
    r = _click_rig(rig, users=["99"], roles=["8"])
    r.hub.click(click(1, user="43"))
    assert eventually(lambda: len(r.events(BUTTON)) == 1)
    assert r.hub.answers == [True]


def test_a_click_whose_custom_id_no_rule_admits_is_not_admitted(rig):
    r = _click_rig(rig, custom_id="merge:")
    r.hub.click(click(1, custom_id="close:tok1"))
    assert eventually(lambda: len(r.hub.answers) == 1)
    assert r.hub.answers == [False]


def test_a_click_whose_custom_id_no_rule_admits_writes_no_event(rig):
    r = _click_rig(rig, custom_id="merge:")
    r.hub.click(click(1, custom_id="close:tok1"))
    assert eventually(lambda: len(r.hub.answers) == 1)
    assert r.events(BUTTON) == []


def test_a_click_one_of_several_rules_admits_is_admitted(rig):
    r = _click_rig(rig, custom_id="merge:")
    r.store.put("rules", discord_rule("on-close", type=BUTTON, custom_id="close:"))
    r.sup.tick()
    r.hub.click(click(1, custom_id="close:tok1"))
    assert eventually(lambda: len(r.events(BUTTON)) == 1)
    assert r.hub.answers == [True]


def test_a_disabled_rule_admitting_the_click_does_not_admit_it(rig):
    r = _click_rig(rig, users=["42"])
    r.store.put("rules", discord_rule("anyone", type=BUTTON, enabled=False))
    r.sup.tick()
    r.hub.click(click(1, user="43"))
    assert eventually(lambda: len(r.hub.answers) == 1)
    assert r.hub.answers == [False]


def test_a_click_for_an_actor_not_declaring_the_event_is_not_admitted(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule("on-click", type=BUTTON))
    r.store.put("actors", discord_actor(events=(EVENT,)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.click(click(1))
    assert eventually(lambda: len(r.hub.answers) == 1)
    assert r.hub.answers == [False]
    assert r.events(BUTTON) == []
    assert _kept(r) == []


def test_a_click_never_logs_its_token(rig, caplog):
    caplog.set_level(logging.DEBUG)
    r = _click_rig(rig)
    secret = "itok-" + uuid.uuid4().hex
    r.hub.click(click(1, token=secret))
    assert eventually(lambda: len(r.events(BUTTON)) == 1)
    assert secret not in caplog.text


def test_a_click_whose_write_fails_answers_none_and_drops_the_token(rig, monkeypatch):
    r = _click_rig(rig)

    def broken(*a, **kw):
        raise RuntimeError("store down")

    monkeypatch.setattr(dg, "sink", broken)
    monkeypatch.setattr(dg.time, "sleep", lambda s: None)
    r.hub.click(click(1))
    assert eventually(lambda: len(r.hub.answers) == 1)
    assert r.hub.answers == [None]
    assert _kept(r) == []


# ---------------------------------------------------------------- clicks: discord.py glue


class _ClickResponse:
    def __init__(self, order):
        self.order = order
        self.done = False

    def is_done(self):
        return self.done

    async def edit_message(self, **kw):
        self.done = True
        self.order.append(("update", kw))

    async def send_message(self, *args, **kw):
        self.done = True
        self.order.append(("send", args, kw))

    async def defer(self, **kw):
        self.done = True
        self.order.append(("defer", kw))


def _click_interaction(order, *, token="itok-1", component_type=2, kind=None):
    discord = pytest.importorskip("discord")
    row = discord.ActionRow(
        {
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": "Merge", "custom_id": MERGE_ID},
                {"type": 2, "style": 5, "label": "PR", "url": "https://github.com/x/y/pull/1"},
            ],
        }
    )
    message = types.SimpleNamespace(id=9001, components=[row])

    async def edit_original_response(**kw):
        order.append(("edit_original", kw))

    async def followup_send(*args, **kw):
        order.append(("followup", args, kw))

    return types.SimpleNamespace(
        id=5001,
        type=kind if kind is not None else discord.InteractionType.component,
        application_id=777000,
        token=token,
        guild_id=1,
        channel_id=100,
        data={"custom_id": MERGE_ID, "component_type": component_type},
        message=message,
        user=types.SimpleNamespace(
            id=42, name="alice", bot=False, roles=[types.SimpleNamespace(id=7)]
        ),
        response=_ClickResponse(order),
        edit_original_response=edit_original_response,
        followup=types.SimpleNamespace(send=followup_send),
    )


def _buttons(view):
    return [(b.label, b.style.value, b.custom_id, b.url, b.disabled) for b in view.children]


def test_an_admitted_click_is_answered_by_updating_the_message_with_its_buttons_disabled():
    pytest.importorskip("discord")
    order: list = []
    seen: list = []

    def handed(raw):
        seen.append(dict(raw))
        return True

    handler = dg.DiscordPyGateway._interaction_handler(handed)  # noqa: SLF001
    asyncio.run(handler(_click_interaction(order, token="itok-x")))
    (raw,) = seen
    assert raw["token"] == "itok-x"
    assert raw["application_id"] == "777000"
    assert raw["custom_id"] == MERGE_ID
    assert (raw["message_id"], raw["channel_id"], raw["guild_id"]) == ("9001", "100", "1")
    assert (raw["interaction_id"], raw["user_id"], raw["username"]) == ("5001", "42", "alice")
    assert raw["user_roles"] == ["7"]
    (answer,) = order
    assert answer[0] == "update"
    view = answer[1]["view"]
    assert _buttons(view) == [
        ("Merge", 3, MERGE_ID, None, True),
        ("PR", 5, None, "https://github.com/x/y/pull/1", True),
    ]
    assert view.is_finished()  # not kept in discord.py's view store


def test_a_click_no_rule_admits_is_answered_privately_and_leaves_the_prompt():
    pytest.importorskip("discord")
    order: list = []
    handler = dg.DiscordPyGateway._interaction_handler(lambda raw: False)  # noqa: SLF001
    asyncio.run(handler(_click_interaction(order)))
    assert order == [("send", (dg.BUTTON_NOT_HERE,), {"ephemeral": True})]


def test_a_click_whose_write_failed_is_told_to_try_again():
    pytest.importorskip("discord")
    from culture_rules.apps.discord_commands import NOT_SAVED

    order: list = []
    handler = dg.DiscordPyGateway._interaction_handler(lambda raw: None)  # noqa: SLF001
    asyncio.run(handler(_click_interaction(order)))
    assert order == [("send", (NOT_SAVED,), {"ephemeral": True})]


def test_a_hand_over_that_raises_is_answered_not_saved():
    pytest.importorskip("discord")
    from culture_rules.apps.discord_commands import NOT_SAVED

    def boom(raw):
        raise RuntimeError("x")

    order: list = []
    handler = dg.DiscordPyGateway._interaction_handler(boom)  # noqa: SLF001
    asyncio.run(handler(_click_interaction(order)))
    assert order == [("send", (NOT_SAVED,), {"ephemeral": True})]


def test_a_slow_hand_over_is_acknowledged_inside_the_window_then_disables_the_buttons():
    pytest.importorskip("discord")
    order: list = []
    release = threading.Event()

    def slow(raw):
        assert release.wait(5)  # held until the click is acknowledged
        return True

    interaction = _click_interaction(order)
    real_defer = interaction.response.defer

    async def defer(**kw):
        await real_defer(**kw)
        release.set()

    interaction.response.defer = defer
    handler = dg.DiscordPyGateway._interaction_handler(slow, ack_wait=0.01)  # noqa: SLF001
    asyncio.run(handler(interaction))
    assert order[0] == ("defer", {})  # a deferred update: the prompt is unchanged so far
    assert order[1][0] == "edit_original"
    assert [b[-1] for b in _buttons(order[1][1]["view"])] == [True, True]


def test_a_slow_hand_over_not_admitted_answers_with_a_private_follow_up():
    pytest.importorskip("discord")
    order: list = []

    def slow(raw):
        time.sleep(0.05)
        return False

    handler = dg.DiscordPyGateway._interaction_handler(slow, ack_wait=0.01)  # noqa: SLF001
    asyncio.run(handler(_click_interaction(order)))
    assert order == [("defer", {}), ("followup", (dg.BUTTON_NOT_HERE,), {"ephemeral": True})]


def test_the_ack_wait_stays_well_inside_discord_s_three_seconds():
    assert 0 < dg.BUTTON_ACK_WAIT_S <= 2.0


def test_commands_and_other_components_are_not_clicks():
    discord = pytest.importorskip("discord")
    seen: list = []
    handler = dg.DiscordPyGateway._interaction_handler(seen.append)  # noqa: SLF001
    order: list = []
    command = _click_interaction(order, kind=discord.InteractionType.application_command)
    asyncio.run(handler(command))
    select = _click_interaction(order, component_type=3)  # a select menu
    asyncio.run(handler(select))
    assert seen == []
    assert order == []


def test_a_sunk_click_is_matched_by_the_custom_id_and_users_filters(rig):
    from culture_rules.engine.matching import trigger_matches

    r = _click_rig(rig)
    r.hub.click(click(1))
    assert eventually(lambda: len(r.events(BUTTON)) == 1)
    envelope = r.events(BUTTON)[0]["envelope"]

    def trig(**params):
        return Trigger(kind="event", params={"type": BUTTON, **params})

    assert trigger_matches(trig(custom_id="merge:", users=["42"]), envelope)
    assert trigger_matches(trig(roles=["8"]), envelope)
    assert not trigger_matches(trig(custom_id="close:"), envelope)
    assert not trigger_matches(trig(users=["someone-else"]), envelope)
