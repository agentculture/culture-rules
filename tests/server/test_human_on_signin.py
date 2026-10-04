"""t16: a person's first Cloudflare Access sign-in creates their human actor, once.

The actor id is the slugged email (``ori.nachum@gmail.com`` -> ``ori-nachum-gmail-com``).
Service tokens (bearer or Access service tokens), the LAN listener and the exempt webhook
paths never create one, a later sign-in never overwrites an operator's edit, and a store
failure while ensuring is logged and never fails the request.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.principal import Principal  # noqa: E402
from culture_rules.auth.resolve import ACCESS_HEADER  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.engine.audit import AUDIT_COLLECTION  # noqa: E402
from culture_rules.server import humans  # noqa: E402
from culture_rules.server import serve as serve_mod  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from culture_rules.store.port import StoreError  # noqa: E402
from tests.auth.jwks import AUD, TEAM, jwks, wall_clock_token  # noqa: E402
from tests.server.conftest import dev_app  # noqa: E402

ENV = {
    "CULTURE_RULES_ACCESS_LISTEN": "127.0.0.1:8766",
    "CULTURE_RULES_ACCESS_TEAM_DOMAIN": TEAM,
    "CULTURE_RULES_ACCESS_AUD": AUD,
}
ALICE_ID = "alice-example-com"


def listeners(store):
    got = serve_mod.build_listeners(store, env=ENV, fetch_jwks=jwks)
    return {lst.name: TestClient(lst.app) for lst in got}


def sso(**claims) -> dict[str, str]:
    return {ACCESS_HEADER: wall_clock_token(**claims)}


def humans_in(store) -> list[dict]:
    return [d for d in store.find("actors") if d.get("kind") == "human"]


def bearer(store, roles=("admin",)) -> dict[str, str]:
    tok = ServiceTokens(store).issue("root", name="ops", roles=list(roles)).token
    return {"Authorization": f"Bearer {tok}"}


# --- the slug ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "email, slug",
    [
        ("ori.nachum@gmail.com", "ori-nachum-gmail-com"),
        ("Alice@Example.COM", "alice-example-com"),
        ("a+tag@sub.example.org", "a-tag-sub-example-org"),
        ("..x__y@z.io", "x-y-z-io"),
    ],
)
def test_human_id_is_the_slugged_email(email, slug):
    assert humans.human_id(email) == slug


# --- first sign-in ------------------------------------------------------------------------


def test_first_sso_request_creates_exactly_one_human_actor():
    store = MemoryStore()
    loop = listeners(store)["loopback"]
    assert loop.get("/whoami", headers=sso()).status_code == 200
    (actor,) = humans_in(store)
    assert actor["id"] == ALICE_ID
    assert actor["kind"] == "human"
    assert actor["name"] == "alice@example.com"
    assert actor["enabled"] is True
    assert actor["config_source"] == "db"
    assert actor["params"]["email"] == "alice@example.com"
    # the GET above and every later sign-in leave it alone
    for _ in range(3):
        assert loop.get("/whoami", headers=sso()).status_code == 200
    assert len(humans_in(store)) == 1


def test_creation_is_audited_as_the_person_themselves():
    store = MemoryStore()
    listeners(store)["loopback"].get("/whoami", headers=sso())
    (entry,) = [e for e in store.find(AUDIT_COLLECTION) if e["target"]["id"] == ALICE_ID]
    assert entry["identity"] == "alice@example.com"
    assert entry["target"]["collection"] == "actors"
    assert entry["verb"] == "definitions.create"


def test_steady_state_costs_no_store_call(monkeypatch):
    store = MemoryStore()
    loop = listeners(store)["loopback"]
    loop.get("/whoami", headers=sso())
    calls = []
    monkeypatch.setattr(humans, "ensure_human", lambda *a, **k: calls.append(a))
    loop.get("/whoami", headers=sso())
    assert calls == []


def test_ten_concurrent_first_requests_create_exactly_one_actor():
    store = MemoryStore()
    loop = listeners(store)["loopback"]
    head = sso()  # sign once: the test keypair is generated lazily, not thread-safely
    with ThreadPoolExecutor(10) as pool:
        codes = list(pool.map(lambda _: loop.get("/whoami", headers=head).status_code, range(10)))
    assert codes == [200] * 10
    assert [a["id"] for a in humans_in(store)] == [ALICE_ID]
    created = [e for e in store.find(AUDIT_COLLECTION) if e["target"]["id"] == ALICE_ID]
    assert len(created) == 1


def test_ten_concurrent_uncached_ensure_calls_create_exactly_one_actor():
    store = MemoryStore()
    who = Principal("ori.nachum@gmail.com", "sso", frozenset({"viewer"}))
    gate = threading.Barrier(10)
    outcomes: list[str] = []

    def go():
        gate.wait()
        outcomes.append(humans.ensure_human(store, who))

    threads = [threading.Thread(target=go) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["created"] + ["exists"] * 9
    assert [a["id"] for a in humans_in(store)] == ["ori-nachum-gmail-com"]


# --- never for anything but a person ------------------------------------------------------


def test_service_token_requests_create_none():
    store = MemoryStore()
    got = listeners(store)
    auth = bearer(store, roles=("viewer",))
    assert got["loopback"].get("/whoami", headers=auth).status_code == 200
    assert got["lan"].get("/whoami", headers=auth).status_code == 200
    assert humans_in(store) == []


def test_access_service_token_creates_none():
    store = MemoryStore()
    loop = listeners(store)["loopback"]
    r = loop.get("/whoami", headers=sso(email=None, common_name="ci-bot.access"))
    assert r.status_code == 200
    assert r.json()["kind"] == "service"
    assert humans_in(store) == []


def test_lan_listener_request_creates_none():
    store = MemoryStore()
    lan = listeners(store)["lan"]
    # the LAN listener ignores the Access header entirely (401) and never creates
    assert lan.get("/whoami", headers=sso()).status_code == 401
    assert lan.get("/whoami", headers=bearer(store)).status_code == 200
    assert humans_in(store) == []


def test_exempt_hook_requests_create_none():
    store = MemoryStore()
    loop = listeners(store)["loopback"]
    loop.post("/hooks/github", headers=sso(), content=b"{}")  # refused: unsigned delivery
    loop.post("/hooks/jira", headers=sso(), content=b"{}")
    assert humans_in(store) == []


def test_dev_identity_creates_only_for_an_email_identity():
    store = MemoryStore()
    c = TestClient(dev_app(store))
    assert c.get("/whoami", headers={"X-Culture-Identity": "alice"}).status_code == 200
    assert c.get("/whoami").status_code == 200  # anonymous
    assert humans_in(store) == []
    assert c.get("/whoami", headers={"X-Culture-Identity": "bob@example.com"}).status_code == 200
    assert [a["id"] for a in humans_in(store)] == ["bob-example-com"]


# --- never overwriting --------------------------------------------------------------------


def test_a_later_sign_in_does_not_overwrite_an_edited_name():
    store = MemoryStore()
    loop = listeners(store)["loopback"]
    loop.get("/whoami", headers=sso())
    admin = bearer(store)
    body = loop.get(f"/actors/{ALICE_ID}", headers=admin).json()
    body = {k: v for k, v in body.items() if k not in ("updated_at",)}
    body["name"] = "Alice A."
    r = loop.put(f"/actors/{ALICE_ID}", headers=admin, json=body)
    assert r.status_code == 200, r.text
    # a fresh process (empty cache) and a sign-in on it
    again = listeners(store)["loopback"]
    assert again.get("/whoami", headers=sso()).status_code == 200
    assert store.get("actors", ALICE_ID)["name"] == "Alice A."
    assert len(humans_in(store)) == 1


def test_a_soft_deleted_human_is_not_resurrected():
    store = MemoryStore()
    loop = listeners(store)["loopback"]
    loop.get("/whoami", headers=sso())
    admin = bearer(store)
    assert loop.delete(f"/actors/{ALICE_ID}", headers=admin).status_code == 200
    again = listeners(store)["loopback"]
    assert again.get("/whoami", headers=sso()).status_code == 200
    doc = store.get("actors", ALICE_ID)
    assert doc.get("deleted_at")
    assert len(humans_in(store)) == 1


# --- failures never fail the request ------------------------------------------------------


class FlakyStore(MemoryStore):
    fail = False

    def transaction(self):
        if self.fail:
            raise StoreError("store is down")
        return super().transaction()


def test_store_failure_is_logged_and_never_fails_the_request(caplog):
    store = FlakyStore()
    loop = listeners(store)["loopback"]
    store.fail = True
    with caplog.at_level(logging.WARNING, logger="culture_rules.server.humans"):
        r = loop.get("/whoami", headers=sso())
    assert r.status_code == 200
    assert r.json()["identity"] == "alice@example.com"
    assert any(ALICE_ID in rec.getMessage() for rec in caplog.records)
    assert all(rec.levelno == logging.WARNING for rec in caplog.records)
    assert humans_in(store) == []
    # a failure is not cached: the next request (store back) creates the actor
    store.fail = False
    assert loop.get("/whoami", headers=sso()).status_code == 200
    assert [a["id"] for a in humans_in(store)] == [ALICE_ID]


# --- d4: link to an existing human actor by params.email -----------------------------------

ALICE = "alice@example.com"


def seed_human(store, ident="nachos", email=ALICE, **extra):
    doc = {
        "id": ident,
        "name": "Ori Nachum",
        "kind": "human",
        "enabled": True,
        "params": {"email": email} if email else {},
        **extra,
    }
    store.insert("actors", doc)


def test_existing_human_with_matching_email_is_linked_not_duplicated():
    store = MemoryStore()
    seed_human(store)
    before = store.get("actors", "nachos")
    signin = humans.HumanSignIn(store)
    who = Principal(ALICE, "sso", frozenset({"viewer"}))
    assert signin.needed(who)
    signin(who)
    assert not signin.needed(who)  # cache marks it known
    assert [a["id"] for a in humans_in(store)] == ["nachos"]
    assert store.get("actors", "nachos") == before
    assert store.get("actors", ALICE_ID) is None
    assert [e for e in store.find(AUDIT_COLLECTION) if e["target"]["id"] == ALICE_ID] == []


def test_ensure_human_reports_exists_for_a_linked_actor():
    store = MemoryStore()
    seed_human(store)
    who = Principal(ALICE, "sso", frozenset({"viewer"}))
    assert humans.ensure_human(store, who) == "exists"


def test_email_match_is_case_insensitive_and_stored_email_kept_as_given():
    store = MemoryStore()
    seed_human(store, email="Alice@Example.COM")
    who = Principal(ALICE, "sso", frozenset({"viewer"}))
    assert humans.ensure_human(store, who) == "exists"
    assert [a["id"] for a in humans_in(store)] == ["nachos"]
    assert store.get("actors", "nachos")["params"]["email"] == "Alice@Example.COM"


def test_soft_deleted_linked_actor_still_counts():
    store = MemoryStore()
    seed_human(store, deleted_at="2026-01-01T00:00:00Z")
    who = Principal(ALICE, "sso", frozenset({"viewer"}))
    assert humans.ensure_human(store, who) == "exists"
    assert store.get("actors", ALICE_ID) is None


def test_human_with_a_different_email_does_not_link():
    store = MemoryStore()
    seed_human(store, email="someone@else.org")
    seed_human(store, ident="noemail", email=None)
    who = Principal(ALICE, "sso", frozenset({"viewer"}))
    assert humans.ensure_human(store, who) == "created"
    assert sorted(a["id"] for a in humans_in(store)) == sorted([ALICE_ID, "noemail", "nachos"])


def test_non_human_actor_with_the_same_email_does_not_link():
    store = MemoryStore()
    store.insert(
        "actors",
        {"id": "bot", "name": "bot", "kind": "agent", "enabled": True, "params": {"email": ALICE}},
    )
    who = Principal(ALICE, "sso", frozenset({"viewer"}))
    assert humans.ensure_human(store, who) == "created"


def test_concurrent_first_sign_ins_with_a_linked_actor_insert_nothing():
    store = MemoryStore()
    seed_human(store)
    who = Principal(ALICE, "sso", frozenset({"viewer"}))
    gate = threading.Barrier(10)
    outcomes: list[str] = []

    def go():
        gate.wait()
        outcomes.append(humans.ensure_human(store, who))

    threads = [threading.Thread(target=go) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes == ["exists"] * 10
    assert [a["id"] for a in humans_in(store)] == ["nachos"]
    assert store.find(AUDIT_COLLECTION) == []


def test_two_humans_with_the_same_email_link_to_the_first_and_warn(caplog):
    store = MemoryStore()
    seed_human(store, ident="b-second")
    seed_human(store, ident="a-first")
    who = Principal(ALICE, "sso", frozenset({"viewer"}))
    with caplog.at_level(logging.WARNING, logger="culture_rules.server.humans"):
        assert humans.ensure_human(store, who) == "exists"
    assert len(humans_in(store)) == 2
    msgs = [r.getMessage() for r in caplog.records]
    assert any("a-first" in m and "b-second" in m for m in msgs)


def test_sign_in_over_http_links_to_the_existing_actor():
    store = MemoryStore()
    seed_human(store)
    loop = listeners(store)["loopback"]
    assert loop.get("/whoami", headers=sso()).status_code == 200
    assert [a["id"] for a in humans_in(store)] == ["nachos"]
