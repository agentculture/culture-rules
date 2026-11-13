"""Jira webhook receiver: ``data.author`` is the actor's accountId on every event."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.extensions.jira.hooks import router  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.server.test_hooks_jira import (  # noqa: E402
    FakeClient,
    actor,
    events,
    payload,
    resolver,
    sign,
)


@pytest.fixture
def store():
    s = MemoryStore()
    s.insert("actors", actor())
    return s


@pytest.fixture
def http(store):
    fake = FakeClient()
    app = FastAPI()
    app.include_router(router(store, secrets=resolver, client_factory=lambda conn, res: fake))
    return TestClient(app)


def _post(http, body):
    return http.post("/hooks/jira", content=body, headers={"X-Hub-Signature": sign(body)})


def test_issue_created_author_is_payload_user(http, store):
    assert _post(http, payload(event="jira:issue_created")).status_code == 202
    (ev,) = events(store)
    assert ev["type"] == "jira.issue.created"
    data = ev["data"]
    assert data["author"] == "acc-1"
    assert data["self_authored"] is False
    assert data["key"] == "OPS-7"
    assert data["assignee"] == "acc-9"


def test_comment_created_author_is_comment_author(http, store):
    body = payload(
        event="comment_created",
        comment={"id": "55", "body": "hi", "author": {"accountId": "bot-acct"}},
    )
    assert _post(http, body).status_code == 202
    (ev,) = events(store)
    assert ev["data"]["author"] == "bot-acct"
    assert ev["data"]["self_authored"] is True
    assert ev["data"]["comment_id"] == "55"
    assert ev["data"]["comment_body"] == "hi"


def test_no_author_stores_null_and_nothing_else_changes(http, store):
    doc = json.loads(payload(event="jira:issue_updated"))
    del doc["user"]
    assert _post(http, json.dumps(doc).encode()).status_code == 202
    (ev,) = events(store)
    data = ev["data"]
    assert "author" in data
    assert data["author"] is None
    assert data["self_authored"] is False
    assert data["key"] == "OPS-7"
    assert data["summary"] == "Broken thing"
