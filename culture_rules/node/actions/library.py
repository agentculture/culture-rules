"""``library.push`` action port: upload a file (an EPUB by default) to a library, queue it when
the library has a device queue, or replace an existing item by id (#53, culture-rules#86).

Params (``culture_rules.model.action_kinds``): ``actor`` (falls back to ``context.actor``),
``content_base64`` (the file bytes, base64) or, instead (exactly one of the two),
``content_path`` with ``content_root`` (d7: absolute paths; at most 50 MiB, else
``too_large``; the bytes are read here, so they never enter run state), ``title``, ``kind``
(one of the actor's
``kinds``), optional ``visibility`` (one of the actor's ``visibilities``), optional
``author`` (default ``culture-rules``) and optional ``device_id`` (overrides the actor's;
used, and checked, only when the actor has a ``queue_path``: a library without a device
queue ignores it, so a leftover value never blocks a push), optional ``format`` and optional
``item_id``. A kind or visibility the actor does not list fails ``invalid_request``.

Reading a file: the library actor, not the step, decides which directories a push may read.
A ``content_path`` push is refused ``path_refused`` unless the actor lists ``content_roots``
and the step's ``content_root``, resolved (``os.path.realpath``), is one of them or under
one of them (each listed root resolved too). The ``content_path`` may be given under the
root as written or under its resolved form (``attachment.fetch`` reports resolved paths);
either way the file must be a regular file below the resolved root with no symlink between
that root and the file (the file is opened ``O_NOFOLLOW``), else ``path_refused``. A
``content_base64`` push never reads a file and ignores ``content_roots``.

Formats: the actor's optional ``formats`` is a non-empty list of the words ``md``, ``txt``,
``json``, ``epub``, ``pdf`` (types ``text/markdown``, ``text/plain``, ``application/json``,
``application/epub+zip``, ``application/pdf``). The step's ``format`` must be one the actor
lists; without ``format`` the file is an EPUB, so an actor that lists ``formats`` without
``epub`` refuses a step with no format, and an actor with no ``formats`` accepts only EPUB
(a ``format`` of ``epub`` or none). The filename is the title slug plus ``.<format>``.

Replace: with ``item_id`` (a positive integer) the bytes go to the actor's ``replace_path``
(an absolute path of plain segments holding ``{item_id}`` as exactly one whole segment) with
the actor's ``replace_method`` (``PUT``, ``POST`` or ``PATCH``; there is no default, the actor
must say). ``item_id`` without both fails ``invalid_actor`` before any request. A replace has
no queue step, and answers ``{"item": {"id"}}`` or the item itself (``{"id"}``, as
culture-library does) with the same id (else ``bad_response``). The
route belongs to the library: agentculture/culture-library#10 defines it; nothing about it is
built in here.

What stays fixed is the wire contract both known libraries share (xteink's and
culture-library's): the multipart field names, the ``{"item": {"id"}, "created"}`` upload
reply and the ``{"item_id"}`` queue body and ``{"id"}`` reply. A library with another
contract needs code, not configuration.

The bound actor is a ``service`` actor whose params describe the library; nothing about it
is built into this module::

    {"base_url": "http://127.0.0.1:8796",
     "upload_path": "/api/library",
     "kinds": ["newspaper", "transcript"],
     "visibilities": ["private", "public"],
     "api_key": "grant:LIBRARY_PUSH_API_KEY"}

and, for a library with a device queue (xteink's), also
``"queue_path": "/api/devices/{device_id}/queue"`` and an optional ``device_id``; for
``content_path`` pushes, ``"content_roots"``, e.g. the node's attachments root
``["/home/<user>/.local/share/culture-rules/attachments"]``.

``base_url`` is http or https with a host and no credentials, query or fragment (a path
prefix is kept). ``upload_path`` (required), ``queue_path`` and ``replace_path`` (optional)
are absolute paths of plain segments; ``queue_path`` holds ``{device_id}`` as exactly one
whole segment, ``replace_path`` holds ``{item_id}`` likewise.
``kinds`` (required) and ``visibilities`` (optional; without it no visibility may be sent)
are non-empty lists of lowercase words. ``content_roots`` (optional) is a non-empty list of
absolute directory paths. ``api_key`` must be a ``grant:<NAME>`` reference to
a dedicated, revocable key; a literal key is refused and never echoed. The reference is
resolved at call time on the executing host (:mod:`culture_rules.actors.secrets`).
``device_id`` is a positive integer, or its decimal string. Everything is checked before
any request is made.

The push is one or two calls, each with ``Authorization: Bearer <key>``:

1. ``POST <base_url><upload_path>`` (or, for a replace, ``<replace_method>
   <base_url><replace_path>``), ``multipart/form-data`` built here with a random
   boundary: ``file`` (named after the title, e.g. ``daily-newspaper-2026-10-11.epub``,
   typed by the format, ``application/epub+zip`` by default), ``title``, ``author``, ``kind``
   and, when given, ``visibility``. The library answers 201
   ``{"item": {"id"}, "created": true}`` or 200 with ``created: false`` when the same bytes
   are already stored.
2. Only with a ``queue_path`` and only after a 2xx upload: ``POST <base_url><queue_path>``
   (``{device_id}`` filled in) with ``{"item_id": <id>}``, answered 201 with the queue
   entry.

Outputs ``{item_id, created, queue_id}``; ``queue_id`` is ``None`` without a queue step (and
on a replace, whose ``item_id`` is the one given).

Failures: content that is not valid base64, empty, or over 50 MiB decoded (``too_large``),
a missing title or kind, a bad device id or actor config fail before any request, never
retryable. A non-2xx upload queues nothing: it fails ``upload_failed`` naming the status
and the library's error ``code`` (never its ``detail`` text); 5xx, 408, 425 and 429 are
retryable, any other status is not, and a redirect is never followed. Network errors and
timeouts are ``network_error``, retryable. A failed queue call (``queue_failed``, 404 for an
unknown device or item) names the uploaded item id and follows the same retry rules.

``supports_idempotency_key`` is True: a retry with the same key repeats the same calls with
the same bytes, and the library makes them idempotent - the upload deduplicates by sha256
(the existing item comes back with ``created: false``) and queueing an item that is still
queued returns the existing entry. The one repeat that is not a no-op: if the device has
already synced the item between the lost acknowledgement and the retry, xteink queues it
again and the device downloads the same book once more - benign for a reader. A completed
result is also replayed in-process for the same key (the last :data:`LEDGER_SIZE` ones).

Each call's timeout is what remains of the attempt deadline when it starts (capped at
:data:`MAX_TIMEOUT`): the queue call recomputes it after the upload, so a slow upload cannot
carry the queue call past the deadline. With no time left before either call the attempt
fails ``deadline_passed``, retryable. That is safe after an upload: the retry uploads the
same bytes again, which the library deduplicates by sha256 (``created: false``, the same
item), and then queues it. Environment proxies are ignored. Nothing is logged.
Standard-library only.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import secrets
import stat
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from culture_rules.actors.secrets import SecretError, is_secret_ref, resolve
from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.node.actions._ledger import DEFAULT_LEDGER_SIZE, ReplayLedger
from culture_rules.node.actions.http import RedirectRefused
from culture_rules.node.actions.machine import load_bound_actor

__all__ = [
    "DEFAULT_AUTHOR",
    "LibraryPushPort",
    "MAX_BYTES",
    "Transport",
    "encode_multipart",
    "epub_filename",
    "item_filename",
    "urllib_transport",
]

MAX_BYTES = 50 * 1024 * 1024
"""The upload limit (xteink's and culture-library's); larger content is refused before any
request."""
MAX_TIMEOUT = 120.0
MAX_REPLY = 1024 * 1024
LEDGER_SIZE = DEFAULT_LEDGER_SIZE
"""How many completed pushes the port keeps for replay (the last ones)."""
DEFAULT_AUTHOR = "culture-rules"
EPUB_TYPE = "application/epub+zip"
ACTOR_KIND = "service"
_RETRYABLE_4XX = frozenset({408, 425, 429})
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DEVICE_RE = re.compile(r"^[1-9]\d{0,17}$")
_WORD_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_~-][A-Za-z0-9._~-]*$")
_DEVICE_SLOT = "{device_id}"
_ITEM_SLOT = "{item_id}"
FORMAT_TYPES = {
    "md": "text/markdown",
    "txt": "text/plain",
    "json": "application/json",
    "epub": EPUB_TYPE,
    "pdf": "application/pdf",
}
"""The formats a library actor may list, and the type each is uploaded as."""
DEFAULT_FORMAT = "epub"
REPLACE_METHODS = ("PUT", "POST", "PATCH")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_UNSAFE_HEADER_TEXT = re.compile(r'["\r\n]')
_INVALID_REQUEST = "invalid_request"
_PATH_REFUSED = "path_refused"
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


def item_filename(title: str, fmt: str = DEFAULT_FORMAT) -> str:
    """A safe filename from ``title`` and ``fmt`` (``Daily newspaper 2026-10-11``, ``md`` ->
    ``daily-newspaper-2026-10-11.md``)."""
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")[:100].strip("-")
    return f"{slug or 'upload'}.{fmt}"


def epub_filename(title: str) -> str:
    """A safe ``.epub`` filename from ``title`` (``Daily newspaper 2026-10-11`` ->
    ``daily-newspaper-2026-10-11.epub``)."""
    return item_filename(title, DEFAULT_FORMAT)


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


def _content_roots(value: Any) -> tuple[str, ...] | None:
    """The actor's ``content_roots`` (absolute directory paths), ``None`` when unset."""
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and os.path.isabs(v) for v in value)
    ):
        raise _Refused(_INVALID_ACTOR, "content_roots must be a non-empty list of absolute paths")
    return tuple(value)


def _allowed_root(root_value: str, allowed: tuple[str, ...] | None) -> Path:
    """``root_value`` resolved, if it is one of the actor's ``allowed`` roots or under one
    (all compared resolved), else ``path_refused``."""
    if allowed is None:
        raise _Refused(_PATH_REFUSED, "the library actor lists no content_roots")
    root = Path(os.path.realpath(root_value))
    for listed in allowed:
        if root.is_relative_to(os.path.realpath(listed)):
            return root
    raise _Refused(_PATH_REFUSED, "content_root is not under the library actor's content_roots")


def _relative(path_value: str, root_value: str, root: Path) -> Path:
    """``path_value`` relative to the root, given under the root as written or resolved."""
    path = Path(os.path.normpath(path_value))
    for base in (Path(os.path.normpath(root_value)), root):
        if path != base and path.is_relative_to(base):
            return path.relative_to(base)
    raise _Refused(_PATH_REFUSED, "content_path is not a file under content_root")


def _read_under(path_value: Any, root_value: Any, allowed: tuple[str, ...] | None) -> bytes:
    """The bytes of the regular file at ``path_value``, which must lie under ``root_value``
    (both absolute), itself within the actor's ``allowed`` roots. A symlink anywhere below the
    resolved root (the file or a parent) is refused ``path_refused``; the file is capped at
    ``MAX_BYTES`` (``too_large``)."""
    for value, label in ((root_value, "content_root"), (path_value, "content_path")):
        if not isinstance(value, str) or not os.path.isabs(value):
            raise _Refused(_PATH_REFUSED, f"{label} must be an absolute path")
    try:
        root = _allowed_root(root_value, allowed)
        current = root
        for part in _relative(path_value, root_value, root).parts:
            current = current / part
            if current.is_symlink():
                raise _Refused(_PATH_REFUSED, "content_path must not contain a symlink")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(current, flags)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise _Refused(_PATH_REFUSED, "content_path is not a regular file")
            data = handle.read(MAX_BYTES + 1)
    except (OSError, RuntimeError) as exc:
        raise _Refused(_PATH_REFUSED, "content_path is not a readable file under the root") from exc
    if len(data) > MAX_BYTES:
        raise _Refused("too_large", f"content_path file exceeds {MAX_BYTES} bytes")
    if not data:
        raise _Refused(_INVALID_REQUEST, "content_path file is empty")
    return data


def _source(input: Mapping[str, Any], allowed: tuple[str, ...] | None) -> bytes:
    """The file bytes: ``content_base64`` or, instead, ``content_path`` with ``content_root``
    (a root within the actor's ``allowed`` content roots)."""
    b64, path, root = (input.get(n) for n in ("content_base64", "content_path", "content_root"))
    if path is None:
        if root is not None:
            raise _Refused(_INVALID_REQUEST, "content_root needs content_path")
        return _content(b64)
    if b64 is not None:
        raise _Refused(_INVALID_REQUEST, "give content_base64 or content_path, not both")
    if root is None:
        raise _Refused(_INVALID_REQUEST, "content_path needs content_root")
    return _read_under(path, root, allowed)


def _text(input: Mapping[str, Any], name: str, default: str | None = None) -> str:
    value = input.get(name)
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise _Refused(_INVALID_REQUEST, f"{name} must be a non-empty string")
    return value


def _path(value: Any, name: str, *, slot: str | None = None) -> str:
    """An absolute path of plain segments (no ``.``/``..``, query or fragment); with
    ``slot`` (e.g. ``{device_id}``), exactly one segment is that slot."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise _Refused(_INVALID_ACTOR, f"{name} must be an absolute path")
    segments = value[1:].split("/")
    slots = sum(segment == slot for segment in segments)
    plain = all(
        segment == slot or (_SEGMENT_RE.match(segment) and segment not in (".", ".."))
        for segment in segments
    )
    if not plain or slots != (1 if slot else 0):
        hint = f" with {slot} as one segment" if slot else ""
        raise _Refused(_INVALID_ACTOR, f"{name} must be plain path segments{hint}")
    return value


def _words(value: Any, name: str) -> tuple[str, ...]:
    """A non-empty list of lowercase words (the actor's ``kinds`` or ``visibilities``)."""
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and _WORD_RE.match(v) for v in value)
    ):
        raise _Refused(_INVALID_ACTOR, f"{name} must be a non-empty list of lowercase words")
    return tuple(value)


def _formats(value: Any) -> tuple[str, ...] | None:
    """The actor's ``formats`` (known words only), ``None`` when unset."""
    if value is None:
        return None
    if not isinstance(value, list) or not value or not all(v in FORMAT_TYPES for v in value):
        known = ", ".join(FORMAT_TYPES)
        raise _Refused(_INVALID_ACTOR, f"formats must be a non-empty list of: {known}")
    return tuple(value)


def _format(input: Mapping[str, Any], allowed: tuple[str, ...] | None) -> str:
    """The step's format (epub when unset) if the actor allows it; an actor with no
    ``formats`` allows only epub."""
    value = input.get("format")
    chosen = DEFAULT_FORMAT if value is None else value
    permitted = allowed if allowed is not None else (DEFAULT_FORMAT,)
    if not isinstance(chosen, str) or chosen not in permitted:
        raise _Refused(_INVALID_REQUEST, f"format must be one of {', '.join(permitted)}")
    return chosen


def _item(value: Any) -> int | None:
    """The ``item_id`` to replace (a positive integer), ``None`` when unset."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _Refused(_INVALID_REQUEST, "item_id must be a positive integer")
    return value


def _reply_item_id(reply: Mapping[str, Any], item_id: int | None) -> int:
    """The item id an upload or replace reply names. An upload answers ``{"item": {"id"}}``; a
    replace may also answer with the item itself (culture-library does), and must name
    ``item_id``."""
    item = reply.get("item")
    if item_id is not None and not isinstance(item, dict):
        item = reply
    got_id = _int_id(item.get("id") if isinstance(item, dict) else None, "upload")
    if item_id is not None and got_id != item_id:
        raise _Refused(_BAD_RESPONSE, f"replace reply names item {got_id}, not {item_id}")
    return got_id


def _replace_target(params: Mapping[str, Any], item_id: int | None) -> tuple[str, str] | None:
    """``(method, path)`` of the actor's replace route. Both are checked whenever either is
    set; a replace (``item_id`` given) needs both."""
    path, method = params.get("replace_path"), params.get("replace_method")
    if path is None and method is None:
        if item_id is not None:
            raise _Refused(_INVALID_ACTOR, "item_id needs replace_path and replace_method")
        return None
    if path is None or method is None:
        raise _Refused(_INVALID_ACTOR, "replace_path and replace_method go together")
    path = _path(path, "replace_path", slot=_ITEM_SLOT)
    if not isinstance(method, str) or method not in REPLACE_METHODS:
        raise _Refused(
            _INVALID_ACTOR, f"replace_method must be one of {', '.join(REPLACE_METHODS)}"
        )
    return method, path


def _choice(
    input: Mapping[str, Any], name: str, allowed: tuple[str, ...] | None, *, required: bool
) -> str | None:
    """``input[name]`` if the actor allows it; ``None`` when unset and not ``required``."""
    value = input.get(name)
    if value is None and not required:
        return None
    if allowed is None:
        raise _Refused(_INVALID_REQUEST, f"the library actor lists no {name} values")
    if not isinstance(value, str) or value not in allowed:
        raise _Refused(_INVALID_REQUEST, f"{name} must be one of {', '.join(allowed)}")
    return value


def _status_failure(stage: str, status: int, raw: bytes, extra: str = "") -> _Refused:
    """A non-2xx answer: the library's error ``code`` is named when it looks like one."""
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

    supports_idempotency_key = True  # the library dedupes the upload and the queue (module doc)

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
        upload_path = _path(params.get("upload_path"), "upload_path")
        item_id = _item(input.get("item_id"))
        replace = _replace_target(params, item_id)
        queue_path = params.get("queue_path")
        device = None
        if queue_path is not None:
            queue_path = _path(queue_path, "queue_path", slot=_DEVICE_SLOT)
            device = _device(input.get("device_id")) or _device(params.get("device_id"))
            if device is None and item_id is None:
                raise _Refused("device_missing", "no device_id on the action or the actor")
        kinds = _words(params.get("kinds"), "kinds")
        fmt = _format(input, _formats(params.get("formats")))
        raw_visibilities = params.get("visibilities")
        visibilities = (
            None if raw_visibilities is None else _words(raw_visibilities, "visibilities")
        )
        roots = _content_roots(params.get("content_roots"))
        data = _source(input, roots)
        title = _text(input, "title")
        author = _text(input, "author", DEFAULT_AUTHOR)
        fields = [
            ("title", title),
            ("author", author),
            ("kind", _choice(input, "kind", kinds, required=True)),
        ]
        visibility = _choice(input, "visibility", visibilities, required=False)
        if visibility is not None:
            fields.append(("visibility", visibility))
        timeout = self._remaining(deadline, "before the upload")
        auth = {"Authorization": "Bearer " + self._key(params.get("api_key"))}
        content_type, body = encode_multipart(
            fields, [("file", item_filename(title, fmt), FORMAT_TYPES[fmt], data)]
        )
        method, url = "POST", base + upload_path
        if item_id is not None and replace is not None:
            method = replace[0]
            url = base + replace[1].replace(_ITEM_SLOT, str(item_id))
        status, raw = self._send(method, url, {**auth, "Content-Type": content_type}, body, timeout)
        if not 200 <= status < 300:
            raise _status_failure("upload_failed", status, raw)
        reply = _json_reply(raw, "upload")
        got_id = _reply_item_id(reply, item_id)
        queue_id = None
        if item_id is None and queue_path is not None and device is not None:
            queue_id = self._queue(base + queue_path, device, got_id, auth, deadline)
        return InvocationResult.completed(
            {"item_id": got_id, "created": bool(reply.get("created")), "queue_id": queue_id}
        )

    def _queue(
        self,
        template: str,
        device: str,
        item_id: int,
        auth: Mapping[str, str],
        deadline: datetime,
    ) -> int:
        queue_url = template.replace(_DEVICE_SLOT, quote(device, safe=""))
        queue_body = json.dumps({"item_id": item_id}).encode("utf-8")
        headers = {**auth, "Content-Type": "application/json"}
        timeout = self._remaining(deadline, f"after the upload (item {item_id} uploaded)")
        status, raw = self._send("POST", queue_url, headers, queue_body, timeout)
        if not 200 <= status < 300:
            raise _status_failure("queue_failed", status, raw, f" (item {item_id} uploaded)")
        return _int_id(_json_reply(raw, "queue").get("id"), "queue")

    def _send(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> tuple[int, bytes]:
        try:
            return self._transport(method, url, headers, body, timeout)
        except (http.client.HTTPException, OSError) as exc:  # URLError/timeouts are OSError
            raise _Refused(_NETWORK_ERROR, type(exc).__name__, retryable=True) from exc
