"""The recording brief's answer: ``{summary, word, facts, action_items}`` as JSON.

:func:`parse_recording_answer` is the one validator for what ``llm.complete`` with
``brief: "recording"`` returns; the answer cache, ``transcript.render`` and the checklist steps
all read the parsed answer, never the model's raw text. Anything that does not qualify is
refused with a code (:class:`AnswerRefused`), never repaired or truncated:

* ``answer_not_json`` - the reply is not a JSON object (a leading ``<think>`` block and one
  markdown fence are dropped first);
* ``answer_invalid`` - a field is missing or has the wrong type or size: ``summary`` is one or
  two non-empty lines in a string, ``word`` is 2-24 Latin letters and hyphens (kept lower case),
  ``facts`` and ``action_items`` are lists of at most :data:`MAX_ITEMS` non-empty strings of at
  most :data:`MAX_ITEM_CHARS` characters (empty lists are fine: never invent).

Standard-library only.
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Mapping
from typing import Any

__all__ = [
    "MAX_ITEMS",
    "MAX_ITEM_CHARS",
    "NOT_JSON",
    "INVALID",
    "AnswerRefused",
    "parse_recording_answer",
]

NOT_JSON, INVALID = "answer_not_json", "answer_invalid"
MAX_ITEMS = 30
MAX_ITEM_CHARS = 300
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z-]{1,23}$")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE = "```"


def _unfenced(text: str) -> str:
    """The body of a reply wrapped in one Markdown code fence (its language word dropped)."""
    if len(text) < 2 * len(_FENCE) or not (text.startswith(_FENCE) and text.endswith(_FENCE)):
        return text
    return text[len(_FENCE) : -len(_FENCE)].lstrip(string.ascii_letters).strip()


class AnswerRefused(ValueError):
    """The reply is not a valid recording answer; ``code`` says why."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _items(data: Mapping[str, Any], name: str) -> list[str]:
    value = data.get(name)
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise AnswerRefused(INVALID, f"{name} must be a list of at most {MAX_ITEMS} strings")
    out = []
    for item in value:
        text = item.strip() if isinstance(item, str) else ""
        if not text or len(text) > MAX_ITEM_CHARS:
            raise AnswerRefused(
                INVALID, f"each of {name} must be a non-empty string of at most {MAX_ITEM_CHARS}"
            )
        out.append(text)
    return out


def parse_recording_answer(value: Any) -> dict[str, Any]:
    """The validated ``{summary: [lines], word, facts, action_items}`` from a reply.

    ``value`` is the reply text, or a mapping with a ``text`` field (an ``llm.complete``
    output). ``summary`` comes back as its one or two stripped lines. Raises
    :class:`AnswerRefused` otherwise."""
    if isinstance(value, Mapping):
        value = value.get("text")
    if not isinstance(value, str):
        raise AnswerRefused(NOT_JSON, "the reply has no text")
    text = _THINK_RE.sub("", value).strip()
    text = _unfenced(text)
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise AnswerRefused(NOT_JSON, "the reply is not JSON") from exc
    if not isinstance(data, dict):
        raise AnswerRefused(NOT_JSON, "the reply is not a JSON object")
    summary, word = data.get("summary"), data.get("word")
    if not isinstance(summary, str) or not isinstance(word, str):
        raise AnswerRefused(INVALID, "summary and word must be strings")
    lines = [ln.strip() for ln in summary.splitlines() if ln.strip()]
    if not 1 <= len(lines) <= 2 or any(len(ln) > MAX_ITEM_CHARS for ln in lines):
        raise AnswerRefused(INVALID, "summary must be one or two short lines")
    word = word.strip()
    if not _WORD_RE.match(word):
        raise AnswerRefused(INVALID, "word must be one topic word in Latin letters")
    return {
        "summary": lines,
        "word": word.lower(),
        "facts": _items(data, "facts"),
        "action_items": _items(data, "action_items"),
    }
