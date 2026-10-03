"""Variable references in a rule's action params and workflow-input mappings.

A run resolves values against a context with three namespaces: ``trigger`` (the event
envelope), ``workflow`` (``workflow.outputs.<name>``, action params only) and ``rules``
(``rules.<id>.outputs.<name>``, outputs exported by a predecessor). Three forms:

* a **plain string** is a reference only when its whole path fits a namespace's shape
  (:func:`is_reference`): ``trigger.<f>...`` where ``f`` is an envelope field
  (:data:`TRIGGER_FIELDS`) or a key the event carries, ``workflow.outputs.<name>...``,
  ``rules.<id>.outputs.<name>...``. Any other string is a literal, so ``rules.yaml``,
  ``workflow.md`` and ``trigger.sh`` reach the actor unchanged. A reference whose value
  is absent resolves to ``None`` (an optional input stays missing);
* ``{"$ref": "<path>"}`` (a one-key object) always references, ``None`` when absent;
  :func:`ref_errors` (called by :func:`culture_rules.model.validate.validate` for a
  rule's ``action.params``) refuses at save time a path that can never resolve;
* ``{"$literal": <value>}`` (a one-key object) is passed through verbatim - the escape
  for a string that *would* resolve, such as the text ``trigger.id``.

``{{ <path> }}`` templates inside a longer string are substituted (absent: empty text).
Workflow-input mappings are strings (the rule schema), so only the plain form applies
there. Standard-library only.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator, Mapping
from typing import Any

__all__ = [
    "LITERAL_KEY",
    "NAMESPACES",
    "REF_KEY",
    "TRIGGER_FIELDS",
    "is_reference",
    "lookup",
    "ref_error",
    "ref_errors",
    "resolve_refs",
    "scanned_strings",
    "structured_form",
]

REF_KEY = "$ref"
LITERAL_KEY = "$literal"
NAMESPACES = ("trigger", "workflow", "rules")

#: Fields an event envelope may carry (events-cli wire form): ``trigger.<field>...`` is a
#: reference even on an event that lacks the field.
TRIGGER_FIELDS = frozenset(
    {
        "id",
        "type",
        "source",
        "time",
        "data",
        "schemaVersion",
        "correlationId",
        "causationId",
        "runId",
        "subject",
    }
)

_PATH = re.compile(r"^(?:workflow|trigger|rules)(?:\.[^.\s{}]+)+$")
_TEMPLATE = re.compile(r"\{\{\s*((?:workflow|trigger|rules)(?:\.[^.\s{}]+)+)\s*\}\}")


def lookup(context: Mapping[str, Any], path: str) -> Any:
    """The value at dotted ``path`` in ``context``, or None."""
    cur: Any = context
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _shape_ok(parts: list[str]) -> bool:
    """Whether a split path has the shape of its namespace (trigger: any field)."""
    if parts[0] == "workflow":
        return len(parts) >= 3 and parts[1] == "outputs"
    if parts[0] == "rules":
        return len(parts) >= 4 and parts[2] == "outputs"
    return len(parts) >= 2


def is_reference(path: str, trigger: Mapping[str, Any] | None = None) -> bool:
    """Whether the whole string ``path`` reads as a reference rather than a literal."""
    if not _PATH.match(path):
        return False
    parts = path.split(".")
    if parts[0] == "trigger":
        return parts[1] in TRIGGER_FIELDS or parts[1] in (trigger or {})
    return _shape_ok(parts)


def structured_form(value: Any) -> str | None:
    """:data:`REF_KEY` / :data:`LITERAL_KEY` when ``value`` is that one-key object."""
    if isinstance(value, Mapping) and len(value) == 1:
        (key,) = value
        if key in (REF_KEY, LITERAL_KEY):
            return key
    return None


def ref_error(path: Any, *, has_workflow: bool) -> str | None:
    """Why a ``{"$ref": path}`` can never resolve, or None when it can."""
    if not isinstance(path, str) or not _PATH.match(path):
        return (
            f"{path!r} is not a reference: expected a dotted path in one of the namespaces "
            + ", ".join(NAMESPACES)
        )
    parts = path.split(".")
    if not _shape_ok(parts):
        return (
            f"{path!r} does not resolve: use trigger.<field>, workflow.outputs.<name> "
            "or rules.<id>.outputs.<name>"
        )
    if parts[0] == "workflow" and not has_workflow:
        return f"{path!r} does not resolve: the rule runs no workflow"
    return None


def scanned_strings(value: Any, path: str, join: Any) -> Iterator[tuple[str, str]]:
    """(path, string) for every string inside ``value``, skipping ``$literal`` values."""
    if isinstance(value, str):
        yield path, value
    elif structured_form(value) == LITERAL_KEY:
        return
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from scanned_strings(v, join(path, str(k)), join)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from scanned_strings(v, join(path, i), join)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def resolve_refs(value: Any, context: Mapping[str, Any]) -> Any:
    """Resolve every reference form (see the module docstring) inside ``value``."""
    if isinstance(value, str):
        if is_reference(value, context.get("trigger")):
            return lookup(context, value)
        return _TEMPLATE.sub(lambda m: _text(lookup(context, m.group(1))), value)
    form = structured_form(value)
    if form == LITERAL_KEY:
        return copy.deepcopy(value[LITERAL_KEY])
    if form == REF_KEY:
        path = value[REF_KEY]
        return lookup(context, path) if isinstance(path, str) else None
    if isinstance(value, Mapping):
        return {k: resolve_refs(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_refs(v, context) for v in value]
    return value


def ref_errors(
    value: Any, path: str, join: Any, *, has_workflow: bool
) -> Iterator[tuple[str, str]]:
    """(path, reason) for every ``{"$ref": ...}`` inside ``value`` that can never resolve."""
    form = structured_form(value)
    if form == REF_KEY:
        reason = ref_error(value[REF_KEY], has_workflow=has_workflow)
        if reason is not None:
            yield path, reason
    elif form is None and isinstance(value, Mapping):
        for k, v in value.items():
            yield from ref_errors(v, join(path, str(k)), join, has_workflow=has_workflow)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from ref_errors(v, join(path, i), join, has_workflow=has_workflow)
