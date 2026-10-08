"""Variable: shared, mutable key-value pairs across the mesh.

A variable holds a JSON scalar or list value.  Each call to
:meth:`~culture_rules.store.port.StoragePort.put_variable` appends a new
version; old versions remain readable (append-only history).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from culture_rules.model.common import Model, doc

__all__ = [
    "VALID_VARIABLE_NAME_RE",
    "Variable",
    "VariableVersion",
    "validate_variable_name",
    "validate_variable_value",
]

#: Regex that every variable name must match.
VALID_VARIABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def validate_variable_name(name: str) -> None:
    """Raise ``ValueError`` if *name* is not a valid variable name."""
    if not isinstance(name, str) or not VALID_VARIABLE_NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid variable name {name!r}: must match {VALID_VARIABLE_NAME_RE.pattern}"
        )


def validate_variable_value(value: Any) -> None:
    """Raise ``ValueError`` if *value* is not a valid JSON scalar or list thereof.

    Valid values: a JSON scalar (str, int, float, bool, None) or a list
    whose every element is a JSON scalar (empty list OK).  Non-finite floats
    (NaN / inf) are rejected because they are not valid JSON.  Nested lists,
    dicts, and other types are rejected.
    """
    _check_scalar(value)


def _check_scalar(value: Any) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (str, int, float)):
        _check_finite(value)
        return
    if isinstance(value, list):
        for item in value:
            _check_item(item)
        return
    raise ValueError(
        f"invalid variable value {type(value).__name__}: must be a JSON scalar or list"
    )


def _check_finite(value: Any) -> None:
    """Refuse a NaN or infinite float: not valid JSON."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError(f"invalid variable value {value!r}: non-finite floats are not valid JSON")


def _check_item(item: Any) -> None:
    """One element of a list value: a JSON scalar (or None), never a list or mapping."""
    if not isinstance(item, (str, int, float, bool)) and item is not None:
        raise ValueError(
            f"invalid variable value {type(item).__name__}: must be a JSON scalar or list"
        )
    _check_finite(item)


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
