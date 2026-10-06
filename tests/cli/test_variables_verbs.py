"""``culture-rules variables``: dry-run by default, --apply commits, refs and history."""

from __future__ import annotations

import json

import pytest

from culture_rules.cli import _api, main
from tests.cli.test_nouns_api import store, wire  # noqa: F401


def run(capsys, *argv):
    code = main(["variables", *argv, "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else None


def test_set_is_a_dry_run_without_apply(store, wire, capsys):  # noqa: F811
    code, out = run(capsys, "set", "trusted_authors", "--value", '["a"]')
    assert code == 0 and out["dry_run"] is True and out["applied"] is False
    assert out["would"]["method"] == "PUT" and out["would"]["path"] == "/variables/trusted_authors"
    assert out["would"]["body"]["value"] == ["a"]
    assert store.get_variable("trusted_authors") is None
    assert wire.mutating() == []


def test_set_apply_commits_and_history_refs_work(store, wire, capsys):  # noqa: F811
    assert run(capsys, "set", "trusted_authors", "--value", '["a"]', "--apply")[0] == 0
    code, out = run(capsys, "set", "trusted_authors", "--value", '["a","b"]', "--apply")
    assert out["applied"] is True and out["result"]["version"] == 2
    assert out["result"]["updated_by"] == "alice"
    assert store.get_variable("trusted_authors")["value"] == ["a", "b"]
    _, hist = run(capsys, "history", "trusted_authors")
    assert [h["version"] for h in hist["items"]] == [1, 2]
    _, got = run(capsys, "get", "trusted_authors")
    assert got["value"] == ["a", "b"]
    _, lst = run(capsys, "list")
    assert [i["name"] for i in lst["items"]] == ["trusted_authors"]
    store.put("rules", {"id": "r", "name": "r", "condition": {"var": "trusted_authors"}})
    _, refs = run(capsys, "refs", "trusted_authors")
    assert [i["id"] for i in refs["items"]] == ["r"]


def test_dry_run_shows_the_current_version(store, wire, capsys):  # noqa: F811
    store.put_variable("limit", 1, updated_by="x")
    _, out = run(capsys, "set", "limit", "--value", "2")
    assert out["current"]["value"] == 1 and out["would"]["body"]["value"] == 2


def test_bad_value_is_a_user_error(store, wire, capsys):  # noqa: F811
    code = main(["variables", "set", "limit", "--value", "not json", "--json"])
    assert code == 1


def test_null_value_is_writable_via_cli(store, wire, capsys):  # noqa: F811
    code, out = run(capsys, "set", "limit", "--value", "null", "--apply")
    assert code == 0 and out["result"]["value"] is None
    assert store.get_variable("limit")["value"] is None


def test_null_value_is_writable_via_mcp(store, wire):  # noqa: F811
    from culture_rules.mcp.tools import ToolError, call_tool

    client = _api.make_client()
    out = call_tool("variables_set", {"name": "limit", "value": None, "apply": True}, client)
    assert out["result"]["version"] == 1 and store.get_variable("limit")["value"] is None
    with pytest.raises(ToolError):
        call_tool("variables_set", {"name": "limit", "apply": True}, client)


def test_text_output_shows_version_value_and_author(store, wire, capsys):  # noqa: F811
    store.put_variable("limit", 1, updated_by="ann")
    store.put_variable("limit", 2, updated_by="bob")
    assert main(["variables", "history", "limit"]) == 0
    out = capsys.readouterr().out
    assert "limit v1 = 1" in out and "by ann" in out
    assert "limit v2 = 2" in out and "by bob" in out
    assert main(["variables", "list"]) == 0
    assert "limit v2 = 2" in capsys.readouterr().out
    store.put("rules", {"id": "r9", "name": "r9", "condition": {"var": "limit"}})
    assert main(["variables", "refs", "limit"]) == 0
    assert "r9" in capsys.readouterr().out
