"""``culture-rules keysets list|show|clear`` and the keysets_* MCP tools (c18, h17).

End to end against ``create_app`` (an admin token over a recording transport), plus a fake
older server whose new routes answer the framework's plain 404.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules import keysets  # noqa: E402
from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.cli import _api, main  # noqa: E402
from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.client.http import ApiClient  # noqa: E402
from culture_rules.explain.catalog import ENTRIES  # noqa: E402
from culture_rules.mcp import tools as mcp_tools  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

NOW = datetime.now(UTC)


class Tap:
    """A transport onto an in-process app with a token of ``role``; records each request."""

    def __init__(self, store, role="admin"):
        self.tc = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
        token = ServiceTokens(store).issue(role, name=role, roles=[role]).token
        self.headers = {"Authorization": f"Bearer {token}"}
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, json.loads(body) if body else None))
        r = self.tc.request(method, url, headers={**headers, **self.headers}, content=body)
        return r.status_code, r.content


class OldServer:
    """An API from before the keysets routes: each answers the generic FastAPI 404."""

    def __call__(self, method, url, headers, body):
        return 404, b'{"detail":"Not Found"}'


def wire_to(monkeypatch, transport):
    monkeypatch.setattr(
        _api,
        "make_client",
        lambda api_url=None: ApiClient("http://127.0.0.1:8765", transport=transport),
    )
    return transport


def run(capsys, *argv):
    rc = main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def jok(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    assert rc == 0, err
    return json.loads(out)


@pytest.fixture
def store():
    s = MemoryStore()
    keysets.add_keys(s, "seen", ["https://a/1", "https://a/2"], NOW, 3600)
    keysets.add_keys(s, "other", ["k"], NOW, 3600)
    return s


@pytest.fixture
def admin(store, monkeypatch):
    return wire_to(monkeypatch, Tap(store))


def _count(store, name):
    return len(store.find(keysets.KEYSETS_COLLECTION, {"set": name}))


# --------------------------------------------------------------------------- registry


@pytest.mark.parametrize("name, mutating", [("list", False), ("show", False), ("clear", True)])
def test_verbs_are_registered_admin_only(name, mutating):
    v = REGISTRY.get("keysets", name)
    assert (v.mutating, v.role) == (mutating, "admin")


@pytest.mark.parametrize("tool", ["keysets_list", "keysets_show", "keysets_clear"])
def test_mcp_tools_exist(tool):
    assert tool in {t["name"] for t in mcp_tools.tool_specs()}


@pytest.mark.parametrize(
    "path", [("keysets",), ("keysets", "list"), ("keysets", "show"), ("keysets", "clear")]
)
def test_explain_has_an_entry(path):
    assert path in ENTRIES


def test_learn_mentions_keysets(capsys):
    _, out, _ = run(capsys, "learn")
    assert "keysets clear" in out


# --------------------------------------------------------------------------- verbs


def test_list_shows_sets_with_key_counts(admin, capsys):
    out = jok(capsys, "keysets", "list")
    assert out["items"] == [{"set": "other", "keys": 1}, {"set": "seen", "keys": 2}]


def test_list_text_names_each_set(admin, capsys):
    _, text, _ = run(capsys, "keysets", "list")
    assert "seen" in text


def test_show_lists_the_keys(admin, capsys):
    out = jok(capsys, "keysets", "show", "seen")
    assert [i["key"] for i in out["items"]] == ["https://a/1", "https://a/2"]


def test_show_gives_iso_datetimes(admin, capsys):
    out = jok(capsys, "keysets", "show", "seen")
    assert out["items"][0]["first_seen"] == NOW.isoformat()


def test_clear_without_apply_leaves_the_documents(admin, store, capsys):
    jok(capsys, "keysets", "clear", "seen")
    assert _count(store, "seen") == 2


def test_clear_without_apply_reports_the_plan(admin, capsys):
    out = jok(capsys, "keysets", "clear", "seen")
    assert (out["dry_run"], out["applied"], out["result"]["count"]) == (True, False, 2)


def test_clear_without_apply_sends_dry_run_true(admin, capsys):
    jok(capsys, "keysets", "clear", "seen", "--key", "https://a/1")
    assert admin.calls[-1][2] == {"key": "https://a/1", "dry_run": True}


def test_clear_text_dry_run_says_how_to_commit(admin, capsys):
    _, text, _ = run(capsys, "keysets", "clear", "seen")
    assert "--apply" in text


def test_clear_apply_with_a_key_removes_one(admin, store, capsys):
    jok(capsys, "keysets", "clear", "seen", "--key", "https://a/1", "--apply")
    assert [d["key"] for d in store.find(keysets.KEYSETS_COLLECTION, {"set": "seen"})] == [
        "https://a/2"
    ]


def test_clear_apply_without_a_key_removes_the_set(admin, store, capsys):
    out = jok(capsys, "keysets", "clear", "seen", "--apply")
    assert (_count(store, "seen"), out["result"]["count"]) == (0, 2)


def test_clear_apply_leaves_other_sets(admin, store, capsys):
    jok(capsys, "keysets", "clear", "seen", "--apply")
    assert _count(store, "other") == 1


@pytest.mark.parametrize("role", ["viewer", "editor"])
@pytest.mark.parametrize(
    "argv",
    [("keysets", "list"), ("keysets", "show", "seen"), ("keysets", "clear", "seen", "--apply")],
)
def test_non_admin_is_refused_with_exit_1(store, monkeypatch, capsys, role, argv):
    wire_to(monkeypatch, Tap(store, role))
    rc, _, _ = run(capsys, *argv, "--json")
    assert rc == 1


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_non_admin_clear_deletes_nothing(store, monkeypatch, capsys, role):
    wire_to(monkeypatch, Tap(store, role))
    run(capsys, "keysets", "clear", "seen", "--apply", "--json")
    assert _count(store, "seen") == 2


@pytest.mark.parametrize(
    "argv",
    [("keysets", "list"), ("keysets", "show", "seen"), ("keysets", "clear", "seen")],
)
def test_an_older_server_is_exit_2(monkeypatch, capsys, argv):
    wire_to(monkeypatch, OldServer())
    rc, _, _ = run(capsys, *argv, "--json")
    assert rc == 2


def test_an_older_server_names_the_release(monkeypatch, capsys):
    wire_to(monkeypatch, OldServer())
    _, _, err = run(capsys, "keysets", "list", "--json")
    assert "server too old" in json.loads(err)["message"]


# --------------------------------------------------------------------------- MCP


def test_mcp_clear_is_a_dry_run_unless_apply(store):
    client = ApiClient("http://127.0.0.1:8765", transport=Tap(store))
    mcp_tools.call_tool("keysets_clear", {"name": "seen"}, client)
    assert _count(store, "seen") == 2


def test_mcp_clear_apply_removes_the_set(store):
    client = ApiClient("http://127.0.0.1:8765", transport=Tap(store))
    mcp_tools.call_tool("keysets_clear", {"name": "seen", "apply": True}, client)
    assert _count(store, "seen") == 0
