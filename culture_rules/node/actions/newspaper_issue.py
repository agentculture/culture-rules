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
* ``email_payload`` - the date's first full email request ``{subject, html, to, sender}``
  (``to`` when the fence is given it, ``sender`` when ``config.email_actor`` names the email
  actor), replayed exactly by any later run for that date (Codex r2 #4);
* ``email_attempt`` - ``{at, key}``, written by the first ``before_email`` that answers
  ``send`` true, never moved (Codex r2 #1);
* ``email`` - ``{message_id, at}``, written as soon as the email is known sent, or
  ``{uncertain: true, attempted_at}`` when its acknowledgement was lost past the window;
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
* ``before_email`` (fence) - input ``subject`` and ``html`` (the rendered issue) and ``to``
  (the recipients); ``config.email_actor`` names the email actor whose from address is
  frozen. It renews the lease, refusing (``claim_lost``, not retryable: the run fails) when
  the date is no longer this run's. It stores the request when none is stored and answers
  ``{send, email_uncertain, date, subject, html, to, sender, idempotency_key, send_until}``
  with the **stored** request and the durable key ``newspaper:<date>:email``
  (:func:`email_key`), the same for every run of the date. ``send_until`` is the first
  attempt plus :data:`EMAIL_RETRY_WINDOW_S`; the email step passes it as ``email.send``'s
  ``not_after``, so every attempt - a retry resumed after an outage included - is bounded
  by the window, not only the fence (Codex r3). ``send`` is true for the first attempt (recorded in
  ``email_attempt``) and for a retry within :data:`EMAIL_RETRY_WINDOW_S` of it; false when
  the email is recorded sent (``message_id`` is then given too) or uncertain - past the
  window with no recorded message id it records ``email`` uncertain and answers
  ``email_uncertain`` true.
* ``before_push`` (fence) - input ``message_id`` (the email step's, absent when it was
  skipped). It records ``email`` the first time it sees a message id - even when the claim
  was lost, since the email went out either way - then renews the lease or refuses
  ``claim_lost``. With no message id and the first attempt past its window (the email step
  failed ``email_window_passed``, or failed before it) it records ``email`` uncertain; with
  no message id inside the window it refuses ``bad_input``, so a skipped email step
  (``on_error: skip``) that failed for another reason never passes as delivered. Output
  ``{message_id, date, email_uncertain}``: the recorded message id (null, with
  ``email_uncertain`` true, when the email is uncertain; the push proceeds).
* ``record`` (after delivery) - sets state ``delivered`` with ``message_id``, ``item_id``,
  ``library`` (``item_id``, ``queue_id``), ``delivered_by`` and ``delivered_at``, whoever
  claimed. Output ``{recorded, date}``: false when another run already recorded the date; a
  re-invoked record of the same run answers true again.

Why a date is never emailed twice (Codex r1 #1, r2 #1 and #4)
==============================================================

A run that sent the email and then failed (say xteink was down) leaves the date claimed with
``email`` recorded. A run that takes the date over after the lease sees ``email`` at its
``before_email`` fence, skips the email and retries only the push. Two narrow windows remain
and are both covered:

* the email went out but ``before_push`` never recorded it (the node died in between): a
  run within :data:`EMAIL_RETRY_WINDOW_S` (23 hours) of the first attempt sends again with
  the same durable key and the same stored request - recipients and sender included, so a
  changed ``newspaper_to`` or sender cannot make Resend answer ``409`` - and Resend answers
  the first message id instead of sending (it keeps keys for 24 hours). A later run does
  not resend: Resend may have forgotten the key, so the date is recorded ``uncertain`` and
  the run reports ``email_uncertain``. A missed email is preferred to a duplicate. The
  attempt is recorded at the fence, before the send, so an email step that failed before
  reaching Resend also ends uncertain once the window has passed;
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
from culture_rules.model.app_actor import email_sender
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = [
    "BEFORE_EMAIL",
    "BEFORE_PUSH",
    "CLAIM",
    "CLAIM_LOST",
    "DEFAULT_CLAIM_TIMEOUT_S",
    "DEFAULT_SUMMARY_WINDOW_S",
    "EMAIL_RETRY_WINDOW_S",
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
EMAIL_RETRY_WINDOW_S = 23 * 3600  # inside Resend's 24-hour idempotency window, with margin
_EMAIL = "email"
_PAYLOAD = "email_payload"
_ATTEMPT = "email_attempt"
_UNCERTAIN = "email_uncertain"
_MESSAGE_ID = "message_id"
_SUBJECT, _HTML, _TO, _SENDER = "subject", "html", "to", "sender"
_REQUEST_FIELDS = (_SUBJECT, _HTML, _TO, _SENDER)
_LOST_ACK = "uncertain"  # the email's state, and its flag in the ``email`` record
_SENT, _FIRST, _RETRY, _EXPIRED = "sent", "first", "retry", "expired"
_DATE, _STATE, _REV, _ITEM_ID = "date", "state", "rev", "item_id"
_CLAIMED_AT, _CLAIMED_BY = "claimed_at", "claimed_by"
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
    return doc is not None and doc.get(_STATE) == CLAIMED and doc.get(_CLAIMED_BY) == run_id


def _decide(doc: Mapping[str, Any] | None, run_id: str, now: datetime, timeout: int) -> str | None:
    """A claim's answer from the current document, or None to (re)claim it."""
    if doc is None:
        return None
    if doc.get(_STATE) == DELIVERED:
        return "already_delivered"
    if doc.get(_CLAIMED_BY) == run_id:
        return CLAIMED
    age = _age_s(doc.get(_CLAIMED_AT), now)
    if age is not None and age < timeout:
        return BY_ANOTHER_RUN
    return None


def _request(input: Mapping[str, Any]) -> dict[str, Any]:
    """This run's email request from the fence's input: ``subject``, ``html`` and, when
    given, ``to`` (a non-empty list of addresses)."""
    subject, html = input.get(_SUBJECT), input.get(_HTML)
    if not isinstance(subject, str) or not subject or not isinstance(html, str) or not html:
        raise _Refusal("bad_input: subject and html: required, non-empty strings")
    request: dict[str, Any] = {_SUBJECT: subject, _HTML: html}
    to = input.get(_TO)
    if to is not None:
        if not isinstance(to, list) or not to or not all(isinstance(a, str) and a for a in to):
            raise _Refusal("bad_input: to: a non-empty list of addresses")
        request[_TO] = list(to)
    return request


def _stored_request(doc: Mapping[str, Any]) -> dict[str, Any] | None:
    """The date's frozen email request (the first fence's), or None when none is stored."""
    stored = doc.get(_PAYLOAD)
    if not isinstance(stored, Mapping):
        return None
    subject, html = stored.get(_SUBJECT), stored.get(_HTML)
    if not (isinstance(subject, str) and subject and isinstance(html, str) and html):
        return None
    return {k: stored[k] for k in _REQUEST_FIELDS if stored.get(k) is not None}


def _uncertain(doc: Mapping[str, Any] | None) -> bool:
    """Whether the date's email is recorded uncertain (its acknowledgement was lost)."""
    email = (doc or {}).get(_EMAIL)
    return isinstance(email, Mapping) and email.get(_LOST_ACK) is True


def _email_state(doc: Mapping[str, Any], now: datetime) -> str:
    """Where the date's email stands at a fence (module doc, Codex r2 #1)."""
    if _sent_id(doc) is not None:
        return _SENT
    if _uncertain(doc):
        return _LOST_ACK
    attempt = doc.get(_ATTEMPT)
    if attempt is None:
        return _FIRST
    stamp = attempt.get("at") if isinstance(attempt, Mapping) else None
    age = _age_s(stamp, now)
    return _RETRY if age is not None and age < EMAIL_RETRY_WINDOW_S else _EXPIRED


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
                output = self._before_email(day, run_id, _request(input), config)
            elif phase == BEFORE_PUSH:
                output = self._before_push(day, run_id, _delivery_id(input.get(_MESSAGE_ID)))
            else:
                output = self._record(day, run_id, input)
        except _Refusal as refusal:
            return refusal.result
        return InvocationResult.completed(output)

    # ------------------------------------------------------------------ store access

    def _cas(self, day: str, doc: Mapping[str, Any] | None, changes: dict[str, Any]) -> bool:
        rev = (doc or {}).get(_REV)
        changes = {**changes, _DATE: day, _REV: (rev or 0) + 1}
        result = self._store.update_if(
            ISSUES_COLLECTION, day, {_REV: rev}, changes, upsert=doc is None
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
            changes = {_STATE: CLAIMED, _CLAIMED_BY: run_id, _CLAIMED_AT: _iso(now)}
            if self._cas(day, doc, changes):
                return _claimed(day, TAKEN_OVER if doc else CLAIMED)
        return _claimed(day, BY_ANOTHER_RUN)

    def _sender(self, config: Mapping[str, Any]) -> str | None:
        """The from address of ``config.email_actor`` (None when not configured)."""
        actor_id = config.get("email_actor")
        if actor_id is None:
            return None
        doc = self._store.get(ACTORS_COLLECTION, actor_id) if isinstance(actor_id, str) else None
        conn = ((doc or {}).get("params") or {}).get("connection")
        if not isinstance(conn, Mapping):
            raise _Refusal(f"bad_input: email_actor: {actor_id!r} is not a stored email actor")
        return email_sender(conn)

    def _before_email(
        self, day: str, run_id: str, request: dict[str, Any], config: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Fence the email: renew the lease, freeze the first request, decide whether to send."""
        for _ in range(_CAS_TRIES):
            doc = self._get(day)
            if not _owned(doc, run_id):
                raise _lost(day)
            now = self._clock()
            stored = _stored_request(doc)
            changes: dict[str, Any] = {_CLAIMED_AT: _iso(now)}
            if stored is None:
                sender = self._sender(config)
                stored = {**request, _SENDER: sender} if sender else dict(request)
                changes[_PAYLOAD] = stored
            state = _email_state(doc, now)
            changes.update(_email_changes(doc, state, day, now))
            if not self._cas(day, doc, changes):
                continue
            output: dict[str, Any] = {
                _DATE: day,
                "idempotency_key": email_key(day),
                **request,
                **stored,
                "send": state in (_FIRST, _RETRY),
                _UNCERTAIN: state in (_LOST_ACK, _EXPIRED),
            }
            if state == _SENT:
                output[_MESSAGE_ID] = _sent_id(doc)
            until = _send_until(doc, state, now)
            if until is not None:
                output["send_until"] = until
            return output
        raise self._contended(day)

    def _before_push(self, day: str, run_id: str, message_id: str | None) -> dict[str, Any]:
        """Record the sent email (whoever holds the date), then fence the push."""
        for _ in range(_CAS_TRIES):
            doc = self._get(day)
            owned = _owned(doc, run_id)
            recorded = _sent_id(doc)
            changes: dict[str, Any] = {}
            if doc is not None and recorded is None:
                changes.update(_push_email_changes(doc, day, message_id, self._clock()))
            if owned:
                changes[_CLAIMED_AT] = _iso(self._clock())
            if changes and not self._cas(day, doc, changes):
                continue
            if not owned:
                raise _lost(day)
            sent = recorded or message_id
            uncertain = sent is None and _uncertain({**(doc or {}), **changes})
            if sent is None and not uncertain:
                raise _Refusal(
                    "bad_input: message_id: no email is recorded sent for the date"
                    " (see the email step's error)"
                )
            return {_MESSAGE_ID: sent, _DATE: day, _UNCERTAIN: uncertain}
        raise self._contended(day)

    def _record(self, day: str, run_id: str, input: Mapping[str, Any]) -> dict[str, Any]:
        item_id = _delivery_id(input.get(_ITEM_ID))
        changes = {
            _STATE: DELIVERED,
            "delivered_by": run_id,
            _MESSAGE_ID: _delivery_id(input.get(_MESSAGE_ID)),
            _ITEM_ID: item_id,
        }
        for _ in range(_CAS_TRIES):
            doc = self._get(day)
            if doc is not None and doc.get(_STATE) == DELIVERED:
                return {"recorded": doc.get("delivered_by") == run_id, _DATE: day}
            at = _iso(self._clock())
            library = {_ITEM_ID: item_id, "queue_id": _delivery_id(input.get("queue_id"))}
            stamped = {**changes, "delivered_at": at, "library": {**library, "at": at}}
            if self._cas(day, doc, stamped):
                return {"recorded": True, _DATE: day}
        raise self._contended(day)


def _email_changes(doc: Mapping[str, Any], state: str, day: str, now: datetime) -> dict[str, Any]:
    """The fence's writes for the email: the first attempt's time, or the uncertain record."""
    if state == _FIRST:
        return {_ATTEMPT: {"at": _iso(now), "key": email_key(day)}}
    if state == _EXPIRED:
        attempt = doc.get(_ATTEMPT)
        at = attempt.get("at") if isinstance(attempt, Mapping) else None
        return {_EMAIL: {_LOST_ACK: True, "attempted_at": at}}
    return {}


def _attempt_at(doc: Mapping[str, Any]) -> datetime | None:
    attempt = doc.get(_ATTEMPT)
    stamp = attempt.get("at") if isinstance(attempt, Mapping) else None
    try:
        return datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
    except ValueError:
        return None


def _send_until(doc: Mapping[str, Any], state: str, now: datetime) -> str | None:
    """The last moment the email step may send (Codex r3): the date's first attempt plus
    :data:`EMAIL_RETRY_WINDOW_S`, frozen like the attempt (None without an attempt)."""
    at = now if state == _FIRST else _attempt_at(doc)
    return None if at is None else _iso(at + timedelta(seconds=EMAIL_RETRY_WINDOW_S))


def _push_email_changes(
    doc: Mapping[str, Any], day: str, message_id: str | None, now: datetime
) -> dict[str, Any]:
    """``before_push``'s write for a date whose email is not recorded sent: the step's message
    id, or - with none, the attempt past its window (the email step refused
    ``email_window_passed``, or failed before it) - the uncertain record (Codex r3)."""
    if message_id is not None:
        return {_EMAIL: {_MESSAGE_ID: message_id, "at": _iso(now)}}
    if not _uncertain(doc) and _email_state(doc, now) == _EXPIRED:
        return _email_changes(doc, _EXPIRED, day, now)
    return {}


def _claimed(day: str, reason: str) -> dict[str, Any]:
    deliver = reason in (CLAIMED, TAKEN_OVER)
    return {"deliver": deliver, _DATE: day, "reason": reason}
