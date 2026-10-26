"""discord-surfaces t8: discord.read and markdown.render are wired into the node and the model."""

from __future__ import annotations

from culture_rules.model.action_kinds import ACTION_KINDS
from culture_rules.model.describe import ACTION_WORDS, BUILTIN_WORDS, action_text
from culture_rules.node.actions.discord_read import DiscordReadAction
from culture_rules.node.actions.markdown_render import MARKDOWN_RENDER_BUILTIN, MarkdownRenderPort
from culture_rules.node.runner import default_ports
from culture_rules.store.memory import MemoryStore


def test_discord_read_is_a_catalogued_action_kind():
    kind = ACTION_KINDS["discord.read"]
    required = {name for name, spec in kind.params.items() if spec.required}
    assert required == {"actor", "channel", "message"}
    assert "guild" in kind.params


def test_discord_read_and_markdown_render_have_words():
    assert "discord.read" in ACTION_WORDS
    assert MARKDOWN_RENDER_BUILTIN in BUILTIN_WORDS
    words = action_text(
        {"kind": "discord.read", "params": {"actor": "jetson-bot", "channel": "20"}}
    )
    assert "discord.read" in words
    assert "20" in words


def test_the_node_registers_discord_read_and_markdown_render():
    ports = default_ports(MemoryStore(), "spark")
    assert isinstance(ports["action:discord.read"], DiscordReadAction)
    assert isinstance(ports["code"]._builtins[MARKDOWN_RENDER_BUILTIN], MarkdownRenderPort)


def test_the_node_registers_the_generic_markdown_and_run_steps():
    from culture_rules.node.actions.markdown_html import FROM_HTML_BUILTIN, MarkdownFromHtmlPort
    from culture_rules.node.actions.run_read import RUN_READ_BUILTIN, RunReadPort

    builtins = default_ports(MemoryStore(), "spark")["code"]._builtins
    assert isinstance(builtins[FROM_HTML_BUILTIN], MarkdownFromHtmlPort)
    assert isinstance(builtins[RUN_READ_BUILTIN], RunReadPort)
    assert FROM_HTML_BUILTIN in BUILTIN_WORDS
    assert RUN_READ_BUILTIN in BUILTIN_WORDS


# --------------------------------------------------------------------------- R2 (t15)


def test_discord_react_is_a_catalogued_action_kind():
    kind = ACTION_KINDS["discord.react"]
    required = {name for name, spec in kind.params.items() if spec.required}
    assert required == {"actor", "channel", "message", "emoji"}
    assert "guild" in kind.params


def test_discord_react_and_attachment_fetch_have_words():
    from culture_rules.node.actions.attachment_fetch import ATTACHMENT_FETCH_BUILTIN

    assert ATTACHMENT_FETCH_BUILTIN in BUILTIN_WORDS
    words = action_text(
        {"kind": "discord.react", "params": {"actor": "discord-bot", "emoji": "📚"}}
    )
    assert "discord.react" in words
    assert "📚" in words


def test_the_node_registers_discord_react_and_attachment_fetch():
    from culture_rules.node.actions.attachment_fetch import (
        ATTACHMENT_FETCH_BUILTIN,
        AttachmentFetchPort,
    )
    from culture_rules.node.actions.discord_react import DiscordReactAction

    ports = default_ports(MemoryStore(), "spark")
    assert isinstance(ports["action:discord.react"], DiscordReactAction)
    assert isinstance(ports["code"]._builtins[ATTACHMENT_FETCH_BUILTIN], AttachmentFetchPort)


# --------------------------------------------------------------------------- R3 (t19)


def test_discord_reply_and_sweep_are_catalogued_action_kinds():
    reply = ACTION_KINDS["discord.reply"]
    assert {n for n, s in reply.params.items() if s.required} == {"actor", "event_id", "text"}
    sweep = ACTION_KINDS["discord.sweep"]
    assert {n for n, s in sweep.params.items() if s.required} == {"actor", "channel", "cursor_key"}
    assert {"guild", "limit", "include_bots", "backfill"} <= set(sweep.params)


def test_discord_reply_sweep_and_cursor_advance_have_words():
    from culture_rules.node.actions.cursor import CURSOR_ADVANCE_BUILTIN

    assert CURSOR_ADVANCE_BUILTIN in BUILTIN_WORDS
    assert "Saved" in action_text(
        {"kind": "discord.reply", "params": {"actor": "discord-bot", "text": "Saved"}}
    )
    words = action_text({"kind": "discord.sweep", "params": {"actor": "b", "channel": "42"}})
    assert "discord.sweep" in words and "42" in words


def test_the_node_registers_discord_reply_sweep_and_cursor_advance():
    from culture_rules.node.actions.cursor import CURSOR_ADVANCE_BUILTIN, CursorAdvancePort
    from culture_rules.node.actions.discord_reply import DiscordReplyAction
    from culture_rules.node.actions.discord_sweep import DiscordSweepAction

    ports = default_ports(MemoryStore(), "spark")
    assert isinstance(ports["action:discord.reply"], DiscordReplyAction)
    assert isinstance(ports["action:discord.sweep"], DiscordSweepAction)
    assert isinstance(ports["code"]._builtins[CURSOR_ADVANCE_BUILTIN], CursorAdvancePort)
