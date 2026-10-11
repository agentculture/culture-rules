"""#39, Codex round 3 #1: replaying a ``/fix`` after a level move never removes the request.

Codex's interleaving: ``queue.add`` with idempotency key K puts the PR's request in level 1's
queue; a ``queue.progress`` pass moves it to level 2's queue (level 1 cools down, level 2's
slot is busy, so it waits there); the engine then replays K. ``_add`` found K in level 1's
``seen`` and answered ``queued`` without writing, and the routing then removed the PR's
request from the other level queues: the moved one. The PR was in no queue. A replay now
writes nothing anywhere: the routing removes elsewhere only after an actual insertion or
replacement.
"""

from __future__ import annotations

from culture_rules.node.actions.fixer_levels import FIXER_COOLDOWNS
from tests.node.test_queue import DEADLINE, ctx, request
from tests.node.test_queue_fix_race import DESTINATION, holders
from tests.node.test_queue_levels import LEVELS_CONFIG, add, doc, progress, world

KEY = "ik-the-fix"


def moved_world():
    """o/a#1 added with :data:`KEY`, then moved to level 2's queue, where it waits."""
    w = world()
    until = w.clock().replace(hour=w.clock().hour + 1).isoformat()
    add(w, **request("o/b", 2))
    progress(w)  # o/b takes level 1's slot... and is dispatched there
    w.store.put(FIXER_COOLDOWNS, {"id": "qwen-fixer", "until": until})
    add(w, **request("o/c", 3))
    progress(w)  # o/c moves to level 2 and takes its only slot
    first = replay(w)
    assert first.output["queued"] is True
    progress(w)  # level 1 cools down: o/a#1 moves to level 2 and waits behind o/c
    assert holders(w) == [(DESTINATION, "waiting")]
    return w


def replay(w):
    return w.add_port.invoke(
        request("o/a", 1, instruction="/fix"), KEY, DEADLINE, context=ctx(LEVELS_CONFIG)
    )


def test_a_replayed_fix_after_a_move_keeps_the_moved_request():
    w = moved_world()
    again = replay(w)
    assert again.outcome == "completed", again.error
    assert holders(w) == [(DESTINATION, "waiting")]


def test_a_replay_reports_no_replacement():
    w = moved_world()
    assert replay(w).output["replaced"] is False


def test_the_moved_request_is_the_original_after_the_replay():
    w = moved_world()
    before = doc(w, DESTINATION)["waiting"][-1]["rid"]
    replay(w)
    assert doc(w, DESTINATION)["waiting"][-1]["rid"] == before
