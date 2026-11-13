"""jira.comment: an absent or empty connection.projects allows every project."""

from __future__ import annotations

import pytest

from tests.node.test_jira_action import DEADLINE, Fake, actor_doc, ctx, params, setup


def _doc_without_projects():
    doc = actor_doc()
    del doc["params"]["connection"]["projects"]
    return doc


@pytest.mark.parametrize("doc", [_doc_without_projects(), actor_doc(projects=[])])
def test_absent_or_empty_projects_allow_any_issue(doc):
    fake = Fake()
    port, resolved = setup(fake, doc)
    res = port.invoke(params("OTHER-1"), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert fake.calls
    assert fake.calls[-1][1].endswith("/issue/OTHER-1/comment")
    assert resolved == ["grant:JIRA_TOKEN"]


def test_listed_projects_still_refuse_others_before_secret():
    fake = Fake()
    port, resolved = setup(fake, actor_doc(projects=["SCRUM"]))
    res = port.invoke(params("OTHER-1"), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "project_not_allowed"
    assert resolved == []
    assert fake.calls == []
    assert port.invoke(params("SCRUM-2"), "k", DEADLINE, context=ctx()).outcome == "completed"


@pytest.mark.parametrize("issue", [None, 7, "NOHYPHEN", "-5", ""])
@pytest.mark.parametrize("projects", [[], ["SCRUM"]])
def test_malformed_issue_is_bad_input(issue, projects):
    fake = Fake()
    port, resolved = setup(fake, actor_doc(projects=projects))
    res = port.invoke(params(issue), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "bad_input"
    assert resolved == []
    assert fake.calls == []


@pytest.mark.parametrize("body", ["", "\n", "  \n\t\n", "   "])
def test_an_empty_or_blank_body_is_bad_input_before_secret(body):
    fake = Fake()
    port, resolved = setup(fake, actor_doc(projects=["SCRUM"]))
    res = port.invoke({**params("SCRUM-1"), "body": body}, "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "bad_input"
    assert resolved == []
    assert fake.calls == []
