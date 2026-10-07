"""Variable references in a rule's action params and workflow-input mappings.

A run resolves values against a context with four namespaces: ``trigger`` (the event
envelope), ``workflow`` (``workflow.outputs.<name>``, action params only), ``rules``
(``rules.<id>.outputs.<name>``, outputs exported by a predecessor) and ``run`` (``run.id``,
the run's own id, rule action params only - so a terminal comment can link its run; a rule's
``on_failure`` params also read ``run.error.step`` / ``.code`` / ``.message``, the failure that
ended the run). Three forms:

* a **plain string** is a reference only when its whole path fits a namespace's shape
  (:func:`is_reference`): ``trigger.<f>...`` where ``f`` is an envelope field
  (:data:`TRIGGER_FIELDS`) or a key the event carries, ``workflow.outputs.<name>...``,
  ``rules.<id>.outputs.<name>...``, ``run.id``. Any other string is a literal, so ``rules.yaml``,
  ``workflow.md`` and ``trigger.sh`` reach the actor unchanged. A reference whose value
  is absent resolves to ``None`` (an optional input stays missing);
* ``{"$ref": "<path>"}`` (a one-key object) always references, ``None`` when absent;
  :func:`ref_errors` (called by :func:`culture_rules.model.validate.validate` for a
  rule's ``action.params``) refuses at save time a path that can never resolve;
* ``{"$literal": <value>}`` (a one-key object) is passed through verbatim - the escape
  for a string that *would* resolve, such as the text ``trigger.id``.

``{{ <path> }}`` templates inside a longer string are substituted (absent: empty text).
Workflow-input mappings accept all three forms (a string or a structured object) and a
fourth, ``{"$var": <name>}`` (a one-key object): the current value of the shared variable
``name``, read from the context's ``variables`` namespace (``None`` when undefined). Only
a context that carries ``variables`` (the engine passes it when it maps workflow inputs)
resolves it; elsewhere, such as action params, the object passes through as written. The
model validator refuses any other value.

A context that carries ``inputs`` (the built-in action step's input ports,
:mod:`culture_rules.model.action_step`) also resolves ``inputs.<port>...`` as a whole string
and inside ``{{ }}`` templates (``{"$ref": "inputs..."}`` resolves anywhere). Without that
key such strings stay literals, so a rule action's params are unaffected. Standard-library
only.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator, Mapping
from typing import Any

__all__ = [
    "INPUTS_NAMESPACE",
    "LITERAL_KEY",
    "NAMESPACES",
    "REF_KEY",
    "TRIGGER_FIELDS",
    "VAR_KEY",
    "is_input_reference",
    "is_reference",
    "lookup",
    "ref_error",
    "ref_errors",
    "resolve_refs",
    "run_error_refs",
    "scanned_strings",
    "structured_form",
    "var_name",
]

REF_KEY = "$ref"
LITERAL_KEY = "$literal"
VAR_KEY = "$var"
NAMESPACES = ("trigger", "workflow", "rules", "run")
RUN_FIELDS = frozenset({"id"})
"""The ``run.<field>`` paths a rule action may read (the run's own id)."""
RUN_ERROR_FIELDS = frozenset({"step", "code", "message"})
"""The ``run.error.<field>`` paths a rule's ``on_failure`` params may read."""
INPUTS_NAMESPACE = "inputs"
"""The step-input namespace, resolved only by a context that carries it (action steps)."""

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

_PATH = re.compile(r"^(?:workflow|trigger|rules|run)(?:\.[^.\s{}]+)+$")
_TEMPLATE = re.compile(
    r"\{\{\s*((?:workflow|trigger|rules)(?:\.[^.\s{}]+)+"
    r"|run\.id|run\.error\.(?:step|code|message))\s*\}\}"
)
_RUN_ERROR = re.compile(r"^run\.error(?:\.|$)|\{\{\s*run\.error\b")
_INPUT_PATH = re.compile(r"^inputs(?:\.[^.\s{}]+)+$")
_STEP_TEMPLATE = re.compile(r"\{\{\s*((?:workflow|trigger|rules|inputs)(?:\.[^.\s{}]+)+)\s*\}\}")


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
    if parts[0] == "run":
        if len(parts) == 3 and parts[1] == "error":
            return parts[2] in RUN_ERROR_FIELDS
        return len(parts) == 2 and parts[1] in RUN_FIELDS
    return len(parts) >= 2


def is_reference(path: str, trigger: Mapping[str, Any] | None = None) -> bool:
    """Whether the whole string ``path`` reads as a reference rather than a literal."""
    if not _PATH.match(path):
        return False
    parts = path.split(".")
    if parts[0] == "trigger":
        return parts[1] in TRIGGER_FIELDS or parts[1] in (trigger or {})
    return _shape_ok(parts)


def is_input_reference(path: str) -> bool:
    """Whether the whole string ``path`` is a step-input reference (``inputs.<port>...``)."""
    return bool(_INPUT_PATH.match(path))


def structured_form(value: Any) -> str | None:
    """:data:`REF_KEY` / :data:`LITERAL_KEY` when ``value`` is that one-key object."""
    if isinstance(value, Mapping) and len(value) == 1:
        (key,) = value
        if key in (REF_KEY, LITERAL_KEY):
            return key
    return None


def var_name(value: Any) -> str | None:
    """The variable name of a ``{"$var": <name>}`` one-key object (any string), else None."""
    if isinstance(value, Mapping) and len(value) == 1 and isinstance(value.get(VAR_KEY), str):
        return value[VAR_KEY]
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
            f"{path!r} does not resolve: use trigger.<field>, workflow.outputs.<name>, "
            "rules.<id>.outputs.<name>, run.id or (on_failure only) run.error.<step|code|message>"
        )
    if parts[0] == "workflow" and not has_workflow:
        return f"{path!r} does not resolve: the rule runs no workflow"
    return None


def run_error_refs(value: Any, path: str, join: Any) -> Iterator[str]:
    """Paths of every ``run.error...`` reference inside ``value`` (whole string, template or
    ``$ref``): only a rule's ``on_failure`` may hold one."""
    for where, text in scanned_strings(value, path, join):
        if _RUN_ERROR.search(text.strip()):
            yield where


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
        step = INPUTS_NAMESPACE in context
        if is_reference(value, context.get("trigger")) or (step and is_input_reference(value)):
            return lookup(context, value)
        template = _STEP_TEMPLATE if step else _TEMPLATE  # one pass: no re-expansion
        return template.sub(lambda m: _text(lookup(context, m.group(1))), value)
    variables = context.get("variables")
    if isinstance(variables, Mapping) and (name := var_name(value)) is not None:
        return copy.deepcopy(variables.get(name))
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
