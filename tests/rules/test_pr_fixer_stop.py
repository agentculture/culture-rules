"""#40 (d34) end to end: a trusted ``/stop`` or 👎 stops a PR's fixer story.

On the two-node chain world with the shipped bundle: ``pr-fixer-stop`` (a ``/stop`` or
``@rules-culture-dev stop`` comment) and ``pr-fixer-stop-reaction`` (a 👎 the reaction watch
read on the story's ``/fix`` comment or status comment) run the non-agentic ``queue-stop``
workflow. It cancels the PR's active fixer run (no push, no hand-back), removes its queued
request and retries, and ends the story's one status comment with "stopped by @user"; the
next ``/fix`` or a new head starts a new story.
"""

from __future__ import annotations

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.actions.queue import QUEUES_COLLECTION
from tests.events.fakes import envelope
from tests.rules.chain_world import KEY, ChainWorld, plain
from tests.rules.test_pr_fixer_queue import guard, settle
from tests.rules.test_pr_fixer_single import TRUSTED, pr_facts

FIX_COMMENT = 900


def say(
    w: ChainWorld, n: int, body: str, *, author=TRUSTED, number=7, comment_id=None, **intent
) -> None:
    """A PR comment on o/r#<number> as the receiver stores it (intent facts given)."""
    facts = pr_facts(
        number=number,
        head_sha=w.repo.start,
        base_sha=w.repo.base,
        comment=body,
        pr_enriched=True,
        state="open",
        author=author,
        comment_id=comment_id or 800 + n,
        **intent,
    )
    w.c.publish(envelope(n, type="github.comment.created", data=facts))


def stop(w: ChainWorld, n: int, **over) -> None:
    say(w, n, "/stop", command="/stop", **over)


def fix(w: ChainWorld, n: int, **over) -> None:
    say(w, n, "/fix please", command="/fix", **over)


def until_the_agent_works(w: ChainWorld, inputs: int = 1) -> None:
    for _ in range(40):
        w.cycle(1)
        if len(w.qwen.inputs) >= inputs:
            return
        w.c.clock.advance(301)
    raise AssertionError("the agent was never asked")


def fresh(w: ChainWorld, run: dict) -> dict:
    return w.c.base.get(RUNS_COLLECTION, run["id"])


def handed_back(w: ChainWorld) -> list[str]:
    return [b for b in w.comments() if b.startswith("PR fixer handed back")]


def status_bodies(w: ChainWorld) -> list[str]:
    return [b for b in w.comments() if "PR fixer status" in b or "stopped by" in b]


class Reactions:
    """GitHub's reactions endpoint as a double: 👎s by comment id."""

    def __init__(self) -> None:
        self.by_comment: dict[int, list[dict]] = {}
        self.reads: list[int] = []

    def add(self, comment_id: int, login: str, content: str = "-1") -> None:
        items = self.by_comment.setdefault(comment_id, [])
        rid = 7000 + 10 * comment_id + len(items)
        items.append({"id": rid, "content": content, "user": {"login": login}})

    def __call__(self, repo, comment_id, timeout):
        self.reads.append(comment_id)
        return list(self.by_comment.get(comment_id, []))


def test_a_trusted_stop_during_the_agent_step_cancels_the_run_within_one_tick(tmp_path):
    w = ChainWorld(tmp_path, turns=["hang"])
    settle(w, 1)
    until_the_agent_works(w)
    (run,) = w.run_of("pr-fix")
    assert fresh(w, run)["status"] == "running"
    stop(w, 50)
    w.cycle(1)  # one engine tick
    assert fresh(w, run)["status"] == "cancelled"
    w.run_chain()
    assert w.push.calls == []  # nothing pushed
    assert w.run_of("review-commit") == [] and w.run_of("publish-fix") == []
    assert handed_back(w) == []  # no hand-back comment
    assert w.qwen.cancelled  # the bridge was asked to stop the agent's job
    (stopper,) = w.runs("pr-fixer-stop")
    assert stopper["status"] == "succeeded"
    assert stopper["outputs"]["cancelled"] == 1
    # the story's one status comment ends "stopped by @user"
    (body,) = status_bodies(w)
    assert f"PR fixer stopped by @{TRUSTED}" in body
    queue = w.c.base.get(QUEUES_COLLECTION, "pr-fixer")
    assert queue["waiting"] == [] and queue["active"] == []


def test_the_apps_mention_with_stop_stops_and_never_starts_a_fix(tmp_path):
    w = ChainWorld(tmp_path, turns=["hang"])
    settle(w, 1)
    until_the_agent_works(w)
    (run,) = w.run_of("pr-fix")
    say(
        w,
        50,
        "@rules-culture-dev stop",
        mention="@rules-culture-dev",
        mention_command="@rules-culture-dev stop",
    )
    w.cycle(1)
    assert fresh(w, run)["status"] == "cancelled"
    assert w.runs("pr-fixer-comment") == []  # the mention did not ask for a fix
    w.run_chain()
    assert len(w.run_of("pr-fix")) == 1


def test_a_trusted_thumbs_down_on_the_fix_comment_stops_the_story(tmp_path):
    w = ChainWorld(tmp_path, turns=["hang"])
    reactions = Reactions()
    w.watch_reactions(reactions)
    fix(w, 1, comment_id=FIX_COMMENT)
    until_the_agent_works(w)
    (run,) = w.run_of("pr-fix")
    reactions.add(FIX_COMMENT, TRUSTED)
    w.c.clock.advance(60)  # the next sweep, at most a minute later
    w.cycle(1)  # the watch reads it, the stop fires and cancels in the same cycle
    assert fresh(w, run)["status"] == "cancelled"
    (stopper,) = w.runs("pr-fixer-stop-reaction")
    assert stopper["status"] in ("running", "succeeded")
    w.run_chain()
    assert w.push.calls == [] and handed_back(w) == []
    (body,) = status_bodies(w)
    assert f"PR fixer stopped by @{TRUSTED}" in body
    # the story ended: no more reactions are read
    reads = len(reactions.reads)
    w.run_chain(5)
    assert len(reactions.reads) == reads


def test_a_thumbs_down_on_the_status_comment_stops_the_story(tmp_path):
    w = ChainWorld(tmp_path, turns=["hang"])
    reactions = Reactions()
    w.watch_reactions(reactions)
    settle(w, 1)
    until_the_agent_works(w)
    w.cycle(2)  # the status comment is posted
    (status_id,) = w.issues.bodies
    reactions.add(status_id, TRUSTED)
    w.c.clock.advance(60)
    w.cycle(1)
    (run,) = w.run_of("pr-fix")
    assert fresh(w, run)["status"] == "cancelled"


def test_a_thumbs_down_by_anyone_else_does_nothing(tmp_path):
    w = ChainWorld(tmp_path, turns=["hang"])
    reactions = Reactions()
    w.watch_reactions(reactions)
    fix(w, 1, comment_id=FIX_COMMENT)
    until_the_agent_works(w)
    reactions.add(FIX_COMMENT, "mallory")
    reactions.add(FIX_COMMENT, TRUSTED, content="+1")
    w.c.clock.advance(60)
    w.cycle(3)
    assert FIX_COMMENT in reactions.reads
    (run,) = w.run_of("pr-fix")
    assert fresh(w, run)["status"] == "running"
    assert w.runs("pr-fixer-stop-reaction") == []


def test_an_untrusted_stop_comment_does_nothing(tmp_path):
    w = ChainWorld(tmp_path, turns=["hang"])
    settle(w, 1)
    until_the_agent_works(w)
    stop(w, 50, author="mallory")
    w.cycle(2)
    (run,) = w.run_of("pr-fix")
    assert fresh(w, run)["status"] == "running"
    assert w.runs("pr-fixer-stop") == []


def test_a_stop_removes_the_prs_queued_request_and_its_retry(tmp_path):
    w = ChainWorld(tmp_path, turns=[guard, "hang"])
    settle(w, 1, number=7)
    settle(w, 2, number=8)
    until_the_agent_works(w, inputs=2)  # A's try did not pass; B's agent is working
    queue = w.c.base.get(QUEUES_COLLECTION, "pr-fixer")
    assert [(r["key"], r["retry"]) for r in queue["waiting"]] == [("o/r#7", True)]
    stop(w, 50, number=7)
    w.cycle(1)
    queue = w.c.base.get(QUEUES_COLLECTION, "pr-fixer")
    assert queue["waiting"] == []
    assert [a["key"] for a in queue["active"]] == ["o/r#8"]  # B goes on
    (b_run,) = [r for r in w.run_of("pr-fix") if r["trigger"]["data"]["number"] == 8]
    assert fresh(w, b_run)["status"] == "running"
    w.run_chain(5)
    assert [r["trigger"]["data"]["number"] for r in w.run_of("pr-fix")] == [7, 8]
    on_a = [plain(w.issues.bodies[i]) for i, post in enumerate(w.issues.posts, 1) if post[1] == 7]
    assert not any("handed back" in b for b in on_a)
    assert any(f"PR fixer stopped by @{TRUSTED}" in b for b in on_a)  # A's story ended


def test_a_stop_removes_a_request_still_waiting_its_turn(tmp_path):
    w = ChainWorld(tmp_path, turns=["hang"])
    settle(w, 1, number=8)
    until_the_agent_works(w)
    settle(w, 2, number=7)
    w.cycle(2)
    queue = w.c.base.get(QUEUES_COLLECTION, "pr-fixer")
    assert [r["key"] for r in queue["waiting"]] == ["o/r#7"]
    stop(w, 50, number=7)
    w.cycle(1)
    assert w.c.base.get(QUEUES_COLLECTION, "pr-fixer")["waiting"] == []


def test_after_a_stop_a_new_fix_starts_a_fresh_story_and_checks_on_the_same_head_do_not(
    tmp_path,
):
    w = ChainWorld(tmp_path, turns=["hang", "commit"])
    settle(w, 1)
    until_the_agent_works(w)
    stop(w, 50)
    w.run_chain()
    settle(w, 60)  # the same head settles red again: the stopped story stays stopped
    w.run_chain()
    assert len(w.run_of("pr-fix")) == 1
    fix(w, 70)  # a trusted /fix: a new story with a fresh budget
    w.run_chain()
    runs = w.run_of("pr-fix")
    assert len(runs) == 2
    assert runs[-1]["status"] == "succeeded"
    assert len(w.run_of("publish-fix")) == 1
    assert len(w.push.calls) == 1
    budget = w.c.base.get(RULE_ATTEMPT_BUDGETS, budget_id(KEY))
    assert budget["count"] == 1
