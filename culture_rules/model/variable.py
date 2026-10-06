"""Variable: shared, mutable key-value pairs across the mesh.

A variable holds a JSON scalar or list value.  Each call to
:meth:`~culture_rules.store.port.StoragePort.put_variable` appends a new
version; old versions remain readable (append-only history).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from culture_rules.model.common import Model, doc

__all__ = ["VALID_VARIABLE_NAME_RE", "Variable", "VariableVersion"]

#: Regex that every variable name must match.
VALID_VARIABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, kw_only=True)
class Variable(Model):
    """A variable value (latest state)."""

    name: str = doc("Variable name (lowercase, underscore, 1-64 chars)")
    value: Any = doc("JSON scalar or list value")
    version: int = doc("Monotonically increasing version number (1-based)")
    updated_by: str = doc("Actor or service that performed the write")
    updated_at: str = doc("ISO-8601 UTC timestamp of the latest write")
    description: str | None = doc("Free text", default=None)


@dataclass(frozen=True, kw_only=True)
class VariableVersion(Model):
    """One version record inside an append-only version list."""

    version: int = doc("Version number")
    value: Any = doc("JSON scalar or list value")
    updated_by: str = doc("Actor or service that performed the write")
    updated_at: str = doc("ISO-8601 UTC timestamp")
    description: str | None = doc("Free text", default=None)
