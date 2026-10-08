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

PLANTED = "planted by the agent: pretend this is a credential\n"


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
    repo.commit("add a secret", {"leak.txt": PLANTED})
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


def test_the_built_commit_is_deterministic(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("step one", {"src/a.py": "a = 1\n"})
    repo.commit("fix: make x 3", {"src/app.py": "x = 3\n"})
    first = judge(store, LocalRunner(), repo, tmp_path, clock)
    again = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert first["commit_sha"] == again["commit_sha"]


def test_no_gate_reports_the_built_commit_too(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, None)
    repo.commit("one", {"src/a.py": "a = 1\n"})
    tip = repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == NO_GATE
    assert out["agent_commit_sha"] == tip and out["commit_sha"] != tip


def test_even_a_single_agent_commit_is_rebuilt_with_engine_metadata(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    tip = repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["agent_commit_sha"] == tip and out["commit_sha"] != tip


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


# --------------------------------------------------------------------------- round 2, #1


def test_nothing_agent_written_reaches_the_built_commits_metadata(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    from culture_rules.actors.gate import FIXER_COMMIT_IDENTITY

    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "add", "-A")
    (repo.wt / "src/app.py").write_text("x = 3\n")
    git(repo.wt, "add", "src/app.py")
    env = {
        "GIT_COMMITTER_EMAIL": "planted-in-the-committer@leak.example",
        "GIT_AUTHOR_NAME": "planted-author-name",
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", "fix\n\nplanted-in-the-message"],
        cwd=repo.wt,
        env={**GIT_ENV, **env},
        check=True,
    )
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    built = out["commit_sha"]
    check = bundle_repo(tmp_path, out["bundle"], built)
    raw = git(check, "cat-file", "commit", built)
    for planted in ("planted-in-the-committer", "planted-author-name", "planted-in-the-message"):
        assert planted not in raw
    who = git(check, "log", "-1", "--format=%an <%ae>|%cn <%ce>", built)
    assert who == f"{FIXER_COMMIT_IDENTITY}|{FIXER_COMMIT_IDENTITY}"
    assert git(check, "log", "-1", "--format=%B", built).startswith("pr-fixer: ")
    # deterministic: the dates are the PR head's, so a re-run builds the same commit
    assert judge(store, LocalRunner(), repo, tmp_path, clock)["commit_sha"] == built
    assert git(check, "log", "-1", "--format=%cd", "--date=raw", built) == git(
        repo.wt, "log", "-1", "--format=%cd", "--date=raw", repo.start
    )


# --------------------------------------------------------------------------- round 3, #5


def test_r3_5_the_gate_tests_exactly_the_commit_it_publishes(store, tmp_path, clock):  # noqa: F811
    from tests.actors.test_gate import PY

    show = [
        PY,
        "-c",
        "import subprocess; print('HEAD=' + subprocess.check_output("
        "['git', 'log', '-1', '--format=%H %an'], text=True).strip())",
    ]
    repo = Repo(tmp_path, gate_yaml([show]))
    tip = repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS
    assert out["commit_sha"] != tip
    # the tests saw the published commit (its SHA and its engine-written author), not the tip
    assert f"HEAD={out['commit_sha']} rules-culture-dev[bot]" in out["output_tail"]
