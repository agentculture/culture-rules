"""Keysets: named sets of keys, each remembered for a window, in the store.

Generic capabilities only. Every rule (what to normalise, how long to remember) comes from
the config or arguments the caller passes; nothing here knows a flow, a URL parameter or a
default window. Standard library only.

A set is named (letters, digits, ``_ : -``, at most :data:`MAX_SET_NAME` characters). A key
is any string of at most :data:`MAX_KEY` characters after :func:`normalise`. One document
per (set, key) lives in :data:`KEYSETS_COLLECTION`::

    {id: sha256(set NUL key), set, key, first_seen, expires_at}

``first_seen`` and ``expires_at`` are timezone-aware datetimes (a BSON date on MongoDB), so
the TTL index from :func:`ensure_ttl_index` reaps expired documents; until it does,
:func:`seen` ignores a document whose ``expires_at`` has passed. Two callers using one set
name share its keys.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = [
    "KEYSETS_COLLECTION",
    "EXPIRY_INDEX",
    "MAX_KEY",
    "MAX_SET_NAME",
    "KeysetError",
    "add_keys",
    "check_key",
    "check_set_name",
    "clear_key",
    "clear_set",
    "doc_id",
    "ensure_ttl_index",
    "list_sets",
    "normalise",
    "seen",
    "show_set",
]

KEYSETS_COLLECTION = "keysets"
EXPIRY_INDEX = "keysets_expires_at"
MAX_SET_NAME = 100
MAX_KEY = 2048
_SET_NAME = re.compile(r"[A-Za-z0-9_:\-]+")


class KeysetError(ValueError):
    """A bad set name, key, config or argument; ``code`` is ``bad_params``."""

    code = "bad_params"


def check_set_name(name: Any) -> str:
    """``name`` when it is a valid set name, else :class:`KeysetError`."""
    if (
        not isinstance(name, str)
        or not 0 < len(name) <= MAX_SET_NAME
        or not _SET_NAME.fullmatch(name)
    ):
        raise KeysetError(
            f"set name must be 1..{MAX_SET_NAME} characters of letters, digits, _ : -"
        )
    return name


def check_key(key: Any) -> str:
    """``key`` when it is a non-empty string of at most :data:`MAX_KEY` characters."""
    if not isinstance(key, str) or not 0 < len(key) <= MAX_KEY:
        raise KeysetError(f"key must be a non-empty string of at most {MAX_KEY} characters")
    return key


def _patterns(config: Mapping[str, Any]) -> list[str]:
    patterns = config.get("drop_query", [])
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise KeysetError("drop_query must be a list of strings")
    return patterns


def normalise(value: str, config: Mapping[str, Any] | None) -> str:
    """Normalise ``value`` as ``config`` says; with no config, ``value`` unchanged.

    Switches (all optional, off unless set): ``lower_host``, ``drop_scheme``,
    ``drop_fragment``, ``drop_trailing_slash`` (booleans) and ``drop_query`` (a list of
    fnmatch patterns naming query parameters to remove). No pattern list is built in.
    """
    if not config:
        return value
    if not isinstance(config, Mapping):
        raise KeysetError("normalise config must be a mapping")
    patterns = _patterns(config)
    parts = urlsplit(value)
    scheme, netloc, path, query, fragment = parts
    if config.get("lower_host"):
        netloc = netloc.lower()
    if patterns:
        kept = [
            (k, v)
            for k, v in parse_qsl(query, keep_blank_values=True)
            if not any(fnmatch.fnmatchcase(k, p) for p in patterns)
        ]
        query = urlencode(kept)
    if config.get("drop_fragment"):
        fragment = ""
    if config.get("drop_trailing_slash") and len(path) > 1:
        path = path.rstrip("/") or "/"
    out = urlunsplit(("" if config.get("drop_scheme") else scheme, netloc, path, query, fragment))
    if config.get("drop_scheme") and out.startswith("//"):
        out = out[2:]
    return out


def doc_id(set_name: str, key: str) -> str:
    """The document id of ``key`` in ``set_name``: sha256 hex of set, NUL, key."""
    return hashlib.sha256(f"{set_name}\x00{key}".encode()).hexdigest()


def _aware(now: Any) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise KeysetError("now must be a timezone-aware datetime")
    return now


def add_keys(
    store: Any, set_name: str, keys: Iterable[str], now: datetime, window_s: int
) -> dict[str, int]:
    """Remember each key until ``now + window_s``; ``{added, already_present}``.

    A key is inserted only if absent (compare-and-set with upsert), so a retried or
    repeated add leaves one document and keeps ``first_seen``. A document whose window has
    passed but which is not yet reaped is renewed and counts as added.
    """
    check_set_name(set_name)
    _aware(now)
    if not isinstance(window_s, int) or isinstance(window_s, bool) or window_s <= 0:
        raise KeysetError("window_s must be a positive integer")
    expires = now + timedelta(seconds=window_s)
    added = present = 0
    done: set[str] = set()
    for key in keys:
        check_key(key)
        if key in done:
            present += 1
            continue
        done.add(key)
        did = doc_id(set_name, key)
        fields = {"set": set_name, "key": key, "first_seen": now, "expires_at": expires}
        won = store.update_if(KEYSETS_COLLECTION, did, {"first_seen": None}, fields, upsert=True)
        if not won.won:
            old = won.document or {}
            old_exp = _utc(old.get("expires_at"))
            if old_exp is not None and old_exp <= now:
                won = store.update_if(
                    KEYSETS_COLLECTION,
                    did,
                    {"first_seen": old.get("first_seen")},
                    fields,
                )
        if won.won:
            added += 1
        else:
            present += 1
    return {"added": added, "already_present": present}


def _utc(value: Any) -> datetime | None:
    """A stored date as an aware UTC datetime: MongoDB hands dates back naive (UTC)."""
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _live(doc: Mapping[str, Any], now: datetime) -> bool:
    expires = _utc(doc.get("expires_at"))
    return expires is not None and expires > now


def seen(store: Any, set_name: str, keys: Iterable[str], now: datetime) -> list[str]:
    """The given keys that are in the set with a window not yet passed (input order)."""
    check_set_name(set_name)
    _aware(now)
    found = []
    for key in keys:
        doc = store.get(KEYSETS_COLLECTION, doc_id(set_name, check_key(key)))
        if doc is not None and _live(doc, now):
            found.append(key)
    return found


def list_sets(store: Any, now: datetime) -> list[dict[str, Any]]:
    """Every set with live keys, ``{set, keys}`` sorted by name."""
    _aware(now)
    counts: dict[str, int] = {}
    for doc in store.find(KEYSETS_COLLECTION):
        if _live(doc, now):
            counts[doc["set"]] = counts.get(doc["set"], 0) + 1
    return [{"set": name, "keys": counts[name]} for name in sorted(counts)]


def show_set(store: Any, set_name: str, now: datetime) -> list[dict[str, Any]]:
    """The live ``{key, first_seen, expires_at}`` of a set, sorted by key."""
    check_set_name(set_name)
    _aware(now)
    docs = [d for d in store.find(KEYSETS_COLLECTION, {"set": set_name}) if _live(d, now)]
    return [
        {"key": d["key"], "first_seen": _utc(d["first_seen"]), "expires_at": _utc(d["expires_at"])}
        for d in sorted(docs, key=lambda d: d["key"])
    ]


def clear_key(store: Any, set_name: str, key: str) -> bool:
    """Forget one key; whether it was stored."""
    check_set_name(set_name)
    return bool(store.delete(KEYSETS_COLLECTION, doc_id(set_name, check_key(key))))


def clear_set(store: Any, set_name: str) -> int:
    """Forget every key of a set; how many documents were deleted."""
    check_set_name(set_name)
    return sum(
        1
        for doc in store.find(KEYSETS_COLLECTION, {"set": set_name})
        if store.delete(KEYSETS_COLLECTION, doc["id"])
    )


def ensure_ttl_index(store: Any) -> None:
    """Create a TTL index on ``expires_at`` where the store supports indexes (MongoDB)."""
    ensure = getattr(store, "ensure_index", None)
    if callable(ensure):
        ensure(KEYSETS_COLLECTION, [("expires_at", 1)], name=EXPIRY_INDEX, ttl_seconds=0)
