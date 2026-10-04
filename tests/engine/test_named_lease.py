"""t26: a mesh-wide named lease (one holder at a time, takeover only after it lapses)."""

from __future__ import annotations

from datetime import timedelta

from culture_rules.engine.named_lease import LEASES_COLLECTION, NamedLease
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock

TTL = timedelta(seconds=30)


def pair():
    clock = Clock()
    base = MemoryStore(clock=clock)
    a = NamedLease(base.peer(), "discord-gateway:bot", "engine@a", ttl=TTL, clock=clock)
    b = NamedLease(base.peer(), "discord-gateway:bot", "engine@b", ttl=TTL, clock=clock)
    return clock, base, a, b


def test_first_acquirer_wins_and_the_other_is_refused():
    clock, base, a, b = pair()
    assert a.acquire() == clock.now + TTL
    assert b.acquire() is None
    doc = base.get(LEASES_COLLECTION, "discord-gateway:bot")
    assert doc["holder"] == "engine@a"


def test_holder_renews_and_extends():
    clock, _, a, b = pair()
    a.acquire()
    clock.advance(20)
    assert a.acquire() == clock.now + TTL
    clock.advance(20)  # 40 s after the first grant, but renewed at 20: still held
    assert b.acquire() is None


def test_takeover_only_after_the_lease_lapses():
    clock, base, a, b = pair()
    a.acquire()
    clock.advance(29)
    assert b.acquire() is None
    clock.advance(2)
    assert b.acquire() == clock.now + TTL
    assert base.get(LEASES_COLLECTION, "discord-gateway:bot")["holder"] == "engine@b"
    assert a.acquire() is None  # the stale holder cannot renew over the new one


def test_release_frees_it_at_once():
    _, _, a, b = pair()
    a.acquire()
    a.release()
    assert b.acquire() is not None
    a.release()  # not the holder: a no-op
    assert b.acquire() is not None


def test_epoch_increments_on_every_acquisition():
    clock, base, a, b = pair()
    a.acquire()
    e1 = base.get(LEASES_COLLECTION, "discord-gateway:bot")["epoch"]
    a.acquire()
    assert base.get(LEASES_COLLECTION, "discord-gateway:bot")["epoch"] == e1
    clock.advance(31)
    b.acquire()
    assert base.get(LEASES_COLLECTION, "discord-gateway:bot")["epoch"] == e1 + 1
