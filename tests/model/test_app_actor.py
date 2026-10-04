"""Actor kind ``app``: declared events, probes, actions; secrets only as grant refs (t5)."""

from __future__ import annotations

import copy

import pytest

from culture_rules.model.actor import ACTOR_KINDS, Actor
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
