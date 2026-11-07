"""The built-in extensions: everything the engine shipped wired in before the registry.

:data:`BUILTIN_EXTENSIONS` lists them in install order, one ``register(ext)`` function each;
:func:`install_builtins` installs them into a :class:`~culture_rules.extensions.Registry`.
The order keeps the action catalogue in its historical order (core, discord, github, jira,
http, machine, email, search, llm, decider, stt, transcript, library, repodocs).

Registering imports no app module: every port, built-in, route and listener is a factory
(:class:`~culture_rules.extensions.Lazy`, or a function here) that imports its module when
the node or the API builds it, so a missing optional extra still degrades the way it did
(``github.*`` ports become ``extra_missing`` without ``cryptography``). Names are literals;
``tests/test_builtin_extensions.py`` pins them to the app modules' constants.

The kinds' typed params are :data:`~culture_rules.model.action_kinds.BUILTIN_KINDS`.
Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from culture_rules.extensions import decider, email, http, library, llm, machine, search, stt
from culture_rules.extensions.registry import Extension, Lazy, NodeContext, Registry
from culture_rules.model.action_kinds import BUILTIN_KINDS

__all__ = ["BUILTIN_EXTENSIONS", "install_builtins"]

_ACTIONS = "culture_rules.node.actions."
_GITHUB_EXTRA = {"needs_module": "cryptography", "extra": "github"}


def _store_port(module: str, attr: str) -> Lazy:
    """``culture_rules.node.actions.<module>.<attr>(store)``."""
    return Lazy(_ACTIONS + module, attr, store=True)


def _plain_port(module: str, attr: str) -> Lazy:
    """``culture_rules.node.actions.<module>.<attr>()``."""
    return Lazy(_ACTIONS + module, attr)


def _kinds(ext: Extension, *names: str) -> None:
    for name in names:
        ext.action_kind(BUILTIN_KINDS[name])


# --------------------------------------------------------------------------- core


def register_core(ext: Extension) -> None:
    """``noop``, ``message`` on the mesh (``mesh.message`` its legacy alias) and the generic
    built-in code steps."""
    _kinds(ext, "noop")
    ext.action_kind(BUILTIN_KINDS["message"], aliases=("mesh.message",), lenient=True)
    ext.action_port("noop", Lazy("culture_rules.node.runner", "NoopAction"))
    ext.action_port("message", _store_port("message", "MessageAction"))
    ext.builtin("select", _plain_port("select", "SelectPort"))
    ext.builtin("json.read", _plain_port("json_read", "JsonReadPort"))
    ext.builtin("markdown.render", _plain_port("markdown_render", "MarkdownRenderPort"))
    ext.builtin("markdown.from_html", _plain_port("markdown_html", "MarkdownFromHtmlPort"))
    ext.builtin("document.sections", _plain_port("sections", "DocumentSectionsPort"))
    ext.builtin("run.read", _store_port("run_read", "RunReadPort"))
    ext.builtin("cursor.advance", _store_port("cursor", "CursorAdvancePort"))
    ext.builtin("keyset.filter", _store_port("keyset", "KeysetFilterPort"))
    ext.builtin("keyset.add", _store_port("keyset", "KeysetAddPort"))
    ext.builtin("text.number", _plain_port("text_steps", "TextNumberPort"))
    ext.builtin("text.replace_refs", _plain_port("text_refs", "TextReplaceRefsPort"))
    ext.builtin("text.validate", _plain_port("text_validate", "TextValidatePort"))
    ext.builtin("checklist.merge", _store_port("checklist", "ChecklistMergePort"))
    ext.builtin("checklist.set", _store_port("checklist", "ChecklistSetPort"))
    ext.builtin("checklist.render", _store_port("checklist", "ChecklistRenderPort"))
    ext.builtin("checklist.bind", _store_port("checklist", "ChecklistBindPort"))


# --------------------------------------------------------------------------- discord


class _GatewayListener:
    """The Discord Gateway supervisor as a node listener: names it listens for go on the
    cycle report; closing stops the threads and releases the leases."""

    def __init__(self, supervisor: Any) -> None:
        self.supervisor = supervisor

    def tick(self, report: Any) -> None:
        report.listening += self.supervisor.tick()

    def close(self) -> None:
        self.supervisor.shutdown()


def discord_gateway(ctx: NodeContext) -> _GatewayListener:
    """The ``discord.gateway`` listener (``options``: ``discord_gateway``,
    ``gateway_options``)."""
    from culture_rules.apps.discord_gateway import GatewaySupervisor  # noqa: PLC0415

    return _GatewayListener(
        GatewaySupervisor(
            ctx.store,
            ctx.identity,
            gateway=ctx.options.get("discord_gateway"),
            resolve_secret=ctx.resolve_secret,
            clock=ctx.clock,
            options=ctx.options.get("gateway_options"),
            host=ctx.host,
        )
    )


def discord_collections() -> tuple[str, ...]:
    from culture_rules.apps.discord_gateway import GATEWAY_STATE_COLLECTION  # noqa: PLC0415

    return (GATEWAY_STATE_COLLECTION,)


def register_discord(ext: Extension) -> None:
    """Discord: the message/read/react/reply/sweep kinds, ``attachment.fetch``, the Gateway
    listener and its four event types."""
    _kinds(ext, "discord.message", "discord.read", "discord.react", "discord.reply")
    _kinds(ext, "discord.sweep")
    ext.action_port("discord.message", _store_port("message", "DiscordMessageAction"))
    ext.action_port("discord.read", _store_port("discord_read", "DiscordReadAction"))
    ext.action_port("discord.react", _store_port("discord_react", "DiscordReactAction"))
    ext.action_port("discord.reply", _store_port("discord_reply", "DiscordReplyAction"))
    ext.action_port("discord.sweep", _store_port("discord_sweep", "DiscordSweepAction"))
    ext.builtin("attachment.fetch", _plain_port("attachment_fetch", "AttachmentFetchPort"))
    for event in (
        "discord.message.created",
        "discord.reaction.added",
        "discord.command.invoked",
        "discord.button.clicked",
    ):
        ext.event_type(event)
    ext.node_listener("discord.gateway", discord_gateway, long_lived=True)
    ext.node_collections(discord_collections)


# --------------------------------------------------------------------------- github


class _SettleListener:
    """The checks settler as a node listener: settles the due SHAs each cycle."""

    def __init__(self, settler: Any) -> None:
        self.settler = settler

    def tick(self, report: Any) -> None:
        del report
        self.settler.tick()


def github_checks_settle(ctx: NodeContext) -> _SettleListener:
    """The ``github.checks_settle`` listener: placed, so this node settles only the SHAs
    whose App actor it can serve."""
    from culture_rules.node import checks_settle  # noqa: PLC0415

    lister = checks_settle.AppSuiteLister(
        ctx.store,
        secrets=ctx.resolve_secret,
        host=ctx.host,
        unresolved_retry_s=checks_settle.UNRESOLVED_RETRY_S,
    )
    return _SettleListener(
        checks_settle.ChecksSettler(
            ctx.store,
            lister.list_suites,
            pull=lister.get_pull,
            serves=lister.serves,
            clock=ctx.clock,
        )
    )


def github_router(store: Any) -> Any:
    """``POST /hooks/github``. Every GitHub read on the delivery path is bounded (suites, PR
    read and secret resolve within one budget); past it the SHA stays armed and the node's
    tick settles it."""
    from culture_rules.node import checks_settle  # noqa: PLC0415
    from culture_rules.server.hooks import github  # noqa: PLC0415

    lister = checks_settle.AppSuiteLister(store)
    on_check = checks_settle.webhook_on_check(store, lister)

    def pull(repo: str, number: int) -> Any:  # read-only, bounded PR lookup for PR comments
        return lister.get_pull(repo, number, timeout_s=github.PULL_LOOKUP_TIMEOUT_S)

    return github.router(store, on_check=on_check, pull=pull)


def github_collections() -> tuple[str, ...]:
    from culture_rules.node.actions.github import ONCE_COLLECTION  # noqa: PLC0415
    from culture_rules.node.checks_settle import (  # noqa: PLC0415
        LATE_COLLECTION,
        RECOVERY_COLLECTION,
        SETTLE_COLLECTION,
    )

    return (SETTLE_COLLECTION, RECOVERY_COLLECTION, LATE_COLLECTION, ONCE_COLLECTION)


_GITHUB_EVENTS = (
    "github.pr.opened",
    "github.pr.closed",
    "github.pr.reopened",
    "github.pr.synchronize",
    "github.pr.ready",
    "github.comment.created",
    "github.issue.opened",
    "github.review.submitted",
    "github.review_comment.created",
    "github.checks.suite_completed",
    "github.checks.workflow_completed",
    "github.pr.checks_settled",
)


def register_github(ext: Extension) -> None:
    """GitHub (as a GitHub App, the ``github`` extra): comment, merge and PR facts, the head
    lookup, ``POST /hooks/github``, the checks settler and their event types."""
    _kinds(ext, "github.comment", "github.merge", "github.pr")
    ext.action_port("github.pr_head", _store_port("github", "GitHubPrHeadPort"), **_GITHUB_EXTRA)
    ext.action_port("github.comment", _store_port("github", "GitHubCommentPort"), **_GITHUB_EXTRA)
    ext.action_port("github.pr", _store_port("github", "GitHubPrPort"), **_GITHUB_EXTRA)
    merge = _store_port("github_merge", "GitHubMergePort")
    ext.action_port("github.merge", merge, **_GITHUB_EXTRA)
    for event in _GITHUB_EVENTS:
        ext.event_type(event)
    ext.webhook_route("/hooks/github", github_router)
    ext.node_listener("github.checks_settle", github_checks_settle)
    ext.node_collections(github_collections)


# --------------------------------------------------------------------------- the others


def register_jira(ext: Extension) -> None:
    """Jira: ``jira.comment``, ``POST /hooks/jira`` and its event types."""
    _kinds(ext, "jira.comment")
    ext.action_port("jira.comment", _store_port("jira", "JiraCommentPort"))
    for event in ("jira.issue.created", "jira.issue.updated", "jira.comment.created"):
        ext.event_type(event)
    ext.webhook_route("/hooks/jira", Lazy("culture_rules.server.hooks.jira", "router", True))


def _single(kind: str, module: str, attr: str) -> Callable[[Extension], None]:
    """An extension of one kind served by ``node.actions.<module>.<attr>(store)``."""

    def register(ext: Extension) -> None:
        _kinds(ext, kind)
        ext.action_port(kind, _store_port(module, attr))

    register.__doc__ = f"``{kind}`` (``node.actions.{module}.{attr}``)."
    return register


register_transcript = _single("transcript.render", "transcript_render", "TranscriptRenderPort")
register_repodocs = _single("repodocs.sync", "repodocs", "RepodocsSyncPort")


def register_newspaper(ext: Extension) -> None:
    """The newspaper built-ins (#53)."""
    ext.builtin("newspaper.sections", _plain_port("newspaper", "NewspaperSectionsPort"))
    ext.builtin("newspaper.filter", _plain_port("newspaper", "NewspaperFilterPort"))
    ext.builtin("newspaper.render", _plain_port("newspaper", "NewspaperRenderPort"))
    ext.builtin("newspaper.issue", _store_port("newspaper", "NewspaperIssuePort"))


BUILTIN_EXTENSIONS: tuple[tuple[str, Callable[[Extension], None]], ...] = (
    ("core", register_core),
    ("discord", register_discord),
    ("github", register_github),
    ("jira", register_jira),
    ("http", http.register),
    ("machine", machine.register),
    ("email", email.register),
    ("search", search.register),
    ("llm", llm.register),
    ("decider", decider.register),
    ("stt", stt.register),
    ("transcript", register_transcript),
    ("library", library.register),
    ("repodocs", register_repodocs),
    ("newspaper", register_newspaper),
)
"""The built-in extensions, in install order (the catalogue's order follows it)."""


def install_builtins(registry: Registry) -> None:
    """Install every built-in extension (each one once)."""
    for name, register in BUILTIN_EXTENSIONS:
        registry.install(name, register)
