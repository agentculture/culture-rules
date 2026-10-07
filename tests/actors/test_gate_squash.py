"""Codex review #4 (P1): the agent's intermediate commits never leave the fixer machine.

``git diff start..tip`` shows only the net change, so an agent could commit a secret or a
payload, remove it in a later commit, and the reviewed diff would hide it while the bundle
and the push carried the whole graph. The gate (trusted code) therefore builds ONE commit
itself - ``git commit-tree <tip^{tree}> -p <start_sha>``, message and author from the
agent's tip - and that commit is what is diffed, reviewed, bundled and pushed. The
agent's own commits stay in its worktree. Merges in ``start..tip`` are refused.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from culture_rules.actors.gate import GUARD, NO_GATE, PASS
from tests.actors.test_gate import (  # noqa: F401 - fixtures
    GIT_ENV,
    PASSING,
    LocalRunner,
    Repo,
    clock,
    gate_yaml,
    git,
    judge,
    store,
)

SECRET = "AWS_SECRET=planted-by-the-agent\n"


def bundle_repo(tmp_path: Path, bundle: str, sha: str) -> Path:
    check = tmp_path / "check.git"
    git(tmp_path, "init", "-q", "--bare", str(check))
    git(check, "fetch", "-q", bundle, f"{sha}:refs/x")
    return check


def has_object(repo: Path, sha: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", sha], cwd=repo, env=GIT_ENV, capture_output=True
        ).returncode
        == 0
    )


def test_an_intermediate_secret_commit_is_never_bundled(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("add a secret", {"leak.txt": SECRET})
    secret_blob = git(repo.wt, "rev-parse", "HEAD:leak.txt")
    repo.commit("remove it", {"leak.txt": None})
    tip = repo.commit("the fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS
    pushed = out["commit_sha"]
    assert pushed != tip and out["agent_commit_sha"] == tip
    check = bundle_repo(tmp_path, out["bundle"], pushed)
    # one commit on top of the PR head, with exactly the tip's tree
    assert git(check, "rev-parse", f"{pushed}^") == repo.start
    assert git(check, "rev-list", "--count", f"{repo.start}..{pushed}") == "1"
    assert git(check, "rev-parse", f"{pushed}^{{tree}}") == git(repo.wt, "rev-parse", "HEAD^{tree}")
    assert not has_object(check, secret_blob)
    assert not has_object(check, tip)
    # the reviewed diff is exactly the pushed commit's diff
    assert out["diff"].strip() == git(repo.wt, "diff", repo.start, tip)
    assert "leak.txt" not in out["diff"]


def test_the_built_commit_keeps_the_tips_message_and_author_and_is_deterministic(
    store, tmp_path, clock  # noqa: F811
):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("step one", {"src/a.py": "a = 1\n"})
    tip = repo.commit("fix: make x 3\n\nbody text", {"src/app.py": "x = 3\n"})
    first = judge(store, LocalRunner(), repo, tmp_path, clock)
    again = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert first["commit_sha"] == again["commit_sha"]
    check = bundle_repo(tmp_path, first["bundle"], first["commit_sha"])
    fmt = "%an <%ae>|%cn <%ce>|%B"
    assert git(check, "log", "-1", f"--format={fmt}", first["commit_sha"]) == git(
        repo.wt, "log", "-1", f"--format={fmt}", tip
    )


def test_no_gate_reports_the_built_commit_too(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, None)
    repo.commit("one", {"src/a.py": "a = 1\n"})
    tip = repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == NO_GATE
    assert out["agent_commit_sha"] == tip and out["commit_sha"] != tip


def test_a_single_plain_agent_commit_rebuilds_to_the_same_sha(store, tmp_path, clock):  # noqa: F811
    # same tree, parent, author, committer, dates and message: byte-identical, so the
    # pushed commit is the agent's - and still nothing but the reviewed change
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    tip = repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["commit_sha"] == tip == out["agent_commit_sha"]


def test_no_agent_commit_pushes_nothing_new(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    out = judge(store, LocalRunner(), repo, tmp_path, clock, commit=repo.start)
    assert out["verdict"] == PASS and out["commit_sha"] == repo.start


def test_a_merge_in_the_range_is_guarded(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "checkout", "-q", "-b", "side", repo.base)
    side = repo.commit("side", {"src/side.py": "s = 1\n"})
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    git(repo.wt, "merge", "-q", "--no-ff", "-m", "merge side", side)
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD and out["rule"] == "merge_commit"
    assert out["bundle"] is None
