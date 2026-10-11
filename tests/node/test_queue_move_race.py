"""#39, Codex round 1 #1: a request moved to another level's queue is never lost to a
concurrent ``/fix``.

``queue.progress`` moves a request whose try another level takes (here: level 1 is on a
capacity cooldown, so try 1 goes to level 2's queue). A ``/fix`` for the same PR is due at
level 1, so it updates the request still waiting in level 1's queue and removes the PR's
request waiting in level 2's queue. Whatever order the two land in, the PR's request ends in
exactly one queue (or is dispatched), carrying the ``/fix``'s head, never in none.
"""

from __future__ import annotations

from culture_rules.node.actions.fixer_levels import FIXER_COOLDOWNS
from culture_rules.node.actions.queue import QUEUES_COLLECTION
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock
from tests.node.test_queue import request
from tests.node.test_queue_levels import add, doc, progress, world

SOURCE, DESTINATION = "pr-fixer", "pr-fixer-codex"
FIX_HEAD = "d" * 40


class RacingStore:
    """A MemoryStore that lands a concurrent ``/fix`` once, at the first point where the
    progress pass has decided to move the request but not yet committed it: the moved copy
    reaching the destination queue outside a transaction, or the pass opening one."""

    def __init__(self, inner: MemoryStore) -> None:
        self._inner = inner
        self.fix = None  # set by the test: the concurrent /fix
        self.fired = False

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _fire(self):
        if self.fix is not None and not self.fired:
            self.fired = True
            self.fix()

    def update_if(self, collection, id, expected, changes, *, upsert=False):
        res = self._inner.update_if(collection, id, expected, changes, upsert=upsert)
        moved = any(str(k).startswith("move:") for k in changes.get("seen") or ())
        if res.won and collection == QUEUES_COLLECTION and id == DESTINATION and moved:
            self._fire()
        return res

    def transaction(self):
        self._fire()
        return self._inner.transaction()


def racing_world():
    clock = Clock()
    store = RacingStore(MemoryStore(clock=clock))
    w = world(store=store, clock=clock)
    until = w.clock().replace(hour=w.clock().hour + 1).isoformat()
    w.store.put(FIXER_COOLDOWNS, {"id": "qwen-fixer", "until": until})  # level 1 cools down
    return w, store


def holders(w):
    """Where the PR's request is: (queue, "waiting" | "active") pairs."""
    found = []
    for name in (SOURCE, DESTINATION):
        for part in ("waiting", "active"):
            found += [(name, part) for r in doc(w, name)[part] if r["key"] == "o/a#1"]
    return found


def test_a_fix_racing_a_move_never_leaves_the_pr_in_no_queue():
    w, store = racing_world()
    add(w, **request("o/a", 1))  # try 1, due at level 1
    assert [r["key"] for r in doc(w, SOURCE)["waiting"]] == ["o/a#1"]
    store.fix = lambda: add(w, **request("o/a", 1, head=FIX_HEAD, instruction="/fix"))
    progress(w)
    assert store.fired is True
    assert len(holders(w)) == 1


def test_the_request_that_survives_the_race_is_the_fixs():
    w, store = racing_world()
    add(w, **request("o/a", 1))
    store.fix = lambda: add(w, **request("o/a", 1, head=FIX_HEAD, instruction="/fix"))
    progress(w)
    (event,) = w.dispatches()
    assert event["data"]["head_sha"] == FIX_HEAD
    assert event["data"]["instruction"] == "/fix"
    assert event["data"]["queue"] == DESTINATION


def test_a_move_without_a_race_still_moves_once():
    w, _store = racing_world()
    add(w, **request("o/a", 1))
    out = progress(w).output
    assert out["moved"] == [{"key": "o/a#1", "from": SOURCE, "to": DESTINATION}]
    assert holders(w) == [(DESTINATION, "active")]
