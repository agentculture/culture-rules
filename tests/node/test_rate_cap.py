"""c47 / h37: a per-rule fire-rate cap, evaluated in the trigger transaction.

``trigger.params.max_fires_per_hour`` (default 60) caps how often a rule fires in a trailing
hour across every host sharing the store. A matching event over the cap is not dropped
silently: the rule records a ``rate_capped`` skip in its history instead of a run. A capped
predecessor is final for its event, so its dependants do not wait for it forever.
MemoryStore plus fake actors.
"""

from __future__ import annotations

from culture_rules.engine.decisions import RATE_CAPPED, RULE_DECISIONS, decision_key
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule, Trigger
from culture_rules.node.firing import (
    DEFAULT_MAX_FIRES_PER_HOUR,
    RULE_FIRES,
    RULE_RATES,
    max_fires_per_hour,
)
from tests.events.fakes import envelope
from tests.node.test_node import EVENT_TYPE, Cluster


def capped_rule(id: str = "r", cap=3, **kw) -> Rule:
    params = {"type": EVENT_TYPE}
    if cap is not None:
        params["max_fires_per_hour"] = cap
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params=params),
        action=Action(kind="noop", params={"event": "trigger.id"}),
        **kw,
    )


def fired(c: Cluster, rule_id: str) -> list[str]:
    return sorted(d["event_id"] for d in c.base.find(RULE_FIRES, {"rule_id": rule_id}))


def runs(c: Cluster, rule_id: str) -> list[dict]:
    return [r for r in c.base.find(RUNS_COLLECTION) if r["rule"]["id"] == rule_id]


def capped(c: Cluster, rule_id: str) -> list[dict]:
    docs = c.base.find(RULE_DECISIONS, {"rule_id": rule_id})
    return [d for d in docs if d["reason"] == RATE_CAPPED]


def cycles(c: Cluster, n: int, *hosts: str) -> None:
    for _ in range(n):
        c.cycle(*hosts)


def test_a_rule_over_its_cap_records_rate_capped_skips_instead_of_runs():
    c = Cluster("spark")
    c.define(capped_rule(cap=3))
    c.start()
    for n in range(1, 6):
        c.publish(envelope(n))
    cycles(c, 3)

    assert fired(c, "r") == ["evt_1", "evt_2", "evt_3"]
    assert len(runs(c, "r")) == 3
    skips = sorted(capped(c, "r"), key=lambda d: d["event_id"])
    assert [d["event_id"] for d in skips] == ["evt_4", "evt_5"]
    for doc in skips:
        assert doc["id"] == decision_key("r", doc["event_id"])
        assert doc["fire"] is False
        assert "superseded" not in doc  # final in one step
        assert "cap 3" in doc["message"]
        assert doc["message"].startswith("rate capped")


def test_two_hosts_racing_on_one_rule_respect_the_shared_cap():
    c = Cluster("spark", "thor")
    c.define(capped_rule(cap=3))
    c.start()
    for n in range(1, 6):
        c.publish(envelope(n))
        c.cycle("thor" if n % 2 else "spark")  # each host takes some of the events
    cycles(c, 2)

    hosts = {d["host"] for d in c.base.find(RULE_FIRES, {"rule_id": "r"})}
    hosts |= {d["host"] for d in capped(c, "r")}
    assert hosts == {"spark", "thor"}  # both hosts evaluated the rule
    assert len(fired(c, "r")) == 3
    assert len(runs(c, "r")) == 3
    assert len(capped(c, "r")) == 2
    # the window both hosts read and write: the cross-host serialisation point
    assert len(c.base.get(RULE_RATES, "r")["fires"]) == 3


def test_the_cap_is_a_trailing_hour_window():
    c = Cluster("spark")
    c.define(capped_rule(cap=2))
    c.start()
    for n in (1, 2, 3):
        c.publish(envelope(n))
    cycles(c, 2)
    assert fired(c, "r") == ["evt_1", "evt_2"]

    c.clock.advance(3600 + 1)  # the first two fires leave the window
    c.publish(envelope(4))
    c.publish(envelope(5))
    c.publish(envelope(6))
    cycles(c, 2)
    assert fired(c, "r") == ["evt_1", "evt_2", "evt_4", "evt_5"]
    assert sorted(d["event_id"] for d in capped(c, "r")) == ["evt_3", "evt_6"]
    assert len(c.base.get(RULE_RATES, "r")["fires"]) == 2  # pruned, bounded by the cap


def test_redelivery_of_a_capped_event_does_not_fire_it_later():
    c = Cluster("spark")
    c.define(capped_rule(cap=1))
    c.start()
    c.publish(envelope(1))
    c.publish(envelope(2))
    cycles(c, 2)
    assert fired(c, "r") == ["evt_1"]

    c.clock.advance(3600 + 1)  # room again, but evt_2 already has its outcome
    c.publish(envelope(2))
    cycles(c, 2)
    assert fired(c, "r") == ["evt_1"]
    (doc,) = capped(c, "r")
    assert doc["event_id"] == "evt_2"


def test_the_default_cap_is_60_and_bad_values_fall_back_to_it():
    assert DEFAULT_MAX_FIRES_PER_HOUR == 60
    assert max_fires_per_hour(capped_rule(cap=None)) == 60
    assert max_fires_per_hour(capped_rule(cap=5)) == 5
    for bad in (0, -1, "10", 2.5, True, None):
        assert max_fires_per_hour(capped_rule(cap=bad)) == 60, bad


def test_the_default_cap_applies_when_unset():
    c = Cluster("spark")
    c.define(capped_rule(cap=None))
    c.start()
    for n in range(1, 63):
        c.publish(envelope(n))
    cycles(c, 3)
    assert len(fired(c, "r")) == 60
    assert len(capped(c, "r")) == 2


def test_a_capped_predecessor_fails_its_must_after_dependant_on_the_same_host():
    c = Cluster("spark")
    c.define(capped_rule("a", cap=1), capped_rule("b", cap=None, must_after=("a",)))
    c.start()
    c.publish(envelope(1))
    c.publish(envelope(2))
    cycles(c, 4)

    assert fired(c, "a") == ["evt_1"]
    assert fired(c, "b") == ["evt_1"]  # ran after a succeeded
    doc = c.base.get(RULE_DECISIONS, decision_key("b", "evt_2"))
    assert doc["reason"] == "predecessor_failed"
    assert doc["by"] == ["a"]


def test_a_capped_predecessor_on_another_host_settles_the_waiting_dependant():
    c = Cluster("spark", "thor")
    c.define(
        capped_rule("a", cap=1, placement=Placement(machine="spark")),
        capped_rule("b", cap=None, must_after=("a",), placement=Placement(machine="thor")),
    )
    c.start()
    c.publish(envelope(1))
    cycles(c, 4)
    assert fired(c, "b") == ["evt_1"]

    c.publish(envelope(2))
    c.cycle("thor")  # thor sees a matching (it cannot know a's cap outcome): b waits
    waiting = c.base.get(RULE_DECISIONS, decision_key("b", "evt_2"))
    assert waiting["reason"] == "blocked_by_predecessor"
    c.cycle("spark")  # a is over its cap: its rate_capped skip is final in one step
    assert c.base.get(RULE_DECISIONS, decision_key("a", "evt_2"))["reason"] == RATE_CAPPED
    cycles(c, 2, "thor")  # the chain feed on thor settles b

    doc = c.base.get(RULE_DECISIONS, decision_key("b", "evt_2"))
    assert doc["reason"] == "predecessor_failed"
    assert doc["by"] == ["a"]
    assert [h["reason"] for h in doc["superseded"]] == ["blocked_by_predecessor"]
    assert fired(c, "b") == ["evt_1"]
