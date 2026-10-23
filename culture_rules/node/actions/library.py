"""``library.push`` action port: upload an EPUB to the xteink library and queue it (#53).

Params (``culture_rules.model.action_kinds``): ``actor`` (falls back to ``context.actor``),
``content_base64`` (the EPUB bytes, base64), ``title``, optional ``author`` (default
``culture-rules``), optional ``device_id`` (overrides the actor's) and optional ``kind``
(``book``, ``article`` or ``transcript``; default ``article``; anything else fails
``invalid_request``).

The bound actor is a ``service`` actor (not an ``app`` actor: xteink has no app surface)
whose params carry the connection::

    {"base_url": "http://<xteink host>:8780",
     "api_key": "grant:XTEINK_NEWSPAPER",
     "device_id": 3}

``base_url`` is http or https with a host and no credentials, query or fragment (a path
prefix is kept). ``api_key`` must be a ``grant:<NAME>`` reference to a dedicated, revocable
``xtk_`` key; a literal key is refused and never echoed. The reference is resolved at call
time on the executing host (:mod:`culture_rules.actors.secrets`). ``device_id`` (optional
here when every action names one) is a positive integer, or its decimal string. Everything
is checked before any request is made.

The push is two calls, each with ``Authorization: Bearer <key>``:

1. ``POST <base_url>/api/library``, ``multipart/form-data`` built here with a random
   boundary: ``file`` (named after the title, e.g. ``daily-newspaper-2026-10-11.epub``,
   ``application/epub+zip``), ``title``, ``author`` and ``kind``
   (``article`` unless ``params.kind`` says otherwise). xteink answers 201
   ``{"item": {"id"}, "created": true}`` or 200 with ``created: false`` when
   the same bytes are already stored.
2. Only after a 2xx upload: ``POST <base_url>/api/devices/<device_id>/queue`` with
   ``{"item_id": <id>}``, answered 201 with the queue entry.

Outputs ``{item_id, created, queue_id}``.

Failures: content that is not valid base64, empty, or over 50 MiB decoded (``too_large``),
a missing title, a bad device id or actor config fail before any request, never retryable.
A non-2xx upload queues nothing: it fails ``upload_failed`` naming the status and xteink's
error ``code`` (never its ``detail`` text); 5xx, 408, 425 and 429 are retryable, any other
status is not, and a redirect is never followed. Network errors and timeouts are
``network_error``, retryable. A failed queue call (``queue_failed``, 404 for an unknown
device or item) names the uploaded item id and follows the same retry rules.

``supports_idempotency_key`` is True: a retry with the same key repeats the same two calls
with the same bytes, and xteink makes both idempotent - the upload deduplicates by sha256
(the existing item comes back with ``created: false``) and queueing an item that is still
queued returns the existing entry. The one repeat that is not a no-op: if the device has
already synced the item between the lost acknowledgement and the retry, xteink queues it
again and the device downloads the same book once more - benign for a reader. A completed
result is also replayed in-process for the same key (the last :data:`LEDGER_SIZE` ones).

Each call's timeout is what remains of the attempt deadline when it starts (capped at
:data:`MAX_TIMEOUT`): the queue call recomputes it after the upload, so a slow upload cannot
carry the queue call past the deadline. With no time left before either call the attempt
fails ``deadline_passed``, retryable. That is safe after an upload: the retry uploads the
same bytes again, which xteink deduplicates by sha256 (``created: false``, the same item),
and then queues it. Environment proxies are ignored. Nothing is logged. Standard-library only.
"""

from __future__ import annotations

import base64
import http.client
import json
import re
import secrets
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

from culture_rules.actors.secrets import SecretError, is_secret_ref, resolve
from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.node.actions._ledger import DEFAULT_LEDGER_SIZE, ReplayLedger
from culture_rules.node.actions.http import RedirectRefused
from culture_rules.node.actions.machine import load_bound_actor

__all__ = [
    "DEFAULT_AUTHOR",
    "KINDS",
    "LibraryPushPort",
    "MAX_BYTES",
    "Transport",
    "encode_multipart",
    "epub_filename",
    "urllib_transport",
]

MAX_BYTES = 50 * 1024 * 1024
"""xteink's upload limit; larger content is refused before any request."""
MAX_TIMEOUT = 120.0
MAX_REPLY = 1024 * 1024
LEDGER_SIZE = DEFAULT_LEDGER_SIZE
"""How many completed pushes the port keeps for replay (the last ones)."""
DEFAULT_AUTHOR = "culture-rules"
KIND = "article"
KINDS = ("book", "article", "transcript")
EPUB_TYPE = "application/epub+zip"
ACTOR_KIND = "service"
_RETRYABLE_4XX = frozenset({408, 425, 429})
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DEVICE_RE = re.compile(r"^[1-9]\d{0,17}$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_UNSAFE_HEADER_TEXT = re.compile(r'["\r\n]')
_INVALID_REQUEST = "invalid_request"
_INVALID_ACTOR = "invalid_actor"
_BAD_RESPONSE = "bad_response"
_NETWORK_ERROR = "network_error"

Transport = Callable[[str, str, Mapping[str, str], bytes, float], tuple[int, bytes]]
"""``transport(method, url, headers, body, timeout) -> (status, reply bytes)``.

A non-2xx answer is returned, not raised; network failures raise ``OSError`` (or
``http.client.HTTPException``)."""


# ------------------------------------------------------------------ multipart


def _header_text(text: str) -> str:
    if _UNSAFE_HEADER_TEXT.search(text):
        raise ValueError("multipart names cannot contain quotes or line breaks")
    return text


def encode_multipart(
    fields: Sequence[tuple[str, str]],
    files: Sequence[tuple[str, str, str, bytes]],
) -> tuple[str, bytes]:
    """Encode ``fields`` (name, value) and ``files`` (name, filename, type, bytes).

    Returns ``(content_type, body)``. The boundary is random and checked to be absent from
    every value."""
    parts: list[tuple[str, bytes]] = []
    for name, value in fields:
        head = f'Content-Disposition: form-data; name="{_header_text(name)}"'
        parts.append((head, value.encode("utf-8")))
    for name, filename, content_type, data in files:
        head = (
            f'Content-Disposition: form-data; name="{_header_text(name)}"; '
            f'filename="{_header_text(filename)}"\r\n'
            f"Content-Type: {_header_text(content_type)}"
        )
        parts.append((head, data))
    boundary = _boundary([value for _head, value in parts])
    delimiter = b"--" + boundary.encode("ascii")
    body = bytearray()
    for head, value in parts:
        body += delimiter + b"\r\n" + head.encode("utf-8") + b"\r\n\r\n" + value + b"\r\n"
    body += delimiter + b"--\r\n"
    return f"multipart/form-data; boundary={boundary}", bytes(body)


def _boundary(values: Sequence[bytes]) -> str:
    while True:
        boundary = "culture-rules-" + secrets.token_hex(16)
        marker = boundary.encode("ascii")
        if not any(marker in value for value in values):
            return boundary


def epub_filename(title: str) -> str:
    """A safe ``.epub`` filename from ``title`` (``Daily newspaper 2026-10-11`` ->
    ``daily-newspaper-2026-10-11.epub``)."""
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")[:100].strip("-")
    return f"{slug or 'upload'}.epub"


# ------------------------------------------------------------------ the transport


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, *_: Any, **__: Any) -> Any:
        raise RedirectRefused(code)


def _opener() -> urllib.request.OpenerDirector:
    """An opener with no proxy handler and no redirect following."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        _NoRedirect(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    """The default :data:`Transport` (stdlib ``urllib``; the url is checked by the port)."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        response = _opener().open(request, timeout=timeout)  # nosec B310 - http(s) only
    except RedirectRefused as exc:
        return exc.status, b""
    except urllib.error.HTTPError as exc:
        response = exc  # a non-2xx answer: the HTTPError is also the response
    try:
        status = int(getattr(response, "status", None) or getattr(response, "code", 0))
        return status, response.read(MAX_REPLY)
    finally:
        response.close()


# ------------------------------------------------------------------ the port


class _Refused(Exception):
    def __init__(self, code: str, detail: str, *, retryable: bool = False):
        super().__init__(f"{code}: {detail}")
        self.retryable = retryable

    def result(self) -> InvocationResult:
        return InvocationResult.failed(str(self), retryable=self.retryable)


def _base_url(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _Refused(_INVALID_ACTOR, "base_url must be a non-empty string")
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError as exc:
        raise _Refused(_INVALID_ACTOR, "base_url is malformed") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise _Refused(_INVALID_ACTOR, "base_url must be http or https")
    if not parts.hostname:
        raise _Refused(_INVALID_ACTOR, "base_url has no host")
    if parts.username is not None or parts.password is not None:
        raise _Refused(_INVALID_ACTOR, "base_url must not carry credentials")
    if parts.query or parts.fragment:
        raise _Refused(_INVALID_ACTOR, "base_url must not carry a query or fragment")
    return value.rstrip("/")


def _device(value: Any) -> str | None:
    """The device id as a path segment (digits only), ``None`` when unset."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not _DEVICE_RE.match(value):
        raise _Refused(_INVALID_REQUEST, "device_id must be a positive integer")
    return value


def _content(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise _Refused(_INVALID_REQUEST, "content_base64 must be a non-empty base64 string")
    if len(value) > (MAX_BYTES + 2) // 3 * 4 + 4:
        raise _Refused("too_large", f"content exceeds {MAX_BYTES} bytes")
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError as exc:  # binascii.Error is a ValueError
        raise _Refused(_INVALID_REQUEST, "content_base64 is not valid base64") from exc
    if not data:
        raise _Refused(_INVALID_REQUEST, "content_base64 decodes to nothing")
    if len(data) > MAX_BYTES:
        raise _Refused("too_large", f"content exceeds {MAX_BYTES} bytes")
    return data


def _text(input: Mapping[str, Any], name: str, default: str | None = None) -> str:
    value = input.get(name)
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise _Refused(_INVALID_REQUEST, f"{name} must be a non-empty string")
    return value


def _kind(input: Mapping[str, Any]) -> str:
    value = input.get("kind")
    if value is None:
        return KIND
    if not isinstance(value, str) or value not in KINDS:
        raise _Refused(_INVALID_REQUEST, f"kind must be one of {', '.join(KINDS)}")
    return value


def _status_failure(stage: str, status: int, raw: bytes, extra: str = "") -> _Refused:
    """A non-2xx answer: xteink's error ``code`` is named when it looks like one."""
    detail = f"http {status}"
    code = _error_code(raw)
    if code:
        detail += f" {code}"
    retryable = status >= 500 or status in _RETRYABLE_4XX
    return _Refused(stage, detail + extra, retryable=retryable)


def _error_code(raw: bytes) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    code = data.get("code") if isinstance(data, dict) else None
    return code if isinstance(code, str) and _CODE_RE.match(code) else ""


def _json_reply(raw: bytes, what: str) -> Mapping[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise _Refused(_BAD_RESPONSE, f"{what} reply is not JSON") from exc
    if not isinstance(data, dict):
        raise _Refused(_BAD_RESPONSE, f"{what} reply is not an object")
    return data


def _int_id(value: Any, what: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise _Refused(_BAD_RESPONSE, f"{what} reply has no integer id")


class LibraryPushPort:
    """ActorPort for ``library.push`` (see the module docstring)."""

    supports_idempotency_key = True  # xteink dedupes the upload and the queue (module doc)

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secret_runner: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport or urllib_transport
        self._secret_runner = secret_runner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ledger = ReplayLedger(LEDGER_SIZE)

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        replayed = self._ledger.get(idempotency_key)
        if replayed is not None:
            return replayed
        actor, _doc, failure = load_bound_actor(self._store, input, context)
        if failure is not None or actor is None:
            return failure or InvocationResult.failed("actor_not_found", retryable=False)
        if actor.kind != ACTOR_KIND:
            return InvocationResult.failed(
                f"actor_kind_mismatch: library.push needs a {ACTOR_KIND} actor, "
                f"{actor.id!r} is {actor.kind!r}",
                retryable=False,
            )
        try:
            result = self._push(input, actor.params, deadline)
        except _Refused as exc:
            return exc.result()
        if result.outcome == COMPLETED:
            self._ledger.put(idempotency_key, result)
        return result

    def _remaining(self, deadline: datetime, when: str) -> float:
        """Seconds left of the attempt (capped at :data:`MAX_TIMEOUT`), checked before each
        call; none left fails ``deadline_passed``, retryable (module doc)."""
        timeout = min((deadline - self._clock()).total_seconds(), MAX_TIMEOUT)
        if timeout <= 0:
            raise _Refused("deadline_passed", f"attempt deadline passed {when}", retryable=True)
        return timeout

    def _key(self, ref: Any) -> str:
        if not is_secret_ref(ref):
            raise _Refused(_INVALID_ACTOR, "api_key must be a grant:<NAME> reference")
        try:
            return resolve(ref, self._secret_runner)
        except SecretError as exc:
            raise _Refused("secret_unresolved", "api_key") from exc

    def _push(
        self, input: Mapping[str, Any], params: Mapping[str, Any], deadline: datetime
    ) -> InvocationResult:
        base = _base_url(params.get("base_url"))
        device = _device(input.get("device_id")) or _device(params.get("device_id"))
        if device is None:
            raise _Refused("device_missing", "no device_id on the action or the actor")
        data = _content(input.get("content_base64"))
        title = _text(input, "title")
        author = _text(input, "author", DEFAULT_AUTHOR)
        kind = _kind(input)
        timeout = self._remaining(deadline, "before the upload")
        auth = {"Authorization": "Bearer " + self._key(params.get("api_key"))}
        content_type, body = encode_multipart(
            [("title", title), ("author", author), ("kind", kind)],
            [("file", epub_filename(title), EPUB_TYPE, data)],
        )
        status, raw = self._send(
            base + "/api/library", {**auth, "Content-Type": content_type}, body, timeout
        )
        if not 200 <= status < 300:
            raise _status_failure("upload_failed", status, raw)
        reply = _json_reply(raw, "upload")
        item = reply.get("item")
        item_id = _int_id(item.get("id") if isinstance(item, dict) else None, "upload")
        queue_url = f"{base}/api/devices/{quote(device, safe='')}/queue"
        queue_body = json.dumps({"item_id": item_id}).encode("utf-8")
        headers = {**auth, "Content-Type": "application/json"}
        timeout = self._remaining(deadline, f"after the upload (item {item_id} uploaded)")
        status, raw = self._send(queue_url, headers, queue_body, timeout)
        if not 200 <= status < 300:
            raise _status_failure("queue_failed", status, raw, f" (item {item_id} uploaded)")
        queue_id = _int_id(_json_reply(raw, "queue").get("id"), "queue")
        return InvocationResult.completed(
            {"item_id": item_id, "created": bool(reply.get("created")), "queue_id": queue_id}
        )

    def _send(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> tuple[int, bytes]:
        try:
            return self._transport("POST", url, headers, body, timeout)
        except (http.client.HTTPException, OSError) as exc:  # URLError/timeouts are OSError
            raise _Refused(_NETWORK_ERROR, type(exc).__name__, retryable=True) from exc
