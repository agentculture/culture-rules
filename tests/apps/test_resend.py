"""Resend client: POST /emails, Bearer auth, Idempotency-Key, error mapping, no key leaks."""

from __future__ import annotations

import json
import logging

import pytest

from culture_rules.apps.resend import RESEND_EMAILS_URL, ResendClient, ResendError

FAKE_KEY = "re" + "_" + "FAKEKEY0123456789abcdef"
SENDER = "no-reply@culture.dev"
RCPT = "reader@example.test"
IDEM = "run-1:step-1"


class Fake:
    def __init__(self, status=200, payload=None, raises=None):
        self.calls = []
        self.status = status
        self.payload = {"id": "msg-123"} if payload is None else payload
        self.raises = raises

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if self.raises:
            raise self.raises
        return self.status, json.dumps(self.payload).encode()


def send(client, **kw):
    kw.setdefault("sender", SENDER)
    kw.setdefault("to", [RCPT])
    kw.setdefault("subject", "Daily")
    kw.setdefault("html", "<p>hi</p>")
    kw.setdefault("idempotency_key", IDEM)
    return client.send_email(**kw)


def test_url_constant_is_resend_emails():
    assert RESEND_EMAILS_URL == "https://api.resend.com/emails"


def test_send_posts_json_with_bearer_and_idempotency_key():
    fake = Fake()
    out = send(ResendClient(token=FAKE_KEY, transport=fake))
    assert out == {"message_id": "msg-123"}
    method, url, headers, _ = fake.calls[0]
    assert (method, url) == ("POST", RESEND_EMAILS_URL)
    assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert headers["Idempotency-Key"] == IDEM
    assert headers["Content-Type"] == "application/json"


def test_body_carries_from_to_subject_and_only_given_parts():
    fake = Fake()
    send(ResendClient(token=FAKE_KEY, transport=fake), html=None, text="plain")
    body = json.loads(fake.calls[0][3])
    assert body == {"from": SENDER, "to": [RCPT], "subject": "Daily", "text": "plain"}


def test_no_idempotency_header_when_key_empty():
    fake = Fake()
    send(ResendClient(token=FAKE_KEY, transport=fake), idempotency_key="")
    assert "Idempotency-Key" not in fake.calls[0][2]


def test_repr_hides_key():
    assert FAKE_KEY not in repr(ResendClient(token=FAKE_KEY, transport=Fake()))


def test_missing_token_not_configured_before_network():
    fake = Fake()
    with pytest.raises(ResendError) as exc:
        send(ResendClient(token="", transport=fake))
    assert exc.value.code == "not_configured"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("status", "retryable"), [(400, False), (403, False), (422, False), (500, True), (503, True)]
)
def test_status_mapping(status, retryable):
    client = ResendClient(token=FAKE_KEY, transport=Fake(status=status, payload={"x": FAKE_KEY}))
    with pytest.raises(ResendError) as exc:
        send(client)
    assert (exc.value.code, exc.value.retryable) == (f"http_{status}", retryable)
    assert FAKE_KEY not in str(exc.value)


def test_network_error_retryable_and_text_withheld():
    client = ResendClient(token=FAKE_KEY, transport=Fake(raises=OSError("boom " + FAKE_KEY)))
    with pytest.raises(ResendError) as exc:
        send(client)
    assert (exc.value.code, exc.value.retryable) == ("network_error", True)
    assert FAKE_KEY not in str(exc.value)


def test_success_without_id_is_bad_response():
    client = ResendClient(token=FAKE_KEY, transport=Fake(payload={}))
    with pytest.raises(ResendError) as exc:
        send(client)
    assert exc.value.code == "bad_response"


def test_key_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    client = ResendClient(token=FAKE_KEY, transport=Fake(status=500))
    with pytest.raises(ResendError):
        send(client)
    assert FAKE_KEY not in caplog.text
