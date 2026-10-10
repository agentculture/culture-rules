"""d37: the fixer's base is the base branch's live tip, not GitHub's ``base.sha``.

Two live cases on 0.18.7 (2026-10-10):

* katvan#57 - ``base.sha`` was the PR's fork point; the agent made a real merge of the
  branch's tip and the gate refused it (``merge_commit``: the second parent is not on
  ``base_sha``), ending the story at try 1;
* irc-lens#68 - ``base.sha`` was the branch's tip, absent from the head's history; the gate
  could not pack it (``source_unavailable``) and the story ended at try 1.

Now the try is given the live tip (dispatch), the gate fetches a base the worktree lacks,
a refusal the agent can fix is a guard verdict (a retry with the finding), and an
infrastructure refusal is the ``unjudged`` verdict (a retry), never the end of a story.
"""

from __future__ import annotations

from culture_rules.actors.gate import GUARD, NO_GATE, PASS, UNJUDGED
from tests.actors.test_gate import (  # noqa: F401 - fixtures
    PASSING,
    LocalRunner,
    Repo,
    clock,
    gate_yaml,
    git,
    judge,
    store,
)
from tests.actors.test_gate_squash import merge_base_in


def test_d37_a_real_merge_of_the_live_tip_passes_and_only_the_resolution_is_reviewed(
    store, tmp_path, clock  # noqa: F811
):
    """katvan#57 with the live tip: the PR forked at the old base, the base moved with a
    conflicting change, the agent merged the tip and resolved it."""
    repo = Repo(tmp_path, None)
    merge_base_in(
        repo,
        {"src/app.py": "x = 5\n", "src/other.py": "o = 1\n"},
        resolve={"src/app.py": "x = 9\n"},
    )
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == NO_GATE
    assert "src/other.py" not in out["diff"]
    assert "+x = 9" in out["diff"]


def test_d37_a_merge_against_a_stale_base_is_a_guard_verdict_not_the_end(
    store, tmp_path, clock  # noqa: F811
):
    """katvan#57 as it happened: base_sha was the fork point. Without a gate section the
    merge was refused as a failure that ended the story; now it is a guard verdict, so
    the try goes back to the queue with the refusal as its finding."""
    repo = Repo(tmp_path, None)
    fork = repo.base
    merge_base_in(repo, {"src/app.py": "x = 5\n"}, resolve={"src/app.py": "x = 9\n"})
    repo.base = fork
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert out["rule"] == "merge_commit"
    assert "merge" in out["instruction"]


def origin_with_moved_base(tmp_path, repo):
    """A remote ``origin`` whose ``main`` moved past the worktree's base; returns the tip
    the worktree does not hold."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo.wt), str(origin))
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(origin), str(other))
    git(other, "checkout", "-q", "-B", "main", repo.base)
    (other / "NOTES.md").write_text("moved\n")
    git(other, "add", "NOTES.md")
    git(other, "commit", "-q", "-m", "main moves")
    tip = git(other, "rev-parse", "HEAD")
    git(other, "push", "-q", "origin", "HEAD:refs/heads/moved")
    git(repo.wt, "remote", "add", "origin", str(origin))
    return tip


def test_d37_a_base_the_worktree_lacks_is_fetched(store, tmp_path, clock):  # noqa: F811
    """irc-lens#68: the base tip is not in the head's history, so not in the worktree."""
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    tip = origin_with_moved_base(tmp_path, repo)
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    repo.base = tip
    runner = LocalRunner()
    out = judge(store, runner, repo, tmp_path, clock)
    assert out["verdict"] == PASS, out
    assert any("fetch" in call for call in runner.calls)


def test_d37_a_base_that_cannot_be_fetched_is_unjudged_a_retry(
    store, tmp_path, clock  # noqa: F811
):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    repo.base = "f" * 40  # nowhere: the worktree has no remote to fetch it from
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == UNJUDGED
    assert out["rule"] == "base_unavailable"
    assert out["instruction"]
    assert out["commit_sha"] is None


def test_d37_a_base_already_in_the_worktree_is_not_fetched(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    runner = LocalRunner()
    assert judge(store, runner, repo, tmp_path, clock)["verdict"] == PASS
    assert not any("fetch" in call for call in runner.calls)


def test_d37_a_missing_worktree_is_unjudged_a_retry(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    head = repo.commit("fix", {"src/app.py": "x = 3\n"})
    repo.wt.rename(tmp_path / "gone")
    out = judge(store, LocalRunner(), repo, tmp_path, clock, commit=head)
    assert out["verdict"] == UNJUDGED
    assert out["rule"] in ("source_unavailable", "gate_runner_unavailable")
