"""``culture-rules actions kinds`` and the ``actions_kinds`` MCP tool (Boundary t9)."""

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
from culture_rules.model.action_kinds import ActionKind, ParamSpec  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402


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
    kind = ActionKind("acme.ping", {"text": ParamSpec("str", True)}, "Ping Acme")
    registry().install("acme", lambda ext: ext.action_kind(kind, aliases=("acme.old",)))
    yield kind
    registry().uninstall("acme")


def run(capsys, *argv):
    rc = main([*argv, "--json"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    return json.loads(cap.out)


def test_the_verb_is_a_read_for_a_viewer():
    verb = REGISTRY.get("actions", "kinds")
    assert (verb.mutating, verb.role) == (False, "viewer")


def test_the_mcp_tool_exists():
    assert "actions_kinds" in {t["name"] for t in mcp_tools.tool_specs()}


@pytest.mark.parametrize("path", [("actions",), ("actions", "kinds")])
def test_explain_has_an_entry(path):
    assert path in ENTRIES


def test_learn_mentions_the_noun(capsys):
    main(["learn"])
    assert "actions kinds" in capsys.readouterr().out


def test_kinds_lists_the_registry_in_order(api, capsys):
    out = run(capsys, "actions", "kinds")
    assert [i["kind"] for i in out["items"]] == list(registry().action_kinds())


def test_kinds_shows_one_kind_by_name(api, capsys, fake):
    out = run(capsys, "actions", "kinds", "acme.ping")
    assert [i["kind"] for i in out["items"]] == ["acme.ping"]


def test_kinds_resolves_an_alias(api, capsys, fake):
    out = run(capsys, "actions", "kinds", "acme.old")
    assert [i["kind"] for i in out["items"]] == ["acme.ping"]


def test_kinds_text_names_the_params(api, capsys, fake):
    main(["actions", "kinds", "acme.ping"])
    assert "text: str (required)" in capsys.readouterr().out


def test_the_mcp_tool_returns_the_same_items(api, fake):
    out = mcp_tools.call_tool("actions_kinds", {"kind": "acme.ping"}, api)
    assert [i["kind"] for i in out["items"]] == ["acme.ping"]


def test_an_old_server_is_exit_2(monkeypatch, capsys):
    client = ApiClient(
        "http://127.0.0.1:8765", transport=lambda *a: (404, b'{"detail":"Not Found"}')
    )
    monkeypatch.setattr(_api, "make_client", lambda api_url=None: client)
    assert main(["actions", "kinds", "--json"]) == 2
    assert "0.44.0" in capsys.readouterr().err
