"""#39, Codex round 3 #2-#4: the hand-off's export replay, its prune and a replay's receipt.

* #3: an export of a hand-off id that already exists never erases its claim or receipt. The
  same commits (``start_sha``, ``head_after``: git object ids are content hashes, so any
  pack of them carries the same objects) renew only the retention, by compare-and-set; other
  commits replace an unclaimed hand-off by compare-and-set and refuse a claimed one
  ``handoff_conflict`` (non-retryable).
* #4: the prune deletes an expired hand-off in one store transaction that re-reads its
  ``rev``, so a hand-off an export renewed or replaced since the query is kept.
* #2: a replay whose receipt write lost to another same-key replay never reports a
  worktree that is gone or changed: it records the one it built instead.

Real git in tmp dirs, as in tests/actors/test_handoff.py; races are injected through the
run-as seam (mid-build) or a store wrapper.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from culture_rules.actors.handoff import HANDOFFS_COLLECTION, prune_handoffs
from culture_rules.engine.actorport import COMPLETED
from tests.actors.test_gate import clock, git, store  # noqa: F401 - fixtures
from tests.actors.test_handoff import export, import_, world  # noqa: F401 - fixtures
from tests.actors.test_handoff_claims import (
    ALREADY,
    DAY_S,
    during_build,
    exported_once,
    later_export,
)

CONFLICT = "handoff_conflict"
FIRST, SECOND = "a", "b"  # two importers' idempotency keys
NOWHERE = "file:///nonexistent/culture-rules-handoff-test.git"


def stored(store, exported):  # noqa: F811
    return store.get(HANDOFFS_COLLECTION, exported["handoff_id"])


# ---- #3: an export replay keeps claims and receipts ----------------------------------------


def imported_then_reexported(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    first = import_(store, world, exported, head, key=FIRST)
    assert first.outcome == COMPLETED, first.error
    again = export(store, world, head)
    assert again.outcome == COMPLETED, again.error
    return head, exported, dict(first.output), dict(again.output)


def test_an_export_replay_keeps_another_importer_out(store, world):  # noqa: F811
    head, exported, _first, _again = imported_then_reexported(store, world)
    late = import_(store, world, exported, head, key=SECOND)
    assert late.error.startswith(ALREADY)


def test_an_export_replay_keeps_the_receipt_for_its_importers_replay(store, world):  # noqa: F811
    head, exported, first, _again = imported_then_reexported(store, world)
    import_(store, world, exported, head, key=SECOND)
    replay = import_(store, world, exported, head, key=FIRST)
    assert replay.outcome == COMPLETED, replay.error
    assert dict(replay.output) == first


def test_an_export_replay_reports_the_stored_pack(store, world):  # noqa: F811
    _head, exported, _first, again = imported_then_reexported(store, world)
    assert again == exported


def test_an_export_replay_renews_the_retention(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    world.clock.advance(DAY_S - 60)
    assert export(store, world, head).outcome == COMPLETED
    world.clock.advance(120)
    later_export(store, world, head)  # prunes what expired
    assert stored(store, exported) is not None


def test_an_export_of_other_commits_replaces_an_unclaimed_hand_off(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    res = export(store, world, head, start=world.up.base)
    assert res.outcome == COMPLETED, res.error
    assert stored(store, exported)["start_sha"] == world.up.base


def test_an_export_of_other_commits_refuses_a_claimed_hand_off(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    assert import_(store, world, exported, head, key=FIRST).outcome == COMPLETED
    res = export(store, world, head, start=world.up.base)
    assert res.error.startswith(CONFLICT)


def test_a_refused_export_is_not_retryable(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    import_(store, world, exported, head, key=FIRST)
    assert export(store, world, head, start=world.up.base).retryable is False


def test_a_refused_export_leaves_the_receipt(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    first = import_(store, world, exported, head, key=FIRST)
    export(store, world, head, start=world.up.base)
    assert stored(store, exported)["imported"]["worktree"] == first.output["worktree"]


# ---- #4: the prune never deletes a renewed or replaced hand-off ----------------------------


class RacedRemoval:
    """A store that runs ``race`` once, right before the prune removes an expired hand-off,
    whichever form the removal takes (a delete, or the transaction that deletes)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.race = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _fire(self) -> None:
        if self.race is not None:
            race, self.race = self.race, None
            race()

    def delete(self, collection, id):
        if collection == HANDOFFS_COLLECTION:
            self._fire()
        return self._inner.delete(collection, id)

    def transaction(self):
        self._fire()
        return self._inner.transaction()


def test_the_prune_keeps_a_hand_off_exported_again_before_its_removal(store, world):  # noqa: F811
    """Codex's interleaving: the prune selected an expired hand-off; before it deleted it
    by id, the export re-ran and wrote a fresh one in its place."""
    head, exported = exported_once(store, world)
    world.clock.advance(DAY_S + 60)
    racing = RacedRemoval(store)
    racing.race = lambda: export(store, world, head)
    prune_handoffs(racing, world.clock())
    assert stored(store, exported) is not None


def test_the_prune_still_removes_an_expired_hand_off(store, world):  # noqa: F811
    _head, exported = exported_once(store, world)
    world.clock.advance(DAY_S + 60)
    assert prune_handoffs(store, world.clock()) == 1
    assert stored(store, exported) is None


# ---- #2: a replay never reports a worktree that is gone ------------------------------------


def broken_receipt(store, world):  # noqa: F811
    """An import whose receipt's worktree then changed (so a replay rebuilds it)."""
    head, exported = exported_once(store, world)
    first = import_(store, world, exported, head)
    assert first.outcome == COMPLETED, first.error
    (Path(first.output["worktree"]) / "stray.txt").write_text("not the fixer's\n")
    return head, exported, first.output["worktree"]


def raced_replay(store, world):  # noqa: F811
    """Codex's interleaving: a replay removes the changed worktree and builds another;
    meanwhile a second same-key replay renews the claim (its ``rev``) and fails before
    recording, so the old receipt stands when the first one's receipt write loses."""
    head, exported, old = broken_receipt(store, world)
    during_build(world, lambda: import_(store, world, exported, head, url=NOWHERE))
    return import_(store, world, exported, head), old


def test_a_raced_replay_never_reports_the_removed_worktree(store, world):  # noqa: F811
    res, old = raced_replay(store, world)
    assert res.outcome == COMPLETED, res.error
    assert res.output["worktree"] != old


def test_a_raced_replay_reports_a_worktree_at_head_after(store, world):  # noqa: F811
    res, _old = raced_replay(store, world)
    assert git(Path(res.output["worktree"]), "rev-parse", "HEAD") == res.output["head_after"]


def test_a_raced_replays_worktree_is_the_receipts(store, world):  # noqa: F811
    res, _old = raced_replay(store, world)
    receipt = store.get(HANDOFFS_COLLECTION, store.find(HANDOFFS_COLLECTION)[0]["id"])
    assert receipt["imported"]["worktree"] == res.output["worktree"]


def test_a_replay_takes_a_competing_receipt_whose_worktree_is_intact(store, world):  # noqa: F811
    """The other side: a same-key replay that recorded an intact worktree first wins, and
    the late one reports it and removes its own."""
    head, exported, old = broken_receipt(store, world)
    shutil.rmtree(Path(old).parent)
    other = []
    during_build(world, lambda: other.append(import_(store, world, exported, head)))
    late = import_(store, world, exported, head)
    assert late.output["worktree"] == other[0].output["worktree"]
    assert len(world.workspaces()) == 1
