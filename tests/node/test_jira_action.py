"""jira.comment action port: allowlist, secret refs, result mapping (fake transport)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.jira import JiraCommentPort
from culture_rules.store.memory import MemoryStore

FAKE_TOKEN = "jira" + "-" + "FAKETOKEN0123456789abcdef"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)


class Fake:
    def __init__(self, status=201):
        self.calls = []
        self.status = status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        if self.status >= 400:
            return self.status, b"{}"
        return self.status, json.dumps({"id": "10001"}).encode()


def actor_doc(surface="jira", **conn):
    connection = {
        "site": "acme.atlassian.net",
        "email": "bot@example.com",
        "token": "grant:JIRA_TOKEN",
        "projects": ["ACME"],
    }
    connection.update(conn)
    return {
        "id": "jira-app",
        "name": "jira",
        "kind": "service",
        "params": {"surface": surface, "connection": connection},
        "schema_version": "1.0",
    }


def setup(fake, doc=None, fail=False):
    store = MemoryStore()
    store.put("actors", doc or actor_doc())
    resolved = []

    def secrets(ref):
        resolved.append(ref)
        if fail:
            raise RuntimeError("grant exploded " + FAKE_TOKEN)
        return FAKE_TOKEN

    return JiraCommentPort(store, transport=fake, secrets=secrets), resolved


def ctx(actor="jira-app"):
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor=actor)


def params(issue="ACME-7"):
    return {"actor": "jira-app", "issue": issue, "body": "hi"}


def test_port_flags():
    assert JiraCommentPort(MemoryStore()).supports_idempotency_key is False


def test_allowlisted_comment_completes():
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert dict(res.output) == {"comment_id": "10001", "issue": "ACME-7"}
    assert fake.calls[-1][1].endswith("/rest/api/3/issue/ACME-7/comment")
    assert resolved == ["grant:JIRA_TOKEN"]


def test_actor_falls_back_to_input():
    port, _ = setup(Fake())
    res = port.invoke(params(), "k", DEADLINE, context=ctx(actor=None))
    assert res.outcome == "completed"


def test_not_allowlisted_project_no_call_no_secret():
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke(params("OTHER-1"), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "project_not_allowed"
    assert res.retryable is False
    assert fake.calls == []
    assert resolved == []


def test_missing_actor_and_wrong_surface():
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke(params(), "k", DEADLINE, context=ctx(actor="nope"))
    assert res.error == "actor_not_found"
    store = MemoryStore()
    store.put("actors", actor_doc(surface="github"))
    port2 = JiraCommentPort(store, transport=fake, secrets=lambda r: FAKE_TOKEN)
    assert port2.invoke(params(), "k", DEADLINE, context=ctx()).error == "actor_not_found"
    assert fake.calls == []
    assert resolved == []


def test_bad_input():
    port, _ = setup(Fake())
    res = port.invoke({"actor": "jira-app", "issue": "ACME-7"}, "k", DEADLINE, context=ctx())
    assert res.error == "bad_input"


def test_retryability_4xx_vs_5xx():
    port, _ = setup(Fake(status=403))
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("http_403", False)
    port, _ = setup(Fake(status=503))
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("http_503", True)


def test_secret_failure_and_no_token_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    port, _ = setup(Fake(), fail=True)
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("secret_unavailable", False)
    port, _ = setup(Fake(status=500))
    port.invoke(params(), "k", DEADLINE, context=ctx())
    assert FAKE_TOKEN not in caplog.text
    assert FAKE_TOKEN not in repr(res)
