"""github.push / github.review_reply ports: fake GitHub API, real local git, no network.

The "remote" is a local bare repo reached through ``git_base=file://...``; the GitHub REST /
GraphQL API is a fake transport. Nothing here talks to the internet.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from culture_rules.engine.actorport import InvocationContext  # noqa: E402
from culture_rules.model.action_kinds import ACTION_KINDS  # noqa: E402
from culture_rules.node.actions.github_pr import (  # noqa: E402
    GIT_TIMED_OUT,
    GIT_UNAVAILABLE,
    GitHubPushPort,
    GitHubReviewReplyPort,
    subprocess_git,
)
from culture_rules.store.memory import MemoryStore  # noqa: E402

if shutil.which("git") is None:  # pragma: no cover
    pytest.skip("git is not installed", allow_module_level=True)

INSTALL_TOKEN = "ghs" + "_" + "FAKEINSTALLATIONTOKEN0123456789abcdefABCD"
PUSH_TOKEN = "ghs" + "_" + "FAKEPUSHTOKEN0123456789abcdefABCDEFGHIJ"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
REPO = "acme/widgets"
PR_BASE_SHA = "e" * 40  # the PR's base as the fake App reports it; reviews record the same


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def git(*args, cwd=None):
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "HOME": str(cwd or "/tmp"),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    out = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def commit(wt, name):
    (wt / name).write_text(name)
    git("add", name, cwd=wt)
    git("commit", "-q", "-m", name, cwd=wt)
    return git("rev-parse", "HEAD", cwd=wt)


class World:
    """A remote bare repo with branch ``fix`` at A, and an agent worktree with B on top."""

    def __init__(self, tmp_path):
        self.base = tmp_path / "remotes"
        self.remote = self.base / "acme" / "widgets.git"
        self.remote.parent.mkdir(parents=True)
        git("init", "-q", "--bare", "-b", "main", str(self.remote))
        seed = tmp_path / "seed"
        seed.mkdir()
        git("init", "-q", "-b", "fix", cwd=seed)
        self.a0 = commit(seed, "a0")
        self.a = commit(seed, "a")
        git("push", "-q", str(self.remote), "fix:refs/heads/fix", cwd=seed)
        self.agent = tmp_path / "agent"
        git("clone", "-q", "-b", "fix", str(self.remote), str(self.agent))
        self.b = commit(self.agent, "b")
        self.seed = seed

    def remote_head(self, branch="fix"):
        return git("rev-parse", f"refs/heads/{branch}", cwd=self.remote)

    def move_remote(self):
        """Someone else pushes C onto the remote fix branch."""
        c = commit(self.seed, "c")
        git("push", "-q", str(self.remote), "fix:refs/heads/fix", cwd=self.seed)
        return c


def _page(items, after, size):
    start = int(after or 0)
    nxt = start + size
    return items[start:nxt], {"hasNextPage": nxt < len(items), "endCursor": str(nxt)}


class FakeThreads:
    """GraphQL review threads, paged ``size`` at a time: ``(id, repo, number, [comment ids])``."""

    def __init__(self, threads, size=100):
        self.threads = threads
        self.size = size
        self.queries = []

    def _comments(self, thread, after):
        chunk, info = _page(thread[3], after, self.size)
        return {"pageInfo": info, "nodes": [{"databaseId": c} for c in chunk]}

    def answer(self, query, variables):
        self.queries.append((query, dict(variables)))
        if "reviewThreads" in query:
            repo, number = f"{variables['owner']}/{variables['name']}", variables["number"]
            mine = [t for t in self.threads if t[1] == repo and t[2] == number]
            chunk, info = _page(mine, variables.get("after"), self.size)
            nodes = [{"id": t[0], "comments": self._comments(t, None)} for t in chunk]
            return {
                "repository": {"pullRequest": {"reviewThreads": {"pageInfo": info, "nodes": nodes}}}
            }
        thread = next((t for t in self.threads if t[0] == variables.get("id")), None)
        if thread is None:
            return {"node": None}
        node = {"id": thread[0], "comments": self._comments(thread, variables.get("after"))}
        if "nameWithOwner" in query:
            node["pullRequest"] = {"number": thread[2], "repository": {"nameWithOwner": thread[1]}}
        return {"node": node}


class FakeGitHub:
    def __init__(self, world, **pull):
        self.world = world
        self.calls = []  # (method, path, headers, payload)
        self.pull = pull
        self.on_push_token = None
        self.gql = FakeThreads([("PRRT_1", REPO, 3, [77])])

    def paths(self):
        return [c[1] for c in self.calls]

    def __call__(self, method, url, headers, body, timeout):
        path = url.removeprefix("https://api.github.com")
        payload = json.loads(body) if body else None
        self.calls.append((method, path, headers, payload))
        exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if path.endswith("/access_tokens"):
            if payload:  # the per-push, repo-scoped token
                if self.on_push_token:
                    self.on_push_token()
                return 201, json.dumps({"token": PUSH_TOKEN, "expires_at": exp}).encode()
            return 201, json.dumps({"token": INSTALL_TOKEN, "expires_at": exp}).encode()
        if path == "/repos/acme/widgets/pulls/3":
            head_sha = self.world.remote_head() if self.world else "0" * 40
            doc = {
                "state": self.pull.get("state", "open"),
                "head": {
                    "ref": self.pull.get("head_ref", "fix"),
                    "sha": self.pull.get("head_sha", head_sha),
                    "repo": {"full_name": self.pull.get("head_repo", REPO)},
                },
                "base": {
                    "ref": "main",
                    "sha": self.pull.get("base_sha", PR_BASE_SHA),
                    "repo": {"full_name": REPO},
                },
            }
            return 200, json.dumps(doc).encode()
        if path.endswith("/replies"):
            return 201, json.dumps({"id": 501, "html_url": "https://x/501"}).encode()
        if path == "/graphql":
            if "resolveReviewThread" in payload["query"]:
                thread = {"id": payload["variables"]["threadId"], "isResolved": True}
                return (
                    200,
                    json.dumps({"data": {"resolveReviewThread": {"thread": thread}}}).encode(),
                )
            data = self.gql.answer(payload["query"], payload["variables"])
            return 200, json.dumps({"data": data}).encode()
        return 404, b"{}"


class RecordingGit:
    def __init__(self):
        self.calls = []  # (argv, env)
        self.timeouts = []

    def __call__(self, argv, env, timeout):
        self.calls.append((list(argv), dict(env)))
        self.timeouts.append(timeout)
        return subprocess_git(argv, env, timeout)

    def verbs(self):
        out = []
        for argv, _ in self.calls:
            rest = argv[1:]
            while rest and rest[0] in ("-c", "-C"):
                rest = rest[2:]
            out.append(rest[0] if rest else "")
        return out


#: The App actor shapes these push-mechanics tests use; each is trusted for them (d20 round
#: 3). Production trusts only the digests pinned in culture_rules/actors/trusted.py.
TEST_APP_AUTHORS = (None, "t", "T@Example.invalid", "rules-culture-dev[bot]")


@pytest.fixture(autouse=True)
def trusted_test_app(monkeypatch):
    from culture_rules.actors import trusted

    shapes = [actor_doc(**({"commit_author": a} if a else {})) for a in TEST_APP_AUTHORS]
    monkeypatch.setattr(
        trusted,
        "TRUSTED_ACTOR_DIGESTS",
        {**trusted.TRUSTED_ACTOR_DIGESTS, "gh-app": frozenset(map(trusted.actor_digest, shapes))},
    )


def actor_doc(**params):
    doc = {
        "id": "gh-app",
        "name": "gh",
        "kind": "app",
        "params": {
            "surface": "github",
            "connection": {
                "app_id": "1",
                "installation_id": "2",
                "private_key": "grant:GH_KEY",
                "webhook_secret": "grant:GH_HOOK",
                "repos": [REPO],
            },
        },
        "schema_version": "1.0",
    }
    doc["params"].update(params)
    return doc


def trusted_workflow() -> dict:
    """The shipped pr-fixer workflow: the only kind of run github.push serves (d20)."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    path = root / "tests/rules/fixtures/pr-fixer-single/workflows/pr-fixer.json"
    return json.loads(path.read_text())


def make_store(rule_enabled=True, **actor_params):
    store = MemoryStore()
    store.put("actors", actor_doc(**actor_params))
    store.put("rules", {"id": "fixer", "name": "fixer", "enabled": rule_enabled})
    store.put(
        "runs",
        {
            "id": "run-1",
            "kind": "run",
            "rule_id": "fixer",
            "workflow_id": "pr-fixer",
            "workflow": {"definition": trusted_workflow()},  # the shipped, trusted one
        },
    )
    return store


def ctx(run_id="run-1"):
    return InvocationContext(run_id=run_id, step_id="push", kind="action", host="h", actor="gh-app")


def approve_review(store, sha, run_id="run-1", *, start, repo=REPO, number=3):
    """The run's reviewer approved ``start..sha`` on ``repo#number`` (d20): every push
    needs this record as the run's current review."""
    from culture_rules.actors.review import record_review

    record_review(
        store,
        run_id,
        iteration=0,
        attempt=1,
        fields={
            "commit_sha": sha,
            "reviewed_commit": sha,
            "verdict": "approve",
            "reviewer_actor": "codex-reviewer",
            "reviewer_backend": "codex",
            "implementer_actor": "qwen-fixer",
            "implementer_backend": "qwen",
            "repo": repo,
            "number": number,
            "start_sha": start,
            "base_sha": PR_BASE_SHA,
        },
    )


def push_port(
    pem,
    world,
    fake,
    store=None,
    gitrec=None,
    clock=None,
    review=True,
    review_start=None,
    gate=True,
):
    """The push port. ``review``: ``True`` (default) records an approval of ``world.b`` for
    run-1, a SHA approves that commit instead, ``False`` records nothing. ``gate``: the
    run's last gate built exactly the pushed commit (:class:`GatedPushPort`); ``False`` for
    a real run whose own gate state is in the store."""
    store = store if store is not None else make_store()
    if review and not store.find("fixer_reviews", {"run_id": "run-1"}):
        approve_review(store, world.b if review is True else review, start=review_start or world.a)
    return (GatedPushPort if gate else GitHubPushPort)(
        store,
        transport=fake,
        secrets=lambda ref: pem,
        git=gitrec or RecordingGit(),
        git_base=f"file://{world.base}",
        clock=clock,
    )


def gated(store, params, run_id="run-1", verdict="pass"):
    """The run's fix loop as the single pr-fixer workflow leaves it before its push: the
    gate of its last try built and gated exactly the commit ``params`` push (d21: the push
    verifies itself against that gate, read from the store)."""
    run = store.get("runs", run_id)
    if run is None or "workflow" not in run:
        return
    steps = [s for s in run.get("steps") or () if not s.get("key", "").startswith("fix")]
    steps += [
        {"key": "fix", "status": "succeeded", "iteration": 0},
        {
            "key": "fix[0]/gate",
            "status": "succeeded",
            "outputs": {
                "verdict": verdict,
                "commit_sha": params.get("commit_sha"),
                "start_sha": params.get("expected_head_sha"),
                "base_sha": PR_BASE_SHA,
                "bundle": params.get("source"),
            },
        },
    ]
    inputs = {
        **(run.get("inputs") or {}),
        "repo": params.get("repo"),
        "number": params.get("number"),
        "head_branch": params.get("head_branch"),
    }
    store.put("runs", {**run, "steps": steps, "inputs": inputs})


class GatedPushPort(GitHubPushPort):
    """The push port as a pr-fixer run reaches it: its gate built exactly the commit pushed
    (:func:`gated`). These tests are about the push mechanics; the chain checks behind it
    are tested in tests/node/test_push_chain.py."""

    def invoke(self, input, key, deadline, *, context):
        gated(self._store, input, context.run_id)
        return super().invoke(input, key, deadline, context=context)


class StepClock:
    def __init__(self):
        self.now = datetime.now(UTC)

    def __call__(self):
        return self.now


class TimedFake(FakeGitHub):
    def __init__(self, world, **pull):
        super().__init__(world, **pull)
        self.timeouts = []

    def __call__(self, method, url, headers, body, timeout):
        self.timeouts.append(timeout)
        return super().__call__(method, url, headers, body, timeout)


def push_params(world, **over):
    p = {
        "actor": "gh-app",
        "repo": REPO,
        "number": 3,
        "head_branch": "fix",
        "expected_head_sha": world.a,
        "commit_sha": world.b,
        "source": str(world.agent),
    }
    p.update(over)
    return p


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# ---------------------------------------------------------------- catalogue


def test_no_merge_action_kind_exists():
    assert "github.push" in ACTION_KINDS
    assert "github.review_reply" in ACTION_KINDS
    assert not [k for k in ACTION_KINDS if "merge" in k]


# ---------------------------------------------------------------- github.push


def test_push_fast_forwards_the_pr_head_branch(pem, world, caplog):
    caplog.set_level(logging.DEBUG)
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec)
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed", res
    assert res.output["pushed"] is True
    assert res.output["head_before"] == world.a
    assert res.output["head_after"] == world.b
    assert world.remote_head() == world.b
    assert "push" in rec.verbs()
    assert PUSH_TOKEN not in caplog.text
    assert INSTALL_TOKEN not in caplog.text


@pytest.mark.parametrize("verdict", ["fail", "guard", "no_gate", None, ""])
def test_push_refuses_unless_the_wired_gate_verdict_is_pass(pem, world, verdict):
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec)
    res = port.invoke(push_params(world, gate_verdict=verdict), "k", DEADLINE, context=ctx())
    assert (res.outcome, res.error, res.retryable) == ("failed", "gate_not_passed", False)
    assert fake.calls == []
    assert rec.verbs() == []
    assert world.remote_head() == world.a


def test_push_with_a_passing_gate_verdict_pushes(pem, world):
    port = push_port(pem, world, FakeGitHub(world))
    res = port.invoke(push_params(world, gate_verdict="pass"), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert res.output["pushed"] is True


def test_push_token_is_minted_for_exactly_one_repo_with_contents_write_only(pem, world):
    fake = FakeGitHub(world)
    push_port(pem, world, fake).invoke(push_params(world), "k", DEADLINE, context=ctx())
    minted = [c[3] for c in fake.calls if c[1].endswith("/access_tokens") and c[3]]
    assert minted == [{"repositories": ["widgets"], "permissions": {"contents": "write"}}]


def test_push_never_forces_and_keeps_the_token_out_of_argv(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    push_port(pem, world, fake, gitrec=rec).invoke(push_params(world), "k", DEADLINE, context=ctx())
    for argv, env in rec.calls:
        joined = " ".join(argv)
        assert PUSH_TOKEN not in joined
        assert INSTALL_TOKEN not in joined
        assert "--force" not in argv
        assert "-f" not in argv
        assert "--force-with-lease" not in joined
        assert "--mirror" not in argv
        assert not any(a.startswith("+") for a in argv)
    pushes = [(argv, env) for argv, env in rec.calls if "push" in argv]
    assert len(pushes) == 1
    argv, env = pushes[0]
    assert "--no-verify" in argv
    assert argv[-1] == f"{world.b}:refs/heads/fix"
    assert env.get("GIT_CONFIG_KEY_0") == "http.extraHeader"
    assert env.get("GIT_CONFIG_GLOBAL")
    assert env.get("GIT_CONFIG_NOSYSTEM") == "1"
    # only the ls-remote and push calls ever carry credentials
    with_token = [a for a, e in rec.calls if "GIT_CONFIG_VALUE_0" in e]
    assert {next(x for x in a if x in ("ls-remote", "push")) for a in with_token} == {
        "ls-remote",
        "push",
    }


def test_stale_expected_head_sha_fails_and_pushes_nothing(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec, review_start=world.a0)
    res = port.invoke(push_params(world, expected_head_sha=world.a0), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "head_moved"
    assert not res.retryable
    assert world.remote_head() == world.a
    assert "push" not in rec.verbs()
    assert not [c for c in fake.calls if c[3]]  # no push token was ever minted


def test_remote_moved_after_the_pr_read_is_refused_by_ls_remote(pem, world):
    fake, rec = FakeGitHub(world, head_sha=None), RecordingGit()
    fake.pull["head_sha"] = world.a  # the API still says A ...
    port = push_port(pem, world, fake, gitrec=rec)
    fake.on_push_token = world.move_remote  # ... but the branch moves before the push
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "head_moved"
    assert "push" not in rec.verbs()
    assert world.remote_head() != world.b


def test_non_fast_forward_is_refused_before_any_network_call(pem, world):
    git("reset", "-q", "--hard", world.a0, cwd=world.agent)
    divergent = commit(world.agent, "divergent")  # not a descendant of A
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec, review=divergent).invoke(
        push_params(world, commit_sha=divergent), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed"
    assert res.error == "not_fast_forward"
    assert not res.retryable
    assert fake.calls == []
    assert "ls-remote" not in rec.verbs()
    assert "push" not in rec.verbs()
    assert world.remote_head() == world.a


def test_unknown_expected_sha_is_not_fast_forward(pem, world):
    fake = FakeGitHub(world)
    res = push_port(pem, world, fake, review_start="1" * 40).invoke(
        push_params(world, expected_head_sha="1" * 40), "k", DEADLINE, context=ctx()
    )
    assert res.error == "not_fast_forward"
    assert fake.calls == []


def test_disabled_rule_refuses_before_anything(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, store=make_store(rule_enabled=False), gitrec=rec)
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "rule_disabled"
    assert not res.retryable
    assert fake.calls == []
    assert rec.calls == []


def test_rule_disabled_during_the_push_step_is_refused_at_push_time(pem, world):
    store = make_store()
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, store=store, gitrec=rec)
    fake.on_push_token = lambda: store.put(
        "rules", {"id": "fixer", "name": "fixer", "enabled": False}
    )
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "rule_disabled"
    assert "push" not in rec.verbs()
    assert world.remote_head() == world.a


def test_deleted_rule_or_missing_run_refuses(pem, world):
    store = make_store()
    store.put("rules", {"id": "fixer", "name": "fixer", "deleted_at": "2026-10-01T00:00:00Z"})
    res = push_port(pem, world, FakeGitHub(world), store=store).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.error == "rule_disabled"
    res = push_port(pem, world, FakeGitHub(world)).invoke(
        push_params(world), "k", DEADLINE, context=ctx("no-such-run")
    )
    assert res.error == "run_not_found"


def test_direct_workflow_run_checks_its_workflow(pem, world):
    store = make_store()
    store.put("runs", {"id": "adhoc-1", "rule_id": "adhoc:wf", "workflow_id": "wf"})
    store.put("workflows", {"id": "wf", "name": "wf", "enabled": False})
    res = push_port(pem, world, FakeGitHub(world), store=store).invoke(
        push_params(world), "k", DEADLINE, context=ctx("adhoc-1")
    )
    assert res.error == "workflow_disabled"


def test_target_must_be_the_prs_own_head_branch(pem, world):
    git("push", "-q", str(world.remote), "fix:refs/heads/other", cwd=world.seed)
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world, head_branch="other"), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed"
    assert res.error == "not_pr_head_branch"
    assert "push" not in rec.verbs()


def test_fork_pr_is_refused(pem, world):
    fake, rec = FakeGitHub(world, head_repo="mallory/widgets"), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.error == "not_same_repo_pr"
    assert "push" not in rec.verbs()


def test_closed_pr_is_refused(pem, world):
    fake = FakeGitHub(world, state="closed")
    res = push_port(pem, world, fake).invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.error == "pr_not_open"


def test_repo_off_the_allowlist_fails_without_network(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world, repo="evil/repo"), "k", DEADLINE, context=ctx()
    )
    assert res.error == "repo_not_allowed"
    assert fake.calls == []
    assert rec.calls == []


@pytest.mark.parametrize(
    "over",
    [
        {"head_branch": "-delete"},
        {"head_branch": "a..b"},
        {"head_branch": "+fix"},
        {"expected_head_sha": "HEAD"},
        {"source": "relative/path"},
        {"commit_sha": "HEAD"},
        {"commit_sha": "--upload-pack=x"},
        {"commit_sha": None},
        {"number": "x"},
    ],
)
def test_bad_input_is_refused(pem, world, over):
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world, **over), "k", DEADLINE, context=ctx()
    )
    assert res.error == "bad_input"
    assert fake.calls == []
    assert rec.calls == []


def test_retry_after_success_completes_without_pushing_again(pem, world):
    fake = FakeGitHub(world)
    port = push_port(pem, world, fake)
    assert port.invoke(push_params(world), "k", DEADLINE, context=ctx()).output["pushed"]
    rec = RecordingGit()
    port._git = rec
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert res.output["pushed"] is False
    assert res.output.get("already") is True
    assert "push" not in rec.verbs()


def test_nothing_to_push_when_commit_is_expected(pem, world):
    fake = FakeGitHub(world)
    res = push_port(pem, world, fake, review=world.a).invoke(
        push_params(world, commit_sha=world.a), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["pushed"] is False
    # d21: only after reading the PR as the App - never a success for a PR that is not open
    assert [c for c in fake.calls if "/pulls/" in c[1]]


def test_nothing_to_push_on_a_closed_pr_is_not_a_success(pem, world):
    fake = FakeGitHub(world, state="closed")
    res = push_port(pem, world, fake, review=world.a).invoke(
        push_params(world, commit_sha=world.a), "k", DEADLINE, context=ctx()
    )
    assert (res.outcome, res.error, res.retryable) == ("failed", "pr_not_open", False)
    assert world.remote_head() == world.a


def test_push_targets_commit_sha_even_after_the_agent_moves_on(pem, world):
    """The pushed commit is commit_sha, never whatever the worktree's HEAD is now."""
    later = commit(world.agent, "d")
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["head_after"] == world.b
    assert world.remote_head() == world.b != later
    # a retry with the same input after the worktree moved again completes without pushing
    commit(world.agent, "e")
    rec2 = RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec2)
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert res.output.get("already") is True
    assert "push" not in rec2.verbs()
    assert world.remote_head() == world.b


def test_commit_sha_missing_from_source_is_refused(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec, review="2" * 40).invoke(
        push_params(world, commit_sha="2" * 40), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed"
    assert res.error == "commit_not_found"
    assert not res.retryable
    assert fake.calls == []
    assert "push" not in rec.verbs()


def test_foreign_author_is_refused_when_a_commit_author_is_configured(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    store = make_store(commit_author="rules-culture-dev[bot]")
    res = push_port(pem, world, fake, store=store, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed"
    assert res.error == "foreign_author"
    assert not res.retryable
    assert fake.calls == []
    assert "push" not in rec.verbs()
    assert world.remote_head() == world.a


BOT = "rules-culture-dev[bot]"


def as_author(name, *args, cwd):
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": f"{name}@example.invalid"}
    subprocess.run(
        ["git", "-c", f"user.name={name}", "-c", f"user.email={name}@example.invalid", *args],
        cwd=cwd,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", **env},
        check=True,
        capture_output=True,
    )
    return git("rev-parse", "HEAD", cwd=cwd)


def main_moves(world, name="m1"):
    """Someone else (author ``other``) commits ``name`` on main, from ``a0``; returns it."""
    seed = world.seed
    git(
        "checkout",
        "-q",
        "-B",
        "main",
        git("rev-parse", "main", cwd=seed) if name != "m1" else world.a0,
        cwd=seed,
    )
    (seed / name).write_text(name)
    git("add", name, cwd=seed)
    sha = as_author("other", "commit", "-q", "-m", name, cwd=seed)
    git("push", "-q", str(world.remote), "main:refs/heads/main", cwd=seed)
    git("checkout", "-q", "fix", cwd=seed)
    return sha


def bot_merge(world, sha, author=BOT):
    """The agent's worktree, at A, merges ``sha`` (the base) as ``author``; returns the merge."""
    git("fetch", "-q", "origin", "main", cwd=world.agent)
    return as_author(
        author, "merge", "-q", "--no-ff", "-m", f"merge {sha[:7]}", sha, cwd=world.agent
    )


class BasedPushPort(GatedPushPort):
    """A run whose gate gated against ``gate_base`` (a real commit): the merge-from-base
    tests (d31) need the base to exist in git."""

    gate_base = None

    def invoke(self, input, key, deadline, *, context):
        gated(self._store, input, context.run_id)
        run = self._store.get("runs", context.run_id)
        for s in run["steps"]:
            if s.get("key") == "fix[0]/gate":
                s["outputs"]["base_sha"] = self.gate_base
        self._store.put("runs", run)
        return GitHubPushPort.invoke(self, input, key, deadline, context=context)


def merge_push(pem, world, tip, base, source=None):
    """Push ``tip`` (from ``source``, default the agent's worktree) for a run whose gate,
    review and PR all name ``base`` as the PR's base."""
    from culture_rules.actors.review import record_review

    git("checkout", "-q", "--detach", tip, cwd=world.agent)
    store = make_store(commit_author=BOT)
    fields = {
        "commit_sha": tip,
        "reviewed_commit": tip,
        "verdict": "approve",
        "reviewer_actor": "codex-reviewer",
        "reviewer_backend": "codex",
        "implementer_actor": "qwen-fixer",
        "implementer_backend": "qwen",
        "repo": REPO,
        "number": 3,
        "start_sha": world.a,
        "base_sha": base,
    }
    record_review(store, "run-1", iteration=0, attempt=1, fields=fields)
    port = BasedPushPort(
        store,
        transport=FakeGitHub(world, base_sha=base),
        secrets=lambda ref: pem,
        git=RecordingGit(),
        git_base=f"file://{world.base}",
    )
    port.gate_base = base
    params = push_params(world, commit_sha=tip, source=source or str(world.agent))
    return port.invoke(params, "k", DEADLINE, context=ctx())


@pytest.fixture
def merge_world(tmp_path):
    w = World(tmp_path)
    git("reset", "-q", "--hard", w.a, cwd=w.agent)  # the agent's own commit B is not used
    return w


def test_d31_a_bot_merge_from_base_is_pushed(pem, merge_world, tmp_path):
    world = merge_world
    m1 = main_moves(world)
    tip = bot_merge(world, m1)
    assert merge_push(pem, world, tip, m1).outcome == "completed"
    assert world.remote_head() == tip


def test_d31_the_merge_pushes_from_a_bundle_carrying_the_base(pem, merge_world, tmp_path):
    world = merge_world
    m1 = main_moves(world)
    tip = bot_merge(world, m1)
    git("update-ref", "refs/culture-rules/base", m1, cwd=world.agent)
    bundle = tmp_path / "gate.bundle"
    git("bundle", "create", "-q", str(bundle), "HEAD", "refs/culture-rules/base", cwd=world.agent)
    res = merge_push(pem, world, tip, m1, source=str(bundle))
    assert res.outcome == "completed", res.error
    assert world.remote_head() == tip


def test_d31_a_merge_whose_second_parent_is_not_on_base_is_refused(pem, merge_world):
    world = merge_world
    m1 = main_moves(world)
    tip = bot_merge(world, m1)
    res = merge_push(pem, world, tip, world.a0)  # the base never moved to m1
    assert res.error == "foreign_author"
    assert world.remote_head() == world.a


def test_d31_two_merges_are_refused(pem, merge_world):
    world = merge_world
    m1 = main_moves(world)
    bot_merge(world, m1)
    m2 = main_moves(world, "m2")
    tip = bot_merge(world, m2)
    res = merge_push(pem, world, tip, m2)
    assert res.error == "foreign_author"
    assert world.remote_head() == world.a


def test_d31_a_merge_not_by_the_bot_is_refused(pem, merge_world):
    world = merge_world
    m1 = main_moves(world)
    tip = bot_merge(world, m1, author="someone")
    res = merge_push(pem, world, tip, m1)
    assert res.error == "foreign_author"


def test_d31_a_foreign_commit_beside_the_merge_is_refused(pem, merge_world):
    world = merge_world
    m1 = main_moves(world)
    bot_merge(world, m1)
    (world.agent / "extra").write_text("x")
    git("add", "extra", cwd=world.agent)
    tip = as_author("someone", "commit", "-q", "-m", "extra", cwd=world.agent)
    res = merge_push(pem, world, tip, m1)
    assert res.error == "foreign_author"


def test_d31_a_foreign_commit_hidden_on_the_merged_side_is_refused(pem, merge_world):
    """The merged commit sits on base's history but is not on base: nothing is exempt."""
    world = merge_world
    m1 = main_moves(world)
    git("fetch", "-q", "origin", "main", cwd=world.agent)
    git("checkout", "-q", "--detach", m1, cwd=world.agent)
    (world.agent / "hidden").write_text("x")
    git("add", "hidden", cwd=world.agent)
    hidden = as_author("someone", "commit", "-q", "-m", "hidden", cwd=world.agent)
    git("checkout", "-q", "--detach", world.a, cwd=world.agent)
    tip = as_author(BOT, "merge", "-q", "--no-ff", "-m", "merge", hidden, cwd=world.agent)
    res = merge_push(pem, world, tip, m1)
    assert res.error == "foreign_author"


def test_configured_author_matches_by_name_or_email(pem, world):
    for author in ("t", "T@Example.invalid"):
        store = make_store(commit_author=author)
        res = push_port(pem, world, FakeGitHub(world), store=store).invoke(
            push_params(world), "k", DEADLINE, context=ctx()
        )
        assert res.outcome == "completed", (author, res)


def test_push_from_a_bundle(pem, world, tmp_path):
    bundle = tmp_path / "work.bundle"
    git("bundle", "create", "-q", str(bundle), "HEAD", cwd=world.agent)
    fake = FakeGitHub(world)
    res = push_port(pem, world, fake).invoke(
        push_params(world, source=str(bundle)), "k", DEADLINE, context=ctx()
    )
    assert res.output["head_after"] == world.b
    assert res.outcome == "completed"
    assert res.output["pushed"] is True
    assert world.remote_head() == world.b


def test_agent_repo_config_cannot_redirect_the_push(pem, world, tmp_path):
    """Hooks and url rewrites in the agent's repo never apply: the push runs elsewhere."""
    marker = tmp_path / "hook-ran"
    hook = world.agent / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    git(
        "config", f"url.file://{tmp_path}/evil/.insteadOf", f"file://{world.base}/", cwd=world.agent
    )
    res = push_port(pem, world, FakeGitHub(world)).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert world.remote_head() == world.b
    assert not marker.exists()


def test_push_port_flags():
    assert GitHubPushPort(MemoryStore()).supports_idempotency_key is True
    assert GitHubReviewReplyPort(MemoryStore()).supports_idempotency_key is False


# ---------------------------------------------------------------- github.review_reply


def reply_port(pem, fake):
    return GitHubReviewReplyPort(make_store(), transport=fake, secrets=lambda ref: pem)


def reply_params(**over):
    p = {"actor": "gh-app", "repo": REPO, "number": 3, "comment_id": 77, "body": "fixed in b"}
    p.update(over)
    return p


def test_review_reply_posts_as_the_app_without_resolving(pem):
    fake = FakeGitHub(None)
    res = reply_port(pem, fake).invoke(reply_params(), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed", res
    assert res.output["comment_id"] == 501
    assert res.output["resolved"] is False
    reply = [c for c in fake.calls if c[1].endswith("/replies")]
    assert [c[1] for c in reply] == ["/repos/acme/widgets/pulls/3/comments/77/replies"]
    assert reply[0][2]["Authorization"] == f"Bearer {INSTALL_TOKEN}"  # the App's token
    assert reply[0][3] == {"body": "fixed in b"}
    assert "/graphql" not in fake.paths()


def test_review_reply_resolves_the_given_thread_after_verifying_it(pem):
    fake = FakeGitHub(None)
    fake.gql.threads.append(("PRRT_9", REPO, 3, [12, 77]))
    res = reply_port(pem, fake).invoke(
        reply_params(resolve=True, thread_id="PRRT_9"), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["resolved"] is True
    gql = [c for c in fake.calls if c[1] == "/graphql"]
    assert "nameWithOwner" in gql[0][3]["query"]  # the supplied id is verified first
    assert "resolveReviewThread" in gql[-1][3]["query"]
    assert gql[-1][3]["variables"] == {"threadId": "PRRT_9"}
    assert all(c[2]["Authorization"] == f"Bearer {INSTALL_TOKEN}" for c in gql)


@pytest.mark.parametrize(
    "thread",
    [
        ("PRRT_X", "acme/other", 3, [77]),  # another repo's thread
        ("PRRT_X", REPO, 4, [77]),  # another PR's thread
        ("PRRT_X", REPO, 3, [5]),  # a thread on the PR that does not hold the comment
    ],
)
@pytest.mark.parametrize("resolve", [True, False])
def test_review_reply_refuses_a_thread_id_that_does_not_match(pem, thread, resolve):
    fake = FakeGitHub(None)
    fake.gql.threads.append(thread)
    res = reply_port(pem, fake).invoke(
        reply_params(resolve=resolve, thread_id="PRRT_X"), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed"
    assert res.error == "thread_mismatch"
    assert not res.retryable
    assert not [p for p in fake.paths() if p.endswith("/replies")]
    assert not [q for q, _ in fake.gql.queries if "resolveReviewThread" in q]


def test_review_reply_refuses_an_unknown_thread_id(pem):
    fake = FakeGitHub(None)
    res = reply_port(pem, fake).invoke(
        reply_params(resolve=True, thread_id="PRRT_nope"), "k", DEADLINE, context=ctx()
    )
    assert res.error == "thread_mismatch"
    assert not [p for p in fake.paths() if p.endswith("/replies")]


def test_review_reply_thread_lookup_pages_threads_and_comments(pem):
    fake = FakeGitHub(None)
    fake.gql.size = 2
    fake.gql.threads = [
        ("PRRT_a", REPO, 3, [1, 2, 3]),
        ("PRRT_b", REPO, 3, [4]),
        ("PRRT_c", REPO, 3, [5, 6, 7, 8, 9]),
        ("PRRT_d", REPO, 3, [10, 11, 12, 77]),
    ]
    res = reply_port(pem, fake).invoke(reply_params(resolve=True), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert res.output["thread_id"] == "PRRT_d"
    thread_pages = [v.get("after") for q, v in fake.gql.queries if "reviewThreads" in q]
    assert thread_pages == [None, "2"]
    assert any("after" in v and v.get("id") == "PRRT_d" for _, v in fake.gql.queries)


def test_review_reply_verifies_a_given_thread_across_comment_pages(pem):
    fake = FakeGitHub(None)
    fake.gql.size = 2
    fake.gql.threads = [("PRRT_z", REPO, 3, [1, 2, 3, 4, 77])]
    res = reply_port(pem, fake).invoke(
        reply_params(thread_id="PRRT_z"), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["thread_id"] == "PRRT_z"
    assert res.output["resolved"] is False


def test_review_reply_finds_the_thread_then_resolves(pem):
    fake = FakeGitHub(None)
    res = reply_port(pem, fake).invoke(reply_params(resolve="true"), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert res.output["thread_id"] == "PRRT_1"
    order = [c[1] for c in fake.calls if not c[1].endswith("/access_tokens")]
    assert order == [
        "/graphql",
        "/repos/acme/widgets/pulls/3/comments/77/replies",
        "/graphql",
    ]


def test_review_reply_unknown_thread_posts_nothing(pem):
    fake = FakeGitHub(None)
    fake.gql.threads = []
    res = reply_port(pem, fake).invoke(reply_params(resolve=True), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "thread_not_found"
    assert not [p for p in fake.paths() if p.endswith("/replies")]


def test_review_reply_refuses_off_allowlist_and_bad_input(pem):
    fake = FakeGitHub(None)
    port = reply_port(pem, fake)
    assert port.invoke(reply_params(repo="x/y"), "k", DEADLINE, context=ctx()).error == (
        "repo_not_allowed"
    )
    assert port.invoke(reply_params(comment_id="z"), "k", DEADLINE, context=ctx()).error == (
        "bad_input"
    )
    assert port.invoke(reply_params(resolve="maybe"), "k", DEADLINE, context=ctx()).error == (
        "bad_input"
    )
    assert fake.calls == []


# ---------------------------------------------------------------- deadlines


def test_deadline_passing_before_the_push_pushes_nothing(pem, world):
    clock = StepClock()
    deadline = clock.now + timedelta(seconds=60)
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec, clock=clock)

    def late():
        clock.now = deadline + timedelta(seconds=1)

    fake.on_push_token = late
    res = port.invoke(push_params(world), "k", deadline, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "deadline_exceeded"
    assert res.retryable
    assert "push" not in rec.verbs()
    assert world.remote_head() == world.a


def test_push_is_not_started_inside_the_safety_margin(pem, world):
    clock = StepClock()
    deadline = clock.now + timedelta(seconds=60)
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec, clock=clock)

    def nearly():
        clock.now = deadline - timedelta(seconds=5)

    fake.on_push_token = nearly
    res = port.invoke(push_params(world), "k", deadline, context=ctx())
    assert res.error == "deadline_exceeded"
    assert res.retryable
    assert "push" not in rec.verbs()
    assert world.remote_head() == world.a


def test_expired_deadline_refuses_before_any_git_or_network(pem, world):
    clock = StepClock()
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec, clock=clock)
    res = port.invoke(push_params(world), "k", clock.now - timedelta(seconds=1), context=ctx())
    assert res.error == "deadline_exceeded"
    assert fake.calls == []
    assert rec.calls == []


def test_git_and_http_calls_are_bounded_by_the_remaining_time(pem, world):
    clock = StepClock()
    fake, rec = TimedFake(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec, clock=clock)
    res = port.invoke(push_params(world), "k", clock.now + timedelta(seconds=30), context=ctx())
    assert res.outcome == "completed", res
    assert rec.timeouts
    assert max(rec.timeouts) <= 30
    assert fake.timeouts
    assert max(fake.timeouts) <= 30


def test_review_reply_honours_the_deadline(pem):
    fake = FakeGitHub(None)
    port = GitHubReviewReplyPort(make_store(), transport=fake, secrets=lambda ref: pem)
    past = datetime.now(UTC) - timedelta(seconds=1)
    res = port.invoke(reply_params(), "k", past, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "deadline_exceeded"
    assert res.retryable
    assert not [p for p in fake.paths() if p.endswith("/replies")]


# ---------------------------------------------------------------- git timeouts


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_git_timeout_kills_the_whole_process_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = tmp_path / "fake-git"
    script.write_text(f"#!/bin/sh\nsleep 30 &\necho $! > {pidfile}\nsleep 30\n")
    script.chmod(0o755)
    started = time.monotonic()
    rc, out = subprocess_git([str(script)], {"PATH": os.environ["PATH"]}, 0.5)
    assert rc == GIT_TIMED_OUT
    assert out == ""
    assert time.monotonic() - started < 10
    child = int(pidfile.read_text().strip())
    assert not _alive(child)  # the grandchild (stand-in for git-remote-https) is gone


def test_subprocess_git_reports_a_missing_binary_as_unavailable(tmp_path):
    rc, _ = subprocess_git([str(tmp_path / "nope")], {"PATH": ""}, 5)
    assert rc == GIT_UNAVAILABLE


class TimingOutGit(RecordingGit):
    """Real git, except the first call whose verb is ``verb`` times out."""

    def __init__(self, verb):
        super().__init__()
        self.verb = verb

    def __call__(self, argv, env, timeout):
        if self.verb in argv:
            self.calls.append((list(argv), dict(env)))
            return GIT_TIMED_OUT, ""
        return super().__call__(argv, env, timeout)


@pytest.mark.parametrize("verb", ["fetch", "rev-parse", "cat-file", "merge-base", "log"])
def test_git_timeouts_are_retryable_not_validation_failures(pem, world, verb):
    store = make_store(commit_author="t")  # so the author check (git log) runs too
    fake, rec = FakeGitHub(world), TimingOutGit(verb)
    res = push_port(pem, world, fake, store=store, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed", res
    assert res.error == "git_timeout", res
    assert res.retryable, res
    assert fake.calls == []
    assert "push" not in rec.verbs()


def test_git_timeout_past_the_deadline_is_deadline_exceeded(pem, world):
    clock = StepClock()
    deadline = clock.now + timedelta(seconds=60)

    class Late(RecordingGit):
        def __call__(self, argv, env, timeout):
            if "fetch" in argv:
                clock.now = deadline + timedelta(seconds=1)
                return GIT_TIMED_OUT, ""
            return super().__call__(argv, env, timeout)

    res = push_port(pem, world, FakeGitHub(world), gitrec=Late(), clock=clock).invoke(
        push_params(world), "k", deadline, context=ctx()
    )
    assert res.error == "deadline_exceeded"
    assert res.retryable


# ---------------------------------------------------------------- characterization
# (the Sonar S3776 split of GitHubPushPort.invoke / _publish: the remaining outcomes)


def test_an_app_without_installation_id_is_misconfigured_before_any_network(pem, world):
    store = make_store(
        connection={"app_id": "1", "private_key": "grant:GH_KEY", "repos": [REPO]},
    )
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, store=store, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert (res.outcome, res.error, res.retryable) == ("failed", "actor_misconfigured", False)
    assert fake.calls == []
    assert rec.calls == []


def test_an_unknown_actor_is_actor_not_found(pem, world):
    fake = FakeGitHub(world)
    port = push_port(pem, world, fake)
    other = InvocationContext(run_id="run-1", step_id="push", kind="action", host="h", actor="x")
    res = port.invoke(push_params(world, actor="x"), "k", DEADLINE, context=other)
    assert (res.error, res.retryable) == ("actor_not_found", False)
    assert fake.calls == []


def test_a_github_error_mid_push_fails_with_its_code(pem, world):
    class Broken(FakeGitHub):
        def __call__(self, method, url, headers, body, timeout):
            if url.endswith("/pulls/3"):
                return 502, b"{}"
            return super().__call__(method, url, headers, body, timeout)

    rec = RecordingGit()
    res = push_port(pem, world, Broken(world), gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed"
    assert (res.error, res.retryable) == ("http_502", True)
    assert "push" not in rec.verbs()
    assert world.remote_head() == world.a


def test_a_remote_already_at_the_commit_completes_without_pushing(pem, world):
    git("push", "-q", str(world.remote), f"{world.b}:refs/heads/fix", cwd=world.agent)
    fake, rec = FakeGitHub(world, head_sha=world.a), RecordingGit()  # the PR read lags
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed", res.error
    assert res.output["pushed"] is False
    assert res.output["already"] is True
    assert "push" not in rec.verbs()


def test_a_refused_consume_stops_the_push(pem, world, monkeypatch):
    from culture_rules.node.actions import github_pr

    monkeypatch.setattr(github_pr, "consume_approval", lambda *a, **k: "review_consumed")
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert (res.outcome, res.error, res.retryable) == ("failed", "review_consumed", False)
    assert "push" not in rec.verbs()
    assert world.remote_head() == world.a
