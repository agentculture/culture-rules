"""d20: the gate reports the diff it verified, for the reviewer.

The fix commit exists only in the fixer's local cache and the gate's bundle, never on
GitHub before the push, so the reviewer cannot fetch it. The gate already holds the three
commits in a node-owned, hash-verified repository; on ``pass`` and ``no_gate`` it outputs
``diff`` (``git diff start_sha commit_sha`` there, external diff drivers and textconv off),
``diff_chars`` (its full length) and ``diff_truncated``. A diff over ``config.diff_max_chars``
(default :data:`DEFAULT_DIFF_MAX_CHARS`) is cut and flagged, so nobody can approve a change
they were only partly shown. ``fail`` and ``guard`` verdicts carry no diff.
"""

from __future__ import annotations

from culture_rules.actors.gate import DEFAULT_DIFF_MAX_CHARS, FAIL, NO_GATE, PASS
from tests.actors.test_gate import (  # noqa: F401 - fixtures
    PASSING,
    PY,
    LocalRunner,
    Repo,
    clock,
    gate_yaml,
    git,
    judge,
    store,
)


def test_a_passing_gate_reports_the_verified_diff(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    head = repo.commit("agent fix", {"src/app.py": "x = 3\n", "src/new.py": "y = 1\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS
    expected = git(repo.wt, "diff", repo.start, head)
    assert out["diff"].strip() == expected
    assert "-x = 2" in out["diff"]
    assert "+x = 3" in out["diff"]
    assert "+y = 1" in out["diff"]
    assert out["diff_truncated"] is False
    assert out["diff_chars"] == len(out["diff"])
    assert out["agent_commit_sha"] == head
    assert out["start_sha"] == repo.start


def test_no_gate_reports_the_diff_too(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, None)
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == NO_GATE
    assert "+x = 3" in out["diff"]
    assert out["diff_truncated"] is False


def test_a_diff_over_the_cap_is_cut_and_flagged(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("big", {"src/big.py": "".join(f"line_{i} = {i}\n" for i in range(400))})
    out = judge(store, LocalRunner(), repo, tmp_path, clock, diff_max_chars=500)
    assert out["verdict"] == PASS
    assert out["diff_truncated"] is True
    assert out["diff_chars"] > 500
    assert len(out["diff"]) <= 500


def test_fail_and_guard_carry_no_diff(store, tmp_path, clock):  # noqa: F811
    failing = [PY, "-c", "import sys; sys.exit(1)"]
    repo = Repo(tmp_path, gate_yaml([failing]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == FAIL
    assert out["diff"] is None
    assert out["diff_truncated"] is None


def test_the_default_cap_leaves_room_in_the_bridges_60000_character_prompt_budget():
    assert 10000 <= DEFAULT_DIFF_MAX_CHARS <= 40000


def test_a_bad_cap_is_a_config_refusal(store, tmp_path, clock):  # noqa: F811
    from datetime import timedelta

    from tests.actors.test_gate import ctx, make_port
    from tests.engine.run_helpers import T0

    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    port_ = make_port(store, LocalRunner(), tmp_path, clock)
    for bad in (0, -1, "40000", True, 10**9):
        res = port_.invoke(
            repo.inputs(), "k", T0 + timedelta(hours=1), context=ctx(diff_max_chars=bad)
        )
        assert res.outcome == "failed", bad
        assert res.error.startswith("bad_config"), bad
