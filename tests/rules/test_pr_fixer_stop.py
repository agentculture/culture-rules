"""#40 (d34) end to end: a trusted ``/stop`` or 👎 stops a PR's fixer story.

On the two-node chain world with the shipped bundle: ``pr-fixer-stop`` (a ``/stop`` or
``@rules-culture-dev stop`` comment) and ``pr-fixer-stop-reaction`` (a 👎 the reaction watch
read on the story's ``/fix`` comment or status comment) run the non-agentic ``queue-stop``
workflow. It cancels the PR's active fixer run (no push, no hand-back), removes its queued
request and retries, and ends the story's one status comment with "stopped by @user"; the
next ``/fix`` or a new head starts a new story.
"""

from __future__ import annotations

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.actions.queue import QUEUES_COLLECTION
from tests.events.fakes import envelope
from tests.rules.chain_world import KEY, ChainWorld
from tests.rules.test_pr_fixer_chain import _untrust_app, pem, trust_app  # noqa: F401
from tests.rules.test_pr_fixer_queue import settle
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


def test_an_untrusted_stop_comment_does_nothing(tmp_path):
    w = ChainWorld(tmp_path, turns=["hang"])
    settle(w, 1)
    until_the_agent_works(w)
    stop(w, 50, author="mallory")
    w.cycle(2)
    (run,) = w.run_of("pr-fix")
    assert fresh(w, run)["status"] == "running"
    assert w.runs("pr-fixer-stop") == []


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


# --------------------------------------------------------------------------- the push guard


def record(w: ChainWorld) -> None:
    from culture_rules.node.story_stop import record_stop

    record_stop(w.c.base, KEY, by=TRUSTED, head_sha=None, at=w.c.clock())
