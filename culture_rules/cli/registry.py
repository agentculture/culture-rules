"""The command registry: every verb is registered once, and every surface reads this registry.

Cited (cite-don't-import) from agentfront 0.20.0 (``agentfront/app.py`` and
``agentfront/_registry.py``): one registry is the single source of truth, surfaces only
enumerate it and cannot add to it. This copy is trimmed to what culture-rules needs and keeps
the runtime free of third-party dependencies. It is owned by culture-rules and may diverge.

Each :class:`Verb` carries its name, a params schema (JSON Schema, derived from :class:`Param`),
a ``mutating`` flag and the minimum role the server requires (``viewer < editor < admin``). The
CLI builds its argparse tree from it, ``learn`` and ``explain`` list it, the MCP server builds
its tools from it, and the parity test enumerates it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ROLES", "Context", "DuplicateVerb", "Param", "Registry", "Verb", "role_allows"]

ROLES: tuple[str, ...] = ("viewer", "editor", "admin")
"""Ordered least to most privileged."""

_JSON_TYPES = {"string", "integer", "boolean", "object", "array", "any"}
"""``any`` is any JSON value (a scalar, list or object); its schema carries no ``type``."""


class DuplicateVerb(ValueError):
    """A (noun, name) pair was registered twice."""


def role_allows(have: str, need: str) -> bool:
    """True when role ``have`` is at least ``need``."""
    return ROLES.index(have) >= ROLES.index(need)


@dataclass(frozen=True)
class Param:
    """One verb parameter. ``type`` is a JSON Schema type; ``object`` takes JSON."""

    name: str
    type: str = "string"
    help: str = ""
    required: bool = False
    positional: bool = False
    default: Any = None

    def __post_init__(self) -> None:
        if self.type not in _JSON_TYPES:
            raise ValueError(f"unsupported param type {self.type!r}")

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {"description": self.help}
        if self.type != "any":
            out = {"type": self.type, **out}
        if self.default is not None:
            out["default"] = self.default
        return out


@dataclass
class Context:
    """What a handler runs against: an API client and whether the write is applied."""

    client: Any
    apply: bool = False


Handler = Callable[..., Any]


@dataclass
class Verb:
    noun: str
    name: str
    summary: str
    handler: Handler
    params: tuple[Param, ...] = ()
    mutating: bool = False
    role: str = "viewer"
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")

    @property
    def path(self) -> tuple[str, str]:
        return (self.noun, self.name)

    @property
    def tool_name(self) -> str:
        """Stable MCP tool name: ``<noun>_<verb>``."""
        return f"{self.noun}_{self.name}"

    def params_schema(self) -> dict[str, Any]:
        """JSON Schema of the verb's arguments; a mutating verb gains ``apply`` (default off)."""
        props = {p.name: p.schema() for p in self.params}
        if self.mutating:
            props["apply"] = {
                "type": "boolean",
                "default": False,
                "description": "false = dry-run (change nothing); true = commit the write",
            }
        return {
            "type": "object",
            "properties": props,
            "required": [p.name for p in self.params if p.required],
            "additionalProperties": False,
        }


class Registry:
    def __init__(self) -> None:
        self._verbs: dict[tuple[str, str], Verb] = {}

    def add(self, verb: Verb) -> Verb:
        if verb.path in self._verbs:
            raise DuplicateVerb(f"verb {' '.join(verb.path)!r} is already registered")
        self._verbs[verb.path] = verb
        return verb

    def get(self, noun: str, name: str) -> Verb | None:
        return self._verbs.get((noun, name))

    def verbs(self, noun: str | None = None) -> list[Verb]:
        return [v for v in self._verbs.values() if noun is None or v.noun == noun]

    def nouns(self) -> list[str]:
        return list(dict.fromkeys(v.noun for v in self._verbs.values()))

    def extend(self, verbs: Iterable[Verb]) -> None:
        for v in verbs:
            self.add(v)
