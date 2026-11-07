"""``culture-rules steps builtins`` and the ``steps_builtins`` MCP tool (R2a, t18)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.cli import _api, main  # noqa: E402
from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.client.http import ApiClient  # noqa: E402
from culture_rules.explain.catalog import ENTRIES  # noqa: E402
from culture_rules.extensions import registry  # noqa: E402
from culture_rules.mcp import tools as mcp_tools  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402


def _port(store):
    return store


@pytest.fixture
def api(monkeypatch):
    store = MemoryStore()
    tc = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    token = ServiceTokens(store).issue("viewer", name="viewer", roles=["viewer"]).token

    def transport(method, url, headers, body):
        r = tc.request(method, url, headers={**headers, "Authorization": f"Bearer {token}"})
        return r.status_code, r.content

    client = ApiClient("http://127.0.0.1:8765", transport=transport)
    monkeypatch.setattr(_api, "make_client", lambda api_url=None: client)
    return client


@pytest.fixture
def fake():
    registry().install(
        "acme",
        lambda ext: ext.builtin("acme.tally", _port, inputs=(("things", "array"),)),
    )
    yield "acme.tally"
    registry().uninstall("acme")


def run(capsys, *argv):
    rc = main([*argv, "--json"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    return json.loads(cap.out)


def test_the_verb_is_a_read_for_a_viewer():
    verb = REGISTRY.get("steps", "builtins")
    assert (verb.mutating, verb.role) == (False, "viewer")


def test_the_mcp_tool_exists():
    assert "steps_builtins" in {t["name"] for t in mcp_tools.tool_specs()}


@pytest.mark.parametrize("path", [("steps",), ("steps", "builtins")])
def test_explain_has_an_entry(path):
    assert path in ENTRIES


def test_learn_mentions_the_noun(capsys):
    main(["learn"])
    assert "steps builtins" in capsys.readouterr().out


def test_builtins_lists_the_registry_in_order(api, capsys):
    out = run(capsys, "steps", "builtins")
    assert [i["name"] for i in out["items"]] == [b.name for b in registry().builtins()]


def test_builtins_shows_one_by_name(api, capsys, fake):
    out = run(capsys, "steps", "builtins", fake)
    assert [i["name"] for i in out["items"]] == [fake]


def test_builtins_text_names_the_ports(api, capsys, fake):
    main(["steps", "builtins", fake])
    assert "in: things: array (required); out: none declared" in capsys.readouterr().out


def test_an_unknown_name_says_so(api, capsys):
    main(["steps", "builtins", "no.such"])
    assert "no such builtin" in capsys.readouterr().out


def test_the_mcp_tool_returns_the_same_items(api, fake):
    out = mcp_tools.call_tool("steps_builtins", {"name": fake}, api)
    assert [i["name"] for i in out["items"]] == [fake]


def test_an_old_server_is_exit_2(monkeypatch, capsys):
    client = ApiClient(
        "http://127.0.0.1:8765", transport=lambda *a: (404, b'{"detail":"Not Found"}')
    )
    monkeypatch.setattr(_api, "make_client", lambda api_url=None: client)
    assert main(["steps", "builtins", "--json"]) == 2
    assert "0.45.0" in capsys.readouterr().err
