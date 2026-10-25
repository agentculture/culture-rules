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
