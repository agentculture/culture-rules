"""The SDK-free half of the MCP layer (runs without the ``mcp`` extra)."""

from __future__ import annotations

import pytest

from culture_rules.cli.verbs import REGISTRY
from culture_rules.mcp import tools


class FakeClient:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None, **kw):
        self.calls.append((method, path))
        return {"items": []}


def test_specs_match_registry():
    specs = tools.tool_specs()
    assert [s["name"] for s in specs] == [v.tool_name for v in REGISTRY.verbs()]
    assert all(
        s["inputSchema"] == REGISTRY.get(*s["name"].split("_", 1)).params_schema()
        for s in specs
        if REGISTRY.get(*s["name"].split("_", 1))
    )


def test_apply_must_be_boolean_true():
    c = FakeClient()
    out = tools.call_tool("rules_delete", {"id": "r1", "apply": "yes"}, c)
    assert out["dry_run"] is True
    assert [m for m, _ in c.calls if m != "GET"] == []


def test_unknown_tool_and_missing_param_raise():
    client = FakeClient()
    with pytest.raises(tools.ToolError):
        tools.call_tool("nope_nope", {}, client)
    with pytest.raises(tools.ToolError):
        tools.call_tool("rules_show", {}, client)
    with pytest.raises(tools.ToolError):
        tools.call_tool("rules_show", {"id": "x", "bogus": 1}, client)


def test_main_without_extra_reports_environment_error(monkeypatch, capsys):
    from culture_rules.mcp import __main__ as entry
    from culture_rules.mcp import server

    def boom():
        raise server.ServerExtraMissing("needs the 'mcp' extra")

    monkeypatch.setattr(server, "run_stdio", boom)
    assert entry.main() == 2
    assert "mcp" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["false", "true", 0, 1, "yes"])
@pytest.mark.parametrize("tool", ["rollout_resume", "rollout_pause", "rollout_seed"])
def test_a_boolean_argument_must_be_a_json_boolean(tool, value):
    """Codex r1 on 0.19.3: MCP input validation is off, so a truthy string such as "false"
    passed for ``force`` bypassed resume's enable guard (#62). Refused before any request."""
    c = FakeClient()
    with pytest.raises(tools.ToolError, match="force must match the declared type"):
        tools.call_tool(tool, {"force": value, "apply": True}, c)
    assert c.calls == []


def test_integer_and_string_arguments_must_have_their_declared_type():
    """Review of #63: the same check covers integer and string params, never bool-as-int."""
    integer = next((v, p.name) for v in REGISTRY.verbs() for p in v.params if p.type == "integer")
    string = next((v, p.name) for v in REGISTRY.verbs() for p in v.params if p.type == "string")
    for (verb, param), bad in ((integer, "5"), (integer, True), (string, 5)):
        c = FakeClient()
        with pytest.raises(tools.ToolError, match=f"{param} must match the declared type"):
            tools.call_tool(verb.tool_name, {param: bad}, c)
        assert c.calls == []
