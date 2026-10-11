"""``email.send`` action port: send one email through an email App actor (Resend).

The step's bound actor (``context.actor``) is a stored App actor (surface ``email``) whose
``params.connection`` carries ``api_key: grant:NAME``, ``domain``, ``local_part`` and
``recipients`` (the allowlist; an empty list refuses everyone). The from address is
``local_part@domain`` (:func:`culture_rules.model.app_actor.email_sender`). A recipient outside
the allowlist fails the action before any secret is read or any network call is made, and the
refusal names no secret. The key is resolved from ``grant`` on the executing host at call time
and kept only in memory.

Resend deduplicates on ``Idempotency-Key``, so the engine's idempotency key is forwarded and
``supports_idempotency_key`` is True: a retried send does not send a second email. The
engine's key is per run and step. An optional ``idempotency_key`` param (a non-empty string of
at most :data:`MAX_IDEMPOTENCY_KEY` characters, Resend's limit) replaces it: the daily
newspaper passes ``newspaper:<date>:email`` so that a *later run* for the same date - after a
takeover - cannot send that date's email twice (Codex r1 #1). Any other value is
``bad_input``. The output is ``{message_id}`` only.

An optional ``sender`` param replaces the actor's from address so that a resend replays the
*same* request (Codex r2 #4): Resend answers a reused key with a different body ``409``. The
daily newspaper freezes the actor's sender with the rest of the date's first request. It
must be an address at the actor's ``domain`` (case-insensitive; the domain Resend verified),
else ``sender_not_allowed`` (not retryable) before any secret is read; a non-string is
``bad_input``. The recipients are always checked against the actor's *current* allowlist: a
frozen recipient that was since removed fails ``recipient_not_allowed`` and is never
replaced.

An optional ``not_after`` param (an ISO-8601 time with a UTC offset) is the last moment a
request may go out (Codex r3): it is checked after the secret is resolved, immediately before
the request, on every attempt - a retry the engine resumes late included. At or past it the
send fails ``email_window_passed`` (not retryable) and makes no request. The daily newspaper
passes ``send_until``, the date's first attempt plus 23 hours: past it Resend may have
forgotten the idempotency key, so a resend could deliver a second email. A malformed value is
``bad_input`` before any secret is read.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.actors.secrets import resolve as resolve_secret
from culture_rules.apps.github import Transport
from culture_rules.apps.resend import ResendClient, ResendError
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.model.app_actor import email_sender
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = ["EmailSendPort"]

log = logging.getLogger(__name__)

_BAD_INPUT = "bad_input"
WINDOW_PASSED = "email_window_passed"
MAX_IDEMPOTENCY_KEY = 256


def _key(input: Mapping[str, Any], engine_key: str) -> str | None:
    """The ``idempotency_key`` param when given (None when it is unusable), else the engine's."""
    if "idempotency_key" not in input or input["idempotency_key"] is None:
        return engine_key
    key = input["idempotency_key"]
    if not isinstance(key, str) or not key.strip() or len(key) > MAX_IDEMPOTENCY_KEY:
        return None
    return key


def _recipients(value: Any) -> list[str] | None:
    """``to`` as a non-empty list of strings, else None."""
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    return value


def _all_allowed(to: list[str], conn: Mapping[str, Any]) -> bool:
    allowed = {str(a).lower() for a in conn.get("recipients") or ()}
    return all(addr.lower() in allowed for addr in to)


def _sender(input: Mapping[str, Any], conn: Mapping[str, Any]) -> str:
    """The from address: the ``sender`` param when given, else the actor's own.

    Raises ``ValueError`` with the failure code: ``bad_input`` or ``sender_not_allowed``."""
    sender = input.get("sender")
    if sender is None:
        return email_sender(conn)
    if not isinstance(sender, str) or sender.count("@") != 1 or sender.startswith("@"):
        raise ValueError(_BAD_INPUT)
    domain = email_sender(conn).rsplit("@", 1)[1]
    if sender.rsplit("@", 1)[1].lower() != domain.lower():
        raise ValueError("sender_not_allowed")
    return sender


def _not_after(value: Any) -> datetime | None:
    """``not_after`` as an aware time (None when absent). Raises ``ValueError`` (bad_input)."""
    if value is None:
        return None
    try:
        when = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        when = None
    if when is None or when.tzinfo is None:
        raise ValueError(_BAD_INPUT)
    return when


def _body_error(input: Mapping[str, Any]) -> str | None:
    """Why ``subject``/``html``/``text`` are unusable, or None (at least one body part)."""
    if not isinstance(input.get("subject"), str) or not input["subject"]:
        return _BAD_INPUT
    html, text = input.get("html"), input.get("text")
    if html is None and text is None:
        return _BAD_INPUT
    if any(part is not None and not isinstance(part, str) for part in (html, text)):
        return _BAD_INPUT
    return None


class EmailSendPort:
    """ActorPort for the ``email.send`` action kind."""

    supports_idempotency_key = True

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secrets: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport
        self._secrets = secrets
        self._clock = clock or (lambda: datetime.now(UTC))

    def _connection(self, actor_id: str | None) -> Mapping[str, Any] | None:
        if not actor_id:
            return None
        doc = self._store.get(ACTORS_COLLECTION, actor_id)
        if not doc or doc.get("enabled") is False:
            return None
        params = doc.get("params") or {}
        conn = params.get("connection")
        if params.get("surface") != "email" or not isinstance(conn, Mapping):
            return None
        return conn

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        conn = self._connection(context.actor or input.get("actor"))
        if conn is None:
            return InvocationResult.failed("actor_not_found", retryable=False)
        to = _recipients(input.get("to"))
        if to is None:
            return InvocationResult.failed(_BAD_INPUT, retryable=False)
        if not _all_allowed(to, conn):
            return InvocationResult.failed("recipient_not_allowed", retryable=False)
        error = _body_error(input)
        key = _key(input, idempotency_key)
        if error or key is None:
            return InvocationResult.failed(error or _BAD_INPUT, retryable=False)
        try:
            sender = _sender(input, conn)
            not_after = _not_after(input.get("not_after"))
        except ValueError as exc:
            return InvocationResult.failed(str(exc), retryable=False)
        try:
            token = (self._secrets or resolve_secret)(str(conn.get("api_key") or ""))
        except Exception:  # noqa: BLE001 - never echo secret text
            return InvocationResult.failed("secret_unavailable", retryable=False)
        if not_after is not None and self._clock() >= not_after:
            return InvocationResult.failed(WINDOW_PASSED, retryable=False)
        client = ResendClient(token=token, transport=self._transport)
        try:
            out = client.send_email(
                sender=sender,
                to=to,
                subject=input["subject"],
                html=input.get("html"),
                text=input.get("text"),
                idempotency_key=key,
            )
        except ResendError as exc:
            log.info("email.send failed: %s", exc.code)
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        return InvocationResult.completed({"message_id": out["message_id"]})
