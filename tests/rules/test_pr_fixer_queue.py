"""#35 end to end (d29, d30, d32): the shipped fixer queue on the two-node chain world.

The trigger rules put a PR in the queue (``queue-add``); ``pr-fixer-queue-progress`` starts
the oldest request when no pr-fix run holds the pool's one slot (``pr-fixer-dispatch``
turns the queue's dispatch event into the run); a try whose gate does not pass goes back in
line at the back (``pr-fixer-retry``); a trusted ``/fix`` starts a new story with a fresh
attempt budget (``resets_attempt_budget``).
"""

from __future__ import annotations

from culture_rules.actors.merge_hint import MERGE_PARAGRAPH_HEAD
from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.engine.runs import step_state
from culture_rules.node.actions.queue import QUEUES_COLLECTION
from tests.events.fakes import envelope
from tests.rules.chain_world import KEY, ChainWorld
from tests.rules.test_pr_fixer_chain import _untrust_app, pem, trust_app  # noqa: F401
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


def test_d36_every_try_is_told_the_one_merge_from_base(tmp_path):
    """d36 (katvan#57): whatever started the story - checks, a /fix, a retry - the agent is
    told, once, that it may make one real merge of the run's base commit. The paragraph is
    appended at the bridge call; no try's instruction or task stores it."""
    w = ChainWorld(tmp_path, turns=[guard, "commit", "commit"])
    settle(w, 1, number=7)
    comment(w, 2, number=8)
    w.run_chain()
    runs = fixes(w)
    assert len(runs) == 3  # the checks story, the /fix story and one retry
    merge = f"git merge {w.repo.base}"
    for run in runs:
        assert run["inputs"]["base_sha"] == w.repo.base
        assert MERGE_PARAGRAPH_HEAD not in run["inputs"]["instruction"]  # never stored
        assert MERGE_PARAGRAPH_HEAD not in (run["inputs"].get("task") or "")
    assert len(w.qwen.inputs) == 3
    for sent in w.qwen.inputs:
        assert sent["instruction"].count(merge) == 1
        assert sent["instruction"].count(MERGE_PARAGRAPH_HEAD) == 1
        assert "Never squash, flatten" in sent["instruction"]
    texts = [s["instruction"] for s in w.qwen.inputs]
    assert [t for t in texts if t.startswith("Checks on PR")]
    assert [t for t in texts if "/fix please" in t]
    assert [t for t in texts if t.startswith("The diff guard rejected commit")]


def test_d36_the_dispatch_rule_passes_the_instruction_through():
    """/code-review #2: the dispatch rule hands on the request's instruction as it is (a
    plain reference), so a request without one still lets the agent fall back to its task."""
    import json
    from pathlib import Path

    rule = Path(__file__).resolve().parents[2] / "docs/rules/pr-fixer/rules/pr-fixer-dispatch.json"
    inputs = json.loads(rule.read_text())["workflow"]["inputs"]
    assert inputs["instruction"] == "trigger.data.instruction"
    assert inputs["task"] == "trigger.data.task"


def handed_back(w: ChainWorld) -> list[str]:
    return [b for b in w.comments() if b.startswith("PR fixer handed back")]


def test_d37_an_agent_timeout_goes_back_to_the_queue_and_the_next_try_pushes(tmp_path):
    """katvan#57 try 2: the agent ran out of time and the story ended at once. Now the try
    goes back in line with its own instruction and a note, within the story's budget."""
    w = ChainWorld(tmp_path, turns=["timeout", "commit"])
    settle(w, 1)
    w.run_chain()
    first, second = fixes(w)
    assert first["status"] == "failed"
    assert first["outputs"]["instruction"] == first["inputs"]["instruction"]
    (requeue,) = w.runs("pr-fixer-retry-failed")
    assert requeue["status"] == "succeeded"
    assert second["trigger"]["data"]["retry"] is True
    assert second["inputs"]["instruction"].startswith(first["inputs"]["instruction"])
    assert "ran out of time" in second["inputs"]["instruction"]
    assert budget(w)["count"] == 2
    assert len(w.run_of("publish-fix")) == 1
    assert handed_back(w) == []  # the chain went on: the first try's hand-back skipped


def test_d37_three_timeouts_spend_the_budget_and_hand_back_once(tmp_path):
    w = ChainWorld(tmp_path, turns=["timeout", "timeout", "timeout", "commit"])
    settle(w, 1)
    w.run_chain()
    assert len(fixes(w)) == 3
    *_, last = w.runs("pr-fixer-retry-failed")
    assert last["status"] == "failed"
    assert last["error"]["message"].startswith("attempt_budget_exhausted")
    (handed,) = handed_back(w)
    assert "attempt_budget_exhausted" in handed


def test_d37_any_other_failure_of_a_try_still_hands_back(tmp_path):
    """A try whose agent made no commit is not retried: it hands back as before."""
    w = ChainWorld(tmp_path, turns=["none", "commit"])
    settle(w, 1)
    w.run_chain()
    assert len(fixes(w)) == 1
    assert w.runs("pr-fixer-retry-failed") == []
    (handed,) = handed_back(w)
    assert "no_changes" in handed


def test_d37_an_unjudged_gate_verdict_is_retried_like_any_other():
    from culture_rules.model.condition import evaluate
    from tests.rules.chain_world import rule_docs

    cond = rule_docs()["pr-fixer-retry"]["condition"]
    data = {"workflow_id": "pr-fix", "outputs": {"verdict": "unjudged"}, "repository": "o/r"}
    variables = {"fixer_repos": ["o/r"], "fixer_excluded_repos": []}
    assert evaluate(cond, {"trigger": {"data": data}, "variables": variables}) is True


def test_d37_the_timeout_retry_matches_both_kinds_of_agent_timeout_and_nothing_else():
    from culture_rules.model.condition import evaluate
    from tests.rules.chain_world import rule_docs

    cond = rule_docs()["pr-fixer-retry-failed"]["condition"]
    variables = {"fixer_repos": ["o/r"], "fixer_excluded_repos": []}

    def fires(message):
        data = {
            "workflow_id": "pr-fix",
            "error_message": message,
            "outputs": {"instruction": "fix it"},
            "repository": "o/r",
        }
        return evaluate(cond, {"trigger": {"data": data}, "variables": variables})

    assert fires("fix[0]/agent: timeout: qwen did not finish within 3600s")
    assert fires("fix[0]/agent: the step's deadline passed")
    assert not fires("fix[0]/agent: no_changes: the agent made no commit")
    assert not fires("fix[0]/gate: merge_commit: the merge's second parent is not on base")


def moved_base_world(tmp_path, **kw):
    """A world whose base branch moved past the PR's base.sha with a conflicting change,
    and an agent that merges the moved tip for real and resolves it (katvan#57)."""
    import subprocess

    from tests.actors.test_gate import GIT_ENV, git

    w = ChainWorld(tmp_path, turns=[], **kw)
    repo = w.repo
    git(repo.wt, "checkout", "-q", "--detach", repo.base)
    tip = repo.commit("main moves", {"src/app.py": "x = 5\n", "NOTES.md": "moved\n"})
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    w.base_tip = tip

    def merge_tip(repo):
        subprocess.run(  # a conflict on src/app.py: the agent resolves it
            ["git", "merge", "-q", "--no-ff", "-m", "merge main", tip],
            cwd=repo.wt,
            env=GIT_ENV,
            capture_output=True,
        )
        (repo.wt / "src/app.py").write_text("x = 9\n")
        git(repo.wt, "add", "src/app.py", "NOTES.md")
        git(repo.wt, "commit", "-q", "--no-edit")
        return git(repo.wt, "rev-parse", "HEAD")

    w.qwen.script = [merge_tip]
    return w, tip


def test_d37_katvan57_the_real_push_publishes_a_fix_judged_on_the_dispatched_tip(
    tmp_path, pem  # noqa: F811
):
    """Codex round 1 #1: the push's own base check accepts the dispatched tip too."""
    w, tip = moved_base_world(tmp_path, real_push_pem=pem)
    trust_app(w)
    settle(w, 1)
    w.run_chain()
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == "succeeded", publish.get("error")
    assert w.remote_head() != w.repo.start  # the built merge reached the PR branch
    assert any("/compare/" in path for _, path in w.github.calls)
    assert handed_back(w) == []


def test_d37_a_base_that_moved_again_before_the_push_still_publishes(tmp_path, pem):  # noqa: F811
    """The branch moved again after dispatch: the dispatched tip still lies on it."""
    from tests.actors.test_gate import git

    w, tip = moved_base_world(tmp_path, real_push_pem=pem)
    trust_app(w)
    real = w.qwen.script[0]

    def merge_then_main_moves(repo):
        head = real(repo)
        git(repo.wt, "checkout", "-q", "--detach", tip)
        w.base_tip = repo.commit("main moves again", {"OTHER.md": "again\n"})
        git(repo.wt, "checkout", "-q", "--detach", head)
        return head

    w.qwen.script = [merge_then_main_moves]
    settle(w, 1)
    w.run_chain()
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == "succeeded", publish.get("error")


def test_d37_katvan57_a_merge_of_the_live_base_tip_is_pushed(tmp_path):
    """katvan#57 end to end: GitHub's base.sha is the PR's fork point, the base branch
    moved on with a conflicting change. The dispatch gives the try the live tip, the bridge
    paragraph names it, the agent merges it for real, the gate passes it and it is pushed."""
    import subprocess

    from tests.actors.test_gate import GIT_ENV, git

    w = ChainWorld(tmp_path, turns=[])
    repo = w.repo
    git(repo.wt, "checkout", "-q", "--detach", repo.base)
    tip = repo.commit("main moves", {"src/app.py": "x = 5\n", "NOTES.md": "moved\n"})
    git(repo.wt, "checkout", "-q", "--detach", repo.start)
    w.base_tip = tip

    def merge_tip(repo):
        subprocess.run(  # a conflict on src/app.py: the agent resolves it
            ["git", "merge", "-q", "--no-ff", "-m", "merge main", tip],
            cwd=repo.wt,
            env=GIT_ENV,
            capture_output=True,
        )
        (repo.wt / "src/app.py").write_text("x = 9\n")
        git(repo.wt, "add", "src/app.py", "NOTES.md")
        git(repo.wt, "commit", "-q", "--no-edit")
        return git(repo.wt, "rev-parse", "HEAD")

    w.qwen.script = [merge_tip]
    settle(w, 1)
    w.run_chain()
    (run,) = fixes(w)
    assert run["inputs"]["base_sha"] == tip  # the live tip, not base.sha (the fork point)
    assert f"git merge {tip}" in w.qwen.inputs[0]["instruction"]
    assert run["outputs"]["verdict"] == "pass", run["outputs"].get("gate_output")
    assert len(w.run_of("publish-fix")) == 1
    assert handed_back(w) == []
