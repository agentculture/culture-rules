"""Save-time validation of a rule set: relationship cycles and exported-output references.

``validate_rule_set(rules, workflows)`` returns structured
:class:`~culture_rules.model.validate.ValidationError` objects and never raises. It checks
what single-rule validation cannot:

* ``supersede_cycle`` -- the ``supersedes`` edges form a cycle;
* ``predecessor_cycle`` -- the ``must_after`` edges form a cycle (it could never run);
* ``unrunnable_relationship`` -- a rule supersedes a rule it must run after, directly or
  transitively (A supersedes* B and A must_after* B): whenever A matches, B is skipped,
  so B never succeeds for that event and A can never fire;
* ``not_a_predecessor`` -- a ``rules.<id>.outputs.<name>`` reference names a rule that is
  not in this rule's ``must_after`` / ``may_after``;
* ``unexported_output`` -- the referenced predecessor's workflow does not explicitly
  export ``<name>``;
* ``invalid_reference`` -- a structured ``{"$ref": path}`` in ``action.params`` can never
  resolve (no namespace, a wrong shape, or ``workflow.outputs`` on a rule without a
  workflow; :func:`culture_rules.engine.refs.ref_error`).

References are scanned in the rule's ``action.params`` and ``workflow.inputs`` values,
anywhere in a string (plain, ``{"$ref": ...}``, or inside ``{{ ... }}`` templates);
``{"$literal": ...}`` values are not references and are not scanned.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from culture_rules.engine.matching import exported_outputs
from culture_rules.engine.refs import REF_KEY, ref_error, scanned_strings, structured_form
from culture_rules.model.graph import find_cycle
from culture_rules.model.rule import Rule
from culture_rules.model.serde import join
from culture_rules.model.validate import ValidationError
from culture_rules.model.workflow import Workflow

__all__ = ["OUTPUT_REF", "validate_rule_set"]

#: ``rules.<rule id>.outputs.<output name>``
#: ``(?a:...)`` keeps ``\w`` ASCII-only, as ``[A-Za-z0-9_]``, while ``\b`` stays Unicode-aware.
OUTPUT_REF = re.compile(r"\brules\.((?a:[\w-]+))\.outputs\.((?a:[A-Za-z_]\w*))")


def _reach(start: str, graph: Mapping[str, tuple[str, ...]]) -> set[str]:
    """Every rule reachable from ``start`` over ``graph`` (not ``start`` unless on a cycle)."""
    seen: set[str] = set()
    todo = list(graph.get(start, ()))
    while todo:
        nxt = todo.pop()
        if nxt not in seen:
            seen.add(nxt)
            todo.extend(graph.get(nxt, ()))
    return seen


def _strings(value: Any, path: str) -> Iterator[tuple[str, str]]:
    return scanned_strings(value, path, join)


def _structured_refs(value: Any, path: str) -> Iterator[tuple[str, Any]]:
    """(path, target) of every ``{"$ref": target}`` inside ``value``."""
    if structured_form(value) == REF_KEY:
        yield path, value[REF_KEY]
    elif isinstance(value, Mapping) and structured_form(value) is None:
        for k, v in value.items():
            yield from _structured_refs(v, join(path, str(k)))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _structured_refs(v, join(path, i))


def _ref_form_errors(i: int, r: Rule) -> list[ValidationError]:
    params_path = join(join(join("rules", i), "action"), "params")
    found = _structured_refs(r.action.params, params_path)
    return [
        ValidationError(path, "invalid_reference", message)
        for path, target in found
        if (message := ref_error(target, has_workflow=r.workflow is not None)) is not None
    ]


def _references(rule: Rule, path: str) -> Iterator[tuple[str, str, str]]:
    """(path, rule id, output name) for every output reference in the rule."""
    yield from (
        (p, m.group(1), m.group(2))
        for p, s in _strings(rule.action.params, join(join(path, "action"), "params"))
        for m in OUTPUT_REF.finditer(s)
    )
    if rule.workflow is not None:
        yield from (
            (p, m.group(1), m.group(2))
            for p, s in _strings(rule.workflow.inputs, join(join(path, "workflow"), "inputs"))
            for m in OUTPUT_REF.finditer(s)
        )


def _cycle_errors(rules: list[Rule]) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for rel, code in (("supersedes", "supersede_cycle"), ("must_after", "predecessor_cycle")):
        cycle = find_cycle({r.id: tuple(getattr(r, rel)) for r in rules})
        if cycle:
            errors.append(
                ValidationError("rules", code, f"{rel} edges form a cycle: " + " -> ".join(cycle))
            )
    return errors


def _unrunnable_errors(rules: list[Rule]) -> list[ValidationError]:
    errors: list[ValidationError] = []
    supersedes = {r.id: tuple(r.supersedes) for r in rules}
    must_after = {r.id: tuple(r.must_after) for r in rules}
    for i, r in enumerate(rules):
        both = sorted((_reach(r.id, supersedes) & _reach(r.id, must_after)) - {r.id})
        if both:
            names = ", ".join(repr(b) for b in both)
            errors.append(
                ValidationError(
                    join("rules", i),
                    "unrunnable_relationship",
                    f"rule {r.id!r} supersedes {names} and must run after it, directly or "
                    "transitively, so it can never fire",
                )
            )
    return errors


def _unknown_rule_errors(i: int, r: Rule, by_id: Mapping[str, Rule]) -> list[ValidationError]:
    return [
        ValidationError(
            join(join(join("rules", i), rel), j),
            "unknown_rule",
            f"{rel} of {r.id!r} names {rid!r}, which is not a rule in the set",
        )
        for rel in ("must_after", "may_after", "supersedes")
        for j, rid in enumerate(getattr(r, rel))
        if rid not in by_id
    ]


def _reference_errors(
    i: int, r: Rule, by_id: Mapping[str, Rule], workflows: Mapping[str, Workflow]
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    preds = set(r.must_after) | set(r.may_after)
    for path, rid, name in _references(r, join("rules", i)):
        if rid not in preds:
            errors.append(
                ValidationError(
                    path,
                    "not_a_predecessor",
                    f"rule {rid!r} is not in must_after/may_after of {r.id!r}",
                )
            )
            continue
        pred = by_id.get(rid)
        if pred is None or name not in exported_outputs(pred, workflows):
            errors.append(
                ValidationError(
                    path,
                    "unexported_output",
                    f"rule {rid!r} does not export output {name!r}",
                )
            )
    return errors


def validate_rule_set(
    rules: Iterable[Rule], workflows: Mapping[str, Workflow] | None = None
) -> list[ValidationError]:
    """Validate relationships across a rule set; returns ``[]`` when valid."""
    rules = list(rules)
    workflows = workflows or {}
    by_id = {r.id: r for r in rules}
    errors = _cycle_errors(rules) + _unrunnable_errors(rules)
    for i, r in enumerate(rules):
        errors += _unknown_rule_errors(i, r, by_id)
        errors += _reference_errors(i, r, by_id, workflows)
        errors += _ref_form_errors(i, r)
    return errors
