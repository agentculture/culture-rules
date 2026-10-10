"""d26 end to end: the shipped PR fixer chain keeps ONE status comment per chain, live
(since #35 a re-fix or retry dispatched from the queue is a chain of its own).

ChainWorld runs the real chain on two nodes with the real ``github.comment`` port on a fake
App (IssuesApp): the node on spark (where the App actor lives) posts the status comment once
the first pr-fix run is past its quiet period and GitGuardian hold, edits it as the stages
move, and the chain-end action (``status: true``) writes its final section into it. No
other comment is posted by the chain.
"""

from __future__ import annotations

from culture_rules.apps.github import GitHubError
from culture_rules.node.fixer_status import STATUS_COLLECTION
from tests.rules.chain_world import ChainWorld, plain
from tests.rules.test_pr_fixer_chain import FINDING, changes
from tests.rules.test_pr_fixer_single import verdict_text

WORKING = "**PR fixer is working on this PR.**"
GHP = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def record(w: ChainWorld) -> dict:
    (doc,) = w.c.base.find(STATUS_COLLECTION)
    return doc


def every_body(w: ChainWorld) -> list[str]:
    return [p[2] for p in w.issues.posts] + [e[2] for e in w.issues.edits]


def test_a_fixed_pr_has_one_status_comment_edited_through_every_stage(tmp_path):
    w = ChainWorld(tmp_path)
    w.fire()
    (fix,) = w.run_of("pr-fix")
    (publish,) = w.run_of("publish-fix")
    ((repo, number, first),) = w.issues.posts  # one comment for the whole chain
    assert (repo, number) == ("o/r", 7)
    assert first.startswith(WORKING)
    assert "- **done** Quiet period and GitGuardian hold" in first
    assert w.issues.edits  # edited live
    assert all(e[1] == 1 for e in w.issues.edits)  # always the same comment
    (body,) = w.comments()
    assert body.startswith("PR fixer pushed the fix: gate pass, reviewed and approved")
    assert f"Run: https://rules.culture.dev/api/runs/{publish['id']}" in body
    assert "- **verdict pass** Test gate" in body
    assert "- **approve (0 findings)** Review (codex-reviewer)" in body
    assert "- **pushed `" in body
    assert "**Fix summary**\n\nmade x 3" in body  # the agent's summary reaches the end
    doc = record(w)
    assert doc["id"] == fix["id"]  # keyed by the chain's root
    assert doc["final"] is True


def test_a_hand_back_lands_in_the_status_comment(tmp_path):
    w = ChainWorld(tmp_path, turns=["none"])
    w.fire()
    assert len(w.issues.posts) == 1
    (body,) = w.comments()
    assert body.startswith("PR fixer handed back (loop_body_failed): fix[0]/agent: no_changes")
    assert "- **failed** Agent (qwen-fixer)" in body
    assert record(w)["final"] is True


def test_a_refix_through_the_queue_gets_a_status_comment_of_its_own(tmp_path):
    """#35 d30: a re-fix goes back through the fixer queue, so its pr-fix run is started by
    the queue's dispatch event and is a chain root of its own: the status board (unchanged
    by #35) gives it its own comment, and the first try's comment ends where its chain
    ended. One comment per story is left to the generic status boards (open question)."""
    w = ChainWorld(tmp_path, reviews=[changes, verdict_text])
    w.fire()
    first_fix, second_fix = w.run_of("pr-fix")
    assert second_fix["trigger"]["data"]["retry"] is True
    assert len(w.issues.posts) == 2
    first, second = w.comments()
    assert "- **request_changes (1 finding)** Review (codex-reviewer)" in first
    assert second.startswith("PR fixer pushed the fix")
    roots = sorted(d["id"] for d in w.c.base.find(STATUS_COLLECTION))
    assert roots == sorted([first_fix["id"], second_fix["id"]])


def test_a_spent_budget_hands_back_the_findings_in_the_last_status_comment(tmp_path):
    w = ChainWorld(tmp_path, reviews=[changes, changes, changes])
    w.fire()
    assert len(w.issues.posts) == 3  # one per dispatched try (#35)
    *_, body = w.comments()
    assert body.startswith("PR fixer handed back (actor_failed): changes_requested")
    assert FINDING["detail"] in body


def test_review_only_mode_writes_the_verdict_into_the_status_comment(tmp_path):
    w = ChainWorld(tmp_path, disabled=("pr-fixer-publish",))
    w.fire()
    assert len(w.issues.posts) == 1
    (body,) = w.comments()
    assert body.startswith("PR fixer review: approve for ")
    assert "review-only mode" in body
    assert "- **waiting** Push" in body


def test_a_deleted_status_comment_is_posted_again(tmp_path):
    w = ChainWorld(tmp_path)

    def deleted():  # someone deletes the status comment while Codex reviews
        w.issues.deleted.add(1)

    w.reviewer.on_request = deleted
    w.fire()
    assert len(w.issues.posts) == 2
    assert record(w)["comment_id"] == 2
    assert plain(w.issues.bodies[2]).startswith("PR fixer pushed the fix")


def test_the_agents_notes_are_relayed_cleaned(tmp_path):
    w = ChainWorld(tmp_path)
    w.qwen.progress = [
        f'tool_call: Shell: echo "STATUS: pushing with {GHP}"',  # refused, never stored
        'tool_call: Shell: echo "STATUS: reading the failing test, cc @mallory"',
        "tool_call: Shell: pytest -q",
    ]
    w.fire()
    bodies = every_body(w)
    assert any("reading the failing test, cc @​mallory" in b for b in bodies)
    assert not any("@mallory" in b for b in bodies)
    assert not any(GHP in b for b in bodies)
    assert not any("pytest -q" in b for b in bodies)  # only STATUS: notes are relayed


def test_a_run_stopped_by_the_gitguardian_hold_posts_only_its_hand_back(tmp_path):
    from tests.rules.test_pr_fixer_secrets import gg_run

    w = ChainWorld(tmp_path)
    w.checks.runs = [gg_run()]
    w.fire()
    (post,) = w.issues.posts
    assert plain(post[2]).startswith("PR fixer handed back (actor_failed): secrets_found")
    assert w.issues.edits == []


def test_an_accepted_post_whose_answer_was_lost_is_adopted_not_repeated(tmp_path):
    w = ChainWorld(tmp_path)
    w.issues.lose_next_post = True
    w.fire()
    assert len(w.issues.posts) == 1
    (body,) = w.comments()
    assert body.startswith("PR fixer pushed the fix")
    assert record(w)["outcome"] == "delivered"


def test_the_chain_end_never_fails_when_github_refuses_the_comment(tmp_path):
    w = ChainWorld(tmp_path)
    w.issues.fail_posts = GitHubError("http_403")
    w.fire()
    for run in (*w.run_of("pr-fix"), *w.run_of("review-commit"), *w.run_of("publish-fix")):
        assert run["status"] == "succeeded", (run["workflow_id"], run.get("error"))
    assert len(w.push.calls) == 1
    assert w.issues.posts == []
    assert record(w)["outcome"] == "gave_up"
