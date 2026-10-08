"""d15: the ``github.threads`` and ``github.threads_addressed`` built-in code steps.

``github.threads`` lists a PR's unresolved review threads through the App (GraphQL
``reviewThreads``, bounded pages) and keeps only threads whose opening comment's author is in
``trusted_authors``; any lookup failure fails the step (fail closed: no thread list is ever
handed on). ``github.threads_addressed`` is pure: it keeps the agent's reported threads that
are in that list, so an unknown or untrusted id is never answered.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.apps.github import GitHubApp, GitHubError
from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.github_pr import AddressedThreadsPort, GitHubThreadsPort
from culture_rules.node.runner import BuiltinCodePort, default_ports
from culture_rules.store.memory import MemoryStore

REPO = "acme/widgets"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)


def thread(tid, cid, login, *, typename="User", resolved=False, body="fix it"):
    return {
        "id": tid,
        "isResolved": resolved,
        "path": "src/app.py",
        "line": 3,
        "comments": {
            "nodes": [
                {
                    "databaseId": cid,
                    "body": body,
                    "author": {"__typename": typename, "login": login},
                }
            ]
        },
    }


class FakeThreadsApi:
    """A GitHub transport answering the installation token and paged reviewThreads."""

    def __init__(self, pages, *, status=200):
        self.pages = pages
        self.status = status
        self.queries: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):
        payload = json.loads(body) if body else None
        if url.endswith("/access_tokens"):
            exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return 201, json.dumps({"token": "t", "expires_at": exp}).encode()
        assert url.endswith("/graphql"), url
        self.queries.append(payload)
        if self.status != 200:
            return self.status, b"{}"
        i = int(payload["variables"]["after"] or 0)
        nodes = self.pages[i]
        info = {"hasNextPage": i + 1 < len(self.pages), "endCursor": str(i + 1)}
        pull = {"reviewThreads": {"pageInfo": info, "nodes": nodes}}
        return 200, json.dumps({"data": {"repository": {"pullRequest": pull}}}).encode()


@pytest.fixture(scope="module")
def pem():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def app(pem, api) -> GitHubApp:
    return GitHubApp(app_id="1", installation_id="2", private_key=pem, repos={REPO}, transport=api)


# ------------------------------------------------------------------ the App client


def test_lists_unresolved_threads_with_the_opening_comment(pem):
    api = FakeThreadsApi(
        [
            [thread("PRRT_1", 101, "alice"), thread("PRRT_2", 102, "bob", resolved=True)],
            [thread("PRRT_3", 103, "qodo-code-review", typename="Bot", body="x" * 9000)],
        ]
    )
    got = app(pem, api).list_review_threads(REPO, 7)
    assert [t["thread_id"] for t in got] == ["PRRT_1", "PRRT_3"]
    assert got[0] == {
        "thread_id": "PRRT_1",
        "comment_id": 101,
        "path": "src/app.py",
        "line": 3,
        "author": "alice",
        "body": "fix it",
    }
    assert got[1]["author"] == "qodo-code-review[bot]"  # GraphQL drops the REST [bot] suffix
    assert len(got[1]["body"]) == 4000
    assert len(api.queries) == 2


def test_too_many_pages_raises(pem):
    api = FakeThreadsApi([[thread(f"T{i}", i, "a")] for i in range(30)])
    with pytest.raises(GitHubError) as err:
        app(pem, api).list_review_threads(REPO, 7, max_pages=3)
    assert err.value.code == "too_many_pages"
    assert len(api.queries) == 3


def test_a_repo_outside_the_allowlist_is_refused_before_any_call(pem):
    api = FakeThreadsApi([[]])
    with pytest.raises(GitHubError):
        app(pem, api).list_review_threads("other/repo", 7)
    assert api.queries == []


# ------------------------------------------------------------------ github.threads


class FakeApp:
    def __init__(self, threads=None, error=None):
        self.threads = threads or []
        self.error = error
        self.calls = []

    def deadline(self, deadline):
        from contextlib import nullcontext  # noqa: PLC0415

        return nullcontext()

    def list_review_threads(self, repo, number):
        self.calls.append((repo, number))
        if self.error is not None:
            raise self.error
        return [dict(t) for t in self.threads]


def actor_doc(repos=(REPO,)):
    return {
        "id": "github-app",
        "name": "gh",
        "kind": "app",
        "params": {
            "surface": "github",
            "connection": {
                "app_id": "1",
                "installation_id": "2",
                "private_key": "grant:K",
                "repos": list(repos),
            },
        },
    }


def threads_port(fake: FakeApp, **actor) -> GitHubThreadsPort:
    store = MemoryStore()
    store.put("actors", actor_doc(**actor))
    port = GitHubThreadsPort(store)
    port._app = lambda actor_id, conn, allowed: fake  # the App seam
    return port


def ctx(builtin="github.threads", **config):
    return InvocationContext(
        run_id="run-1",
        step_id="threads",
        kind="code",
        host="spark",
        actor="github-app",
        config={"builtin": builtin, **config},
    )


LISTED = [
    {
        "thread_id": "PRRT_1",
        "comment_id": 101,
        "path": "a",
        "line": 1,
        "author": "Alice",
        "body": "x",
    },
    {
        "thread_id": "PRRT_2",
        "comment_id": 102,
        "path": "b",
        "line": 2,
        "author": "mallory",
        "body": "y",
    },
]


def test_only_trusted_authors_threads_are_kept():
    port = threads_port(FakeApp(LISTED))
    res = port.invoke(
        {"repo": REPO, "number": 7, "trusted_authors": ["alice"]}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed", res.error
    assert [t["thread_id"] for t in res.output["threads"]] == ["PRRT_1"]  # login case-folded
    assert res.output["untrusted"] == 1


@pytest.mark.parametrize(
    "error", [GitHubError("http_502", retryable=True), GitHubError("too_many_pages")]
)
def test_a_lookup_error_fails_the_step_closed(error):
    res = threads_port(FakeApp(error=error)).invoke(
        {"repo": REPO, "number": 7, "trusted_authors": ["alice"]}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed"
    assert res.error == error.code
    assert res.retryable is error.retryable
    assert not res.output


@pytest.mark.parametrize(
    "inp, code",
    [
        ({"repo": "x/y", "number": 7, "trusted_authors": []}, "repo_not_allowed"),
        ({"repo": REPO, "number": "seven", "trusted_authors": []}, "bad_input"),
        ({"repo": REPO, "number": 7, "trusted_authors": "alice"}, "bad_input"),
        ({"repo": REPO, "number": 7}, "bad_input"),
    ],
)
def test_bad_input_and_allowlist_fail_without_a_lookup(inp, code):
    fake = FakeApp(LISTED)
    res = threads_port(fake).invoke(inp, "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == code
    assert fake.calls == []


def test_the_actor_comes_from_the_step_config_then_the_placement():
    fake = FakeApp(LISTED)
    port = threads_port(fake)
    no_actor = InvocationContext(
        run_id="r",
        step_id="s",
        kind="code",
        host="h",
        actor=None,
        config={"builtin": "github.threads", "actor": "github-app"},
    )
    inp = {"repo": REPO, "number": 7, "trusted_authors": ["alice"]}
    assert port.invoke(inp, "k", DEADLINE, context=no_actor).outcome == "completed"
    unknown = InvocationContext(
        run_id="r", step_id="s", kind="code", host="h", actor="nobody", config={}
    )
    assert port.invoke(inp, "k", DEADLINE, context=unknown).error == "actor_not_found"


# ------------------------------------------------------------------ github.threads_addressed


def test_only_listed_threads_the_agent_addressed_are_answered():
    listed = [
        {"thread_id": "PRRT_1", "comment_id": 101},
        {"thread_id": "PRRT_3", "comment_id": 103},
    ]
    addressed = [
        {"thread_id": "PRRT_1", "commit": "abc", "reply": "done"},
        {"thread_id": "PRRT_1", "commit": "abc", "reply": "again"},  # duplicate: once
        {"thread_id": "PRRT_9", "commit": None, "reply": "made up"},  # not listed: dropped
        {"thread_id": "102", "reply": "an untrusted thread's REST id"},  # dropped
        "junk",
    ]
    res = AddressedThreadsPort().invoke(
        {"threads": listed, "addressed": addressed}, "k", DEADLINE, context=ctx("x")
    )
    assert res.outcome == "completed"
    assert res.output["replies"] == [
        {"thread_id": "PRRT_1", "comment_id": 101, "commit": "abc", "reply": "done"}
    ]
    assert res.output["dropped"] == 4


def test_addressed_with_bad_input_fails():
    res = AddressedThreadsPort().invoke(
        {"threads": "x", "addressed": []}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed"
    assert res.error == "bad_input"


def test_default_ports_register_both_builtins():
    ports = default_ports(MemoryStore(), "spark")
    code = ports["code"]
    assert isinstance(code, BuiltinCodePort)
    assert {"gate", "github.threads", "github.threads_addressed"} <= set(code._builtins)
