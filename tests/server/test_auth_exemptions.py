"""Only the exact webhook paths answer without a principal; everything else is 401 first.

Walks ``app.routes``: every route/method, called with no credential, gets the auth
middleware's 401 envelope (``{"error": {"code": "no_credentials", ...}}``) - ``/health``
included, which has always needed a principal - except ``POST /hooks/github`` and
``POST /hooks/jira``, which reach their handler (whose own refusal is a flat
``{"error": "unauthorized"}``). Near-miss paths, other methods, case and encoding variants and
the ``/api`` alias all stay behind the middleware. Plus a signed GitHub delivery end to end
through ``create_app``, and the access-log filter that keeps ``?token=`` out of logs.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets as pysecrets

import pytest

pytest.importorskip("fastapi")

from fastapi import APIRouter, Request  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.events.ingest import EVENTS_COLLECTION  # noqa: E402
from culture_rules.server import serve as serve_mod  # noqa: E402
from culture_rules.server.app import HOOK_PATHS, create_app  # noqa: E402
from culture_rules.server.hooks import github as gh  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

EXEMPT = {("POST", "/hooks/github"), ("POST", "/hooks/jira")}
HANDLER_REFUSAL = {"error": "unauthorized"}


def _app(store=None, **kw):
    return create_app(store or MemoryStore(), auth=AuthSettings(listener=LAN), **kw)


def _is_auth_401(r) -> bool:
    if r.status_code != 401:
        return False
    if r.request.method == "HEAD":
        return r.headers.get("content-type", "").startswith("application/json")
    err = r.json().get("error")
    return isinstance(err, dict) and err.get("code") == "no_credentials"


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _flat(routes):
    """``app.routes`` with included routers expanded (FastAPI keeps them as wrappers)."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _flat(inner.routes)
        else:
            yield route


def test_the_exemption_set_is_exactly_the_two_hook_paths():
    assert HOOK_PATHS == frozenset({"/hooks/github", "/hooks/jira"})
    assert isinstance(HOOK_PATHS, frozenset)


def test_both_hook_routes_are_mounted():
    app = _app()
    mounted = {(m, r.path) for r in _flat(app.routes) if isinstance(r, APIRoute) for m in r.methods}
    assert EXEMPT <= mounted


def test_walk_every_route_without_a_principal():
    app = _app()
    client = TestClient(app)
    seen = 0
    for route in _flat(app.routes):
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        path = _concrete(route.path)
        for method in sorted(methods):
            r = client.request(method, path)
            seen += 1
            if (method, route.path) in EXEMPT:
                assert r.status_code == 401, (method, path, r.text)
                assert r.json() == HANDLER_REFUSAL, (method, path, r.text)
            else:
                assert _is_auth_401(r), (method, path, r.status_code, r.text)
    assert seen > 30


def test_health_still_requires_a_principal():
    r = TestClient(_app()).get("/health")
    assert _is_auth_401(r), r.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/hooks/github/x"),
        ("POST", "/hooks/githubx"),
        ("POST", "/hooks/github/"),
        ("POST", "/hooks/jira/"),
        ("POST", "/hooks/jirax"),
        ("POST", "/hooks//github"),
        ("POST", "/HOOKS/github"),
        ("POST", "/hooks/GitHub"),
        ("POST", "/hooks/github%2F"),
        ("POST", "/hooks%2Fgithub"),
        ("POST", "/hooks/%67ithub"),
        ("POST", "/hooks"),
        ("POST", "/hooks/"),
        ("GET", "/hooks/github"),
        ("PUT", "/hooks/github"),
        ("DELETE", "/hooks/jira"),
        ("HEAD", "/hooks/jira"),
        ("OPTIONS", "/hooks/github"),
    ],
)
def test_near_misses_get_the_middleware_401(method, path):
    r = TestClient(_app()).request(method, path)
    assert r.status_code == 401, (method, path, r.status_code, r.text)
    assert _is_auth_401(r), (method, path, r.text)


def _raw_asgi(app, path: str, raw_path: bytes) -> tuple[int, bytes]:
    """Call ``app`` with an exact scope (no client-side URL normalisation)."""
    out: dict = {"body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": raw_path,
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    return out["status"], out["body"]


@pytest.mark.parametrize(
    ("path", "raw"),
    [
        ("/./hooks/github", b"/./hooks/github"),
        ("/hooks/github", b"/hooks/%67ithub"),  # decoded path matches, raw bytes do not
        ("/hooks/jira", b"/hooks%2Fjira"),
    ],
)
def test_raw_scope_variants_are_not_exempt(path, raw):
    status, body = _raw_asgi(_app(), path, raw)
    assert status == 401
    assert json.loads(body)["error"]["code"] == "no_credentials"


def test_raw_scope_exact_path_is_exempt():
    status, body = _raw_asgi(_app(), "/hooks/jira", b"/hooks/jira")
    assert json.loads(body) != {} and not isinstance(json.loads(body)["error"], dict)


def test_the_api_prefix_alias_is_not_exempt(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    client = TestClient(_app(web_dist=tmp_path))
    for path in ("/api/hooks/github", "/api/hooks/jira"):
        assert _is_auth_401(client.post(path)), path
    # the exact path still reaches the handler with the web UI mounted
    assert client.post("/hooks/github").json() == HANDLER_REFUSAL


def test_exempt_requests_carry_no_principal(monkeypatch):
    seen = []

    def spy_router(store, **kw):
        api = APIRouter()

        async def spy(request: Request):
            seen.append(getattr(request.state, "principal", "unset"))
            return {"error": "unauthorized"}

        api.add_api_route("/hooks/github", spy, methods=["POST"])
        return api

    monkeypatch.setattr(gh, "router", spy_router)
    r = TestClient(_app()).post("/hooks/github", content=b"{}")
    assert r.status_code == 200, r.text
    assert seen == [None]


def test_a_signed_github_delivery_through_create_app_writes_one_event(monkeypatch):
    key = pysecrets.token_hex(16)
    ref = "grant:GH_HOOK"
    monkeypatch.setattr(gh, "resolve", lambda r: {ref: key}[r])
    store = MemoryStore()
    store.insert(
        "actors",
        {
            "id": "gh-app",
            "kind": "app",
            "enabled": True,
            "params": {
                "surface": "github",
                "events": ["github.pr.opened"],
                "self_identity": "culture[bot]",
                "connection": {"app_id": "111", "webhook_secret": ref},
            },
        },
    )
    body = json.dumps(
        {
            "action": "opened",
            "pull_request": {"number": 7, "title": "T", "html_url": "https://x/pr/7"},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "alice"},
        }
    ).encode()
    headers = {
        "x-github-event": "pull_request",
        "x-github-delivery": "d-1",
        "x-github-hook-installation-target-id": "111",
        "x-hub-signature-256": "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest(),
    }
    client = TestClient(_app(store))
    r = client.post("/hooks/github", content=body, headers=headers)
    assert r.status_code == 202, r.text
    assert r.json() == {"accepted": True}
    again = client.post("/hooks/github", content=body, headers=headers)
    assert again.json() == {"duplicate": True}
    events = store.find(EVENTS_COLLECTION)
    assert len(events) == 1
    assert "github.pr.opened" in json.dumps(events[0])
    # a wrong signature reaches the handler and is refused there, writing nothing
    forged = {**headers, "x-github-delivery": "d-2", "x-hub-signature-256": "sha256=00"}
    bad = client.post("/hooks/github", content=body, headers=forged)
    assert bad.status_code == 401 and bad.json() == HANDLER_REFUSAL
    assert len(store.find(EVENTS_COLLECTION)) == 1


def _access_record(path: str) -> logging.LogRecord:
    """A record shaped like uvicorn's access log line (path with query string at args[2])."""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "POST", path, "1.1", 202),
        None,
    )


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("/hooks/jira?token=s3cr3t", "/hooks/jira"),
        ("/hooks/github?x=1&token=s3cr3t", "/hooks/github"),
        ("/api/hooks/jira?token=s3cr3t", "/api/hooks/jira"),
        ("/hooks/jira/x?token=s3cr3t", "/hooks/jira/x"),
        ("/HOOKS/jira?token=s3cr3t", "/HOOKS/jira"),
        ("/hooks%2Fjira?token=s3cr3t", "/hooks%2Fjira"),
        ("/hooks/jira/?token=s3cr3t", "/hooks/jira/"),
        ("/rules?limit=5", "/rules?limit=5"),
        ("/hooks/jira", "/hooks/jira"),
    ],
)
def test_access_log_filter_strips_hook_query_strings(raw, shown):
    record = _access_record(raw)
    assert serve_mod.HookQueryFilter().filter(record) is True
    assert record.args[2] == shown
    assert "s3cr3t" not in record.getMessage()


def test_access_log_filter_leaves_unexpected_records_alone():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "plain", None, None)
    assert serve_mod.HookQueryFilter().filter(record) is True
    assert record.getMessage() == "plain"


def test_serve_installs_the_filter_on_the_uvicorn_access_logger(monkeypatch, caplog):
    uvicorn = pytest.importorskip("uvicorn")
    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access, "filters", [])
    ran = []
    monkeypatch.setattr(serve_mod, "_run_servers", lambda configs: ran.append(configs))
    serve_mod.serve(MemoryStore(), node_name="spark")
    assert ran and all(isinstance(c, uvicorn.Config) for c in ran[0])
    assert any(isinstance(f, serve_mod.HookQueryFilter) for f in access.filters)
    serve_mod.install_hook_log_filter()  # idempotent
    assert sum(isinstance(f, serve_mod.HookQueryFilter) for f in access.filters) == 1
    monkeypatch.setattr(access, "handlers", [caplog.handler])
    monkeypatch.setattr(access, "disabled", False)
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        access.handle(_access_record("/hooks/jira?token=s3cr3t"))
    assert "s3cr3t" not in caplog.text
    assert "/hooks/jira" in caplog.text


def test_hook_routes_stay_out_of_the_openapi_contract():
    paths = _app().openapi()["paths"]
    assert not [p for p in paths if p.startswith("/hooks")]
