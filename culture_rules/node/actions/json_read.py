"""Built-in ``json.read``: named scalar fields from a JSON file under a root (#gl t5).

A workflow reads e.g. ``language`` and ``recording_at`` from a pocketsync ``meta.json`` whose
path comes from an event field, so later steps can use them.

Inputs (each may instead sit in ``config``; the input wins): ``path`` - the file, absolute or
relative to ``root``; ``root`` - an absolute directory the file must resolve under (symlinks
followed, so a link pointing out of it is refused); ``fields`` (required, usually in
config) - ``{output name: dotted path}``, e.g. ``{"audio": "files.audio.rel_path"}``; none
given fails ``bad_params``. A dotted path walks object keys
only; a list index is not supported (a list on the way yields null, a list at the end is
refused as ``not_scalar``).

Outputs: one per field name, a string, number, bool or ``None`` (null for a missing field). A
list or object at a requested path fails ``not_scalar`` naming the field. Final (non-retryable)
failures, all before anything is parsed beyond what is needed: ``bad_params``,
``path_refused`` (outside the root, missing, not a regular file), ``too_large`` (over 1 MiB,
checked before reading), ``not_json``, ``not_scalar``. Standard library only.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = ["JSON_READ_BUILTIN", "JsonReadPort", "MAX_BYTES"]

JSON_READ_BUILTIN = "json.read"
MAX_BYTES = 1024 * 1024
_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_BAD_PARAMS, _PATH_REFUSED = "bad_params", "path_refused"


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _fields(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or not value:
        raise _Refused(_BAD_PARAMS, "fields must map each output name to a dotted path")
    out: dict[str, list[str]] = {}
    for name, dotted in value.items():
        if not (isinstance(name, str) and _NAME.fullmatch(name)):
            raise _Refused(_BAD_PARAMS, f"field name {name!r} is not a short identifier")
        parts = dotted.split(".") if isinstance(dotted, str) else []
        if not parts or not all(parts):
            raise _Refused(_BAD_PARAMS, f"field {name!r} needs a dotted path")
        out[name] = parts
    return out


def _resolve(path_value: Any, root_value: Any) -> Path:
    if not isinstance(root_value, str) or not os.path.isabs(root_value):
        raise _Refused(_BAD_PARAMS, "root must be an absolute directory")
    if not isinstance(path_value, str) or not path_value:
        raise _Refused(_BAD_PARAMS, "path must be a non-empty string")
    try:
        root = Path(root_value).resolve(strict=True)
        path = (root / path_value).resolve(strict=True)  # an absolute path_value wins
    except (OSError, RuntimeError) as exc:
        raise _Refused(_PATH_REFUSED, "the file or its root does not exist") from exc
    if not path.is_relative_to(root):
        raise _Refused(_PATH_REFUSED, "the file is not under the root")
    if not path.is_file():
        raise _Refused(_PATH_REFUSED, "the path is not a file")
    return path


def _load(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_BYTES:
            raise _Refused("too_large", f"the file is over {MAX_BYTES} bytes")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise _Refused("not_json", "the file is not readable JSON") from exc


def _walk(doc: Any, parts: list[str]) -> Any:
    node = doc
    for part in parts:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def json_read(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The named fields of the JSON file (module doc), else :class:`_Refused`."""
    fields = _fields(_param(input, config, "fields"))
    path = _resolve(_param(input, config, "path"), _param(input, config, "root"))
    doc = _load(path)
    out: dict[str, Any] = {}
    for name, parts in fields.items():
        value = _walk(doc, parts)
        if isinstance(value, (list, dict)):
            raise _Refused("not_scalar", f"field {name!r} is a list or object, not a scalar")
        out[name] = value
    return out


class JsonReadPort:
    """Built-in ``json.read`` (see the module docstring). Reads one local file."""

    supports_idempotency_key = True

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        try:
            return InvocationResult.completed(json_read(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
