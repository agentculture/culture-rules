"""Transcript result file: the schema-1 document, atomic writes and the per-window work dir.

The result lives at ``<output_dir>/<audio_sha256>/transcript.<model-slug>.json``. While a
transcription runs, each window's reply is kept at
``<output_dir>/<audio_sha256>/.work.<model-slug>/window-NNNN.json`` (written atomically) so a
restart resumes instead of re-asking. The caller validates ``audio_sha256`` before calling here;
:func:`result_path` re-checks it only because it builds a filesystem path from it.

:func:`read_excerpt` reads a finished transcript's text back for a summary (``llm.complete``'s
``content_path``, d2): the path must resolve under a given root, symlinks followed, and the text
is cut to a limit, so the transcript itself never passes through a run's step outputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = 1
TRANSCRIPT_FILE_CAP = 32 << 20  # a transcript file larger than this is not read (excerpt or book)
EXCERPT_FILE_CAP = TRANSCRIPT_FILE_CAP
PATH_REFUSED, INVALID_REQUEST = "path_refused", "invalid_request"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


class BadReply(ValueError):
    """A window reply is neither ``{text}`` nor ``{text, segments[{start, end, text}]}``."""


def model_slug(model: str) -> str:
    """Replace every character outside ``[A-Za-z0-9._-]`` with ``-``."""
    return _SLUG_RE.sub("-", model)


def _dir(output_dir: str, audio_sha256: str) -> str:
    if not _SHA_RE.match(audio_sha256):
        raise ValueError("audio_sha256 must be 64 lowercase hex characters")
    return os.path.join(output_dir, audio_sha256)


def result_path(output_dir: str, audio_sha256: str, model: str) -> str:
    return os.path.join(_dir(output_dir, audio_sha256), f"transcript.{model_slug(model)}.json")


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _segments(start: float, end: float, reply: Any) -> list[dict[str, Any]]:
    if not isinstance(reply, Mapping) or not isinstance(reply.get("text"), str):
        raise BadReply("reply must be an object with a string 'text'")
    segs = reply.get("segments")
    if segs is None:
        return [{"start": start, "end": end, "text": reply["text"]}]
    if not isinstance(segs, list):
        raise BadReply("'segments' must be a list")
    out: list[dict[str, Any]] = []
    for seg in segs:
        if (
            not isinstance(seg, Mapping)
            or not _is_num(seg.get("start"))
            or not _is_num(seg.get("end"))
            or not isinstance(seg.get("text"), str)
        ):
            raise BadReply("each segment needs numeric start and end and string text")
        out.append({"start": start + seg["start"], "end": start + seg["end"], "text": seg["text"]})
    return out


def build_result(
    audio_sha256: str,
    model: str,
    language: str | None,
    duration: float,
    windows: Sequence[tuple[float, float, Any]],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the schema-1 document from ``(start, end, reply)`` windows."""
    segments: list[dict[str, Any]] = []
    empty = 0
    for start, end, reply in windows:
        segments.extend(_segments(start, end, reply))
        if isinstance(reply, Mapping) and reply.get("text") == "":
            empty += 1
    return {
        "schema": SCHEMA,
        "audio_sha256": audio_sha256,
        "model": model,
        "language": language,
        "duration": duration,
        "segments": segments,
        "created_at": now or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_count": len(windows),
        "empty_window_count": empty,
    }


def write_atomic(path: str, doc: Any) -> str:
    """Write ``doc`` as JSON via a temp file and ``os.replace``; return the bytes' sha256."""
    data = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False, suffix=".tmp") as tmp:
            tmp_name = tmp.name
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None and os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return hashlib.sha256(data).hexdigest()


def read_existing(path: str) -> Any | None:
    """Return the parsed document at ``path``, or None when absent or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class WorkDir:
    """Per-window replies kept on disk so an interrupted run resumes."""

    def __init__(self, output_dir: str, audio_sha256: str, model: str) -> None:
        self.path = os.path.join(_dir(output_dir, audio_sha256), f".work.{model_slug(model)}")

    def _file(self, index: int) -> str:
        return os.path.join(self.path, f"window-{index:04d}.json")

    def get(self, index: int) -> Any | None:
        return read_existing(self._file(index))

    def put(self, index: int, reply: Any) -> None:
        write_atomic(self._file(index), reply)

    def clear(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class ExcerptRefused(ValueError):
    """:func:`read_excerpt` refused: ``code`` is ``path_refused`` or ``invalid_request``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _under(path_value: Any, root_value: Any) -> Path:
    if not isinstance(root_value, str) or not os.path.isabs(root_value):
        raise ExcerptRefused(PATH_REFUSED, "the root must be an absolute path")
    if not isinstance(path_value, str) or not os.path.isabs(path_value):
        raise ExcerptRefused(PATH_REFUSED, "the transcript path must be an absolute path")
    try:
        root = Path(root_value).resolve(strict=True)
        path = Path(path_value).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ExcerptRefused(PATH_REFUSED, "the transcript or its root does not exist") from exc
    if not path.is_relative_to(root) or not path.is_file():
        raise ExcerptRefused(PATH_REFUSED, "the transcript is not a file under the root")
    return path


def read_excerpt(path_value: Any, root_value: Any, limit: int) -> str:
    """The non-empty segment texts of the schema-1 transcript at ``path_value``, one per line,
    cut to ``limit`` characters. The path must resolve under ``root_value``."""
    path = _under(path_value, root_value)
    try:
        if path.stat().st_size > EXCERPT_FILE_CAP:
            raise ExcerptRefused(INVALID_REQUEST, "the transcript file is too large")
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ExcerptRefused(INVALID_REQUEST, "the transcript is not readable JSON") from exc
    segments = doc.get("segments") if isinstance(doc, Mapping) else None
    if (
        not isinstance(doc, Mapping)
        or doc.get("schema") != SCHEMA
        or not isinstance(segments, list)
    ):
        raise ExcerptRefused(INVALID_REQUEST, "not a schema-1 transcript")
    texts = (s.get("text") for s in segments if isinstance(s, Mapping))
    lines = [t.strip() for t in texts if isinstance(t, str) and t.strip()]
    return "\n".join(lines)[:limit]
