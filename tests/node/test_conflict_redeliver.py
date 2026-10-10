"""d39 (#44): a conflict is requested until a rule hears it, not just emitted once.

The d31 watch emitted one ``github.pr.conflicting`` event per head and base pair, ever, so a
conflict evaluated while ``pr-fixer-conflict`` was missing or disabled (between a bundle
import and the re-enable), or while the engine was paused (a trigger event evaluated during
a pause is dropped), was never requested again. Now a pair is emitted again as a new
generation when:

* no rule triggered by ``github.pr.conflicting`` heard any of its stored generations (no
  firing intent, no recorded decision);
* the last generation was **evaluated** - the fire marker of the trigger consumer that
  decides each enabled listener exists, so whatever it decided is final (an event still
  waiting for its first poll, or deferred for a drained host, is never sent twice);
* it is at least one interval old, a listener is enabled and the engine is not paused;

and at most :data:`~culture_rules.node.conflict_watch.MAX_GENERATIONS` events per pair. The
end-to-end tests drive the node's real :class:`~culture_rules.node.firing.RuleFiring`.
"""

from __future__ import annotations

from datetime import timedelta

from culture_rules.engine.claims import firing_key
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.runs import CONTROLS_COLLECTION, RULES_COLLECTION
from culture_rules.events.emit import PR_CONFLICTING_TYPE
from culture_rules.events.triggers import FIRES_COLLECTION
from culture_rules.node.conflict_watch import (
    DEFAULT_INTERVAL_S,
    MAX_GENERATIONS,
    REQUESTS_PER_TICK,
    conflict_event_id,
)
from culture_rules.node.firing import RULE_FIRES, RuleFiring
from tests.node.test_conflict_watch import BASE, HEAD, REPO, events, pull, world
from tests.node.test_start_fired_characterization import ScriptedExecutor

RULE = "pr-fixer-conflict"
GEN0 = conflict_event_id(REPO, 3, HEAD, BASE)
GEN1 = conflict_event_id(REPO, 3, HEAD, BASE, generation=1)


def listener(store, enabled=True, rule_id=RULE, kind=PR_CONFLICTING_TYPE, placement=None, **extra):
    store.put(
        RULES_COLLECTION,
        {
            "id": rule_id,
            "schema_version": "1.0",
            "name": rule_id,
            "enabled": enabled,
            "placement": placement,
            "trigger": {"kind": "event", "params": {"type": kind}},
            "action": {
                "kind": "github.comment",
                "name": "comment",
                "params": {
                    "actor": "github-app",
                    "body": "conflict",
                    "number": "trigger.data.number",
                    "repo": "trigger.data.repository",
                },
                "idempotent": False,
                "retry": None,
                "timeout_s": None,
            },
            **extra,
        },
    )


def evaluated(store, event_id, consumer="triggers"):
    store.put(FIRES_COLLECTION, {"id": f"{consumer}/{event_id}"})


def later(clock, seconds=DEFAULT_INTERVAL_S + 1):
    clock.now += timedelta(seconds=seconds)


def ids(store):
    return sorted(e["id"] for e in events(store))


def paused(store, flag):
    store.put(CONTROLS_COLLECTION, {"id": "global", "paused": flag})


class Engine:
    """The node's real trigger consumers over the same store (host ``spark``)."""

    def __init__(self, store, clock):
        self.firing = RuleFiring(store, "spark", ScriptedExecutor(), clock=clock)
        for consumer in self.firing.start_consumers:
            self.firing.poll(consumer)

    def poll(self):
        for consumer in self.firing.consumers:
            outcome = self.firing.poll(consumer)
            assert outcome.error is None, outcome.error


def fired(store, rule=RULE):
    return sorted(i["event_id"] for i in store.find(RULE_FIRES) if i.get("rule_id") == rule)


# ------------------------------------------------------------------ end to end


def test_a_conflict_evaluated_while_the_rule_was_disabled_fires_once_it_is_enabled():
    store, _gh, clock, watcher = world([pull()])
    listener(store, enabled=False)  # an import lands every rule disabled
    engine = Engine(store, clock)
    assert watcher.tick() == 1
    engine.poll()  # evaluated: the rule is disabled, nothing fires
    assert fired(store) == []
    listener(store, enabled=True)
    later(clock)
    assert watcher.tick() == 1
    engine.poll()
    assert fired(store) == [GEN1]
    for _ in range(4):  # heard: never again
        later(clock)
        assert watcher.tick() == 0
        engine.poll()
    assert fired(store) == [GEN1]
    assert ids(store) == sorted([GEN0, GEN1])


def test_an_event_still_waiting_for_its_first_poll_is_never_sent_twice():
    """Codex r1 #1: older than an interval but not evaluated yet is pending, not missed."""
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    engine = Engine(store, clock)
    watcher.tick()
    later(clock)
    assert watcher.tick() == 0  # no poll ran yet: wait
    engine.poll()
    later(clock)
    assert watcher.tick() == 0
    assert fired(store) == [GEN0]


def test_a_conflict_evaluated_while_paused_fires_after_resume():
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    engine = Engine(store, clock)
    paused(store, True)
    watcher.tick()
    engine.poll()  # a trigger event evaluated while paused is dropped
    assert fired(store) == []
    later(clock)
    assert watcher.tick() == 0  # never into a paused engine
    paused(store, False)
    later(clock)
    assert watcher.tick() == 1
    engine.poll()
    assert fired(store) == [GEN1]


def test_a_conflict_ingested_while_paused_but_evaluated_after_resume_fires_once():
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    engine = Engine(store, clock)  # the node's consumers exist; none polls in the pause
    paused(store, True)
    watcher.tick()  # ingested during the pause, not evaluated yet
    paused(store, False)
    later(clock)
    assert watcher.tick() == 0  # unevaluated: it is still pending
    engine.poll()
    later(clock)
    assert watcher.tick() == 0
    assert fired(store) == [GEN0]  # its first evaluation, after resume: once


# ------------------------------------------------------------------ the decision


def test_a_consumed_conflict_is_never_requested_again():
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    watcher.tick()
    evaluated(store, GEN0)
    store.put(RULE_FIRES, {"id": firing_key(RULE, GEN0), "status": "started"})
    for _ in range(5):
        later(clock)
        assert watcher.tick() == 0
    assert ids(store) == [GEN0]


def test_a_late_firing_on_an_earlier_generation_stops_the_next():
    """Codex r1 #2: consumption of any stored generation counts, not just the last."""
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    watcher.tick()
    evaluated(store, GEN0)
    later(clock)
    assert watcher.tick() == 1  # gen 1
    evaluated(store, GEN1)
    store.put(RULE_FIRES, {"id": firing_key(RULE, GEN0), "status": "pending"})
    later(clock)
    assert watcher.tick() == 0
    assert ids(store) == sorted([GEN0, GEN1])


def test_a_recorded_decision_counts_as_heard():
    """A recorded skip (deduplicated, attempt budget spent, ...) is the rule hearing it."""
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    watcher.tick()
    evaluated(store, GEN0)
    store.put(RULE_DECISIONS, {"id": decision_key(RULE, GEN0), "reason": "deduplicated"})
    later(clock)
    assert watcher.tick() == 0


def test_any_rule_on_the_type_hearing_it_counts():
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    listener(store, rule_id="another-conflict-rule", enabled=False)
    watcher.tick()
    evaluated(store, GEN0)
    store.put(RULE_FIRES, {"id": firing_key("another-conflict-rule", GEN0), "status": "pending"})
    later(clock)
    assert watcher.tick() == 0


def test_a_rule_placed_on_a_machine_waits_for_that_machines_consumer():
    store, _gh, clock, watcher = world([pull()])
    listener(store, placement={"machine": "spark2", "actor": None, "requirement": None})
    watcher.tick()
    evaluated(store, GEN0)  # the shared consumer's marker does not decide a placed rule
    later(clock)
    assert watcher.tick() == 0
    evaluated(store, GEN0, consumer="triggers@spark2")
    later(clock)
    assert watcher.tick() == 1


def test_a_rule_placed_by_actor_is_never_requested_again():
    """Its deciding consumer cannot be named here: never twice is the safe side."""
    store, _gh, clock, watcher = world([pull()])
    listener(store, placement={"machine": None, "actor": "github-app", "requirement": None})
    watcher.tick()
    evaluated(store, GEN0)
    later(clock)
    assert watcher.tick() == 0


def test_no_enabled_listener_waits():
    store, _gh, clock, watcher = world([pull()])
    listener(store, enabled=False)
    watcher.tick()
    evaluated(store, GEN0)
    later(clock)
    assert watcher.tick() == 0
    listener(store, enabled=True)
    later(clock)
    assert watcher.tick() == 1


def test_a_rule_on_another_type_or_deleted_is_not_a_listener():
    store, _gh, clock, watcher = world([pull()])
    listener(store, kind="github.pr.checks_settled")
    listener(store, rule_id="gone", deleted_at="2026-10-10T00:00:00+00:00")
    watcher.tick()
    evaluated(store, GEN0)
    later(clock)
    assert watcher.tick() == 0


def test_the_next_generation_waits_one_interval_after_the_last_event():
    store, _gh, clock, watcher = world([pull()], conflict_watch_interval_s=600)
    listener(store)
    watcher.tick()
    evaluated(store, GEN0)
    later(clock, 300)
    watcher._last = None  # force a sweep: only the event's own age may hold it back
    assert watcher.tick() == 0
    later(clock, 301)
    watcher._last = None
    assert watcher.tick() == 1


def test_the_events_age_is_taken_when_it_is_written_not_at_cycle_start():
    """Codex r1 #4: a slow cycle never back-dates the event."""
    store, gh, clock, watcher = world([pull()])
    real = gh.get_pull

    def slow(repo, number, timeout):
        clock.now += timedelta(seconds=4)
        return real(repo, number, timeout)

    gh.get_pull = slow
    watcher._get = slow
    start = clock.now
    watcher.tick()
    (doc,) = store.find("events")
    assert doc["received_at"] > start.isoformat()


def test_a_pair_is_emitted_at_most_max_generations_times():
    """A conflict a listening rule keeps declining (condition false is not recorded) is
    not re-requested forever."""
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    for gen in range(MAX_GENERATIONS + 4):
        watcher.tick()
        if gen < MAX_GENERATIONS:
            evaluated(store, conflict_event_id(REPO, 3, HEAD, BASE, generation=gen))
        later(clock)
    assert len(events(store)) == MAX_GENERATIONS


def test_generation_zero_keeps_the_d31_event_id():
    assert GEN0 == conflict_event_id(REPO, 3, HEAD, BASE, generation=0)
    assert GEN1 != GEN0


def test_listeners_are_read_once_per_cycle():
    """Codex r1 #5: the rules collection is read once per cycle, not once per pair."""
    pulls = [pull(number=n) for n in range(1, 8)]
    store, _gh, clock, watcher = world(pulls)
    listener(store)
    watcher.tick()
    for n in range(1, 8):
        evaluated(store, conflict_event_id(REPO, n, HEAD, BASE))
    later(clock)
    finds = []
    real = store.find

    def counting(collection, *args, **kwargs):
        finds.append(collection)
        return real(collection, *args, **kwargs)

    store.find = counting
    watcher.tick()
    assert finds.count(RULES_COLLECTION) <= 1


def test_redelivery_makes_no_extra_github_requests():
    """The consumption check reads the store only: the per-cycle budget is unchanged."""
    pulls = [pull(number=n) for n in range(1, 30)]
    store, gh, clock, watcher = world(pulls)
    listener(store, enabled=False)
    watcher.tick()
    listener(store)
    later(clock)
    gh.reads.clear()
    gh.pages.clear()
    watcher.tick()
    assert len(gh.reads) + len(gh.pages) <= REQUESTS_PER_TICK
