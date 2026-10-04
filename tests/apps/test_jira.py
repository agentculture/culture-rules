"""Jira REST client: get_issue, add_comment (ADF), error mapping, no token leaks."""

from __future__ import annotations

import base64
import json
import logging

import pytest

from culture_rules.apps.jira import JiraClient, JiraError

FAKE_TOKEN = "atl" + "-" + "FAKEAPITOKEN0123456789"


class Fake:
    def __init__(self, status=200, payload=None, raises=None):
        self.calls = []
        self.status = status
        self.payload = {"key": "OPS-7"} if payload is None else payload
        self.raises = raises

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if self.raises:
            raise self.raises
        return self.status, json.dumps(self.payload).encode()


def make(fake, **kw):
    kw.setdefault("site", "acme.atlassian.net")
    kw.setdefault("email", "bot@example.test")
    return JiraClient(token=FAKE_TOKEN, transport=fake, **kw)


def test_get_issue_basic_auth_and_url():
    fake = Fake()
    issue = make(fake).get_issue("OPS-7")
    assert issue == {"key": "OPS-7"}
    method, url, headers, body = fake.calls[0]
    assert (method, body) == ("GET", None)
    assert url.startswith("https://acme.atlassian.net/rest/api/3/issue/OPS-7")
    expected = base64.b64encode(f"bot@example.test:{FAKE_TOKEN}".encode()).decode()
    assert headers["Authorization"] == f"Basic {expected}"


def test_api_base_gateway_with_bearer_when_no_email():
    fake = Fake()
    client = JiraClient(token=FAKE_TOKEN, api_base="http://localhost:9/gw/", transport=fake)
    client.get_issue("OPS-7")
    _, url, headers, _ = fake.calls[0]
    assert url.startswith("http://localhost:9/gw/rest/api/3/issue/OPS-7")
    assert headers["Authorization"] == f"Bearer {FAKE_TOKEN}"


def test_bad_key_refused_before_network():
    fake = Fake()
    client = make(fake)
    with pytest.raises(JiraError) as exc:
        client.get_issue("../../etc")
    assert exc.value.code == "bad_key"
    assert fake.calls == []


def test_add_comment_posts_adf():
    fake = Fake(201, {"id": "55"})
    out = make(fake).add_comment("OPS-7", "hello")
    method, url, headers, body = fake.calls[0]
    assert method == "POST"
    assert url.endswith("/rest/api/3/issue/OPS-7/comment")
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}],
        }
    }
    assert out == {"comment_id": "55"}


@pytest.mark.parametrize(
    "status,retryable", [(404, False), (401, False), (429, True), (500, True), (503, True)]
)
def test_http_errors(status, retryable):
    client = make(Fake(status, {"errorMessages": ["x"]}))
    with pytest.raises(JiraError) as exc:
        client.get_issue("OPS-7")
    assert exc.value.code == f"http_{status}"
    assert exc.value.retryable is retryable


def test_network_error_is_retryable_and_text_withheld():
    client = make(Fake(raises=OSError(f"boom {FAKE_TOKEN}")))
    with pytest.raises(JiraError) as exc:
        client.get_issue("OPS-7")
    assert exc.value.code == "network_error"
    assert exc.value.retryable
    assert FAKE_TOKEN not in str(exc.value)


def test_not_configured():
    client = JiraClient(token=FAKE_TOKEN, transport=Fake())
    with pytest.raises(JiraError) as exc:
        client.get_issue("OPS-7")
    assert exc.value.code == "not_configured"


def test_no_token_in_logs_or_repr(caplog):
    caplog.set_level(logging.DEBUG)
    client = make(Fake(500, {"errorMessages": [FAKE_TOKEN]}))
    with pytest.raises(JiraError):
        client.get_issue("OPS-7")
    assert FAKE_TOKEN not in caplog.text
    assert FAKE_TOKEN not in repr(client)
