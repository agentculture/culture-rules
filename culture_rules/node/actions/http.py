"""``http.call`` action port: one HTTP request through the bound actor's allowlist.

Params (``culture_rules.model.action_kinds``): ``actor`` (falls back to ``context.actor``),
``method``, ``url``, optional ``headers`` (str -> str) and ``body`` (str sent as UTF-8; any
other JSON value is sent as JSON with ``Content-Type: application/json`` unless set).

The bound actor (any kind: service, daemon, runner, an app actor, ...) carries the policy in
``params.http``::

    {"http": {"allow": ["api.example.com", "10.1.2.3"],
              "headers": {"Authorization": "grant:EXAMPLE_TOKEN"}}}

``allow`` lists exact hostnames (case-insensitive, trailing dot ignored, no wildcards, any
port) and exact IP literals. ``headers`` are sent on every call by this actor and take
precedence over the action's own headers. A header value of the form ``grant:<NAME>``, or of
the form ``<word> grant:<NAME>`` (exactly one word of ``[A-Za-z][A-Za-z0-9._-]*``, one space,
then the reference; e.g. ``Bearer grant:TOKEN``), is resolved at call time on this host
(:mod:`culture_rules.actors.secrets`), sent as the value or as ``<word> <value>``, and scrubbed
from the response output. Any other value holding ``grant:`` is sent unchanged.
No allowlist means every call is refused.

Destination policy, checked before any request is made (failure code
``destination_refused``, never retryable):

* the scheme must be ``http`` or ``https``; userinfo in the URL is refused;
* the URL's host must be on the allowlist;
* the host is resolved (an IP literal is not) and **every** resolved address is vetted:
  loopback, link-local (169.254/16, fe80::/10), private (10/8, 172.16/12, 192.168/16,
  IPv6 ULA fc00::/7), CGNAT/tailnet 100.64/10, unspecified, multicast, reserved and any
  other non-global address is refused - including IPv4 addresses wrapped in IPv4-mapped or
  6to4 IPv6 - unless that exact IP is itself on the allowlist. (So an allow-listed
  hostname that resolves into a private range is still refused; allow the IP too.)

The connection is then made to the vetted address (pinned), while the ``Host`` header and
TLS SNI/certificate check keep the URL's hostname, so a DNS answer that changes between
the check and the connect (rebinding) cannot redirect the request. Environment proxies
are ignored, redirects are refused (``redirect_refused``, never followed) and the
timeout comes from the attempt deadline. The User-Agent is :data:`USER_AGENT`
(``culture-rules``) unless a ``User-Agent`` header is given.

Outcome: 2xx -> completed with ``{status, headers (subset), body (text, truncated),
truncated}``; 4xx -> failed ``http_status`` (retryable only for 408/425/429); 5xx and
network errors -> failed, retryable. Error texts never carry a body or a header value, and
nothing is logged. Standard-library only.

Saving the body to a file (``save_to``; the body never enters run state, deviation d7, as
``attachment.fetch`` does). The step param ``save_to`` is an absolute path naming a directory
the actor's policy lists in ``save_roots``; the policy may also set ``save_max_bytes`` (a
positive integer, default :data:`DEFAULT_SAVE_MAX_BYTES`, 50 MiB)::

    {"http": {"allow": ["127.0.0.1"], "save_roots": ["/srv/culture-rules/fetched"],
              "save_max_bytes": 52428800}}

Checked before any lookup or request (failure ``path_refused``, never retryable): ``save_to``
is absolute, ``save_roots`` is a non-empty list of absolute paths, and ``save_to``
(normalised: ``.``/``..`` and trailing slashes folded) **equals** one listed root (normalised
the same way) - a directory under a root is not accepted. The root must be an existing
directory with no symlink anywhere in its path (``os.path.realpath`` must change nothing, and
it is opened ``O_NOFOLLOW | O_DIRECTORY``). A ``save_to`` that is not a non-empty string is
``invalid_request``; a bad ``save_max_bytes`` is ``invalid_request``.

The answer's status is handled as above (a 4xx/5xx is never saved). A 2xx is saved only when
its ``Content-Type`` media type (parameters such as ``charset`` ignored, case-insensitive) is
one of :data:`SAVE_TYPES` (``application/epub+zip`` -> ``epub``, ``application/pdf`` ->
``pdf``, ``text/markdown`` -> ``md``, ``application/json`` -> ``json``, ``text/plain`` ->
``txt``); any other or no type fails ``unsupported_type`` (not retryable) and writes nothing.
A ``Content-Length`` over the cap fails ``too_large`` before writing. The body is then
streamed in :data:`SAVE_CHUNK` pieces to a temporary file inside the root, hashed on the way,
the cap enforced while reading (``too_large``, never more than the cap plus one chunk read),
and renamed atomically to ``<root>/<sha256>.<ext>``; on any failure the temporary file is
removed. The same bytes again find that file and reuse it; a symlink or non-file at that name
is ``path_refused`` (it can only be checked once the body's digest is known). A write error
fails ``write_failed`` (retryable). Completed output: ``{status, path, sha256, size,
content_type}`` (``content_type`` is the bare media type); no ``body``, ``truncated`` or
``headers``.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from culture_rules.actors.secrets import GRANT_SCHEME, Redactor, SecretError, resolve
from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.node.actions.machine import load_bound_actor

__all__ = [
    "DEFAULT_SAVE_MAX_BYTES",
    "SAVE_TYPES",
    "USER_AGENT",
    "HttpCallPort",
    "RedirectRefused",
    "is_refused_address",
    "pinned_opener",
    "resolve_host",
]

DEFAULT_MAX_BODY = 64 * 1024
DEFAULT_SAVE_MAX_BYTES = 50 * 1024 * 1024
SAVE_CHUNK = 1024 * 1024
SAVE_TYPES = {
    "application/epub+zip": "epub",
    "application/pdf": "pdf",
    "text/markdown": "md",
    "application/json": "json",
    "text/plain": "txt",
}
_PATH_REFUSED = "path_refused"
# Sent unless a request names its own: hosts such as Discord's CDN answer 403 to Python's
# default "Python-urllib/3.x".
USER_AGENT = "culture-rules"
MAX_TIMEOUT = 60.0
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_SCHEMES = {"http": 80, "https": 443}
_KEPT_HEADERS = (
    "content-type",
    "content-length",
    "etag",
    "last-modified",
    "location",
    "retry-after",
)
_RETRYABLE_4XX = frozenset({408, 425, 429})
_REFUSED_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",  # NOSONAR - a refused SSRF range by design
        "100.64.0.0/10",  # NOSONAR - a refused SSRF range by design
        "127.0.0.0/8",
        "169.254.0.0/16",  # NOSONAR - a refused SSRF range by design
        "172.16.0.0/12",  # NOSONAR - a refused SSRF range by design
        "192.168.0.0/16",  # NOSONAR - a refused SSRF range by design
        "224.0.0.0/4",  # NOSONAR - a refused SSRF range by design
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)

Resolver = Callable[[str, int], list[str]]
OpenerFactory = Callable[[str], Any]


# ``<word> grant:<NAME>``: one word, one space, one reference (no further spaces).
_WORD_GRANT = re.compile(r"([A-Za-z][A-Za-z0-9._-]*) (grant:\S*)")


class RedirectRefused(Exception):
    """The server answered with a redirect; http.call never follows one."""

    def __init__(self, status: int):
        super().__init__(f"redirect ({status}) refused")
        self.status = status


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    return ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)


def is_refused_address(address: str) -> bool:
    """Whether ``address`` is in a range http.call refuses unless allow-listed exactly."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_v4(ip)
        if inner is not None:
            candidates.append(inner)
    for cand in candidates:
        if any(cand in net for net in _REFUSED_NETS if net.version == cand.version):
            return True
        if cand.is_multicast or cand.is_unspecified or cand.is_reserved or not cand.is_global:
            return True
    return False


def resolve_host(host: str, port: int) -> list[str]:
    """All addresses ``host`` resolves to (scope ids stripped, order kept, deduplicated)."""
    out: list[str] = []
    for *_, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        addr = str(sockaddr[0]).split("%", 1)[0]
        if addr not in out:
            out.append(addr)
    return out


# ------------------------------------------------------------------ pinned transport


def _pin(conn: http.client.HTTPConnection, pinned: str) -> None:
    # http.client connects through ``self._create_connection((host, port), ...)``; swapping
    # it keeps ``self.host`` (Host header, TLS server_hostname) while dialling the vetted IP.
    def connect(address: tuple[str, int], timeout: Any = None, source: Any = None) -> Any:
        return socket.create_connection((pinned, address[1]), timeout, source)

    conn._create_connection = connect  # type: ignore[attr-defined]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, pinned: str, **kw: Any) -> None:
        super().__init__(host, **kw)
        _pin(self, pinned)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, pinned: str, **kw: Any) -> None:
        super().__init__(host, **kw)
        _pin(self, pinned)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, pinned: str) -> None:
        super().__init__()
        self._pinned = pinned

    def http_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(functools.partial(_PinnedHTTPConnection, pinned=self._pinned), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned: str) -> None:
        context = ssl.create_default_context()
        context.check_hostname = True  # against the URL's hostname (the SNI), not the pin
        context.verify_mode = ssl.CERT_REQUIRED
        super().__init__(context=context)
        self._pinned = pinned

    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(
            functools.partial(_PinnedHTTPSConnection, pinned=self._pinned),
            req,
            context=self._context,  # type: ignore[attr-defined]
        )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, *_: Any, **__: Any) -> Any:
        raise RedirectRefused(code)


def pinned_opener(pinned: str) -> urllib.request.OpenerDirector:
    """An opener that dials only ``pinned``: no proxies, no redirects, http/https only.

    It names itself ``USER_AGENT``; a request's own User-Agent header still wins.
    """
    opener = urllib.request.OpenerDirector()
    opener.addheaders = [("User-agent", USER_AGENT)]
    for handler in (
        _PinnedHTTPHandler(pinned),
        _PinnedHTTPSHandler(pinned),
        urllib.request.HTTPDefaultErrorHandler(),
        _NoRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


# ------------------------------------------------------------------ the port


class _Refused(Exception):
    def __init__(self, code: str, detail: str, *, retryable: bool = False):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.retryable = retryable

    def result(self) -> InvocationResult:
        return InvocationResult.failed(str(self), retryable=self.retryable)


def _norm_host(host: str) -> str:
    host = host.strip().lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return host


def _allowlist(params: Mapping[str, Any]) -> set[str]:
    policy = params.get("http")
    allow = policy.get("allow") if isinstance(policy, Mapping) else None
    if not isinstance(allow, list | tuple):
        return set()
    return {_norm_host(a) for a in allow if isinstance(a, str) and a.strip()}


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _checked_url(url: Any) -> tuple[str, str, int | None]:
    """Parse ``url`` and apply the scheme/userinfo/host rules; returns (host, scheme, port)."""
    if not isinstance(url, str) or not url:
        raise _Refused("invalid_request", "url must be a non-empty string")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise _Refused("invalid_request", "malformed url") from exc
    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        raise _Refused("destination_refused", f"scheme {scheme or '(none)'!r} not allowed")
    if parts.username is not None or parts.password is not None:
        raise _Refused("destination_refused", "credentials in the url are not allowed")
    if not parts.hostname:
        raise _Refused("destination_refused", "url has no host")
    return _norm_host(parts.hostname), scheme, port


def _vet_address(host: str, address: str, allow: set[str]) -> None:
    """Refuse ``address`` if it is in a refused range and not itself allow-listed."""
    try:
        refused = is_refused_address(address)
    except ValueError as exc:
        raise _Refused("destination_refused", "unparseable address") from exc
    if refused and address not in allow:
        raise _Refused(
            "destination_refused",
            f"host {host!r} resolves to {address}, a refused range not on the allowlist",
        )


class HttpCallPort:
    """ActorPort for ``http.call`` (see the module docstring)."""

    supports_idempotency_key = False  # an arbitrary endpoint cannot deduplicate on our key

    def __init__(
        self,
        store: Any,
        *,
        resolver: Resolver | None = None,
        opener_factory: OpenerFactory | None = None,
        secret_runner: Callable[[str], str] | None = None,
        max_body: int = DEFAULT_MAX_BODY,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._resolver = resolver or resolve_host
        self._opener_factory = opener_factory or pinned_opener
        self._secret_runner = secret_runner
        self._max_body = max_body
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ledger: dict[str, InvocationResult] = {}

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        if idempotency_key in self._ledger:
            return self._ledger[idempotency_key]
        actor, _doc, failure = load_bound_actor(self._store, input, context)
        if failure is not None or actor is None:
            return failure or InvocationResult.failed("actor_not_found", retryable=False)
        try:
            result = self._call(input, actor.params, deadline)
        except _Refused as exc:
            return exc.result()
        if result.outcome == COMPLETED:
            self._ledger[idempotency_key] = result
        return result

    # -- checks ---------------------------------------------------------------------

    def _destination(self, url: Any, allow: set[str]) -> tuple[str, str]:
        """Validate ``url`` against the policy; returns (hostname, vetted address to dial)."""
        host, scheme, port = _checked_url(url)
        if host not in allow:
            raise _Refused("destination_refused", f"host {host!r} is not on the actor allowlist")
        addresses = self._addresses(host, port or _SCHEMES[scheme])
        for address in addresses:
            _vet_address(host, address, allow)
        return host, addresses[0]

    def _addresses(self, host: str, port: int) -> list[str]:
        """The addresses to vet for ``host`` (an IP literal is not resolved)."""
        if _is_ip(host):
            return [host]
        try:
            addresses = [_norm_host(a) for a in self._resolver(host, port)]
        except OSError as exc:
            raise _Refused("network_error", "cannot resolve host", retryable=True) from exc
        if not addresses:
            raise _Refused("network_error", "host resolved to no address", retryable=True)
        return addresses

    def _headers(
        self, input: Mapping[str, Any], params: Mapping[str, Any], redactor: Redactor
    ) -> dict[str, str]:
        policy = params.get("http")
        merged: dict[str, str] = {}
        for source in (input.get("headers"), policy.get("headers") if policy else None):
            if source is None:
                continue
            if not isinstance(source, Mapping):
                raise _Refused("invalid_request", "headers must be a mapping")
            for name, value in source.items():
                merged[name] = self._header_value(name, value, redactor)
        return merged

    def _header_value(self, name: Any, value: Any, redactor: Redactor) -> str:
        """Check one header; resolve a ``grant:`` value (registered with ``redactor``)."""
        if not isinstance(name, str) or not isinstance(value, str):
            raise _Refused("invalid_request", "header names and values must be strings")
        if name.strip().lower() == "host":
            raise _Refused("invalid_request", "the Host header cannot be set")
        word = ""
        ref = value
        match = _WORD_GRANT.fullmatch(value)
        if match:
            word, ref = match.group(1) + " ", match.group(2)
        if not ref.startswith(GRANT_SCHEME + ":"):
            return value
        try:
            return word + resolve(ref, self._secret_runner, redactor=redactor)
        except SecretError as exc:
            raise _Refused("secret_unresolved", f"header {name!r}") from exc

    @staticmethod
    def _body(input: Mapping[str, Any], headers: dict[str, str]) -> bytes | None:
        body = input.get("body")
        if body is None:
            return None
        if isinstance(body, str):
            return body.encode("utf-8")
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"
        try:
            return json.dumps(body).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise _Refused("invalid_request", "body is not JSON-serialisable") from exc

    # -- the call -------------------------------------------------------------------

    def _call(
        self, input: Mapping[str, Any], params: Mapping[str, Any], deadline: datetime
    ) -> InvocationResult:
        method = input.get("method")
        method = method.upper() if isinstance(method, str) else ""
        if method not in METHODS:
            raise _Refused("invalid_request", f"method {method or '(none)'!r} not allowed")
        save = _save_target(input.get("save_to"), params)
        host, pinned = self._destination(input.get("url"), _allowlist(params))
        timeout = (deadline - self._clock()).total_seconds()
        if timeout <= 0:
            raise _Refused("deadline_passed", "attempt deadline already passed", retryable=True)
        redactor = Redactor()
        headers = self._headers(input, params, redactor)
        data = self._body(input, headers)
        try:
            request = urllib.request.Request(
                input["url"], data=data, headers=headers, method=method
            )
        except ValueError as exc:
            raise _Refused("invalid_request", "malformed url") from exc
        # urllib parses the url with its own splitter; refuse any parser differential so the
        # Host header / SNI always name the host that was checked against the allowlist.
        try:
            request_host = urlsplit("//" + (request.host or "")).hostname or ""
        except ValueError:
            request_host = ""
        if _norm_host(request_host) != host:
            raise _Refused("destination_refused", "url host is ambiguous")
        try:
            response = self._opener_factory(pinned).open(request, timeout=min(timeout, MAX_TIMEOUT))
        except RedirectRefused as exc:
            raise _Refused("redirect_refused", f"server answered {exc.status}") from exc
        except urllib.error.HTTPError as exc:
            response = exc  # non-2xx: an HTTPError is also the response
        except (http.client.HTTPException, OSError) as exc:  # URLError is an OSError
            raise _Refused("network_error", type(exc).__name__, retryable=True) from exc
        try:
            if save is not None:
                return self._saved(response, *save)
            return self._result(response, redactor)
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()

    def _result(self, response: Any, redactor: Redactor) -> InvocationResult:
        status = _checked_status(response)
        try:
            raw = response.read(self._max_body + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise _Refused("network_error", type(exc).__name__, retryable=True) from exc
        truncated = len(raw) > self._max_body
        headers = _kept_headers(getattr(response, "headers", {}) or {})
        text = raw[: self._max_body].decode(_charset(headers), errors="replace")
        return InvocationResult.completed(
            {
                "status": status,
                "headers": redactor.redact(headers),
                "body": redactor.redact(text),
                "truncated": truncated,
            }
        )

    def _saved(self, response: Any, root: str, cap: int) -> InvocationResult:
        """Stream a 2xx body to ``<root>/<sha256>.<ext>`` (see the module docstring)."""
        status = _checked_status(response)
        headers = _kept_headers(getattr(response, "headers", {}) or {})
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        ext = SAVE_TYPES.get(content_type)
        if ext is None:
            raise _Refused("unsupported_type", "the response type is not one save_to writes")
        if _over(headers.get("content-length"), cap):
            raise _Refused("too_large", f"the body is over {cap} bytes")
        fd, tmp = tempfile.mkstemp(dir=root, prefix=".http-call.", suffix=".part")
        try:
            try:
                with os.fdopen(fd, "wb") as out:
                    sha256, size = _stream(response, out, cap)
                path = _place(tmp, os.path.join(root, f"{sha256}.{ext}"))
            except OSError as exc:  # a full disk, a file racing away: transient
                raise _Refused("write_failed", type(exc).__name__, retryable=True) from exc
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return InvocationResult.completed(
            {
                "status": status,
                "path": path,
                "sha256": sha256,
                "size": size,
                "content_type": content_type,
            }
        )


def _checked_status(response: Any) -> int:
    """The answer's status; a redirect, 1xx, 4xx or 5xx raises as http.call reports it."""
    status = int(getattr(response, "status", None) or getattr(response, "code", 0))
    if 300 <= status < 400:
        raise _Refused("redirect_refused", f"server answered {status}")
    if status >= 500 or status < 200:
        raise _Refused("http_status", f"server answered {status}", retryable=True)
    if status >= 400:
        raise _Refused(
            "http_status", f"server answered {status}", retryable=status in _RETRYABLE_4XX
        )
    return status


# ------------------------------------------------------------------ save_to
# A local copy of attachment_fetch's stream-and-place technique: that module imports this one,
# so importing back from it would be circular, and its helpers raise its own exception types.


def _save_target(save_to: Any, params: Mapping[str, Any]) -> tuple[str, int] | None:
    """(root, cap) for a ``save_to`` the policy allows, None without one; else refused."""
    if save_to is None:
        return None
    if not isinstance(save_to, str) or not save_to:
        raise _Refused("invalid_request", "save_to must be a non-empty string")
    policy = params.get("http")
    policy = policy if isinstance(policy, Mapping) else {}
    cap = policy.get("save_max_bytes", DEFAULT_SAVE_MAX_BYTES)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise _Refused("invalid_request", "save_max_bytes must be a positive integer")
    if not os.path.isabs(save_to):
        raise _Refused(_PATH_REFUSED, "save_to must be an absolute path")
    roots = policy.get("save_roots")
    if (
        not isinstance(roots, list)
        or not roots
        or not all(isinstance(r, str) and os.path.isabs(r) for r in roots)
    ):
        raise _Refused(_PATH_REFUSED, "the actor lists no save_roots (absolute paths)")
    root = os.path.normpath(save_to)
    if root not in {os.path.normpath(r) for r in roots}:
        raise _Refused(_PATH_REFUSED, "save_to is not one of the actor's save_roots")
    if os.path.realpath(root) != root:
        raise _Refused(_PATH_REFUSED, "save_to has a symlink in its path")
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise _Refused(_PATH_REFUSED, "save_to is not an existing directory") from exc
    os.close(fd)
    return root, cap


def _over(value: Any, limit: int) -> bool:
    """Whether a declared size (a numeric string) exceeds ``limit``."""
    try:
        return int(value) > limit
    except (TypeError, ValueError):
        return False


def _stream(response: Any, out: Any, cap: int) -> tuple[str, int]:
    """Copy the body to ``out`` in chunks, hashing it and refusing it once past ``cap``."""
    digest, size = hashlib.sha256(), 0
    while True:
        try:
            block = response.read(SAVE_CHUNK)
        except (OSError, http.client.HTTPException) as exc:
            raise _Refused("network_error", type(exc).__name__, retryable=True) from exc
        if not block:
            return digest.hexdigest(), size
        size += len(block)
        if size > cap:
            raise _Refused("too_large", f"the body is over {cap} bytes")
        digest.update(block)
        out.write(block)


def _place(tmp: str, target: str) -> str:
    """Rename ``tmp`` to ``target`` (atomic); an existing regular file there holds the same
    bytes and is reused (``tmp`` dropped); anything else there is refused."""
    if os.path.lexists(target):
        if os.path.islink(target) or not os.path.isfile(target):
            raise _Refused(_PATH_REFUSED, "the target name exists and is not a regular file")
        os.unlink(tmp)
        return target
    os.replace(tmp, target)
    return target


def _kept_headers(headers: Any) -> dict[str, str]:
    items: Iterable[tuple[str, Any]] = headers.items()
    return {k.lower(): str(v) for k, v in items if k.lower() in _KEPT_HEADERS}


def _charset(headers: Mapping[str, str]) -> str:
    content_type = headers.get("content-type", "")
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip().strip('"')
            try:
                "".encode(charset)
            except LookupError:
                break
            return charset
    return "utf-8"
