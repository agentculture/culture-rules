"""``culture-rules variables``: dry-run by default, --apply commits, refs and history."""

from __future__ import annotations

import json

from culture_rules.cli import main
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
