"""No shared cache may keep an API answer: every response says how it may be cached.

rules.culture.dev sits behind Cloudflare; without an explicit ``Cache-Control`` a zone cache rule
served a 15-minute-old ``/api/machines`` (newly enrolled machines missing from Statistics), and a
shared cache could hand one signed-in user's answer to another.
"""

from __future__ import annotations

import pytest

from culture_rules.store.memory import MemoryStore
from tests.server.conftest import ALICE, DEV, TestClient, create_app

NO_STORE = "no-store"


@pytest.fixture
def web(tmp_path):
    d = tmp_path / "web_dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    (d / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (d / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return TestClient(create_app(MemoryStore(), auth=DEV, web_dist=d))


@pytest.fixture
def api():
    return TestClient(create_app(MemoryStore(), auth=DEV))


@pytest.mark.parametrize("path", ["/machines", "/machines/status", "/health", "/rules", "/whoami"])
def test_api_reads_are_never_stored(api, path):
    r = api.get(path, headers=ALICE)
    assert r.status_code == 200
    assert NO_STORE in r.headers["cache-control"]


@pytest.mark.parametrize("path", ["/api/machines", "/api/machines/status", "/api/health"])
def test_api_reads_under_the_prefix_are_never_stored(web, path):
    r = web.get(path, headers=ALICE)
    assert r.status_code == 200
    assert NO_STORE in r.headers["cache-control"]


def test_auth_failures_are_never_stored():
    # production auth (no dev identity): a request without credentials is refused
    r = TestClient(create_app(MemoryStore())).get("/machines")
    assert r.status_code == 401
    assert NO_STORE in r.headers["cache-control"]


def test_unknown_api_paths_are_never_stored(web):
    r = web.get("/api/nope", headers=ALICE)
    assert r.status_code == 404
    assert NO_STORE in r.headers["cache-control"]


def test_the_html_shell_is_revalidated_and_kept_out_of_shared_caches(web):
    for path in ("/", "/statistics"):
        r = web.get(path, headers={**ALICE, "accept": "text/html"})
        assert r.status_code == 200
        cc = r.headers["cache-control"]
        assert "no-cache" in cc
        assert "private" in cc


def test_hashed_assets_are_private_and_immutable(web):
    r = web.get("/assets/index-abc123.js", headers=ALICE)
    assert r.status_code == 200
    cc = r.headers["cache-control"]
    assert "private" in cc
    assert "immutable" in cc
    assert "max-age=31536000" in cc
    assert "public" not in cc


def test_unhashed_static_files_are_revalidated(web):
    r = web.get("/favicon.svg", headers=ALICE)
    assert r.status_code == 200
    cc = r.headers["cache-control"]
    assert "no-cache" in cc
    assert "private" in cc
