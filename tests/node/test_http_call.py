"""http.call action port: allowlist + range refusal after DNS, pinned connect, no redirects."""

from __future__ import annotations

import http.server
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions import http as http_mod
from culture_rules.node.actions.http import HttpCallPort, is_refused_address
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

PUBLIC = "8.8.8.8"

REFUSED = [
    "127.0.0.1",
    "127.10.20.30",
    "::1",
    "169.254.169.254",
    "fe80::1",
    "10.0.0.1",
    "10.255.255.255",
    "172.16.0.1",
    "172.31.255.254",
    "192.168.1.1",
    "100.64.0.1",
    "100.127.105.72",
    "0.0.0.0",
    "::",
    "224.0.0.1",
    "ff02::1",
    "fd00::1",
    "fc00::1",
    "::ffff:10.0.0.1",
    "::ffff:127.0.0.1",
    "2002:a00:1::",  # 6to4 wrapping 10.0.0.1
]


class FakeResponse:
    def __init__(self, status=200, body=b"ok", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "text/plain; charset=utf-8"}

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def close(self):
        pass


class FakeOpener:
    def __init__(self, calls, response=None, exc=None):
        self.calls = calls
        self.response = response or FakeResponse()
        self.exc = exc

    def open(self, request, timeout=None):
        self.calls[-1]["request"] = request
        self.calls[-1]["timeout"] = timeout
        if self.exc is not None:
            raise self.exc
        return self.response


def _store(allow=("api.example.com",), kind="service", headers=None, enabled=True):
    store = MemoryStore()
    http_params = {"allow": list(allow)}
    if headers is not None:
        http_params["headers"] = headers
    store.put(
        ACTORS_COLLECTION,
        {
            "id": "svc",
            "name": "svc",
            "kind": kind,
            "enabled": enabled,
            "params": {"http": http_params},
        },
    )
    return store


def _port(store, resolved=(PUBLIC,), response=None, exc=None, **kw):
    calls: list[dict] = []
    lookups: list[tuple[str, int]] = []

    def resolver(host, port):
        lookups.append((host, port))
        return list(resolved)

    def opener_factory(pinned):
        calls.append({"pinned": pinned})
        return FakeOpener(calls, response, exc)

    port = HttpCallPort(store, resolver=resolver, opener_factory=opener_factory, **kw)
    return port, calls, lookups


def _ctx(actor="svc"):
    return InvocationContext(
        run_id="run1",
        step_id="action",
        kind="action",
        host="h1",
        actor=actor,
        config={"kind": "http.call"},
    )


def _invoke(port, params, key="k1", seconds=30, actor="svc"):
    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    return port.invoke(params, key, deadline, context=_ctx(actor))


def _call(url="https://api.example.com/v1/x", method="GET", **extra):
    return {"actor": "svc", "method": method, "url": url, **extra}


@pytest.mark.parametrize("address", REFUSED)
def test_every_refused_range_is_classified(address) -> None:
    assert is_refused_address(address)


@pytest.mark.parametrize("address", [PUBLIC, "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_not_refused(address) -> None:
    assert not is_refused_address(address)


@pytest.mark.parametrize("address", REFUSED)
def test_allowlisted_host_resolving_into_refused_range_makes_no_request(address) -> None:
    port, calls, _ = _port(_store(), resolved=[address])
    res = _invoke(port, _call())
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("destination_refused")
    assert calls == []


def test_any_refused_address_among_several_refuses() -> None:
    port, calls, _ = _port(_store(), resolved=[PUBLIC, "10.1.2.3"])
    res = _invoke(port, _call())
    assert res.error.startswith("destination_refused")
    assert calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.org/",
        "http://127.0.0.1:8791/api",
        "http://100.127.105.72:27028/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "https://api.example.com.evil.org/",
        "https://sub.api.example.com/",
    ],
)
def test_non_allowlisted_host_makes_no_request(url) -> None:
    port, calls, _ = _port(_store())
    res = _invoke(port, _call(url))
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("destination_refused")
    assert calls == []


def test_no_allowlist_refuses_everything() -> None:
    store = MemoryStore()
    store.put(ACTORS_COLLECTION, {"id": "svc", "name": "svc", "kind": "service", "params": {}})
    port, calls, _ = _port(store)
    res = _invoke(port, _call())
    assert res.error.startswith("destination_refused")
    assert calls == []


def test_allowlisted_private_ip_literal_is_allowed_and_pinned() -> None:
    port, calls, lookups = _port(_store(allow=["10.1.2.3"]))
    res = _invoke(port, _call("http://10.1.2.3:8080/hook", method="POST", body={"a": 1}))
    assert res.outcome == COMPLETED, res.error
    assert lookups == []  # an IP literal is not resolved
    assert calls[0]["pinned"] == "10.1.2.3"
    req = calls[0]["request"]
    assert req.get_method() == "POST"
    assert json.loads(req.data) == {"a": 1}
    assert req.get_header("Content-type") == "application/json"


def test_hostname_resolving_to_private_ip_needs_that_exact_ip_allowlisted() -> None:
    port, calls, _ = _port(_store(allow=["intranet.example"]), resolved=["10.9.9.9"])
    res = _invoke(port, _call("https://intranet.example/x"))
    assert res.error.startswith("destination_refused")
    assert calls == []
    port, calls, _ = _port(_store(allow=["intranet.example", "10.9.9.9"]), resolved=["10.9.9.9"])
    res = _invoke(port, _call("https://intranet.example/x"))
    assert res.outcome == COMPLETED
    assert calls[0]["pinned"] == "10.9.9.9"


def test_public_host_is_pinned_to_the_vetted_address_and_keeps_host() -> None:
    port, calls, lookups = _port(_store(allow=["API.example.com."]))
    res = _invoke(port, _call("https://api.example.com/v1/x?q=1"), seconds=5)
    assert res.outcome == COMPLETED
    assert lookups == [("api.example.com", 443)]
    assert calls[0]["pinned"] == PUBLIC
    assert calls[0]["request"].host == "api.example.com"
    assert 0 < calls[0]["timeout"] <= 5
    assert res.output == {
        "status": 200,
        "headers": {"content-type": "text/plain; charset=utf-8"},
        "body": "ok",
        "truncated": False,
    }


@pytest.mark.parametrize(
    "url",
    [
        "ftp://api.example.com/x",
        "file:///etc/passwd",
        "gopher://api.example.com/",
        "https://user:pw@api.example.com/",
        "api.example.com/x",
        "https:///nohost",
    ],
)
def test_bad_scheme_or_url_is_refused_without_request(url) -> None:
    port, calls, _ = _port(_store())
    res = _invoke(port, _call(url))
    assert res.outcome == FAILED
    assert res.retryable is False
    assert calls == []


def test_bad_method_is_refused() -> None:
    port, calls, _ = _port(_store())
    res = _invoke(port, _call(method="CONNECT"))
    assert res.outcome == FAILED
    assert res.retryable is False
    assert calls == []


def test_redirect_from_opener_is_refused_non_retryable() -> None:
    port, calls, _ = _port(_store(), exc=http_mod.RedirectRefused(302))
    res = _invoke(port, _call())
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("redirect_refused")


def test_server_error_is_retryable_client_error_is_not() -> None:
    port, _, _ = _port(_store(), response=FakeResponse(503, b"down"))
    res = _invoke(port, _call())
    assert res.outcome == FAILED
    assert res.retryable is True
    assert "503" in res.error
    port, _, _ = _port(_store(), response=FakeResponse(404, b"secret body"))
    res = _invoke(port, _call())
    assert res.outcome == FAILED
    assert res.retryable is False
    assert "404" in res.error
    assert "secret body" not in res.error


def test_network_error_is_retryable() -> None:
    port, _, _ = _port(_store(), exc=ConnectionResetError("boom"))
    res = _invoke(port, _call())
    assert res.outcome == FAILED
    assert res.retryable is True
    assert res.error.startswith("network_error")


def test_body_is_truncated() -> None:
    port, _, _ = _port(_store(), response=FakeResponse(200, b"x" * 100), max_body=10)
    res = _invoke(port, _call())
    assert res.output["body"] == "x" * 10
    assert res.output["truncated"] is True


def test_grant_header_refs_are_resolved_and_redacted() -> None:
    seen = []

    def secret_runner(name):
        seen.append(name)
        return "s3cr3t-value"

    store = _store(headers={"Authorization": "grant:API_TOKEN"})
    port, calls, _ = _port(
        store,
        response=FakeResponse(200, b"echo s3cr3t-value"),
        secret_runner=secret_runner,
    )
    res = _invoke(port, _call(headers={"X-Extra": "grant:OTHER", "Accept": "text/plain"}))
    assert res.outcome == COMPLETED
    req = calls[0]["request"]
    assert req.get_header("Authorization") == "s3cr3t-value"
    assert req.get_header("X-extra") == "s3cr3t-value"
    assert sorted(seen) == ["API_TOKEN", "OTHER"]
    assert "s3cr3t-value" not in res.output["body"]


def test_unresolvable_secret_fails_without_request() -> None:
    def secret_runner(name):
        raise RuntimeError("nope")

    store = _store(headers={"Authorization": "grant:MISSING"})
    port, calls, _ = _port(store, secret_runner=secret_runner)
    res = _invoke(port, _call())
    assert res.outcome == FAILED
    assert res.retryable is False
    assert calls == []
    assert res.error.startswith("secret_unresolved")


def test_host_header_override_is_refused() -> None:
    port, calls, _ = _port(_store())
    res = _invoke(port, _call(headers={"Host": "internal"}))
    assert res.outcome == FAILED
    assert res.retryable is False
    assert calls == []


def test_actor_missing_disabled_or_absent() -> None:
    port, calls, _ = _port(MemoryStore())
    assert _invoke(port, _call()).error.startswith("actor_not_found")
    port, calls, _ = _port(_store(enabled=False))
    assert _invoke(port, _call()).error.startswith("actor_disabled")
    port, calls, _ = _port(_store())
    params = _call()
    del params["actor"]
    assert _invoke(port, params, actor=None).error.startswith("actor_missing")
    assert calls == []


def test_expired_deadline_makes_no_request() -> None:
    port, calls, _ = _port(_store())
    res = _invoke(port, _call(), seconds=-1)
    assert res.outcome == FAILED
    assert calls == []


def test_default_resolver_strips_scope_and_dedups(monkeypatch) -> None:
    def fake_getaddrinfo(host, port, *a, **kw):
        return [
            (10, 1, 6, "", ("fe80::1%eth0", port, 0, 2)),
            (2, 1, 6, "", (PUBLIC, port)),
            (2, 1, 6, "", (PUBLIC, port)),
        ]

    monkeypatch.setattr(http_mod.socket, "getaddrinfo", fake_getaddrinfo)
    assert http_mod.resolve_host("x.example", 443) == ["fe80::1", PUBLIC]


# --- real stdlib transport against a loopback server (allow-listed explicitly) -----------


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list[tuple[str, str | None]] = []

    def do_GET(self):  # noqa: N802 - stdlib name
        type(self).seen.append((self.path, self.headers.get("Host")))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_real_transport_pins_vetted_ip_and_keeps_host_header(server) -> None:
    port = HttpCallPort(
        _store(allow=["pinned.invalid", "127.0.0.1"]),
        resolver=lambda host, p: ["127.0.0.1"],
    )
    res = _invoke(port, _call(f"http://pinned.invalid:{server}/hi"))
    assert res.outcome == COMPLETED, res.error
    assert res.output["body"] == "hello"
    assert res.output["status"] == 200
    assert _Handler.seen == [("/hi", f"pinned.invalid:{server}")]


def test_real_transport_refuses_redirects_and_does_not_follow(server) -> None:
    port = HttpCallPort(_store(allow=["127.0.0.1"]))
    res = _invoke(port, _call(f"http://127.0.0.1:{server}/redirect"))
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("redirect_refused")
    assert [p for p, _ in _Handler.seen] == ["/redirect"]


def test_real_transport_ignores_proxy_environment(server, monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://10.0.0.1:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://10.0.0.1:3128")
    port = HttpCallPort(_store(allow=["127.0.0.1"]))
    res = _invoke(port, _call(f"http://127.0.0.1:{server}/direct"), seconds=5)
    assert res.outcome == COMPLETED, res.error
