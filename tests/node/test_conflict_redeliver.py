"""d39 (#44): a conflict is requested until a rule hears it, not just emitted once.

The d31 watch emitted one ``github.pr.conflicting`` event per head and base pair, ever, so a
conflict seen while ``pr-fixer-conflict`` was missing or disabled (between a bundle import
and the re-enable) or while the engine was paused was never requested again. Now a pair
whose last event no rule consumed - no firing intent and no recorded decision of any rule
triggered by ``github.pr.conflicting`` - is emitted again as a new generation, once such a
rule is enabled and the engine is not paused, at least one interval after the last one, and
at most :data:`~culture_rules.node.conflict_watch.MAX_GENERATIONS` events per pair.
"""

from __future__ import annotations

from datetime import timedelta

from culture_rules.engine.claims import firing_key
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.runs import CONTROLS_COLLECTION, RULES_COLLECTION
from culture_rules.events.emit import PR_CONFLICTING_TYPE
from culture_rules.node.conflict_watch import (
    DEFAULT_INTERVAL_S,
    MAX_GENERATIONS,
    REQUESTS_PER_TICK,
    conflict_event_id,
)
from culture_rules.node.firing import RULE_FIRES
from tests.node.test_conflict_watch import BASE, HEAD, REPO, events, pull, world

RULE = "pr-fixer-conflict"


def listener(store, enabled=True, rule_id=RULE, kind=PR_CONFLICTING_TYPE, **extra):
    store.put(
        RULES_COLLECTION,
        {
            "id": rule_id,
            "enabled": enabled,
            "trigger": {"kind": "event", "params": {"type": kind}},
            **extra,
        },
    )


def later(clock, seconds=DEFAULT_INTERVAL_S + 1):
    clock.now += timedelta(seconds=seconds)


def ids(store):
    return sorted(e["id"] for e in events(store))


def test_a_conflict_nobody_heard_is_requested_again_once_the_rule_is_enabled():
    store, _gh, clock, watcher = world([pull()])
    listener(store, enabled=False)  # an import lands every rule disabled
    assert watcher.tick() == 1
    later(clock)
    assert watcher.tick() == 0  # still nobody listening: nothing new
    listener(store, enabled=True)
    later(clock)
    assert watcher.tick() == 1
    first = conflict_event_id(REPO, 3, HEAD, BASE)
    second = conflict_event_id(REPO, 3, HEAD, BASE, generation=1)
    assert ids(store) == sorted([first, second])
    (again,) = [e for e in events(store) if e["id"] == second]
    assert again["type"] == PR_CONFLICTING_TYPE
    assert again["data"]["head_sha"] == HEAD and again["data"]["number"] == 3


def test_a_conflict_with_no_listener_rule_at_all_is_requested_once_it_exists():
    store, _gh, clock, watcher = world([pull()])
    watcher.tick()
    later(clock)
    listener(store)
    assert watcher.tick() == 1


def test_a_conflict_seen_while_paused_is_requested_again_after_resume():
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    store.put(CONTROLS_COLLECTION, {"id": "global", "paused": True})
    watcher.tick()
    later(clock)
    assert watcher.tick() == 0  # paused: never re-requested into a paused engine
    store.put(CONTROLS_COLLECTION, {"id": "global", "paused": False})
    later(clock)
    assert watcher.tick() == 1


def test_a_consumed_conflict_is_never_requested_again():
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    watcher.tick()
    first = conflict_event_id(REPO, 3, HEAD, BASE)
    store.put(RULE_FIRES, {"id": firing_key(RULE, first), "status": "started"})
    for _ in range(5):
        later(clock)
        assert watcher.tick() == 0
    assert ids(store) == [first]


def test_a_recorded_decision_counts_as_heard():
    """A recorded skip (deduplicated, attempt budget spent, ...) is the rule hearing it."""
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    watcher.tick()
    first = conflict_event_id(REPO, 3, HEAD, BASE)
    store.put(RULE_DECISIONS, {"id": decision_key(RULE, first), "reason": "deduplicated"})
    later(clock)
    assert watcher.tick() == 0


def test_any_rule_on_the_type_hearing_it_counts():
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    listener(store, rule_id="another-conflict-rule")
    watcher.tick()
    first = conflict_event_id(REPO, 3, HEAD, BASE)
    store.put(RULE_FIRES, {"id": firing_key("another-conflict-rule", first), "status": "pending"})
    later(clock)
    assert watcher.tick() == 0


def test_a_rule_on_another_type_is_not_a_listener():
    store, _gh, clock, watcher = world([pull()])
    listener(store, kind="github.pr.checks_settled")
    watcher.tick()
    later(clock)
    assert watcher.tick() == 0


def test_a_deleted_listener_rule_is_not_a_listener():
    store, _gh, clock, watcher = world([pull()])
    listener(store, deleted_at="2026-10-10T00:00:00+00:00")
    watcher.tick()
    later(clock)
    assert watcher.tick() == 0


def test_the_next_generation_waits_one_interval_after_the_last_event():
    store, _gh, clock, watcher = world([pull()], conflict_watch_interval_s=600)
    listener(store, enabled=False)
    watcher.tick()
    listener(store)
    later(clock, 300)
    watcher._last = None  # force a sweep: only the event's own age may hold it back
    assert watcher.tick() == 0
    later(clock, 301)
    watcher._last = None
    assert watcher.tick() == 1


def test_a_pair_is_emitted_at_most_max_generations_times():
    """A conflict a listening rule keeps declining (its condition false is not recorded)
    is not re-requested forever."""
    store, _gh, clock, watcher = world([pull()])
    listener(store)
    for _ in range(MAX_GENERATIONS + 4):
        watcher.tick()
        later(clock)
    assert len(events(store)) == MAX_GENERATIONS


def test_a_new_head_or_base_starts_its_own_generations():
    store, gh, clock, watcher = world([pull()])
    listener(store)
    store.put(
        RULE_FIRES,
        {"id": firing_key(RULE, conflict_event_id(REPO, 3, HEAD, BASE)), "status": "started"},
    )
    watcher.tick()
    gh.pulls[3] = pull(base="c" * 40)
    later(clock)
    assert watcher.tick() == 1
    assert conflict_event_id(REPO, 3, HEAD, "c" * 40) in ids(store)


def test_generation_zero_keeps_the_d31_event_id():
    assert conflict_event_id(REPO, 3, HEAD, BASE) == conflict_event_id(
        REPO, 3, HEAD, BASE, generation=0
    )
    assert conflict_event_id(REPO, 3, HEAD, BASE, generation=1) != conflict_event_id(
        REPO, 3, HEAD, BASE
    )


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
