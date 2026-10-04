"""Generic, stdlib-only (de)serialisation for the frozen model dataclasses.

The wire form is plain JSON with snake_case keys equal to the dataclass field
names. Tuples serialise as arrays; every field is always emitted, so a document
is self-describing. Parsing is *structural* only: it checks JSON shapes against
the type hints and raises :class:`ModelParseError` (with a dotted path) on a
mismatch or an unknown key. A ``null`` (or a missing required field) parses to
``None``; :mod:`culture_rules.model.validate` then reports it as ``required``.
Semantic rules (allowed kinds, loop bounds, placement forms, ...) live in
``validate``, never here.
"""

from __future__ import annotations

import contextvars
import dataclasses
import types
import typing
from functools import cache
from typing import Any, Literal, Union, get_args, get_origin

__all__ = [
    "ModelParseError",
    "field_types",
    "from_dict",
    "is_optional",
    "to_plain",
    "union_members",
]


class ModelParseError(ValueError):
    """A document does not have the JSON shape its model declares."""

    def __init__(self, path: str, message: str, code: str = "type") -> None:
        super().__init__(f"{path or '<root>'}: {message}")
        self.path = path
        self.message = message
        self.code = code


def join(path: str, key: str | int) -> str:
    """Join a dotted/indexed path: ``a`` + ``b`` -> ``a.b``; ``a`` + 0 -> ``a[0]``."""
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else key


@cache
def field_types(cls: type) -> dict[str, Any]:
    """Resolved type hints for a dataclass's fields (cached)."""
    hints = typing.get_type_hints(cls)
    return {f.name: hints[f.name] for f in dataclasses.fields(cls)}


def _union_args(tp: Any) -> tuple[Any, ...] | None:
    if get_origin(tp) in (Union, types.UnionType):
        return get_args(tp)
    return None


def is_optional(tp: Any) -> bool:
    """True when ``None`` is a legal value of ``tp``."""
    args = _union_args(tp)
    return tp is Any or (args is not None and type(None) in args)


def union_members(tp: Any) -> tuple[Any, ...]:
    """The non-None members of a union with two or more of them, else ``()``."""
    args = _union_args(tp)
    rest = tuple(a for a in args if a is not type(None)) if args else ()
    return rest if len(rest) > 1 else ()


def strip_optional(tp: Any) -> Any:
    """``X | None`` -> ``X``; anything else unchanged."""
    args = _union_args(tp)
    if args is None:
        return tp
    rest = [a for a in args if a is not type(None)]
    if len(rest) != 1:  # pragma: no cover - models only use X | None unions
        raise TypeError(f"unsupported union {tp!r}")
    return rest[0]


def to_plain(value: Any) -> Any:
    """Convert a model value to plain JSON-compatible Python (dict/list/scalars)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (tuple, list)):
        return [to_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    return value


def _json_value(value: Any, path: str) -> Any:
    """Deep-copy an arbitrary JSON value, rejecting non-JSON Python objects."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [_json_value(v, join(path, i)) for i, v in enumerate(value)]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ModelParseError(path, "object keys must be strings")
            out[k] = _json_value(v, join(path, k))
        return out
    raise ModelParseError(path, f"not a JSON value: {type(value).__name__}")


def _fail(path: str, expected: str, value: Any) -> ModelParseError:
    return ModelParseError(path, f"expected {expected}, got {type(value).__name__}")


def _type_name(tp: Any) -> str:
    return {str: "string", dict: "object"}.get(get_origin(tp) or tp, "value")


def _scalar(tp: Any, value: Any, path: str) -> Any:
    if tp is str:
        if not isinstance(value, str):
            raise _fail(path, "string", value)
        return value
    if tp is bool:
        if not isinstance(value, bool):
            raise _fail(path, "boolean", value)
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _fail(path, "integer", value)
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _fail(path, "number", value)
        return float(value)
    raise TypeError(f"unsupported field type {tp!r}")  # pragma: no cover


def from_value(tp: Any, value: Any, path: str) -> Any:
    """Parse one JSON value into the Python shape declared by ``tp``."""
    if value is None:
        return None
    if tp is Any:
        return _json_value(value, path)
    members = union_members(tp)
    if members:
        for member in members:
            try:
                return from_value(member, value, path)
            except ModelParseError:
                continue
        raise _fail(path, " or ".join(_type_name(m) for m in members), value)
    tp = strip_optional(tp)
    origin = get_origin(tp)
    if origin is Literal:
        return _scalar(type(get_args(tp)[0]), value, path)
    if origin is tuple:
        if not isinstance(value, list):
            raise _fail(path, "array", value)
        (item,) = (a for a in get_args(tp) if a is not Ellipsis)
        return tuple(from_value(item, v, join(path, i)) for i, v in enumerate(value))
    if origin is dict:
        if not isinstance(value, dict):
            raise _fail(path, "object", value)
        _, item = get_args(tp)
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):  # pragma: no cover - JSON keys are always strings
                raise ModelParseError(path, "object keys must be strings")
            out[k] = from_value(item, v, join(path, k))
        return out
    if dataclasses.is_dataclass(tp):
        return from_dict(tp, value, path)
    return _scalar(tp, value, path)


#: Strict (the default) rejects unknown keys; tolerant reads skip them, so a node can read
#: documents written by a newer minor version (storage obligation o3: readers ignore unknown
#: fields). A ContextVar keeps the mode for one top-level call without threading a parameter.
_STRICT: contextvars.ContextVar[bool] = contextvars.ContextVar("model_serde_strict", default=True)


def from_dict(cls: type, data: Any, path: str = "", *, strict: bool | None = None) -> Any:
    """Build dataclass ``cls`` from a JSON object, defaulting omitted optional fields.

    ``strict=False`` ignores unknown keys at every depth (tolerant read); ``None`` keeps the
    mode of the enclosing call (strict at top level).
    """
    if strict is None:
        return _from_dict(cls, data, path)
    token = _STRICT.set(strict)
    try:
        return _from_dict(cls, data, path)
    finally:
        _STRICT.reset(token)


def _from_dict(cls: type, data: Any, path: str) -> Any:
    if not isinstance(data, dict):
        raise _fail(path, "object", data)
    fields = {f.name: f for f in dataclasses.fields(cls)}
    strict = _STRICT.get()
    for key in data:
        if key not in fields and strict:
            raise ModelParseError(join(path, str(key)), "unknown field", code="unknown_field")
    hints = field_types(cls)
    kwargs: dict[str, Any] = {}
    for name, f in fields.items():
        if name in data:
            kwargs[name] = from_value(hints[name], data[name], join(path, name))
        elif f.default is not dataclasses.MISSING:
            kwargs[name] = f.default
        elif f.default_factory is not dataclasses.MISSING:
            kwargs[name] = f.default_factory()
        else:
            kwargs[name] = None  # missing required field: validate() reports it
    return cls(**kwargs)
