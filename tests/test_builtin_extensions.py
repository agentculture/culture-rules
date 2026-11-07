"""Acceptance 2 of Boundary t7: the built-in apps register exactly what was wired before.

The registry's names are literals (registering imports no app module); these tests pin them
to the constants in the app modules so the two cannot drift.
"""

from __future__ import annotations

import pytest

from culture_rules.extensions import registry
from culture_rules.extensions.builtin import BUILTIN_EXTENSIONS
from culture_rules.model.action_kinds import ACTION_KINDS, BUILTIN_KINDS
from culture_rules.node import runner
from culture_rules.store.memory import MemoryStore

PORTS_BEFORE_T7 = {
    "action:noop",
    "action:github.pr_head",
    "action:message",
    "action:mesh.message",
    "action:discord.message",
    "action:discord.read",
    "action:discord.react",
    "action:discord.reply",
    "action:discord.sweep",
    "action:github.comment",
    "action:github.pr",
    "action:github.merge",
    "action:jira.comment",
    "action:email.send",
    "action:search.web",
    "action:llm.complete",
    "action:library.push",
    "action:audio.transcribe",
    "action:transcript.render",
    "action:decider.assess",
    "action:repodocs.sync",
    "action:http.call",
    "action:machine.command",
    "code",
}


def test_every_built_in_extension_is_installed_in_order():
    names = [name for name, _ in BUILTIN_EXTENSIONS]
    assert list(registry().installed())[: len(names)] == names


def test_the_catalogue_is_the_built_in_kinds_in_their_order():
    assert [k for k in ACTION_KINDS if k in BUILTIN_KINDS] == list(BUILTIN_KINDS)


def test_the_legacy_alias_resolves_and_stays_lenient():
    assert registry().resolve_kind("mesh.message") is ACTION_KINDS["message"]
    assert registry().is_lenient("mesh.message") is True


def test_default_ports_has_exactly_the_ports_wired_before():
    assert set(runner.default_ports(MemoryStore(), "spark")) == PORTS_BEFORE_T7


def test_the_alias_shares_its_kinds_port():
    ports = runner.default_ports(MemoryStore(), "spark")
    assert ports["action:mesh.message"] is ports["action:message"]


def _builtin_classes():
    from culture_rules.extensions.newspaper import port as newspaper
    from culture_rules.node.actions import (
        attachment_fetch,
        checklist,
        cursor,
        json_read,
        keyset,
        markdown_html,
        markdown_render,
        run_read,
        sections,
        select,
        text_refs,
        text_steps,
        text_validate,
    )

    return {
        newspaper.SECTIONS_BUILTIN: newspaper.NewspaperSectionsPort,
        newspaper.FILTER_BUILTIN: newspaper.NewspaperFilterPort,
        newspaper.RENDER_BUILTIN: newspaper.NewspaperRenderPort,
        newspaper.ISSUE_BUILTIN: newspaper.NewspaperIssuePort,
        select.SELECT_BUILTIN: select.SelectPort,
        json_read.JSON_READ_BUILTIN: json_read.JsonReadPort,
        markdown_render.MARKDOWN_RENDER_BUILTIN: markdown_render.MarkdownRenderPort,
        markdown_html.FROM_HTML_BUILTIN: markdown_html.MarkdownFromHtmlPort,
        sections.SECTIONS_BUILTIN: sections.DocumentSectionsPort,
        run_read.RUN_READ_BUILTIN: run_read.RunReadPort,
        attachment_fetch.ATTACHMENT_FETCH_BUILTIN: attachment_fetch.AttachmentFetchPort,
        cursor.CURSOR_ADVANCE_BUILTIN: cursor.CursorAdvancePort,
        keyset.KEYSET_FILTER_BUILTIN: keyset.KeysetFilterPort,
        keyset.KEYSET_ADD_BUILTIN: keyset.KeysetAddPort,
        text_steps.TEXT_NUMBER_BUILTIN: text_steps.TextNumberPort,
        text_refs.TEXT_REPLACE_REFS_BUILTIN: text_refs.TextReplaceRefsPort,
        text_validate.TEXT_VALIDATE_BUILTIN: text_validate.TextValidatePort,
        checklist.MERGE_BUILTIN: checklist.ChecklistMergePort,
        checklist.SET_BUILTIN: checklist.ChecklistSetPort,
        checklist.RENDER_BUILTIN: checklist.ChecklistRenderPort,
        checklist.BIND_BUILTIN: checklist.ChecklistBindPort,
    }


def test_the_code_port_holds_exactly_the_built_ins_wired_before():
    builtins = runner.default_ports(MemoryStore(), "spark")["code"]._builtins
    assert {name: type(p) for name, p in builtins.items()} == _builtin_classes()


def test_the_webhook_routes_are_github_then_jira():
    assert [r.path for r in registry().webhook_routes()] == ["/hooks/github", "/hooks/jira"]


def test_the_node_listeners_are_the_discord_gateway_and_the_checks_settler():
    """Install order (discord before github, as the catalogue); the two share no state."""
    listeners = [(n.name, n.long_lived) for n in registry().node_listeners()]
    assert listeners == [("discord.gateway", True), ("github.checks_settle", False)]


def test_the_node_collections_include_every_app_collection():
    from culture_rules.apps.discord_gateway import GATEWAY_STATE_COLLECTION
    from culture_rules.node.actions.github import ONCE_COLLECTION
    from culture_rules.node.checks_settle import (
        LATE_COLLECTION,
        RECOVERY_COLLECTION,
        SETTLE_COLLECTION,
    )
    from culture_rules.node.daemon import NODE_COLLECTIONS

    app = {
        GATEWAY_STATE_COLLECTION,
        ONCE_COLLECTION,
        SETTLE_COLLECTION,
        RECOVERY_COLLECTION,
        LATE_COLLECTION,
    }
    assert app <= set(NODE_COLLECTIONS)


def _source_event_types():
    from culture_rules.apps import discord_gateway as gw
    from culture_rules.events.emit import CHECKS_SETTLED_TYPE
    from culture_rules.model.discord_filters import BUTTON_CLICKED
    from culture_rules.server.hooks import github, jira

    return (
        set(github._TYPES.values())
        | {CHECKS_SETTLED_TYPE}
        | set(jira._EVENT_TYPES.values())
        | {gw.GATEWAY_EVENT, gw.REACTION_EVENT, gw.COMMAND_EVENT, BUTTON_CLICKED}
    )


def test_the_event_types_are_the_ones_the_apps_emit():
    assert set(registry().event_types()) == _source_event_types()


@pytest.mark.parametrize("name", ["github", "discord", "jira"])
def test_each_apps_event_types_are_registered_by_that_app(name):
    owned = {t for t, e in registry().event_types().items() if e.extension == name}
    assert owned and all(t.startswith(f"{name}.") for t in owned)
