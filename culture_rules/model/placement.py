"""Placement: where a step (or later, a rule) executes — exactly one of three forms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from culture_rules.model.common import Model, doc

__all__ = ["PLACEMENT_FORMS", "Placement"]

PLACEMENT_FORMS: tuple[str, ...] = ("machine", "actor", "requirement")


@dataclass(frozen=True, kw_only=True)
class Placement(Model):
    """A named machine, a named actor (runs where it lives), or a capability requirement."""

    machine: str | None = doc("Name of an enrolled machine", default=None)
    actor: str | None = doc("Id of an actor; runs on that actor's machine", default=None)
    requirement: tuple[str, ...] | None = doc(
        "Capabilities (e.g. gpu) an eligible enrolled machine must offer", default=None
    )

    __schema_extra__: ClassVar[dict[str, Any]] = {
        "oneOf": [
            {
                "required": ["machine"],
                "properties": {
                    "machine": {"type": "string", "minLength": 1},
                    "actor": {"type": "null"},
                    "requirement": {"type": "null"},
                },
            },
            {
                "required": ["actor"],
                "properties": {
                    "machine": {"type": "null"},
                    "actor": {"type": "string", "minLength": 1},
                    "requirement": {"type": "null"},
                },
            },
            {
                "required": ["requirement"],
                "properties": {
                    "machine": {"type": "null"},
                    "actor": {"type": "null"},
                    "requirement": {"type": "array", "minItems": 1},
                },
            },
        ]
    }

    @property
    def form(self) -> str | None:
        """The single populated form, or ``None`` when not exactly one is set."""
        set_forms = [name for name in PLACEMENT_FORMS if getattr(self, name) not in (None, "", ())]
        return set_forms[0] if len(set_forms) == 1 else None
