"""The shipped Discord -> library bundle (docs/rules/discord-library), as data and end to end.

It ships disabled with a placeholder channel. Its read step names the bot by id: an action
step's ``params.actor`` is never resolved as a reference (the live flow failed
``actor_unavailable`` on ``inputs.actor``), and the push files kind ``discord-message``
(culture-library#19). End to end, one node runs it on doubles of Discord's REST API and
culture-library: a message in the chosen channel becomes one private Markdown item, and the
same message again reports ``created: false``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.io.exchange import import_bundle, read_bundle
from culture_rules.model.action_step import action_spec
from culture_rules.node.actions.discord_read import DiscordReadAction
from culture_rules.node.actions.library import LibraryPushPort
from culture_rules.node.actions.markdown_render import MARKDOWN_RENDER_BUILTIN, MarkdownRenderPort
from culture_rules.node.daemon import Node
from culture_rules.node.runner import BuiltinCodePort, NoopAction
from culture_rules.store.memory import MemoryStore
from tests.apps.test_discord_gateway import discord_actor
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.events.fakes import FakeEventSource, envelope
from tests.node.test_node import BEATS
from tests.rules.test_newspaper_bundle import BUNDLE as NEWSPAPER_BUNDLE
from tests.rules.test_newspaper_bundle import LIBRARY, ROOT

BUNDLE = ROOT / "docs" / "rules" / "discord-library"
RULE, WORKFLOW = "discord-to-library", "discord-message-to-library"
BOT = "discord-bot"
KIND = "discord-message"
PLACEHOLDER = "000000000000000000"
GUILD, CHANNEL, MESSAGE = "11", "42", "900"
TEXT = "Read [the post](https://example.test/post) **today**"
START = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
PARAMS = "params"


def bundle():
    read = read_bundle(BUNDLE)
    assert read.errors == [], [e.to_dict() for e in read.errors]
    return read.bundle


def workflow():
    (wf,) = bundle().workflows
    return wf


def rule():
    (r,) = bundle().rules
    return r


def actions() -> dict[str, dict]:
    return {s.id: spec for s in workflow().steps if (spec := action_spec(s)) is not None}


# --------------------------------------------------------------------------- the data


def test_the_bundle_reads_without_errors_and_ships_no_actors():
    b = bundle()
    assert [w.id for w in b.workflows] == [WORKFLOW]
    assert [r.id for r in b.rules] == [RULE]
    assert list(b.actors) == []


def test_it_imports_disabled_with_the_placeholder_channel():
    base = MemoryStore()
    plan = import_bundle(BUNDLE, base, apply=True)
    assert plan.errors == []
    doc = base.get("rules", RULE)
    assert doc["enabled"] is False
    assert doc["trigger"][PARAMS] == {"type": "discord.message.created", "channels": [PLACEHOLDER]}


def test_the_read_names_its_bot_by_id():
    read = actions()["read"]
    assert read["kind"] == "discord.read"
    assert read[PARAMS]["actor"] == BOT


def test_the_push_files_a_private_markdown_discord_message():
    push = actions()["push"]
    assert push["kind"] == "library.push"
    assert {k: push[PARAMS][k] for k in ("actor", "kind", "format", "visibility")} == {
        "actor": LIBRARY,
        "kind": KIND,
        "format": "md",
        "visibility": "private",
    }


def test_the_rule_maps_only_inputs_the_workflow_declares():
    declared = {p.name for p in workflow().inputs}
    assert set(rule().workflow.inputs) == declared


# --------------------------------------------------------------------------- end to end


class Discord:
    """Discord's REST API: CHANNEL in GUILD, and one message in it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append((url, dict(headers)))
        path = urlsplit(url).path
        if path.endswith(f"/channels/{CHANNEL}"):
            return 200, {"id": CHANNEL, "guild_id": GUILD}, {}
        if path.endswith(f"/channels/{CHANNEL}/messages/{MESSAGE}"):
            body = {
                "id": MESSAGE,
                "channel_id": CHANNEL,
                "content": TEXT,
                "author": {"id": "7", "username": "ori"},
                "attachments": [],
            }
            return 200, body, {}
        return 404, {"message": "Unknown Message"}, {}


class Library:
    """culture-library's upload: deduplicates by content, as the real one does."""

    def __init__(self) -> None:
        self.uploads: list[bytes] = []
        self.items: dict[str, int] = {}

    def __call__(self, method, url, headers, body, timeout):
        assert urlsplit(url).path == "/api/library"
        self.uploads.append(body)
        digest = hashlib.sha256(_markdown_part(body)).hexdigest()
        created = digest not in self.items
        self.items.setdefault(digest, len(self.items) + 1)
        reply = {"item": {"id": self.items[digest]}, "created": created}
        return (201 if created else 200), json.dumps(reply).encode()


def _markdown_part(body: bytes) -> bytes:
    """The uploaded file's bytes (the multipart part after the ``.md`` filename)."""
    _head, _sep, rest = body.partition(b'.md"')
    return rest.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]


def library_actor() -> dict:
    """The newspaper bundle's library actor with the kind and format this flow needs added,
    as the bundle's README says."""
    (actor,) = [a for a in read_bundle(NEWSPAPER_BUNDLE).bundle.actors if a.id == LIBRARY]
    doc = copy.deepcopy(actor.to_dict())
    doc[PARAMS]["kinds"] = sorted({*doc[PARAMS]["kinds"], KIND})
    doc[PARAMS]["formats"] = sorted({*doc[PARAMS].get("formats", ["epub"]), "md"})
    return doc


class World:
    """spark running the shipped bundle, enabled on CHANNEL, on fakes."""

    def __init__(self) -> None:
        self.clock = Clock(START)
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        self.base.put("actors", discord_actor(BOT, guild=GUILD, events=()))
        self.base.put("actors", library_actor())
        self.base.put("workflows", workflow().to_dict())
        doc = rule().to_dict()
        doc["enabled"] = True
        doc["trigger"][PARAMS]["channels"] = [CHANNEL]
        self.base.put("rules", doc)
        self.discord, self.library = Discord(), Library()
        self.source = FakeEventSource(name="sub@spark")
        secret = lambda _ref: "fake-secret"  # noqa: E731
        ports = {
            "action:noop": NoopAction(),
            "action:discord.read": DiscordReadAction(
                self.base, resolve_secret=secret, transport=self.discord
            ),
            "action:library.push": LibraryPushPort(
                self.base, transport=self.library, secret_runner=secret, clock=self.clock
            ),
            "code": BuiltinCodePort({MARKDOWN_RENDER_BUILTIN: MarkdownRenderPort()}),
        }
        self.node = Node(
            self.base.peer(),
            "spark",
            actors=ports,
            event_source=self.source,
            clock=self.clock,
            heartbeat_options=BEATS,
        )
        self.node.start()
        self.sent = 0

    def post(self, channel: str = CHANNEL, **data) -> None:
        self.sent += 1
        payload = {
            "actor": BOT,
            "guild_id": GUILD,
            "channel_id": channel,
            "message_id": MESSAGE,
            "author_id": "7",
            "author_name": "ori",
            "content": TEXT,
            **data,
        }
        self.source.publish(
            envelope(self.sent, type="discord.message.created", source="discord://", data=payload)
        )
        for _ in range(20):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors

    def runs(self) -> list[dict]:
        docs = self.base.find(RUNS_COLLECTION, {"rule_id": RULE})
        return sorted(docs, key=lambda d: d["created_at"])


def push_outputs(run: dict) -> dict:
    (push,) = [s for s in run["steps"] if s["def"] == "push"]
    return push["outputs"]


def test_a_message_becomes_one_private_discord_message_item():
    world = World()
    world.post()
    (run,) = world.runs()
    assert run["status"] == "succeeded", run.get("error")
    assert push_outputs(run)["created"] is True
    (upload,) = world.library.uploads
    assert b'name="kind"\r\n\r\ndiscord-message' in upload
    assert b'name="visibility"\r\n\r\nprivate' in upload


def test_the_item_holds_the_whole_text_with_its_link():
    world = World()
    world.post()
    page = _markdown_part(world.library.uploads[0]).decode()
    assert TEXT in page
    assert f"https://discord.com/channels/{GUILD}/{CHANNEL}/{MESSAGE}" in page


def test_the_read_goes_through_the_named_bot():
    world = World()
    world.post()
    urls = [url for url, _headers in world.discord.calls]
    assert any(url.endswith(f"/channels/{CHANNEL}/messages/{MESSAGE}") for url in urls)
    assert {h.get("Authorization") for _url, h in world.discord.calls} == {"Bot fake-secret"}


def test_the_same_message_again_files_no_second_item():
    world = World()
    world.post()
    world.post()
    first, second = world.runs()
    assert second["status"] == "succeeded", second.get("error")
    assert push_outputs(first)["item_id"] == push_outputs(second)["item_id"]
    assert push_outputs(second)["created"] is False


def test_other_channels_and_bots_start_nothing():
    world = World()
    world.post(channel="43")
    world.post(bot=True)
    assert world.runs() == []
    assert world.library.uploads == []
