"""Release 3 end to end (discord-surfaces t19): a command and a sweep save to the library.

* ``/save`` (or Save to library) on a message: its ``discord.command.invoked`` event starts the
  shipped discord-library workflow with a ``discord.reply`` step added, which answers the
  invoker privately once the item is saved (the interaction token is kept apart, by event id).
* A sweep: ``discord.sweep`` reads the channel since its cursor, a ``for_each`` renders each
  message (``markdown.render`` reading ``inputs.item.*`` through ``config.with``, d8) and
  pushes it, then ``cursor.advance`` moves the cursor. A first run starts now; the next run
  saves what came since; a run with nothing new saves nothing.

One node runs both on doubles of Discord's REST API and culture-library.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from culture_rules.apps.discord_commands import remember_interaction
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.actions.cursor import (
    CURSOR_ADVANCE_BUILTIN,
    SWEEP_CURSORS_COLLECTION,
    CursorAdvancePort,
)
from culture_rules.node.actions.discord_read import DiscordReadAction
from culture_rules.node.actions.discord_reply import DiscordReplyAction
from culture_rules.node.actions.discord_sweep import DiscordSweepAction
from culture_rules.node.actions.library import LibraryPushPort
from culture_rules.node.actions.markdown_render import MARKDOWN_RENDER_BUILTIN, MarkdownRenderPort
from culture_rules.node.daemon import Node, NodeOptions
from culture_rules.node.runner import BuiltinCodePort, NoopAction
from culture_rules.store.memory import MemoryStore
from tests.apps.test_discord_gateway import discord_actor
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.events.fakes import FakeEventSource, envelope
from tests.node.test_node import BEATS
from tests.rules.test_discord_library_bundle import (
    BOT,
    BOT_GRANT,
    CHANNEL,
    GUILD,
    KIND,
    library_actor,
    rule,
    workflow,
)
from tests.rules.test_discord_reaction_flow import Library, action, edge, port, step

COMMAND = "discord.command.invoked"
TICK = "test.tick"
APP_ID, TOKEN = "777000", "itok-secret-1"
START = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


class Discord:
    """Discord's REST API: CHANNEL in GUILD with its messages, and the interaction webhook."""

    def __init__(self) -> None:
        self.messages: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.replies: list[str] = []

    def post(self, mid: str, text: str, *, bot: bool = False) -> None:
        self.messages[mid] = {
            "id": mid,
            "channel_id": CHANNEL,
            "content": text,
            "author": {"id": "7", "username": "ori", "bot": bot},
            "attachments": [],
        }

    def __call__(self, url, data, headers, timeout, method=None):
        parts = urlsplit(url)
        path, query = parts.path, parse_qs(parts.query)
        self.calls.append((method or ("POST" if data is not None else "GET"), path, headers))
        if method == "PATCH" and path.endswith("/messages/@original"):
            assert f"/webhooks/{APP_ID}/{TOKEN}/" in path
            import json

            self.replies.append(json.loads(data)["content"])
            return 200, {"id": "1"}, {}
        if path.endswith(f"/channels/{CHANNEL}"):
            return 200, {"id": CHANNEL, "guild_id": GUILD}, {}
        if path.endswith(f"/channels/{CHANNEL}/messages"):
            ids = sorted(self.messages, key=int)
            limit = int(query["limit"][0])
            if "after" in query:
                ids = [i for i in ids if int(i) > int(query["after"][0])][:limit]
            else:
                ids = ids[-limit:]
            return 200, [self.messages[i] for i in reversed(ids)], {}
        mid = path.rsplit("/", 1)[-1]
        if f"/channels/{CHANNEL}/messages/" in path and mid in self.messages:
            return 200, self.messages[mid], {}
        return 404, {"message": "Unknown"}, {}


def command_workflow() -> dict:
    wf = copy.deepcopy(workflow().to_dict())
    wf["id"] = wf["name"] = "discord-command-to-library"
    wf["inputs"].append(port("event_id"))
    reply = step(
        "reply",
        action("discord.reply", {"actor": BOT, "event_id": "inputs.event_id", "text": "Saved"}),
        inputs=[port("event_id"), port("saved", "integer")],
        outputs=[port("replied", "boolean")],
    )
    wf["steps"].append(reply)
    wf["edges"] += [
        edge("inputs", "event_id", "reply", "event_id"),
        edge("push", "item_id", "reply", "saved"),
    ]
    return wf


def command_rule() -> dict:
    doc = rule().to_dict()
    doc["id"], doc["enabled"] = "save-on-command", True
    doc["concurrency_key"] = "discord:{trigger.data.target_message_id}"  # the command's target
    doc["trigger"]["params"] = {"type": COMMAND, "channels": [CHANNEL]}
    doc["workflow"]["id"] = "discord-command-to-library"
    doc["workflow"]["inputs"] = {
        "title": "trigger.data.target_message_id",
        "channel": "trigger.data.target_channel_id",
        "id": "trigger.data.target_message_id",
        "guild": "trigger.data.guild_id",
        "event_id": "trigger.id",
    }
    return doc


def sweep_workflow() -> dict:
    lib = library_actor()["id"]
    sweep = step(
        "sweep",
        action("discord.sweep", {"actor": BOT, "channel": "inputs.channel", "cursor_key": "lib"}),
        inputs=[port("channel")],
        outputs=[
            port("messages", "array"),
            port("count", "integer"),
            port("more", "boolean"),
            port("cursor", "string", False),
            port("next_cursor", "string"),
            port("cursor_key", "string"),
        ],
    )
    render = step(
        "render",
        {
            "builtin": MARKDOWN_RENDER_BUILTIN,
            "with": {
                "title": "Discord message {{ inputs.item.id }}",
                "text": "inputs.item.content",
                "author": "inputs.item.author_name",
                "url": "inputs.item.url",
                "id": "inputs.item.id",
            },
        },
        inputs=[port("item", "object")],
        outputs=[port("markdown"), port("content_base64")],
    )
    push = step(
        "push",
        action(
            "library.push",
            {
                "actor": lib,
                "kind": KIND,
                "format": "md",
                "visibility": "private",
                "title": "Discord message {{ inputs.item.id }}",
                "content_base64": "inputs.content_base64",
            },
        ),
        inputs=[port("item", "object"), port("content_base64")],
        outputs=[port("item_id", "integer"), port("created", "boolean", False)],
    )
    each = step(
        "each",
        {},
        kind="for_each",
        inputs=[port("items", "array")],
        outputs=[port("item_id", "array")],
        body=[render, push],
        max_iterations=100,
    )
    advance = step(
        "advance",
        {"builtin": CURSOR_ADVANCE_BUILTIN},
        inputs=[
            port("key"),
            port("expected", "string", False),
            port("value"),
            port("saved", "array"),
        ],
        outputs=[port("advanced", "boolean")],
    )
    return {
        "id": "discord-sweep-to-library",
        "name": "discord-sweep-to-library",
        "description": "",
        "notes": "",
        "enabled": True,
        "schema_version": "1.0",
        "inputs": [port("channel")],
        "outputs": [],
        "steps": [sweep, each, advance],
        "edges": [
            edge("inputs", "channel", "sweep", "channel"),
            edge("sweep", "messages", "each", "items"),
            edge("render", "content_base64", "push", "content_base64"),
            edge("sweep", "cursor_key", "advance", "key"),
            edge("sweep", "cursor", "advance", "expected"),
            edge("sweep", "next_cursor", "advance", "value"),
            edge("each", "item_id", "advance", "saved"),
        ],
    }


def sweep_rule() -> dict:
    doc = rule().to_dict()
    doc["id"], doc["enabled"] = "sweep-to-library", True
    doc["concurrency_key"] = "discord-sweep:lib"  # one sweep of a flow at a time
    doc["trigger"]["params"] = {"type": TICK}
    doc["condition"] = None
    doc["workflow"]["id"] = "discord-sweep-to-library"
    doc["workflow"]["inputs"] = {"channel": {"$literal": CHANNEL}}
    return doc


class World:
    def __init__(self) -> None:
        self.clock = Clock(START)
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        bot = discord_actor(BOT, guild=GUILD, token=BOT_GRANT, events=(COMMAND,))
        bot["params"]["actions"] = ["discord.read", "discord.reply", "discord.sweep"]
        self.base.put("actors", bot)
        self.base.put("actors", library_actor())
        for doc in (command_workflow(), sweep_workflow()):
            self.base.put("workflows", doc)
        for doc in (command_rule(), sweep_rule()):
            self.base.put("rules", doc)
        self.discord, self.library = Discord(), Library()
        self.source = FakeEventSource(name="sub@spark")
        secret = lambda ref: f"secret-for-{ref}"  # noqa: E731
        ports = {
            "action:noop": NoopAction(),
            "action:discord.read": DiscordReadAction(
                self.base, resolve_secret=secret, transport=self.discord
            ),
            "action:discord.reply": DiscordReplyAction(
                self.base, transport=self.discord, clock=self.clock
            ),
            "action:discord.sweep": DiscordSweepAction(
                self.base, resolve_secret=secret, transport=self.discord
            ),
            "action:library.push": LibraryPushPort(
                self.base, transport=self.library, secret_runner=secret, clock=self.clock
            ),
            "code": BuiltinCodePort(
                {
                    MARKDOWN_RENDER_BUILTIN: MarkdownRenderPort(),
                    CURSOR_ADVANCE_BUILTIN: CursorAdvancePort(self.base),
                }
            ),
        }
        self.node = Node(
            self.base.peer(),
            "spark",
            actors=ports,
            event_source=self.source,
            clock=self.clock,
            heartbeat_options=BEATS,
            options=NodeOptions(listen_gateways=False),  # events come from the fake source
        )
        self.node.start()
        self.sent = 0

    def publish(self, type_: str, data: dict) -> str:
        self.sent += 1
        self.source.publish(envelope(self.sent, type=type_, source="discord://", data=data))
        for _ in range(30):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors
        return f"evt_{self.sent}"

    def command(self, target: str) -> None:
        # the gateway keeps the token apart, keyed by the event id, before the event lands
        remember_interaction(
            self.base, f"evt_{self.sent + 1}", TOKEN, APP_ID, BOT, now=self.clock()
        )
        self.publish(
            COMMAND,
            {
                "actor": BOT,
                "command": "save",
                "command_type": "slash",
                "options": {},
                "target_message_id": target,
                "target_channel_id": CHANNEL,
                "channel_id": CHANNEL,
                "guild_id": GUILD,
                "user_id": "7",
                "username": "ori",
                "author_id": "7",
                "author_name": "ori",
                "user_roles": [],
                "bot": False,
            },
        )

    def runs(self, rule_id: str) -> list[dict]:
        docs = self.base.find(RUNS_COLLECTION, {"rule_id": rule_id})
        return sorted(docs, key=lambda d: d["created_at"])


def test_both_workflows_validate():
    from culture_rules.model.validate import validate
    from culture_rules.model.workflow import Workflow

    for doc in (command_workflow(), sweep_workflow()):
        assert validate(Workflow.from_dict(doc)) == [], doc["id"]


def test_save_command_files_the_message_and_answers_privately():
    world = World()
    world.discord.post("900", "Read [this](https://example.test) today")
    world.command("900")
    (run,) = world.runs("save-on-command")
    assert run["status"] == "succeeded", run.get("error") or run["steps"]
    assert len(world.library.files) == 1
    assert world.discord.replies == ["Saved"]
    patch = [h for m, p, h in world.discord.calls if m == "PATCH"]
    assert patch
    assert "Authorization" not in patch[0]
    assert TOKEN not in repr(run)


def test_a_sweep_starts_now_then_saves_what_came_since_and_nothing_twice():
    world = World()
    world.discord.post("100", "old history")
    world.publish(TICK, {})
    first = world.runs("sweep-to-library")[-1]
    assert first["status"] == "succeeded", first.get("error") or first["steps"]
    assert world.library.files == {}  # a first run files no history
    assert world.base.get(SWEEP_CURSORS_COLLECTION, "lib:" + CHANNEL)["value"] == "100"

    world.discord.post("101", "new one")
    world.discord.post("102", "a bot's post", bot=True)
    world.discord.post("103", "another")
    world.publish(TICK, {})
    second = world.runs("sweep-to-library")[-1]
    assert second["status"] == "succeeded", second.get("error") or second["steps"]
    assert len(world.library.files) == 2  # 101 and 103; the bot's post is skipped
    assert world.base.get(SWEEP_CURSORS_COLLECTION, "lib:" + CHANNEL)["value"] == "103"

    world.publish(TICK, {})
    third = world.runs("sweep-to-library")[-1]
    assert third["status"] == "succeeded", third.get("error") or third["steps"]
    assert len(world.library.files) == 2
