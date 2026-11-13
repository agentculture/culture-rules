"""JiraClient full issue read and ADF comment bodies (fake transport).

The comment pagination shape (``startAt``, ``maxResults``, ``total``, ``comments``) and the
``GET /rest/api/3/field`` list of ``{id, name}`` are encoded here from the Jira Cloud REST v3
docs as remembered, not checked against a live site.
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

from culture_rules.extensions.jira.adf import to_adf
from culture_rules.extensions.jira.client import JiraClient, JiraError

FAKE_TOKEN = "jira" + "-" + "FAKETOKEN0123456789abcdef"
LONG = "x" * 2000


def adf(text):
    return to_adf(text)


def comment(i, author="acc-1"):
    return {
        "id": str(1000 + i),
        "author": {"accountId": author, "displayName": "Someone"},
        "created": f"2026-11-0{1 + i % 9}T10:00:00.000+0000",
        "body": adf(f"comment {i}"),
    }


class Fake:
    """Serves an issue, its paginated comments and the field list."""

    def __init__(self, *, comments=None, page=2, total=None, issue_fields=None, status=200):
        self.calls = []
        self.comments = comments if comments is not None else [comment(i) for i in range(5)]
        self.page = page
        self.total = total
        self.status = status
        self.issue_fields = issue_fields or {}

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        if self.status >= 400:
            return self.status, b"{}"
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path
        if path == "/rest/api/3/field":
            return (
                200,
                json.dumps(
                    [
                        {"id": "customfield_10016", "name": "Story Points"},
                        {"id": "customfield_10020", "name": "Acceptance"},
                        {"id": "labels", "name": "Labels"},
                    ]
                ).encode(),
            )
        if path.endswith("/comment") and method == "GET":
            start = int(query["startAt"][0])
            size = min(int(query["maxResults"][0]), self.page)
            chunk = self.comments[start : start + size]
            total = self.total if self.total is not None else len(self.comments)
            return (
                200,
                json.dumps(
                    {"startAt": start, "maxResults": size, "total": total, "comments": chunk}
                ).encode(),
            )
        if path.endswith("/comment") and method == "POST":
            return 201, json.dumps({"id": "10001"}).encode()
        if path.startswith("/rest/api/3/issue/"):
            fields = {
                "summary": "Do the thing",
                "project": {"key": "ACME", "id": "1"},
                "description": adf("line one\nline two " + LONG),
                "reporter": {"accountId": "acc-rep"},
            }
            fields.update(self.issue_fields)
            return 200, json.dumps({"key": "ACME-7", "fields": fields}).encode()
        return 404, b"{}"


def client(fake, **kw):
    return JiraClient(token=FAKE_TOKEN, site="acme.atlassian.net", transport=fake, **kw)


def test_get_issue_full_shape():
    fake = Fake(page=50)
    out = client(fake).get_issue_full("ACME-7")
    assert out["key"] == "ACME-7"
    assert out["project"] == "ACME"
    assert out["summary"] == "Do the thing"
    assert out["description"] == "line one\nline two " + LONG  # flattened, no 500 cut
    assert out["reporter"] == "acc-rep"
    assert out["fields"] == {}
    assert out["comments"][0] == {
        "id": "1000",
        "author": "acc-1",
        "created": "2026-11-01T10:00:00.000+0000",
        "body": "comment 0",
    }
    issue_url = fake.calls[0][1]
    assert issue_url.startswith("https://acme.atlassian.net/rest/api/3/issue/ACME-7?")
    requested = urllib.parse.parse_qs(urllib.parse.urlparse(issue_url).query)["fields"][0]
    assert {"summary", "project", "description", "reporter"} <= set(requested.split(","))


def test_comments_over_several_pages_in_order():
    fake = Fake(comments=[comment(i) for i in range(7)], page=2)
    out = client(fake).get_issue_full("ACME-7")
    assert [c["id"] for c in out["comments"]] == [str(1000 + i) for i in range(7)]
    comment_calls = [c for c in fake.calls if "/comment" in c[1]]
    assert len(comment_calls) == 4
    starts = [
        urllib.parse.parse_qs(urllib.parse.urlparse(c[1]).query)["startAt"][0]
        for c in comment_calls
    ]
    assert starts == ["0", "2", "4", "6"]


def test_comment_body_not_cut():
    fake = Fake(comments=[{"id": "1", "author": None, "created": "t", "body": adf(LONG)}])
    out = client(fake).get_issue_full("ACME-7")
    assert out["comments"] == [{"id": "1", "author": None, "created": "t", "body": LONG}]


def test_empty_page_stops_even_if_total_lies():
    fake = Fake(comments=[comment(0)], total=99, page=5)
    out = client(fake).get_issue_full("ACME-7")
    assert len(out["comments"]) == 1


def test_too_many_comment_pages_raises():
    fake = Fake(comments=[comment(i) for i in range(200)], page=1)
    c = client(fake)
    with pytest.raises(JiraError) as err:
        c.get_issue_full("ACME-7")
    assert err.value.code == "too_many_comments"


def test_no_comments_and_missing_description():
    fake = Fake(comments=[], issue_fields={"description": None, "reporter": None})
    out = client(fake).get_issue_full("ACME-7")
    assert out["comments"] == []
    assert out["description"] == ""
    assert out["reporter"] is None


def test_named_custom_fields_resolved_and_keyed_by_caller_name():
    fake = Fake(
        issue_fields={
            "customfield_10016": 5,
            "customfield_10020": adf("must work"),
            "labels": ["a", "b"],
            "customfield_10099": {"value": "Red"},
        }
    )
    out = client(fake).get_issue_full(
        "ACME-7", fields=["Story Points", "Acceptance", "labels", "customfield_10099", "Nope"]
    )
    assert out["fields"] == {
        "Story Points": 5,
        "Acceptance": "must work",
        "labels": ["a", "b"],
        "customfield_10099": {"value": "Red"},
    }
    issue_url = next(c[1] for c in fake.calls if "/issue/ACME-7?" in c[1])
    requested = urllib.parse.parse_qs(urllib.parse.urlparse(issue_url).query)["fields"][0]
    assert {"customfield_10016", "customfield_10020", "labels", "customfield_10099"} <= set(
        requested.split(",")
    )
    assert sum(1 for c in fake.calls if c[1].endswith("/rest/api/3/field")) == 1


def test_ids_only_skip_field_lookup():
    fake = Fake(issue_fields={"customfield_10016": 3})
    out = client(fake).get_issue_full("ACME-7", fields=["customfield_10016", "labels"])
    assert out["fields"] == {"customfield_10016": 3}
    assert not any(c[1].endswith("/rest/api/3/field") for c in fake.calls)


def test_bad_key_refused_before_any_call():
    fake = Fake()
    c = client(fake)
    with pytest.raises(JiraError) as err:
        c.get_issue_full("acme 7")
    assert err.value.code == "bad_key"
    assert fake.calls == []


def test_http_error_never_carries_token():
    fake = Fake(status=500)
    c = client(fake, email="bot@example.com")
    with pytest.raises(JiraError) as err:
        c.get_issue_full("ACME-7")
    assert FAKE_TOKEN not in str(err.value)
    assert FAKE_TOKEN not in repr(err.value)
    assert FAKE_TOKEN not in repr(c)


def test_add_comment_one_line_is_legacy_json():
    fake = Fake()
    out = client(fake).add_comment("ACME-7", "hello there")
    assert out == {"comment_id": "10001"}
    legacy = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "hello there"}]}
            ],
        }
    }
    assert fake.calls[-1][3] == json.dumps(legacy).encode()


def test_add_comment_multi_line_sends_to_adf():
    fake = Fake()
    text = "Summary\n- one\n- two"
    client(fake).add_comment("ACME-7", text)
    assert json.loads(fake.calls[-1][3]) == {"body": to_adf(text)}


def test_a_null_comment_does_not_make_pages_overlap():
    comments = [comment(0), None, comment(2), comment(3), comment(4)]
    fake = Fake(comments=comments, page=2)
    out = client(fake).get_issue_full("ACME-7")
    assert [c["id"] for c in out["comments"]] == ["1000", "1002", "1003", "1004"]
    starts = [
        urllib.parse.parse_qs(urllib.parse.urlparse(c[1]).query)["startAt"][0]
        for c in fake.calls
        if "/comment" in c[1]
    ]
    assert starts == ["0", "2", "4"]
