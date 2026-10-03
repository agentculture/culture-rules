"""t34: the API serves the packaged web build at / (SPA fallback) and under /api."""

from __future__ import annotations

import json

import pytest

from culture_rules.store.memory import MemoryStore
from tests.server.conftest import ALICE, DEV, TestClient, create_app


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "web_dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    return d


@pytest.fixture
def web(dist):
    c = TestClient(create_app(MemoryStore(), auth=DEV, web_dist=dist))
    c.app.state.dist = dist
    return c


def test_root_and_assets_are_served(web):
    r = web.get("/", headers=ALICE)
    assert r.status_code == 200
    assert "<title>spa</title>" in r.text
    assert r.headers["content-type"].startswith("text/html")
    a = web.get("/assets/app.js", headers=ALICE)
    assert a.status_code == 200
    assert a.text == "console.log(1)"


def test_spa_fallback_for_client_routes(web):
    # a browser navigation (Accept: text/html), including paths that are also API routes
    for path in ("/", "/rules", "/rules/build-and-publish", "/workflows", "/actors", "/statistics"):
        r = web.get(path, headers={**ALICE, "Accept": "text/html,*/*;q=0.8"})
        assert r.status_code == 200, path
        assert "<title>spa</title>" in r.text, path


def test_api_is_reachable_bare_and_under_the_api_prefix(web):
    bare = web.get("/whoami", headers=ALICE)
    prefixed = web.get("/api/whoami", headers=ALICE)
    assert bare.status_code == prefixed.status_code == 200
    # without a browser Accept, a bare API path stays the API (JSON, not the page)
    assert web.get("/rules", headers=ALICE).json() == {"items": []}
    assert bare.json() == prefixed.json()
    assert prefixed.json()["identity"] == "alice"
    assert web.get("/api/health", headers=ALICE).status_code == 200


def test_api_prefix_writes_and_authz_still_apply(web):
    locked = TestClient(create_app(MemoryStore(), web_dist=web.app.state.dist))
    assert locked.get("/api/whoami").status_code == 401  # no credential, prefix or not
    assert locked.get("/").status_code == 401  # static is behind the same auth
    made = web.post("/api/rules", headers=ALICE, json={"id": "x"})
    assert made.status_code != 404


def test_unknown_api_path_is_a_json_404_not_the_spa(web):
    r = web.get("/api/no-such-thing", headers=ALICE)
    assert r.status_code == 404
    assert "spa" not in r.text
    assert "error" in json.loads(r.text) or "detail" in json.loads(r.text)


def test_no_path_traversal_out_of_the_dist(web):
    r = web.get("/..%2fsecret.txt", headers=ALICE)
    assert "nope" not in r.text
    r = web.get("/assets/../../secret.txt", headers=ALICE)
    assert "nope" not in r.text


def test_openapi_is_unchanged_by_serving_the_web(dist):
    assert create_app(MemoryStore(), web_dist=dist).openapi() == create_app(MemoryStore()).openapi()


def test_without_a_build_nothing_is_mounted(tmp_path):
    app = create_app(MemoryStore(), auth=DEV, web_dist=tmp_path / "missing")
    c = TestClient(app)
    assert c.get("/", headers=ALICE).status_code == 404
    assert c.get("/api/whoami", headers=ALICE).status_code == 404  # no prefix without a UI
    assert c.get("/whoami", headers=ALICE).status_code == 200


def test_unknown_unprefixed_path_for_a_non_browser_client_is_a_json_404(web):
    """Live-test finding: curl/CLI clients must not get the SPA for a path that is no route."""
    for accept in ("application/json", "*/*"):
        r = web.get("/nope/x", headers={**ALICE, "Accept": accept})
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")
    page = web.get("/nope/x", headers={**ALICE, "Accept": "text/html"})
    assert page.status_code == 200
    assert "spa" in page.text
    asset = web.get("/assets/app.js", headers={**ALICE, "Accept": "*/*"})
    assert asset.status_code == 200
    assert "console.log" in asset.text
