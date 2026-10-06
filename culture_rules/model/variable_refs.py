"""Which shared variables a rule references (shared by save-time validation and the API).

A rule references a variable through its condition tree (``{"var": name}``, the
``vars.<name>`` text form) or a workflow input mapped as ``{"$var": name}``. Works on
a :class:`~culture_rules.model.rule.Rule` or its plain-dict document.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["condition_variable_refs", "rule_variable_refs"]


def condition_variable_refs(tree: Any) -> set[str]:
    """Every name a condition tree reads as ``{"var": name}``, at any depth."""
    found: set[str] = set()
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, Mapping):
            if "literal" in node:
                continue  # a literal is data, never a reference
            name = node.get("var")
            if isinstance(name, str) and len(node) == 1:
                found.add(name)
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return found


def _field(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def rule_variable_refs(rule: Any) -> set[str]:
    """Every variable a rule (model or document) references in its condition or inputs."""
    found = condition_variable_refs(_field(rule, "condition"))
    inputs = _field(_field(rule, "workflow"), "inputs") or {}
    for value in inputs.values() if isinstance(inputs, Mapping) else ():
        if isinstance(value, Mapping) and isinstance(value.get("$var"), str):
            found.add(value["$var"])
    return found
