"""The shared Jira project allowlist: one normaliser for the webhook, jira.comment, jira.read."""

from __future__ import annotations

import pytest

from culture_rules.extensions.jira.hooks import handle
from culture_rules.extensions.jira.port import JiraCommentPort
from culture_rules.extensions.jira.projects import allowed_projects, project_allowed, well_formed
from culture_rules.extensions.jira.read import JiraReadPort
from culture_rules.store.memory import MemoryStore
from tests.node import test_jira_action as comment_t
from tests.node import test_jira_read_action as read_t
from tests.server import test_hooks_jira as hook_t


@pytest.mark.parametrize("conn", [{}, {"projects": []}, {"projects": None}, {"projects": ""}])
def test_absent_or_empty_allows_all(conn):
    allowed = allowed_projects(conn)
    assert allowed == frozenset()
    assert project_allowed("ANY-1", allowed) is True


def test_uppercases_both_sides():
    allowed = allowed_projects({"projects": ["scrum", "Ops"]})
    assert allowed == frozenset({"SCRUM", "OPS"})
    assert project_allowed("SCRUM-1", allowed)
    assert project_allowed("scrum-1", allowed)
    assert project_allowed("ops-9", allowed)
    assert not project_allowed("OTHER-1", allowed)


def test_a_bare_string_is_a_one_item_list():
    allowed = allowed_projects({"projects": "scrum"})
    assert allowed == frozenset({"SCRUM"})
    assert not project_allowed("S-1", allowed)


def test_lowercase_list_admits_scrum_1_in_comment():
    fake = comment_t.Fake()
    port, resolved = comment_t.setup(fake, comment_t.actor_doc(projects=["scrum"]))
    res = port.invoke(comment_t.params("SCRUM-1"), "k", comment_t.DEADLINE, context=comment_t.ctx())
    assert res.outcome == "completed"
    assert isinstance(port, JiraCommentPort)


def test_lowercase_list_admits_scrum_1_in_read():
    fake = read_t.Fake()
    port, _ = read_t.setup(fake, read_t.actor_doc(projects=["scrum"]))
    res = port.invoke(read_t.params("SCRUM-1"), "k", read_t.DEADLINE, context=read_t.ctx())
    assert res.outcome == "completed"
    assert isinstance(port, JiraReadPort)


def test_lowercase_list_admits_scrum_1_in_webhook_and_ignores_others():
    store = MemoryStore()
    store.put("actors", hook_t.actor(projects=["scrum"]))
    fake = hook_t.FakeClient()
    for key, ignored in (("SCRUM-1", False), ("OTHER-1", True)):
        body = hook_t.payload(key=key)
        status, out = handle(
            store,
            body=body,
            headers={"X-Hub-Signature": hook_t.sign(body)},
            query={},
            secrets=hook_t.resolver,
            client_factory=lambda c, r: fake,
        )
        assert (status, out) == (200, {"ignored": True}) if ignored else status == 202
    assert fake.fetched == ["SCRUM-1"]


@pytest.mark.parametrize("issue", [None, 7, "", "NOHYPHEN", "-5"])
def test_malformed_keys_are_not_well_formed(issue):
    assert well_formed(issue) is False


@pytest.mark.parametrize("issue", ["SCRUM-1", "a-b-3"])
def test_prefixed_keys_are_well_formed(issue):
    assert well_formed(issue) is True
