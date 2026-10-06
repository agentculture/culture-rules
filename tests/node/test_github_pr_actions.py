"""github.push / github.review_reply ports: fake GitHub API, real local git, no network.

The "remote" is a local bare repo reached through ``git_base=file://...``; the GitHub REST /
GraphQL API is a fake transport. Nothing here talks to the internet.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from culture_rules.engine.actorport import InvocationContext  # noqa: E402
from culture_rules.model.action_kinds import ACTION_KINDS  # noqa: E402
from culture_rules.node.actions.github_pr import (  # noqa: E402
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
                "base": {"ref": "main", "repo": {"full_name": REPO}},
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


def make_store(rule_enabled=True, **actor_params):
    store = MemoryStore()
    store.put("actors", actor_doc(**actor_params))
    store.put("rules", {"id": "fixer", "name": "fixer", "enabled": rule_enabled})
    store.put("runs", {"id": "run-1", "kind": "run", "rule_id": "fixer", "workflow_id": None})
    return store


def ctx(run_id="run-1"):
    return InvocationContext(run_id=run_id, step_id="push", kind="action", host="h", actor="gh-app")


def push_port(pem, world, fake, store=None, gitrec=None, clock=None):
    return GitHubPushPort(
        store or make_store(),
        transport=fake,
        secrets=lambda ref: pem,
        git=gitrec or RecordingGit(),
        git_base=f"file://{world.base}",
        clock=clock,
    )


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
    assert "github.push" in ACTION_KINDS and "github.review_reply" in ACTION_KINDS
    assert not [k for k in ACTION_KINDS if "merge" in k]


# ---------------------------------------------------------------- github.push


def test_push_fast_forwards_the_pr_head_branch(pem, world, caplog):
    caplog.set_level(logging.DEBUG)
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec)
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed", res
    assert res.output["pushed"] is True
    assert res.output["head_before"] == world.a and res.output["head_after"] == world.b
    assert world.remote_head() == world.b
    assert "push" in rec.verbs()
    assert PUSH_TOKEN not in caplog.text and INSTALL_TOKEN not in caplog.text


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
        assert PUSH_TOKEN not in joined and INSTALL_TOKEN not in joined
        assert "--force" not in argv and "-f" not in argv
        assert "--force-with-lease" not in joined and "--mirror" not in argv
        assert not any(a.startswith("+") for a in argv)
    pushes = [(argv, env) for argv, env in rec.calls if "push" in argv]
    assert len(pushes) == 1
    argv, env = pushes[0]
    assert "--no-verify" in argv and argv[-1] == f"{world.b}:refs/heads/fix"
    assert env.get("GIT_CONFIG_KEY_0") == "http.extraHeader"
    assert env.get("GIT_CONFIG_GLOBAL") and env.get("GIT_CONFIG_NOSYSTEM") == "1"
    # only the ls-remote and push calls ever carry credentials
    with_token = [a for a, e in rec.calls if "GIT_CONFIG_VALUE_0" in e]
    assert {next(x for x in a if x in ("ls-remote", "push")) for a in with_token} == {
        "ls-remote",
        "push",
    }


def test_stale_expected_head_sha_fails_and_pushes_nothing(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec)
    res = port.invoke(push_params(world, expected_head_sha=world.a0), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed" and res.error == "head_moved" and not res.retryable
    assert world.remote_head() == world.a
    assert "push" not in rec.verbs()
    assert not [c for c in fake.calls if c[3]]  # no push token was ever minted


def test_remote_moved_after_the_pr_read_is_refused_by_ls_remote(pem, world):
    fake, rec = FakeGitHub(world, head_sha=None), RecordingGit()
    fake.pull["head_sha"] = world.a  # the API still says A ...
    port = push_port(pem, world, fake, gitrec=rec)
    fake.on_push_token = world.move_remote  # ... but the branch moves before the push
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed" and res.error == "head_moved"
    assert "push" not in rec.verbs()
    assert world.remote_head() != world.b


def test_non_fast_forward_is_refused_before_any_network_call(pem, world):
    git("reset", "-q", "--hard", world.a0, cwd=world.agent)
    divergent = commit(world.agent, "divergent")  # not a descendant of A
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world, commit_sha=divergent), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed" and res.error == "not_fast_forward" and not res.retryable
    assert fake.calls == []
    assert "ls-remote" not in rec.verbs() and "push" not in rec.verbs()
    assert world.remote_head() == world.a


def test_unknown_expected_sha_is_not_fast_forward(pem, world):
    fake = FakeGitHub(world)
    res = push_port(pem, world, fake).invoke(
        push_params(world, expected_head_sha="1" * 40), "k", DEADLINE, context=ctx()
    )
    assert res.error == "not_fast_forward" and fake.calls == []


def test_disabled_rule_refuses_before_anything(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, store=make_store(rule_enabled=False), gitrec=rec)
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed" and res.error == "rule_disabled" and not res.retryable
    assert fake.calls == [] and rec.calls == []


def test_rule_disabled_during_the_push_step_is_refused_at_push_time(pem, world):
    store = make_store()
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, store=store, gitrec=rec)
    fake.on_push_token = lambda: store.put(
        "rules", {"id": "fixer", "name": "fixer", "enabled": False}
    )
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed" and res.error == "rule_disabled"
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
    assert res.outcome == "failed" and res.error == "not_pr_head_branch"
    assert "push" not in rec.verbs()


def test_fork_pr_is_refused(pem, world):
    fake, rec = FakeGitHub(world, head_repo="mallory/widgets"), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.error == "not_same_repo_pr" and "push" not in rec.verbs()


def test_closed_pr_is_refused(pem, world):
    fake = FakeGitHub(world, state="closed")
    res = push_port(pem, world, fake).invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.error == "pr_not_open"


def test_repo_off_the_allowlist_fails_without_network(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world, repo="evil/repo"), "k", DEADLINE, context=ctx()
    )
    assert res.error == "repo_not_allowed" and fake.calls == [] and rec.calls == []


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
    assert res.error == "bad_input" and fake.calls == [] and rec.calls == []


def test_retry_after_success_completes_without_pushing_again(pem, world):
    fake = FakeGitHub(world)
    port = push_port(pem, world, fake)
    assert port.invoke(push_params(world), "k", DEADLINE, context=ctx()).output["pushed"]
    rec = RecordingGit()
    port._git = rec
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed" and res.output["pushed"] is False
    assert res.output.get("already") is True
    assert "push" not in rec.verbs()


def test_nothing_to_push_when_commit_is_expected(pem, world):
    fake = FakeGitHub(world)
    res = push_port(pem, world, fake).invoke(
        push_params(world, commit_sha=world.a), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed" and res.output["pushed"] is False and fake.calls == []


def test_push_targets_commit_sha_even_after_the_agent_moves_on(pem, world):
    """The pushed commit is commit_sha, never whatever the worktree's HEAD is now."""
    later = commit(world.agent, "d")
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed" and res.output["head_after"] == world.b
    assert world.remote_head() == world.b != later
    # a retry with the same input after the worktree moved again completes without pushing
    commit(world.agent, "e")
    rec2 = RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec2)
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed" and res.output.get("already") is True
    assert "push" not in rec2.verbs() and world.remote_head() == world.b


def test_commit_sha_missing_from_source_is_refused(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    res = push_port(pem, world, fake, gitrec=rec).invoke(
        push_params(world, commit_sha="2" * 40), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed" and res.error == "commit_not_found" and not res.retryable
    assert fake.calls == [] and "push" not in rec.verbs()


def test_foreign_author_is_refused_when_a_commit_author_is_configured(pem, world):
    fake, rec = FakeGitHub(world), RecordingGit()
    store = make_store(commit_author="rules-culture-dev[bot]")
    res = push_port(pem, world, fake, store=store, gitrec=rec).invoke(
        push_params(world), "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "failed" and res.error == "foreign_author" and not res.retryable
    assert fake.calls == [] and "push" not in rec.verbs()
    assert world.remote_head() == world.a


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
    assert res.outcome == "completed" and res.output["pushed"] is True
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
    assert res.outcome == "completed" and world.remote_head() == world.b
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
    assert res.output["comment_id"] == 501 and res.output["resolved"] is False
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
    assert res.outcome == "completed" and res.output["resolved"] is True
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
    assert res.outcome == "failed" and res.error == "thread_mismatch" and not res.retryable
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
    assert res.outcome == "completed" and res.output["thread_id"] == "PRRT_d"
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
    assert res.outcome == "completed" and res.output["thread_id"] == "PRRT_z"
    assert res.output["resolved"] is False


def test_review_reply_finds_the_thread_then_resolves(pem):
    fake = FakeGitHub(None)
    res = reply_port(pem, fake).invoke(reply_params(resolve="true"), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed" and res.output["thread_id"] == "PRRT_1"
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
    assert res.outcome == "failed" and res.error == "thread_not_found"
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
    assert res.outcome == "failed" and res.error == "deadline_exceeded" and res.retryable
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
    assert res.error == "deadline_exceeded" and res.retryable
    assert "push" not in rec.verbs() and world.remote_head() == world.a


def test_expired_deadline_refuses_before_any_git_or_network(pem, world):
    clock = StepClock()
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec, clock=clock)
    res = port.invoke(push_params(world), "k", clock.now - timedelta(seconds=1), context=ctx())
    assert res.error == "deadline_exceeded" and fake.calls == [] and rec.calls == []


def test_git_and_http_calls_are_bounded_by_the_remaining_time(pem, world):
    clock = StepClock()
    fake, rec = TimedFake(world), RecordingGit()
    port = push_port(pem, world, fake, gitrec=rec, clock=clock)
    res = port.invoke(push_params(world), "k", clock.now + timedelta(seconds=30), context=ctx())
    assert res.outcome == "completed", res
    assert rec.timeouts and max(rec.timeouts) <= 30
    assert fake.timeouts and max(fake.timeouts) <= 30


def test_review_reply_honours_the_deadline(pem):
    fake = FakeGitHub(None)
    port = GitHubReviewReplyPort(make_store(), transport=fake, secrets=lambda ref: pem)
    past = datetime.now(UTC) - timedelta(seconds=1)
    res = port.invoke(reply_params(), "k", past, context=ctx())
    assert res.outcome == "failed" and res.error == "deadline_exceeded" and res.retryable
    assert not [p for p in fake.paths() if p.endswith("/replies")]
