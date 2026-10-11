"""#39: the fixer's escalation levels, resolved by the queue at dispatch.

``fixer_levels`` lists the levels; each has its own queue on its own pool. ``queue.add``
routes a request to the queue of the level its try is due at; ``queue.progress`` resolves
the try's level again at dispatch (skipping an unavailable one as ``fixer_unavailable``
says), moves a request whose level is another queue's, hands a story back once when no
level can take its try, and writes the level into the dispatch event. Without
``fixer_levels`` the queue is exactly as before (tests/node/test_queue.py).
"""

from __future__ import annotations

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.actions import queue as queue_mod
from culture_rules.node.actions.fixer_levels import FIXER_COOLDOWNS, NO_FIXER
from culture_rules.node.actions.queue import QUEUES_COLLECTION, QueueStopPort
from tests.node.test_queue import CONFIG, DEADLINE, PROGRESS, World, budget, ctx, request

LEVELS_CONFIG = {**CONFIG, "levels": "fixer_levels", "unavailable": "fixer_unavailable"}
LEVELS_PROGRESS = {**PROGRESS, "levels": "fixer_levels", "unavailable": "fixer_unavailable"}


def level(number, fixer, reviewer, pool, queue, enabled=True):
    """One ``fixer_levels`` entry: six key=value fields in a string (variables hold scalars)."""
    on = "true" if enabled else "false"
    return (
        f"level={number} actor={fixer} reviewer={reviewer} pool={pool} queue={queue} enabled={on}"
    )


QWEN = level(1, "qwen-fixer", "codex-reviewer", "qwen-spark2", "pr-fixer")
CODEX = level(2, "codex-fixer", "qwen-reviewer", "codex-spark", "pr-fixer-codex")
CLAUDE = level(3, "claude-fixer", "codex-reviewer", "claude-spark", "pr-fixer-claude")
ACTORS = (
    ("qwen-fixer", "qwen-spark2"),
    ("codex-fixer", "codex-spark"),
    ("claude-fixer", "claude-spark"),
)


def actor(actor_id, pool, **extra):
    return {
        "id": actor_id,
        "name": actor_id,
        "kind": "agent",
        "params": {"concurrency_pool": pool, "max_concurrency": 1, **extra.pop("params", {})},
        **extra,
    }


def world(levels=(QWEN, CODEX), unavailable="next", **kw):
    w = World(**kw)
    w.store.put_variable("fixer_levels", list(levels), updated_by="t")
    w.store.put_variable("fixer_unavailable", unavailable, updated_by="t")
    for actor_id, pool in ACTORS:
        w.store.put("actors", actor(actor_id, pool))
    return w


def add(w, **req):
    return w.add(config=LEVELS_CONFIG, **req)


def progress(w, config=LEVELS_PROGRESS):
    return w.progress(config)


def doc(w, name):
    return w.store.get(QUEUES_COLLECTION, name) or {"waiting": [], "active": []}


def waiting(w, name):
    return [r["key"] for r in doc(w, name)["waiting"]]


def finish(w, name, key, status="succeeded"):
    act = next(a for a in doc(w, name)["active"] if a["key"] == key)
    w.store.put(RUNS_COLLECTION, {"id": act["run_id"], "status": status})


def on(w, queue):
    """The dispatch events of one level queue (request ids count per queue)."""
    return [e for e in w.dispatches() if e["data"]["queue"] == queue]


def level_of(event):
    data = event["data"]
    return {k: data.get(k) for k in ("queue", "level", "fixer", "reviewer", "attempt")}


# ---- backward compatibility ----------------------------------------------------------------


def test_without_fixer_levels_the_queue_is_as_before():
    w = World()  # config names the variables, none is defined
    assert add(w, **request("o/a", 1)).output["position"] == 1
    assert progress(w).output["dispatched"] == ["o/a#1"]
    (event,) = w.dispatches()
    assert event["data"]["queue"] == "pr-fixer"
    assert event["data"]["attempt"] == 1
    for absent in ("level", "fixer", "reviewer", "levels_var"):
        assert absent not in event["data"]


def test_a_request_cannot_name_its_own_level():
    w = world()
    add(w, **request("o/a", 1, fixer="codex-fixer", level=2, reviewer="me", attempt=9))
    progress(w)
    (event,) = w.dispatches()
    assert level_of(event) == {
        "queue": "pr-fixer",
        "level": 1,
        "fixer": "qwen-fixer",
        "reviewer": "codex-reviewer",
        "attempt": 1,
    }


# ---- the level of a try ----------------------------------------------------------------------


def test_try_1_is_dispatched_to_level_1_with_its_fixer_and_reviewer():
    w = world()
    res = add(w, **request("o/a", 1))
    assert res.output["queue"] == "pr-fixer"
    out = progress(w).output
    assert out["dispatched"] == ["o/a#1"]
    (event,) = w.dispatches()
    assert level_of(event) == {
        "queue": "pr-fixer",
        "level": 1,
        "fixer": "qwen-fixer",
        "reviewer": "codex-reviewer",
        "attempt": 1,
    }
    assert event["data"]["levels_var"] == "fixer_levels"


def test_a_retry_goes_to_the_next_levels_queue_and_is_dispatched_there():
    w = world()
    budget(w, 1)
    res = add(w, **request("o/a", 1, instruction="the gate failed", retry=True))
    assert res.output["queue"] == "pr-fixer-codex"
    assert waiting(w, "pr-fixer-codex") == ["o/a#1"]
    assert waiting(w, "pr-fixer") == []
    assert progress(w).output["dispatched"] == ["o/a#1"]
    (event,) = w.dispatches()
    assert level_of(event) == {
        "queue": "pr-fixer-codex",
        "level": 2,
        "fixer": "codex-fixer",
        "reviewer": "qwen-reviewer",
        "attempt": 2,
    }


def test_the_try_is_counted_at_dispatch_not_at_add():
    w = world()
    add(w, **request("o/a", 1))  # added as try 1
    budget(w, 1)  # meanwhile a try was counted
    out = progress(w).output
    assert out["moved"] == [{"key": "o/a#1", "from": "pr-fixer", "to": "pr-fixer-codex"}]
    (event,) = w.dispatches()
    assert event["data"]["queue"] == "pr-fixer-codex"
    assert event["data"]["attempt"] == 2


def test_a_disabled_level_is_not_on_the_ladder_the_last_level_repeats():
    w = world(levels=(QWEN, CODEX.replace("enabled=true", "enabled=false")))
    budget(w, 1)
    assert add(w, **request("o/a", 1, retry=True)).output["queue"] == "pr-fixer"
    progress(w)
    (event,) = w.dispatches()
    assert (event["data"]["fixer"], event["data"]["attempt"]) == ("qwen-fixer", 2)


def test_try_3_past_the_ladder_takes_the_last_enabled_level():
    w = world()
    budget(w, 2)
    assert add(w, **request("o/a", 1, retry=True)).output["queue"] == "pr-fixer-codex"
    progress(w)
    (event,) = w.dispatches()
    assert (event["data"]["level"], event["data"]["attempt"]) == (2, 3)


def test_try_3_takes_level_3_when_it_is_enabled():
    w = world(levels=(QWEN, CODEX, CLAUDE))
    budget(w, 2)
    add(w, **request("o/a", 1, retry=True))
    progress(w)
    (event,) = w.dispatches()
    assert (event["data"]["fixer"], event["data"]["queue"]) == ("claude-fixer", "pr-fixer-claude")


def test_a_story_never_gets_a_4th_try():
    w = world()
    budget(w, 3, limit=None)  # the key does not know its limit: 3 tries is still the end
    add(w, **request("o/a", 1, retry=True))
    out = progress(w).output
    assert out["dispatched"] == []
    assert out["dropped"] == [
        {"key": "o/a#1", "reason": "attempt_budget_exhausted", "queue": "pr-fixer-codex"}
    ]


# ---- an unavailable level -------------------------------------------------------------------


def unavailable_codex(w, how):
    if how == "disabled":
        w.store.put("actors", actor("codex-fixer", "codex-spark", enabled=False))
    elif how == "deleted":
        w.store.put("actors", actor("codex-fixer", "codex-spark", deleted_at="2026-10-01"))
    elif how == "unknown":
        w.store.delete("actors", "codex-fixer")
    elif how == "over_budget":
        w.store.put("actors", actor("codex-fixer", "codex-spark", params={"token_budget": 100}))
        day = w.clock().strftime("%Y-%m-%d")
        w.store.put("actor_usage", {"id": "codex-fixer", "day": day, "tokens": 100})
    else:  # a capacity cooldown (the contract t2 writes)
        until = w.clock().replace(hour=w.clock().hour + 1).isoformat()
        w.store.put(FIXER_COOLDOWNS, {"id": "codex-fixer", "until": until})


@pytest.mark.parametrize("how", ["disabled", "deleted", "unknown", "over_budget", "cooldown"])
def test_next_skips_an_unavailable_level_to_the_next_available_one(how):
    w = world(levels=(QWEN, CODEX, CLAUDE))
    unavailable_codex(w, how)
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    out = progress(w).output
    assert out["dispatched"] == ["o/a#1"]
    (event,) = w.dispatches()
    assert level_of(event) == {
        "queue": "pr-fixer-claude",
        "level": 3,
        "fixer": "claude-fixer",
        "reviewer": "codex-reviewer",
        "attempt": 2,  # skipping a level never adds a try
    }


def test_a_cooldown_that_ended_or_is_malformed_leaves_the_level_available():
    w = world()
    w.store.put(FIXER_COOLDOWNS, {"id": "codex-fixer", "until": w.clock().isoformat()})
    w.store.put(FIXER_COOLDOWNS, {"id": "qwen-fixer", "until": "soon"})
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    progress(w)
    (event,) = w.dispatches()
    assert event["data"]["fixer"] == "codex-fixer"


def test_spent_tokens_of_an_earlier_day_do_not_count():
    w = world()
    w.store.put("actors", actor("codex-fixer", "codex-spark", params={"token_budget": 100}))
    w.store.put("actor_usage", {"id": "codex-fixer", "day": "2000-01-01", "tokens": 500})
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    progress(w)
    (event,) = w.dispatches()
    assert event["data"]["fixer"] == "codex-fixer"


def ended_stories(monkeypatch):
    ended = []

    class Board:
        def __init__(self, store, *, clock=None):
            pass

        def finish(self, run, text, *, where=None):
            ended.append((run["id"], text, where))

    monkeypatch.setattr("culture_rules.node.status_board.StatusBoard", Board)
    return ended


def test_next_with_no_level_after_hands_the_story_back_once(monkeypatch):
    ended = ended_stories(monkeypatch)
    w = world()
    w.store.put(RUNS_COLLECTION, {"id": "r1", "status": "succeeded"})  # the queue-add run
    unavailable_codex(w, "cooldown")
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    out = progress(w).output
    assert out["dispatched"] == []
    assert out["dropped"] == [{"key": "o/a#1", "reason": NO_FIXER, "queue": "pr-fixer-codex"}]
    assert w.dispatches() == []
    ((run, text, where),) = ended
    assert (run, where) == ("r1", ("o/a", 1))
    assert text.startswith("PR fixer handed back (no_fixer_available)")
    assert "try 2 of 3" in text
    assert "codex-fixer): capacity_cooldown" in text
    assert progress(w).output["dropped"] == []  # once: the request is gone
    assert len(ended) == 1


def test_hand_back_ends_the_story_at_the_first_unavailable_level(monkeypatch):
    ended = ended_stories(monkeypatch)
    w = world(levels=(QWEN, CODEX, CLAUDE), unavailable="hand_back")
    w.store.put(RUNS_COLLECTION, {"id": "r1", "status": "succeeded"})
    unavailable_codex(w, "disabled")
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    out = progress(w).output
    assert [d["reason"] for d in out["dropped"]] == [NO_FIXER]
    assert w.dispatches() == []
    assert len(ended) == 1


def test_a_closed_pr_with_no_level_is_dropped_without_a_hand_back(monkeypatch):
    class Closed:
        def invoke(self, input, key, deadline, *, context):
            from culture_rules.engine.actorport import InvocationResult

            return InvocationResult.completed({"state": "closed", "head_sha": "a" * 40})

    ended = ended_stories(monkeypatch)
    w = world(levels=(QWEN,), lookup=Closed())
    w.store.put("actors", actor("qwen-fixer", "qwen-spark2", enabled=False))
    add(w, **request("o/a", 1))
    out = progress(w, {**LEVELS_PROGRESS, "lookup_actor": "github-app"}).output
    assert out["dropped"] == [{"key": "o/a#1", "reason": "pr_not_open", "queue": "pr-fixer"}]
    assert ended == []


def test_no_enabled_level_hands_back_instead_of_parking_the_request():
    w = world(levels=(QWEN.replace("enabled=true", "enabled=false"),))
    add(w, **request("o/a", 1))
    out = progress(w).output
    assert [d["reason"] for d in out["dropped"]] == [NO_FIXER]


def test_a_full_queue_still_lets_a_request_whose_level_is_gone_leave():
    w = world(levels=(QWEN, CODEX, CLAUDE))
    budget(w, 1, key="pr-fixer:o/b#2")
    add(w, **request("o/b", 2, retry=True))  # try 2 of o/b on Codex
    progress(w)
    assert doc(w, "pr-fixer-codex")["active"][0]["key"] == "o/b#2"
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))  # waits behind o/b on Codex's one slot
    assert progress(w).output["dispatched"] == []
    unavailable_codex(w, "cooldown")
    out = progress(w).output
    assert out["dispatched"] == ["o/a#1"]
    assert [e["data"]["fixer"] for e in on(w, "pr-fixer-claude")] == ["claude-fixer"]


# ---- one queue per level, each on its own pool -----------------------------------------------


def test_a_busy_qwen_queue_never_delays_a_codex_try():
    w = world()
    add(w, **request("o/b", 2))
    add(w, **request("o/c", 3))
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    out = progress(w).output
    assert out["dispatched"] == ["o/b#2", "o/a#1"]  # one per pool: Qwen's and Codex's
    assert [r["key"] for r in doc(w, "pr-fixer")["waiting"]] == ["o/c#3"]
    assert {w_["key"]: w_["queue"] for w_ in out["waiting"]} == {"o/c#3": "pr-fixer"}


def test_each_level_queue_is_first_come_first_served():
    w = world()
    for i, repo in ((1, "o/a"), (2, "o/b")):
        budget(w, 1, key=f"pr-fixer:{repo}#{i}")
        add(w, **request(repo, i, retry=True))
    assert progress(w).output["dispatched"] == ["o/a#1"]
    finish(w, "pr-fixer-codex", "o/a#1")
    assert progress(w).output["dispatched"] == ["o/b#2"]


def test_a_pr_dispatched_on_one_level_is_not_dispatched_on_another_meanwhile():
    w = world()
    add(w, **request("o/a", 1))
    progress(w)
    budget(w, 1)
    # a push arrives while try 1's dispatch is not yet claimed: due at level 2
    add(w, **request("o/a", 1, head="d" * 40))
    assert progress(w).output["dispatched"] == []
    assert waiting(w, "pr-fixer-codex") == ["o/a#1"]


def test_a_request_moved_back_to_an_earlier_level_is_dispatched_in_the_same_invocation():
    w = world()
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))  # waits on Codex
    w.store.put_variable(
        "fixer_levels", [QWEN, CODEX.replace("enabled=true", "enabled=false")], updated_by="t"
    )
    out = progress(w).output
    assert out["moved"] == [{"key": "o/a#1", "from": "pr-fixer-codex", "to": "pr-fixer"}]
    assert out["dispatched"] == ["o/a#1"]
    assert w.dispatches()[0]["data"]["fixer"] == "qwen-fixer"


# ---- one waiting request per PR across the level queues --------------------------------------


def test_a_new_fix_replaces_a_retry_waiting_on_another_level():
    w = world()
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    budget(w, 0)  # the /fix began a new story: a fresh budget
    res = add(w, **request("o/a", 1, head="d" * 40, instruction="/fix"))
    assert res.output["queue"] == "pr-fixer"
    assert res.output["replaced"] is True
    assert waiting(w, "pr-fixer") == ["o/a#1"]
    assert waiting(w, "pr-fixer-codex") == []


def test_a_retry_is_absorbed_by_a_newer_request_waiting_on_another_level():
    w = world()
    add(w, **request("o/a", 1, head="d" * 40, instruction="/fix"))
    budget(w, 1)
    res = add(w, **request("o/a", 1, instruction="old gate", retry=True))
    assert res.output["queued"] is True
    assert res.output["replaced"] is False
    assert res.output["queue"] == "pr-fixer"
    assert waiting(w, "pr-fixer-codex") == []
    (req,) = doc(w, "pr-fixer")["waiting"]
    assert req["inputs"]["instruction"] == "/fix"


# ---- malformed levels fail closed ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "qwen",
        [1],
        [QWEN.replace("actor=qwen-fixer", "actor=")],
        [QWEN.replace("enabled=true", "enabled=yes")],
        [QWEN.replace("level=1", "level=0")],
        [QWEN + " extra=1"],
        [QWEN + " actor=other"],
        [QWEN.replace(" pool=qwen-spark2", "")],
        [QWEN, CODEX.replace("level=2", "level=1")],
    ],
)
def test_malformed_levels_fail_the_queue_steps(value):
    w = world()
    w.store.put_variable("fixer_levels", value, updated_by="t")
    res = add(w, **request("o/a", 1))
    assert res.outcome == FAILED
    assert res.error.startswith("bad_levels")
    assert progress(w).outcome == FAILED


def test_a_malformed_unavailable_policy_fails_closed():
    w = world(unavailable="maybe")
    assert progress(w).error.startswith("bad_levels")


# ---- queue.stop covers every level's queue ---------------------------------------------------


def test_stop_unqueues_the_story_from_every_level_queue():
    w = world()
    w.store.put(
        RUNS_COLLECTION, {"id": "r1", "status": "succeeded", "created_at": w.clock().isoformat()}
    )
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    assert waiting(w, "pr-fixer-codex") == ["o/a#1"]
    port = QueueStopPort(w.store, clock=w.clock)
    res = port.invoke(
        {"repo": "o/a", "number": 1, "by": "OriNachum"},
        "ik-stop",
        DEADLINE,
        context=ctx(LEVELS_CONFIG, run="stop-run"),
    )
    assert res.outcome == COMPLETED
    assert res.output["removed"] == 1
    assert waiting(w, "pr-fixer-codex") == []


def test_stop_names_its_story_live_while_it_waits_on_another_level():
    w = world()
    w.store.put(RUNS_COLLECTION, {"id": "r1", "status": "succeeded"})
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    port = QueueStopPort(w.store, clock=w.clock)
    res = port.invoke(
        {"repo": "o/a", "number": 1, "by": "OriNachum", "story": "r1"},
        "ik-stop",
        DEADLINE,
        context=ctx(LEVELS_CONFIG, run="stop-run"),
    )
    assert res.output["stopped"] is True


def test_handback_text_is_exported():
    assert "{attempt}" in queue_mod.HANDBACK_TEXT


def test_a_try_that_ended_on_another_level_does_not_hold_the_pr():
    w = world()
    budget(w, 1)
    add(w, **request("o/a", 1, retry=True))
    progress(w)  # try 2 on Codex
    finish(w, "pr-fixer-codex", "o/a#1", status="failed")  # freed by Codex's own pass, later
    budget(w, 0)  # a /fix began a new story
    add(w, **request("o/a", 1, head="d" * 40, instruction="/fix"))
    assert progress(w).output["dispatched"] == ["o/a#1"]  # pr-fixer's pass runs first
    assert [e["data"]["fixer"] for e in on(w, "pr-fixer")] == ["qwen-fixer"]
