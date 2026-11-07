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
:data:`ACTION_KINDS` is a live view over it, and the kinds above are
:data:`BUILTIN_KINDS`, registered by the built-in apps. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from culture_rules.extensions import registry
from culture_rules.model.refs import REF_KEY, is_reference, structured_form

__all__ = [
    "ACTION_KINDS",
    "BUILTIN_KINDS",
    "ActionKind",
    "ParamSpec",
    "describe_kinds",
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


def _k(
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
    return ActionKind(name, params, summary, any_of, label, actors, actorless)


_S = ParamSpec("str")
_RS = ParamSpec("str", True)
_I = ParamSpec("int")
_ADV = ParamSpec("str", advanced=True)
_RI = ParamSpec("int", True)
_TEXT = ParamSpec("str", widget="multiline")
_RTEXT = ParamSpec("str", True, widget="multiline")
_CHANNEL = ParamSpec("str", True, widget="discord_channel")
_GUILD = ParamSpec("str", widget="discord_guild")
_AGENT_ENDPOINT = ({"kinds": ["agent"], "has": ["base_url"]},)

_KINDS = (
    _k("noop", "Do nothing (placeholder, tests)", label="Do nothing", actorless=True),
    _k(
        "message",
        "Send a message on the Culture mesh",
        label="Send a message on the mesh",
        actorless=True,
        actor=ParamSpec("str", widget="hidden"),  # legacy: a Discord app actor here still posts
        channel=_RS,
        text=_RTEXT,
    ),
    _k(
        "discord.message",
        "Post a message on Discord through a Discord app actor",
        label="Post a message on Discord",
        actor=_RS,
        guild=_GUILD,
        channel=_CHANNEL,
        text=_RTEXT,
        buttons=ParamSpec("list", widget="buttons"),
        thread_name=_S,  # set: channel is a forum and the text starts a post titled so
        tags=ParamSpec("list"),  # forum tag names, with thread_name only
    ),
    _k(
        "discord.read",
        "Read one Discord message in full (text and attachments) through a Discord app actor",
        label="Read a Discord message in full",
        actor=_RS,
        guild=_GUILD,  # fills the message url when given
        channel=_CHANNEL,
        message=_RS,
    ),
    _k(
        "discord.react",
        "React to one Discord message (a unicode emoji or a custom name:id) through a Discord"
        " app actor, and take back the bot's own earlier reactions (remove)",
        label="React to a Discord message",
        any_of=(("emoji", "remove"),),
        actor=_RS,
        guild=_GUILD,
        channel=_CHANNEL,
        message=_RS,
        emoji=_S,
        remove=ParamSpec("list"),
    ),
    _k(
        "discord.reply",
        "Answer a Discord slash or message command privately through a Discord app actor",
        label="Answer a Discord command privately",
        actor=_RS,
        event_id=_RS,
        text=_RTEXT,
    ),
    _k(
        "discord.sweep",
        "Read a Discord channel's messages newer than a stored cursor through a Discord app"
        " actor",
        label="Read a Discord channel since the last run",
        actor=_RS,
        guild=_GUILD,
        channel=_CHANNEL,
        cursor_key=_RS,
        limit=_I,
        include_bots=ParamSpec("bool"),
        backfill=ParamSpec("bool"),
    ),
    _k(
        "github.comment",
        "Comment on a GitHub issue or PR",
        label="Comment on GitHub",
        actor=_RS,
        repo=_RS,
        number=_RI,
        body=_RTEXT,
        once_key=_ADV,  # d25: post at most once per (repo, PR, key), durably
    ),
    _k(
        "github.merge",
        "Merge a PR as the GitHub App, only from a click carrying the button's one-time code",
        label="Merge a GitHub PR",
        actor=_RS,
        repo=_RS,
        number=_RI,
        sha=_RS,
        delete_branch=ParamSpec("bool"),
    ),
    _k(
        "github.pr",
        "Read one PR's title, link, author, draft flag, state, repos, head branch and sha"
        " as the GitHub App",
        label="Read a GitHub PR",
        actor=_RS,
        repo=_RS,
        number=_RI,
    ),
    _k(
        "jira.comment",
        "Comment on a Jira issue",
        label="Comment on Jira",
        actor=_RS,
        issue=_RS,
        body=_RTEXT,
    ),
    _k(
        "http.call",
        "Call an HTTP endpoint",
        label="Call an HTTP endpoint",
        actors=({"declares_action": True}, {"has": ["http.allow"]}),
        actor=_RS,
        method=ParamSpec(
            "str",
            True,
            choices=tuple((m, m) for m in ("GET", "POST", "PUT", "PATCH", "DELETE")),
            choose="Choose a method…",
        ),
        url=_RS,
        headers=ParamSpec("dict"),
        body=ParamSpec("any"),
        save_to=_S,  # an absolute dir the actor's http.save_roots lists: stream the body there
    ),
    _k(
        "machine.command",
        "Run a command on a machine actor",
        label="Run a command on a machine",
        actors=({"kinds": ["runner"]},),
        actor=_RS,
        command=ParamSpec("str", True, widget="command"),
        args=ParamSpec("dict", widget="command_args"),
    ),
    _k(
        "email.send",
        "Send an email through an email app actor (Resend)",
        label="Send an email",
        actors=({"kinds": ["app"], "surface": "email"},),
        actor=_RS,
        to=ParamSpec("list", True),  # each must be on the actor's recipient allowlist
        subject=_RS,
        html=_TEXT,  # at least one of html/text: the port checks it
        text=_TEXT,
        idempotency_key=_ADV,  # replaces the engine's per-run key (a durable, per-date key)
        sender=_ADV,  # replaces the actor's from address; must be at the actor's domain
        not_after=_ADV,  # ISO-8601: at or past it the send fails email_window_passed, no request
    ),
    _k(
        "search.web",
        "Search the web through a search app actor (Brave)",
        label="Search the web",
        actors=({"kinds": ["app"], "surface": "search"},),
        actor=_RS,
        query=_RS,
        count=_I,
        freshness=ParamSpec(
            "str",
            choices=(
                ("pd", "Past day"),
                ("pw", "Past week"),
                ("pm", "Past month"),
                ("py", "Past year"),
            ),
            choose="Any time",
        ),
        country=_S,
        lang=_S,
    ),
    _k(
        "llm.complete",
        "Complete an actor's brief with an OpenAI-compatible model on an agent actor (no tools)",
        label="Summarise with a language model",
        any_of=(("content", "content_path"),),
        actors=_AGENT_ENDPOINT,
        actor=_RS,
        brief=ParamSpec(
            "str",
            needed=True,
            options_from="briefs",
            choose="Choose one of the actor's briefs…",
        ),  # a name in the actor's params.briefs; required at run time, no default
        answer=ParamSpec("dict", widget="json"),  # {shape: <culture_rules.model.shape spec>}
        content=ParamSpec("any", widget="multiline"),  # or content_path (d2): one of the two
        content_path=_S,  # a schema-1 transcript file; needs content_root (checked at run time)
        content_root=_S,
        content_max_chars=_I,
        max_tokens=_I,
        temperature=ParamSpec("any", widget="number"),
        not_after=_S,  # ISO-8601: past it the work fails llm_deadline instead of queueing
        cache=ParamSpec(
            "bool", label="Keep the first answer for this file"
        ),  # answer + content_path: keep the first valid answer
        cache_name=_S,  # the cache file stem (answer.<name>.<sha16>.json), default "recording"
        cache_label=_S,  # the cache document's brief label, default "recording"
    ),
    _k(
        "decider.assess",
        "Ask the mesh's decider for probabilities over the options a step defines (no text)",
        label="Ask the decider",
        actors=_AGENT_ENDPOINT,
        actor=_RS,
        state=_RTEXT,
        questions=ParamSpec(
            "dict", True, widget="json"
        ),  # {key: {type: choice|noul|score, instructions, criteria}}
        not_after=_S,  # ISO-8601: past it the work fails decider_deadline instead of queueing
    ),
    _k(
        "audio.transcribe",
        "Transcribe an audio file with a speech-to-text endpoint on an agent actor",
        label="Transcribe an audio file",
        actors=_AGENT_ENDPOINT,
        actor=_RS,
        audio_path=_RS,
        audio_sha256=_S,  # optional: without it the port hashes the file itself
        output_dir=_RS,
        language=_S,
        model=_S,
    ),
    _k(
        "transcript.render",
        "Render one transcript and its summary as an EPUB or Markdown (right to left for Hebrew)",
        label="Make a book or Markdown from a transcript",
        actors=({"has_any": ["transcripts_root", "output_root"]},),
        actorless=True,
        actor=_S,  # optional: its transcripts_root/output_root is a second boundary
        result_path=_RS,
        root=_RS,  # the transcripts root: result_path must resolve under it
        recorded_at=_RS,  # ISO-8601 with a zone; dated in Asia/Jerusalem
        language=_S,
        summary=ParamSpec("any"),  # llm.complete's output: {text} or a string, or none
        format=ParamSpec(
            "str",
            choices=(("epub", "EPUB book"), ("md", "Markdown")),
            choose="EPUB book (default)",
        ),  # epub (default) or md: summary, key facts, then the transcript
    ),
    _k(
        "library.push",
        "Upload a file to a library, or replace one by id (and queue it on a device queue)",
        label="Send a book to a library",
        any_of=(("content_base64", "content_path"),),
        actors=({"not_kinds": ["agent", "app"], "has": ["base_url"]},),
        actor=_RS,
        content_base64=_S,  # or content_path + content_root (d7): one of the two is required
        content_path=_S,  # a file under content_root on the node; its bytes never enter run state
        content_root=ParamSpec(
            "str",
            options_from="content_roots",
            choose="Choose one of the library's content roots…",
        ),
        title=_RS,
        kind=ParamSpec(
            "str", True, options_from="kinds", choose="Choose a kind…"
        ),  # one of the actor's params.kinds
        visibility=ParamSpec(
            "str",
            options_from="visibilities",
            choose="The library's default",
            needs_actor_param="visibilities",
        ),  # one of the actor's params.visibilities
        format=ParamSpec(
            "str", options_from="formats", choose="EPUB (default)"
        ),  # one of the actor's params.formats; epub when unset
        item_id=_I,  # replace this item through the actor's replace_path
        author=_S,
        device_id=ParamSpec("str", needs_actor_param="queue_path"),  # only with a queue_path
    ),
    _k(
        "repodocs.sync",
        "Push every repo's specs, plans, deliveries, designs and .devague files to the library",
        label="Push repo documents to the library",
        actors=({"kinds": ["service"], "has": ["repos_root"]},),
        actor=_RS,  # a service actor: base_url, api_key, repos_root, state_path, limits
    ),
)

BUILTIN_KINDS: dict[str, ActionKind] = {k.name: k for k in _KINDS}
"""The built-in kinds' typed params, by name; the built-in extensions register them
(:mod:`culture_rules.extensions.builtin`), each with the app it belongs to."""


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
