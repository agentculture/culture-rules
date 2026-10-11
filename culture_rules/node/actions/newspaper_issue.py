"""The per-date guard of the daily newspaper (#53): built-in ``newspaper.issue``.

:class:`NewspaperIssuePort` is re-exported from :mod:`culture_rules.node.actions.newspaper`.
Standard-library only.

One issue per local date (c15, h8). Input ``slot`` (the schedule event's ISO-8601 UTC slot,
``trigger.data.slot``), ``tz`` (IANA zone; the input or ``config.tz``) and ``phase`` (the
input or ``config.phase``): the local date is the slot's date in ``tz`` (zoneinfo, so DST is
right). One document per date in :data:`ISSUES_COLLECTION` (id = the date), written only by
compare-and-set on ``rev``.

The document
============

* ``state`` - ``claimed`` or ``delivered``;
* ``claimed_by`` / ``claimed_at`` - the run that holds the date and its **lease**: the lease
  ends ``config.claim_timeout_s`` (default :data:`DEFAULT_CLAIM_TIMEOUT_S`) after
  ``claimed_at``, and the holder renews it (moves ``claimed_at``) at every fence;
* ``email_payload`` - ``{subject, html}``, the first rendered email for the date, reused by
  any later run for that date;
* ``email`` - ``{message_id, at}``, written as soon as the email is known sent;
* ``library`` - ``{item_id, queue_id, at}``, written with the delivery record;
* ``delivered_by``, ``delivered_at``, ``message_id``, ``item_id`` - the delivery record.

The phases, in workflow order
=============================

* ``claim`` - output ``{deliver, date, reason, summaries_until}``. ``reason`` is
  ``already_delivered`` (deliver false), ``claimed`` (no document, or this run's own claim),
  ``claimed_by_another_run`` (another run's lease is live: deliver false) or ``taken_over``
  (another run's lease ran out - its run died or stalled: this run takes the date and keeps
  its ``email`` progress and ``email_payload``). ``summaries_until`` is the slot plus
  ``config.summary_window_s`` (default :data:`DEFAULT_SUMMARY_WINDOW_S`), an ISO-8601 UTC
  time: the summary steps give up at it, so the issue goes out on time with results only
  (Codex r1 #3). A re-run for a past slot is therefore past its window and sends results.
* ``before_email`` (fence) - input ``subject`` and ``html`` (the rendered issue). It renews
  the lease, refusing (``claim_lost``, not retryable: the run fails) when the date is no
  longer this run's. It stores the payload when none is stored and answers ``{send, date,
  subject, html, idempotency_key}`` with the **stored** payload and the durable key
  ``newspaper:<date>:email`` (:func:`email_key`), the same for every run of the date; ``send``
  is false when the email is already recorded sent (``message_id`` is then given too).
* ``before_push`` (fence) - input ``message_id`` (the email step's, absent when it was
  skipped). It records ``email`` the first time it sees a message id - even when the claim
  was lost, since the email went out either way - then renews the lease or refuses
  ``claim_lost``. Output ``{message_id, date}``: the recorded message id.
* ``record`` (after delivery) - sets state ``delivered`` with ``message_id``, ``item_id``,
  ``library`` (``item_id``, ``queue_id``), ``delivered_by`` and ``delivered_at``, whoever
  claimed. Output ``{recorded, date}``: false when another run already recorded the date; a
  re-invoked record of the same run answers true again.

Why a date is never emailed twice (Codex r1 #1)
===============================================

A run that sent the email and then failed (say xteink was down) leaves the date claimed with
``email`` recorded. A run that takes the date over after the lease sees ``email`` at its
``before_email`` fence, skips the email and retries only the push. Two narrow windows remain
and are both covered:

* the email went out but ``before_push`` never recorded it (the node died in between): the
  next run sends again with the same durable key and the same stored payload, and Resend
  answers the first message id instead of sending (its idempotency window is 24 hours);
* a slow owner: every side effect follows a fence that renews the lease, and a takeover
  needs the lease to run out, so an owner that was taken over fails its next fence and sends
  nothing more. The lease (an hour) is far longer than an email or push step takes, and the
  summaries are bounded by ``summaries_until``.

The EPUB has no such key: a crash between a successful push and its record can queue the
issue on the reader a second time (never a second email).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = [
    "BEFORE_EMAIL",
    "BEFORE_PUSH",
    "CLAIM",
    "CLAIM_LOST",
    "DEFAULT_CLAIM_TIMEOUT_S",
    "DEFAULT_SUMMARY_WINDOW_S",
    "ISSUES_COLLECTION",
    "PHASES",
    "RECORD",
    "NewspaperIssuePort",
    "email_key",
    "local_date",
]

ISSUES_COLLECTION = "newspaper_issues"
CLAIM = "claim"
BEFORE_EMAIL = "before_email"
BEFORE_PUSH = "before_push"
RECORD = "record"
PHASES = (CLAIM, BEFORE_EMAIL, BEFORE_PUSH, RECORD)
CLAIMED = "claimed"
DELIVERED = "delivered"
TAKEN_OVER = "taken_over"
BY_ANOTHER_RUN = "claimed_by_another_run"
CLAIM_LOST = "claim_lost"
DEFAULT_CLAIM_TIMEOUT_S = 3600
DEFAULT_SUMMARY_WINDOW_S = 600
_EMAIL = "email"
_PAYLOAD = "email_payload"
_MESSAGE_ID = "message_id"
_SUBJECT, _HTML = "subject", "html"
_ID_LEN = 200
_CAS_TRIES = 3

Clock = Callable[[], datetime]


class _Refusal(Exception):
    """A phase refuses: ``result`` is its failed InvocationResult."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.result = InvocationResult.failed(message, retryable=retryable)


def local_date(slot: Any, tz: Any) -> str:
    """The slot's date (``YYYY-MM-DD``) in the IANA zone ``tz``; ValueError names the field."""
    return _local(_slot(slot), tz).date().isoformat()


def _slot(slot: Any) -> datetime:
    if not isinstance(slot, str):
        raise ValueError("slot: required, an ISO-8601 time")
    try:
        when = datetime.fromisoformat(slot)
    except ValueError:
        raise ValueError("slot: not an ISO-8601 time") from None
    if when.tzinfo is None:
        raise ValueError("slot: must carry a UTC offset")
    return when


def _local(when: datetime, tz: Any) -> datetime:
    if not isinstance(tz, str) or not tz:
        raise ValueError("tz: required, an IANA zone such as Asia/Jerusalem")
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("tz: unknown IANA zone") from None
    return when.astimezone(zone)


def email_key(day: str) -> str:
    """The durable Resend idempotency key of a date's email: the same for every run."""
    return f"newspaper:{day}:{_EMAIL}"


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat(timespec="seconds")


def _age_s(stamp: Any, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return None


def _delivery_id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
        return None
    return str(value)[:_ID_LEN]


def _seconds(config: Mapping[str, Any], name: str, default: int) -> int:
    value = config.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name}: must be a positive integer (seconds)")
    return value


def _sent_id(doc: Mapping[str, Any] | None) -> str | None:
    """The recorded message id of the date's email, or None while it is not known sent."""
    email = (doc or {}).get(_EMAIL)
    return _delivery_id(email.get(_MESSAGE_ID)) if isinstance(email, Mapping) else None


def _owned(doc: Mapping[str, Any] | None, run_id: str) -> bool:
    return doc is not None and doc.get("state") == CLAIMED and doc.get("claimed_by") == run_id


def _decide(doc: Mapping[str, Any] | None, run_id: str, now: datetime, timeout: int) -> str | None:
    """A claim's answer from the current document, or None to (re)claim it."""
    if doc is None:
        return None
    if doc.get("state") == DELIVERED:
        return "already_delivered"
    if doc.get("claimed_by") == run_id:
        return CLAIMED
    age = _age_s(doc.get("claimed_at"), now)
    if age is not None and age < timeout:
        return BY_ANOTHER_RUN
    return None


def _payload(input: Mapping[str, Any]) -> dict[str, str]:
    subject, html = input.get(_SUBJECT), input.get(_HTML)
    if not isinstance(subject, str) or not subject or not isinstance(html, str) or not html:
        raise _Refusal("bad_input: subject and html: required, non-empty strings")
    return {_SUBJECT: subject, _HTML: html}


def _stored_payload(doc: Mapping[str, Any]) -> dict[str, str] | None:
    stored = doc.get(_PAYLOAD)
    if not isinstance(stored, Mapping):
        return None
    subject, html = stored.get(_SUBJECT), stored.get(_HTML)
    if isinstance(subject, str) and subject and isinstance(html, str) and html:
        return {_SUBJECT: subject, _HTML: html}
    return None


def _lost(day: str) -> _Refusal:
    return _Refusal(f"{CLAIM_LOST}: {ISSUES_COLLECTION}/{day} is no longer held by this run")


class NewspaperIssuePort:
    """``newspaper.issue`` (module doc): one issue per local date, fenced side effects."""

    supports_idempotency_key = True  # every phase is idempotent per run

    def __init__(self, store: Any, *, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config or {}
        phase = input.get("phase") or config.get("phase")
        try:
            if phase not in PHASES:
                raise ValueError(f"phase: must be one of {', '.join(PHASES)}")
            slot = _slot(input.get("slot"))
            day = _local(slot, input.get("tz") or config.get("tz")).date().isoformat()
            timeout = _seconds(config, "claim_timeout_s", DEFAULT_CLAIM_TIMEOUT_S)
            window = _seconds(config, "summary_window_s", DEFAULT_SUMMARY_WINDOW_S)
        except ValueError as exc:
            return InvocationResult.failed(f"bad_input: {exc}", retryable=False)
        run_id = context.run_id
        try:
            if phase == CLAIM:
                output = self._claim(day, run_id, timeout)
                output["summaries_until"] = _iso(slot + timedelta(seconds=window))
            elif phase == BEFORE_EMAIL:
                output = self._before_email(day, run_id, _payload(input))
            elif phase == BEFORE_PUSH:
                output = self._before_push(day, run_id, _delivery_id(input.get(_MESSAGE_ID)))
            else:
                output = self._record(day, run_id, input)
        except _Refusal as refusal:
            return refusal.result
        return InvocationResult.completed(output)

    # ------------------------------------------------------------------ store access

    def _cas(self, day: str, doc: Mapping[str, Any] | None, changes: dict[str, Any]) -> bool:
        rev = (doc or {}).get("rev")
        changes = {**changes, "date": day, "rev": (rev or 0) + 1}
        result = self._store.update_if(
            ISSUES_COLLECTION, day, {"rev": rev}, changes, upsert=doc is None
        )
        return bool(result.won)

    def _get(self, day: str) -> Mapping[str, Any] | None:
        return self._store.get(ISSUES_COLLECTION, day)

    @staticmethod
    def _contended(day: str) -> _Refusal:
        return _Refusal(f"issue_contended: {ISSUES_COLLECTION}/{day}", retryable=True)

    # ------------------------------------------------------------------ the phases

    def _claim(self, day: str, run_id: str, timeout: int) -> dict[str, Any]:
        for _ in range(_CAS_TRIES):
            doc = self._get(day)
            now = self._clock()
            reason = _decide(doc, run_id, now, timeout)
            if reason is not None:
                return _claimed(day, reason)
            changes = {"state": CLAIMED, "claimed_by": run_id, "claimed_at": _iso(now)}
            if self._cas(day, doc, changes):
                return _claimed(day, TAKEN_OVER if doc else CLAIMED)
        return _claimed(day, BY_ANOTHER_RUN)

    def _before_email(self, day: str, run_id: str, payload: dict[str, str]) -> dict[str, Any]:
        """Fence the email: renew the lease, keep the first payload, report a sent email."""
        for _ in range(_CAS_TRIES):
            doc = self._get(day)
            if not _owned(doc, run_id):
                raise _lost(day)
            stored = _stored_payload(doc)
            changes: dict[str, Any] = {"claimed_at": _iso(self._clock())}
            if stored is None:
                changes[_PAYLOAD] = payload
            if not self._cas(day, doc, changes):
                continue
            output: dict[str, Any] = {
                "date": day,
                "idempotency_key": email_key(day),
                **(stored or payload),
            }
            sent = _sent_id(doc)
            output["send"] = sent is None
            if sent is not None:
                output[_MESSAGE_ID] = sent
            return output
        raise self._contended(day)

    def _before_push(self, day: str, run_id: str, message_id: str | None) -> dict[str, Any]:
        """Record the sent email (whoever holds the date), then fence the push."""
        for _ in range(_CAS_TRIES):
            doc = self._get(day)
            owned = _owned(doc, run_id)
            recorded = _sent_id(doc)
            changes: dict[str, Any] = {}
            if doc is not None and recorded is None and message_id is not None:
                changes[_EMAIL] = {_MESSAGE_ID: message_id, "at": _iso(self._clock())}
            if owned:
                changes["claimed_at"] = _iso(self._clock())
            if changes and not self._cas(day, doc, changes):
                continue
            if not owned:
                raise _lost(day)
            sent = recorded or message_id
            if sent is None:
                raise _Refusal("bad_input: message_id: no email is recorded sent for the date")
            return {_MESSAGE_ID: sent, "date": day}
        raise self._contended(day)

    def _record(self, day: str, run_id: str, input: Mapping[str, Any]) -> dict[str, Any]:
        item_id = _delivery_id(input.get("item_id"))
        changes = {
            "state": DELIVERED,
            "delivered_by": run_id,
            _MESSAGE_ID: _delivery_id(input.get(_MESSAGE_ID)),
            "item_id": item_id,
        }
        for _ in range(_CAS_TRIES):
            doc = self._get(day)
            if doc is not None and doc.get("state") == DELIVERED:
                return {"recorded": doc.get("delivered_by") == run_id, "date": day}
            at = _iso(self._clock())
            library = {"item_id": item_id, "queue_id": _delivery_id(input.get("queue_id"))}
            stamped = {**changes, "delivered_at": at, "library": {**library, "at": at}}
            if self._cas(day, doc, stamped):
                return {"recorded": True, "date": day}
        raise self._contended(day)


def _claimed(day: str, reason: str) -> dict[str, Any]:
    deliver = reason in (CLAIMED, TAKEN_OVER)
    return {"deliver": deliver, "date": day, "reason": reason}
