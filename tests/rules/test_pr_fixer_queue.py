"""#35 end to end (d29, d30, d32): the shipped fixer queue on the two-node chain world.

The trigger rules put a PR in the queue (``queue-add``); ``pr-fixer-queue-progress`` starts
the oldest request when no pr-fix run holds the pool's one slot (``pr-fixer-dispatch``
turns the queue's dispatch event into the run); a try whose gate does not pass goes back in
line at the back (``pr-fixer-retry``); a trusted ``/fix`` starts a new story with a fresh
attempt budget (``resets_attempt_budget``).
"""

from __future__ import annotations

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.engine.runs import step_state
from culture_rules.node.actions.queue import QUEUES_COLLECTION
from tests.events.fakes import envelope
from tests.rules.chain_world import KEY, ChainWorld
from tests.rules.test_pr_fixer_single import TRUSTED, pr_facts

KEY8 = KEY.replace("#7", "#8")


def guard(repo):
    """An agent turn the diff guard rejects: it edits CI."""
    return repo.commit("edit ci", {".github/workflows/ci.yml": "on: push\n"})


def settle(w: ChainWorld, n: int, number: int = 7) -> None:
    facts = pr_facts(
        number=number,
        head_sha=w.repo.start,
        base_sha=w.repo.base,
        conclusion="failure",
        state="open",
    )
    w.c.publish(envelope(n, type="github.pr.checks_settled", data=facts))


def comment(w: ChainWorld, n: int, *, author=TRUSTED, command="/fix", number=7) -> None:
    facts = pr_facts(
        number=number,
        head_sha=w.repo.start,
        base_sha=w.repo.base,
        comment=f"{command or 'thanks'} please",
        command=command,
        pr_enriched=True,
        state="open",
        author=author,
    )
    w.c.publish(envelope(n, type="github.comment.created", data=facts))


def fixes(w: ChainWorld) -> list[dict]:
    return w.run_of("pr-fix")


def pr(run: dict) -> int:
    return run["trigger"]["data"]["number"]


def budget(w: ChainWorld, key: str = KEY) -> dict:
    return w.c.base.get(RULE_ATTEMPT_BUDGETS, budget_id(key)) or {}


def assert_one_at_a_time(runs: list[dict]) -> None:
    """With the pool's cap of 1 no two pr-fix runs ever overlap."""
    for earlier, later in zip(runs, runs[1:]):
        assert later["created_at"] >= earlier["finished_at"], (earlier["id"], later["id"])


def test_two_prs_are_fixed_one_after_the_other_in_arrival_order(tmp_path):
    w = ChainWorld(tmp_path)
    settle(w, 1, number=7)
    settle(w, 2, number=8)
    w.run_chain()
    runs = fixes(w)
    assert [pr(r) for r in runs] == [7, 8]
    assert all(r["rule_id"] == "pr-fixer-dispatch" for r in runs)
    assert_one_at_a_time(runs)
    assert len(w.run_of("publish-fix")) == 2
    # the second PR saw its place in line when it was queued
    second = next(r for r in w.run_of("queue-add") if r["inputs"]["number"] == 8)
    assert second["outputs"]["position"] in (1, 2)
    queue = w.c.base.get(QUEUES_COLLECTION, "pr-fixer")
    assert queue["waiting"] == []
    assert queue["active"] == []
    # both nodes moved the queue: one queue document, one order
    hosts = {step_state(r, "progress")["host"] for r in w.run_of("queue-progress")}
    assert hosts <= {"spark", "spark2"}


def test_a_try_whose_gate_does_not_pass_goes_back_behind_the_waiting_pr(tmp_path):
    w = ChainWorld(tmp_path, turns=[guard, "commit", "commit"])
    settle(w, 1, number=7)
    settle(w, 2, number=8)
    w.run_chain()
    runs = fixes(w)
    assert [pr(r) for r in runs] == [7, 8, 7]  # A, then B, then A's retry
    assert_one_at_a_time(runs)
    first, _, retry = runs
    assert step_state(first, "fix")["status"] == "succeeded"  # one try, verdict recorded
    assert first["outputs"]["verdict"] == "guard"
    assert retry["trigger"]["data"]["retry"] is True
    assert retry["inputs"]["instruction"].startswith("The diff guard rejected commit")
    assert retry["inputs"]["task"] == first["inputs"]["instruction"]  # the story's task
    (requeue,) = w.runs("pr-fixer-retry")
    assert requeue["status"] == "succeeded"
    assert budget(w)["count"] == 2  # two tries of A's story
    assert len(w.run_of("publish-fix")) == 2


def test_a_prs_fourth_try_never_runs_and_the_third_hands_back_once(tmp_path):
    w = ChainWorld(tmp_path, turns=[guard, guard, guard, guard])
    settle(w, 1)
    w.run_chain()
    assert len(fixes(w)) == 3
    assert budget(w)["count"] == 3
    *_, last = w.runs("pr-fixer-retry")
    assert last["status"] == "failed"
    assert last["error"]["message"].startswith("attempt_budget_exhausted: o/r#7 used its 3")
    assert "The diff guard rejected commit" in last["error"]["message"]
    bodies = w.comments()
    handed = [b for b in bodies if b.startswith("PR fixer handed back")]
    assert len(handed) == 1
    assert "attempt_budget_exhausted" in handed[0]
    assert w.c.base.get(QUEUES_COLLECTION, "pr-fixer")["waiting"] == []


def test_a_trusted_fix_after_three_failed_tries_starts_a_new_story(tmp_path):
    w = ChainWorld(tmp_path, turns=[guard, guard, guard, "commit"])
    settle(w, 1)
    w.run_chain()
    assert len(fixes(w)) == 3
    comment(w, 10)
    w.run_chain()
    runs = fixes(w)
    assert len(runs) == 4
    assert runs[-1]["status"] == "succeeded"
    assert budget(w)["count"] == 1  # a fresh budget for the new request
    assert len(w.run_of("publish-fix")) == 1


def test_an_untrusted_or_plain_comment_does_not_reset_the_budget(tmp_path):
    w = ChainWorld(tmp_path, turns=[guard, guard, guard, "commit"])
    settle(w, 1)
    w.run_chain()
    comment(w, 10, author="mallory")
    comment(w, 11, command=None)
    w.run_chain()
    assert len(fixes(w)) == 3
    assert budget(w)["count"] == 3


def test_a_fix_while_a_retry_is_queued_replaces_it_in_place_with_a_fresh_budget(tmp_path):
    w = ChainWorld(tmp_path, turns=[guard, "commit", "commit"])
    sent = {"done": False}

    def fix_on_a_while_b_works():
        # B's agent is working: A's retry waits in line; a trusted /fix lands on A
        if len(w.qwen.inputs) == 1 and not sent["done"]:
            sent["done"] = True
            queue = w.c.base.get(QUEUES_COLLECTION, "pr-fixer")
            (waiting,) = queue["waiting"]
            assert (waiting["key"], waiting["retry"]) == ("o/r#7", True)
            sent["rid"] = waiting["rid"]
            comment(w, 20)

    w.qwen.on_request = fix_on_a_while_b_works
    settle(w, 1, number=7)
    settle(w, 2, number=8)
    w.run_chain()
    assert sent["done"]
    runs = fixes(w)
    assert [pr(r) for r in runs] == [7, 8, 7]
    last = runs[-1]
    assert last["trigger"]["data"]["retry"] is False  # the /fix replaced the retry
    assert last["trigger"]["data"]["request_id"] == sent["rid"]  # in its place
    assert "/fix please" in last["inputs"]["instruction"]
    assert budget(w)["count"] == 1  # the /fix reset A's budget: a new story


def test_a_request_whose_head_moved_before_its_turn_is_dropped(tmp_path):
    w = ChainWorld(tmp_path)
    w.moved_head = "f" * 40  # the PR head moved after the settle
    settle(w, 1)
    w.run_chain(10)
    assert fixes(w) == []
    dropped = [d for r in w.run_of("queue-progress") for d in r["outputs"].get("dropped") or ()]
    assert dropped == [{"key": "o/r#7", "reason": "head_moved"}]
