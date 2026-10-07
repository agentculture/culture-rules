"""The built-in ``action`` code step (deviation d12): any catalogued action kind as a step.

A workflow step of kind ``code`` whose ``config`` is ``{"builtin": "action", "action":
{"kind": <action kind>, "params": {...}}}`` runs that action kind through the same router as
a rule's terminal action (:mod:`culture_rules.engine.runs` turns its invocation context into
an ``"action"`` one): ``params.actor`` engages that actor's limits, an unknown or disabled
actor fails ``actor_unavailable``, and the action port's own refusals apply (``github.push``'s
``gate_verdict``, the disabled source rule, the repo allowlist). Optional
``config.action.name`` and ``config.action.idempotent`` mean what they mean on a rule action;
retry and timeout are the step's own.

Params resolve like a rule action's (:func:`culture_rules.model.refs.resolve_refs`: whole
strings, ``{{ }}`` templates, ``{"$ref": ...}``, ``{"$literal": ...}``) against one namespace,
the step's input ports: ``inputs.<port>...``. A workflow never knows its trigger, so
``trigger.*`` / ``rules.*`` / ``workflow.*`` references are refused at save time
(``action_step_ref``): a value from the trigger reaches the step through the rule's
workflow-input mapping and an edge, like any other step input. Inside a loop body the
implicit ``item`` / ``index`` ports are inputs like any other, so a ``for_each`` can post one
``github.review_reply`` per item (``inputs.item.comment_id``).

At save time the kind must be catalogued and the params pass the same kind-param checks as a
rule action (``action_kind_unknown`` / ``action_param_required`` / ``action_param_type``,
skipped in stored mode like every catalog check); every ``inputs.<port>`` reference must name
one of the step's declared input ports (``action_step_ref``). Standard-library only.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator, Mapping
from typing import Any

from culture_rules.model.refs import (
    INPUTS_NAMESPACE,
    REF_KEY,
    is_input_reference,
    is_reference,
    structured_form,
)

__all__ = [
    "ACTION_BUILTIN",
    "action_spec",
    "is_action_step",
    "ref_problems",
    "validation_params",
]

ACTION_BUILTIN = "action"
"""``config.builtin`` of a code step that runs an action kind."""

_ANY_TEMPLATE = re.compile(r"\{\{\s*((?:workflow|trigger|rules|inputs)(?:\.[^.\s{}]+)+)\s*\}\}")


def is_action_step(kind: Any, config: Any) -> bool:
    """Whether a step of ``kind`` with ``config`` is a built-in action step."""
    if kind != "code" or not isinstance(config, Mapping):
        return False
    return config.get("builtin") == ACTION_BUILTIN


def action_spec(step: Any) -> Mapping[str, Any] | None:
    """The ``config.action`` object of a built-in action step, else None."""
    config = getattr(step, "config", None)
    if not is_action_step(getattr(step, "kind", None), config):
        return None
    spec = config.get("action")
    return spec if isinstance(spec, Mapping) else None


def validation_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """``params`` as the kind-param checks should see them: a whole-string step-input
    reference is dynamic (any type, resolved at run time) like any other reference."""
    return {
        k: ({REF_KEY: v} if isinstance(v, str) and is_input_reference(v) else v)
        for k, v in params.items()
    }


def _path_refs(text: str) -> Iterator[str]:
    """The reference paths a param string holds: itself, or its ``{{ }}`` templates."""
    if is_input_reference(text) or is_reference(text):
        yield text
    yield from (m.group(1) for m in _ANY_TEMPLATE.finditer(text))


def _refs(value: Any, path: str) -> Iterator[tuple[str, str]]:
    """(param path, reference path) for every reference inside ``value``."""
    form = structured_form(value)
    if isinstance(value, str):
        yield from ((path, ref) for ref in _path_refs(value))
    elif form == REF_KEY:
        yield path, str(value[REF_KEY])
    elif form is None and isinstance(value, Mapping):
        for k, v in value.items():
            yield from _refs(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _refs(v, f"{path}[{i}]")


def ref_problems(params: Mapping[str, Any], ports: Collection[str]) -> list[tuple[str, str]]:
    """(param path, reason) for every reference that is not ``inputs.<declared port>``."""
    problems: list[tuple[str, str]] = []
    for where, ref in (hit for k, v in params.items() for hit in _refs(v, str(k))):
        head, _, rest = ref.partition(".")
        port = rest.split(".", 1)[0]
        if head != INPUTS_NAMESPACE:
            problems.append(
                (where, f"{ref!r}: an action step reads only inputs.<port> (map other values in)")
            )
        elif port not in ports:
            problems.append((where, f"inputs.{port} is not one of the step's input ports"))
    return problems
