"""`culture-rules mcp` runs the MCP server over stdio, or exits 2 with an install hint (t42)."""

from __future__ import annotations

import json

from culture_rules.cli import main
from culture_rules.explain.catalog import ENTRIES
from culture_rules.mcp import server as mcp_server


def test_mcp_runs_the_stdio_server(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "run_stdio", lambda: calls.append("stdio"))
    assert main(["mcp"]) == 0
    assert calls == ["stdio"]


def test_mcp_without_the_extra_exits_2_with_a_hint(monkeypatch, capsys):
    def missing():
        raise mcp_server.ServerExtraMissing("the MCP server needs the 'mcp' extra")

    monkeypatch.setattr(mcp_server, "run_stdio", missing)
    assert main(["mcp"]) == 2
    err = capsys.readouterr().err
    assert "mcp" in err and "pip install 'culture-rules[mcp]'" in err


def test_mcp_without_the_extra_json_error(monkeypatch, capsys):
    def missing():
        raise mcp_server.ServerExtraMissing("the MCP server needs the 'mcp' extra")

    monkeypatch.setattr(mcp_server, "run_stdio", missing)
    assert main(["mcp", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["code"] == 2 and "culture-rules[mcp]" in payload["remediation"]


def test_mcp_is_explained_and_learned(capsys):
    assert ("mcp",) in ENTRIES
    assert "stdio" in ENTRIES[("mcp",)]
    assert main(["learn"]) == 0
    assert "culture-rules mcp" in capsys.readouterr().out
    assert main(["learn", "--json"]) == 0
    listed = {tuple(c["path"]) for c in json.loads(capsys.readouterr().out)["commands"]}
    assert ("mcp",) in listed
    assert main(["explain", "mcp"]) == 0
    assert "culture-rules mcp" in capsys.readouterr().out
