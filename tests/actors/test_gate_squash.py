"""Codex review #4 (P1): the agent's intermediate commits never leave the fixer machine.

``git diff start..tip`` shows only the net change, so an agent could commit a secret or a
payload, remove it in a later commit, and the reviewed diff would hide it while the bundle
and the push carried the whole graph. The gate (trusted code) therefore builds ONE commit
itself - ``git commit-tree <tip^{tree}> -p <start_sha>``, message and author from the
agent's tip - and that commit is what is diffed, reviewed, bundled and pushed. The
agent's own commits stay in its worktree. Merges in ``start..tip`` are refused, but for
one (d31): a merge from base, whose second parent is on the PR's base branch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from culture_rules.actors.gate import GUARD, NO_GATE, PASS
from tests.actors.test_gate import (  # noqa: F401 - fixtures
    GIT_ENV,
    PASSING,
    TESTS_PY,
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
    assert pushed != tip
    assert out["agent_commit_sha"] == tip
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
    assert out["agent_commit_sha"] == tip
    assert out["commit_sha"] != tip


def test_even_a_single_agent_commit_is_rebuilt_with_engine_metadata(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    tip = repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["agent_commit_sha"] == tip
    assert out["commit_sha"] != tip


def test_no_agent_commit_pushes_nothing_new(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    out = judge(store, LocalRunner(), repo, tmp_path, clock, commit=repo.start)
    assert out["verdict"] == PASS
    assert out["commit_sha"] == repo.start


def test_a_merge_in_the_range_is_guarded(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "checkout", "-q", "-b", "side", repo.base)
    side = repo.commit("side", {"src/side.py": "s = 1\n"})
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    git(repo.wt, "merge", "-q", "--no-ff", "-m", "merge side", side)
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert out["rule"] == "merge_commit"
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


# --------------------------------------------------------------------------- d31


BIG = "".join(f"line {i}\n" for i in range(3000))  # a base change far over any review cap


def merge_base_in(repo: Repo, main_files: dict, resolve: dict | None = None) -> str:
    """The base branch moves (``main_files`` on top of the PR's base) and the agent merges
    it into the PR head, resolving any conflict with ``resolve``; ``repo.base`` becomes the
    moved base (the PR's base at gate time). Returns the agent's merge commit."""
    git(repo.wt, "checkout", "-q", "--detach", repo.base)
    moved = repo.commit("main moves", main_files)
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    merged = subprocess.run(
        ["git", "merge", "-q", "--no-ff", "-m", "merge main", moved],
        cwd=repo.wt,
        env=GIT_ENV,
        capture_output=True,
    )
    if merged.returncode != 0:  # a conflict: the agent resolves it
        assert resolve, merged.stderr
        for name, text in resolve.items():
            if text is not None:  # None: commit the file as the merge left it
                (repo.wt / name).write_text(text)
            git(repo.wt, "add", name)
        git(repo.wt, "commit", "-q", "--no-edit")
    repo.base = moved
    return git(repo.wt, "rev-parse", "HEAD")


def test_d31_a_merge_from_base_is_built_as_one_bot_merge(store, tmp_path, clock):  # noqa: F811
    from culture_rules.actors.gate import FIXER_COMMIT_IDENTITY

    repo = Repo(tmp_path, gate_yaml([PASSING]))
    tip = merge_base_in(repo, {"src/other.py": "o = 1\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS, out
    built = out["commit_sha"]
    assert built != tip
    check = bundle_repo(tmp_path, out["bundle"], built)
    # exactly one new commit of the PR's own line: a merge of the PR head and the base
    assert git(check, "rev-parse", f"{built}^1", f"{built}^2").split() == [repo.start, repo.base]
    assert git(check, "rev-parse", f"{built}^{{tree}}") == git(repo.wt, "rev-parse", "HEAD^{tree}")
    who = git(check, "log", "-1", "--format=%an <%ae>", built)
    assert who == FIXER_COMMIT_IDENTITY
    # the bundle carries the base too, so github.push can check the merge against it
    git(check, "fetch", "-q", out["bundle"], f"{repo.base}:refs/base")
    # a clean merge resolves nothing: the reviewed diff holds none of the base's change
    assert "src/other.py" not in out["diff"]
    assert out["diff_truncated"] is False
    assert judge(store, LocalRunner(), repo, tmp_path, clock)["commit_sha"] == built


def test_d31_the_review_sees_only_the_resolution(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    merge_base_in(
        repo,
        {"src/app.py": "x = 5\n", "src/big.py": BIG},
        resolve={"src/app.py": "x = 7\n"},
    )
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS, out
    assert out["diff_truncated"] is False  # the base's 3000-line file is not the agent's
    assert "src/big.py" not in out["diff"]
    assert "+x = 7" in out["diff"]
    assert repo.base in out["diff"]  # the reviewer is told it is a merge from that base


def test_d31_the_bases_own_changes_are_not_guarded(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    skip = TESTS_PY + "\n\n@pytest.mark.skip\ndef test_later():\n    pass\n"
    merge_base_in(repo, {"tests/test_x.py": skip, ".github/workflows/ci.yml": "on: push\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS, out


def test_d31_a_suppression_added_in_the_resolution_is_guarded(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    merge_base_in(
        repo,
        {"src/app.py": "x = 5\n"},
        resolve={"src/app.py": "x = 7  # noqa: E501\n"},
    )
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert out["rule"] == "suppression_marker"


def test_d31_dropping_the_prs_own_test_in_the_merge_is_guarded(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    pr_tests = TESTS_PY + "\n\ndef test_pr_new():\n    assert True\n"
    repo.start = repo.commit("pr adds a test", {"tests/test_x.py": pr_tests})
    tip = merge_base_in(repo, {"src/other.py": "o = 1\n"})
    git(repo.wt, "checkout", "-q", "--detach", tip)
    repo.commit("take main's tests", {"tests/test_x.py": TESTS_PY})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert out["rule"] == "test_removed"


def test_d31_two_merges_are_guarded(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    first = merge_base_in(repo, {"src/one.py": "a = 1\n"})
    git(repo.wt, "checkout", "-q", "--detach", repo.base)
    moved = repo.commit("main moves again", {"src/two.py": "b = 1\n"})
    git(repo.wt, "checkout", "-q", "--detach", first)
    git(repo.wt, "merge", "-q", "--no-ff", "-m", "merge main again", moved)
    repo.base = moved
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert out["rule"] == "merge_commit"


def test_d31_a_merge_of_a_commit_not_on_base_is_guarded(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    merge_base_in(repo, {"src/other.py": "o = 1\n"})
    repo.base = git(repo.wt, "rev-parse", f"{repo.base}^")  # the base did not move after all
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert out["rule"] == "merge_commit"


def test_d31_no_gate_builds_the_merge_too(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, None)
    merge_base_in(repo, {"src/other.py": "o = 1\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == NO_GATE
    assert out["commit_sha"] != repo.start
    assert "src/other.py" not in out["diff"]
    assert repo.base in out["diff"]


# --------------------------------------------------------------------------- d31, Codex #1-#2


def test_d31_a_conflicted_protected_file_left_with_markers_is_guarded(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    ci = ".github/workflows/ci.yml"
    repo.start = repo.commit("pr edits ci", {ci: "on: pull_request\n"})
    merge_base_in(repo, {ci: "on: push\n"}, resolve={ci: None})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    rules = {v["rule"] for v in out["violations"]}
    assert {"protected_path", "conflict_unresolved"} <= rules


def test_d31_conflict_markers_left_in_a_file_are_guarded(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    merge_base_in(repo, {"src/app.py": "x = 5\n"}, resolve={"src/app.py": None})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert out["rule"] == "conflict_unresolved"


def test_d31_a_conflicted_test_resolved_to_one_definition_passes(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    ours = TESTS_PY + "\n\ndef test_same():\n    assert 1\n"
    repo.start = repo.commit("pr adds test_same", {"tests/test_x.py": ours})
    theirs = TESTS_PY + "\n\ndef test_same():\n    assert 2\n"
    resolved = TESTS_PY + "\n\ndef test_same():\n    assert 1 and 2\n"
    merge_base_in(repo, {"tests/test_x.py": theirs}, resolve={"tests/test_x.py": resolved})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS, out["violations"]
    assert "# conflicted tests/test_x.py" in out["diff"]


def test_d31_dropping_one_sides_test_in_a_conflict_is_guarded(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    ours = TESTS_PY + "\n\ndef test_ours():\n    assert 1\n"
    repo.start = repo.commit("pr adds test_ours", {"tests/test_x.py": ours})
    theirs = TESTS_PY + "\n\ndef test_theirs():\n    assert 2\n"
    merge_base_in(repo, {"tests/test_x.py": theirs}, resolve={"tests/test_x.py": theirs})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert ("test_removed", "tests/test_x.py") in {
        (v["rule"], v["path"]) for v in out["violations"]
    }


def test_d31_a_binary_conflict_is_guarded(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    (repo.wt / "logo.bin").write_bytes(b"\x00ours\x01")
    git(repo.wt, "add", "logo.bin")
    git(repo.wt, "commit", "-q", "-m", "pr logo")
    repo.start = git(repo.wt, "rev-parse", "HEAD")
    git(repo.wt, "checkout", "-q", "--detach", repo.base)
    (repo.wt / "logo.bin").write_bytes(b"\x00theirs\x02")
    git(repo.wt, "add", "logo.bin")
    git(repo.wt, "commit", "-q", "-m", "main logo")
    moved = git(repo.wt, "rev-parse", "HEAD")
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    subprocess.run(
        ["git", "merge", "-q", "--no-ff", "-m", "merge main", moved],
        cwd=repo.wt,
        env=GIT_ENV,
        capture_output=True,
    )
    git(repo.wt, "checkout", "-q", "--ours", "logo.bin")
    git(repo.wt, "add", "logo.bin")
    git(repo.wt, "commit", "-q", "--no-edit")
    repo.base = moved
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert out["rule"] == "conflict_not_text"


# --------------------------------------------------------------------------- d31, Codex round 2


def test_d31_moving_a_protected_file_while_resolving_a_conflict_is_guarded(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    ci = ".github/workflows/ci.yml"
    tip = merge_base_in(
        repo, {"src/app.py": "x = 5\n", ci: "on: push\n"}, resolve={"src/app.py": "x = 7\n"}
    )
    git(repo.wt, "checkout", "-q", "--detach", tip)
    git(repo.wt, "mv", ci, "src/ci.yml")
    git(repo.wt, "commit", "-q", "-m", "move ci")
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert ("protected_path", ci) in {(v["rule"], v["path"]) for v in out["violations"]}


def test_d31_each_sides_own_markers_kept_in_a_conflict_pass(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    repo.start = repo.commit("pr", {"src/app.py": "x = 2  # noqa: E501\n"})
    theirs = "x = 5  # nosec\n"
    both = "x = 2  # noqa: E501\ny = 5  # nosec\n"
    merge_base_in(repo, {"src/app.py": theirs}, resolve={"src/app.py": both})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS, out["violations"]


def test_d31_a_real_replacement_character_is_complete_review_material(
    store, tmp_path, clock  # noqa: F811
):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    merge_base_in(repo, {"src/app.py": "x = 5\n"}, resolve={"src/app.py": "x = 7  # \ufffd\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS
    assert out["diff_truncated"] is False, out["diff_problems"]
