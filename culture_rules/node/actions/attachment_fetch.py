"""Built-in ``attachment.fetch``: download pdf and epub attachments, vetted (discord-surfaces R2).

A code-step built-in (``config.builtin: attachment.fetch``), like ``markdown.render``: it
fetches public CDN URLs and needs no secret, so no actor is involved. The connection policy
is ``http.call``'s, reused from :mod:`culture_rules.node.actions.http` (not copied): the URL
checks, DNS resolution with every address vetted against the refused (private, loopback,
link-local, CGNAT, ...) ranges, a connection pinned to the vetted address, no proxies and no
redirects.

Inputs (each may instead sit in ``config``; the input wins):

* ``attachments``: a list of ``{url, filename, content_type}`` items - the shape
  ``discord.read`` outputs per attachment (its ``id`` and ``size`` are read when present);
* ``allowed_hosts`` (optional): exact hostnames to fetch from; it replaces the default
  :data:`DEFAULT_ALLOWED_HOSTS` (Discord's CDN hosts).

The format comes from ``content_type`` (``application/pdf``, ``application/epub+zip``) or,
when the type is absent or ``application/octet-stream``, from the filename's extension
(``.pdf``, ``.epub``). Anything else - images included - is not fetched.

Output: ``items`` (one per attachment, in order), ``fetched`` and ``skipped`` (counts). A
fetched item is ``{filename, skipped: false, format, content_type, size, content_base64}``;
a skipped one is ``{filename, skipped: true, reason}``, the reason one of:

* ``unsupported_format`` - not a pdf or an epub (no request made);
* ``invalid_item`` - not a mapping, or no url;
* ``host_refused`` - not ``https``, credentials in the url, or a host not allowed;
* ``private_address`` - the host resolves to a refused address (no connection made);
* ``redirect_refused`` - the server answered a redirect (never followed);
* ``too_large`` - over :data:`MAX_ATTACHMENT_BYTES` by the declared ``size``, the
  ``Content-Length`` or the bytes read (never more than the limit plus one is read);
* ``http_status`` - a 4xx answer.

A 5xx or 429 answer, a network error and a resolution failure fail the whole step,
retryable, so a retry fetches again. Non-list ``attachments`` or ``allowed_hosts`` fail
``bad_params``. Nothing is logged. Standard library only.
"""

from __future__ import annotations

import base64
import http.client
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.http import (
    MAX_TIMEOUT,
    OpenerFactory,
    RedirectRefused,
    Resolver,
    _checked_url,
    _is_ip,
    _norm_host,
    _Refused,
    _vet_address,
    pinned_opener,
    resolve_host,
)

__all__ = [
    "ATTACHMENT_FETCH_BUILTIN",
    "DEFAULT_ALLOWED_HOSTS",
    "MAX_ATTACHMENT_BYTES",
    "AttachmentFetchPort",
]

ATTACHMENT_FETCH_BUILTIN = "attachment.fetch"
DEFAULT_ALLOWED_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
_HTTPS_PORT = 443
_BAD_PARAMS = "bad_params"
_TYPES = {"application/pdf": "pdf", "application/epub+zip": "epub"}
_EXTENSIONS = {".pdf": "pdf", ".epub": "epub"}
_GENERIC_TYPES = frozenset({"", "application/octet-stream"})
_RETRYABLE_STATUS = frozenset({408, 425, 429})


class _BadParams(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(f"{_BAD_PARAMS}: {message}")


class _Skip(Exception):
    """One attachment is not fetched; ``reason`` is the item's reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _StepFailed(Exception):
    """A transient failure: the whole step fails retryable."""


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _format(filename: Any, content_type: Any) -> str | None:
    """``pdf``, ``epub`` or None (unsupported), from the content type, else the extension."""
    ctype = content_type.split(";", 1)[0].strip().lower() if isinstance(content_type, str) else ""
    if ctype in _TYPES:
        return _TYPES[ctype]
    if ctype not in _GENERIC_TYPES or not isinstance(filename, str):
        return None
    dot = filename.rfind(".")
    return _EXTENSIONS.get(filename[dot:].lower()) if dot >= 0 else None


def _header(headers: Any, name: str) -> str | None:
    for key, value in getattr(headers, "items", lambda: [])():
        if str(key).lower() == name:
            return str(value)
    return None


def _over(value: Any, limit: int) -> bool:
    """Whether a declared size (int or numeric string) exceeds ``limit``."""
    if isinstance(value, bool):
        return False
    try:
        return int(value) > limit
    except (TypeError, ValueError):
        return False


class AttachmentFetchPort:
    """Built-in ``attachment.fetch`` (see the module docstring)."""

    supports_idempotency_key = True  # a GET of the same URLs is safe to repeat

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        opener_factory: OpenerFactory | None = None,
        max_bytes: int = MAX_ATTACHMENT_BYTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._resolver = resolver or resolve_host
        self._opener_factory = opener_factory or pinned_opener
        self._max_bytes = max_bytes
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        _idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        try:
            attachments, allow = self._params(input, config)
            items = [self._item(att, allow, deadline) for att in attachments]
        except _BadParams as exc:
            return InvocationResult.failed(str(exc), retryable=False)
        except _StepFailed as exc:
            return InvocationResult.failed(str(exc), retryable=True)
        fetched = sum(1 for i in items if not i["skipped"])
        return InvocationResult.completed(
            {"items": items, "fetched": fetched, "skipped": len(items) - fetched}
        )

    @staticmethod
    def _params(input: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[list, set[str]]:
        attachments = _param(input, config, "attachments")
        if not isinstance(attachments, list):
            raise _BadParams("attachments must be a list")
        hosts = _param(input, config, "allowed_hosts")
        if hosts is None:
            hosts = DEFAULT_ALLOWED_HOSTS
        if not isinstance(hosts, list | tuple):
            raise _BadParams("allowed_hosts must be a list of hostnames")
        allow = {_norm_host(h) for h in hosts if isinstance(h, str) and h.strip()}
        return attachments, allow

    def _item(self, att: Any, allow: set[str], deadline: datetime) -> dict[str, Any]:
        filename = att.get("filename") if isinstance(att, Mapping) else None
        try:
            if not isinstance(att, Mapping) or not isinstance(att.get("url"), str):
                raise _Skip("invalid_item")
            fmt = _format(filename, att.get("content_type"))
            if fmt is None:
                raise _Skip("unsupported_format")
            if _over(att.get("size"), self._max_bytes):
                raise _Skip("too_large")
            data = self._fetch(att["url"], allow, deadline)
        except _Skip as skip:
            return {"filename": filename, "skipped": True, "reason": skip.reason}
        content_type = "application/pdf" if fmt == "pdf" else "application/epub+zip"
        return {
            "filename": filename,
            "skipped": False,
            "format": fmt,
            "content_type": content_type,
            "size": len(data),
            "content_base64": base64.b64encode(data).decode("ascii"),
        }

    # -- the vetted fetch (http.call's policy) -------------------------------------------

    def _pinned(self, url: str, allow: set[str]) -> tuple[str, str]:
        """(hostname, vetted address to dial) for ``url``, else :class:`_Skip`."""
        try:
            host, scheme, port = _checked_url(url)
        except _Refused as exc:
            reason = "host_refused" if exc.code == "destination_refused" else "invalid_item"
            raise _Skip(reason) from exc
        if scheme != "https" or host not in allow:
            raise _Skip("host_refused")
        if _is_ip(host):
            addresses = [host]
        else:
            try:
                addresses = [_norm_host(a) for a in self._resolver(host, port or _HTTPS_PORT)]
            except OSError as exc:
                raise _StepFailed("network_error: cannot resolve host") from exc
            if not addresses:
                raise _StepFailed("network_error: host resolved to no address")
        for address in addresses:
            try:
                _vet_address(host, address, allow)
            except _Refused as exc:
                raise _Skip("private_address") from exc
        return host, addresses[0]

    def _fetch(self, url: str, allow: set[str], deadline: datetime) -> bytes:
        host, pinned = self._pinned(url, allow)
        timeout = (deadline - self._clock()).total_seconds()
        if timeout <= 0:
            raise _StepFailed("deadline_passed: attempt deadline already passed")
        try:
            request = urllib.request.Request(url, method="GET")
            request_host = urlsplit("//" + (request.host or "")).hostname or ""
        except ValueError as exc:
            raise _Skip("invalid_item") from exc
        if _norm_host(request_host) != host:  # a parser differential: refuse, as http.call does
            raise _Skip("host_refused")
        try:
            response = self._opener_factory(pinned).open(request, timeout=min(timeout, MAX_TIMEOUT))
        except RedirectRefused as exc:
            raise _Skip("redirect_refused") from exc
        except urllib.error.HTTPError as exc:
            response = exc  # non-2xx: an HTTPError is also the response
        except (http.client.HTTPException, OSError) as exc:  # URLError is an OSError
            raise _StepFailed(f"network_error: {type(exc).__name__}") from exc
        try:
            return self._body(response)
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()

    def _body(self, response: Any) -> bytes:
        status = int(getattr(response, "status", None) or getattr(response, "code", 0))
        if 300 <= status < 400:
            raise _Skip("redirect_refused")
        if status >= 500 or status < 200 or status in _RETRYABLE_STATUS:
            raise _StepFailed(f"http_status: server answered {status}")
        if status >= 400:
            raise _Skip("http_status")
        if _over(_header(getattr(response, "headers", None), "content-length"), self._max_bytes):
            raise _Skip("too_large")
        try:
            data = response.read(self._max_bytes + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise _StepFailed(f"network_error: {type(exc).__name__}") from exc
        if len(data) > self._max_bytes:
            raise _Skip("too_large")
        return data
