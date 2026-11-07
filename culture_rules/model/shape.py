"""A generic, declarative shape validator for small structured values.

:func:`validate` checks a value against a *spec* and returns the normalised value, or raises
:class:`ShapeError` naming the exact field path (``facts[3]``, ``a.b``, ``[1].n``; ``""`` for the
root). The spec is plain JSON-able data (dicts, lists, strings, numbers, booleans), so it can
live in step configuration. Nothing here names a flow: callers bring the spec.

A spec is an object with a ``type`` and the keys that type takes. Every type also takes
``required`` (bool; a missing object field is refused), ``default`` (used when the value is
missing or ``None``; it is validated against its own spec) and ``nullable`` (bool; a ``None``
that no default replaced is kept as ``None`` instead of being refused, so ``default: null``
with ``nullable: true`` makes a field optional with no value).

========  ==============================================================================
type      keys
========  ==============================================================================
text      ``strip``, ``lower`` (bool, off unless set), ``min_chars``, ``max_chars``,
          ``pattern`` (searched; screened by ``regex_safety``), ``forbid`` (a list of
          patterns, none of which may be found; each screened too), ``enum``
word      as ``text``, and the result must be one token with no whitespace
lines     text split on line breaks, each line stripped, empty lines dropped; ``min`` and
          ``max`` count lines; ``lower``, ``min_chars``, ``max_chars``, ``pattern``, ``forbid``
          apply per line; the result is a list of strings
int       ``min``, ``max`` (values), ``enum``; a bool or a float is not an int
bool      (none)
list      ``items`` (a spec for each item; absent = any value), ``min_items``, ``max_items``
object    ``fields`` (name to spec); ``extra``: ``"refuse"`` (the default) refuses a key not
          in ``fields``, ``"drop"`` leaves it out of the result; ``ignore`` (a list of key
          names) lets those keys be present but leaves them out, even under ``"refuse"``
========  ==============================================================================

Normalisation happens only when the spec asks for it (``strip``, ``lower``, the line split of
``lines``); the input is never mutated. A bad spec raises :class:`ShapeSpecError` (also a
``ValueError``) before any value is looked at. Standard library only.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from culture_rules.model.regex_safety import unsafe_reason

__all__ = ["ShapeError", "ShapeSpecError", "validate"]

_COMMON = frozenset({"type", "required", "default", "nullable"})
_TEXT_KEYS = frozenset({"strip", "lower", "min_chars", "max_chars", "pattern", "forbid", "enum"})
_KEYS: dict[str, frozenset[str]] = {
    "text": _COMMON | _TEXT_KEYS,
    "word": _COMMON | _TEXT_KEYS,
    "lines": _COMMON | {"lower", "min_chars", "max_chars", "pattern", "forbid", "min", "max"},
    "int": _COMMON | {"min", "max", "enum"},
    "bool": _COMMON,
    "list": _COMMON | {"items", "min_items", "max_items"},
    "object": _COMMON | {"fields", "extra", "ignore"},
}
_FLAGS = ("required", "strip", "lower", "nullable")
_BOUNDS = ("min_chars", "max_chars", "min_items", "max_items")
_PAIRS = (("min", "max"), ("min_chars", "max_chars"), ("min_items", "max_items"))
_EXTRA = ("refuse", "drop")


class ShapeError(ValueError):
    """A value does not fit its spec: ``path`` is the field path, ``reason`` says why."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}" if path else reason)
        self.path = path
        self.reason = reason


class ShapeSpecError(ValueError):
    """The spec itself is malformed (the message starts with the spec path)."""


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# -- checking a spec ---------------------------------------------------------------------------


def _check_spec(spec: Any, where: str = "spec") -> None:
    """Raise :class:`ShapeSpecError` unless ``spec`` is well formed (recursively)."""

    def bad(why: str) -> ShapeSpecError:
        return ShapeSpecError(f"{where}: {why}")

    kind = _check_type(spec, bad)
    _check_keys(spec, kind, bad)
    _check_flags(spec, bad)
    _check_numbers(spec, bad)
    _check_options(spec, bad)
    _check_children(spec, kind, where, bad)
    if "default" in spec:
        try:
            _run(copy.deepcopy(spec["default"]), spec, "")
        except ShapeError as exc:
            raise bad(f"default {exc.reason}") from exc


def _check_type(spec: Any, bad: Any) -> str:
    """The spec's ``type``, once it is an object naming a known type."""
    if not isinstance(spec, dict):
        raise bad("must be an object")
    kind = spec.get("type")
    if kind is None:
        raise bad("type is required")
    if kind not in _KEYS:
        raise bad(f"unknown type {kind!r}")
    return kind


def _check_keys(spec: dict[str, Any], kind: str, bad: Any) -> None:
    for key in spec:
        if key in _KEYS[kind]:
            continue
        if key in set().union(*_KEYS.values()):
            raise bad(f"key {key!r} is not valid for type {kind}")
        raise bad(f"unknown key {key!r}")


def _check_flags(spec: dict[str, Any], bad: Any) -> None:
    for key in _FLAGS:
        if key in spec and not isinstance(spec[key], bool):
            raise bad(f"{key} must be true or false")


def _check_numbers(spec: dict[str, Any], bad: Any) -> None:
    for key in _BOUNDS + ("min", "max"):
        if key not in spec:
            continue
        if not _is_int(spec[key]):
            raise bad(f"{key} must be an integer")
        if key in _BOUNDS and spec[key] < 0:
            raise bad(f"{key} must be non-negative")
    for low, high in _PAIRS:
        if low in spec and high in spec and spec[low] > spec[high]:
            raise bad(f"{low} exceeds {high}")


def _check_options(spec: dict[str, Any], bad: Any) -> None:
    if "enum" in spec and not (isinstance(spec["enum"], list) and spec["enum"]):
        raise bad("enum must be a non-empty list")
    if "pattern" in spec:
        _check_pattern(spec["pattern"], bad)
    if "forbid" in spec:
        patterns = spec["forbid"]
        if not (isinstance(patterns, list) and patterns):
            raise bad("forbid must be a non-empty list of patterns")
        for pattern in patterns:
            _check_pattern(pattern, bad)
    if "extra" in spec and spec["extra"] not in _EXTRA:
        raise bad("extra must be refuse or drop")
    if "ignore" in spec and not (
        isinstance(spec["ignore"], list) and all(isinstance(k, str) for k in spec["ignore"])
    ):
        raise bad("ignore must be a list of key names")


def _check_children(spec: dict[str, Any], kind: str, where: str, bad: Any) -> None:
    if kind == "object":
        fields = spec.get("fields")
        if not isinstance(fields, dict):
            raise bad("fields is required and must be an object")
        for name, sub in fields.items():
            _check_spec(sub, _join(f"{where}.fields", str(name)))
    if "items" in spec:
        _check_spec(spec["items"], f"{where}.items")


def _check_pattern(pattern: Any, bad: Any) -> None:
    if not isinstance(pattern, str):
        raise bad("pattern must be text")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise bad(f"pattern is not a valid regular expression ({exc})") from exc
    reason = unsafe_reason(pattern)
    if reason is not None:
        raise bad(f"pattern is unsafe: {reason}")


# -- checking a value --------------------------------------------------------------------------


def _text(value: Any, spec: dict[str, Any], path: str, *, word: bool = False) -> str:
    if not isinstance(value, str):
        raise ShapeError(path, "must be text")
    if spec.get("strip"):
        value = value.strip()
    if spec.get("lower"):
        value = value.lower()
    if word and (not value or any(ch.isspace() for ch in value)):
        raise ShapeError(path, "must be a single word")
    if "min_chars" in spec and len(value) < spec["min_chars"]:
        raise ShapeError(path, f"must be at least {spec['min_chars']} characters")
    if "max_chars" in spec and len(value) > spec["max_chars"]:
        raise ShapeError(path, f"must be at most {spec['max_chars']} characters")
    if "pattern" in spec and not re.search(spec["pattern"], value):
        raise ShapeError(path, "does not match the required pattern")
    for pattern in spec.get("forbid", ()):
        if re.search(pattern, value):
            raise ShapeError(path, "matches a forbidden pattern")
    _enum(value, spec, path)
    return value


def _enum(value: Any, spec: dict[str, Any], path: str) -> None:
    if "enum" in spec and value not in spec["enum"]:
        raise ShapeError(path, "must be one of: " + ", ".join(str(v) for v in spec["enum"]))


def _lines(value: Any, spec: dict[str, Any], path: str) -> list[str]:
    if not isinstance(value, str):
        raise ShapeError(path, "must be text")
    lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
    if "min" in spec and len(lines) < spec["min"]:
        raise ShapeError(path, f"must have at least {spec['min']} lines")
    if "max" in spec and len(lines) > spec["max"]:
        raise ShapeError(path, f"must have at most {spec['max']} lines")
    line_spec = {
        k: v
        for k, v in spec.items()
        if k in ("lower", "min_chars", "max_chars", "pattern", "forbid")
    }
    return [_text(ln, line_spec, f"{path}[{i}]") for i, ln in enumerate(lines)]


def _int(value: Any, spec: dict[str, Any], path: str) -> int:
    if not _is_int(value):
        raise ShapeError(path, "must be an integer")
    if "min" in spec and value < spec["min"]:
        raise ShapeError(path, f"must be at least {spec['min']}")
    if "max" in spec and value > spec["max"]:
        raise ShapeError(path, f"must be at most {spec['max']}")
    _enum(value, spec, path)
    return value


def _list(value: Any, spec: dict[str, Any], path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ShapeError(path, "must be a list")
    if "min_items" in spec and len(value) < spec["min_items"]:
        raise ShapeError(path, f"must have at least {spec['min_items']} items")
    if "max_items" in spec and len(value) > spec["max_items"]:
        raise ShapeError(path, f"must have at most {spec['max_items']} items")
    item_spec = spec.get("items")
    if item_spec is None:
        return copy.deepcopy(value)
    return [_run(item, item_spec, f"{path}[{i}]") for i, item in enumerate(value)]


def _object(value: Any, spec: dict[str, Any], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ShapeError(path, "must be an object")
    fields = spec["fields"]
    ignored = spec.get("ignore", ())
    if spec.get("extra", "refuse") == "refuse":
        for key in value:
            if key not in fields and key not in ignored:
                raise ShapeError(_join(path, str(key)), "is not allowed")
    out: dict[str, Any] = {}
    for name, sub in fields.items():
        here = _join(path, name)
        if name in value:
            out[name] = _run(value[name], sub, here)
        elif "default" in sub:
            out[name] = _run(copy.deepcopy(sub["default"]), sub, here)
        elif sub.get("required"):
            raise ShapeError(here, "is required")
    return out


def _run(value: Any, spec: dict[str, Any], path: str) -> Any:
    kind = spec["type"]
    if value is None and "default" in spec:
        value = copy.deepcopy(spec["default"])
    if value is None and spec.get("nullable"):
        return None
    if kind == "text":
        return _text(value, spec, path)
    if kind == "word":
        return _text(value, spec, path, word=True)
    if kind == "lines":
        return _lines(value, spec, path)
    if kind == "int":
        return _int(value, spec, path)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ShapeError(path, "must be true or false")
        return value
    if kind == "list":
        return _list(value, spec, path)
    return _object(value, spec, path)


def validate(value: Any, spec: dict[str, Any]) -> Any:
    """The normalised ``value`` under ``spec``; raises :class:`ShapeError` with the field path.

    Raises :class:`ShapeSpecError` when ``spec`` itself is malformed."""
    _check_spec(spec)
    return _run(value, spec, "")
