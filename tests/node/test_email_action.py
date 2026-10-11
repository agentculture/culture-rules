"""email.send action port: allowlist before secret, sender, Idempotency-Key, result mapping."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from culture_rules.apps.resend import RESEND_EMAILS_URL
from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.email import EmailSendPort
from culture_rules.store.memory import MemoryStore

FAKE_KEY = "re" + "_" + "FAKEKEY0123456789abcdef"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
ACTOR = "email-app"
RCPT = "reader@example.test"
OUTSIDER = "stranger@example.test"
KEY_REF = "grant:RESEND_API_KEY"
IDEM = "run-1:step-1"
IDEM_HEADER = "Idempotency-Key"
BAD_INPUT = "bad_input"
NOT_FOUND = "actor_not_found"
COMPLETED = "completed"


class Fake:
    def __init__(self, status=200):
        self.calls = []
        self.status = status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        return self.status, json.dumps({"id": "msg-123"}).encode()


def actor_doc(surface="email", enabled=True, **conn):
    connection = {"api_key": KEY_REF, "domain": "culture.dev", "recipients": [RCPT]}
    connection.update(conn)
    return {
        "id": ACTOR,
        "name": "email",
        "kind": "service",
        "enabled": enabled,
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
            raise RuntimeError("grant exploded " + FAKE_KEY)
        return FAKE_KEY

    return EmailSendPort(store, transport=fake, secrets=secrets), resolved


def ctx(actor=ACTOR):
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor=actor)


def params(**over):
    out = {"actor": ACTOR, "to": [RCPT], "subject": "Daily", "html": "<p>hi</p>"}
    out.update(over)
    return out


def invoke(port, inp=None, key=IDEM, actor=ACTOR):
    return port.invoke(params() if inp is None else inp, key, DEADLINE, context=ctx(actor))


def sent_body(fake):
    return json.loads(fake.calls[-1][3])


def test_port_declares_idempotency_support():
    assert EmailSendPort(MemoryStore()).supports_idempotency_key is True


def test_allowlisted_send_outputs_only_message_id():
    fake = Fake()
    port, resolved = setup(fake)
    res = invoke(port)
    assert res.outcome == COMPLETED
    assert dict(res.output) == {"message_id": "msg-123"}
    assert fake.calls[-1][1] == RESEND_EMAILS_URL
    assert resolved == [KEY_REF]


def test_engine_idempotency_key_sent_as_header():
    fake = Fake()
    port, _ = setup(fake)
    invoke(port)
    assert fake.calls[-1][2][IDEM_HEADER] == IDEM


DURABLE = "newspaper:2026-10-11:email"


def test_an_explicit_idempotency_key_param_overrides_the_engine_key():
    """Codex r1 #1: a durable per-date key, the same for every run that sends the date."""
    fake = Fake()
    port, _ = setup(fake)
    invoke(port, params(idempotency_key=DURABLE))
    assert fake.calls[-1][2][IDEM_HEADER] == DURABLE


def test_two_runs_with_the_same_explicit_key_send_the_same_key():
    fake = Fake()
    port, _ = setup(fake)
    invoke(port, params(idempotency_key=DURABLE), key="run-1:email")
    invoke(port, params(idempotency_key=DURABLE), key="run-2:email")
    assert {call[2][IDEM_HEADER] for call in fake.calls} == {DURABLE}


def test_a_bad_explicit_idempotency_key_is_bad_input_without_a_call():
    fake = Fake()
    port, _ = setup(fake)
    result = invoke(port, params(idempotency_key=7))
    assert (result.error, fake.calls) == (BAD_INPUT, [])


def test_an_overlong_explicit_idempotency_key_is_bad_input():
    port, _ = setup(Fake())
    assert invoke(port, params(idempotency_key="k" * 257)).error == BAD_INPUT


def test_from_is_local_part_at_domain_with_default():
    fake = Fake()
    port, _ = setup(fake)
    invoke(port)
    assert sent_body(fake)["from"] == "no-reply@culture.dev"


def test_from_uses_actor_local_part_and_domain():
    fake = Fake()
    port, _ = setup(fake, actor_doc(domain="jetson-ai-lab.org", local_part="newsletter-no-reply"))
    invoke(port)
    assert sent_body(fake)["from"] == "newsletter-no-reply@jetson-ai-lab.org"


def test_text_only_body():
    fake = Fake()
    port, _ = setup(fake)
    invoke(port, {"actor": ACTOR, "to": [RCPT], "subject": "S", "text": "plain"})
    assert sent_body(fake) == {
        "from": "no-reply@culture.dev",
        "to": [RCPT],
        "subject": "S",
        "text": "plain",
    }


def test_recipient_match_ignores_case():
    port, _ = setup(Fake())
    assert invoke(port, params(to=[RCPT.upper()])).outcome == COMPLETED


def test_outsider_refused_before_secret_naming_no_secret():
    fake = Fake()
    port, resolved = setup(fake)
    res = invoke(port, params(to=[RCPT, OUTSIDER]))
    assert (res.error, res.retryable) == ("recipient_not_allowed", False)
    assert resolved == []
    assert fake.calls == []
    assert "RESEND" not in repr(res)
    assert "grant" not in repr(res)


def test_empty_allowlist_refuses_everyone():
    fake = Fake()
    port, resolved = setup(fake, actor_doc(recipients=[]))
    res = invoke(port)
    assert res.error == "recipient_not_allowed"
    assert resolved == []


def test_missing_or_empty_to_is_bad_input():
    port, resolved = setup(Fake())
    assert invoke(port, params(to=[])).error == BAD_INPUT
    assert invoke(port, params(to=RCPT)).error == BAD_INPUT
    assert resolved == []


def test_neither_html_nor_text_is_bad_input():
    fake = Fake()
    port, resolved = setup(fake)
    res = invoke(port, {"actor": ACTOR, "to": [RCPT], "subject": "S"})
    assert (res.error, res.retryable) == (BAD_INPUT, False)
    assert resolved == []


def test_missing_subject_is_bad_input():
    port, _ = setup(Fake())
    res = invoke(port, {"actor": ACTOR, "to": [RCPT], "html": "x"})
    assert res.error == BAD_INPUT


def test_actor_falls_back_to_input():
    port, _ = setup(Fake())
    assert invoke(port, actor=None).outcome == COMPLETED


def test_unknown_actor_not_found():
    fake = Fake()
    port, resolved = setup(fake)
    assert invoke(port, actor="nope").error == NOT_FOUND
    assert resolved == []


def test_wrong_surface_not_found():
    fake = Fake()
    port, resolved = setup(fake, actor_doc(surface="jira"))
    assert invoke(port).error == NOT_FOUND
    assert fake.calls == []


def test_disabled_actor_not_found():
    port, resolved = setup(Fake(), actor_doc(enabled=False))
    assert invoke(port).error == NOT_FOUND
    assert resolved == []


def test_secret_failure_names_no_secret():
    fake = Fake()
    port, _ = setup(fake, fail=True)
    res = invoke(port)
    assert (res.error, res.retryable) == ("secret_unavailable", False)
    assert FAKE_KEY not in repr(res)
    assert fake.calls == []


def test_4xx_not_retryable():
    port, _ = setup(Fake(status=422))
    res = invoke(port)
    assert (res.error, res.retryable) == ("http_422", False)


def test_5xx_retryable():
    port, _ = setup(Fake(status=503))
    res = invoke(port)
    assert (res.error, res.retryable) == ("http_503", True)


def test_key_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    port, _ = setup(Fake(status=500))
    res = invoke(port)
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in repr(res)
