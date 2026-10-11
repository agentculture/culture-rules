"""Action kind catalog: typed ``params`` per kind and the ``params.actor`` binding convention.

An :class:`~culture_rules.model.action.Action` has no actor slot; an action that invokes an
actor capability names the actor in ``params.actor`` (an actor id, or a reference). Per kind
this module says which params exist, their types and which are required.

Actor policy: ``discord.message``, ``github.comment``, ``github.push``, ``github.review_reply``,
``jira.comment``, ``http.call``, ``machine.command``, ``email.send``, ``search.web``,
``llm.complete`` and ``library.push`` always need ``params.actor`` (the credentialed or
executing party).
``message`` sends on the Culture mesh and takes no actor; a stored ``message`` that still names
a Discord app actor (the form before ``discord.message`` existed) keeps posting through it.
``discord.message`` posts to a Discord channel through a Discord app actor; ``guild`` records
the server the channel was picked from. ``mesh.message`` is a legacy alias of ``message``.

Any param value may be a reference (``trigger.data.number``), a ``{"$ref": ...}`` object or a
``{{ }}`` template, accepted wherever a typed value is expected. Extra params are tolerated.

``github.push`` fast-forwards a same-repo PR's head branch to a local commit, as the App and
never with force (and, with ``gate_verdict`` wired in, only after a test gate ``pass``);
``github.review_reply`` replies in a PR review thread and optionally resolves it. There is
deliberately **no merge kind**: merging stays a human gate.

The daily-newspaper kinds (#53): ``email.send`` sends through an ``email`` app actor to
addresses on its recipient allowlist; ``search.web`` queries through a ``search`` app actor;
``llm.complete`` asks an agent actor's OpenAI-compatible endpoint (never with tools);
``library.push`` uploads ``content_base64`` (an EPUB) to xteink and queues it for
``device_id``.
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


def _k(name: str, summary: str, **params: ParamSpec) -> ActionKind:
    return ActionKind(name, params, summary)


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
        actor=_RS,
        content=ParamSpec("any", True),
        max_tokens=_I,
        temperature=ParamSpec("any"),
    ),
    _k(
        "library.push",
        "Upload an EPUB to the xteink library and queue it for a device",
        actor=_RS,
        content_base64=_RS,
        title=_RS,
        author=_S,
        device_id=_S,
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
