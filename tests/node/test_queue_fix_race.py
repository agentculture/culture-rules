"""#39, Codex round 2 #1: a ``/fix`` routed across the level queues never loses its request
to a concurrent level move.

``queue.add`` with levels writes the PR's request into the queue its try is due at, then
removes the PR's request waiting in the other level queues. Codex's interleaving: the
``/fix`` refreshes the request still waiting in level 1's queue (``_add``); before it removes
the copies elsewhere (``_unwait``), a ``queue.progress`` pass moves that very request to
level 2's queue (level 1 cools down, and level 2's slot is busy, so it waits there); the
``/fix`` then removed the moved request, and the PR was in no queue, with no dispatch.

The ``/fix`` here runs on its own thread and is paused between ``_add`` and ``_unwait``
while the progress pass runs on another. The routing is one store transaction, so the pass
waits for it (the MemoryStore serialises transactions) and judges the result.
"""

from __future__ import annotations

import threading

from culture_rules.node.actions import queue as queue_mod
from culture_rules.node.actions.fixer_levels import FIXER_COOLDOWNS
from tests.node.test_queue import request
from tests.node.test_queue_levels import add, doc, progress, world

SOURCE, DESTINATION = "pr-fixer", "pr-fixer-codex"
PR = "o/a#1"
FIX_HEAD = "d" * 40
WAIT_S = 5.0
RACE_S = 0.5


def cooled_world():
    """Level 1 cools down; PR o/b takes level 2's only slot, so a moved request waits."""
    w = world()
    until = w.clock().replace(hour=w.clock().hour + 1).isoformat()
    w.store.put(FIXER_COOLDOWNS, {"id": "qwen-fixer", "until": until})
    add(w, **request("o/b", 2))
    progress(w)  # o/b moves to level 2's queue and is dispatched there (its run goes on)
    add(w, **request("o/a", 1))  # try 1 is due at level 1: it waits in level 1's queue
    return w


def holders(w):
    """Where the PR's request is: (queue, "waiting" | "active") pairs."""
    found = []
    for name in (SOURCE, DESTINATION):
        for part in ("waiting", "active"):
            found += [(name, part) for r in doc(w, name)[part] if r["key"] == PR]
    return found


class PausedAdd:
    """Pauses the first ``QueueAddPort._write`` (the routing's add, Codex round 3 #1) made on
    the ``/fix``'s thread right after it returns, i.e. before its ``_unwait`` calls."""

    def __init__(self, monkeypatch) -> None:
        self.paused, self.resume = threading.Event(), threading.Event()
        self.thread: int | None = None
        original = queue_mod.QueueAddPort._write

        def _write(port, *args, **kwargs):
            out = original(port, *args, **kwargs)
            if threading.get_ident() == self.thread and not self.paused.is_set():
                self.paused.set()
                self.resume.wait(WAIT_S)
            return out

        monkeypatch.setattr(queue_mod.QueueAddPort, "_write", _write)


def race(w, monkeypatch):
    """The ``/fix`` paused between ``_add`` and ``_unwait`` while a progress pass runs."""
    pause = PausedAdd(monkeypatch)
    errors: list[BaseException] = []

    def guarded(fn):
        def run():
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 - reported by the test
                errors.append(exc)

        return run

    def fix():
        pause.thread = threading.get_ident()
        add(w, **request("o/a", 1, head=FIX_HEAD, instruction="/fix"))

    fixer = threading.Thread(target=guarded(fix))
    fixer.start()
    assert pause.paused.wait(WAIT_S)
    mover = threading.Thread(target=guarded(lambda: progress(w)))
    mover.start()
    mover.join(RACE_S)  # without the transaction the pass completes inside the pause
    pause.resume.set()
    fixer.join(WAIT_S)
    mover.join(WAIT_S)
    return errors


def test_a_fix_paused_between_add_and_unwait_never_leaves_the_pr_in_no_queue(monkeypatch):
    w = cooled_world()
    errors = race(w, monkeypatch)
    assert errors == []
    assert len(holders(w)) == 1


def test_after_the_race_a_later_pass_still_finds_the_fixs_request(monkeypatch):
    w = cooled_world()
    race(w, monkeypatch)
    progress(w)
    (found,) = [r for name in (SOURCE, DESTINATION) for r in doc(w, name)["waiting"]]
    assert found["head_sha"] == FIX_HEAD


def test_a_fix_without_a_race_routes_once():
    w = cooled_world()
    res = add(w, **request("o/a", 1, head=FIX_HEAD, instruction="/fix"))
    assert res.output["queued"] is True
    assert holders(w) == [(SOURCE, "waiting")]


def test_a_routing_that_keeps_conflicting_fails_retryably(monkeypatch):
    from culture_rules.store.port import TransientStoreError

    w = cooled_world()

    def conflicting(*_args, **_kwargs):
        raise TransientStoreError("write conflict")

    monkeypatch.setattr(queue_mod, "run_transaction", conflicting, raising=False)
    res = add(w, **request("o/a", 1, head=FIX_HEAD, instruction="/fix"))
    assert res.outcome == "failed"
    assert res.retryable is True
