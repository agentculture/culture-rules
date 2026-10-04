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
precedence over the action's own headers. Any header value of the form ``grant:<NAME>`` is
resolved at call time on this host (:mod:`culture_rules.actors.secrets`) and its value is
scrubbed from the response output. No allowlist means every call is refused.

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
timeout comes from the attempt deadline.

Outcome: 2xx -> completed with ``{status, headers (subset), body (text, truncated),
truncated}``; 4xx -> failed ``http_status`` (retryable only for 408/425/429); 5xx and
network errors -> failed, retryable. Error texts never carry a body or a header value, and
nothing is logged. Standard-library only.
"""

from __future__ import annotations

import functools
import http.client
import ipaddress
import json
import socket
import ssl
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
    "HttpCallPort",
    "RedirectRefused",
    "is_refused_address",
    "pinned_opener",
    "resolve_host",
]

DEFAULT_MAX_BODY = 64 * 1024
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
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/4",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)

Resolver = Callable[[str, int], list[str]]
OpenerFactory = Callable[[str], Any]


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
        super().__init__(context=ssl.create_default_context())
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
    """An opener that dials only ``pinned``: no proxies, no redirects, http/https only."""
    opener = urllib.request.OpenerDirector()
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
        host = _norm_host(parts.hostname)
        if host not in allow:
            raise _Refused("destination_refused", f"host {host!r} is not on the actor allowlist")
        if _is_ip(host):
            addresses = [host]
        else:
            try:
                addresses = [_norm_host(a) for a in self._resolver(host, port or _SCHEMES[scheme])]
            except OSError as exc:
                raise _Refused("network_error", "cannot resolve host", retryable=True) from exc
        if not addresses:
            raise _Refused("network_error", "host resolved to no address", retryable=True)
        for address in addresses:
            try:
                refused = is_refused_address(address)
            except ValueError as exc:
                raise _Refused("destination_refused", "unparseable address") from exc
            if refused and address not in allow:
                raise _Refused(
                    "destination_refused",
                    f"host {host!r} resolves to {address}, a refused range not on the allowlist",
                )
        return host, addresses[0]

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
                if not isinstance(name, str) or not isinstance(value, str):
                    raise _Refused("invalid_request", "header names and values must be strings")
                if name.strip().lower() == "host":
                    raise _Refused("invalid_request", "the Host header cannot be set")
                if value.startswith(GRANT_SCHEME + ":"):
                    try:
                        value = resolve(value, self._secret_runner, redactor=redactor)
                    except SecretError as exc:
                        raise _Refused("secret_unresolved", f"header {name!r}") from exc
                merged[name] = value
        return merged

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
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            raise _Refused("network_error", type(exc).__name__, retryable=True) from exc
        try:
            return self._result(response, redactor)
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()

    def _result(self, response: Any, redactor: Redactor) -> InvocationResult:
        status = int(getattr(response, "status", None) or getattr(response, "code", 0))
        if 300 <= status < 400:
            raise _Refused("redirect_refused", f"server answered {status}")
        if status >= 500 or status < 200:
            raise _Refused("http_status", f"server answered {status}", retryable=True)
        if status >= 400:
            raise _Refused(
                "http_status", f"server answered {status}", retryable=status in _RETRYABLE_4XX
            )
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
