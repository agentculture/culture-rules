"""github.comment action port: allowlist, secret refs, result mapping (fake transport)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from culture_rules.engine.actorport import InvocationContext  # noqa: E402
from culture_rules.node.actions.github import GitHubCommentPort  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

FAKE_BEARER = "ghs" + "_" + "FAKEINSTALLATIONTOKEN0123456789abcdefABCD"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class Fake:
    def __init__(self, status=201):
        self.calls = []
        self.status = status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(url)
        if url.endswith("/access_tokens"):
            exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return 201, json.dumps({"token": FAKE_BEARER, "expires_at": exp}).encode()
        if self.status != 201:
            return self.status, b"{}"
        return 201, json.dumps({"id": 9, "html_url": "https://x/9"}).encode()


def actor_doc(**conn):
    connection = {
        "app_id": "1",
        "installation_id": "2",
        "private_key": "grant:GH_KEY",
        "webhook_secret": "grant:GH_HOOK",
        "repos": ["acme/widgets"],
    }
    connection.update(conn)
    return {
        "id": "gh-app",
        "name": "gh",
        "kind": "service",
        "params": {"surface": "github", "connection": connection},
        "schema_version": "1.0",
    }


def setup(pem, fake, doc=None):
    store = MemoryStore()
    store.put("actors", doc or actor_doc())
    resolved = []

    def secrets(ref):
        resolved.append(ref)
        return pem

    port = GitHubCommentPort(store, transport=fake, secrets=secrets)
    return port, resolved


def ctx():
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor="gh-app")


def params(repo="acme/widgets"):
    return {"actor": "gh-app", "repo": repo, "number": 3, "body": "hi"}


def test_port_flags():
    assert GitHubCommentPort(MemoryStore()).supports_idempotency_key is False


def test_allowlisted_comment_completes(pem):
    fake = Fake()
    port, resolved = setup(pem, fake)
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert dict(res.output) == {"comment_id": 9, "url": "https://x/9"}
    assert fake.calls[-1].endswith("/repos/acme/widgets/issues/3/comments")
    assert resolved == ["grant:GH_KEY"]


def test_not_allowlisted_fails_without_network_or_secret(pem, caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake()
    port, resolved = setup(pem, fake)
    res = port.invoke(params("evil/repo"), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed" and res.error == "repo_not_allowed" and not res.retryable
    assert fake.calls == [] and resolved == []
    assert FAKE_BEARER not in caplog.text


def test_missing_actor_fails(pem):
    port, _ = setup(pem, Fake())
    c = InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor="nope")
    res = port.invoke(params(), "k", DEADLINE, context=c)
    assert res.outcome == "failed" and not res.retryable


def test_5xx_retryable_4xx_not(pem):
    port, _ = setup(pem, Fake(status=502))
    assert port.invoke(params(), "k", DEADLINE, context=ctx()).retryable is True
    port, _ = setup(pem, Fake(status=422))
    assert port.invoke(params(), "k", DEADLINE, context=ctx()).retryable is False


def test_secret_failure_is_nonretryable_and_quiet(pem, caplog):
    store = MemoryStore()
    store.put("actors", actor_doc())

    def secrets(ref):
        raise RuntimeError("boom")

    port = GitHubCommentPort(store, transport=Fake(), secrets=secrets)
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed" and not res.retryable


def test_token_not_in_logs(pem, caplog):
    caplog.set_level(logging.DEBUG)
    port, _ = setup(pem, Fake())
    port.invoke(params(), "k", DEADLINE, context=ctx())
    assert FAKE_BEARER not in caplog.text and "PRIVATE KEY" not in caplog.text


def test_app_and_token_cached_across_invocations(pem):
    fake = Fake()
    port, resolved = setup(pem, fake)
    port.invoke(params(), "k1", DEADLINE, context=ctx())
    port.invoke(params(), "k2", DEADLINE, context=ctx())
    assert resolved == ["grant:GH_KEY"]
    assert sum(u.endswith("/access_tokens") for u in fake.calls) == 1
