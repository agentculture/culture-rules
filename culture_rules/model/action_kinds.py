"""Action kind catalog: typed ``params`` per kind and the ``params.actor`` binding convention.

An :class:`~culture_rules.model.action.Action` has no actor slot; an action that invokes an
actor capability names the actor in ``params.actor`` (an actor id, or a reference). Per kind
this module says which params exist, their types and which are required.

Actor policy: ``github.comment``, ``jira.comment``, ``http.call`` and ``machine.command``
always need ``params.actor`` (the credentialed or executing party). ``message`` does not:
omitting the actor sends on the Culture mesh; naming one (e.g. a discord app actor) routes
through it. ``mesh.message`` is a legacy alias of ``message`` (the web editor's placeholder).

Any param value may be a reference (``trigger.data.number``), a ``{"$ref": ...}`` object or a
``{{ }}`` template, accepted wherever a typed value is expected. Extra params are tolerated.
Standard-library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from culture_rules.model.refs import REF_KEY, is_reference, structured_form

__all__ = ["ACTION_KINDS", "ActionKind", "ParamSpec", "param_type_ok"]


@dataclass(frozen=True)
class ParamSpec:
    """One param: ``type`` is str, int, dict or list; ``required`` means present and non-empty."""

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

_KINDS = (
    _k("noop", "Do nothing (placeholder, tests)"),
    _k(
        "message",
        "Send a message; actor omitted means the Culture mesh",
        actor=_S,
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
        args=ParamSpec("list"),
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
