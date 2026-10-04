"""A validly signed Access assertion that names no person and no service is refused over HTTP."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import ACCESS_HEADER  # noqa: E402
from culture_rules.server import serve as serve_mod  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.auth.jwks import AUD, TEAM, jwks, wall_clock_token  # noqa: E402

ENV = {
    "CULTURE_RULES_ACCESS_LISTEN": "127.0.0.1:8766",
    "CULTURE_RULES_ACCESS_TEAM_DOMAIN": TEAM,
    "CULTURE_RULES_ACCESS_AUD": AUD,
    "CULTURE_RULES_EDITORS": "alice@example.com",
}


@pytest.fixture
def loopback():
    listeners = serve_mod.build_listeners(MemoryStore(), env=ENV, fetch_jwks=jwks)
    return TestClient({lst.name: lst for lst in listeners}["loopback"].app)


def test_a_valid_token_names_the_person_so_the_fixture_is_sound(loopback):
    r = loopback.get("/whoami", headers={ACCESS_HEADER: wall_clock_token()})
    assert r.status_code == 200
    assert r.json()["identity"] == "alice@example.com"


@pytest.mark.parametrize(
    "changes",
    [
        {"email": None, "sub": None},  # names nothing at all
        {"email": None},  # a subject but no email and no common_name
        {"sub": None},  # an email but no subject, and no common_name
    ],
    ids=["no-person-no-service", "sub-only", "email-only"],
)
def test_signed_token_naming_no_person_or_service_is_401_malformed(loopback, changes):
    r = loopback.get("/whoami", headers={ACCESS_HEADER: wall_clock_token(**changes)})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "malformed"


def test_a_service_token_naming_only_a_common_name_is_accepted(loopback):
    tok = wall_clock_token(email=None, sub=None, common_name="ci-bot.access")
    r = loopback.get("/whoami", headers={ACCESS_HEADER: tok})
    assert r.status_code == 200
    assert r.json()["kind"] == "service"
