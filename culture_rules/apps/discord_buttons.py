"""What a Discord button means, kept in the store rather than in its ``custom_id``.

A ``discord.message`` button may carry a ``value``: a JSON object (string keys; strings,
numbers, booleans, ``null`` and nested objects or lists of those; at most
:data:`MAX_VALUE_BYTES` bytes as compact JSON). The given ``custom_id`` is then a **prefix**:
the posted id is ``<custom_id>:<token>`` (:func:`posted_id`, a fresh
``secrets.token_urlsafe(16)``), so the ``custom_id`` trigger filter still selects the button
by the id it was given (``merge`` or ``merge:``,
:func:`~culture_rules.model.discord_filters.custom_id_admits`), and no step ever has to parse
an id. A button without
a ``value`` posts its ``custom_id`` unchanged.

The value is written to :data:`BUTTONS_COLLECTION` **before** the message is posted (so a fast
click always finds it), one document per valued button::

    {id: "<actor id>:<posted custom_id>", actor_id, custom_id, value, posted_by, channel_id,
     message_id, created_at, expires_at}

``posted_by`` is ``{run_id, workflow_id}``: the run whose ``discord.message`` step posted the
button and that run's workflow id (``None`` when the run could not be read), or ``None`` for
a post outside a run (deviation d2 of the green PR merge prompt). ``github.merge`` loads that
run and merges only when its pinned workflow is trusted in the ``merge_prompt`` role, so an
untrusted workflow cannot post a button whose stored value another workflow then merges.

``message_id`` is filled after the post (best effort); a post Discord definitively refused
deletes the documents again (one that may have landed - a timeout, a 5xx, a 429, a lost
connection - keeps them). ``expires_at`` is :data:`BUTTON_VALUE_TTL` (30 days) after
``created_at``; the TTL index :data:`BUTTONS_TTL_INDEX` deletes the document then, and
:func:`button_value` treats a passed ``expires_at`` as gone. A click on an expired button
carries no value.

Only the bot's own ``discord.message`` port writes these documents: the gateway reads them
by the actor and the clicked ``custom_id`` (:func:`stored_button`) and attaches the value and
the poster to the ``discord.button.clicked`` event as ``data.value`` and ``data.posted_by``.
A clicker sends only the posted id, so it cannot change or invent either. Standard-library
only, store-agnostic.
"""

from __future__ import annotations

import json
import math
import secrets
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.apps.discord_rest import MAX_CUSTOM_ID, button_components

__all__ = [
    "BUTTONS_COLLECTION",
    "BUTTONS_TTL_INDEX",
    "BUTTON_VALUE_TTL",
    "MAX_VALUED_PREFIX",
    "MAX_VALUE_BYTES",
    "button_value",
    "encoded",
    "ensure_buttons_ttl",
    "forget_buttons",
    "posted_id",
    "prepare_buttons",
    "remember_buttons",
    "set_message_id",
    "stored_button",
]

BUTTONS_COLLECTION = "discord_buttons"
BUTTONS_TTL_INDEX = "discord_buttons_ttl"
BUTTON_VALUE_TTL = timedelta(days=30)
MAX_VALUE_BYTES = 4096
_TOKEN_BYTES = 16
_TOKEN_CHARS = len(secrets.token_urlsafe(_TOKEN_BYTES))  # 22
MAX_VALUED_PREFIX = MAX_CUSTOM_ID - 1 - _TOKEN_CHARS
"""The longest ``custom_id`` a valued button may be given: the ``:`` and the token must fit."""
_EXPIRES_AT = "expires_at"


def encoded(value: Any) -> bytes:
    """``value`` as compact JSON (the size :data:`MAX_VALUE_BYTES` limits). Raises
    ``ValueError`` or ``TypeError`` for what JSON cannot hold (NaN and infinities included)."""
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json_ok(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(isinstance(k, str) and _json_ok(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(_json_ok(v) for v in value)
    return False


def _check_value(value: Any, i: int) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"button {i} value must be a JSON object")
    try:
        size = len(encoded(value))
    except (TypeError, ValueError, RecursionError):
        raise ValueError(f"button {i} value must be plain JSON") from None
    if not _json_ok(value):
        raise ValueError(f"button {i} value must be plain JSON")
    if size > MAX_VALUE_BYTES:
        raise ValueError(f"button {i} value must be at most {MAX_VALUE_BYTES} bytes as JSON")


def posted_id(prefix: str) -> str:
    """The id a valued button is posted with: ``<prefix>:<token>``, a fresh random token."""
    return f"{prefix}:{secrets.token_urlsafe(_TOKEN_BYTES)}"


def prepare_buttons(buttons: Any) -> tuple[list[dict[str, Any]], list[tuple[str, Any]]]:
    """Validate ``buttons`` and give each valued one its posted id.

    Returns the components to post (see
    :func:`~culture_rules.apps.discord_rest.button_components`, without any ``value``) and
    ``(posted custom_id, value)`` for each valued button, in button order. Raises
    ``ValueError`` for anything refused, before any store or network call."""
    button_components(buttons)  # label, style, custom_id, count, uniqueness
    if not buttons:
        return [], []
    posted: list[dict[str, Any]] = []
    valued: list[tuple[str, Any]] = []
    for i, b in enumerate(buttons):
        value = b.get("value")
        custom_id = b["custom_id"]
        if value is not None:
            _check_value(value, i)
            if len(custom_id) > MAX_VALUED_PREFIX:
                raise ValueError(
                    f"button {i} custom_id must be 1-{MAX_VALUED_PREFIX} characters with a value"
                    " (a generated token is added)"
                )
            custom_id = posted_id(custom_id)
            valued.append((custom_id, json.loads(encoded(value))))
        posted.append({"label": b["label"], "style": b["style"], "custom_id": custom_id})
    return button_components(posted), valued


def _doc_id(actor_id: str, custom_id: str) -> str:
    return f"{actor_id}:{custom_id}"


def ensure_buttons_ttl(store: Any) -> None:
    """Create :data:`BUTTONS_TTL_INDEX` where the store supports indexes (MongoDB)."""
    ensure = getattr(store, "ensure_index", None)
    if callable(ensure):
        ensure(BUTTONS_COLLECTION, [(_EXPIRES_AT, 1)], name=BUTTONS_TTL_INDEX, ttl_seconds=0)


def remember_buttons(
    store: Any,
    actor_id: str,
    channel_id: str,
    valued: Iterable[tuple[str, Any]],
    *,
    now: datetime,
    posted_by: Mapping[str, Any] | None = None,
) -> list[str]:
    """Insert one document per valued button and return their ids. ``posted_by`` (``{run_id,
    workflow_id}`` of the posting run, or ``None``) is kept on each. A failed insert deletes the
    ones already written (best effort) and raises."""
    poster = dict(posted_by) if posted_by is not None else None
    ids: list[str] = []
    try:
        for custom_id, value in valued:
            doc_id = _doc_id(actor_id, custom_id)
            store.insert(
                BUTTONS_COLLECTION,
                {
                    "id": doc_id,
                    "actor_id": actor_id,
                    "custom_id": custom_id,
                    "value": value,
                    "posted_by": poster,
                    "channel_id": channel_id,
                    "message_id": None,
                    "created_at": now,
                    _EXPIRES_AT: now + BUTTON_VALUE_TTL,  # a date: the TTL index deletes it
                },
            )
            ids.append(doc_id)
    except Exception:
        forget_buttons(store, ids)
        raise
    return ids


def set_message_id(store: Any, ids: Sequence[str], message_id: str) -> None:
    """Record the posted message's id on each document."""
    for doc_id in ids:
        doc = store.get(BUTTONS_COLLECTION, doc_id)
        if doc is not None:
            store.put(BUTTONS_COLLECTION, {**doc, "message_id": message_id})


def forget_buttons(store: Any, ids: Iterable[str]) -> None:
    """Delete the documents (Discord refused the post); each delete is best effort."""
    for doc_id in ids:
        try:
            store.delete(BUTTONS_COLLECTION, doc_id)
        except Exception:  # noqa: BLE001 # nosec B112 - the TTL index deletes it anyway
            continue


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def stored_button(
    store: Any, actor_id: str, custom_id: str, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """``{value, posted_by}`` stored for the actor's button ``custom_id`` (one read by id), or
    ``None`` when there is none or it has expired. ``posted_by`` is ``None`` for a document
    written without one."""
    doc = store.get(BUTTONS_COLLECTION, _doc_id(actor_id, custom_id))
    if doc is None:
        return None
    expires = _as_utc(doc.get(_EXPIRES_AT))
    if expires is None or (now or datetime.now(UTC)) >= expires:
        return None
    return {"value": doc.get("value"), "posted_by": doc.get("posted_by")}


def button_value(store: Any, actor_id: str, custom_id: str, *, now: datetime | None = None) -> Any:
    """The value stored for the actor's button ``custom_id``, or ``None`` when there is none or
    it has expired (:func:`stored_button`'s ``value``)."""
    stored = stored_button(store, actor_id, custom_id, now=now)
    return stored["value"] if stored is not None else None
