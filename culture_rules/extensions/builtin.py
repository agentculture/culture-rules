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
from culture_rules.extensions.registry import BuiltinPortSpec, Extension, Lazy, Registry
from culture_rules.model.action_kinds import REQ_STR, REQ_TEXT, ActionKind, ParamSpec, kind

__all__ = ["BUILTIN_EXTENSIONS", "BUILTIN_KINDS", "CORE_KINDS", "install_builtins"]

_ACTIONS = "culture_rules.node.actions."
_DISCORD_MESSAGE = "culture_rules.extensions.discord.message"

CORE_KINDS = (
    kind("noop", "Do nothing (placeholder, tests)", label="Do nothing", actorless=True),
    kind(
        "message",
        "Send a message on the Culture mesh",
        label="Send a message on the mesh",
        actorless=True,
        # legacy: a message naming a Discord app actor still posts through it (its port), so an
        # editor shows and saves that stored form as the kind that took it over
        shown_as=("actor", "discord.message"),
        actor=ParamSpec("str", widget="hidden"),  # legacy: a Discord app actor here still posts
        channel=REQ_STR,
        text=REQ_TEXT,
    ),
)
"""Core's own kinds: ``noop`` and ``message`` (``mesh.message`` its legacy alias)."""


def _store_port(module: str, attr: str) -> Lazy:
    """``culture_rules.node.actions.<module>.<attr>(store)``."""
    return Lazy(_ACTIONS + module, attr, store=True)


def _plain_port(module: str, attr: str) -> Lazy:
    """``culture_rules.node.actions.<module>.<attr>()``."""
    return Lazy(_ACTIONS + module, attr)


def _req(name: str, type: str = "string") -> BuiltinPortSpec:
    """A required port."""
    return BuiltinPortSpec(name, type, True)


def _opt(name: str, type: str = "string") -> BuiltinPortSpec:
    """An optional port."""
    return BuiltinPortSpec(name, type, False)


def _register_core_builtins(ext: Extension) -> None:
    """Core's generic built-in code steps: each with its describe phrase and, where an editor
    pre-fills them, its typed ports (``GET /steps/builtins``)."""
    ext.builtin(
        "select",
        _plain_port("select", "SelectPort"),
        phrase="select: the outputs of the path that ran (one source for the next step)",
    )
    ext.builtin(
        "json.read",
        _plain_port("json_read", "JsonReadPort"),
        phrase="json.read: named fields from a JSON file under its root",
    )
    ext.builtin(
        "markdown.render",
        _plain_port("markdown_render", "MarkdownRenderPort"),
        phrase="markdown.render: fields as one Markdown page, the same bytes each time",
        label="markdown.render: fields as one Markdown page",
        inputs=(
            _req("title"),
            _req("text"),
            _opt("author"),
            _opt("source"),
            _opt("url"),
            _opt("id"),
        ),
        outputs=(_req("markdown"), _req("content_base64")),
    )
    ext.builtin(
        "markdown.from_html",
        _plain_port("markdown_html", "MarkdownFromHtmlPort"),
        phrase="markdown.from_html: HTML as Markdown, whole and in chunks",
        inputs=(_req("html"), _opt("split_at")),
        outputs=(
            _req("markdown"),
            _req("chunks", "array"),
            _req("count", "integer"),
            _opt("parts", "array"),
        ),
    )
    ext.builtin(
        "document.sections",
        _plain_port("sections", "DocumentSectionsPort"),
        phrase=(
            "document.sections: a document split at a heading, as headings, links and excerpts"
        ),
        inputs=(
            _opt("content_base64"),
            _opt("html"),
            _opt("markdown"),
            _opt("content_path"),
            _opt("content_root"),
            _opt("format"),
        ),
        outputs=(_req("sections", "array"), _req("count", "integer"), _req("found", "integer")),
    )
    ext.builtin(
        "run.read",
        _store_port("run_read", "RunReadPort"),
        phrase="run.read: a step's outputs from the run that started this one",
        inputs=(_opt("run_id"),),
        outputs=(_req("run_id"),),
    )
    ext.builtin(
        "cursor.advance",
        _store_port("cursor", "CursorAdvancePort"),
        phrase="cursor.advance: move a stored cursor on, only from the value read",
        inputs=(_req("key"), _opt("expected"), _req("value")),
        outputs=(_req("key"), _req("value"), _opt("previous"), _req("advanced", "boolean")),
    )
    ext.builtin(
        "keyset.filter",
        _store_port("keyset", "KeysetFilterPort"),
        phrase="keyset.filter: drop the items whose key a named set already holds",
        inputs=(_req("items", "array"),),
        outputs=(
            _req("items", "array"),
            _req("kept", "integer"),
            _req("dropped", "integer"),
            _req("keyless", "integer"),
        ),
    )
    ext.builtin(
        "keyset.add",
        _store_port("keyset", "KeysetAddPort"),
        phrase="keyset.add: remember the items' keys in a named set for a window",
        inputs=(_req("items", "array"),),
        outputs=(
            _req("added", "integer"),
            _req("already_present", "integer"),
            _req("keyless", "integer"),
        ),
    )
    ext.builtin(
        "text.number",
        _plain_port("text_steps", "TextNumberPort"),
        phrase="text.number: number items into text using a template and separator",
        inputs=(_req("items", "array"), _opt("template"), _opt("separator")),
        outputs=(_req("text"), _req("count", "integer")),
    )
    ext.builtin(
        "text.replace_refs",
        _plain_port("text_refs", "TextReplaceRefsPort"),
        phrase="text.replace_refs: swap each reference in a text for its item's text",
        inputs=(_req("text"), _req("items", "array"), _opt("pattern"), _opt("template")),
        outputs=(_req("text"), _req("replaced", "integer"), _req("missing_refs", "array")),
    )
    ext.builtin(
        "text.validate",
        _plain_port("text_validate", "TextValidatePort"),
        phrase=(
            "text.validate: check text against configured rules, returning a verdict and reasons"
        ),
        inputs=(
            _req("text"),
            _opt("allowed", "array"),
            _opt("allowed_field"),
            _opt("require_links", "boolean"),
            _opt("line_shape"),
            _opt("link_schemes", "array"),
        ),
        outputs=(
            _req("ok", "boolean"),
            _req("reasons", "array"),
            _req("text"),
            _req("links", "array"),
        ),
    )
    ext.builtin(
        "checklist.merge",
        _store_port("checklist", "ChecklistMergePort"),
        phrase="checklist.merge: add new items, keep ids and done marks",
    )
    ext.builtin(
        "checklist.set",
        _store_port("checklist", "ChecklistSetPort"),
        phrase="checklist.set: mark one item done or not, if newer",
    )
    ext.builtin(
        "checklist.render",
        _store_port("checklist", "ChecklistRenderPort"),
        phrase="checklist.render: the list as Markdown, with its revision",
    )
    ext.builtin(
        "checklist.bind",
        _store_port("checklist", "ChecklistBindPort"),
        phrase="checklist.bind: record the pushed library item and revision",
    )
    _register_building_blocks(ext)


def _register_building_blocks(ext: Extension) -> None:
    """The generic value, file and list steps (no app, no flow: every rule is step config)."""
    ext.builtin(
        "json.parse",
        _plain_port("json_parse", "JsonParsePort"),
        phrase="json.parse: JSON text parsed and checked against a configured shape",
        inputs=(_req("text"), _opt("shape", "object")),
        outputs=(_req("value", "any"),),
    )
    ext.builtin(
        "text.join",
        _plain_port("value_steps", "TextJoinPort"),
        phrase="text.join: a list of values joined into one text with a separator",
        inputs=(_req("items", "array"), _opt("separator")),
        outputs=(_req("text"), _req("count", "integer")),
    )
    ext.builtin(
        "time.add",
        _plain_port("value_steps", "TimeAddPort"),
        phrase="time.add: an instant moved on by a duration",
        inputs=(
            _req("instant"),
            _opt("seconds", "number"),
            _opt("minutes", "number"),
            _opt("hours", "number"),
            _opt("days", "number"),
        ),
        outputs=(_req("instant"),),
    )
    ext.builtin(
        "date.local",
        _plain_port("value_steps", "DateLocalPort"),
        phrase="date.local: an instant written as local time in a time zone",
        inputs=(_req("instant"), _opt("tz"), _opt("format")),
        outputs=(_req("text"), _req("local")),
    )
    ext.builtin(
        "file.read",
        _plain_port("file_read", "FileReadPort"),
        phrase="file.read: a file's text or JSON, read only under configured roots",
        inputs=(_req("path"),),
        outputs=(
            _req("bytes", "integer"),
            _opt("content"),
            _opt("value", "any"),
            _opt("fields", "object"),
        ),
    )
    ext.builtin(
        "list.filter",
        _plain_port("list_filter", "ListFilterPort"),
        phrase="list.filter: the items that pass configured rules, up to a limit",
        inputs=(_req("items", "array"), _opt("limit", "integer")),
        outputs=(
            _req("items", "array"),
            _req("count", "integer"),
            _req("dropped_host", "integer"),
            _req("dropped_duplicate", "integer"),
            _req("dropped_where", "integer"),
            _req("dropped_limit", "integer"),
        ),
    )


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
    _register_core_builtins(ext)


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
