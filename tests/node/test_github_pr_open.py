"""github.pr_open: commit files to a new branch and open a PR as the GitHub App (fake GitHub,
memory store): the allowlist, the actor's declared actions, the default branch, an existing
branch, the input limits and a retried step that opens no second PR."""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.parse
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from culture_rules.engine.actorport import InvocationContext  # noqa: E402
from culture_rules.extensions.github import pr_open as po  # noqa: E402
from culture_rules.extensions.github.client import GitHubApp, GitHubError  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

FAKE_BEARER = "ghs" + "_" + "FAKEINSTALLATIONTOKEN0123456789abcdefABCD"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
REPO = "acme/widgets"
BASE_SHA = "b" * 40
BASE_TREE = "c" * 40
KIND = "github.pr_open"


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class FakeGitHub:
    """A tiny stateful GitHub: one repo with refs, commits, trees, pulls and labels."""

    def __init__(self, default="main"):
        self.default = default
        self.refs = {default: BASE_SHA, "develop": BASE_SHA}
        self.commits = {BASE_SHA: {"tree": {"sha": BASE_TREE}}}
        self.pulls: list[dict] = []
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail: dict[tuple[str, str], int] = {}  # (method, path suffix) -> status, once
        self.lose_answer: set[tuple[str, str]] = set()  # do the work, then answer 502, once
        self._n = 0

    def _sha(self) -> str:
        self._n += 1
        return f"{self._n:040x}"

    def __call__(self, method, url, headers, body, timeout):
        payload = json.loads(body) if body else None
        path = url.split("api.github.com", 1)[1]
        self.calls.append((method, path, payload))
        for (m, suffix), status in list(self.fail.items()):
            if m == method and path.split("?")[0].endswith(suffix):
                del self.fail[(m, suffix)]
                return status, b"{}"
        if path.endswith("/access_tokens"):
            exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return 201, json.dumps({"token": FAKE_BEARER, "expires_at": exp}).encode()
        assert headers["Authorization"] == f"Bearer {FAKE_BEARER}"
        status, out = self._route(method, path, payload)
        for m, suffix in list(self.lose_answer):
            if m == method and path.split("?")[0].endswith(suffix):
                self.lose_answer.discard((m, suffix))
                return 502, b"{}"
        return status, json.dumps(out).encode()

    def _route(self, method, path, payload):
        prefix = f"/repos/{REPO}"
        assert path.startswith(prefix), path
        rest = path[len(prefix) :]
        if method == "GET" and rest == "":
            return 200, {"full_name": REPO, "default_branch": self.default}
        if method == "GET" and rest.startswith("/git/ref/heads/"):
            name = rest[len("/git/ref/heads/") :]
            if name not in self.refs:
                return 404, {"message": "Not Found"}
            return 200, {"ref": f"refs/heads/{name}", "object": {"sha": self.refs[name]}}
        if method == "GET" and rest.startswith("/git/commits/"):
            sha = rest[len("/git/commits/") :]
            if sha not in self.commits:
                return 404, {}
            return 200, {"sha": sha, **self.commits[sha]}
        if method == "POST" and rest == "/git/trees":
            sha = self._sha()
            return 201, {"sha": sha}
        if method == "POST" and rest == "/git/commits":
            sha = self._sha()
            self.commits[sha] = {"tree": {"sha": payload["tree"]}, "parents": payload["parents"]}
            return 201, {"sha": sha}
        if method == "POST" and rest == "/git/refs":
            name = payload["ref"][len("refs/heads/") :]
            if name in self.refs:
                return 422, {"message": "Reference already exists"}
            self.refs[name] = payload["sha"]
            return 201, {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
        if method == "GET" and rest.startswith("/pulls?"):
            query = urllib.parse.parse_qs(rest.split("?", 1)[1])
            assert query["state"] in (["open"], ["all"])
            owner, branch = query["head"][0].split(":", 1)
            items = [
                p
                for p in reversed(self.pulls)  # newest first, as GitHub lists them
                if (query["state"] == ["all"] or p["state"] == "open")
                and p["head"]["ref"] == branch
                and owner == "acme"
            ]
            return 200, items
        if method == "POST" and rest == "/pulls":
            head = payload["head"]
            if head not in self.refs:
                return 422, {}
            if any(p["head"]["ref"] == head and p["state"] == "open" for p in self.pulls):
                return 422, {"message": "A pull request already exists"}
            number = 100 + len(self.pulls)
            pull = {
                "number": number,
                "html_url": f"https://github.com/{REPO}/pull/{number}",
                "state": "open",
                "title": payload["title"],
                "body": payload.get("body"),
                "head": {"ref": head, "sha": self.refs[head]},
                "base": {"ref": payload["base"]},
                "labels": [],
            }
            self.pulls.append(pull)
            return 201, pull
        if method == "POST" and rest.startswith("/issues/") and rest.endswith("/labels"):
            number = int(rest.split("/")[2])
            pull = next(p for p in self.pulls if p["number"] == number)
            for label in payload["labels"]:
                if label not in pull["labels"]:
                    pull["labels"].append(label)
            return 200, [{"name": n} for n in pull["labels"]]
        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m != "GET" and "access_tokens" not in p]


def actor_doc(actions=(KIND,), **conn):
    connection = {
        "app_id": "1",
        "installation_id": "2",
        "private_key": "grant:GH_KEY",
        "repos": [REPO],
    }
    connection.update(conn)
    params = {"surface": "github", "connection": connection}
    if actions is not None:
        params["actions"] = list(actions)
    return {"id": "gh-app", "name": "gh", "kind": "app", "params": params}


def setup(pem, fake, doc=None):
    store = MemoryStore()
    store.put("actors", doc or actor_doc())
    resolved = []

    def secrets(ref):
        resolved.append(ref)
        return pem

    port = po.GitHubPrOpenPort(store, transport=fake, secrets=secrets)
    return port, store, resolved


def ctx():
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor="gh-app")


def params(**over):
    out = {
        "actor": "gh-app",
        "repo": REPO,
        "branch": "bot/update-notes",
        "base": "main",
        "files": [
            {"path": "docs/notes.md", "content": "# Notes\n"},
            {"path": "README.md", "content": "hello\n"},
        ],
        "title": "Update the notes",
        "body": "Generated.",
        "labels": ["automated", "docs"],
    }
    out.update(over)
    return out


def run(port, p=None, key="idem-1"):
    return port.invoke(p or params(), key, DEADLINE, context=ctx())


# --------------------------------------------------------------------------- happy path


def test_port_deduplicates_by_the_step_key():
    assert po.GitHubPrOpenPort(MemoryStore()).supports_idempotency_key is True


def test_commits_the_files_to_a_new_branch_and_opens_the_pr(pem):
    fake = FakeGitHub()
    port, _store, resolved = setup(pem, fake)
    res = run(port)
    assert res.outcome == "completed", res
    out = dict(res.output)
    pull = fake.pulls[0]
    assert out["number"] == pull["number"] == 100
    assert out["url"] == f"https://github.com/{REPO}/pull/100"
    assert out["head_sha"] == fake.refs["bot/update-notes"] == pull["head"]["sha"]
    assert out["branch"] == "bot/update-notes"
    assert out["base"] == "main"
    assert out["labels_error"] is None
    assert "skipped" not in out
    assert pull["title"] == "Update the notes"
    assert pull["body"] == "Generated."
    assert pull["base"]["ref"] == "main"
    assert pull["labels"] == ["automated", "docs"]
    assert resolved == ["grant:GH_KEY"]
    # the tree holds the files on top of the base's tree; the commit's parent is the base tip
    tree = next(p for m, path, p in fake.calls if path.endswith("/git/trees"))
    assert tree["base_tree"] == BASE_TREE
    assert tree["tree"] == [
        {"path": "docs/notes.md", "mode": "100644", "type": "blob", "content": "# Notes\n"},
        {"path": "README.md", "mode": "100644", "type": "blob", "content": "hello\n"},
    ]
    commit = next(p for m, path, p in fake.calls if path.endswith("/git/commits"))
    assert commit["parents"] == [BASE_SHA]
    assert commit["message"] == "Update the notes"
    ref = next(p for m, path, p in fake.calls if path.endswith("/git/refs"))
    assert ref == {"ref": "refs/heads/bot/update-notes", "sha": out["head_sha"]}
    # the default branch was never written
    assert fake.refs["main"] == BASE_SHA


def test_base_defaults_to_the_repos_default_branch(pem):
    fake = FakeGitHub(default="trunk")
    fake.refs["trunk"] = BASE_SHA
    port, _store, _ = setup(pem, fake)
    p = params()
    del p["base"]
    res = run(port, p)
    assert res.outcome == "completed", res
    assert res.output["base"] == "trunk"
    assert fake.pulls[0]["base"]["ref"] == "trunk"


def test_an_empty_base_means_the_repos_default_branch(pem):
    fake = FakeGitHub(default="trunk")
    fake.refs["trunk"] = BASE_SHA
    port, _store, _ = setup(pem, fake)
    res = run(port, params(base=""))
    assert res.outcome == "completed", res
    assert res.output["base"] == "trunk"
    assert fake.pulls[0]["base"]["ref"] == "trunk"


def test_a_pr_against_another_base_branch(pem):
    fake = FakeGitHub()
    port, _store, _ = setup(pem, fake)
    res = run(port, params(base="develop"))
    assert res.outcome == "completed", res
    assert fake.pulls[0]["base"]["ref"] == "develop"


def test_body_and_labels_are_optional(pem):
    fake = FakeGitHub()
    port, _store, _ = setup(pem, fake)
    p = params()
    del p["body"], p["labels"]
    res = run(port, p)
    assert res.outcome == "completed", res
    assert not any(path.endswith("/labels") for _m, path, _ in fake.calls)
    assert res.output["labels_error"] is None


def test_a_label_failure_does_not_fail_the_opened_pr(pem):
    fake = FakeGitHub()
    fake.fail[("POST", "/labels")] = 403
    port, _store, _ = setup(pem, fake)
    res = run(port)
    assert res.outcome == "completed"
    assert res.output["number"] == 100
    assert res.output["labels_error"] == "http_403"


# --------------------------------------------------------------------------- refusals


def test_repo_off_the_allowlist_is_refused_before_any_secret_or_network(pem, caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeGitHub()
    port, _store, resolved = setup(pem, fake)
    res = run(port, params(repo="evil/repo"))
    assert (res.outcome, res.error, res.retryable) == ("failed", "repo_not_allowed", False)
    assert fake.calls == []
    assert resolved == []
    assert FAKE_BEARER not in caplog.text


@pytest.mark.parametrize("actions", [None, [], ["github.comment"]])
def test_an_actor_that_does_not_list_the_kind_is_refused(pem, actions):
    fake = FakeGitHub()
    port, _store, resolved = setup(pem, fake, actor_doc(actions=actions))
    res = run(port)
    assert (res.outcome, res.error, res.retryable) == ("failed", "action_not_declared", False)
    assert fake.calls == []
    assert resolved == []


def test_a_missing_or_disabled_actor_is_refused(pem):
    fake = FakeGitHub()
    doc = actor_doc()
    doc["enabled"] = False
    port, _store, _ = setup(pem, fake, doc)
    res = run(port)
    assert (res.outcome, res.error) == ("failed", "actor_not_found")
    assert fake.calls == []


def test_the_default_branch_as_the_new_branch_is_refused(pem):
    fake = FakeGitHub()
    port, store, _ = setup(pem, fake)
    p = params(branch="main")
    del p["base"]
    res = run(port, p)
    assert (res.outcome, res.error, res.retryable) == ("failed", "branch_is_default", False)
    assert fake.writes() == []
    assert store.find(po.PR_OPEN_ONCE_COLLECTION) == []  # the claim is released


def test_the_default_branch_is_refused_whatever_the_base(pem):
    fake = FakeGitHub()
    port, _store, _ = setup(pem, fake)
    res = run(port, params(branch="MAIN", base="develop"))
    assert (res.outcome, res.error) == ("failed", "branch_is_default")
    assert fake.writes() == []


def test_a_branch_equal_to_its_base_is_refused_without_network(pem):
    fake = FakeGitHub()
    port, _store, resolved = setup(pem, fake)
    res = run(port, params(branch="develop", base="develop"))
    assert (res.outcome, res.error) == ("failed", "branch_is_base")
    assert fake.calls == []
    assert resolved == []


def test_an_existing_branch_opens_nothing_and_says_which_pr_is_open(pem):
    fake = FakeGitHub()
    port, store, _ = setup(pem, fake)
    first = run(port, key="run-1")
    assert first.outcome == "completed"
    fake.calls.clear()
    again = run(port, key="run-2")  # another run, the same branch
    assert again.outcome == "completed"
    assert again.output["skipped"] == "branch_exists"
    assert again.output["number"] == first.output["number"]
    assert again.output["url"] == first.output["url"]
    assert len(fake.pulls) == 1
    assert fake.writes() == []


def test_an_existing_branch_without_an_open_pr_is_skipped_with_no_number(pem):
    fake = FakeGitHub()
    fake.refs["bot/update-notes"] = BASE_SHA
    port, _store, _ = setup(pem, fake)
    res = run(port)
    assert res.outcome == "completed"
    assert res.output["skipped"] == "branch_exists"
    assert res.output["number"] is None
    assert fake.writes() == []
    assert fake.pulls == []


def test_a_branch_created_meanwhile_is_skipped_too(pem):
    fake = FakeGitHub()
    real = fake._route

    def racing(method, path, payload):
        if method == "POST" and path.endswith("/git/refs"):
            fake.refs.setdefault("bot/update-notes", "e" * 40)  # someone else won the race
        return real(method, path, payload)

    fake._route = racing
    port, _store, _ = setup(pem, fake)
    res = run(port)
    assert res.outcome == "completed"
    assert res.output["skipped"] == "branch_exists"
    assert fake.pulls == []


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"files": []}, "bad_input"),
        ({"files": "x"}, "bad_input"),
        ({"files": [{"path": "a"}]}, "bad_input"),
        ({"files": [{"path": "a", "content": 3}]}, "bad_input"),
        ({"files": [{"path": "/etc/passwd", "content": "x"}]}, "path_refused"),
        ({"files": [{"path": "../up", "content": "x"}]}, "path_refused"),
        ({"files": [{"path": "a/./b", "content": "x"}]}, "path_refused"),
        ({"files": [{"path": "a//b", "content": "x"}]}, "path_refused"),
        ({"files": [{"path": ".git/config", "content": "x"}]}, "path_refused"),
        ({"files": [{"path": ".github/workflows/ci.yml", "content": "x"}]}, "path_refused"),
        ({"files": [{"path": "a\x00b", "content": "x"}]}, "path_refused"),
        (
            {"files": [{"path": "a", "content": "x"}, {"path": "a", "content": "y"}]},
            "bad_input",
        ),
        ({"branch": "bad..name"}, "bad_input"),
        ({"branch": "refs/heads/x"}, "bad_input"),
        ({"branch": "x.lock"}, "bad_input"),
        ({"branch": ""}, "bad_input"),
        ({"base": "no good"}, "bad_input"),
        ({"title": ""}, "bad_input"),
        ({"title": "t" * 257}, "bad_input"),
        ({"body": 3}, "bad_input"),
        ({"labels": "docs"}, "bad_input"),
        ({"labels": [""]}, "bad_input"),
        ({"labels": ["x" * 51]}, "bad_input"),
    ],
)
def test_malformed_input_is_refused_before_any_secret_or_network(pem, over, code):
    fake = FakeGitHub()
    port, _store, resolved = setup(pem, fake)
    res = run(port, params(**over))
    assert (res.outcome, res.error, res.retryable) == ("failed", code, False)
    assert fake.calls == []
    assert resolved == []


def test_too_many_files_are_refused(pem):
    fake = FakeGitHub()
    port, _store, _ = setup(pem, fake)
    files = [{"path": f"f{i}.txt", "content": "x"} for i in range(po.MAX_FILES + 1)]
    res = run(port, params(files=files))
    assert (res.outcome, res.error) == ("failed", "too_many_files")
    files = files[: po.MAX_FILES]
    assert run(port, params(files=files)).outcome == "completed"


def test_files_over_the_byte_limit_are_refused(pem):
    fake = FakeGitHub()
    port, _store, _ = setup(pem, fake)
    half = po.MAX_TOTAL_BYTES // 2
    files = [{"path": "a", "content": "x" * half}, {"path": "b", "content": "é" * (half // 2 + 1)}]
    res = run(port, params(files=files))  # the bytes are counted UTF-8 encoded
    assert (res.outcome, res.error) == ("failed", "files_too_large")
    assert fake.calls == []


def test_a_secret_failure_is_quiet_and_final(pem):
    store = MemoryStore()
    store.put("actors", actor_doc())

    def secrets(ref):
        raise RuntimeError("boom")

    port = po.GitHubPrOpenPort(store, transport=FakeGitHub(), secrets=secrets)
    res = run(port)
    assert (res.outcome, res.error, res.retryable) == ("failed", "secret_unavailable", False)


# --------------------------------------------------------------------------- retries


def test_a_retried_step_opens_no_second_pr(pem):
    fake = FakeGitHub()
    port, _store, _ = setup(pem, fake)
    first = run(port)
    again = run(port)  # the same run and step: the same idempotency key
    assert again.outcome == "completed"
    assert again.output["skipped"] == "opened_before"
    assert again.output["number"] == first.output["number"]
    assert len(fake.pulls) == 1


def test_a_lost_pull_answer_is_reconciled_not_resent(pem):
    fake = FakeGitHub()
    fake.lose_answer.add(("POST", "/pulls"))  # GitHub opened it, the answer was lost
    port, store, _ = setup(pem, fake)
    first = run(port)
    assert (first.outcome, first.error, first.retryable) == ("failed", "http_502", True)
    assert len(fake.pulls) == 1
    again = run(port)
    assert again.outcome == "completed"
    assert again.output["skipped"] == "opened_unconfirmed"
    assert again.output["number"] == fake.pulls[0]["number"]
    assert len(fake.pulls) == 1
    assert fake.pulls[0]["labels"] == ["automated", "docs"]
    claim = store.find(po.PR_OPEN_ONCE_COLLECTION)[0]
    assert claim["state"] == "opened"


def _lose_the_pull_answer(pem):
    """A first attempt whose PR GitHub opened but whose answer was lost."""
    fake = FakeGitHub()
    fake.lose_answer.add(("POST", "/pulls"))
    port, store, _ = setup(pem, fake)
    first = run(port)
    assert (first.outcome, first.error, first.retryable) == ("failed", "http_502", True)
    assert len(fake.pulls) == 1
    return fake, port, store


def test_a_lost_pull_answer_then_a_closed_pr_is_not_opened_again(pem):
    fake, port, store = _lose_the_pull_answer(pem)
    pull = fake.pulls[0]
    pull["state"] = "closed"  # closed before the retry; the branch is still there
    before = list(store.find(po.PR_OPEN_ONCE_COLLECTION))
    fake.calls.clear()
    again = run(port)
    assert again.outcome == "completed", again
    assert again.output["skipped"] == "opened_unconfirmed"
    assert again.output["number"] == pull["number"]
    assert again.output["head_sha"] == pull["head"]["sha"]
    assert len(fake.pulls) == 1
    assert fake.writes() == []
    assert store.find(po.PR_OPEN_ONCE_COLLECTION)[0]["commit_sha"] == before[0]["commit_sha"]


def test_a_lost_pull_answer_then_a_merged_pr_and_deleted_branch_is_not_redone(pem):
    fake, port, _store = _lose_the_pull_answer(pem)
    pull = fake.pulls[0]
    pull["state"], pull["merged_at"] = "closed", "2030-01-01T00:00:00Z"
    del fake.refs["bot/update-notes"]  # merged, and the branch deleted
    fake.calls.clear()
    again = run(port)
    assert again.outcome == "completed", again
    assert again.output["skipped"] == "opened_unconfirmed"
    assert again.output["number"] == pull["number"]
    assert again.output["head_sha"] == pull["head"]["sha"]
    assert len(fake.pulls) == 1
    assert fake.writes() == []
    assert "bot/update-notes" not in fake.refs


def test_an_older_closed_pr_that_reused_the_branch_name_is_not_taken_for_this_one(pem):
    fake = FakeGitHub()
    fake.pulls.append(
        {
            "number": 7,
            "html_url": f"https://github.com/{REPO}/pull/7",
            "state": "closed",
            "head": {"ref": "bot/update-notes", "sha": "f" * 40},
            "base": {"ref": "main"},
            "labels": [],
        }
    )
    fake.lose_answer.add(("POST", "/git/refs"))  # the branch exists, the answer was lost
    port, _store, _ = setup(pem, fake)
    assert run(port).outcome == "failed"
    again = run(port)
    assert again.outcome == "completed", again
    assert "skipped" not in again.output
    assert again.output["number"] != 7
    assert len(fake.pulls) == 2


def test_a_failure_after_the_push_resumes_on_the_pushed_branch(pem):
    fake = FakeGitHub()
    fake.fail[("POST", "/pulls")] = 503  # nothing opened
    port, _store, _ = setup(pem, fake)
    first = run(port)
    assert (first.outcome, first.retryable) == ("failed", True)
    pushed = fake.refs["bot/update-notes"]
    trees = sum(path.endswith("/git/trees") for _m, path, _ in fake.calls)
    again = run(port)
    assert again.outcome == "completed", again
    assert "skipped" not in again.output
    assert again.output["head_sha"] == pushed
    assert len(fake.pulls) == 1
    # the branch was not pushed again
    assert sum(path.endswith("/git/trees") for _m, path, _ in fake.calls) == trees


def test_a_lost_ref_answer_resumes_on_the_recorded_commit(pem):
    fake = FakeGitHub()
    fake.lose_answer.add(("POST", "/git/refs"))  # the branch exists, the answer was lost
    port, _store, _ = setup(pem, fake)
    first = run(port)
    assert (first.outcome, first.retryable) == ("failed", True)
    again = run(port)
    assert again.outcome == "completed", again
    assert again.output["head_sha"] == fake.refs["bot/update-notes"]
    assert len(fake.pulls) == 1


def test_a_failure_before_the_push_starts_over(pem):
    fake = FakeGitHub()
    fake.fail[("POST", "/git/trees")] = 502
    port, store, _ = setup(pem, fake)
    first = run(port)
    assert (first.outcome, first.retryable) == ("failed", True)
    assert "bot/update-notes" not in fake.refs
    again = run(port)
    assert again.outcome == "completed", again
    assert "skipped" not in again.output
    assert len(fake.pulls) == 1
    assert len(store.find(po.PR_OPEN_ONCE_COLLECTION)) == 1


def test_a_pull_github_refused_fails_final(pem):
    fake = FakeGitHub()
    fake.fail[("POST", "/pulls")] = 422
    port, _store, _ = setup(pem, fake)
    res = run(port)
    assert (res.outcome, res.error, res.retryable) == ("failed", "http_422", False)


def test_the_claim_is_keyed_by_the_step_key(pem):
    fake = FakeGitHub()
    port, store, _ = setup(pem, fake)
    run(port, key="idem-x")
    claim = store.find(po.PR_OPEN_ONCE_COLLECTION)[0]
    assert claim["id"] == hashlib.sha256(b"github.pr_open#idem-x").hexdigest()
    assert claim["repo"] == REPO
    assert claim["branch"] == "bot/update-notes"
    assert "content" not in json.dumps(claim)  # file contents never stored


def test_a_token_failure_takes_no_claim(pem):
    fake = FakeGitHub()
    fake.fail[("POST", "/access_tokens")] = 500
    port, store, _ = setup(pem, fake)
    res = run(port)
    assert (res.outcome, res.retryable) == ("failed", True)
    assert store.find(po.PR_OPEN_ONCE_COLLECTION) == []


# --------------------------------------------------------------------------- overlapping attempts


def _overlap(fake, method, suffix, during, *, after_route=False):
    """Run ``during()`` (another attempt of the same step) inside the first ``method`` call
    whose path ends with ``suffix``: before GitHub handles it, or after."""
    real = fake._route
    state = {"done": False}

    def hooked(m, path, payload):
        hit = not state["done"] and m == method and path.split("?")[0].endswith(suffix)
        if hit:
            state["done"] = True
            if not after_route:
                during()
        out = real(m, path, payload)
        if hit and after_route:
            during()
        return out

    fake._route = hooked


def test_an_overlapping_attempt_takes_the_claim_and_the_first_stops_before_its_branch(pem):
    fake = FakeGitHub()
    port, store, _ = setup(pem, fake)
    second = {}
    _overlap(fake, "POST", "/git/trees", lambda: second.setdefault("res", run(port)))
    first = run(port)
    won = second["res"]
    assert won.outcome == "completed", won
    assert "skipped" not in won.output
    assert (first.outcome, first.error, first.retryable) == ("failed", "claim_lost", True)
    assert len(fake.pulls) == 1
    assert sum(path.endswith("/git/refs") for _m, path, _ in fake.calls) == 1
    claim = store.find(po.PR_OPEN_ONCE_COLLECTION)[0]
    assert claim["state"] == "opened"
    assert claim["commit_sha"] == won.output["head_sha"] == fake.pulls[0]["head"]["sha"]
    assert run(port).output["skipped"] == "opened_before"


def test_an_attempt_that_lost_the_claim_after_its_branch_opens_no_pull(pem):
    fake = FakeGitHub()
    port, store, _ = setup(pem, fake)
    real = store.update_if

    def update_if(collection, doc_id, expected, changes):
        res = real(collection, doc_id, expected, changes)
        if changes.get("state") == "pushed":  # another attempt takes the claim right after
            real(collection, doc_id, {"state": "pushed"}, {"owner": "another-attempt"})
        return res

    store.update_if = update_if
    first = run(port)
    assert (first.outcome, first.error, first.retryable) == ("failed", "claim_lost", True)
    assert fake.pulls == []
    assert not any(m == "POST" and path.endswith("/pulls") for m, path, _ in fake.calls)


def test_an_attempt_that_lost_the_claim_never_releases_the_winners(pem):
    fake = FakeGitHub()
    port, store, _ = setup(pem, fake)
    second = {}
    # the second attempt runs to the end between the first's commit and the first's branch
    _overlap(fake, "POST", "/git/refs", lambda: second.setdefault("res", run(port)))
    first = run(port)  # its branch creation then answers 422
    won = second["res"]
    assert won.outcome == "completed", won
    assert "skipped" not in won.output
    assert (first.outcome, first.error, first.retryable) == ("failed", "claim_lost", True)
    assert len(fake.pulls) == 1
    claims = store.find(po.PR_OPEN_ONCE_COLLECTION)
    assert len(claims) == 1  # the winner's claim survives
    assert claims[0]["state"] == "opened"
    assert claims[0]["commit_sha"] == won.output["head_sha"] == fake.refs["bot/update-notes"]
    assert run(port).output["skipped"] == "opened_before"


def test_the_attempt_whose_branch_landed_owns_the_claim(pem):
    fake = FakeGitHub()
    port, store, _ = setup(pem, fake)
    second = {}

    def during():  # takes the claim over, records its commit, then its branch fails
        fake.fail[("POST", "/git/refs")] = 503
        second["res"] = run(port)

    _overlap(fake, "POST", "/git/refs", during)
    first = run(port)  # its branch lands after the second recorded its own commit
    assert (second["res"].outcome, second["res"].retryable) == ("failed", True)
    assert first.outcome == "completed", first
    assert "skipped" not in first.output
    assert len(fake.pulls) == 1
    claim = store.find(po.PR_OPEN_ONCE_COLLECTION)[0]
    assert claim["state"] == "opened"
    assert claim["commit_sha"] == first.output["head_sha"] == fake.refs["bot/update-notes"]
    assert run(port).output["skipped"] == "opened_before"


def test_an_attempt_that_finds_the_claim_taken_meanwhile_writes_nothing(pem):
    fake = FakeGitHub()
    fake.fail[("POST", "/git/trees")] = 502  # the first attempt fails before its branch
    port, store, _ = setup(pem, fake)
    store.insert(
        po.PR_OPEN_ONCE_COLLECTION,
        {
            "id": hashlib.sha256(b"github.pr_open#idem-1").hexdigest(),
            "repo": REPO,
            "branch": "bot/update-notes",
            "base": None,
            "commit_sha": None,
            "state": "claimed",
            "owner": "someone-else",
        },
    )
    real_update_if = store.update_if

    def racing_update_if(collection, id, expected, changes, **kw):
        if collection == po.PR_OPEN_ONCE_COLLECTION and "owner" in changes:
            real_update_if(collection, id, {}, {"owner": "a-third-attempt"})
        return real_update_if(collection, id, expected, changes, **kw)

    store.update_if = racing_update_if
    res = run(port)
    assert (res.outcome, res.error, res.retryable) == ("failed", "claim_lost", True)
    assert fake.writes() == []
    assert store.find(po.PR_OPEN_ONCE_COLLECTION)[0]["owner"] == "a-third-attempt"


# --------------------------------------------------------------------------- client


def _app(pem, fake, repos=(REPO,)):
    return GitHubApp(app_id=1, installation_id=2, private_key=pem, repos=repos, transport=fake)


def test_client_writes_refuse_a_repo_off_the_allowlist(pem):
    fake = FakeGitHub()
    app = _app(pem, fake, repos=())
    for call in (
        lambda: app.get_repo(REPO),
        lambda: app.branch_head(REPO, "x"),
        lambda: app.create_tree(REPO, BASE_TREE, []),
        lambda: app.create_commit(REPO, "m", BASE_TREE, BASE_SHA),
        lambda: app.create_branch(REPO, "x", BASE_SHA),
        lambda: app.open_pull(REPO, "t", "x", "main", ""),
        lambda: app.open_pull_for(REPO, "x"),
        lambda: app.pull_for(REPO, "x", BASE_SHA),
        lambda: app.add_labels(REPO, 1, ["a"]),
    ):
        with pytest.raises(GitHubError) as err:
            call()
        assert err.value.code == "repo_not_allowed"
    assert fake.calls == []


def test_client_branch_head_is_none_for_a_missing_branch(pem):
    fake = FakeGitHub()
    app = _app(pem, fake)
    assert app.branch_head(REPO, "nope") is None
    assert app.branch_head(REPO, "main") == BASE_SHA


def test_client_never_creates_a_ref_outside_heads(pem):
    app = _app(pem, FakeGitHub())
    for bad in ("refs/tags/v1", "a..b", "", "x.lock", "a b"):
        with pytest.raises(GitHubError) as err:
            app.create_branch(REPO, bad, BASE_SHA)
        assert err.value.code == "bad_input"
