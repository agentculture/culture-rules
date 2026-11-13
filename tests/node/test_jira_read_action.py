"""jira.read action port: the whole issue as plain data, read-only (fake transport)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.extensions import registry
from culture_rules.extensions.jira.read import JiraReadPort
from culture_rules.store.memory import MemoryStore

FAKE_TOKEN = "jira" + "-" + "FAKETOKEN0123456789abcdef"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)

ISSUE = {
    "key": "ACME-7",
    "fields": {
        "summary": "The printer is on fire",
        "project": {"key": "ACME"},
        "description": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Again."}]}],
        },
        "reporter": {"accountId": "acc-rep"},
        "customfield_10010": "high",
    },
}
COMMENTS = {
    "total": 1,
    "comments": [
        {
            "id": "c1",
            "author": {"accountId": "acc-1"},
            "created": "2026-11-13T10:00:00.000+0000",
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hi"}]}],
            },
        }
    ],
}
FIELDS = [{"id": "customfield_10010", "name": "Severity"}]


class Fake:
    """Routes GETs by path; any other method is recorded and refused."""

    def __init__(self, status=200):
        self.calls = []
        self.status = status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        if self.status >= 400:
            return self.status, b"{}"
        if method != "GET":
            return 405, b"{}"
        path = url.split("?", 1)[0]
        if path.endswith("/comment"):
            return 200, json.dumps(COMMENTS).encode()
        if path.endswith("/rest/api/3/field"):
            return 200, json.dumps(FIELDS).encode()
        return 200, json.dumps(ISSUE).encode()


def actor_doc(surface="jira", enabled=True, **conn):
    connection = {
        "site": "acme.atlassian.net",
        "email": "bot@example.com",
        "token": "grant:JIRA_TOKEN",
        "projects": ["ACME"],
    }
    connection.update(conn)
    doc = {
        "id": "jira-app",
        "name": "jira",
        "kind": "app",
        "params": {"surface": surface, "connection": connection},
        "schema_version": "1.0",
    }
    if not enabled:
        doc["enabled"] = False
    return doc


def setup(fake, doc=None, fail=False):
    store = MemoryStore()
    store.put("actors", doc or actor_doc())
    resolved = []

    def secrets(ref):
        resolved.append(ref)
        if fail:
            raise RuntimeError("grant exploded " + FAKE_TOKEN)
        return FAKE_TOKEN

    return JiraReadPort(store, transport=fake, secrets=secrets), resolved


def ctx(actor="jira-app"):
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor=actor)


def params(issue="ACME-7", **extra):
    return {"actor": "jira-app", "issue": issue, **extra}


def test_port_flags():
    assert JiraReadPort(MemoryStore()).supports_idempotency_key is True


def test_read_returns_the_whole_issue_as_plain_data():
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke(params(fields=["Severity"]), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed", res.error
    out = dict(res.output)
    assert out == {
        "key": "ACME-7",
        "project": "ACME",
        "summary": "The printer is on fire",
        "description": "Again.",
        "reporter": "acc-rep",
        "comments": [
            {
                "id": "c1",
                "author": "acc-1",
                "created": "2026-11-13T10:00:00.000+0000",
                "body": "hi",
            }
        ],
        "fields": {"Severity": "high"},
    }
    assert json.loads(json.dumps(out)) == out  # JSON-safe: run state is a MongoDB document
    assert resolved == ["grant:JIRA_TOKEN"]


def test_read_never_writes():
    fake = Fake()
    port, _ = setup(fake)
    port.invoke(params(fields=["Severity"]), "k", DEADLINE, context=ctx())
    assert fake.calls
    assert {m for m, _ in fake.calls} == {"GET"}
    assert not any(u.split("?", 1)[0].endswith("/comment") and m != "GET" for m, u in fake.calls)


def test_fields_are_optional():
    port, _ = setup(Fake())
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert res.output["fields"] == {}


@pytest.mark.parametrize("bad", ["Severity", [1], {"a": 1}])
def test_fields_must_be_a_list_of_strings(bad):
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke(params(fields=bad), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("bad_input", False)
    assert fake.calls == [] and resolved == []


def test_actor_falls_back_to_input():
    port, _ = setup(Fake())
    res = port.invoke(params(), "k", DEADLINE, context=ctx(actor=None))
    assert res.outcome == "completed"


def test_not_allowlisted_project_fails_before_the_secret():
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke(params("OTHER-1"), "k", DEADLINE, context=ctx())
    assert (res.outcome, res.error, res.retryable) == ("failed", "project_not_allowed", False)
    assert fake.calls == []
    assert resolved == []


@pytest.mark.parametrize("issue", [None, "", 7, "NOHYPHEN", "-5"])
@pytest.mark.parametrize("projects", [[], ["ACME"]])
def test_a_malformed_issue_is_bad_input(issue, projects):
    fake = Fake()
    port, resolved = setup(fake, actor_doc(projects=projects))
    res = port.invoke({"actor": "jira-app", "issue": issue}, "k", DEADLINE, context=ctx())
    assert res.error == "bad_input"
    assert fake.calls == [] and resolved == []


@pytest.mark.parametrize("projects", [None, []])
def test_absent_or_empty_projects_allows_every_project(projects):
    """As the guide (docs/actors/jira.md) and the webhook's allowlist read it."""
    doc = actor_doc()
    if projects is None:
        del doc["params"]["connection"]["projects"]
    else:
        doc["params"]["connection"]["projects"] = projects
    port, _ = setup(Fake(), doc=doc)
    assert port.invoke(params(), "k", DEADLINE, context=ctx()).outcome == "completed"


def test_allowlist_is_case_insensitive():
    port, _ = setup(Fake(), doc=actor_doc(projects=["acme"]))
    assert port.invoke(params(), "k", DEADLINE, context=ctx()).outcome == "completed"


@pytest.mark.parametrize(
    "doc,actor",
    [
        (actor_doc(), "nope"),
        (actor_doc(surface="github"), "jira-app"),
        (actor_doc(enabled=False), "jira-app"),
    ],
    ids=["missing", "non-jira", "disabled"],
)
def test_non_jira_missing_or_disabled_actor_is_not_found(doc, actor):
    fake = Fake()
    port, resolved = setup(fake, doc=doc)
    res = port.invoke(params(), "k", DEADLINE, context=ctx(actor=actor))
    assert (res.error, res.retryable) == ("actor_not_found", False)
    assert fake.calls == [] and resolved == []


def test_retryability_4xx_vs_5xx():
    port, _ = setup(Fake(status=404))
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.outcome, res.retryable) == ("failed", False)
    port, _ = setup(Fake(status=503))
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("http_503", True)


def test_secret_failure_and_no_token_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake()
    port, _ = setup(fake, fail=True)
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("secret_unavailable", False)
    assert fake.calls == []
    port, _ = setup(Fake(status=500))
    port.invoke(params(), "k", DEADLINE, context=ctx())
    assert FAKE_TOKEN not in caplog.text
    assert FAKE_TOKEN not in repr(res)


# ------------------------------------------------------------------ the registry


def test_the_kind_is_registered_with_typed_params():
    spec = registry().action_kinds()["jira.read"]
    assert spec.declarable_by_apps is True
    assert set(spec.params) == {"actor", "issue", "fields"}
    assert (spec.params["actor"].type, spec.params["actor"].required) == ("str", True)
    assert (spec.params["issue"].type, spec.params["issue"].required) == ("str", True)
    assert (spec.params["fields"].type, spec.params["fields"].required) == ("list", False)


def test_the_port_is_registered():
    from culture_rules.node import runner

    ports = runner.default_ports(MemoryStore(), "spark")
    assert isinstance(ports["action:jira.read"], JiraReadPort)
