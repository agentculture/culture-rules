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
  export ``<name>``.

References are scanned in the rule's ``action.params`` and ``workflow.inputs`` values,
anywhere in a string (plain or inside ``{{ ... }}`` templates).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from culture_rules.engine.matching import exported_outputs
from culture_rules.model.rule import Rule
from culture_rules.model.serde import join
from culture_rules.model.validate import ValidationError
from culture_rules.model.workflow import Workflow

__all__ = ["OUTPUT_REF", "validate_rule_set"]

#: ``rules.<rule id>.outputs.<output name>``
OUTPUT_REF = re.compile(r"\brules\.([A-Za-z0-9_\-]+)\.outputs\.([A-Za-z_][A-Za-z0-9_]*)")


def _find_cycle(graph: Mapping[str, tuple[str, ...]]) -> list[str] | None:
    state: dict[str, int] = {}  # 1 = on stack, 2 = done

    def visit(node: str, stack: list[str]) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt) :] + [nxt]
            if nxt not in state:
                found = visit(nxt, stack)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(graph):
        if node not in state:
            found = visit(node, [])
            if found:
                return found
    return None


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
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _strings(v, join(path, str(k)))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _strings(v, join(path, i))


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


def validate_rule_set(
    rules: Iterable[Rule], workflows: Mapping[str, Workflow] | None = None
) -> list[ValidationError]:
    """Validate relationships across a rule set; returns ``[]`` when valid."""
    rules = list(rules)
    workflows = workflows or {}
    by_id = {r.id: r for r in rules}
    errors: list[ValidationError] = []

    for rel, code in (("supersedes", "supersede_cycle"), ("must_after", "predecessor_cycle")):
        cycle = _find_cycle({r.id: tuple(getattr(r, rel)) for r in rules})
        if cycle:
            errors.append(
                ValidationError("rules", code, f"{rel} edges form a cycle: " + " -> ".join(cycle))
            )

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

    for i, r in enumerate(rules):
        for rel in ("must_after", "may_after", "supersedes"):
            for j, rid in enumerate(getattr(r, rel)):
                if rid not in by_id:
                    errors.append(
                        ValidationError(
                            join(join(join("rules", i), rel), j),
                            "unknown_rule",
                            f"{rel} of {r.id!r} names {rid!r}, which is not a rule in the set",
                        )
                    )
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
