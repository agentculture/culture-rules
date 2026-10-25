"""Action kind catalog: typed ``params`` per kind and the ``params.actor`` binding convention.

An :class:`~culture_rules.model.action.Action` has no actor slot; an action that invokes an
actor capability names the actor in ``params.actor`` (an actor id, or a reference). Per kind
this module says which params exist, their types and which are required.

Actor policy: ``discord.message``, ``discord.read``, ``github.comment``, ``github.push``,
``github.review_reply``, ``jira.comment``, ``http.call``, ``machine.command``, ``email.send``,
``search.web``, ``llm.complete``, ``audio.transcribe``, ``decider.assess`` and ``library.push``
always need ``params.actor`` (the credentialed or executing party).
``message`` sends on the Culture mesh and takes no actor; a stored ``message`` that still names
a Discord app actor (the form before ``discord.message`` existed) keeps posting through it.
``discord.message`` posts to a Discord channel through a Discord app actor; ``guild`` records
the server the channel was picked from. ``discord.read`` reads one message in full (its
text untruncated and its attachments) with the same actor's token, within its allow-list.
``mesh.message`` is a legacy alias of ``message``.

Any param value may be a reference (``trigger.data.number``), a ``{"$ref": ...}`` object or a
``{{ }}`` template, accepted wherever a typed value is expected. Extra params are tolerated.

``github.push`` fast-forwards a same-repo PR's head branch to a local commit, as the App and
never with force (and, with ``gate_verdict`` wired in, only after a test gate ``pass``);
``github.review_reply`` replies in a PR review thread and optionally resolves it. There is
deliberately **no merge kind**: merging stays a human gate.

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
Standard-library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from culture_rules.model.refs import REF_KEY, is_reference, structured_form

__all__ = ["ACTION_KINDS", "ActionKind", "ParamSpec", "param_type_ok"]


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


def _k(
    name: str, summary: str, /, *, any_of: tuple[tuple[str, ...], ...] = (), **params: ParamSpec
) -> ActionKind:
    return ActionKind(name, params, summary, any_of)


_S = ParamSpec("str")
_RS = ParamSpec("str", True)
_I = ParamSpec("int")

_KINDS = (
    _k("noop", "Do nothing (placeholder, tests)"),
    _k(
        "message",
        "Send a message on the Culture mesh",
        actor=_S,  # legacy: a Discord app actor here still posts to Discord
        channel=_RS,
        text=_RS,
    ),
    _k(
        "discord.message",
        "Post a message on Discord through a Discord app actor",
        actor=_RS,
        guild=_S,
        channel=_RS,
        text=_RS,
    ),
    _k(
        "discord.read",
        "Read one Discord message in full (text and attachments) through a Discord app actor",
        actor=_RS,
        guild=_S,  # fills the message url when given
        channel=_RS,
        message=_RS,
    ),
    _k(
        "github.comment",
        "Comment on a GitHub issue or PR",
        actor=_RS,
        repo=_RS,
        number=ParamSpec("int", True),
        body=_RS,
        once_key=_S,  # d25: post at most once per (repo, PR, key), durably
        status=ParamSpec("bool"),  # d26: write as the chain's status comment's final section
    ),
    _k(
        "github.push",
        "Fast-forward a same-repo PR's head branch as the GitHub App (never force)",
        actor=_RS,
        repo=_RS,
        number=ParamSpec("int", True),
        head_branch=_RS,
        expected_head_sha=_RS,
        commit_sha=_RS,
        source=_RS,
        gate_verdict=_S,
    ),
    _k(
        "github.review_reply",
        "Reply in a PR review thread as the GitHub App, optionally resolving it",
        actor=_RS,
        repo=_RS,
        number=ParamSpec("int", True),
        comment_id=ParamSpec("int", True),
        body=_RS,
        thread_id=_S,
        resolve=ParamSpec("bool"),
    ),
    _k("jira.comment", "Comment on a Jira issue", actor=_RS, issue=_RS, body=_RS),
    _k(
        "http.call",
        "Call an HTTP endpoint",
        actor=_RS,
        method=_RS,
        url=_RS,
        headers=ParamSpec("dict"),
        body=ParamSpec("any"),
    ),
    _k(
        "machine.command",
        "Run a command on a machine actor",
        actor=_RS,
        command=_RS,
        args=ParamSpec("dict"),
    ),
    _k(
        "email.send",
        "Send an email through an email app actor (Resend)",
        actor=_RS,
        to=ParamSpec("list", True),  # each must be on the actor's recipient allowlist
        subject=_RS,
        html=_S,  # at least one of html/text: the port checks it
        text=_S,
        idempotency_key=_S,  # replaces the engine's per-run key (a durable, per-date key)
        sender=_S,  # replaces the actor's from address; must be at the actor's domain
        not_after=_S,  # ISO-8601: at or past it the send fails email_window_passed, no request
    ),
    _k(
        "search.web",
        "Search the web through a search app actor (Brave)",
        actor=_RS,
        query=_RS,
        count=_I,
        freshness=_S,
        country=_S,
        lang=_S,
    ),
    _k(
        "llm.complete",
        "Complete a locked brief with an OpenAI-compatible model on an agent actor (no tools)",
        any_of=(("content", "content_path"),),
        actor=_RS,
        content=ParamSpec("any"),  # or content_path (d2): one of the two is required
        content_path=_S,  # a schema-1 transcript file; needs content_root (checked at run time)
        content_root=_S,
        content_max_chars=_I,
        max_tokens=_I,
        temperature=ParamSpec("any"),
        not_after=_S,  # ISO-8601: past it the work fails llm_deadline instead of queueing
        brief=_S,  # which locked brief: newspaper (default), transcript or recording
        cache=ParamSpec("bool"),  # recording + content_path: keep the first valid answer
    ),
    _k(
        "decider.assess",
        "Ask the mesh's decider for probabilities over the options a step defines (no text)",
        actor=_RS,
        state=_RS,
        questions=ParamSpec(
            "dict", True
        ),  # {key: {type: choice|noul|score, instructions, criteria}}
        not_after=_S,  # ISO-8601: past it the work fails decider_deadline instead of queueing
    ),
    _k(
        "audio.transcribe",
        "Transcribe an audio file with a speech-to-text endpoint on an agent actor",
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
        actor=_S,  # optional: its transcripts_root/output_root is a second boundary
        result_path=_RS,
        root=_RS,  # the transcripts root: result_path must resolve under it
        recorded_at=_RS,  # ISO-8601 with a zone; dated in Asia/Jerusalem
        language=_S,
        summary=ParamSpec("any"),  # llm.complete's output: {text} or a string, or none
        format=_S,  # epub (default) or md: summary, key facts, then the transcript
    ),
    _k(
        "library.push",
        "Upload a file to a library, or replace one by id (and queue it on a device queue)",
        actor=_RS,
        content_base64=_RS,
        title=_RS,
        kind=_RS,  # one of the actor's params.kinds
        visibility=_S,  # one of the actor's params.visibilities
        author=_S,
        device_id=_S,  # used only when the actor has a queue_path
        format=_S,  # one of the actor's params.formats; epub when unset
        item_id=ParamSpec("int"),  # replace this item through the actor's replace_path
    ),
)

ACTION_KINDS: dict[str, ActionKind] = {k.name: k for k in _KINDS}

#: Legacy names accepted as aliases of a catalogued kind (web editor placeholder).
_ALIASES = {"mesh.message": "message"}
#: ``message`` is lenient: channel/text are not enforced so the legacy placeholder passes.
_LENIENT = frozenset({"message"})


def resolve_kind(kind: str) -> ActionKind | None:
    """The catalogue entry for ``kind`` (aliases resolved), or ``None`` if unknown."""
    return ACTION_KINDS.get(_ALIASES.get(kind, kind))


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
    """Whether the kind's required params are not enforced (legacy-tolerant kinds)."""
    return _ALIASES.get(kind, kind) in _LENIENT
