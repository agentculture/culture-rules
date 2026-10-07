"""d18: atomic list edits on a shared variable (``variables add`` / ``variables remove``).

The variable must exist and be a list; add of a present item and remove of an absent one
append no version; every change appends one version naming the caller, written with a
compare-and-set on the version so two concurrent adds both land. The item must be a JSON
scalar of the list's item type.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.cli import main  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.server.service import Invalid, NotFound, Variables  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from culture_rules.store.port import VariableVersionConflict  # noqa: E402
from tests.cli.test_nouns_api import store, wire  # noqa: E402, F401

REPOS = "fixer_repos"


def seeded(value=("agentculture/a",)) -> tuple[MemoryStore, Variables]:
    s = MemoryStore()
    s.put_variable(REPOS, list(value), updated_by="seed", description="repos the fixer runs on")
    return s, Variables(s)


# ------------------------------------------------------------------ store CAS


def test_put_variable_with_expected_version_is_a_compare_and_set():
    s = MemoryStore()
    s.put_variable("x", [1], updated_by="a", expected_version=0)
    with pytest.raises(VariableVersionConflict):
        s.put_variable("x", [2], updated_by="b", expected_version=0)
    s.put_variable("x", [2], updated_by="b", expected_version=1)
    assert s.get_variable("x")["version"] == 2
    s.put_variable("x", [3], updated_by="c")  # without it: unconditional, as before
    assert s.get_variable("x")["version"] == 3


# ------------------------------------------------------------------ service


def test_add_appends_a_version_naming_the_caller_and_keeps_the_description():
    s, v = seeded()
    out = v.add_item(REPOS, "agentculture/b", "guildmaster")
    assert out["changed"] is True
    doc = out["variable"]
    assert doc["value"] == ["agentculture/a", "agentculture/b"]
    assert doc["version"] == 2 and doc["updated_by"] == "guildmaster"
    assert doc["description"] == "repos the fixer runs on"


def test_add_of_a_present_item_and_remove_of_an_absent_one_write_nothing():
    s, v = seeded()
    assert v.add_item(REPOS, "agentculture/a", "admin")["changed"] is False
    assert v.remove_item(REPOS, "agentculture/zzz", "admin")["changed"] is False
    assert s.get_variable(REPOS)["version"] == 1


def test_remove_takes_every_copy_out_in_one_version():
    s, v = seeded(("x/a", "x/b"))
    out = v.remove_item(REPOS, "x/a", "admin")
    assert out["changed"] is True and out["variable"]["value"] == ["x/b"]
    assert out["variable"]["version"] == 2


def test_two_interleaved_adds_both_land():
    s, v = seeded()
    real = s.put_variable
    state = {"raced": False}

    def racing_put(name, value, **kw):
        if not state["raced"]:  # another writer lands between this read and this write
            state["raced"] = True
            Variables(s).add_item(REPOS, "agentculture/other", "guildmaster")
        return real(name, value, **kw)

    s.put_variable = racing_put
    out = v.add_item(REPOS, "agentculture/mine", "admin")
    assert out["changed"] is True
    value = s.get_variable(REPOS)["value"]
    assert set(value) == {"agentculture/a", "agentculture/other", "agentculture/mine"}
    assert s.get_variable(REPOS)["version"] == 3


@pytest.mark.parametrize(
    "setup, item, code",
    [
        (["x/a"], 3, "item_type_mismatch"),
        (["x/a"], True, "item_type_mismatch"),
        ([1, 2], "3", "item_type_mismatch"),
        ([1, 2], True, "item_type_mismatch"),  # a boolean is not a number
        ("scalar", "x", "not_a_list"),
        (["x/a"], ["nested"], "invalid_item"),
        (["x/a"], {"k": 1}, "invalid_item"),
    ],
)
def test_bad_items_and_non_lists_are_refused(setup, item, code):
    s = MemoryStore()
    s.put_variable(REPOS, setup, updated_by="seed")
    with pytest.raises(Invalid) as err:
        Variables(s).add_item(REPOS, item, "admin")
    assert err.value.errors[0]["code"] == code
    assert s.get_variable(REPOS)["version"] == 1


def test_an_empty_list_takes_any_scalar_and_a_number_list_takes_numbers():
    s = MemoryStore()
    s.put_variable("empty", [], updated_by="seed")
    s.put_variable("nums", [1], updated_by="seed")
    v = Variables(s)
    assert v.add_item("empty", "x/a", "admin")["variable"]["value"] == ["x/a"]
    assert v.add_item("nums", 2.5, "admin")["variable"]["value"] == [1, 2.5]


def test_a_missing_variable_is_not_found():
    with pytest.raises(NotFound):
        Variables(MemoryStore()).add_item(REPOS, "x/a", "admin")


# ------------------------------------------------------------------ HTTP


@pytest.fixture
def world():
    s = MemoryStore()
    s.put_variable(REPOS, ["x/a"], updated_by="seed")
    client = TestClient(create_app(s, auth=AuthSettings(listener=LAN)))
    tokens = ServiceTokens(s)
    hdr = {
        role: {"Authorization": f"Bearer {tokens.issue(role, name=role, roles=[role]).token}"}
        for role in ("viewer", "editor", "admin")
    }
    return s, client, hdr


def test_add_and_remove_routes_are_admin_only(world):
    s, client, hdr = world
    for role in ("viewer", "editor"):
        for op in ("add", "remove"):
            r = client.post(
                f"/variables/{REPOS}/items/{op}", json={"item": "x/b"}, headers=hdr[role]
            )
            assert r.status_code == 403
    assert s.get_variable(REPOS)["version"] == 1
    r = client.post(f"/variables/{REPOS}/items/add", json={"item": "x/b"}, headers=hdr["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["changed"] is True and r.json()["variable"]["updated_by"] == "admin"
    r = client.post(f"/variables/{REPOS}/items/remove", json={"item": "x/a"}, headers=hdr["admin"])
    assert r.json()["variable"]["value"] == ["x/b"]


def test_route_errors(world):
    _s, client, hdr = world
    a = hdr["admin"]
    assert (
        client.post("/variables/nope/items/add", json={"item": "x"}, headers=a).status_code == 404
    )
    r = client.post(f"/variables/{REPOS}/items/add", json={"item": 3}, headers=a)
    assert r.status_code == 422 and "item_type_mismatch" in r.text


# ------------------------------------------------------------------ CLI


def cli(capsys, *argv):
    code = main(["variables", *argv, "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else None


def test_cli_add_is_a_dry_run_without_apply(store, wire, capsys):  # noqa: F811
    store.put_variable(REPOS, ["x/a"], updated_by="seed")
    code, out = cli(capsys, "add", REPOS, "x/b")
    assert code == 0 and out["dry_run"] is True and out["would_change"] is True
    assert out["would"] == {
        "method": "POST",
        "path": f"/variables/{REPOS}/items/add",
        "body": {"item": "x/b"},
    }
    assert store.get_variable(REPOS)["version"] == 1
    _, out = cli(capsys, "add", REPOS, "x/a")
    assert out["would_change"] is False


def test_cli_add_and_remove_with_apply(store, wire, capsys):  # noqa: F811
    store.put_variable(REPOS, ["x/a"], updated_by="seed")
    code, out = cli(capsys, "add", REPOS, "agentculture/pr-fixer-sandbox", "--apply")
    assert code == 0 and out["applied"] is True and out["result"]["changed"] is True
    assert store.get_variable(REPOS)["value"] == ["x/a", "agentculture/pr-fixer-sandbox"]
    code, out = cli(capsys, "remove", REPOS, "x/a", "--apply")
    assert out["result"]["variable"]["value"] == ["agentculture/pr-fixer-sandbox"]
    code, out = cli(capsys, "remove", REPOS, "x/a", "--apply")
    assert out["result"]["changed"] is False
    assert store.get_variable(REPOS)["version"] == 3


def test_cli_item_is_a_string_unless_it_parses_as_a_json_scalar(store, wire, capsys):  # noqa: F811
    store.put_variable("nums", [1], updated_by="seed")
    _code, out = cli(capsys, "add", "nums", "2", "--apply")
    assert store.get_variable("nums")["value"] == [1, 2]
    code, _ = cli(capsys, "add", "nums", "abc", "--apply")
    assert code == 1  # a string into a number list
