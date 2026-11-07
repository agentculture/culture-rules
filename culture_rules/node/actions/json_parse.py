"""Built-in ``json.parse``: JSON text, parsed and checked against a configured shape.

A generic step: it knows no app and no flow. A workflow holds a JSON document as text (a
variable, a step's output, a model's reply) and needs it parsed and validated before later
steps use it; the rules live in the step's ``shape`` (see :mod:`culture_rules.model.shape`:
types, bounds, patterns, enums, defaults, unknown-key refusal, line counts).

Inputs (each may instead sit in ``config``; the input wins):

* ``text`` - the JSON text, a string or bytes (UTF-8, at most :data:`MAX_BYTES`). A value that
  is already a list or an object is taken as decoded and only checked;
* ``shape`` (required, usually in config) - the shape spec the parsed value must fit.

Output ``value``: the parsed value, normalised by the shape (defaults filled, text stripped or
lower-cased where the shape says so, ignored keys left out).

Final (non-retryable) failures, all named ``<code>: <detail>``: ``bad_params`` (no ``shape``,
or a malformed one), ``not_json`` (no text, the wrong type, over the size cap, or not valid
JSON), ``shape_invalid`` (the value does not fit; the detail is the failing field path and
the reason, e.g. ``sections[2].queries: must have at least 1 items``). With
``config.on_error: "skip"`` a failure turns the step ``skipped``, like any step. Standard
library only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.model.shape import ShapeError, ShapeSpecError, validate

__all__ = ["JSON_PARSE_BUILTIN", "JsonParsePort", "MAX_BYTES", "json_parse"]

JSON_PARSE_BUILTIN = "json.parse"
MAX_BYTES = 1024 * 1024


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _decode(text: Any) -> Any:
    if isinstance(text, (list, dict)):
        return text
    if isinstance(text, (bytes, bytearray)):
        if len(text) > MAX_BYTES:
            raise _Refused("not_json", f"the text is over {MAX_BYTES} bytes")
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError:
            raise _Refused("not_json", "the bytes are not UTF-8 text") from None
    if not isinstance(text, str):
        raise _Refused("not_json", "text is missing, or not a string")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise _Refused("not_json", f"the text is over {MAX_BYTES} bytes")
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise _Refused("not_json", f"not valid JSON ({exc})") from None


def json_parse(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """``{"value": ...}`` for the parsed, shape-checked text (module doc); else _Refused."""
    spec = _param(input, config, "shape")
    if not isinstance(spec, Mapping):
        raise _Refused("bad_params", "shape must be a shape spec (an object)")
    value = _decode(_param(input, config, "text"))
    try:
        return {"value": validate(value, dict(spec))}
    except ShapeSpecError as exc:
        raise _Refused("bad_params", str(exc)) from None
    except ShapeError as exc:
        raise _Refused("shape_invalid", str(exc)) from None


class JsonParsePort:
    """Built-in ``json.parse`` (see the module docstring). Pure."""

    supports_idempotency_key = True

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        try:
            return InvocationResult.completed(json_parse(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
