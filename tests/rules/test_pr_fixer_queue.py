"""#35 end to end (d29, d30, d32): the shipped fixer queue on the two-node chain world.

The trigger rules put a PR in the queue (``queue-add``); ``pr-fixer-queue-progress`` starts
the oldest request when no pr-fix run holds the pool's one slot (``pr-fixer-dispatch``
turns the queue's dispatch event into the run); a try whose gate does not pass goes back in
line at the back (``pr-fixer-retry``); a trusted ``/fix`` starts a new story with a fresh
attempt budget (``resets_attempt_budget``).
"""

from __future__ import annotations

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
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
    # #39: with fixer_levels each drop names the level queue it left
    assert dropped == [{"key": "o/r#7", "reason": "head_moved", "queue": "pr-fixer"}]


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

    # #39, #56: the agent step of any level (agent_qwen, agent_codex, agent_claude), or of a
    # try pinned to the d30 pr-fix (agent) during the rollout; never the hand-off's
    for agent in ("agent_qwen", "agent_codex", "agent_claude", "agent"):
        assert fires(f"fix[0]/{agent}: timeout: qwen did not finish within 3600s")
        assert fires(f"fix[0]/{agent}: the step's deadline passed")
        assert not fires(f"fix[0]/{agent}: no_changes: the agent made no commit")
    assert not fires("fix[0]/agent_claude: capacity_exhausted: usage limit reached")
    assert not fires("fix[0]/agent_other: timeout: no such level")
    assert not fires("fix[0]/handoff_export: timeout: the export ran out of time")
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
