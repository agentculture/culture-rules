"""d8: ``DELETE /variables/{name}`` (admin), ``variables delete`` and the MCP tool.

A delete is a dry run unless applied. It is refused (409 ``variable_in_use``) while an
enabled, live rule references the variable; a disabled or soft-deleted referrer does not
block it and is listed in the answer. Applied, it appends a tombstone version (the history
stays) and one audit entry in the same transaction; the variable then reads as undefined.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.cli import main  # noqa: E402
from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.engine.audit import AUDIT_COLLECTION, MUTATING_VERBS  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.server.service import Definitions, Invalid  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from culture_rules.store.port import VARIABLE_GUARDS_COLLECTION  # noqa: E402
from tests.cli.test_nouns_api import store, wire  # noqa: E402, F401
from tests.server.conftest import rule_body  # noqa: E402

NAME = "fixer_levels"


def _rule(id: str, *, enabled: bool = True, deleted: bool = False) -> dict:
    doc = rule_body(
        id, enabled=enabled, condition={"op": "in", "value": {"field": "a"}, "items": {"var": NAME}}
    )
    if deleted:
        doc["deleted_at"] = "2026-11-01T00:00:00+00:00"
    return doc


@pytest.fixture
def world():
    s = MemoryStore()
    s.put_variable(NAME, ["a"], updated_by="seed")
    s.put_variable(NAME, ["a", "b"], updated_by="seed")
    client = TestClient(create_app(s, auth=AuthSettings(listener=LAN)))
    tokens = ServiceTokens(s)
    hdr = {
        role: {"Authorization": f"Bearer {tokens.issue(role, name=role, roles=[role]).token}"}
        for role in ("viewer", "editor", "admin")
    }
    return s, client, hdr


def _delete(client, hdr, name=NAME, *, apply=False):
    return client.delete(f"/variables/{name}", params={"dry_run": not apply}, headers=hdr)


def _audits(s):
    return [a for a in s.find(AUDIT_COLLECTION) if a.get("verb") == "variables.delete"]


def test_viewer_and_editor_are_refused(world):
    s, client, hdr = world
    for role in ("viewer", "editor"):
        for apply in (False, True):
            assert _delete(client, hdr[role], apply=apply).status_code == 403
    assert s.get_variable(NAME)["version"] == 2


def test_a_dry_run_is_the_default_and_writes_nothing(world):
    s, client, hdr = world
    r = client.delete(f"/variables/{NAME}", headers=hdr["admin"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["version"] == 2
    assert body["deleted_version"] == 3
    assert s.get_variable(NAME)["version"] == 2
    assert _audits(s) == []


def test_an_applied_delete_hides_the_variable(world):
    s, client, hdr = world
    r = _delete(client, hdr["admin"], apply=True)
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is False
    assert r.json()["deleted_version"] == 3
    assert client.get(f"/variables/{NAME}", headers=hdr["viewer"]).status_code == 404
    names = [v["name"] for v in client.get("/variables", headers=hdr["viewer"]).json()["items"]]
    assert NAME not in names


def test_an_applied_delete_is_audited_once(world):
    s, client, hdr = world
    _delete(client, hdr["admin"], apply=True)
    [entry] = _audits(s)
    assert entry["identity"] == "admin"
    assert entry["target"] == {"collection": "variables", "id": NAME}
    assert entry["diff"]["deleted"] == {"before": None, "after": True}
    assert "variables.delete" in MUTATING_VERBS


def test_the_history_survives_the_delete(world):
    _s, client, hdr = world
    _delete(client, hdr["admin"], apply=True)
    r = client.get(f"/variables/{NAME}/history", headers=hdr["viewer"])
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["version"] for i in items] == [1, 2, 3]
    assert items[1]["value"] == ["a", "b"]
    assert items[2]["deleted"] is True
    assert items[2]["updated_by"] == "admin"


def test_an_enabled_referrer_refuses_the_delete(world):
    s, client, hdr = world
    s.put("rules", _rule("uses-it"))
    for apply in (False, True):
        r = _delete(client, hdr["admin"], apply=apply)
        assert r.status_code == 409, r.text
        err = r.json()["error"]
        assert err["code"] == "variable_in_use"
        assert "rules/uses-it" in err["message"]
    assert s.get_variable(NAME)["version"] == 2
    assert _audits(s) == []


def test_disabled_and_soft_deleted_referrers_do_not_block(world):
    s, client, hdr = world
    s.put("rules", _rule("off", enabled=False))
    s.put("rules", _rule("binned", deleted=True))
    r = _delete(client, hdr["admin"], apply=True)
    assert r.status_code == 200, r.text
    ids = {(i["id"], i["enabled"], i["deleted"]) for i in r.json()["not_blocking"]}
    assert ids == {("off", False, False), ("binned", True, True)}
    assert s.get_variable(NAME) is None


def test_a_rule_saved_after_the_delete_is_refused_variable_undefined(world):
    _s, client, hdr = world
    _delete(client, hdr["admin"], apply=True)
    r = client.post("/rules", json=_rule("late"), headers=hdr["editor"])
    assert r.status_code == 422, r.text
    assert "variable_undefined" in r.text


def test_an_unknown_or_already_deleted_variable_is_404(world):
    _s, client, hdr = world
    assert _delete(client, hdr["admin"], "nope").status_code == 404
    _delete(client, hdr["admin"], apply=True)
    assert _delete(client, hdr["admin"], apply=True).status_code == 404
    assert client.get("/variables/nope/history", headers=hdr["viewer"]).status_code == 404


# ------------------------------------------------------------------ CLI


def cli(capsys, *argv):
    code = main(["variables", *argv, "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else None


def test_cli_delete_is_a_dry_run_without_apply(store, wire, capsys):  # noqa: F811
    store.put_variable(NAME, ["a"], updated_by="seed")
    code, out = cli(capsys, "delete", NAME)
    assert code == 0
    assert out["dry_run"] is True
    assert out["result"]["deleted_version"] == 2
    assert store.get_variable(NAME)["version"] == 1


def test_cli_delete_with_apply(store, wire, capsys):  # noqa: F811
    store.put_variable(NAME, ["a"], updated_by="seed")
    code, out = cli(capsys, "delete", NAME, "--apply")
    assert code == 0
    assert out["applied"] is True
    assert store.get_variable(NAME) is None


def test_cli_delete_in_use_is_exit_1(store, wire, capsys):  # noqa: F811
    store.put_variable(NAME, ["a"], updated_by="seed")
    store.put("rules", _rule("uses-it"))
    code = main(["variables", "delete", NAME, "--apply", "--json"])
    assert code == 1
    assert "variable_in_use" in json.loads(capsys.readouterr().err)["message"]
    assert store.get_variable(NAME)["version"] == 1


def test_the_verb_is_an_admin_write_with_an_mcp_tool():
    verb = REGISTRY.get("variables", "delete")
    assert verb.mutating is True
    assert verb.role == "admin"
    assert verb.tool_name == "variables_delete"


# ------------------------------------------------------------------ the guard (Codex review P1)


def _guard(s, name=NAME):
    return s.get(VARIABLE_GUARDS_COLLECTION, name)


def test_enabling_a_disabled_referrer_after_the_delete_is_refused(world):
    s, client, hdr = world
    s.put("rules", _rule("off", enabled=False))
    _delete(client, hdr["admin"], apply=True)
    r = client.post("/rules/off/enable", headers=hdr["editor"])
    assert r.status_code == 422, r.text
    assert r.json()["error"]["errors"][0]["code"] == "variable_undefined"
    assert s.get("rules", "off")["enabled"] is False


def test_restoring_an_enabled_referrer_after_the_delete_is_refused(world):
    s, client, hdr = world
    assert client.post("/rules", json=_rule("binned"), headers=hdr["editor"]).status_code == 201
    assert client.delete("/rules/binned", headers=hdr["editor"]).status_code == 200
    _delete(client, hdr["admin"], apply=True)
    r = client.post("/rules/binned/restore", headers=hdr["editor"])
    assert r.status_code == 422, r.text
    assert r.json()["error"]["errors"][0]["code"] == "variable_undefined"
    assert s.get("rules", "binned")["deleted_at"]


def test_an_update_adding_a_reference_to_a_deleted_variable_is_refused(world):
    s, client, hdr = world
    plain = rule_body("plain")
    assert client.post("/rules", json=plain, headers=hdr["editor"]).status_code == 201
    _delete(client, hdr["admin"], apply=True)
    r = client.put("/rules/plain", json=_rule("plain"), headers=hdr["editor"])
    assert r.status_code == 422, r.text
    assert "variable_undefined" in r.text
    assert s.get("rules", "plain")["condition"] == plain["condition"]


def test_disabling_a_referrer_of_a_deleted_variable_is_never_refused(world):
    s, client, hdr = world
    _delete(client, hdr["admin"], apply=True)
    s.put("rules", _rule("stale"))  # an enabled referrer written behind the verbs' back
    r = client.post("/rules/stale/disable", headers=hdr["editor"])
    assert r.status_code == 200, r.text
    assert s.get("rules", "stale")["enabled"] is False
    assert _guard(s)["by"] == "variables.delete"  # disabling writes no guard


def test_enabling_a_referrer_writes_the_variable_guard(world):
    s, client, hdr = world
    s.put("rules", _rule("off", enabled=False))
    assert client.post("/rules/off/enable", headers=hdr["editor"]).status_code == 200
    assert _guard(s)["by"] == "definitions.set_enabled"


def test_saving_an_enabled_referrer_writes_the_variable_guard(world):
    s, client, hdr = world
    assert client.post("/rules", json=_rule("on"), headers=hdr["editor"]).status_code == 201
    assert _guard(s)["by"] == "definitions.create"


def test_saving_a_disabled_referrer_writes_no_guard(world):
    s, client, hdr = world
    off = _rule("off", enabled=False)
    assert client.post("/rules", json=off, headers=hdr["editor"]).status_code == 201
    assert _guard(s) is None


def test_restoring_an_enabled_referrer_writes_the_variable_guard(world):
    s, client, hdr = world
    assert client.post("/rules", json=_rule("binned"), headers=hdr["editor"]).status_code == 201
    assert client.delete("/rules/binned", headers=hdr["editor"]).status_code == 200
    assert client.post("/rules/binned/restore", headers=hdr["editor"]).status_code == 200
    assert _guard(s)["by"] == "lifecycle.restore"


def test_importing_an_enabled_referrer_writes_the_variable_guard(world):
    s, _client, _hdr = world
    files = {"rules/imp.json": json.dumps(_rule("imp"))}
    Definitions(s).import_files(files, "editor", apply=True)
    assert _guard(s)["by"] == "definitions.import"


def test_importing_a_referrer_of_a_deleted_variable_is_refused(world):
    s, client, hdr = world
    _delete(client, hdr["admin"], apply=True)
    files = {"rules/imp.json": json.dumps(_rule("imp"))}
    with pytest.raises(Invalid) as exc:
        Definitions(s).import_files(files, "editor", apply=True)
    assert exc.value.errors[0]["code"] == "variable_undefined"
    assert s.get("rules", "imp") is None
