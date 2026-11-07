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
    "INT",
    "REQ_STR",
    "STR",
    "ActionKind",
    "ParamSpec",
    "kind",
    "param_type_ok",
]


@dataclass(frozen=True)
class ParamSpec:
    """A param: ``type`` is str, int, bool, dict, list or any; ``required`` means non-empty."""

    type: str
    required: bool = False


@dataclass(frozen=True)
class ActionKind:
    """A catalogued action kind."""

    name: str
    params: dict[str, ParamSpec] = field(default_factory=dict)
    summary: str = ""
    #: groups of params of which at least one must be set (e.g. content or content_path)
    any_of: tuple[tuple[str, ...], ...] = ()


def kind(
    name: str, summary: str, /, *, any_of: tuple[tuple[str, ...], ...] = (), **params: ParamSpec
) -> ActionKind:
    """An :class:`ActionKind` from keyword params (what the apps' ``KINDS`` are built with)."""
    return ActionKind(name, params, summary, any_of)


STR = ParamSpec("str")
REQ_STR = ParamSpec("str", True)
INT = ParamSpec("int")

_KINDS = (
    kind("noop", "Do nothing (placeholder, tests)"),
    kind(
        "message",
        "Send a message on the Culture mesh",
        actor=STR,  # legacy: a Discord app actor here still posts to Discord
        channel=REQ_STR,
        text=REQ_STR,
    ),
    kind(
        "discord.message",
        "Post a message on Discord through a Discord app actor",
        actor=REQ_STR,
        guild=STR,
        channel=REQ_STR,
        text=REQ_STR,
        buttons=ParamSpec("list"),
        thread_name=STR,  # set: channel is a forum and the text starts a post titled so
        tags=ParamSpec("list"),  # forum tag names, with thread_name only
    ),
    kind(
        "discord.read",
        "Read one Discord message in full (text and attachments) through a Discord app actor",
        actor=REQ_STR,
        guild=STR,  # fills the message url when given
        channel=REQ_STR,
        message=REQ_STR,
    ),
    kind(
        "discord.react",
        "React to one Discord message (a unicode emoji or a custom name:id) through a Discord"
        " app actor, and take back the bot's own earlier reactions (remove)",
        any_of=(("emoji", "remove"),),
        actor=REQ_STR,
        guild=STR,
        channel=REQ_STR,
        message=REQ_STR,
        emoji=STR,
        remove=ParamSpec("list"),
    ),
    kind(
        "discord.reply",
        "Answer a Discord slash or message command privately through a Discord app actor",
        actor=REQ_STR,
        event_id=REQ_STR,
        text=REQ_STR,
    ),
    kind(
        "discord.sweep",
        "Read a Discord channel's messages newer than a stored cursor through a Discord app"
        " actor",
        actor=REQ_STR,
        guild=STR,
        channel=REQ_STR,
        cursor_key=REQ_STR,
        limit=INT,
        include_bots=ParamSpec("bool"),
        backfill=ParamSpec("bool"),
    ),
    kind(
        "github.comment",
        "Comment on a GitHub issue or PR",
        actor=REQ_STR,
        repo=REQ_STR,
        number=ParamSpec("int", True),
        body=REQ_STR,
        once_key=STR,  # d25: post at most once per (repo, PR, key), durably
    ),
    kind(
        "github.merge",
        "Merge a PR as the GitHub App, only from a click carrying the button's one-time code",
        actor=REQ_STR,
        repo=REQ_STR,
        number=ParamSpec("int", True),
        sha=REQ_STR,
        delete_branch=ParamSpec("bool"),
    ),
    kind(
        "github.pr",
        "Read one PR's title, link, author, draft flag, state, repos, head branch and sha"
        " as the GitHub App",
        actor=REQ_STR,
        repo=REQ_STR,
        number=ParamSpec("int", True),
    ),
    kind("jira.comment", "Comment on a Jira issue", actor=REQ_STR, issue=REQ_STR, body=REQ_STR),
    kind(
        "http.call",
        "Call an HTTP endpoint",
        actor=REQ_STR,
        method=REQ_STR,
        url=REQ_STR,
        headers=ParamSpec("dict"),
        body=ParamSpec("any"),
        save_to=STR,  # an absolute dir the actor's http.save_roots lists: stream the body there
    ),
    kind(
        "machine.command",
        "Run a command on a machine actor",
        actor=REQ_STR,
        command=REQ_STR,
        args=ParamSpec("dict"),
    ),
    kind(
        "email.send",
        "Send an email through an email app actor (Resend)",
        actor=REQ_STR,
        to=ParamSpec("list", True),  # each must be on the actor's recipient allowlist
        subject=REQ_STR,
        html=STR,  # at least one of html/text: the port checks it
        text=STR,
        idempotency_key=STR,  # replaces the engine's per-run key (a durable, per-date key)
        sender=STR,  # replaces the actor's from address; must be at the actor's domain
        not_after=STR,  # ISO-8601: at or past it the send fails email_window_passed, no request
    ),
    kind(
        "search.web",
        "Search the web through a search app actor (Brave)",
        actor=REQ_STR,
        query=REQ_STR,
        count=INT,
        freshness=STR,
        country=STR,
        lang=STR,
    ),
    kind(
        "llm.complete",
        "Complete an actor's brief with an OpenAI-compatible model on an agent actor (no tools)",
        any_of=(("content", "content_path"),),
        actor=REQ_STR,
        content=ParamSpec("any"),  # or content_path (d2): one of the two is required
        content_path=STR,  # a schema-1 transcript file; needs content_root (checked at run time)
        content_root=STR,
        content_max_chars=INT,
        max_tokens=INT,
        temperature=ParamSpec("any"),
        not_after=STR,  # ISO-8601: past it the work fails llm_deadline instead of queueing
        brief=STR,  # a name in the actor's params.briefs; required at run time, no default
        answer=ParamSpec("dict"),  # {shape: <culture_rules.model.shape spec>}: a JSON answer
        cache=ParamSpec("bool"),  # answer + content_path: keep the first valid answer
        cache_name=STR,  # the cache file stem (answer.<name>.<sha16>.json), default "recording"
        cache_label=STR,  # the cache document's brief label, default "recording"
    ),
    kind(
        "decider.assess",
        "Ask the mesh's decider for probabilities over the options a step defines (no text)",
        actor=REQ_STR,
        state=REQ_STR,
        questions=ParamSpec(
            "dict", True
        ),  # {key: {type: choice|noul|score, instructions, criteria}}
        not_after=STR,  # ISO-8601: past it the work fails decider_deadline instead of queueing
    ),
    kind(
        "audio.transcribe",
        "Transcribe an audio file with a speech-to-text endpoint on an agent actor",
        actor=REQ_STR,
        audio_path=REQ_STR,
        audio_sha256=STR,  # optional: without it the port hashes the file itself
        output_dir=REQ_STR,
        language=STR,
        model=STR,
    ),
    kind(
        "transcript.render",
        "Render one transcript and its summary as an EPUB or Markdown (right to left for Hebrew)",
        actor=STR,  # optional: its transcripts_root/output_root is a second boundary
        result_path=REQ_STR,
        root=REQ_STR,  # the transcripts root: result_path must resolve under it
        recorded_at=REQ_STR,  # ISO-8601 with a zone; dated in Asia/Jerusalem
        language=STR,
        summary=ParamSpec("any"),  # llm.complete's output: {text} or a string, or none
        format=STR,  # epub (default) or md: summary, key facts, then the transcript
    ),
    kind(
        "library.push",
        "Upload a file to a library, or replace one by id (and queue it on a device queue)",
        any_of=(("content_base64", "content_path"),),
        actor=REQ_STR,
        content_base64=STR,  # or content_path + content_root (d7): one of the two is required
        content_path=STR,  # a file under content_root on the node; its bytes never enter run state
        content_root=STR,
        title=REQ_STR,
        kind=REQ_STR,  # one of the actor's params.kinds
        visibility=STR,  # one of the actor's params.visibilities
        author=STR,
        device_id=STR,  # used only when the actor has a queue_path
        format=STR,  # one of the actor's params.formats; epub when unset
        item_id=ParamSpec("int"),  # replace this item through the actor's replace_path
    ),
    kind(
        "repodocs.sync",
        "Push every repo's specs, plans, deliveries, designs and .devague files to the library",
        actor=REQ_STR,  # a service actor: base_url, api_key, repos_root, state_path, limits
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
