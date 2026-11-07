"""Action kind catalog: typed ``params`` per kind and the ``params.actor`` binding convention.

An :class:`~culture_rules.model.action.Action` has no actor slot; an action that invokes an
actor capability names the actor in ``params.actor`` (an actor id, or a reference). Per kind
this module says which params exist, their types and which are required.

Actor policy: ``discord.message``, ``discord.read``, ``discord.react``, ``discord.reply``,
``discord.sweep``, ``github.comment``,
``github.merge``, ``github.pr``, ``jira.comment``,
``http.call``, ``machine.command``,
``email.send``, ``search.web``, ``llm.complete``, ``audio.transcribe``, ``decider.assess``,
``library.push`` and ``repodocs.sync`` always need ``params.actor`` (the credentialed or
executing party).
``message`` sends on the Culture mesh and takes no actor; a stored ``message`` that still names
a Discord app actor (the form before ``discord.message`` existed) keeps posting through it.
``discord.message`` posts to a Discord channel through a Discord app actor; ``guild`` records
the server the channel was picked from, and ``buttons`` (a list) puts buttons under the text;
a button's optional ``value`` is kept in the store and carried by its click as ``data.value``.
With ``thread_name`` the channel is a forum: the text starts a post of that title, tagged by the
names in ``tags`` (at most 5), and a text longer than one message continues as replies in it.
``discord.read`` reads one message in full (its
text untruncated and its attachments) with the same actor's token, within its allow-list;
``discord.react`` adds the bot's reaction (a unicode emoji or a custom ``name:id``) to one
and takes back its own earlier ones (``remove``, a list); it needs ``emoji`` or ``remove``.
``discord.reply`` answers a slash or message command privately (``event_id`` names the
``discord.command.invoked`` event); ``discord.sweep`` reads a channel's messages newer than a
stored cursor, which the ``cursor.advance`` step moves once the run's saves succeed.
``mesh.message`` is a legacy alias of ``message``.

Any param value may be a reference (``trigger.data.number``), a ``{"$ref": ...}`` object or a
``{{ }}`` template, accepted wherever a typed value is expected. Extra params are tolerated.

There is
exactly **one merge kind**, ``github.merge`` (``sha`` pins the head commit, ``delete_branch``
removes the branch after): merging stays a human gate, fenced by the click and the clicked
button's one-time code. The port runs it only in a run started by a ``discord.button.clicked``
event whose stored code is unused and names the same repo, PR and sha; ``github.pr`` reads
one PR's title, link, author, state, repos, branch and head sha (no write).

The daily-newspaper kinds (#53): ``email.send`` sends through an ``email`` app actor to
addresses on its recipient allowlist; ``search.web`` queries through a ``search`` app actor;
``llm.complete`` asks an agent actor's OpenAI-compatible endpoint (never with tools);
``audio.transcribe`` sends an audio file (checked against ``audio_sha256``) to the same
kind of endpoint for a transcript written under ``output_dir``;
``transcript.render`` renders a transcript under the transcripts root, with its summary, as
an EPUB (no actor needed);
``library.push`` uploads ``content_base64`` (an EPUB, or another ``format`` the actor lists) as
``kind`` (and ``visibility``) to the library its actor describes, or replaces ``item_id`` there
through the actor's ``replace_path``, queueing a new upload for ``device_id`` when that library
has a device queue (culture-rules#86).
``repodocs.sync`` pushes the devague record (specs, plans, deliveries, designs, ``.devague``) of
every top-level repo under its service actor's ``repos_root`` to the library the actor
describes (culture-rules#124); everything but the actor is the actor's params.

Which kinds exist is the extension registry's (:mod:`culture_rules.extensions`):
:data:`ACTION_KINDS` is a live view over it. Each kind above is defined by the extension
that registers it (``KINDS`` in its package under :mod:`culture_rules.extensions`; ``noop``
and ``message`` in :mod:`culture_rules.extensions.builtin`, which also gathers them as
``BUILTIN_KINDS``), built with :func:`kind`. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from culture_rules.extensions import registry
from culture_rules.model.refs import REF_KEY, is_reference, structured_form

__all__ = [
    "ACTION_KINDS",
    "ADV",
    "INT",
    "REQ_INT",
    "REQ_STR",
    "REQ_TEXT",
    "STR",
    "TEXT",
    "ActionKind",
    "ParamSpec",
    "describe_kinds",
    "kind",
    "param_type_ok",
]


@dataclass(frozen=True)
class ParamSpec:
    """A param: ``type`` is str, int, bool, dict, list or any; ``required`` means non-empty.

    The rest is how an editor shows it, served with the catalogue (``GET /actions/kinds``) so
    no editor keeps a table of its own: ``label`` (default: the name), ``widget`` (``multiline``,
    ``json``, ``number``, ``buttons``, ``command``, ``command_args``, ``discord_guild``,
    ``discord_channel`` or ``hidden``; empty: by ``type``), a closed set of ``choices``
    (``(value, label)`` pairs) or ``options_from`` (the key of the chosen actor's params that
    lists the values, or maps them as keys), ``choose`` (the prompt of an empty choice) and
    ``needs_actor_param`` (shown only when the chosen actor has that param set) and ``needed``
    (not required of a stored step, which the port refuses at run time, but an editor asks
    for it) and ``advanced`` (rarely set: an editor keeps it behind a "More settings" fold, open
    when the step already sets it).
    """

    type: str
    required: bool = False
    label: str = ""
    widget: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    options_from: str = ""
    choose: str = ""
    needs_actor_param: str = ""
    needed: bool = False
    advanced: bool = False


@dataclass(frozen=True)
class ActionKind:
    """A catalogued action kind."""

    name: str
    params: dict[str, ParamSpec] = field(default_factory=dict)
    summary: str = ""
    #: groups of params of which at least one must be set (e.g. content or content_path)
    any_of: tuple[tuple[str, ...], ...] = ()
    #: a short name for pickers (default: the summary)
    label: str = ""
    #: which actors can perform it: alternatives (OR), each a clause of conditions (AND) over an
    #: enabled actor: ``kinds``, ``not_kinds``, ``surface``, ``declares_action`` (it lists this
    #: kind in ``params.actions``), ``has`` (these dotted params non-empty), ``has_any``
    #: (at least one of these). Empty: an app actor that declares the kind.
    actors: tuple[dict[str, Any], ...] = ()
    #: offered even when no enabled actor can perform it (it needs none)
    actorless: bool = False


def kind(
    name: str,
    summary: str,
    /,
    *,
    label: str = "",
    any_of: tuple[tuple[str, ...], ...] = (),
    actors: tuple[dict[str, Any], ...] = (),
    actorless: bool = False,
    **params: ParamSpec,
) -> ActionKind:
    """An :class:`ActionKind` from keyword params (what the apps' ``KINDS`` are built with)."""
    return ActionKind(name, params, summary, any_of, label, actors, actorless)


STR = ParamSpec("str")
REQ_STR = ParamSpec("str", True)
INT = ParamSpec("int")
REQ_INT = ParamSpec("int", True)
TEXT = ParamSpec("str", widget="multiline")
REQ_TEXT = ParamSpec("str", True, widget="multiline")
ADV = ParamSpec("str", advanced=True)


class _Catalogue(Mapping[str, ActionKind]):
    """The registered kinds, read live from :func:`culture_rules.extensions.registry`."""

    def __getitem__(self, name: str) -> ActionKind:
        return registry().action_kinds()[name]

    def __iter__(self) -> Iterator[str]:
        return iter(tuple(registry().action_kinds()))

    def __len__(self) -> int:
        return len(registry().action_kinds())

    def __repr__(self) -> str:
        return f"ACTION_KINDS({list(self)!r})"


ACTION_KINDS: Mapping[str, ActionKind] = _Catalogue()
"""Every registered action kind, ``name -> ActionKind`` in registration order (a live view
over the extension registry; the built-in apps register theirs on first use)."""


def resolve_kind(kind: str) -> ActionKind | None:
    """The catalogue entry for ``kind`` (aliases resolved), or ``None`` if unknown."""
    return registry().resolve_kind(kind)


def _is_dynamic(value: Any) -> bool:
    """A reference, ``$ref`` object or ``{{ }}`` template: resolved at run time, any type."""
    if isinstance(value, str):
        return "{{" in value or is_reference(value.strip())
    return structured_form(value) == REF_KEY


def param_type_ok(spec: ParamSpec, value: Any) -> bool:
    """Whether ``value`` satisfies ``spec`` (dynamic values always do)."""
    if _is_dynamic(value) or spec.type == "any":
        return True
    if spec.type == "bool":
        return isinstance(value, bool)
    if spec.type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if spec.type == "str":
        return isinstance(value, str)
    if spec.type == "dict":
        return isinstance(value, dict)
    return isinstance(value, (list, tuple))


def is_lenient(kind: str) -> bool:
    """Whether the kind's required params are not enforced (legacy-tolerant kinds: ``message``
    and its alias ``mesh.message``, so the legacy placeholder passes)."""
    return registry().is_lenient(kind)


def _describe_param(name: str, spec: ParamSpec) -> dict[str, Any]:
    return {
        "name": name,
        "type": spec.type,
        "required": spec.required,
        "label": spec.label,
        "widget": spec.widget,
        "choices": [{"value": value, "label": label} for value, label in spec.choices],
        "options_from": spec.options_from,
        "choose": spec.choose,
        "needs_actor_param": spec.needs_actor_param,
        "needed": spec.needed,
        "advanced": spec.advanced,
    }


def describe_kinds() -> list[dict[str, Any]]:
    """The registered action kinds as plain data, in catalogue order (``GET /actions/kinds``).

    Each item: ``kind``, ``label`` (the summary when none), ``summary``, ``lenient``,
    ``aliases``, ``params`` (in order, with the editor hints of :class:`ParamSpec`), ``any_of``,
    ``actors`` (the actor clauses of :class:`ActionKind`; an app actor declaring the kind when
    the kind names none) and ``actorless``. Built from the registry, never from a list here.
    """
    reg = registry()
    return [
        {
            "kind": name,
            "label": kind.label or kind.summary,
            "summary": kind.summary,
            "lenient": reg.is_lenient(name),
            "aliases": list(reg.aliases(name)),
            "params": [_describe_param(p, spec) for p, spec in kind.params.items()],
            "any_of": [list(group) for group in kind.any_of],
            "actors": [dict(c) for c in kind.actors] or [{"declares_action": True}],
            "actorless": kind.actorless,
        }
        for name, kind in reg.action_kinds().items()
    ]
