"""Built-in ``file.read``: a local file, read only under configured roots.

A generic step: it knows no app and no flow. It reads one file and returns its text, or a
parsed JSON value (or named fields of it), so later steps can use the content.

Config (the roots can only come from here, never from an input, so a run cannot widen them):

* ``roots`` - a list of absolute directories, or ``root`` - one (required). The file must
  resolve under ANY one of them (this differs from ``transcript.render``, which needs all of
  its roots). Roots and path are resolved with symlinks followed before the check, so a link
  inside a root that points outside is refused;
* ``mode`` - ``text`` (the default; UTF-8, undecodable refused) or ``json``;
* ``fields`` - json mode only: ``{output name: dotted path}`` as ``json.read`` takes (object
  keys only; a missing field is null; any JSON value may be returned);
* ``max_bytes`` - the size cap, default 1 MiB and at most :data:`HARD_MAX_BYTES` (8 MiB),
  checked from ``stat`` before anything is read.

Inputs (each may instead sit in ``config``; the input wins): ``path`` - the file, absolute or
relative to the first root.

Outputs: ``bytes`` (the size) and, text mode, ``content``; json mode, ``value`` (the whole
document) or, with ``fields``, ``fields`` (the extracted mapping). Nothing is output for a
refused file.

Final (non-retryable) failures, ``<code>: <detail>``: ``bad_params``, ``path_refused``
(missing, outside every root, not a regular file), ``too_large``, ``not_text``, ``not_json``.
Standard library only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions._paths import PathProblem, resolve_under
from culture_rules.node.actions.json_read import _fields, _param, _Refused, _walk

__all__ = ["DEFAULT_MAX_BYTES", "FILE_READ_BUILTIN", "HARD_MAX_BYTES", "FileReadPort", "file_read"]

FILE_READ_BUILTIN = "file.read"
DEFAULT_MAX_BYTES = 1024 * 1024
HARD_MAX_BYTES = 8 * 1024 * 1024


def _roots(config: Mapping[str, Any]) -> list[Any]:
    roots = config.get("roots")
    if roots is None:
        roots = [config.get("root")]
    if not isinstance(roots, list) or not roots:
        raise _Refused("bad_params", "roots must be a non-empty list of absolute directories")
    return roots


def _cap(config: Mapping[str, Any]) -> int:
    cap = config.get("max_bytes", DEFAULT_MAX_BYTES)
    if isinstance(cap, bool) or not isinstance(cap, int) or not 0 < cap <= HARD_MAX_BYTES:
        raise _Refused("bad_params", f"max_bytes must be an integer from 1 to {HARD_MAX_BYTES}")
    return cap


def _read(path: Path, cap: int) -> bytes:
    try:
        if path.stat().st_size > cap:
            raise _Refused("too_large", f"the file is over {cap} bytes")
        return path.read_bytes()
    except OSError as exc:
        raise _Refused("path_refused", "the file cannot be read") from exc


def file_read(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The file's content (module doc), else :class:`_Refused`."""
    mode = config.get("mode", "text")
    if mode not in ("text", "json"):
        raise _Refused("bad_params", "mode must be text or json")
    raw_fields = config.get("fields")
    if raw_fields is not None and mode != "json":
        raise _Refused("bad_params", "fields needs mode json")
    fields = _fields(raw_fields) if raw_fields is not None else None
    cap = _cap(config)
    roots = _roots(config)
    try:
        path = resolve_under(_param(input, config, "path"), roots)
    except PathProblem as exc:
        raise _Refused(exc.code, exc.message) from exc
    raw = _read(path, cap)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Refused("not_text", "the file is not UTF-8 text") from exc
    if mode == "text":
        return {"content": text, "bytes": len(raw)}
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise _Refused("not_json", "the file is not readable JSON") from exc
    if fields is None:
        return {"value": doc, "bytes": len(raw)}
    return {
        "fields": {name: _walk(doc, parts) for name, parts in fields.items()},
        "bytes": len(raw),
    }


class FileReadPort:
    """Built-in ``file.read`` (see the module docstring). Reads one local file."""

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
            return InvocationResult.completed(file_read(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
