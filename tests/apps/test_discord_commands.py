"""t16: slash and message commands over the Discord Gateway (fakes, no network)."""

from __future__ import annotations

import asyncio
import logging
import time
import types
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.apps import discord_commands as dc
from culture_rules.apps import discord_gateway as dg
from culture_rules.events.hook_sink import event_id_for
from culture_rules.model.discord_command_config import DEFAULT_NOT_HERE, definitions
from culture_rules.store.memory import MemoryStore
from tests.apps.discord_fakes import eventually, interaction
from tests.apps.test_discord_gateway import EVENT, Rig, discord_actor, discord_rule

COMMAND = "discord.command.invoked"
APP_ID = "777000"
SAVE = {
    "name": "save",
    "type": "slash",
    "description": "Save a message to the library",
    "options": [{"name": "message", "description": "A message link or id", "kind": "message"}],
}
MENU = {"name": "Save to library", "type": "message"}
COMMANDS = (SAVE, MENU)


def command_actor(
    *,
    app_id=APP_ID,
    guild="1",
    events=(EVENT, COMMAND),
    command_guild=None,
    commands=COMMANDS,
    **kw,
):
    doc = discord_actor(events=events, guild=guild, **kw)
    if commands is not None:
        doc["params"]["commands"] = [dict(c) for c in commands]
    if app_id is not None:
        doc["params"]["connection"]["application_id"] = app_id
    if command_guild is not None:
        doc["params"]["connection"]["command_guild_id"] = command_guild
    return doc


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


# ---------------------------------------------------------------- definitions


def test_no_command_is_built_in():
    # d12: which commands a bot offers is configuration (params.commands), never code
    assert not hasattr(dc, "DEFINITIONS")
    assert not hasattr(dc, "SAVE_COMMAND")
    assert "save" not in dc.NOT_SAVED.casefold()


def test_the_definition_hash_is_stable_and_covers_the_application_id_and_commands():
    defs = definitions(COMMANDS)
    first, again = dc.definition_hash(APP_ID, defs), dc.definition_hash(APP_ID, defs)
    assert first == again
    assert dc.definition_hash("778000", defs) != first
    changed = definitions([{**SAVE, "description": "something else"}, MENU])
    assert dc.definition_hash(APP_ID, changed) != first


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("https://discord.com/channels/1/100/9001", ("100", "9001")),
        ("https://ptb.discord.com/channels/1/100/9001", ("100", "9001")),
        ("https://discordapp.com/channels/@me/100/9001", ("100", "9001")),
        ("9001", (None, "9001")),
        (" 9001 ", (None, "9001")),
        ("not a link", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_a_message_option_is_a_link_or_an_id(value, want):
    assert dc.parse_message_ref(value) == want


def test_command_data_never_carries_the_token():
    raw = interaction(1)
    data = dc.command_data(raw)
    assert "token" not in data
    assert "application_id" not in data
    assert set(data) == {
        "command",
        "command_type",
        "options",
        "target_message_id",
        "target_channel_id",
        "interaction_id",
        "user_id",
        "username",
        "user_roles",
        "bot",
        "channel_id",
        "guild_id",
        "author_id",
        "author_name",
    }
    assert (data["author_id"], data["author_name"]) == ("42", "alice")


# ---------------------------------------------------------------- token collection


def test_the_token_is_kept_by_event_id_and_expires():
    store = MemoryStore()
    now = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
    secret = "itok-" + uuid.uuid4().hex
    dc.remember_interaction(store, "hook_discord_x", secret, APP_ID, "bot", now=now)
    doc = store.get(dc.INTERACTIONS_COLLECTION, "hook_discord_x")
    assert doc["token"] == secret
    assert doc["application_id"] == APP_ID
    assert doc["actor"] == "bot"
    assert doc["expires_at"] == now + dc.INTERACTION_TOKEN_TTL
    assert dc.INTERACTION_TOKEN_TTL == timedelta(minutes=15)
    got = dc.interaction_token(store, "hook_discord_x", now=now + timedelta(minutes=14))
    assert got == {"token": secret, "application_id": APP_ID, "actor": "bot"}
    assert dc.interaction_token(store, "hook_discord_x", now=now + timedelta(minutes=15)) is None
    assert dc.interaction_token(store, "missing", now=now) is None


def test_a_naive_stored_expiry_is_read_as_utc():
    store = MemoryStore()
    store.put(
        dc.INTERACTIONS_COLLECTION,
        {
            "id": "e",
            "token": "t",
            "application_id": APP_ID,
            "actor": "bot",
            "expires_at": datetime(2026, 10, 26, 12, 15),  # MongoDB without tz_aware
        },
    )
    assert dc.interaction_token(store, "e", now=datetime(2026, 10, 26, 12, 14, tzinfo=UTC))
    assert dc.interaction_token(store, "e", now=datetime(2026, 10, 26, 12, 16, tzinfo=UTC)) is None


def test_the_supervisor_ensures_the_interaction_ttl_index():
    calls = []

    class Indexed(MemoryStore):
        def ensure_index(self, collection, keys, *, name, partial=None, ttl_seconds=None):
            calls.append((collection, keys, name, ttl_seconds))

    dg.GatewaySupervisor(Indexed(), "engine@spark", gateway=types.SimpleNamespace())
    assert (
        dc.INTERACTIONS_COLLECTION,
        [("expires_at", 1)],
        dc.INTERACTIONS_TTL_INDEX,
        0,
    ) in calls


# ---------------------------------------------------------------- supervisor


def test_the_command_event_starts_a_listener():
    assert COMMAND in dg.LISTENED_EVENTS


def test_the_holder_registers_per_guild_and_reconnects_do_not_resync(rig):
    r = rig()
    r.store.put("actors", command_actor(guild=["1", "2"]))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    assert eventually(lambda: sorted(r.hub.syncs) == [("spark", "1"), ("spark", "2")])
    assert r.hub.registrations[-1].application_id == APP_ID
    assert r.hub.registrations[-1].guild_ids == ("1", "2")
    r.hub.drop()  # reconnect: the definitions did not change, so nothing is synced again
    assert eventually(lambda: len(r.hub.connects) == 2 and r.hub.live_now() == ["spark"])
    time.sleep(0.05)
    assert sorted(r.hub.syncs) == [("spark", "1"), ("spark", "2")]
    state = r.store.get(dc.COMMAND_SYNC_COLLECTION, "bot:1")
    assert state["hash"] == dc.definition_hash(APP_ID, definitions(COMMANDS))


@pytest.mark.parametrize(
    ("guild", "command_guild", "want"),
    [
        (["1", "2"], None, {"1", "2"}),  # unset: every guild in guild_id
        (["1", "2"], "2", {"2"}),
        (["1", "2"], ["2", "3"], {"2"}),  # a guild outside guild_id is ignored
        (None, ["2"], {"2"}),  # guild_id unset (every guild): the list stands
        (None, None, set()),
        (["1"], [""], {"1"}),  # a list of only blanks is unset
        (["1", "x"], None, {"1"}),  # not an id
    ],
)
def test_command_guilds_are_command_guild_id_within_guild_id(guild, command_guild, want):
    assert dc.command_guild_ids(guild, command_guild) == frozenset(want)


def test_commands_register_only_in_the_command_guilds(rig):
    r = rig()
    r.store.put("actors", command_actor(guild=["1", "2"], command_guild=["2"]))
    r.sup.tick()
    assert eventually(lambda: r.hub.syncs == [("spark", "2")])
    assert r.hub.registrations[-1].guild_ids == ("2",)


def test_an_invocation_outside_the_command_guilds_is_answered_not_here(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule(type=COMMAND))
    r.store.put("actors", command_actor(guild=["1", "2"], command_guild="2"))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.command(interaction(1, guild="1"))  # e.g. before Discord drops the old commands
    r.hub.command(interaction(2, guild="2"))
    assert eventually(lambda: len(r.hub.answers) == 2)
    assert r.hub.answers == [False, True]  # False is answered NOT_HERE
    assert [e["envelope"]["data"]["guild_id"] for e in r.events(COMMAND)] == ["2"]


def test_a_guild_left_out_has_its_commands_withdrawn_and_its_record_dropped(rig):
    r = rig()
    for guild in ("1", "2"):
        r.store.put(
            dc.COMMAND_SYNC_COLLECTION,
            {"id": f"bot:{guild}", "actor": "bot", "guild_id": guild, "hash": "old"},
        )
    r.store.put(
        dc.COMMAND_SYNC_COLLECTION,
        {"id": "other:1", "actor": "other", "guild_id": "1", "hash": "old"},
    )
    r.store.put("actors", command_actor(guild=["1", "2"], command_guild="2"))
    r.sup.tick()
    assert eventually(lambda: r.hub.withdrawals == [("spark", "1")])
    assert r.hub.registrations[-1].withdrawn == ("1",)
    assert r.store.get(dc.COMMAND_SYNC_COLLECTION, "bot:1") is None
    assert r.store.get(dc.COMMAND_SYNC_COLLECTION, "other:1") is not None  # another actor's


def test_changing_where_commands_go_reconnects_the_listener(rig):
    r = rig()
    r.store.put("actors", command_actor(guild=["1", "2"], command_guild="1"))
    r.sup.tick()
    assert eventually(lambda: r.hub.syncs == [("spark", "1")])
    r.store.put("actors", command_actor(guild=["1", "2"], command_guild=["1", "2"]))
    r.sup.tick()
    assert eventually(lambda: len(r.hub.connects) == 2 and r.hub.live_now() == ["spark"])
    assert eventually(lambda: r.hub.registrations[-1].guild_ids == ("1", "2"))
    assert ("spark", "2") in r.hub.syncs


def test_setting_the_application_id_later_reconnects_and_registers(rig):
    r = rig()
    r.store.put("actors", command_actor(app_id=None))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.syncs == [("spark", "1")])
    assert len(r.hub.connects) == 2


def test_an_edit_that_leaves_commands_alone_does_not_reconnect(rig):
    r = rig()
    r.store.put("actors", command_actor(guild=["1", "2"], command_guild="1"))
    r.sup.tick()
    assert eventually(lambda: r.hub.syncs == [("spark", "1")])
    r.store.put("actors", command_actor(guild=["1", "2", "3"], command_guild="1"))
    r.sup.tick()
    r.sup.tick()
    assert len(r.hub.connects) == 1


def test_command_guilds_outside_guild_id_warn_about_command_guild_id(rig, caplog):
    caplog.set_level(logging.INFO, logger="culture_rules.apps.discord_gateway")
    r = rig()
    r.store.put("actors", command_actor(guild=["10"], command_guild="20"))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    assert set(r.hub.registrations) == {None}
    assert any("connection.command_guild_id" in m for m in caplog.messages)
    assert not any("connection.guild_id" in m for m in caplog.messages)


def test_a_changed_definition_hash_resyncs(rig):
    r = rig()
    r.store.put("actors", command_actor())
    r.store.put(
        dc.COMMAND_SYNC_COLLECTION,
        {"id": "bot:1", "actor": "bot", "guild_id": "1", "hash": "stale"},
    )
    r.sup.tick()
    assert eventually(lambda: r.hub.syncs == [("spark", "1")])
    assert r.store.get(dc.COMMAND_SYNC_COLLECTION, "bot:1")["hash"] == dc.definition_hash(
        APP_ID, definitions(COMMANDS)
    )


def test_without_an_application_id_or_guild_no_commands_are_registered(rig, caplog):
    caplog.set_level(logging.INFO, logger="culture_rules.apps.discord_gateway")
    r = rig()
    r.store.put("actors", command_actor(app_id=None))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])  # messages still listened to
    r.sup.tick()
    r.hub.drop()
    assert eventually(lambda: len(r.hub.connects) == 2)
    assert set(r.hub.registrations) == {None}
    assert r.hub.syncs == []
    assert len([m for m in caplog.messages if "application_id" in m]) == 1

    r2 = rig()
    r2.store.put("actors", command_actor(guild=None))
    r2.sup.tick()
    assert eventually(lambda: r2.hub.live_now() == ["spark"])
    assert set(r2.hub.registrations) == {None}
    assert any("guild_id" in m for m in caplog.messages)

    r3 = rig()
    r3.store.put("actors", command_actor(app_id="not-an-id", guild=["x", ""]))
    r3.sup.tick()
    assert eventually(lambda: r3.hub.live_now() == ["spark"])
    assert set(r3.hub.registrations) == {None}


def test_an_actor_with_no_commands_registers_nothing_and_says_so(rig, caplog):
    caplog.set_level(logging.INFO, logger="culture_rules.apps.discord_gateway")
    r = rig()
    r.store.put("actors", command_actor(commands=None))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    assert set(r.hub.registrations) == {None}
    assert any("params.commands" in m for m in caplog.messages)


def test_removing_every_command_withdraws_them_where_they_were(rig):
    r = rig()
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.syncs == [("spark", "1")])
    r.store.put("actors", command_actor(commands=[]))
    r.sup.tick()
    assert eventually(lambda: r.hub.withdrawals == [("spark", "1")])
    assert r.store.get(dc.COMMAND_SYNC_COLLECTION, "bot:1") is None


def test_dropping_the_command_event_withdraws_the_commands(rig):
    # review of #106: a bot that stops declaring commands must not leave dead ones behind
    r = rig()
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.syncs == [("spark", "1")])
    r.store.put("actors", command_actor(events=(EVENT,)))
    r.sup.tick()
    assert eventually(lambda: r.hub.withdrawals == [("spark", "1")])
    assert r.store.get(dc.COMMAND_SYNC_COLLECTION, "bot:1") is None


def test_editing_the_commands_reconnects_and_resyncs(rig):
    r = rig()
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.syncs == [("spark", "1")])
    r.store.put("actors", command_actor(commands=[MENU]))
    r.sup.tick()
    assert eventually(lambda: len(r.hub.syncs) == 2 and r.hub.live_now() == ["spark"])
    assert r.hub.registrations[-1].commands == (MENU,)


def test_an_actor_not_declaring_the_command_event_registers_nothing(rig):
    r = rig()
    r.store.put("actors", command_actor(events=(EVENT,)))
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    assert set(r.hub.registrations) == {None}


def test_an_invocation_is_sunk_and_its_token_kept_apart(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule(type=COMMAND))
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    secret = "itok-" + uuid.uuid4().hex
    r.hub.command(interaction(1, token=secret))
    assert eventually(lambda: len(r.events(COMMAND)) == 1)
    event = r.events(COMMAND)[0]
    d = event["envelope"]["data"]
    assert d["delivery_id"] == "bot:interaction:5001"
    assert (d["command"], d["command_type"]) == ("Save to library", "message")
    assert (d["target_message_id"], d["target_channel_id"]) == ("9001", "100")
    assert (d["user_id"], d["username"], d["user_roles"]) == ("42", "alice", ["7", "8"])
    assert (d["channel_id"], d["guild_id"], d["options"]) == ("100", "1", {})
    assert (d["author_id"], d["author_name"]) == ("42", "alice")
    assert secret not in repr(event)
    assert event["id"] == event_id_for("discord", "bot:interaction:5001")
    kept = r.store.get(dc.INTERACTIONS_COLLECTION, event["id"])
    assert kept["token"] == secret
    assert kept["application_id"] == APP_ID


def test_a_slash_invocation_carries_its_options(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule(type=COMMAND))
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    link = "https://discord.com/channels/1/200/9555"
    r.hub.command(
        interaction(
            2,
            command="save",
            command_type="slash",
            options={"message": link},
            target_message_id="9555",
            target_channel_id="200",
        )
    )
    assert eventually(lambda: len(r.events(COMMAND)) == 1)
    d = r.events(COMMAND)[0]["envelope"]["data"]
    assert (d["command"], d["options"]) == ("save", {"message": link})
    assert (d["target_message_id"], d["target_channel_id"]) == ("9555", "200")


def test_an_unselected_channel_writes_neither_event_nor_token(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule(type=COMMAND, channels=["100"]))
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    r.hub.command(interaction(1, channel="999"))
    r.hub.command(interaction(2))
    assert eventually(lambda: len(r.events(COMMAND)) == 1)
    time.sleep(0.05)
    assert [d["id"] for d in r.store.find(dc.INTERACTIONS_COLLECTION)] == [
        r.events(COMMAND)[0]["id"]
    ]


def test_a_command_never_logs_its_token(rig, caplog):
    caplog.set_level(logging.DEBUG)
    r = rig(rule=False)
    r.store.put("rules", discord_rule(type=COMMAND))
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.live_now() == ["spark"])
    secret = "itok-" + uuid.uuid4().hex
    r.hub.command(interaction(1, token=secret))
    assert eventually(lambda: len(r.events(COMMAND)) == 1)
    assert secret not in caplog.text


# ---------------------------------------------------------------- discord.py glue


class _Response:
    def __init__(self, order):
        self.order = order

    async def defer(self, **kwargs):
        self.order.append(("defer", kwargs))


def _fake_interaction(order, *, token):
    return types.SimpleNamespace(
        id=5001,
        application_id=int(APP_ID),
        token=token,
        guild_id=1,
        channel_id=100,
        user=types.SimpleNamespace(
            id=42, name="alice", bot=False, roles=[types.SimpleNamespace(id=7)]
        ),
        response=_Response(order),
    )


def _registration(invoked, *, need=lambda g: True, synced=None, commands=COMMANDS):
    return dc.CommandRegistration(
        actor_id="bot",
        application_id=APP_ID,
        guild_ids=("1", "2"),
        commands=tuple(commands),
        invoke=invoked,
        needs_sync=need,
        synced=synced or (lambda g: None),
    )


def _client():
    discord = pytest.importorskip("discord")
    return discord.Client(intents=discord.Intents.none(), application_id=int(APP_ID))


def test_the_tree_holds_both_commands_per_guild():
    discord = pytest.importorskip("discord")
    tree = dg.build_command_tree(_client(), _registration(lambda _raw: None))
    for g in (1, 2):
        cmds = {c.name: c for c in tree.get_commands(guild=discord.Object(g))}
        assert set(cmds) == {"save", "Save to library"}
        assert cmds["Save to library"].type == discord.AppCommandType.message
        assert [p.name for p in cmds["save"].parameters] == ["message"]
        assert cmds["save"].parameters[0].required is False


def test_a_message_command_defers_ephemerally_before_the_event_is_written():
    discord = pytest.importorskip("discord")
    order: list = []
    secret = "itok-" + uuid.uuid4().hex

    def invoked(raw):
        order.append(("invoke", dict(raw)))

    tree = dg.build_command_tree(_client(), _registration(invoked))
    menu = tree.get_command(
        "Save to library", guild=discord.Object(1), type=discord.AppCommandType.message
    )
    target = types.SimpleNamespace(id=9001, channel=types.SimpleNamespace(id=100))
    asyncio.run(menu.callback(_fake_interaction(order, token=secret), target))
    assert order[0] == ("defer", {"ephemeral": True, "thinking": True})
    kind, raw = order[1]
    assert kind == "invoke"
    assert raw["token"] == secret
    assert raw["application_id"] == APP_ID
    assert raw["command"] == "Save to library"
    assert raw["command_type"] == "message"
    assert (raw["target_message_id"], raw["target_channel_id"]) == ("9001", "100")
    assert (raw["interaction_id"], raw["user_id"], raw["username"]) == ("5001", "42", "alice")
    assert (raw["user_roles"], raw["channel_id"], raw["guild_id"]) == (["7"], "100", "1")
    assert raw["options"] == {}


def test_a_slash_command_defers_then_passes_its_option():
    discord = pytest.importorskip("discord")
    order: list = []

    def invoked(raw):
        order.append(("invoke", dict(raw)))

    tree = dg.build_command_tree(_client(), _registration(invoked))
    cmd = tree.get_command("save", guild=discord.Object(1))
    link = "https://discord.com/channels/1/200/9555"
    asyncio.run(cmd.callback(_fake_interaction(order, token="t"), message=link))
    assert order[0][0] == "defer"
    raw = order[1][1]
    assert (raw["command"], raw["command_type"], raw["options"]) == (
        "save",
        "slash",
        {"message": link},
    )
    assert (raw["target_message_id"], raw["target_channel_id"]) == ("9555", "200")
    order.clear()
    asyncio.run(cmd.callback(_fake_interaction(order, token="t"), message=None))
    raw = order[1][1]
    assert raw["options"] == {}
    assert raw["target_message_id"] is None


def test_the_tree_holds_exactly_the_configured_commands():
    discord = pytest.importorskip("discord")
    digest = {
        "name": "digest",
        "type": "slash",
        "description": "Post a digest",
        "options": [
            {"name": "topic", "description": "What about", "required": True},
            {"name": "since", "description": "From when"},
        ],
    }
    pin = {"name": "Pin it", "type": "message"}
    tree = dg.build_command_tree(
        _client(), _registration(lambda _raw: None, commands=[digest, pin])
    )
    cmds = {c.name: c for c in tree.get_commands(guild=discord.Object(1))}
    assert set(cmds) == {"digest", "Pin it"}
    assert [(p.name, p.required) for p in cmds["digest"].parameters] == [
        ("topic", True),
        ("since", False),
    ]
    assert cmds["digest"].parameters[0].description == "What about"
    assert cmds["Pin it"].type == discord.AppCommandType.message


def test_a_slash_command_passes_string_options_and_no_target():
    discord = pytest.importorskip("discord")
    order: list = []
    digest = {
        "name": "digest",
        "type": "slash",
        "description": "Post a digest",
        "options": [{"name": "topic", "description": "What about", "required": True}],
    }
    tree = dg.build_command_tree(
        _client(), _registration(lambda raw: order.append(("invoke", dict(raw))), commands=[digest])
    )
    cmd = tree.get_command("digest", guild=discord.Object(1))
    asyncio.run(cmd.callback(_fake_interaction(order, token="t"), topic="jetson"))
    raw = order[1][1]
    assert (raw["command"], raw["options"]) == ("digest", {"topic": "jetson"})
    assert raw["target_message_id"] is None


def test_a_command_that_cannot_be_built_is_skipped_not_fatal(caplog):
    # review of #106: one bad command must not take the bot's listener down
    discord = pytest.importorskip("discord")
    bad = {**SAVE, "name": "bad", "options": [{"name": "from", "description": "x"}]}
    tree = dg.build_command_tree(_client(), _registration(lambda _raw: None, commands=[bad, MENU]))
    assert {c.name for c in tree.get_commands(guild=discord.Object(1))} == {"Save to library"}
    assert any("bad" in m and "skipped" in m for m in caplog.messages)


def test_a_slash_command_with_no_options_takes_none():
    discord = pytest.importorskip("discord")
    order: list = []
    ping = {"name": "ping", "type": "slash", "description": "Ping"}
    tree = dg.build_command_tree(
        _client(), _registration(lambda raw: order.append(("invoke", dict(raw))), commands=[ping])
    )
    cmd = tree.get_command("ping", guild=discord.Object(1))
    assert cmd.parameters == []
    asyncio.run(cmd.callback(_fake_interaction(order, token="t")))
    assert order[1][1]["options"] == {}


def test_a_withdrawn_guild_is_synced_empty_then_forgotten():
    pytest.importorskip("discord")
    forgotten: list[str] = []
    reg = dc.CommandRegistration(
        actor_id="bot",
        application_id=APP_ID,
        guild_ids=("2",),
        commands=COMMANDS,
        invoke=lambda _raw: None,
        needs_sync=lambda g: False,
        synced=lambda g: None,
        withdrawn=("1",),
        forgotten=forgotten.append,
    )
    tree = dg.build_command_tree(_client(), reg)
    sent: list[tuple[int, list]] = []

    async def fake_sync(*, guild=None):
        sent.append((guild.id, [c.name for c in tree.get_commands(guild=guild)]))
        return []

    tree.sync = fake_sync
    asyncio.run(dg.sync_commands(tree, reg))
    assert sent == [(1, [])]  # nothing registered there: Discord drops the old commands
    assert forgotten == ["1"]


def test_sync_only_the_guilds_that_need_it_and_a_failure_is_not_recorded(caplog):
    discord = pytest.importorskip("discord")
    synced: list[str] = []
    calls: list[int] = []
    reg = _registration(lambda _raw: None, need=lambda g: g == "2", synced=synced.append)
    tree = dg.build_command_tree(_client(), reg)

    async def fake_sync(*, guild=None):
        calls.append(guild.id)
        return []

    tree.sync = fake_sync
    asyncio.run(dg.sync_commands(tree, reg))
    assert calls == [2]
    assert synced == ["2"]

    async def failing_sync(*, guild=None):
        raise discord.HTTPException(types.SimpleNamespace(status=403, reason="x"), "secret-ish")

    tree.sync = failing_sync
    synced.clear()
    asyncio.run(dg.sync_commands(tree, reg))  # logged, not raised
    assert synced == []
    assert "HTTPException" in caplog.text
    assert "secret-ish" not in caplog.text


def _answering_interaction(order, *, token):
    fake = _fake_interaction(order, token=token)

    async def edit_original_response(**kw):
        order.append(("edit", kw))

    fake.edit_original_response = edit_original_response
    return fake


def test_an_invocation_that_is_not_written_is_answered_at_once():
    # t16 left it "thinking" until Discord dropped it: a channel no rule selects (t19)
    discord = pytest.importorskip("discord")
    order: list = []
    tree = dg.build_command_tree(_client(), _registration(lambda raw: False))
    menu = tree.get_command(
        "Save to library", guild=discord.Object(1), type=discord.AppCommandType.message
    )
    target = types.SimpleNamespace(id=9001, channel=types.SimpleNamespace(id=100))
    asyncio.run(menu.callback(_answering_interaction(order, token="t"), target))
    assert [kind for kind, _ in order] == ["defer", "edit"]
    assert order[1][1] == {"content": DEFAULT_NOT_HERE}


def test_a_command_s_own_not_here_answer_is_used():
    discord = pytest.importorskip("discord")
    order: list = []
    own = {**SAVE, "not_here": "Use #library for this."}
    tree = dg.build_command_tree(_client(), _registration(lambda raw: False, commands=[own]))
    cmd = tree.get_command("save", guild=discord.Object(1))
    asyncio.run(cmd.callback(_answering_interaction(order, token="t")))
    assert order[1][1] == {"content": "Use #library for this."}


def test_a_written_invocation_is_left_for_the_flow_to_answer():
    discord = pytest.importorskip("discord")
    order: list = []
    tree = dg.build_command_tree(_client(), _registration(lambda raw: True))
    cmd = tree.get_command("save", guild=discord.Object(1))
    asyncio.run(cmd.callback(_answering_interaction(order, token="t")))
    assert [kind for kind, _ in order] == ["defer"]


def test_the_supervisor_says_whether_the_command_event_was_written(rig):
    r = rig(rule=False)
    r.store.put("rules", discord_rule(type=COMMAND, channels=["100"]))
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.registrations)
    (registration,) = r.hub.registrations
    assert registration.invoke(interaction(1, channel="100")) is True
    assert registration.invoke(interaction(2, channel="555")) is False  # no rule selects it


def test_an_invocation_whose_write_failed_is_told_to_try_again():
    # review of the R3 diff: a store failure must not say the channel is not set up
    discord = pytest.importorskip("discord")
    order: list = []
    tree = dg.build_command_tree(_client(), _registration(lambda raw: None))
    cmd = tree.get_command("save", guild=discord.Object(1))
    asyncio.run(cmd.callback(_answering_interaction(order, token="t")))
    assert [kind for kind, _ in order] == ["defer", "edit"]
    assert order[1][1] == {"content": dc.NOT_SAVED}


def test_the_supervisor_returns_none_when_every_write_fails(rig, monkeypatch):
    r = rig(rule=False)
    r.store.put("rules", discord_rule(type=COMMAND, channels=["100"]))
    r.store.put("actors", command_actor())
    r.sup.tick()
    assert eventually(lambda: r.hub.registrations)
    (registration,) = r.hub.registrations

    def broken(*a, **kw):
        raise RuntimeError("store down")

    monkeypatch.setattr(dg, "sink", broken)
    monkeypatch.setattr(dg.time, "sleep", lambda s: None)
    assert registration.invoke(interaction(3, channel="100")) is None
