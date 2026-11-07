"""The bridge agent's trust check for a pinned actor (``BridgeAgentActor._trust``).

Generic: the test pins its own actor id in ``TRUSTED_ACTOR_DIGESTS`` for the duration of a
test, so it does not depend on which actors production pins. A pinned actor dispatches only
when its snapshot hashes to a pinned digest and the bridge called is the snapshot's; a
refusal sends nothing. An unpinned actor dispatches, and its digest is recorded either way.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from culture_rules.actors import trusted
from culture_rules.actors.agent import BRIDGE_INVOCATIONS
from culture_rules.engine.actorport import InvocationContext
from culture_rules.store.memory import MemoryStore
from tests.actors.test_bridge_agent import PR, FakeBridge, make_actor
from tests.engine.run_helpers import T0, Clock

ACTOR_ID = "pinned-agent"
BRIDGE = "http://127.0.0.1:8765"
DOC = {
    "id": ACTOR_ID,
    "kind": "agent",
    "harness": "bridge",
    "model": "m",
    "machine": "spark",
    "params": {"bridge_url": BRIDGE + "/", "sandbox": "read-only"},
}


def invoke(store, clock, doc, *, actor_id=ACTOR_ID):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge, actor_id=actor_id, actor_doc=doc)
    ctx = InvocationContext("r", "s", "actor_task", "spark", 1, None, {})
    return actor.invoke(PR, "k", T0 + timedelta(hours=1), context=ctx), bridge


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setitem(
        trusted.TRUSTED_ACTOR_DIGESTS, ACTOR_ID, frozenset({trusted.actor_digest(DOC)})
    )


@pytest.mark.usefixtures("pinned")
def test_a_pinned_actor_matching_its_digest_dispatches(store, clock):
    res, bridge = invoke(store, clock, DOC)
    assert res.outcome == "accepted"
    assert len(bridge.requests) == 1


@pytest.mark.usefixtures("pinned")
def test_a_pinned_actor_records_its_digest_on_the_invocation(store, clock):
    invoke(store, clock, DOC)
    (doc,) = store.find(BRIDGE_INVOCATIONS)
    assert doc["actor_digest"] == trusted.actor_digest(DOC)


@pytest.mark.usefixtures("pinned")
def test_a_pinned_actor_whose_configuration_changed_is_refused(store, clock):
    changed = {**DOC, "params": {**DOC["params"], "sandbox": "danger-full-access"}}
    res, _ = invoke(store, clock, changed)
    assert res.outcome == "failed"
    assert res.error.startswith("actor_not_trusted")


@pytest.mark.usefixtures("pinned")
def test_a_refused_pinned_actor_sends_nothing(store, clock):
    changed = {**DOC, "params": {**DOC["params"], "sandbox": "danger-full-access"}}
    _, bridge = invoke(store, clock, changed)
    assert bridge.requests == []


@pytest.mark.usefixtures("pinned")
def test_a_refusal_is_not_retried(store, clock):
    changed = {**DOC, "model": "other"}
    res, _ = invoke(store, clock, changed)
    assert res.retryable is False


@pytest.mark.usefixtures("pinned")
def test_a_pinned_actor_calling_another_bridge_is_refused(store, clock):
    elsewhere = {**DOC, "params": {**DOC["params"], "bridge_url": "http://127.0.0.1:9999"}}
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(
            trusted.TRUSTED_ACTOR_DIGESTS, ACTOR_ID, frozenset({trusted.actor_digest(elsewhere)})
        )
        res, bridge = invoke(store, clock, elsewhere)
    assert res.error.startswith("actor_not_trusted")
    assert bridge.requests == []


@pytest.mark.usefixtures("pinned")
def test_a_pinned_actor_without_a_snapshot_is_refused(store, clock):
    res, bridge = invoke(store, clock, None)
    assert res.error.startswith("actor_not_trusted")
    assert bridge.requests == []


@pytest.mark.usefixtures("pinned")
def test_a_disabled_pinned_actor_is_refused(store, clock):
    res, _ = invoke(store, clock, {**DOC, "enabled": False})
    assert res.error.startswith("actor_not_trusted")


def test_an_unpinned_actor_dispatches_whatever_its_configuration(store, clock):
    res, bridge = invoke(store, clock, {**DOC, "model": "other"})
    assert res.outcome == "accepted"
    assert len(bridge.requests) == 1


def test_an_unpinned_actor_still_records_its_digest(store, clock):
    other = {**DOC, "model": "other"}
    invoke(store, clock, other)
    (doc,) = store.find(BRIDGE_INVOCATIONS)
    assert doc["actor_digest"] == trusted.actor_digest(other)
