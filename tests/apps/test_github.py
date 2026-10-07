"""GitHub App client: JWT, cached installation token, allowlisted comments (fake transport)."""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from culture_rules.apps.github import GitHubApp, GitHubError  # noqa: E402

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
FAKE_BEARER = "ghs" + "_" + "FAKEINSTALLATIONTOKEN0123456789abcdefABCD"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class Fake:
    def __init__(self, expires=None, comment_status=201):
        self.calls = []
        self.expires = expires or (NOW + timedelta(hours=1))
        self.comment_status = comment_status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if url.endswith("/access_tokens"):
            payload = {
                "token": FAKE_BEARER,
                "expires_at": self.expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            return 201, json.dumps(payload).encode()
        if self.comment_status == 201:
            return 201, json.dumps({"id": 77, "html_url": "https://x/c/77"}).encode()
        return self.comment_status, b'{"message": "nope"}'


def make(pem, fake, clock=None, repos=("acme/widgets",)):
    now = {"t": NOW}
    app = GitHubApp(
        app_id="123",
        installation_id="456",
        private_key=pem,
        repos=repos,
        transport=fake,
        clock=clock or (lambda: now["t"]),
    )
    return app, now


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_comment_uses_installation_token(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    out = app.post_comment("acme/widgets", 5, "hello")
    assert out == {"comment_id": 77, "url": "https://x/c/77"}
    exch, comment = fake.calls
    assert exch[0] == "POST"
    assert exch[1] == "https://api.github.com/app/installations/456/access_tokens"
    assert exch[2]["Authorization"].startswith("Bearer ")
    assert comment[1] == "https://api.github.com/repos/acme/widgets/issues/5/comments"
    assert comment[2]["Authorization"] == f"Bearer {FAKE_BEARER}"
    assert comment[2]["Accept"] == "application/vnd.github+json"
    assert "X-GitHub-Api-Version" in comment[2]
    assert json.loads(comment[3]) == {"body": "hello"}


def test_jwt_is_rs256_with_claims(pem, key):
    fake = Fake()
    app, _ = make(pem, fake)
    app.post_comment("acme/widgets", 1, "x")
    jwt = fake.calls[0][2]["Authorization"].split(" ", 1)[1]
    h, p, s = jwt.split(".")
    assert "=" not in jwt
    assert json.loads(_b64d(h)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(_b64d(p))
    ts = int(NOW.timestamp())
    assert claims == {"iat": ts - 60, "exp": ts + 540, "iss": "123"}
    key.public_key().verify(_b64d(s), f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_token_cached_until_five_minutes_before_expiry(pem):
    fake = Fake(expires=NOW + timedelta(hours=1))
    app, now = make(pem, fake)
    app.post_comment("acme/widgets", 1, "a")
    now["t"] = NOW + timedelta(minutes=54)
    app.post_comment("acme/widgets", 1, "b")
    assert sum(c[1].endswith("/access_tokens") for c in fake.calls) == 1
    now["t"] = NOW + timedelta(minutes=55, seconds=1)
    app.post_comment("acme/widgets", 1, "c")
    assert sum(c[1].endswith("/access_tokens") for c in fake.calls) == 2


def test_not_allowlisted_repo_makes_no_network_call(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as exc:
        app.post_comment("evil/repo", 1, "x")
    assert exc.value.code == "repo_not_allowed"
    assert not exc.value.retryable
    assert fake.calls == []


@pytest.mark.parametrize("repo", ["acme/widgets/../x", "acme", "ACME/Widgets/extra", "../../x"])
def test_malformed_repo_rejected(pem, repo):
    fake = Fake()
    app, _ = make(pem, fake, repos=("acme/widgets",))
    with pytest.raises(GitHubError):
        app.post_comment(repo, 1, "x")
    assert fake.calls == []


@pytest.mark.parametrize("status,retryable", [(500, True), (503, True), (403, False), (404, False)])
def test_status_classification(pem, status, retryable):
    app, _ = make(pem, Fake(comment_status=status))
    with pytest.raises(GitHubError) as exc:
        app.post_comment("acme/widgets", 1, "x")
    assert exc.value.retryable is retryable


def test_network_error_is_retryable(pem):
    def boom(*a):
        raise OSError("down")

    app, _ = make(pem, boom)
    with pytest.raises(GitHubError) as exc:
        app.post_comment("acme/widgets", 1, "x")
    assert exc.value.retryable


def test_logs_never_contain_token_or_key(pem, caplog):
    caplog.set_level(logging.DEBUG)
    app, _ = make(pem, Fake(comment_status=500))
    with pytest.raises(GitHubError) as exc:
        app.post_comment("acme/widgets", 1, "x")
    with pytest.raises(GitHubError):
        app.post_comment("other/x", 1, "x")
    text = caplog.text + str(exc.value)
    assert FAKE_BEARER not in text
    assert "PRIVATE KEY" not in text
    assert pem.splitlines()[1] not in text


def test_api_base_configurable(pem):
    fake = Fake()
    app = GitHubApp(
        app_id="1",
        installation_id="2",
        private_key=pem,
        repos=["a/b"],
        transport=fake,
        api_base="http://localhost:9/",
        clock=lambda: NOW,
    )
    app.post_comment("a/b", 3, "x")
    assert fake.calls[0][1] == "http://localhost:9/app/installations/2/access_tokens"


class Revoking(Fake):
    """The first ``n`` comment calls answer 401, as after a server-side token revocation."""

    def __init__(self, n):
        super().__init__()
        self.left = n

    def __call__(self, method, url, headers, body, timeout):
        if not url.endswith("/access_tokens") and self.left:
            self.left -= 1
            self.calls.append((method, url, dict(headers), body))
            return 401, b'{"message": "Bad credentials"}'
        return super().__call__(method, url, headers, body, timeout)


def test_a_revoked_cached_token_is_re_exchanged_once(pem):
    fake = Revoking(1)
    app, _ = make(pem, fake)
    app.installation_token()  # cached, then revoked server-side
    assert app.post_comment("acme/widgets", 5, "hello")["comment_id"] == 77
    assert sum(c[1].endswith("/access_tokens") for c in fake.calls) == 2


def test_a_401_with_a_fresh_token_is_not_retried_again(pem):
    fake = Revoking(5)
    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as err:
        app.post_comment("acme/widgets", 5, "hello")
    assert err.value.code == "http_401"
    assert not err.value.retryable
    assert sum(not c[1].endswith("/access_tokens") for c in fake.calls) == 2


def test_push_token_is_fresh_single_repo_contents_write(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    assert app.push_token("acme/widgets") == FAKE_BEARER
    assert app.push_token("acme/widgets") == FAKE_BEARER
    mints = [c for c in fake.calls if c[1].endswith("/access_tokens")]
    assert len(mints) == 2  # never cached
    for _, _, headers, body in mints:
        assert json.loads(body) == {
            "repositories": ["widgets"],
            "permissions": {"contents": "write"},
        }
        assert headers["Authorization"] != f"Bearer {FAKE_BEARER}"  # signed with the App JWT
    app.installation_token()  # the general token is a separate, unscoped exchange
    assert fake.calls[-1][3] is None


def test_push_token_refuses_off_allowlist_without_network(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as err:
        app.push_token("evil/repo")
    assert err.value.code == "repo_not_allowed" and fake.calls == []


def test_push_token_scope_mismatch_is_refused(pem):
    def fake(method, url, headers, body, timeout):
        payload = {
            "token": FAKE_BEARER,
            "expires_at": "2030-01-01T00:00:00Z",
            "repositories": [{"full_name": "acme/widgets"}, {"full_name": "acme/other"}],
        }
        return 201, json.dumps(payload).encode()

    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as err:
        app.push_token("acme/widgets")
    assert err.value.code == "token_scope_mismatch"


def test_app_has_no_merge_call():
    assert not [n for n in dir(GitHubApp) if "merge" in n.lower()]


def test_deadline_bounds_http_timeouts_and_refuses_when_past(pem):
    seen = []

    def fake(method, url, headers, body, timeout):
        seen.append(timeout)
        return Fake()(method, url, headers, body, timeout)

    app, now = make(pem, fake)
    with app.deadline(NOW + timedelta(seconds=4)):
        app.post_comment("acme/widgets", 1, "x")
    assert seen and max(seen) <= 4
    seen.clear()
    with app.deadline(NOW - timedelta(seconds=1)):
        with pytest.raises(GitHubError) as err:
            app.post_comment("acme/widgets", 1, "x")
    assert err.value.code == "deadline_exceeded" and err.value.retryable and seen == []
    app.post_comment("acme/widgets", 1, "x")  # outside the block: the default bound again
    assert seen == [15]


def test_list_check_suites_paginates_and_trims(pem):
    def page(n):
        return [
            {"app": {"slug": f"app{n}-{i}"}, "status": "completed", "conclusion": "success"}
            for i in range(100 if n == 1 else 2)
        ]

    class SuitesFake(Fake):
        def __call__(self, method, url, headers, body, timeout):
            if "/check-suites" not in url:
                return super().__call__(method, url, headers, body, timeout)
            self.calls.append((method, url, dict(headers), body))
            n = int(url.rsplit("page=", 1)[1])
            return 200, json.dumps({"check_suites": page(n)}).encode()

    fake = SuitesFake()
    app, _ = make(pem, fake)
    out = app.list_check_suites("acme/widgets", "ab12" * 10)
    assert len(out) == 102 and out[0] == {
        "app_slug": "app1-0",
        "status": "completed",
        "conclusion": "success",
    }
    with pytest.raises(GitHubError) as err:
        app.list_check_suites("acme/widgets", "../x")
    assert err.value.code == "bad_input"
    with pytest.raises(GitHubError) as err:
        app.list_check_suites("other/repo", "ab12" * 10)
    assert err.value.code == "repo_not_allowed"


def test_pr_facts_shape():
    from culture_rules.apps.github import PR_FACT_FIELDS, complete_pr_facts, pr_facts

    pr = {
        "draft": True,
        "head": {"sha": "a" * 40, "ref": "feat", "repo": {"full_name": "fork/r"}},
        "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": "o/r"}},
        "user": {"login": "alice"},
    }
    facts = {
        "head_sha": "a" * 40,
        "head_branch": "feat",
        "head_repo": "fork/r",
        "base_repo": "o/r",
        "base_branch": "main",
        "base_sha": "b" * 40,
        "draft": True,
        "pr_author": "alice",
    }
    assert pr_facts(pr) == facts and tuple(pr_facts(pr)) == PR_FACT_FIELDS
    assert complete_pr_facts(pr) == facts
    assert pr_facts(None) == {} and pr_facts(["x"]) == {}


def test_pr_facts_omit_missing_and_malformed_fields_never_null():
    from culture_rules.apps.github import complete_pr_facts, pr_facts

    assert pr_facts({}) == {}  # no null repos to compare equal, no default draft
    assert complete_pr_facts({}) is None and complete_pr_facts(None) is None
    bad = {
        "draft": "false",
        "head": {"sha": "abc123", "ref": "", "repo": None},
        "base": {"sha": "g" * 40, "ref": 7, "repo": {"full_name": "not a repo"}},
        "user": {"login": ""},
    }
    assert pr_facts(bad) == {}
    assert complete_pr_facts(bad) is None
    # a deleted fork: only head_repo is gone, so head_repo == base_repo cannot hold
    fork = {
        "draft": False,
        "head": {"sha": "a" * 40, "ref": "feat", "repo": None},
        "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": "o/r"}},
        "user": {"login": "alice"},
    }
    facts = pr_facts(fork)
    assert "head_repo" not in facts and facts["base_repo"] == "o/r" and facts["draft"] is False
    assert complete_pr_facts(fork) is None
    assert pr_facts({"draft": 0}) == {}  # a real bool only
