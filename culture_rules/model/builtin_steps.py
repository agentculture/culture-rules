"""The builtin catalogue: the built-in ``code`` steps the extension registry holds, as data.

``GET /steps/builtins`` serves :func:`describe_builtins`, and the editor's step panel builds
its Built-in picker from it (it keeps no table of its own): each builtin's name, the label a
picker shows and the typed ports an editor pre-fills, all declared where the builtin is
registered (``ext.builtin(..., label=..., inputs=..., outputs=...)``), so a builtin an
extension adds shows up with no editor change. Standard library only.
"""

from __future__ import annotations

from typing import Any

from culture_rules.extensions import registry

__all__ = ["describe_builtins"]


def describe_builtins() -> list[dict[str, Any]]:
    """The registered builtins as plain data, in registration order.

    Each item: ``name``, ``label`` (the registered label, else its describe phrase, else the
    name), ``inputs`` and ``outputs`` (``{name, type, required}`` each, in order; empty when the
    builtin declares none, and an editor then leaves the step's ports as they are).
    """
    return [
        {
            "name": entry.name,
            "label": entry.shown_label,
            "inputs": [p.to_dict() for p in entry.inputs],
            "outputs": [p.to_dict() for p in entry.outputs],
        }
        for entry in registry().builtins()
    ]
