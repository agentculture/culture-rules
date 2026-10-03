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
    with pytest.raises(tools.ToolError):
        tools.call_tool("nope_nope", {}, FakeClient())
    with pytest.raises(tools.ToolError):
        tools.call_tool("rules_show", {}, FakeClient())
    with pytest.raises(tools.ToolError):
        tools.call_tool("rules_show", {"id": "x", "bogus": 1}, FakeClient())


def test_main_without_extra_reports_environment_error(monkeypatch, capsys):
    from culture_rules.mcp import __main__ as entry
    from culture_rules.mcp import server

    def boom():
        raise server.ServerExtraMissing("needs the 'mcp' extra")

    monkeypatch.setattr(server, "run_stdio", boom)
    assert entry.main() == 2
    assert "mcp" in capsys.readouterr().err
