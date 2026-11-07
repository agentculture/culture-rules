"""Which shared variables a rule references (shared by save-time validation and the API).

A rule references a variable through its condition tree (``{"var": name}``, the
``vars.<name>`` text form) or a workflow input mapped as ``{"$var": name}``. Works on
a :class:`~culture_rules.model.rule.Rule` or its plain-dict document.
"""

from __future__ import annotations

from typing import Any, Mapping

from culture_rules.model.refs import var_name

__all__ = ["condition_variable_refs", "rule_variable_refs"]


def condition_variable_refs(tree: Any) -> set[str]:
    """Every name a condition tree reads as ``{"var": name}``, at any depth."""
    found: set[str] = set()
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, Mapping):
            if len(node) == 1 and "literal" in node:
                continue  # a literal operand is data, never a reference
            name = node.get("var")
            if isinstance(name, str) and len(node) == 1:
                found.add(name)
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return found


def rule_field(obj: Any, name: str) -> Any:
    """``name`` of a rule part, whether a model (attribute) or a stored document (key)."""
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def rule_variable_refs(rule: Any) -> set[str]:
    """Every variable a rule (model or document) references in its condition or inputs."""
    found = condition_variable_refs(rule_field(rule, "condition"))
    inputs = rule_field(rule_field(rule, "workflow"), "inputs") or {}
    for value in inputs.values() if isinstance(inputs, Mapping) else ():
        name = var_name(value)  # the same reading the save check uses (one-key ``$var``)
        if name is not None:
            found.add(name)
    return found
