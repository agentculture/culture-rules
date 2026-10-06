"""h78 / c97: a skipped rule records why in its contextual history (``rule_decisions``).

The node persists one decision record per (rule, event) for the skips that matter
(superseded_by, blocked_by_predecessor, group_lost), inside the same trigger transaction
that commits the firing intents, so it is idempotent under redelivery and rolls back with it.
"""

from __future__ import annotations

from culture_rules.engine.decisions import RECORDED_REASONS, RULE_DECISIONS, decision_key
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger
from culture_rules.node import firing as firing_mod
from culture_rules.node.daemon import NODE_COLLECTIONS
from culture_rules.node.firing import RULE_FIRES
from tests.events.fakes import envelope
from tests.node.test_node import EVENT_TYPE, Cluster


def rule(id: str, **kw) -> Rule:
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params={"type": EVENT_TYPE}),
        action=Action(kind="noop"),
        **kw,
    )


def decisions(c: Cluster, rule_id: str | None = None) -> list[dict]:
    where = {"rule_id": rule_id} if rule_id else None
    return c.base.find(RULE_DECISIONS, where)


def test_the_recorded_reasons_and_collection():
    assert set(RECORDED_REASONS) == {
        "superseded_by",
        "blocked_by_predecessor",
        "group_lost",
        "predecessor_failed",
        "rate_capped",
        "variables_unsupported",
        "variable_undefined",
        "deduplicated",
        "attempt_budget_exhausted",
    }
    assert RULE_DECISIONS in NODE_COLLECTIONS


def test_a_superseded_rule_records_superseded_by_its_superseder():
    c = Cluster("spark")
    c.define(rule("a", supersedes=("b",)), rule("b"))
    c.start()
    c.publish(envelope(1))
    c.cycle()

    assert c.run("a", "evt_1") is not None
    assert c.run("b", "evt_1") is None
    (doc,) = decisions(c, "b")
    assert doc["id"] == decision_key("b", "evt_1")
    assert doc["rule_id"] == "b"
    assert doc["event_id"] == "evt_1"
    assert doc["reason"] == "superseded_by"
    assert doc["by"] == ["a"]
    assert doc["host"] == "spark"
    assert doc["at"]
    assert doc["message"] == "superseded by a"
    assert decisions(c, "a") == []  # a fired: its history is the run


def test_group_lost_and_blocked_by_predecessor_are_recorded_too():
    c = Cluster("spark")
    c.define(
        rule("hi", exclusive_group="g", priority=9),
        rule("lo", exclusive_group="g", priority=1),
        rule("down", must_after=("hi",)),
    )
    c.start()
    c.publish(envelope(1))
    c.cycle()

    (lost,) = decisions(c, "lo")
    assert lost["reason"] == "group_lost"
    assert lost["by"] == ["hi"]
    (blocked,) = decisions(c, "down")
    assert blocked["reason"] == "blocked_by_predecessor"
    assert blocked["by"] == ["hi"]


def test_condition_false_and_disabled_are_not_recorded():
    false = {"op": "compare", "cmp": "==", "left": {"literal": 1}, "right": {"literal": 2}}
    c = Cluster("spark")
    c.define(rule("off", enabled=False), rule("no", condition=false))
    c.start()
    c.publish(envelope(1))
    c.cycle()
    assert decisions(c) == []


def test_redelivery_of_the_same_event_records_one_decision():
    c = Cluster("spark")
    c.define(rule("a", supersedes=("b",)), rule("b"))
    c.start()
    c.publish(envelope(1))
    c.cycle()
    node = c.nodes["spark"]
    env = c.base.get("events", "evt_1")
    with c.base.transaction() as tx:  # the same event evaluated again (redelivery)
        node.firing._evaluate(tx, env, placed=False)
    assert len(decisions(c, "b")) == 1


def test_decision_rolls_back_with_the_trigger_transaction(monkeypatch):
    c = Cluster("spark")
    # "b" is evaluated (and its skip recorded) before "z" fires; "z" then fails mid-transaction.
    c.define(rule("z", supersedes=("b",)), rule("b"))
    c.start()
    c.publish(envelope(1))
    real = firing_mod.run_id_for

    def broken(rule_id, event_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(firing_mod, "run_id_for", broken)
    report = c.nodes["spark"].run_once()
    assert report.errors, report
    assert decisions(c) == []
    assert c.base.find(RULE_FIRES) == []

    monkeypatch.setattr(firing_mod, "run_id_for", real)
    c.cycle()
    (doc,) = decisions(c, "b")
    assert doc["reason"] == "superseded_by"
    assert doc["by"] == ["z"]
    assert c.run("z", "evt_1") is not None
