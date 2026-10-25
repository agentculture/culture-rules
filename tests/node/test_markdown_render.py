"""Built-in markdown.render: generic fields to Markdown + base64, no render-time state."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.library import LibraryPushPort
from culture_rules.node.actions.markdown_render import (
    MARKDOWN_RENDER_BUILTIN,
    MarkdownRenderPort,
)
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(minutes=5)
CTX = InvocationContext(run_id="run", step_id="s", kind="action", host="spark")
EVENT = {
    "guild_id": "1",
    "channel_id": "2",
    "message_id": "998877",
    "author_id": "3",
    "author_name": "ori",
    "bot": False,
    "content": "Remember the milk",
    "created_at": "2026-10-25T10:00:00+00:00",
    "url": "https://discord.com/channels/1/2/998877",
}


def _render(**over):
    data = {
        "title": "Note from ori",
        "text": EVENT["content"],
        "author": EVENT["author_name"],
        "source": "discord",
        "url": EVENT["url"],
        "id": EVENT["message_id"],
    }
    data.update(over)
    return MarkdownRenderPort().invoke(data, "k", DEADLINE, context=CTX)


def test_builtin_name():
    assert MARKDOWN_RENDER_BUILTIN == "markdown.render"


def test_returns_markdown_and_base64_with_id_and_url_in_body():
    result = _render()
    assert result.outcome == COMPLETED
    md = result.output["markdown"]
    assert md.startswith("# Note from ori\n")
    assert "Remember the milk" in md
    assert EVENT["url"] in md and "998877" in md and "ori" in md
    assert base64.b64decode(result.output["content_base64"]).decode("utf-8") == md


def test_same_inputs_are_byte_identical():
    assert dict(_render().output) == dict(_render().output)


def test_optional_fields_are_left_out():
    md = _render(author=None, source=None, url=None, id=None).output["markdown"]
    assert md == "# Note from ori\n\nRemember the milk\n"


def test_missing_title_or_text_fails_bad_params():
    for over in ({"title": ""}, {"text": None}, {"title": 5}):
        result = _render(**over)
        assert result.outcome == FAILED and "bad_params" in str(result.error)
        assert result.retryable is False


class FakeLibrary:
    """Dedupes uploads by sha256 of the file bytes; a repeat reports created false."""

    def __init__(self):
        self.items = {}
        self.bodies = []

    def __call__(self, method, url, headers, body, timeout):
        self.bodies.append(body)
        # key on the file part only (title/author fields are outside it)
        file_part = body.split(b'name="file"', 1)[1].split(b"\r\n\r\n", 1)[1]
        digest = hashlib.sha256(file_part.rsplit(b"\r\n--", 1)[0]).hexdigest()
        created = digest not in self.items
        if created:
            self.items[digest] = len(self.items) + 1
        reply = {"item": {"id": self.items[digest]}, "created": created}
        return 201 if created else 200, json.dumps(reply).encode()


def test_discord_event_through_render_into_library_push_dedupes():
    store = MemoryStore()
    store.put(
        ACTORS_COLLECTION,
        {
            "id": "lib",
            "name": "lib",
            "kind": "service",
            "enabled": True,
            "params": {
                "base_url": "http://localhost:8780",
                "api_key": "grant:LIB",
                "upload_path": "/api/library",
                "kinds": ["note"],
                "formats": ["md"],
            },
        },
    )
    library = FakeLibrary()
    push = LibraryPushPort(
        store, transport=library, secret_runner=lambda _n: "key", clock=lambda: NOW
    )

    def run_once():
        rendered = _render(
            title=f"Note from {EVENT['author_name']}",
            text=EVENT["content"],
            author=EVENT["author_name"],
            url=EVENT["url"],
            id=EVENT["message_id"],
        )
        return push.invoke(
            {
                "actor": "lib",
                "title": "Note from ori",
                "kind": "note",
                "format": "md",
                "content_base64": rendered.output["content_base64"],
            },
            "key-" + str(len(library.bodies)),
            DEADLINE,
            context=CTX,
        )

    first = run_once()
    assert first.outcome == COMPLETED and first.output["created"] is True
    body = library.bodies[0]
    assert b"Remember the milk" in body and EVENT["url"].encode() in body
    second = run_once()
    assert second.outcome == COMPLETED and second.output["created"] is False
    assert second.output["item_id"] == first.output["item_id"]
