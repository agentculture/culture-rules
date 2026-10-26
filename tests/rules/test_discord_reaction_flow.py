"""Release 2 end to end (discord-surfaces t15): a reaction saves a message and its attachments.

A 📚 reaction on a message starts the shipped discord-library workflow, extended with the
Release 2 steps: ``discord.read`` -> ``markdown.render`` -> ``library.push`` (the text), then
``attachment.fetch`` (each pdf/epub to a file under a root on the node, d7) -> a ``for_each``
whose body pushes each fetched file by ``content_path`` -> ``discord.react`` (✅ once saved).
One node runs it on doubles of Discord's REST API, its CDN and culture-library.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.actions.attachment_fetch import (
    ATTACHMENT_FETCH_BUILTIN,
    AttachmentFetchPort,
)
from culture_rules.node.actions.discord_react import DiscordReactAction
from culture_rules.node.actions.discord_read import DiscordReadAction
from culture_rules.node.actions.library import LibraryPushPort
from culture_rules.node.actions.markdown_render import MARKDOWN_RENDER_BUILTIN, MarkdownRenderPort
from culture_rules.node.daemon import Node, NodeOptions
from culture_rules.node.runner import BuiltinCodePort, NoopAction
from culture_rules.store.memory import MemoryStore
from tests.apps.test_discord_gateway import discord_actor
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.events.fakes import FakeEventSource, envelope
from tests.node.test_attachment_fetch import EPUB, PDF, PUBLIC, FakeOpener, FakeResponse
from tests.node.test_node import BEATS
from tests.rules.test_discord_library_bundle import (
    BOT,
    BOT_GRANT,
    CHANNEL,
    GUILD,
    KIND,
    MESSAGE,
    TEXT,
    library_actor,
    rule,
    workflow,
)

REACTION = "discord.reaction.added"
SAVE, DONE = "📚", "✅"
START = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
CDN = "https://cdn.discordapp.com/attachments/42/{id}/{name}?ex=1&is=2&hm=3"
ATTACHMENTS = [
    {"id": "1", "filename": "paper.pdf", "content_type": "application/pdf", "size": len(PDF)},
    {"id": "2", "filename": "book.epub", "content_type": "application/epub+zip", "size": 9},
    {"id": "3", "filename": "photo.png", "content_type": "image/png", "size": 5},
]


def port(name, type_="string", required=True):
    return {"name": name, "type": type_, "required": required, "description": ""}


def action(kind, params, **extra):
    return {"builtin": "action", "idempotent": True, "action": {"kind": kind, "params": params}}


def step(id, config, inputs=(), outputs=(), **extra):
    return {
        "id": id,
        "name": id,
        "description": "",
        "notes": "",
        "kind": extra.pop("kind", "code"),
        "enabled": True,
        "config": config,
        "inputs": list(inputs),
        "outputs": list(outputs),
        "body": extra.pop("body", []),
        "max_iterations": extra.pop("max_iterations", None),
    }


def edge(source, source_port, target, target_port):
    return {
        "source": source,
        "source_port": source_port,
        "target": target,
        "target_port": target_port,
    }


def reaction_workflow(root: str) -> dict:
    """The shipped bundle workflow plus the Release 2 steps (all generic; config only)."""
    wf = copy.deepcopy(workflow().to_dict())
    wf["id"] = wf["name"] = "discord-reaction-to-library"
    read = next(s for s in wf["steps"] if s["id"] == "read")
    read["outputs"].append(port("attachments", "array", False))
    fetch = step(
        "fetch",
        {"builtin": ATTACHMENT_FETCH_BUILTIN, "output_root": root},
        inputs=[port("attachments", "array")],
        outputs=[port("items", "array"), port("fetched", "integer"), port("skipped", "integer")],
    )
    body = step(
        "file",
        {
            **action(
                "library.push",
                {
                    "actor": library_actor()["id"],
                    "kind": KIND,
                    "visibility": "private",
                    "format": "inputs.item.format",
                    "title": "inputs.item.filename",
                    "content_path": "inputs.item.path",
                    "content_root": root,
                },
            ),
            "when": {
                "op": "compare",
                "cmp": "==",
                "left": {"field": "item.skipped"},
                "right": {"literal": False},
            },
        },
        inputs=[port("item", "object")],
        outputs=[port("item_id", "integer", False), port("created", "boolean", False)],
    )
    files = step(
        "files",
        {},
        kind="for_each",
        inputs=[port("items", "array")],
        outputs=[port("item_id", "array")],
        body=[body],
        max_iterations=10,
    )
    react = step(
        "react",
        action(
            "discord.react",
            {"actor": BOT, "channel": "inputs.channel", "message": "inputs.id", "emoji": DONE},
        ),
        inputs=[port("channel"), port("id"), port("saved", "integer"), port("files", "array")],
        outputs=[port("reacted", "boolean")],
    )
    wf["steps"] += [fetch, files, react]
    wf["edges"] += [
        edge("read", "attachments", "fetch", "attachments"),
        edge("fetch", "items", "files", "items"),
        edge("inputs", "channel", "react", "channel"),
        edge("inputs", "id", "react", "id"),
        edge("push", "item_id", "react", "saved"),
        edge("files", "item_id", "react", "files"),
    ]
    return wf


def reaction_rule() -> dict:
    doc = rule().to_dict()
    doc["id"] = "save-on-reaction"
    doc["enabled"] = True
    doc["trigger"]["params"] = {"type": REACTION, "channels": [CHANNEL], "emoji": SAVE}
    doc["workflow"]["id"] = "discord-reaction-to-library"
    return doc


class Discord:
    """Discord's REST API: CHANNEL in GUILD, one message with three attachments, reactions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url, data, headers, timeout, method=None):
        path = urlsplit(url).path
        self.calls.append((method or ("POST" if data is not None else "GET"), path))
        if method == "PUT" and "/reactions/" in path:
            return 204, None, {}
        if path.endswith(f"/channels/{CHANNEL}"):
            return 200, {"id": CHANNEL, "guild_id": GUILD}, {}
        if path.endswith(f"/channels/{CHANNEL}/messages/{MESSAGE}"):
            atts = [{**a, "url": CDN.format(id=a["id"], name=a["filename"])} for a in ATTACHMENTS]
            body = {
                "id": MESSAGE,
                "channel_id": CHANNEL,
                "content": TEXT,
                "author": {"id": "7", "username": "ori"},
                "attachments": atts,
            }
            return 200, body, {}
        return 404, {"message": "Unknown"}, {}

    def reactions(self) -> list[str]:
        return [p for m, p in self.calls if m == "PUT"]


class Library:
    """culture-library's upload: deduplicates by the file's bytes, as the real one does."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[int, str]] = {}  # sha256 -> (item id, format)

    def __call__(self, method, url, headers, body, timeout):
        head, _, rest = body.partition(b'name="file"; filename="')
        filename = rest.split(b'"', 1)[0].decode()
        data = rest.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0].split(b"\r\n--")[0]
        digest = hashlib.sha256(data).hexdigest()
        created = digest not in self.files
        self.files.setdefault(digest, (len(self.files) + 1, filename.rsplit(".", 1)[-1]))
        reply = {"item": {"id": self.files[digest][0]}, "created": created}
        return (201 if created else 200), json.dumps(reply).encode()

    def formats(self) -> list[str]:
        return sorted(f for _, f in self.files.values())


class World:
    def __init__(self, root) -> None:
        self.clock = Clock(START)
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        bot = discord_actor(BOT, guild=GUILD, token=BOT_GRANT, events=(REACTION,))
        bot["params"]["actions"] = ["discord.read", "discord.react"]
        self.base.put("actors", bot)
        lib = library_actor()
        lib["params"]["formats"] = sorted({*lib["params"]["formats"], "pdf", "epub"})
        self.base.put("actors", lib)
        self.base.put("workflows", reaction_workflow(str(root)))
        self.base.put("rules", reaction_rule())
        self.discord, self.library = Discord(), Library()
        self.downloads: list[str] = []
        self.source = FakeEventSource(name="sub@spark")
        secret = lambda ref: f"secret-for-{ref}"  # noqa: E731
        fetch = AttachmentFetchPort(
            resolver=lambda host, p: [PUBLIC],
            opener_factory=lambda pinned: FakeOpener([{}], self._cdn),
            clock=self.clock,
        )
        ports = {
            "action:noop": NoopAction(),
            "action:discord.read": DiscordReadAction(
                self.base, resolve_secret=secret, transport=self.discord
            ),
            "action:discord.react": DiscordReactAction(
                self.base, resolve_secret=secret, transport=self.discord
            ),
            "action:library.push": LibraryPushPort(
                self.base, transport=self.library, secret_runner=secret, clock=self.clock
            ),
            "code": BuiltinCodePort(
                {MARKDOWN_RENDER_BUILTIN: MarkdownRenderPort(), ATTACHMENT_FETCH_BUILTIN: fetch}
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

    def _cdn(self, url):
        self.downloads.append(url)
        return FakeResponse(body=PDF if "/1/" in url else EPUB)

    def react(self, emoji: str = SAVE, **data) -> None:
        self.sent += 1
        payload = {
            "actor": BOT,
            "guild_id": GUILD,
            "channel_id": CHANNEL,
            "message_id": MESSAGE,
            "user_id": "7",
            "author_id": "7",
            "author_name": "ori",
            "username": "ori",
            "user_roles": [],
            "bot": False,
            "emoji_name": emoji,
            "emoji_id": None,
            **data,
        }
        self.source.publish(envelope(self.sent, type=REACTION, source="discord://", data=payload))
        for _ in range(30):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors

    def runs(self) -> list[dict]:
        docs = self.base.find(RUNS_COLLECTION, {"rule_id": "save-on-reaction"})
        return sorted(docs, key=lambda d: d["created_at"])


def test_the_extended_workflow_validates():
    from culture_rules.model.validate import validate
    from culture_rules.model.workflow import Workflow

    assert validate(Workflow.from_dict(reaction_workflow("/srv/attachments"))) == []


def test_a_reaction_saves_the_text_and_each_pdf_and_epub_then_reacts(tmp_path):
    world = World(tmp_path)
    world.react()
    (run,) = world.runs()
    assert run["status"] == "succeeded", run.get("error") or run["steps"]
    # the text as Markdown, then the pdf and the epub; the png is skipped, never downloaded
    assert world.library.formats() == ["epub", "md", "pdf"]
    assert len(world.downloads) == 2
    assert all("photo.png" not in url for url in world.downloads)
    assert len(world.discord.reactions()) == 1
    # d7: no file bytes in run state, only paths under the root
    assert "content_base64" not in json.dumps(
        [s for s in run["steps"] if s["def"] in ("fetch", "files", "file")]
    )


def test_another_emoji_starts_nothing(tmp_path):
    world = World(tmp_path)
    world.react(emoji="👍")
    assert world.runs() == []
    assert world.library.files == {}


def test_reacting_again_saves_no_second_item(tmp_path):
    world = World(tmp_path)
    world.react()
    world.react(user_id="8", author_id="8", author_name="dana", username="dana")
    first, second = world.runs()
    assert second["status"] == "succeeded", second.get("error") or second["steps"]
    assert len(world.library.files) == 3
