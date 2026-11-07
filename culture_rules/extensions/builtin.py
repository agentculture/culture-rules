"""The built-in extensions: everything the engine ships, in install order.

:data:`BUILTIN_EXTENSIONS` lists them, one ``register(ext)`` function each:
:func:`register_core` here (``noop``, ``message`` and the generic built-in code steps of
:mod:`culture_rules.node.actions`), then one package per app under
:mod:`culture_rules.extensions` (``discord``, ``github``, ``jira``, ``http``, ``machine``,
``email``, ``search``, ``llm``, ``decider``, ``stt``, ``transcript``, ``library``,
``repodocs``, ``newspaper``), each holding its kinds (``KINDS``), its integration code and
its ``register``. :func:`install_builtins` installs them into a
:class:`~culture_rules.extensions.Registry`; the order keeps the action catalogue in its
historical order. This module and :mod:`culture_rules.extensions` are the only core modules
that name the app packages (``tests/test_extension_boundary.py``).

Registering imports no app module: every port, built-in, route and listener is a factory
(:class:`~culture_rules.extensions.Lazy`, or a function in the app's package) that imports
its module when the node or the API builds it, so a missing optional extra still degrades
the way it did (``github.*`` ports become ``extra_missing`` without ``cryptography``). Names
are literals; ``tests/test_builtin_extensions.py`` pins them to the app modules' constants.
Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable

from culture_rules.extensions import (
    decider,
    discord,
    email,
    github,
    http,
    jira,
    library,
    llm,
    machine,
    newspaper,
    repodocs,
    search,
    stt,
    transcript,
)
from culture_rules.extensions.registry import Extension, Lazy, Registry
from culture_rules.model.action_kinds import REQ_STR, STR, ActionKind, kind

__all__ = ["BUILTIN_EXTENSIONS", "BUILTIN_KINDS", "CORE_KINDS", "install_builtins"]

_ACTIONS = "culture_rules.node.actions."
_DISCORD_MESSAGE = "culture_rules.extensions.discord.message"

CORE_KINDS = (
    kind("noop", "Do nothing (placeholder, tests)"),
    kind(
        "message",
        "Send a message on the Culture mesh",
        actor=STR,  # legacy: a Discord app actor here still posts to Discord
        channel=REQ_STR,
        text=REQ_STR,
    ),
)
"""Core's own kinds: ``noop`` and ``message`` (``mesh.message`` its legacy alias)."""


def _store_port(module: str, attr: str) -> Lazy:
    """``culture_rules.node.actions.<module>.<attr>(store)``."""
    return Lazy(_ACTIONS + module, attr, store=True)


def _plain_port(module: str, attr: str) -> Lazy:
    """``culture_rules.node.actions.<module>.<attr>()``."""
    return Lazy(_ACTIONS + module, attr)


def register_core(ext: Extension) -> None:
    """``noop``, ``message`` on the mesh (``mesh.message`` its legacy alias) and the generic
    built-in code steps."""
    noop, message = CORE_KINDS
    ext.action_kind(noop)
    ext.action_kind(message, aliases=("mesh.message",), lenient=True)
    ext.action_port("noop", Lazy("culture_rules.node.runner", "NoopAction"))
    # the message port posts to Discord when it names an actor, so it is the Discord
    # extension's module; the kind (and its legacy alias) stay core's
    ext.action_port("message", Lazy(_DISCORD_MESSAGE, "MessageAction", store=True))
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


BUILTIN_EXTENSIONS: tuple[tuple[str, Callable[[Extension], None]], ...] = (
    ("core", register_core),
    ("discord", discord.register),
    ("github", github.register),
    ("jira", jira.register),
    ("http", http.register),
    ("machine", machine.register),
    ("email", email.register),
    ("search", search.register),
    ("llm", llm.register),
    ("decider", decider.register),
    ("stt", stt.register),
    ("transcript", transcript.register),
    ("library", library.register),
    ("repodocs", repodocs.register),
    ("newspaper", newspaper.register),
)
"""The built-in extensions, in install order (the catalogue's order follows it)."""

BUILTIN_KINDS: dict[str, ActionKind] = {
    k.name: k
    for k in (
        *CORE_KINDS,
        *discord.KINDS,
        *github.KINDS,
        *jira.KINDS,
        *http.KINDS,
        *machine.KINDS,
        *email.KINDS,
        *search.KINDS,
        *llm.KINDS,
        *decider.KINDS,
        *stt.KINDS,
        *transcript.KINDS,
        *library.KINDS,
        *repodocs.KINDS,
    )
}
"""The built-in kinds' typed params, by name, in catalogue order."""


def install_builtins(registry: Registry) -> None:
    """Install every built-in extension (each one once)."""
    for name, register in BUILTIN_EXTENSIONS:
        registry.install(name, register)
