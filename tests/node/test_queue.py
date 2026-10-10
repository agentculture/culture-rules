"""#35 / d29-d30: the fixer queue's built-ins ``queue.add`` and ``queue.progress``.

The queue is one store document per queue (``queues/<name>``) changed only by
compare-and-set, so it is first come, first served across hosts. ``queue.add`` appends a
request (or refreshes the PR's queued one in place); ``queue.progress`` frees the slots of
dispatched runs that ended, drops requests whose PR closed or whose head moved, and
dispatches the oldest eligible request as a root ``rules.queue.dispatch`` event.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext, InvocationResult
from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.emit import reserved_reason
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.node.actions.queue import (
    DISPATCH_TYPE,
    QUEUE_SOURCE,
    QUEUES_COLLECTION,
    QueueAddPort,
    QueueProgressPort,
    dispatch_event_id,
)
from culture_rules.node.firing import run_id_for
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import T0, Clock

DEADLINE = T0.replace(hour=23)
CONFIG = {"queue": "pr-fixer", "key_prefix": "pr-fixer:"}
PROGRESS = {**CONFIG, "dispatch_rule": "pr-fixer-dispatch", "cap": 1}


def ctx(config, step="queue", run="r1"):
    return InvocationContext(run_id=run, step_id=step, kind="code", host="spark2", config=config)


def request(repo="o/a", number=1, head="a" * 40, **extra):
    return {
        "repo": repo,
        "number": number,
        "head_sha": head,
        "head_branch": "fix",
        "base_sha": "b" * 40,
        "clone_url": f"https://github.com/{repo}.git",
        "instruction": f"fix {repo}#{number}",
        **extra,
    }


class World:
    def __init__(self, store=None, clock=None, lookup=None):
        self.clock = clock or Clock()
        self.store = store or MemoryStore(clock=self.clock)
        self.add_port = QueueAddPort(self.store, clock=self.clock)
        self.progress_port = QueueProgressPort(self.store, clock=self.clock, pr_lookup=lookup)
        self.n = 0

    def add(self, config=CONFIG, **req):
        self.n += 1
        res = self.add_port.invoke(
            req, f"ik-add-{id(self)}-{self.n}", DEADLINE, context=ctx(config)
        )
        return res

    def progress(self, config=PROGRESS):
        self.n += 1
        return self.progress_port.invoke(
            {}, f"ik-progress-{id(self)}-{self.n}", DEADLINE, context=ctx(config)
        )

    def doc(self):
        return self.store.get(QUEUES_COLLECTION, "pr-fixer")

    def waiting(self):
        return [(r["key"], r["position"]) for r in self.doc()["waiting"]]

    def active(self):
        return [a["key"] for a in self.doc()["active"]]

    def dispatches(self):
        docs = self.store.find(EVENTS_COLLECTION)
        found = [d["envelope"] for d in docs if d["envelope"]["type"] == DISPATCH_TYPE]
        return sorted(found, key=lambda e: int(e["data"]["request_id"].split("-")[0]))

    def finish(self, key, status="succeeded"):
        act = next(a for a in self.doc()["active"] if a["key"] == key)
        self.store.put(RUNS_COLLECTION, {"id": act["run_id"], "status": status})


def test_add_appends_in_arrival_order_and_reports_the_place_in_line():
    w = World()
    first = w.add(**request("o/a", 1))
    assert first.outcome == COMPLETED
    assert first.output["position"] == 1
    assert first.output["ahead"] is None
    w.add(**request("o/b", 2))
    third = w.add(**request("o/c", 3))
    assert third.output["position"] == 3
    assert third.output["ahead"] == "o/b#2"
    assert w.waiting() == [("o/a#1", 1), ("o/b#2", 2), ("o/c#3", 3)]


def test_a_newer_request_for_a_queued_pr_replaces_it_and_keeps_its_place():
    w = World()
    w.add(**request("o/a", 1, head="a" * 40))
    w.add(**request("o/b", 2))
    again = w.add(**request("o/a", 1, head="c" * 40, instruction="newer"))
    assert again.output["position"] == 1
    assert again.output["replaced"] is True
    assert w.waiting() == [("o/a#1", 1), ("o/b#2", 2)]
    a, _ = w.doc()["waiting"]
    assert a["head_sha"] == "c" * 40
    assert a["inputs"]["instruction"] == "newer"


def test_a_repeated_add_with_the_same_idempotency_key_changes_nothing():
    w = World()
    req = request("o/a", 1)
    one = w.add_port.invoke(req, "same", DEADLINE, context=ctx(CONFIG))
    w.add(**request("o/b", 2))
    two = w.add_port.invoke(req, "same", DEADLINE, context=ctx(CONFIG))
    assert one.output["position"] == two.output["position"] == 1
    assert w.waiting() == [("o/a#1", 1), ("o/b#2", 2)]


def test_add_refuses_a_malformed_request():
    w = World()
    assert w.add(repo="o/a").outcome == FAILED  # no PR number
    res = w.add_port.invoke(request(), "k", DEADLINE, context=ctx({}))
    assert res.outcome == FAILED
    assert res.retryable is False


def test_progress_dispatches_the_oldest_request_as_a_root_engine_event():
    w = World()
    w.add(**request("o/a", 1))
    w.add(**request("o/b", 2))
    res = w.progress()
    assert res.outcome == COMPLETED
    assert res.output["dispatched"] == ["o/a#1"]
    assert res.output["waiting"] == [{"key": "o/b#2", "position": 1, "ahead": "o/a#1"}]
    (event,) = w.dispatches()
    assert event["source"] == QUEUE_SOURCE
    assert event["hops"] == 0
    assert "causationId" not in event
    assert event["data"]["repository"] == "o/a"
    assert event["data"]["number"] == 1
    assert event["data"]["instruction"] == "fix o/a#1"
    assert event["data"]["attempt"] == 1
    act = w.doc()["active"][0]
    assert act["event_id"] == event["id"]
    assert act["run_id"] == run_id_for("pr-fixer-dispatch", event["id"])
    assert w.waiting() == [("o/b#2", 1)]


def test_progress_dispatches_nothing_more_while_the_pool_is_full():
    w = World()
    w.add(**request("o/a", 1))
    w.add(**request("o/b", 2))
    w.progress()
    again = w.progress()
    assert again.output["dispatched"] == []
    assert len(w.dispatches()) == 1


def test_a_run_ending_frees_its_slot_and_the_next_request_is_dispatched():
    w = World()
    for i, repo in enumerate(("o/a", "o/b", "o/c"), start=1):
        w.add(**request(repo, i))
    w.progress()
    w.finish("o/a#1", "failed")
    assert w.progress().output["dispatched"] == ["o/b#2"]
    w.finish("o/b#2", "superseded")
    assert w.progress().output["dispatched"] == ["o/c#3"]
    assert [e["data"]["repository"] for e in w.dispatches()] == ["o/a", "o/b", "o/c"]


def test_three_requests_are_dispatched_in_arrival_order_even_if_a_later_one_re_asks_first():
    w = World()
    w.add(**request("o/a", 1))
    w.add(**request("o/b", 2))
    w.add(**request("o/c", 3))
    w.add(**request("o/c", 3, instruction="again"))  # C asks again first: keeps its place
    order = []
    for _ in range(3):
        order += w.progress().output["dispatched"]
        w.finish(order[-1])
    assert order == ["o/a#1", "o/b#2", "o/c#3"]


def test_fifo_holds_across_hosts_sharing_one_store():
    clock = Clock()
    base = MemoryStore(clock=clock)
    spark = World(base.peer(), clock)
    thor = World(base.peer(), clock)
    spark.add(**request("o/a", 1))
    thor.add(**request("o/b", 2))
    spark.add(**request("o/c", 3))
    assert thor.progress().output["dispatched"] == ["o/a#1"]
    assert spark.progress().output["dispatched"] == []
    spark.finish("o/a#1")
    assert spark.progress().output["dispatched"] == ["o/b#2"]


def test_concurrent_progress_on_many_hosts_dispatches_one_request_per_free_slot():
    clock = Clock()
    base = MemoryStore(clock=clock)
    seed = World(base.peer(), clock)
    for i in range(1, 6):
        seed.add(**request(f"o/r{i}", i))
    hosts = [World(base.peer(), clock) for _ in range(8)]
    with ThreadPoolExecutor(8) as pool:
        outs = list(pool.map(lambda h: h.progress().output["dispatched"], hosts))
    assert sum(len(o) for o in outs) == 1
    assert len(seed.dispatches()) == 1


def test_the_cap_admits_that_many_requests_at_once():
    w = World()
    for i in range(1, 4):
        w.add(**request(f"o/r{i}", i))
    res = w.progress({**PROGRESS, "cap": 2})
    assert res.output["dispatched"] == ["o/r1#1", "o/r2#2"]


def test_a_closed_pr_or_a_moved_head_is_dropped_at_dispatch():
    heads = {1: ("open", "a" * 40), 2: ("closed", "a" * 40), 3: ("open", "f" * 40)}

    class Lookup:
        calls: list = []

        def invoke(self, input, key, deadline, *, context):
            self.calls.append((input["repo"], input["number"], context.actor))
            state, head = heads[input["number"]]
            return InvocationResult.completed({"head_sha": head, "state": state})

    w = World(lookup=Lookup())
    w.add(**request("o/b", 2))
    w.add(**request("o/c", 3))
    w.add(**request("o/a", 1))
    res = w.progress({**PROGRESS, "lookup_actor": "github-app"})
    assert res.output["dispatched"] == ["o/a#1"]
    assert res.output["dropped"] == [
        {"key": "o/b#2", "reason": "pr_not_open"},
        {"key": "o/c#3", "reason": "head_moved"},
    ]
    assert w.doc()["waiting"] == []
    assert Lookup.calls[0] == ("o/b", 2, "github-app")


def test_a_failed_lookup_dispatches_anyway_the_run_guards_itself():
    class Down:
        def invoke(self, input, key, deadline, *, context):
            return InvocationResult.failed("http_502")

    w = World(lookup=Down())
    w.add(**request("o/a", 1))
    res = w.progress({**PROGRESS, "lookup_actor": "github-app"})
    assert res.output["dispatched"] == ["o/a#1"]


def test_a_request_whose_pr_key_is_busy_waits_and_the_next_one_goes():
    w = World()
    w.add(**request("o/a", 1))
    w.add(**request("o/b", 2))
    w.store.put(RUNS_COLLECTION, {"id": "run-live", "status": "running"})
    w.store.put(
        RULE_ATTEMPT_BUDGETS,
        {"id": budget_id("pr-fixer:o/a#1"), "key": "pr-fixer:o/a#1", "run_id": "run-live"},
    )
    res = w.progress()
    assert res.output["dispatched"] == ["o/b#2"]
    assert w.waiting() == [("o/a#1", 1)]


def test_a_request_whose_pr_spent_its_attempts_is_dropped():
    w = World()
    w.add(**request("o/a", 1))
    w.store.put(
        RULE_ATTEMPT_BUDGETS,
        {"id": budget_id("pr-fixer:o/a#1"), "key": "pr-fixer:o/a#1", "count": 3, "limit": 3},
    )
    res = w.progress()
    assert res.output["dispatched"] == []
    assert res.output["dropped"] == [{"key": "o/a#1", "reason": "attempt_budget_exhausted"}]


def test_a_dispatch_that_never_started_a_run_frees_its_slot_after_stale_after_s():
    w = World()
    w.add(**request("o/a", 1))
    w.add(**request("o/b", 2))
    w.progress({**PROGRESS, "stale_after_s": 600})
    w.clock.advance(599)
    assert w.progress({**PROGRESS, "stale_after_s": 600}).output["dispatched"] == []
    w.clock.advance(2)
    assert w.progress({**PROGRESS, "stale_after_s": 600}).output["dispatched"] == ["o/b#2"]


def test_a_started_run_holds_its_slot_however_long_it_runs():
    w = World()
    w.add(**request("o/a", 1))
    w.add(**request("o/b", 2))
    w.progress()
    act = w.doc()["active"][0]
    w.store.put(RUNS_COLLECTION, {"id": act["run_id"], "status": "running"})
    w.clock.advance(5 * 3600)
    assert w.progress().output["dispatched"] == []


def test_a_lost_dispatch_event_is_written_again():
    w = World()
    w.add(**request("o/a", 1))
    w.progress()
    (event,) = w.dispatches()
    w.store.delete(EVENTS_COLLECTION, event["id"])  # the node died after the queue write
    w.progress()
    assert [e["id"] for e in w.dispatches()] == [event["id"]]


# ---- d30: retries wait in the queue -------------------------------------------------------


def budget(w, count, limit=3, key="pr-fixer:o/a#1"):
    w.store.put(
        RULE_ATTEMPT_BUDGETS,
        {"id": budget_id(key), "key": key, "count": count, "limit": limit},
    )


def test_a_retry_joins_at_the_back_with_its_instruction_and_attempt_count():
    w = World()
    w.add(**request("o/b", 2))
    budget(w, 1)
    res = w.add(
        **request(
            "o/a", 1, instruction="the gate failed", retry=True, prior_instruction="fix o/a#1"
        )
    )
    assert res.output["position"] == 2
    _, a = w.doc()["waiting"]
    assert a["retry"] is True
    assert a["attempt"] == 2
    assert a["inputs"]["instruction"] == "the gate failed"
    assert a["inputs"]["task"] == "fix o/a#1"  # the original task travels with the retry


def test_a_retry_after_the_last_attempt_fails_the_step_to_hand_back():
    w = World()
    budget(w, 3)
    res = w.add(**request("o/a", 1, instruction="the gate failed again", retry=True))
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("attempt_budget_exhausted")
    assert "the gate failed again" in res.error
    assert w.doc() is None or w.doc()["waiting"] == []


def test_a_new_request_replaces_a_queued_retry_in_place():
    w = World()
    budget(w, 1)
    w.add(**request("o/a", 1, instruction="gate said", retry=True))
    w.add(**request("o/b", 2))
    res = w.add(**request("o/a", 1, head="d" * 40, instruction="/fix"))
    assert res.output["position"] == 1
    a, _ = w.doc()["waiting"]
    assert a["retry"] is False
    assert a["inputs"]["instruction"] == "/fix"
    assert a["head_sha"] == "d" * 40


def test_a_retry_does_not_replace_a_newer_queued_request():
    w = World()
    w.add(**request("o/a", 1, head="d" * 40, instruction="/fix"))
    budget(w, 1)
    res = w.add(**request("o/a", 1, instruction="old gate", retry=True))
    assert res.output["queued"] is True
    assert res.output["replaced"] is False
    (a,) = w.doc()["waiting"]
    assert a["inputs"]["instruction"] == "/fix"


# ---- the dispatch event is the engine's own ----------------------------------------------


def test_dispatch_events_are_reserved_at_external_ingest():
    assert reserved_reason({"id": "evt_1", "type": DISPATCH_TYPE, "source": "x"})
    assert reserved_reason({"id": dispatch_event_id("pr-fixer", "q1"), "type": "t", "source": "x"})
    assert reserved_reason({"id": "evt_2", "type": "rules.queue.changed", "source": "x"})


@pytest.mark.parametrize("cap", [0, -1, "2", True])
def test_a_bad_cap_fails_the_step(cap):
    w = World()
    res = w.progress({**PROGRESS, "cap": cap})
    assert res.outcome == FAILED
    assert res.retryable is False


def test_the_cap_can_come_from_an_actor_concurrency_pool():
    w = World()
    for actor_id, cap in (("qwen-fixer", 2), ("qwen-review", 3)):
        w.store.put(
            "actors",
            {
                "id": actor_id,
                "name": actor_id,
                "kind": "agent",
                "params": {"concurrency_pool": "qwen-spark2", "max_concurrency": cap},
            },
        )
    for i in range(1, 4):
        w.add(**request(f"o/r{i}", i))
    res = w.progress({**PROGRESS, "pool": "qwen-spark2", "cap": 1})
    assert res.output["dispatched"] == ["o/r1#1", "o/r2#2"]  # the pool's cap: 2
    other = World()
    other.add(**request("o/a", 1))
    assert other.progress({**PROGRESS, "pool": "nobody"}).output["dispatched"] == ["o/a#1"]


def test_a_request_replaced_during_the_pr_lookup_is_judged_on_its_new_head():
    """Codex P2: progress reads head A; another host replaces the request with the PR's
    current head C while the lookup runs (which answers C). The lost compare-and-set must
    re-judge the fresh request against the fetched facts, never reuse a refusal of A."""
    w = World()

    class Lookup:
        def invoke(self, input, key, deadline, *, context):
            if w.doc()["waiting"][0]["head_sha"] == "a" * 40:
                w.add(**request("o/a", 1, head="c" * 40, instruction="newer"))
            return InvocationResult.completed({"head_sha": "c" * 40, "state": "open"})

    w.progress_port = QueueProgressPort(w.store, clock=w.clock, pr_lookup=Lookup())
    w.add(**request("o/a", 1, head="a" * 40))
    res = w.progress({**PROGRESS, "lookup_actor": "github-app"})
    assert res.output["dropped"] == []
    assert res.output["dispatched"] == ["o/a#1"]
    (event,) = w.dispatches()
    assert event["data"]["head_sha"] == "c" * 40
    assert event["data"]["instruction"] == "newer"


def test_facts_read_before_a_newer_push_never_drop_the_newer_request():
    """Codex round 2: the lookup reads head A; a push lands and the request is replaced with
    C before the queue write; the retried pass must read the PR again for C (facts older
    than the request never drop it), not judge C on A's facts."""
    w = World()
    calls = []

    class Lookup:
        def invoke(self, input, key, deadline, *, context):
            calls.append(key)
            if len(calls) == 1:  # GitHub still says A; meanwhile C is pushed and queued
                w.add(**request("o/a", 1, head="c" * 40, instruction="after the push"))
                return InvocationResult.completed({"head_sha": "a" * 40, "state": "open"})
            return InvocationResult.completed({"head_sha": "c" * 40, "state": "open"})

    w.progress_port = QueueProgressPort(w.store, clock=w.clock, pr_lookup=Lookup())
    w.add(**request("o/a", 1, head="a" * 40))
    res = w.progress({**PROGRESS, "lookup_actor": "github-app"})
    assert res.output["dropped"] == []
    assert res.output["dispatched"] == ["o/a#1"]
    (event,) = w.dispatches()
    assert event["data"]["head_sha"] == "c" * 40
    assert len(calls) == 2


class TipLookup:
    """An open PR at head ``a*40`` whose base branch's live tip is ``tip`` (d37)."""

    def __init__(self, tip):
        self.tip = tip
        self.inputs = []

    def invoke(self, input, key, deadline, *, context):
        self.inputs.append(dict(input))
        out = {"head_sha": "a" * 40, "state": "open", "base_sha": "b" * 40}
        if input.get("with_base_tip") is True:
            out["base_tip_sha"] = self.tip
        return InvocationResult.completed(out)


def test_d37_a_dispatch_carries_the_base_branchs_live_tip():
    """GitHub's base.sha is the base as of the PR's last push; the try is given the base
    branch's tip read at dispatch, so it can merge (and the gate check) the real base."""
    lookup = TipLookup("c" * 40)
    w = World(lookup=lookup)
    w.add(**request("o/a", 1))
    w.progress({**PROGRESS, "lookup_actor": "github-app"})
    (event,) = w.dispatches()
    assert event["data"]["base_sha"] == "c" * 40
    assert event["data"]["base_tip_sha"] == "c" * 40  # the queue's own, for the gate and push
    assert lookup.inputs[0]["with_base_tip"] is True


def test_d37_a_request_cannot_name_the_dispatched_tip():
    """base_tip_sha vouches for a base other than base.sha: only the queue writes it."""
    w = World(lookup=TipLookup(None))
    w.add(**request("o/a", 1, base_tip_sha="e" * 40))
    w.progress({**PROGRESS, "lookup_actor": "github-app"})
    (event,) = w.dispatches()
    assert "base_tip_sha" not in event["data"]


def test_d37_an_unread_tip_keeps_the_requests_base():
    w = World(lookup=TipLookup(None))
    w.add(**request("o/a", 1))
    w.progress({**PROGRESS, "lookup_actor": "github-app"})
    (event,) = w.dispatches()
    assert event["data"]["base_sha"] == "b" * 40


def test_d37_without_a_lookup_the_requests_base_is_dispatched():
    w = World()
    w.add(**request("o/a", 1))
    w.progress()
    (event,) = w.dispatches()
    assert event["data"]["base_sha"] == "b" * 40
