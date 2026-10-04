"""c47 / h37: the fire-rate cap holds across three live hosts racing on one rule.

An unplaced rule capped at 3 fires per hour; six events reach every host's subscription at
once and the hosts race to evaluate them, on MemoryStore and on the MongoDB replica set.
Exactly three fire and three record ``rate_capped``, whichever host evaluated which event.
(Trigger evaluations of the shared consumer also serialise on its cursor document; the
``rule_rates`` window write is what serialises fires across consumers, see
:mod:`culture_rules.node.firing`, "Rate cap".)
"""

from __future__ import annotations

from culture_rules.engine.decisions import RATE_CAPPED, RULE_DECISIONS
from culture_rules.model.rule import Rule
from tests.events.fakes import envelope
from tests.multihost.harness import HOSTS, event_rule

CAP = 3
EVENTS = 6


def test_hosts_racing_on_one_rule_fire_it_at_most_its_cap(cluster):
    doc = event_rule("capped").to_dict()
    doc["trigger"]["params"]["max_fires_per_hour"] = CAP
    cluster.define(Rule.from_dict(doc))
    cluster.start(*HOSTS)
    for n in range(EVENTS):
        cluster.publish(envelope(n))

    def capped() -> list[dict]:
        docs = cluster.store.find(RULE_DECISIONS, {"rule_id": "capped"})
        return [d for d in docs if d["reason"] == RATE_CAPPED]

    cluster.wait_until(
        lambda: len(cluster.fires("capped")) + len(capped()) >= EVENTS,
        timeout=60,
        what="every event to be fired or capped",
    )
    cluster.settle(seconds=1.0)
    assert len(cluster.fires("capped")) == CAP
    assert len(capped()) == EVENTS - CAP
    assert set(cluster.fires("capped")).isdisjoint(d["event_id"] for d in capped())
