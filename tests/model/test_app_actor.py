"""Actor kind ``app``: declared events, probes, actions; secrets only as grant refs (t5)."""

from __future__ import annotations

import copy

import pytest

from culture_rules.model.actor import ACTOR_KINDS, Actor
from culture_rules.model.app_actor import (
    APP_SURFACES,
    EMAIL_DEFAULT_DOMAIN,
    EMAIL_DEFAULT_LOCAL_PART,
    EMAIL_DOMAINS,
    email_sender,
)
from culture_rules.model.validate import validate

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import AuthSettings  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

GITHUB = {
    "surface": "github",
    "events": ["github.pr.opened", "github.issue.commented"],
    "probes": [{"name": "rate", "command": "gh api rate_limit", "schedule": "*/5 * * * *"}],
    "actions": ["github.comment"],
    "connection": {
        "app_id": "123",
        "installation_id": "456",
        "private_key": "grant:GH_APP_KEY",
        "webhook_secret": "grant:GH_WEBHOOK",
        "repos": ["agentculture/culture-rules"],
    },
    "self_identity": "culture-bot[bot]",
}


def app_actor(**params) -> Actor:
    merged = {**copy.deepcopy(GITHUB), **params}
    return Actor(id="gh", name="GitHub", kind="app", params=merged)


def codes(actor: Actor) -> set[tuple[str, str]]:
    return {(e.path, e.code) for e in validate(actor)}


def test_kind_registered() -> None:
    assert "app" in ACTOR_KINDS


def test_valid_github_app() -> None:
    assert validate(app_actor()) == []


def test_valid_jira_and_discord() -> None:
    jira = Actor(
        id="j",
        name="Jira",
        kind="app",
        params={
            "surface": "jira",
            "events": ["jira.issue.created"],
            "actions": ["jira.comment"],
            "connection": {
                "site": "x.atlassian.net",
                "email": "a@b.c",
                "token": "grant:JIRA",
                "webhook_token": "grant:JIRA_HOOK",
                "projects": ["ABC"],
            },
        },
    )
    discord = Actor(
        id="d",
        name="Discord",
        kind="app",
        params={
            "surface": "discord",
            "events": ["discord.message.created"],
            "actions": ["message"],
            "connection": {"bot_token": "grant:DISCORD", "guild_id": "1", "channels": ["2"]},
        },
    )
    assert validate(jira) == []
    assert validate(discord) == []


def test_unknown_surface() -> None:
    assert ("params.surface", "invalid_value") in codes(app_actor(surface="slack"))


@pytest.mark.parametrize("event", ["pr_opened", "GitHub.PR", "github..pr", "github.", ""])
def test_undotted_event_rejected(event: str) -> None:
    assert ("params.events[0]", "invalid_event_type") in codes(app_actor(events=[event]))


def test_undeclared_action_kind_rejected() -> None:
    errs = codes(app_actor(actions=["github.comment", "github.explode"]))
    assert ("params.actions[1]", "unknown_action_kind") in errs
    assert ("params.actions[0]", "unknown_action_kind") not in errs


def test_malformed_lists_do_not_raise() -> None:
    errs = codes(app_actor(events="github.pr.opened", actions=[3], probes=["x"]))
    assert ("params.events", "invalid_type") in errs
    assert ("params.actions[0]", "invalid_type") in errs
    assert ("params.probes[0]", "invalid_type") in errs


def test_probe_needs_name_and_command() -> None:
    errs = codes(app_actor(probes=[{"schedule": "* * * * *"}]))
    assert ("params.probes[0].name", "required") in errs
    assert ("params.probes[0].command", "required") in errs


@pytest.mark.parametrize(
    ("value", "code"),
    [(None, "required"), ("", "required"), (42, "invalid_type"), (["gh"], "invalid_type")],
)
def test_probe_name_and_command_codes(value, code) -> None:
    errs = codes(app_actor(probes=[{"name": value, "command": value}]))
    assert ("params.probes[0].name", code) in errs
    assert ("params.probes[0].command", code) in errs


def test_surface_and_connection_required() -> None:
    actor = Actor(id="a", name="a", kind="app", params={})
    errs = codes(actor)
    assert ("params.surface", "required") in errs
    assert ("params.connection", "required") in errs


def test_non_app_actor_params_untouched() -> None:
    assert validate(Actor(id="a", name="a", kind="agent", params={"events": "x"})) == []


# --- secrets only as grant refs, through the real save path --------------------------------

DEV = AuthSettings(insecure_dev_identity=True)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(MemoryStore(), auth=DEV))


def body(**conn) -> dict:
    actor = app_actor()
    params = copy.deepcopy(actor.params)
    params["connection"].update(conn)
    return {"id": "gh", "name": "GitHub", "kind": "app", "params": params}


def test_grant_ref_accepted_on_save(client) -> None:
    assert client.post("/actors", json=body()).status_code == 201


@pytest.mark.parametrize("key", ["private_key", "webhook_secret"])
def test_literal_in_connection_refused(client, key) -> None:
    resp = client.post("/actors", json=body(**{key: "ghp_literal_value_123"}))
    assert resp.status_code == 422
    assert "secret_literal" in resp.text
    assert "ghp_literal_value_123" not in resp.text


def test_literal_refused_on_put_too(client) -> None:
    assert client.post("/actors", json=body()).status_code == 201
    resp = client.put("/actors/gh", json=body(private_key="-----BEGIN KEY-----"))
    assert resp.status_code == 422
    assert "secret_literal" in resp.text


LITERAL_SHAPES = [
    pytest.param({"private_key": ["literal-in-list"]}, id="list"),
    pytest.param({"private_key": 12345}, id="number"),
    pytest.param({"webhook_secret": {"value": "literal-in-dict"}}, id="dict"),
    pytest.param({"webhook_secret": True}, id="bool"),
]


@pytest.mark.parametrize("conn", LITERAL_SHAPES)
def test_non_string_literal_under_secret_key_refused(client, conn) -> None:
    resp = client.post("/actors", json=body(**conn))
    assert resp.status_code == 422
    assert "secret_literal" in resp.text
    for leaked in ("literal-in-list", "12345", "literal-in-dict"):
        assert leaked not in resp.text


def test_non_secret_nested_structures_still_accepted(client) -> None:
    conn = {"repos": ["a/b"], "extra": {"labels": ["x"], "max_tokens": 5}, "token_budget": 3}
    assert client.post("/actors", json=body(**conn)).status_code == 201


def test_empty_secret_stays_tolerated(client) -> None:
    assert client.post("/actors", json=body(webhook_secret="")).status_code == 201
    assert client.put("/actors/gh", json=body(webhook_secret=None)).status_code == 200


# --- the email and search surfaces (#53 t1) ------------------------------------------------

EMAIL_PATH = "params.connection"
INVALID = "invalid_value"
EMAIL_SEND = "email.send"
DOMAIN_PATH = f"{EMAIL_PATH}.domain"
EMAIL, SEARCH = "email", "search"
CULTURE_DEV, JETSON = "culture.dev", "jetson-ai-lab.org"
OUTSIDE = "example.com"
OPERATOR = "reader@example.org"
FAKE_LITERAL_KEY = "re_literal_value_123"  # not a real key: a literal the save must refuse


def email_conn(**over) -> dict:
    conn = {
        "api_key": "grant:RESEND_API_KEY",
        "domain": CULTURE_DEV,
        "local_part": "newsletter-no-reply",
        "recipients": [OPERATOR],
    }
    conn.update(over)
    return {k: v for k, v in conn.items() if v is not None}


def surface_actor(surface: str, connection: dict, **params) -> Actor:
    merged = {"surface": surface, "connection": connection, **params}
    return Actor(id=surface, name=surface, kind="app", params=merged)


def email_codes(**over) -> set[tuple[str, str]]:
    return codes(surface_actor(EMAIL, email_conn(**over), actions=[EMAIL_SEND]))


def test_email_and_search_are_surfaces() -> None:
    assert {EMAIL, SEARCH} <= set(APP_SURFACES)


def test_email_constants() -> None:
    assert EMAIL_DOMAINS == (CULTURE_DEV, JETSON)
    assert EMAIL_DEFAULT_LOCAL_PART == "no-reply"
    assert EMAIL_DEFAULT_DOMAIN == CULTURE_DEV


@pytest.mark.parametrize("domain", [CULTURE_DEV, JETSON])
def test_valid_email_actor(domain: str) -> None:
    assert email_codes(domain=domain) == set()


def test_email_local_part_and_domain_are_optional() -> None:
    assert email_codes(domain=None, local_part=None) == set()


def test_email_sender_defaults_to_no_reply_at_culture_dev() -> None:
    assert email_sender({}) == "no-reply@culture.dev"


def test_email_sender_uses_the_connection() -> None:
    conn = email_conn(domain=JETSON)
    assert email_sender(conn) == "newsletter-no-reply@jetson-ai-lab.org"


@pytest.mark.parametrize("domain", [OUTSIDE, "culture.dev.evil.org", "CULTURE.DEV ", 3])
def test_email_domain_outside_the_enum_refused(domain) -> None:
    assert (DOMAIN_PATH, INVALID) in email_codes(domain=domain)


@pytest.mark.parametrize("local", ["no reply", "a@b", "", ".lead", "trail.", "a..b", "x\n", 7])
def test_email_bad_local_part_refused(local) -> None:
    assert (f"{EMAIL_PATH}.local_part", INVALID) in email_codes(local_part=local)


@pytest.mark.parametrize(
    "recipient",
    ["no-at-sign", "two@@at.org", "a b@c.org", "a@b", "a@c.org\r\nBcc: x@y.org", "a@c.org\n", 5],
)
def test_email_malformed_recipient_refused(recipient) -> None:
    errs = email_codes(recipients=[OPERATOR, recipient])
    assert (f"{EMAIL_PATH}.recipients[1]", INVALID) in errs


def test_email_good_recipient_not_flagged() -> None:
    errs = email_codes(recipients=[OPERATOR, "bad"])
    assert (f"{EMAIL_PATH}.recipients[0]", INVALID) not in errs


@pytest.mark.parametrize("recipients", [None, OPERATOR])
def test_email_recipients_must_be_a_list(recipients) -> None:
    errs = email_codes(recipients=recipients)
    assert {code for path, code in errs if path == f"{EMAIL_PATH}.recipients"} != set()


def test_an_empty_recipient_allowlist_is_valid() -> None:
    """h18: the shipped actor's allow-list is empty until the operator seeds it."""
    errs = email_codes(recipients=[])
    assert [e for e in errs if e[0].startswith(f"{EMAIL_PATH}.recipients")] == []


@pytest.mark.parametrize("surface", [EMAIL, SEARCH])
def test_api_key_must_be_a_grant_ref(surface: str) -> None:
    actor = surface_actor(surface, {**email_conn(), "api_key": FAKE_LITERAL_KEY})
    assert (f"{EMAIL_PATH}.api_key", "secret_literal") in codes(actor)


@pytest.mark.parametrize("surface", [EMAIL, SEARCH])
def test_api_key_required(surface: str) -> None:
    conn = email_conn(api_key=None) if surface == EMAIL else {}
    assert (f"{EMAIL_PATH}.api_key", "required") in codes(surface_actor(surface, conn))


def test_valid_search_actor() -> None:
    conn = {"api_key": "grant:BRAVE_API_KEY"}
    assert codes(surface_actor(SEARCH, conn, actions=["search.web"])) == set()


def test_github_connection_still_unchecked_per_field() -> None:
    assert validate(app_actor(connection={"anything": 1})) == []


def email_body(**over) -> dict:
    params = {"surface": EMAIL, "actions": ["email.send"], "connection": email_conn(**over)}
    return {"id": "mail", "name": "Mail", "kind": "app", "params": params}


def test_api_saves_an_email_actor(client) -> None:
    assert client.post("/actors", json=email_body()).status_code == 201


def test_api_refuses_a_domain_outside_the_enum(client) -> None:
    resp = client.post("/actors", json=email_body(domain=OUTSIDE))
    assert resp.status_code == 422
    assert DOMAIN_PATH in resp.text


def test_api_refuses_a_malformed_recipient(client) -> None:
    resp = client.post("/actors", json=email_body(recipients=["not an address"]))
    assert resp.status_code == 422
    assert f"{EMAIL_PATH}.recipients[0]" in resp.text


def test_api_refuses_a_literal_key_without_echoing_it(client) -> None:
    resp = client.post("/actors", json=email_body(api_key=FAKE_LITERAL_KEY))
    assert resp.status_code == 422
    assert f"{EMAIL_PATH}.api_key" in resp.text
    assert FAKE_LITERAL_KEY not in resp.text


def test_api_refuses_a_bad_domain_on_put_too(client) -> None:
    assert client.post("/actors", json=email_body()).status_code == 201
    resp = client.put("/actors/mail", json=email_body(domain=OUTSIDE))
    assert resp.status_code == 422
    assert DOMAIN_PATH in resp.text
