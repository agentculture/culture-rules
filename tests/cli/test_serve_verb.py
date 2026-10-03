"""`culture-rules serve` maps a missing/unconfigured store to exit 2 with a remediation."""

from __future__ import annotations

import json

import pytest

from culture_rules.cli import main

pytest.importorskip("fastapi")


@pytest.fixture
def no_store_env(monkeypatch):
    for name in (
        "CULTURE_RULES_MONGO_URI",
        "CULTURE_RULES_MONGO_DB",
        "CULTURE_RULES_MONGO_TLS_CA_FILE",
        "CULTURE_RULES_MONGO_TLS_CERT_KEY_FILE",
        "CULTURE_RULES_ACCESS_LISTEN",
        "CULTURE_RULES_ACCESS_TEAM_DOMAIN",
        "CULTURE_RULES_ACCESS_AUD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_serve_without_a_store_exits_2_with_a_remediation(no_store_env, capsys):
    assert main(["serve", "--port", "1"]) == 2
    err = capsys.readouterr().err
    assert "CULTURE_RULES_MONGO_URI" in err
    assert "culture-rules[store]" in err


def test_serve_without_a_store_json_error(no_store_env, capsys):
    assert main(["serve", "--port", "1", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["code"] == 2
    assert "CULTURE_RULES_MONGO_URI" in payload["message"]
    assert "culture-rules[store]" in payload["remediation"]
    assert "CULTURE_RULES_MONGO_URI" in payload["remediation"]


def test_serve_maps_any_store_error_to_exit_2(monkeypatch, capsys):
    from culture_rules.server import serve as serve_mod
    from culture_rules.store.port import StoreError

    def unreachable(**kw):
        raise StoreError("no replica set primary")

    monkeypatch.setattr(serve_mod, "serve", unreachable)
    assert main(["serve"]) == 2
    assert "no replica set primary" in capsys.readouterr().err
