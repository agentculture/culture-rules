"""d20: the shipped pr-fixer workflow reviews every gated fix before github.push.

End to end on two nodes (spark: the App and the reviewer; spark2: the fixer, gate and
push) with the real gate, the real built-in ``review`` step and a codex-bridge double. The
review must approve exactly the gated commit, from another actor and backend, read-only;
anything else pushes nothing and hands back. Fail closed throughout.
"""

from __future__ import annotations

import copy

import pytest

from culture_rules.actors.review import REVIEWS_COLLECTION, review_refusal
from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.runs import FAILURE_STEP, step_state
from tests.actors.test_gate import PushSpy, git
from tests.rules.test_pr_fixer_bundle import (
    World,
    assert_handed_back,
    reviewer_actor,
    verdict_text,
    workflow_doc,
)

FINDING = {
    "path": "src/app.py",
    "line": 1,
    "severity": "high",
    "detail": "x must come from the config, not a literal",
}


def changes(commit: str) -> str:
    return verdict_text(commit, "request_changes", [FINDING])


def record(w: World, doc: dict) -> dict:
    return w.c.base.get(REVIEWS_COLLECTION, doc["id"])


def refusal(w: World, doc: dict, commit: str):
    """github.push's check for this run's PR, from its PR head, to ``commit``."""
    return review_refusal(w.c.base, doc["id"], commit, repo="o/r", number=7, start_sha=w.repo.start)


def pushed_commit(doc: dict, i: int = 0) -> str:
    return step_state(doc, f"fix[{i}]/agent")["outputs"]["head_after"]


# --------------------------------------------------------------------------- approve


def test_an_approved_fix_is_pushed_after_an_independent_read_only_review(tmp_path):
    w = World(tmp_path)
    doc = w.fire()
    assert doc["status"] == "succeeded", (doc.get("error"), [s["key"] for s in doc["steps"]])
    commit = pushed_commit(doc)
    (given,) = w.reviewer.inputs
    gate = step_state(doc, "fix[0]/gate")["outputs"]
    # the reviewer sees the PR head checkout plus the gate's verified diff of the fix
    assert given["head_sha"] == w.repo.start and given["commit_sha"] == commit
    assert given["diff"] == gate["diff"] and "+x = 3" in given["diff"]
    assert given["diff_truncated"] is False and given["gate_verdict"] == "pass"
    assert "o/r#7" in given["pr_intent"]
    assert [t["thread_id"] for t in given["threads"]] == ["PRRT_1"]  # trusted only
    assert "instruction" not in given  # its brief is the step config's, never the fixer's
    assert step_state(doc, "fix[0]/review")["host"] == "spark"  # where codex-reviewer lives
    out = step_state(doc, "fix[0]/verdict")["outputs"]
    assert out["review"] == "approve" and out["verdict"] == "pass"
    assert out["reviewed_commit"] == commit and out["instruction"] is None
    rec = record(w, doc)
    assert rec["verdict"] == "approve" and rec["commit_sha"] == commit
    assert (rec["reviewer_actor"], rec["reviewer_backend"]) == ("codex-reviewer", "codex")
    assert (rec["implementer_actor"], rec["implementer_backend"]) == ("qwen-fixer", "qwen")
    assert refusal(w, doc, commit) is None
    (push_call,) = w.push.calls
    assert push_call[1]["commit_sha"] == commit
    (comment,) = w.comment.calls
    assert "review approve" in comment[1]["body"]


def test_the_real_push_port_gets_past_the_review_check_for_an_approved_commit(tmp_path):
    w = World(tmp_path, push=PushSpy)
    doc = w.fire()
    # the spy's git refuses everything, so the push fails - but only after the review check
    assert doc["error"]["step"] == "push"
    assert not doc["error"]["message"].startswith(("review_", "reviewer_"))


# --------------------------------------------------------------------------- request changes


def test_requested_changes_become_the_next_attempts_instruction(tmp_path):
    w = World(tmp_path, reviews=[changes])
    doc = w.fire()
    assert doc["status"] == "succeeded", doc.get("error")
    first, second = (c[1]["instruction"] for c in w.agent.calls)
    assert "o/r#7" in first
    assert "An independent reviewer requested changes" in second
    assert FINDING["detail"] in second and "[high] src/app.py:1" in second
    assert "The original task:" in second and "o/r#7" in second
    assert len(w.reviewer.inputs) == 2
    assert step_state(doc, "fix[0]/verdict")["outputs"]["review"] == "request_changes"
    (push_call,) = w.push.calls  # only the approved attempt is pushed
    assert push_call[1]["commit_sha"] == pushed_commit(doc, 1)


def test_three_requests_for_changes_hand_back_with_the_findings_and_push_nothing(tmp_path):
    w = World(tmp_path, reviews=[changes, changes, changes])
    doc = w.fire()
    assert doc["status"] == "failed"
    error = step_state(doc, "fix")["error"]
    assert error["code"] == "loop_max_exceeded"
    assert FINDING["detail"] in error["message"]
    assert w.push.calls == [] and w.reply.calls == []
    assert_handed_back(w, doc, "fix", FINDING["detail"])
    assert record(w, doc)["verdict"] == "request_changes"
    assert refusal(w, doc, pushed_commit(doc, 2)) == "review_rejected"


# --------------------------------------------------------------------------- fail closed


def _other(commit: str) -> str:
    return verdict_text("f" * 40)


@pytest.mark.parametrize(
    "review, code",
    [
        ("LGTM, ship it", "review_invalid"),
        (lambda c: "Approve.\n" + verdict_text(c), "review_invalid"),
        (lambda c: verdict_text(c)[:-3], "review_invalid"),
        (lambda c: verdict_text(c, "approved"), "review_invalid"),
        (_other, "review_commit_mismatch"),
        ({"summary": None}, "review_invalid"),
        ({"status": "completed", "commits": [{"sha": "f" * 40}]}, "reviewer_not_read_only"),
        ({"dirty": True}, "reviewer_not_read_only"),
        ({"head_after": "f" * 40}, "reviewer_not_read_only"),
        ({"status": "permission_blocked"}, "review_invalid"),
        ({"backend": "qwen"}, "review_invalid"),  # reported backend contradicts the actor
    ],
)
def test_anything_but_a_clear_approval_of_this_commit_pushes_nothing(tmp_path, review, code):
    w = World(tmp_path, reviews=[review])
    doc = w.fire()
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "fix" and doc["error"]["code"] == "loop_body_failed"
    assert f"fix[0]/verdict: {code}" in doc["error"]["message"]
    assert w.push.calls == [] and w.reply.calls == []
    assert len(w.agent.calls) == 1  # a broken review hands back; it is not retried
    assert_handed_back(w, doc, "fix", code)
    assert record(w, doc)["verdict"] == code
    assert refusal(w, doc, pushed_commit(doc)) == "review_rejected"


def test_a_reviewer_bridge_failure_pushes_nothing(tmp_path):
    w = World(tmp_path, reviews=[{"fail": "timeout: codex took too long"}])
    doc = w.fire()
    assert doc["status"] == "failed"
    assert "fix[0]/review" in doc["error"]["message"]
    assert w.push.calls == []
    assert_handed_back(w, doc, "fix", "codex took too long")
    assert refusal(w, doc, pushed_commit(doc)) == "review_missing"


def test_a_failing_gate_never_runs_the_reviewer(tmp_path, monkeypatch):
    w = World(tmp_path)
    calls: list[str] = []

    def guarded(input, key, deadline, *, context):  # deletes a test: the guard refuses
        calls.append(input["instruction"])
        git(w.repo.wt, "reset", "-q", "--hard", w.repo.start)
        head = w.repo.commit("drop test", {"tests/test_x.py": None})
        return InvocationResult.completed(
            {
                "head_before": w.repo.start,
                "head_after": head,
                "worktree": str(w.repo.wt),
                "threads_addressed": [],
                "backend": "qwen",
            }
        )

    monkeypatch.setattr(w.agent, "invoke", guarded)
    doc = w.fire()
    assert doc["status"] == "failed"
    assert w.reviewer.inputs == []
    for i in range(3):
        assert step_state(doc, f"fix[{i}]/review")["status"] == "skipped"
        assert step_state(doc, f"fix[{i}]/verdict")["outputs"]["review"] == "not_run"
    assert len(calls) == 3
    assert "diff guard rejected" in calls[1]  # the gate's own text goes on, as before
    assert w.push.calls == []
    assert_handed_back(w, doc, "fix", "diff guard rejected")
    assert record(w, doc)["verdict"] == "not_run"


def test_a_diff_too_large_to_review_asks_for_a_smaller_fix_and_never_runs_codex(tmp_path):
    wf = workflow_doc()
    fix = next(s for s in wf["steps"] if s["id"] == "fix")
    gate = next(b for b in fix["body"] if b["id"] == "gate")
    gate["config"]["diff_max_chars"] = 10
    w = World(tmp_path, workflow=wf)
    doc = w.fire()
    assert doc["status"] == "failed"
    assert w.reviewer.inputs == [] and w.push.calls == []
    assert "over the 10 the reviewer reads" in w.agent.calls[1][1]["instruction"]
    assert_handed_back(w, doc, "fix", "smaller, text-only fix")


def _without_review(wf: dict) -> dict:
    """The workflow as someone might edit it: review steps gone, loop ends on the gate."""
    wf = copy.deepcopy(wf)
    fix = next(s for s in wf["steps"] if s["id"] == "fix")
    fix["body"] = [b for b in fix["body"] if b["id"] in ("agent", "gate")]
    fix["config"]["until"] = fix["config"]["until"]["args"][0]
    fix["outputs"] = [p for p in fix["outputs"] if p["name"] != "review"]
    wf["edges"] = [e for e in wf["edges"] if e["target"] not in ("review", "verdict")]
    wf["outputs"] = [o for o in wf["outputs"] if o["name"] != "review"]
    return wf


def test_a_workflow_edited_to_skip_the_review_still_pushes_nothing(tmp_path):
    w = World(tmp_path, push=PushSpy, workflow=_without_review(workflow_doc()))
    doc = w.fire()
    assert step_state(doc, "fix[0]/gate")["outputs"]["verdict"] == "pass"
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "push" and doc["error"]["message"] == "review_missing"
    assert w.push.git_calls == [] and w.push.http_calls == []  # refused before git or network
    assert w.reviewer.inputs == []
    assert_handed_back(w, doc, "push", "review_missing")


def test_a_reviewer_on_the_implementers_backend_is_refused(tmp_path):
    w = World(tmp_path, reviews=[{"backend": "qwen"}])
    w.c.base.put("actors", {**reviewer_actor(), "harness": "qwen"})
    doc = w.fire()
    assert "fix[0]/verdict: reviewer_is_implementer" in doc["error"]["message"]
    assert w.push.calls == []
    assert step_state(doc, FAILURE_STEP) is not None


def test_the_implementer_reviewing_itself_is_refused(tmp_path):
    wf = workflow_doc()
    fix = next(s for s in wf["steps"] if s["id"] == "fix")
    review = next(b for b in fix["body"] if b["id"] == "review")
    review["placement"]["actor"] = "qwen-fixer"
    w = World(tmp_path, workflow=wf)
    w.c.base.put(
        "actors",
        {**w.c.base.get("actors", "qwen-fixer"), "params": {"sandbox": "read-only"}},
    )
    doc = w.fire()
    assert doc["status"] == "failed" and w.push.calls == []
    assert "reviewer_is_implementer" in doc["error"]["message"]


def test_a_reviewer_actor_that_is_not_read_only_is_refused(tmp_path):
    w = World(tmp_path)
    actor = reviewer_actor()
    actor["params"] = {**actor["params"], "sandbox": "workspace-write"}
    w.c.base.put("actors", actor)
    doc = w.fire()
    assert "fix[0]/verdict: reviewer_not_read_only" in doc["error"]["message"]
    assert w.push.calls == []


# --------------------------------------------------------------------------- #4: built commit


def test_push_takes_the_gate_built_commit_and_its_start_never_the_agents_outputs():
    wf = workflow_doc()
    into_push = {
        e["target_port"]: (e["source"], e["source_port"])
        for e in wf["edges"]
        if e["target"] == "push"
    }
    assert into_push["commit_sha"] == ("gate", "commit_sha")
    assert into_push["expected_head_sha"] == ("gate", "start_sha")
    assert into_push["source"] == ("gate", "bundle")


def test_an_agent_that_commits_then_removes_a_secret_pushes_one_clean_commit(tmp_path, monkeypatch):
    w = World(tmp_path)

    def sneaky(input, key, deadline, *, context):
        git(w.repo.wt, "reset", "-q", "--hard", w.repo.start)
        w.repo.commit("add", {"leak.txt": "TOKEN=planted\n"})
        head = w.repo.commit("fix x and drop leak", {"leak.txt": None, "src/app.py": "x = 3\n"})
        return InvocationResult.completed(
            {
                "head_before": w.repo.start,
                "head_after": head,
                "worktree": str(w.repo.wt),
                "threads_addressed": [],
                "backend": "qwen",
                "summary": "made x 3",
            }
        )

    monkeypatch.setattr(w.agent, "invoke", sneaky)
    doc = w.fire()
    assert doc["status"] == "succeeded", doc.get("error")
    gate = step_state(doc, "fix[0]/gate")["outputs"]
    agent_tip = step_state(doc, "fix[0]/agent")["outputs"]["head_after"]
    assert gate["agent_commit_sha"] == agent_tip != gate["commit_sha"]
    (push_call,) = w.push.calls
    assert push_call[1]["commit_sha"] == gate["commit_sha"]
    assert w.reviewer.inputs[0]["commit_sha"] == gate["commit_sha"]
    assert "leak.txt" not in w.reviewer.inputs[0]["diff"]


# --------------------------------------------------------------------------- #5: non-text


def test_a_binary_change_is_never_reviewed_or_pushed(tmp_path, monkeypatch):
    w = World(tmp_path)
    calls: list[str] = []

    def binary(input, key, deadline, *, context):
        calls.append(input["instruction"])
        git(w.repo.wt, "reset", "-q", "--hard", w.repo.start)
        (w.repo.wt / "src/app.py").write_text("x = 3\n")
        (w.repo.wt / "src/payload.bin").write_bytes(b"\x00\x7fELF\x00" * 20)
        git(w.repo.wt, "add", "-A")
        git(w.repo.wt, "commit", "-q", "-m", "fix")
        head = git(w.repo.wt, "rev-parse", "HEAD")
        return InvocationResult.completed(
            {
                "head_before": w.repo.start,
                "head_after": head,
                "worktree": str(w.repo.wt),
                "threads_addressed": [],
                "backend": "qwen",
            }
        )

    monkeypatch.setattr(w.agent, "invoke", binary)
    doc = w.fire()
    assert doc["status"] == "failed"
    assert w.reviewer.inputs == [] and w.push.calls == []
    assert len(calls) == 3
    assert "src/payload.bin: binary change" in calls[1]
    assert "text-only" in calls[1]
    out = step_state(doc, "fix[0]/verdict")["outputs"]
    assert out["review"] == "request_changes"
    assert_handed_back(w, doc, "fix", "binary change")


# --------------------------------------------------------------------------- #3: range


def test_the_record_binds_repo_pr_start_and_tip(tmp_path):
    w = World(tmp_path)
    doc = w.fire()
    rec = record(w, doc)
    assert (rec["repo"], rec["number"]) == ("o/r", 7)
    assert rec["start_sha"] == w.repo.start
    assert rec["commit_sha"] == step_state(doc, "fix[0]/gate")["outputs"]["commit_sha"]


def test_a_gate_start_that_is_not_the_pr_head_is_never_approved(tmp_path, monkeypatch):
    # the agent reports it started from an older commit: the reviewed range would not be
    # the PR's, so nothing is approved (the start must be the trigger's PR head)
    w = World(tmp_path)

    def elsewhere(input, key, deadline, *, context):
        git(w.repo.wt, "reset", "-q", "--hard", w.repo.base)
        head = w.repo.commit("fix", {"src/app.py": "x = 3\n"})
        return InvocationResult.completed(
            {
                "head_before": w.repo.base,
                "head_after": head,
                "worktree": str(w.repo.wt),
                "threads_addressed": [],
                "backend": "qwen",
            }
        )

    monkeypatch.setattr(w.agent, "invoke", elsewhere)
    doc = w.fire()
    assert doc["status"] == "failed" and w.push.calls == []
    assert "fix[0]/verdict: review_invalid" in doc["error"]["message"]
    assert "PR head" in doc["error"]["message"]
