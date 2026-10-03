"""t26: the MCP server exposes the one registry over the HTTP API client only."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("mcp")

from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.client.http import ApiClient  # noqa: E402
from culture_rules.mcp import server as mcp_server  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.cli.test_nouns_api import COLLECTIONS, Wire, snapshot  # noqa: E402
from tests.server.conftest import rule_body  # noqa: E402

assert COLLECTIONS


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def wire(store):
    return Wire(store)


@pytest.fixture
def client(wire):
    return ApiClient("http://127.0.0.1:8765", token=wire.token, transport=wire)


def with_session(client, fn):
    from mcp.shared.memory import create_connected_server_and_client_session

    async def go():
        server = mcp_server.build_server(lambda: client)
        async with create_connected_server_and_client_session(server) as session:
            return await fn(session)

    return asyncio.run(go())


def call(client, name, args):
    async def fn(session):
        res = await session.call_tool(name, args)
        return res

    return with_session(client, fn)


def payload(res):
    return json.loads(res.content[0].text)


def test_importing_the_package_does_not_import_mcp():
    code = (
        "import sys, culture_rules.mcp, culture_rules.mcp.tools;"
        "assert 'mcp' not in sys.modules and 'agentfront' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_list_tools_equals_registry_verbs(client):
    async def fn(session):
        return (await session.list_tools()).tools

    tools = with_session(client, fn)
    assert sorted(t.name for t in tools) == sorted(v.tool_name for v in REGISTRY.verbs())
    by_name = {t.name: t for t in tools}
    for v in REGISTRY.verbs():
        assert by_name[v.tool_name].inputSchema == v.params_schema()
    assert "rules_list" in by_name


def test_mutating_tools_declare_apply_and_default_off(client):
    for v in REGISTRY.verbs():
        props = v.params_schema()["properties"]
        assert ("apply" in props) == v.mutating
        if v.mutating:
            assert props["apply"]["default"] is False


def test_dry_run_sends_nothing_mutating(client, wire, store):
    wire.tc.post("/rules", json=rule_body("r1"), headers={"X-Culture-Identity": "alice"})
    wire.calls.clear()
    before = snapshot(store)
    for name, args in [
        ("rules_create", {"body": rule_body("r9")}),
        ("rules_create", {"body": rule_body("r9"), "apply": False}),
        ("rules_disable", {"id": "r1"}),
        ("rules_delete", {"id": "r1"}),
    ]:
        res = call(client, name, args)
        assert not res.isError, res.content
        out = payload(res)
        assert out["dry_run"] is True and out["applied"] is False
    assert wire.mutating() == []
    assert snapshot(store) == before


def test_apply_goes_through(client, wire, store):
    res = call(client, "rules_create", {"body": rule_body("r5"), "apply": True})
    assert not res.isError, res.content
    assert payload(res)["applied"] is True
    assert wire.mutating()
    assert any(d["id"] == "r5" for d in store.find("rules"))
    listed = payload(call(client, "rules_list", {}))
    assert any(i["id"] == "r5" for i in listed["items"])


def test_api_errors_become_tool_errors_not_crashes(client):
    res = call(client, "rules_show", {"id": "nope"})
    assert res.isError
    assert "404" in res.content[0].text


def test_bad_arguments_are_tool_errors(client):
    assert call(client, "rules_show", {}).isError
    assert call(client, "no_such_tool", {}).isError


def test_server_only_talks_to_the_client_factory(client, wire):
    calls = []

    def factory():
        calls.append(1)
        return client

    async def go():
        from mcp.shared.memory import create_connected_server_and_client_session

        server = mcp_server.build_server(factory)
        async with create_connected_server_and_client_session(server) as session:
            await session.call_tool("rules_list", {})

    asyncio.run(go())
    assert calls and all(m == "GET" for m, _ in [(c[0], c[1]) for c in wire.calls])
