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

``posted_by`` is ``{run_id, workflow_id, step}``: the run whose ``discord.message`` step
posted the button, that run's workflow id (``None`` when the run could not be read) and the
step, or ``None`` for a post outside a run (deviation d2 of the green PR merge prompt). It is
a record of who posted the button; nothing trusts it.

**One-time codes** (deviation d3). A valued button given ``code: true`` also gets a code: a
fresh ``secrets.token_urlsafe(24)``, never shown in the button's label or id. It is written
with the value, on the button's document (``code``) and as its own document in
:data:`CODES_COLLECTION`::

    {id: "<code>", button_id, custom_id, value, used_at: null, used_by: null, created_at,
     expires_at}

The gateway copies the code onto the ``discord.button.clicked`` event (``data.code``), as it
copies the value, so a clicker cannot supply one. A step that acts on a click calls
:func:`use_code`: the code must exist, be unexpired, belong to the clicked ``custom_id``, hold
the value the step acts on, and be unused (or used by the same run, a retry), and it is used
up by compare-and-set. A code is used once: a second click, a replayed event or a forged value
gets nothing. ``github.merge`` merges only with such a code, so the merge workflow and the
workflow that posts the button are both configuration: no digest is pinned for them.

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
    "CODES_COLLECTION",
    "CODE_MISMATCH",
    "CODE_NOT_FOUND",
    "CODE_USED",
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
    "release_code",
    "stored_button",
    "use_code",
]

BUTTONS_COLLECTION = "discord_buttons"
BUTTONS_TTL_INDEX = "discord_buttons_ttl"
BUTTON_VALUE_TTL = timedelta(days=30)
CODES_COLLECTION = "discord_button_codes"
CODES_TTL_INDEX = "discord_button_codes_ttl"
CODE_NOT_FOUND = "code_not_found"
"""The click carries no code, or one that was never issued or has expired."""
CODE_MISMATCH = "code_mismatch"
"""The code belongs to another button, or holds another value than the one acted on."""
CODE_USED = "code_used"
"""The code was already used, by another run."""
_CODE_BYTES = 24
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


def prepare_buttons(
    buttons: Any,
) -> tuple[list[dict[str, Any]], list[tuple[str, Any, bool]]]:
    """Validate ``buttons`` and give each valued one its posted id.

    Returns the components to post (see
    :func:`~culture_rules.apps.discord_rest.button_components`, without any ``value`` or
    ``code``) and ``(posted custom_id, value, wants a code)`` for each valued button, in button
    order. Raises ``ValueError`` for anything refused, before any store or network call."""
    button_components(buttons)  # label, style, custom_id, count, uniqueness
    if not buttons:
        return [], []
    posted: list[dict[str, Any]] = []
    valued: list[tuple[str, Any, bool]] = []
    for i, b in enumerate(buttons):
        value = b.get("value")
        custom_id = b["custom_id"]
        code = b.get("code", False)
        if not isinstance(code, bool):
            raise ValueError(f"button {i} code must be true or false")
        if code and value is None:
            raise ValueError(f"button {i} code needs a value (the code is bound to it)")
        if value is not None:
            _check_value(value, i)
            if len(custom_id) > MAX_VALUED_PREFIX:
                raise ValueError(
                    f"button {i} custom_id must be 1-{MAX_VALUED_PREFIX} characters with a value"
                    " (a generated token is added)"
                )
            custom_id = posted_id(custom_id)
            valued.append((custom_id, json.loads(encoded(value)), code))
        posted.append({"label": b["label"], "style": b["style"], "custom_id": custom_id})
    return button_components(posted), valued


def _doc_id(actor_id: str, custom_id: str) -> str:
    return f"{actor_id}:{custom_id}"


def ensure_buttons_ttl(store: Any) -> None:
    """Create :data:`BUTTONS_TTL_INDEX` and :data:`CODES_TTL_INDEX` where the store supports
    indexes (MongoDB)."""
    ensure = getattr(store, "ensure_index", None)
    if callable(ensure):
        ensure(BUTTONS_COLLECTION, [(_EXPIRES_AT, 1)], name=BUTTONS_TTL_INDEX, ttl_seconds=0)
        ensure(CODES_COLLECTION, [(_EXPIRES_AT, 1)], name=CODES_TTL_INDEX, ttl_seconds=0)


def remember_buttons(
    store: Any,
    actor_id: str,
    channel_id: str,
    valued: Iterable[tuple[str, Any, bool]],
    *,
    now: datetime,
    posted_by: Mapping[str, Any] | None = None,
) -> list[str]:
    """Insert one document per valued button (and one code per button that wants one) and
    return the buttons' ids. ``posted_by`` (``{run_id, workflow_id, step}`` of the posting run,
    or ``None``) is kept on each. A failed insert deletes the ones already written (best
    effort) and raises."""
    poster = dict(posted_by) if posted_by is not None else None
    expires = now + BUTTON_VALUE_TTL  # a date: the TTL index deletes it
    ids: list[str] = []
    try:
        for custom_id, value, wants_code in valued:
            doc_id = _doc_id(actor_id, custom_id)
            code = secrets.token_urlsafe(_CODE_BYTES) if wants_code else None
            store.insert(
                BUTTONS_COLLECTION,
                {
                    "id": doc_id,
                    "actor_id": actor_id,
                    "custom_id": custom_id,
                    "value": value,
                    "code": code,
                    "posted_by": poster,
                    "channel_id": channel_id,
                    "message_id": None,
                    "created_at": now,
                    _EXPIRES_AT: expires,
                },
            )
            ids.append(doc_id)
            if code is not None:
                store.insert(
                    CODES_COLLECTION,
                    {
                        "id": code,
                        "button_id": doc_id,
                        "custom_id": custom_id,
                        "value": value,
                        "used_at": None,
                        "used_by": None,
                        "created_at": now,
                        _EXPIRES_AT: expires,
                    },
                )
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
    """Delete the documents and their codes (Discord refused the post); each delete is best
    effort."""
    for doc_id in ids:
        try:
            code = (store.get(BUTTONS_COLLECTION, doc_id) or {}).get("code")
            if code:
                store.delete(CODES_COLLECTION, code)
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
    """``{value, posted_by, code}`` stored for the actor's button ``custom_id`` (one read by
    id), or ``None`` when there is none or it has expired. ``posted_by`` and ``code`` are
    ``None`` for a document written without one."""
    doc = store.get(BUTTONS_COLLECTION, _doc_id(actor_id, custom_id))
    if doc is None:
        return None
    expires = _as_utc(doc.get(_EXPIRES_AT))
    if expires is None or (now or datetime.now(UTC)) >= expires:
        return None
    return {"value": doc.get("value"), "posted_by": doc.get("posted_by"), "code": doc.get("code")}


def button_value(store: Any, actor_id: str, custom_id: str, *, now: datetime | None = None) -> Any:
    """The value stored for the actor's button ``custom_id``, or ``None`` when there is none or
    it has expired (:func:`stored_button`'s ``value``)."""
    stored = stored_button(store, actor_id, custom_id, now=now)
    return stored["value"] if stored is not None else None


def use_code(
    store: Any, code: Any, *, custom_id: Any, value: Mapping[str, Any], run_id: str, now: datetime
) -> str | None:
    """Use up the one-time ``code`` a click carried, for the run ``run_id`` acting on
    ``value``; ``None`` when it may act, else :data:`CODE_NOT_FOUND`, :data:`CODE_MISMATCH` or
    :data:`CODE_USED`.

    The code must be stored and unexpired, belong to the clicked ``custom_id``, and its stored
    value must hold every key of ``value`` with the same value. Unused, it is marked used by
    ``run_id`` by compare-and-set (so of two runs, one wins); used by ``run_id`` already, it
    passes again (that run's retry)."""
    if not isinstance(code, str) or not code or not isinstance(custom_id, str):
        return CODE_NOT_FOUND
    doc = store.get(CODES_COLLECTION, code)
    if doc is None:
        return CODE_NOT_FOUND
    expires = _as_utc(doc.get(_EXPIRES_AT))
    if expires is None or now >= expires:
        return CODE_NOT_FOUND
    stored = doc.get("value")
    if doc.get("custom_id") != custom_id or not isinstance(stored, Mapping):
        return CODE_MISMATCH
    if any(k not in stored or not _same(stored[k], v) for k, v in value.items()):
        return CODE_MISMATCH
    if doc.get("used_by") == run_id:
        return None
    if doc.get("used_at") is not None:
        return CODE_USED
    result = store.update_if(
        CODES_COLLECTION,
        code,
        {"used_at": None, "used_by": None},
        {"used_at": now, "used_by": run_id},
    )
    if result.won:
        return None
    current = result.document or {}
    return None if current.get("used_by") == run_id else CODE_USED


def _same(a: Any, b: Any) -> bool:
    """Equal and of the same type: a stored ``true`` is not the number 1, nor ``1.0`` the
    integer 1."""
    return type(a) is type(b) and a == b


def release_code(store: Any, code: Any, run_id: str) -> bool:
    """Give the code ``run_id`` used back (unused again), when that run's action did nothing
    with it, e.g. a merge GitHub refused; ``True`` when it was released. Only the run holding
    it can release it (compare-and-set), so a code another run used stays used."""
    if not isinstance(code, str) or not code:
        return False
    result = store.update_if(
        CODES_COLLECTION, code, {"used_by": run_id}, {"used_at": None, "used_by": None}
    )
    return bool(result.won)
