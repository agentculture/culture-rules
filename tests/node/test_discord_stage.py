"""t26: the node's discord-gateway stage - one connection mesh-wide, takeover after lapse."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest

from culture_rules.actors import secrets
from culture_rules.apps.discord_gateway import GatewayOptions
from culture_rules.engine.named_lease import LEASES_COLLECTION
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.machines.probe import ProbeResult
from culture_rules.node.daemon import HeartbeatOptions, Node
from culture_rules.node.firing import run_id_for
from culture_rules.store.memory import MemoryStore
from tests.apps.discord_fakes import FakeGateway, FakeHub, eventually, message
from tests.apps.test_discord_gateway import Grant, discord_actor, discord_rule
from tests.engine.run_helpers import Clock, FakeActor, enrol_online, machine

START = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
TTL = timedelta(seconds=30)
EVENT = "discord.message.created"
BEATS = HeartbeatOptions(
    probe=lambda: ProbeResult(tools={}), load_reader=lambda: {}, engine_version="test"
)


class Mesh:
    def __init__(self, *hosts: str) -> None:
        self.clock = Clock(START)
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, *(machine(h) for h in hosts))
        self.hub = FakeHub()
        self.grant = Grant()
        self.base.put("actors", discord_actor())
        self.base.put("rules", discord_rule())
        self.nodes = {h: self.node(h) for h in hosts}

    def node(self, host: str) -> Node:
        return Node(
            self.base.peer(),
            host,
            actors={"*": FakeActor()},
            clock=self.clock,
            heartbeat_options=BEATS,
            discord_gateway=FakeGateway(self.hub, host),
            resolve_secret=partial(secrets.resolve, runner=self.grant),
            gateway_options=GatewayOptions(ttl=TTL, keeper=None, backoff=0.01, max_backoff=0.02),
        )

    def holder(self):
        doc = self.base.get(LEASES_COLLECTION, "discord-gateway:bot")
        return doc and doc.get("holder")

    def events(self):
        return [e for e in self.base.find(EVENTS_COLLECTION) if e["envelope"].get("type") == EVENT]

    def close(self) -> None:
        for n in self.nodes.values():
            n.close()
            assert not n.gateways.threads_alive()


@pytest.fixture
def mesh():
    made: list[Mesh] = []

    def make(*hosts: str) -> Mesh:
        m = Mesh(*hosts)
        made.append(m)
        return m

    yield make
    for m in made:
        m.close()


def test_two_nodes_never_hold_the_connection_at_once_and_take_over(mesh):
    m = mesh("spark", "thor")
    a, b = m.nodes["spark"], m.nodes["thor"]
    assert a.run_once().listening == ["bot"]
    assert b.run_once().listening == []
    assert eventually(lambda: m.hub.live_now() == ["spark"])
    for _ in range(3):
        m.clock.advance(5)
        a.run_once()
        assert b.run_once().listening == []
    assert m.holder() == "engine@spark"
    m.hub.post(message(1))
    m.hub.post(message(2))
    assert eventually(lambda: len(m.events()) == 2)

    # spark "crashes": it stops cycling (and renewing). It fences itself before expiry.
    m.clock.advance(TTL.total_seconds() * 2 / 3 + 1)
    assert eventually(lambda: m.hub.live_now() == [])
    assert b.run_once().listening == []  # the lease has not lapsed yet
    m.clock.advance(TTL.total_seconds() / 3)
    assert b.run_once().listening == ["bot"]
    assert m.holder() == "engine@thor"
    assert eventually(lambda: m.hub.live_now() == ["thor"])
    m.hub.post(message(3))  # thor's connection replayed 1 and 2: still once each
    assert eventually(lambda: len(m.events()) == 3)
    assert sorted(e["envelope"]["data"]["message_id"] for e in m.events()) == [
        "9001",
        "9002",
        "9003",
    ]
    assert m.hub.max_live == 1

    # spark comes back: it cannot win the lease while thor renews
    m.clock.advance(5)
    assert a.run_once().listening == []
    assert m.hub.max_live == 1


def test_graceful_close_hands_over_at_once(mesh):
    m = mesh("spark", "thor")
    a, b = m.nodes["spark"], m.nodes["thor"]
    a.run_once()
    assert eventually(lambda: m.hub.live_now() == ["spark"])
    a.close()
    assert m.hub.live_now() == []
    assert m.holder() is None
    assert b.run_once().listening == ["bot"]
    assert eventually(lambda: m.hub.live_now() == ["thor"])
    assert m.hub.max_live == 1


def test_disabling_the_actor_disconnects(mesh):
    m = mesh("spark")
    a = m.nodes["spark"]
    a.run_once()
    assert eventually(lambda: m.hub.live_now() == ["spark"])
    m.base.put("actors", discord_actor(enabled=False))
    assert a.run_once().listening == []
    assert m.hub.live_now() == []


def test_reconnect_lands_each_message_once(mesh, caplog):
    caplog.set_level(logging.DEBUG)
    m = mesh("spark")
    a = m.nodes["spark"]
    a.run_once()
    assert eventually(lambda: m.hub.live_now() == ["spark"])
    secret_text = "private-" + uuid.uuid4().hex
    m.hub.post(message(1, content=secret_text))
    m.hub.drop()
    m.hub.post(message(2))
    assert eventually(lambda: len(m.hub.connects) == 2 and len(m.events()) == 2)
    assert {c[1] for c in m.hub.connects} == {m.grant.value}
    assert m.grant.value not in caplog.text
    assert secret_text not in caplog.text


def test_run_closes_the_gateway_on_exit(mesh):
    m = mesh("spark")
    a = m.nodes["spark"]
    a.run(idle=0.0, max_cycles=1)
    assert m.hub.live_now() == []
    assert not a.gateways.threads_alive()
    assert m.holder() is None


# discord-surfaces t4 (R1): two Discord actors end to end


def _bot(id: str, token: str, me: str) -> dict:
    doc = discord_actor(id, token=token)
    doc["params"]["self_identity"] = me
    return doc


class _BotGateways:
    """One node's gateway client for two bots: each bot token opens its own session on its
    own hub (Discord delivers every message to every bot in the channel)."""

    def __init__(self, hubs: dict[str, FakeHub], host: str) -> None:
        self._by_name = {name: FakeGateway(hub, f"{host}:{name}") for name, hub in hubs.items()}

    def available(self) -> bool:
        return True

    def connect(
        self,
        token,
        on_message,
        should_stop,
        on_reaction=None,
        commands=None,
        events=(),
        on_button=None,
    ) -> None:
        name = token.split(":", 1)[0]  # the per-bot token below starts with its grant name
        self._by_name[name].connect(
            token,
            on_message,
            should_stop,
            on_reaction,
            commands=commands,
            events=events,
            on_button=on_button,
        )


def test_two_discord_actors_two_leases_two_events_and_no_cross_bot_firing():
    clock = Clock(START)
    base = MemoryStore(clock=clock)
    enrol_online(base, clock, machine("spark"))
    hubs = {"BOT_A": FakeHub(), "BOT_B": FakeHub()}
    salt = uuid.uuid4().hex
    node = Node(
        base.peer(),
        "spark",
        actors={"*": FakeActor()},
        clock=clock,
        heartbeat_options=BEATS,
        discord_gateway=_BotGateways(hubs, "spark"),
        resolve_secret=partial(secrets.resolve, runner=lambda name: f"{name}:{salt}"),
        gateway_options=GatewayOptions(ttl=TTL, keeper=None, backoff=0.01, max_backoff=0.02),
    )

    def events():
        return [e for e in base.find(EVENTS_COLLECTION) if e["envelope"].get("type") == EVENT]

    def post(msg) -> None:  # one Discord message reaches both bots
        for hub in hubs.values():
            hub.post(msg)

    try:
        base.put("rules", discord_rule())
        base.put("actors", _bot("bot-a", "grant:BOT_A", "culture-dev"))
        base.put("actors", _bot("bot-b", "grant:BOT_B", "jetson-bot"))
        assert node.run_once().listening == ["bot-a", "bot-b"]
        holders = {
            a: (base.get(LEASES_COLLECTION, f"discord-gateway:{a}") or {}).get("holder")
            for a in ("bot-a", "bot-b")
        }
        assert holders == {"bot-a": "engine@spark", "bot-b": "engine@spark"}  # two leases
        assert eventually(lambda: all(len(h.live_now()) == 1 for h in hubs.values()))
        _two_bots_flow(node, base, events, post)
    finally:
        node.close()
        assert not node.gateways.threads_alive()


def _two_bots_flow(node, base, events, post) -> None:
    # a human's message: both bots see it, so two events, and the rule fires on each
    post(message(1))
    assert eventually(lambda: len(events()) == 2)
    # bot-a posts (e.g. a reply): bot-b sees another bot's message, bot-a its own
    reply = message(2)
    reply.update(author_name="culture-dev", author_id="77", bot=True)
    post(reply)
    assert eventually(lambda: len(events()) == 4)
    by_message: dict[str, set[str]] = {}
    for ev in events():
        data = ev["envelope"]["data"]
        by_message.setdefault(data["message_id"], set()).add(data["actor"])
    assert by_message == {"9001": {"bot-a", "bot-b"}, "9002": {"bot-a", "bot-b"}}
    tags = {
        e["envelope"]["data"]["actor"]: e["envelope"]["data"]["self_authored"]
        for e in events()
        if e["envelope"]["data"]["message_id"] == "9002"
    }
    assert tags == {"bot-a": True, "bot-b": False}  # bot-b sees another bot (data.bot true)

    for _ in range(3):
        node.run_once()
    fired = sorted(
        (ev["envelope"]["data"]["message_id"], ev["envelope"]["data"]["actor"])
        for ev in events()
        if base.get(RUNS_COLLECTION, run_id_for("every-message", ev["id"])) is not None
    )
    # the human's message fires once per bot; the bot's post fires nothing on either bot
    assert fired == [("9001", "bot-a"), ("9001", "bot-b")]
