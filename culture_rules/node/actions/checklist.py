"""The checklist builtins (library items): a to-do list kept in the store.

Four built-in code ports over :data:`CHECKLISTS_COLLECTION`, one document per list. Standard
library only; every output is a scalar or a plain value. Each step input is read from the
step's inputs first, then from ``config`` (so a workflow can pin ``prefix`` or ``list_key``).

The document (id = the list key)
================================

* ``list_key`` - the key again; ``title`` - optional, rendered as the H1;
* ``rev`` - the compare-and-set counter: every write is ``store.update_if`` on it, retried
  (at most :data:`CAS_TRIES` times) on a conflict, so concurrent steps never overwrite each
  other (c27);
* ``revision`` - the content revision: 1 when the list is created, +1 on every change to
  what :func:`render_markdown` writes (an item added, a title changed, a flip that changes a
  rendered line). ``checklist.render`` returns it and ``checklist.bind`` records it;
* ``prefix`` - the id prefix, fixed when the list is created (a later ``prefix`` is ignored,
  so ids never change); ``next_index`` - the next unused item index; it never decreases, so
  an index is never reused, even after an item disappears;
* ``items`` - ``[{id, text, norm, done, at, by}]`` in creation order: ``id`` is
  ``<prefix>.<list key>.<n>``, ``norm`` is :func:`normalize` of ``text``, ``at`` the epoch
  seconds of the last applied flip (None before any) and ``by`` who made it;
* ``item_id`` / ``pushed_revision`` - the library item the list was last pushed as, and the
  revision that push carried (both None before the first bind).

The builtins
============

* ``checklist.merge`` - inputs ``list_key``, ``prefix``, ``items`` (a list of strings),
  optional ``title``. An item whose normalized text matches a stored item's ``norm`` keeps
  that item's id, done mark, ``at``, ``by`` *and its original text* (they differ only by
  case, whitespace or end punctuation, so the reader's line does not churn); a new one gets
  the next index. A stored item not extracted again is kept (c16/c29: a done mark is never
  lost). Duplicates within one extraction and items that normalize to nothing are ignored.
  Empty ``items`` with no stored list writes nothing. Output ``{list_key, revision, added,
  kept, ignored, total, empty}`` (``kept`` = stored items matched again; ``total`` = items
  in the list now; ``empty`` = the list has no items; ``revision`` 0 when no list exists).
* ``checklist.set`` - inputs ``list_key``, ``id``, ``done`` (bool), ``at`` (finite epoch
  seconds), ``by`` (non-empty; whitespace becomes ``_``, ``<`` and ``>`` are dropped). Applied
  only when ``at`` is newer than the item's last applied ``at``. Output ``{list_key, id,
  applied, reason, done, revision}``; ``reason`` is None when applied, else ``unknown_list``,
  ``unknown_id`` or ``stale`` (all completed steps: a flip for a gone list is not an error).
* ``checklist.render`` - input ``list_key``. Output ``{list_key, markdown, content_base64,
  revision, empty, item_count, done_count, title}``: the title as ``# title`` and a blank
  line, then one line per item, ``- [ ] text <!-- todo:<id> -->`` or ``- [x] text <!--
  todo:<id> done_at=<ISO 8601 UTC, Z> by=<by> -->``. ``content_base64`` is the markdown in
  UTF-8, for ``library.push``. Item text cannot break its line or comment: whitespace
  (newlines included) collapses to one space, ``<!--`` and ``-->`` are escaped. A missing
  list renders as ``""`` with revision 0; ``empty`` is true whenever there are no items.
* ``checklist.bind`` - inputs ``list_key``, ``item_id`` (the library item: a positive int, as
  ``library.push`` returns it, or a string; kept as given), ``revision`` (the
  rendered revision that was pushed). Stores both only when ``revision`` is newer than the
  stored ``pushed_revision`` (re-binding the same item at the same revision answers bound,
  without a write). Output ``{bound, stale, pushed_revision, item_id, revision, reason}``:
  ``pushed_revision``/``item_id`` as stored afterwards, ``revision`` the list's current
  one, ``stale`` true when that is ahead of ``pushed_revision`` (render, push and bind
  again, c28), ``reason`` None, ``not_newer`` or ``unknown_list``. A revision ahead of the
  list's own is refused (``bad_input``).

Bad input fails the step non-retryably (``bad_input: ...``); a list still contended after
:data:`CAS_TRIES` tries fails it retryably (``checklist_contended``).
"""

from __future__ import annotations

import base64
import math
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = [
    "BIND_BUILTIN",
    "CAS_TRIES",
    "CHECKLISTS_COLLECTION",
    "MERGE_BUILTIN",
    "RENDER_BUILTIN",
    "SET_BUILTIN",
    "ChecklistBindPort",
    "ChecklistMergePort",
    "ChecklistRenderPort",
    "ChecklistSetPort",
    "normalize",
    "render_markdown",
]

CHECKLISTS_COLLECTION = "checklists"
MERGE_BUILTIN = "checklist.merge"
SET_BUILTIN = "checklist.set"
RENDER_BUILTIN = "checklist.render"
BIND_BUILTIN = "checklist.bind"
CAS_TRIES = 8
MAX_ITEMS = 500
MAX_TEXT = 500
_MAX_BY = 100
_MAX_ID = 200
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:-]{0,99}")
_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,39}")
_SPACE = re.compile(r"\s+")
_REV, _REVISION, _ITEMS, _NEXT = "rev", "revision", "items", "next_index"
_LIST_KEY, _PREFIX_F, _TITLE = "list_key", "prefix", "title"
_ITEM_ID, _PUSHED = "item_id", "pushed_revision"
_UNKNOWN_LIST, _UNKNOWN_ID, _STALE, _NOT_NEWER = (
    "unknown_list",
    "unknown_id",
    "stale",
    "not_newer",
)


class _Refusal(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.result = InvocationResult.failed(message, retryable=retryable)


def _bad(message: str) -> _Refusal:
    return _Refusal(f"bad_input: {message}")


def _end_char(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith("P")


def normalize(text: str) -> str:
    """The match key of an item's text: casefolded, whitespace collapsed, punctuation and
    space stripped at both ends."""
    folded = _SPACE.sub(" ", text.casefold())
    start, end = 0, len(folded)
    while start < end and _end_char(folded[start]):
        start += 1
    while end > start and _end_char(folded[end - 1]):
        end -= 1
    return folded[start:end]


def _clean_text(text: str) -> str:
    return _SPACE.sub(" ", text).strip()[:MAX_TEXT]


def _safe(text: str) -> str:
    """Item text that cannot end its line or open/close an HTML comment."""
    return _SPACE.sub(" ", text).replace("<!--", "&lt;!--").replace("-->", "--&gt;").strip()


def _iso(at: float) -> str:
    stamp = datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds")
    return stamp.replace("+00:00", "Z")


def _line(item: Mapping[str, Any]) -> str:
    text, item_id = _safe(str(item.get("text", ""))), item.get("id")
    if item.get("done"):
        at = item.get("at")
        when = f" done_at={_iso(at)}" if isinstance(at, (int, float)) else ""
        by = f" by={item['by']}" if item.get("by") else ""
        return f"- [x] {text} <!-- todo:{item_id}{when}{by} -->"
    return f"- [ ] {text} <!-- todo:{item_id} -->"


def render_markdown(doc: Mapping[str, Any] | None) -> str:
    """The list as markdown (module doc), ``""`` for a missing list."""
    if doc is None:
        return ""
    lines: list[str] = []
    title = doc.get(_TITLE)
    if isinstance(title, str) and title.strip():
        lines += [f"# {_safe(title)}", ""]
    lines += [_line(item) for item in doc.get(_ITEMS) or []]
    return "\n".join(lines) + "\n" if lines else ""


# ------------------------------------------------------------------ input helpers


def _param(input: Mapping[str, Any], config: Mapping[str, Any], name: str) -> Any:
    value = input.get(name)
    return config.get(name) if value is None else value


def _list_key(value: Any) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise _bad("list_key: letters, digits, _ : - (at most 100)")
    return value


def _prefix(value: Any) -> str:
    if not isinstance(value, str) or not _PREFIX.fullmatch(value):
        raise _bad("prefix: a short identifier (letters, digits, _ -)")
    return value


def _items(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise _bad("items: a list of strings")
    if len(value) > MAX_ITEMS:
        raise _bad(f"items: at most {MAX_ITEMS}")
    return list(value)


def _title(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _bad("title: a string")
    return _clean_text(value) or None


def _text_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_ID:
        raise _bad(f"{name}: a non-empty string (at most {_MAX_ID})")
    return value


def _item_ref(value: Any) -> int | str:
    """The library item id: a positive int (library.push's ``item_id``) or a non-empty string,
    kept as given so it maps straight back into a replace."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 1:
            raise _bad(f"{_ITEM_ID}: a positive int or a non-empty string")
        return value
    return _text_id(value, _ITEM_ID)


def _at(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _bad("at: finite epoch seconds")
    return value


def _by(value: Any) -> str:
    if not isinstance(value, str):
        raise _bad("by: a non-empty string")
    cleaned = _SPACE.sub("_", value.strip()).replace("<", "").replace(">", "")[:_MAX_BY]
    if not cleaned:
        raise _bad("by: a non-empty string")
    return cleaned


def _revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _bad("revision: a non-negative integer")
    return value


# ------------------------------------------------------------------ the ports


class _ChecklistPort:
    """Shared store access: get and compare-and-set on ``rev``."""

    supports_idempotency_key = True

    def __init__(self, store: Any) -> None:
        self._store = store

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
            return InvocationResult.completed(self._run(input, config))
        except _Refusal as refusal:
            return refusal.result

    def _run(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _get(self, key: str) -> Mapping[str, Any] | None:
        return self._store.get(CHECKLISTS_COLLECTION, key)

    def _cas(self, key: str, doc: Mapping[str, Any] | None, changes: dict[str, Any]) -> bool:
        rev = (doc or {}).get(_REV)
        changes = {**changes, _LIST_KEY: key, _REV: (rev or 0) + 1}
        result = self._store.update_if(
            CHECKLISTS_COLLECTION, key, {_REV: rev}, changes, upsert=doc is None
        )
        return bool(result.won)

    @staticmethod
    def _contended(key: str) -> _Refusal:
        return _Refusal(f"checklist_contended: {CHECKLISTS_COLLECTION}/{key}", retryable=True)


def _merged(
    doc: Mapping[str, Any] | None, key: str, prefix: str, texts: list[str], title: str | None
) -> tuple[dict[str, Any], dict[str, int]]:
    """The merge's changes (empty when nothing changes) and its counts."""
    items = [dict(i) for i in (doc or {}).get(_ITEMS) or []]
    by_norm = {i.get("norm"): i for i in items}
    prefix = (doc or {}).get(_PREFIX_F) or prefix
    next_index = int((doc or {}).get(_NEXT) or 1)
    added = kept = ignored = 0
    seen: set[str] = set()
    for raw in texts:
        text = _clean_text(raw)
        norm = normalize(text)
        if not norm or norm in seen:
            ignored += 1
            continue
        seen.add(norm)
        if norm in by_norm:
            kept += 1
            continue
        item = {
            "id": f"{prefix}.{key}.{next_index}",
            "text": text,
            "norm": norm,
            "done": False,
            "at": None,
            "by": None,
        }
        items.append(item)
        by_norm[norm] = item
        next_index += 1
        added += 1
    counts = {"added": added, "kept": kept, "ignored": ignored, "total": len(items)}
    retitled = title is not None and title != (doc or {}).get(_TITLE)
    if not added and (not retitled or doc is None):
        return {}, counts
    changes: dict[str, Any] = {
        _ITEMS: items,
        _NEXT: next_index,
        _PREFIX_F: prefix,
        _REVISION: int((doc or {}).get(_REVISION) or 0) + 1,
    }
    if doc is None:
        changes.update({_ITEM_ID: None, _PUSHED: None, _TITLE: title})
    elif retitled:
        changes[_TITLE] = title
    return changes, counts


class ChecklistMergePort(_ChecklistPort):
    """``checklist.merge`` (module doc)."""

    def _run(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        key = _list_key(_param(input, config, _LIST_KEY))
        prefix = _prefix(_param(input, config, _PREFIX_F))
        texts = _items(_param(input, config, _ITEMS))
        title = _title(_param(input, config, _TITLE))
        for _ in range(CAS_TRIES):
            doc = self._get(key)
            changes, counts = _merged(doc, key, prefix, texts, title)
            if not changes:
                revision = int((doc or {}).get(_REVISION) or 0)
                return {_LIST_KEY: key, _REVISION: revision, **counts, "empty": not counts["total"]}
            if self._cas(key, doc, changes):
                return {
                    _LIST_KEY: key,
                    _REVISION: changes[_REVISION],
                    **counts,
                    "empty": not counts["total"],
                }
        raise self._contended(key)


def _renders_differently(item: Mapping[str, Any], done: bool, at: float, by: str) -> bool:
    if bool(item.get("done")) != done:
        return True
    return done and (item.get("at") != at or item.get("by") != by)


class ChecklistSetPort(_ChecklistPort):
    """``checklist.set`` (module doc): one flip, newest ``at`` wins, never lost (c27)."""

    def _run(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        key = _list_key(_param(input, config, _LIST_KEY))
        item_id = _text_id(input.get("id"), "id")
        done = input.get("done")
        if not isinstance(done, bool):
            raise _bad("done: true or false")
        at, by = _at(input.get("at")), _by(input.get("by"))
        for _ in range(CAS_TRIES):
            doc = self._get(key)
            answer = {_LIST_KEY: key, "id": item_id, "done": done}
            if doc is None:
                return {**answer, "applied": False, "reason": _UNKNOWN_LIST, _REVISION: 0}
            revision = int(doc.get(_REVISION) or 0)
            items = [dict(i) for i in doc.get(_ITEMS) or []]
            item = next((i for i in items if i.get("id") == item_id), None)
            if item is None:
                return {**answer, "applied": False, "reason": _UNKNOWN_ID, _REVISION: revision}
            last = item.get("at")
            if isinstance(last, (int, float)) and at <= last:
                return {**answer, "applied": False, "reason": _STALE, _REVISION: revision}
            if _renders_differently(item, done, at, by):
                revision += 1
            item.update({"done": done, "at": at, "by": by})
            if self._cas(key, doc, {_ITEMS: items, _REVISION: revision}):
                return {**answer, "applied": True, "reason": None, _REVISION: revision}
        raise self._contended(key)


class ChecklistRenderPort(_ChecklistPort):
    """``checklist.render`` (module doc). Read only."""

    def _run(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        key = _list_key(_param(input, config, _LIST_KEY))
        doc = self._get(key)
        items = (doc or {}).get(_ITEMS) or []
        markdown = render_markdown(doc)
        return {
            _LIST_KEY: key,
            "markdown": markdown,
            "content_base64": base64.b64encode(markdown.encode("utf-8")).decode("ascii"),
            _REVISION: int((doc or {}).get(_REVISION) or 0),
            "empty": not items,
            "item_count": len(items),
            "done_count": sum(1 for i in items if i.get("done")),
            _TITLE: (doc or {}).get(_TITLE),
        }


class ChecklistBindPort(_ChecklistPort):
    """``checklist.bind`` (module doc): record a push only if newer; say when stale (c28)."""

    def _run(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        key = _list_key(_param(input, config, _LIST_KEY))
        item_id = _item_ref(input.get(_ITEM_ID))
        pushed = _revision(input.get(_REVISION))
        for _ in range(CAS_TRIES):
            doc = self._get(key)
            if doc is None:
                return _bound(False, None, None, 0, _UNKNOWN_LIST)
            current = int(doc.get(_REVISION) or 0)
            if pushed > current:
                raise _bad(f"revision: {pushed} is ahead of the list's revision {current}")
            stored, stored_item = doc.get(_PUSHED), doc.get(_ITEM_ID)
            if isinstance(stored, int) and pushed <= stored:
                same = pushed == stored and item_id == stored_item
                return _bound(same, stored, stored_item, current, None if same else _NOT_NEWER)
            if self._cas(key, doc, {_ITEM_ID: item_id, _PUSHED: pushed}):
                return _bound(True, pushed, item_id, current, None)
        raise self._contended(key)


def _bound(
    bound: bool, pushed: int | None, item_id: Any, current: int, reason: str | None
) -> dict[str, Any]:
    return {
        "bound": bound,
        "stale": pushed is not None and current > pushed,
        _PUSHED: pushed,
        _ITEM_ID: item_id,
        _REVISION: current,
        "reason": reason,
    }
