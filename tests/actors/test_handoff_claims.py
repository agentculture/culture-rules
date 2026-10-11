"""#39, Codex round 2 #2-#4: the hand-off import's claim, its durable receipt, and a prune
that reads no pack bodies.

* #2: two importers with different idempotency keys never both succeed. The import claims
  the hand-off (a compare-and-set on the document's ``rev``) before it builds anything, so
  the later one is refused ``handoff_already_imported``; one that lost its claim after
  building (the document was pruned and exported again meanwhile) removes its own
  workspace.
* #3: an import reports completion only once its receipt is durably recorded. A document
  pruned while the import builds fails the step ``handoff_receipt_lost`` (non-retryable)
  and removes the workspace; the claim renews the document's retention, so the prune never
  takes a document an import is building from.
* #4: the prune selects only expired ids (``expires_at``, a date: a MongoDB TTL index where
  the store has indexes), never loads pack bodies, and runs on import as well as export.

Real git in tmp dirs, as in tests/actors/test_handoff.py; races are injected through the
run-as seam (during ``git index-pack``, mid-build) or a store wrapper.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from culture_rules.actors.handoff import (
    HANDOFF_TTL_S,
    HANDOFFS_COLLECTION,
    HandoffImportPort,
)
from culture_rules.engine.actorport import InvocationContext
from culture_rules.store.memory import MemoryStore
from tests.actors.test_gate import clock, git, store  # noqa: F401 - fixtures
from tests.actors.test_handoff import DEADLINE, IMPORT_CTX, export, import_, world  # noqa: F401

ALREADY = "handoff_already_imported"
LOST = "handoff_receipt_lost"
DAY_S = 24 * 3600


def during_build(world, action):  # noqa: F811
    """Run ``action`` once, while an import indexes its pack (mid-build)."""
    runner = world.runner
    fired = []

    def hooked(argv, **kwargs):
        if "index-pack" in argv and not fired:
            fired.append(True)
            action()
        return runner(argv, **kwargs)

    hooked.calls = runner.calls  # type: ignore[attr-defined]
    world.runner = hooked
    return fired


def exported_once(store, world):  # noqa: F811
    head = world.fix()
    res = export(store, world, head)
    assert res.outcome == "completed", res.error
    return head, res.output


# ---- #2: concurrent importers ------------------------------------------------------------


def test_an_export_replay_mid_build_keeps_the_claim(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    during_build(world, lambda: export(store, world, head))
    res = import_(store, world, exported, head, key="a")
    assert res.outcome == "completed", res.error
    doc = store.get(HANDOFFS_COLLECTION, exported["handoff_id"])
    assert doc["imported"]["worktree"] == res.output["worktree"]


def test_an_importer_racing_another_mid_build_is_refused(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    other = []
    during_build(world, lambda: other.append(import_(store, world, exported, head, key="b")))
    first = import_(store, world, exported, head, key="a")
    assert first.outcome == "completed", first.error
    assert other[0].error.startswith(ALREADY)


def test_the_racing_importers_receipt_is_the_winners(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    during_build(world, lambda: import_(store, world, exported, head, key="b"))
    first = import_(store, world, exported, head, key="a")
    doc = store.get(HANDOFFS_COLLECTION, exported["handoff_id"])
    assert doc["imported"]["idempotency_key"] == "a"
    assert doc["imported"]["worktree"] == first.output["worktree"]


def test_the_winners_replay_still_succeeds_after_a_race(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    during_build(world, lambda: import_(store, world, exported, head, key="b"))
    first = import_(store, world, exported, head, key="a")
    again = import_(store, world, exported, head, key="a")
    assert again.outcome == "completed", again.error
    assert dict(again.output) == dict(first.output)


class ReadThenRace:
    """A store that runs ``race`` once, right after the import's first read of the
    hand-off: the other importer then runs whole between this one's read and its claim."""

    def __init__(self, inner: MemoryStore) -> None:
        self._inner = inner
        self.race = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get(self, collection, id):
        doc = self._inner.get(collection, id)
        if collection == HANDOFFS_COLLECTION and self.race is not None:
            race, self.race = self.race, None
            race()
        return doc


def test_an_importer_that_read_before_another_claimed_is_refused(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    racing = ReadThenRace(store)
    racing.race = lambda: import_(store, world, exported, head, key="a")
    late = import_(racing, world, exported, head, key="b")
    assert late.error.startswith(ALREADY)


def test_a_refused_racing_importer_builds_nothing(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    racing = ReadThenRace(store)
    racing.race = lambda: import_(store, world, exported, head, key="a")
    import_(racing, world, exported, head, key="b")
    assert len(world.workspaces()) == 1


def gone_and_exported_again(store, world, exported, head):  # noqa: F811
    """The hand-off is deleted (pruned) and the export re-runs: a fresh one, unclaimed.
    (An export replay over a live hand-off keeps its claim, Codex round 3 #3.)"""
    store.delete(HANDOFFS_COLLECTION, exported["handoff_id"])
    assert export(store, world, head).outcome == "completed"


def test_one_that_lost_its_claim_after_building_removes_its_workspace(store, world):  # noqa: F811
    """The hand-off was pruned and exported again mid-build (the claim gone) and another
    importer claimed and imported it: the first one's built workspace is removed."""
    head, exported = exported_once(store, world)

    def replaced_then_imported():
        gone_and_exported_again(store, world, exported, head)
        assert import_(store, world, exported, head, key="b").outcome == "completed"

    during_build(world, replaced_then_imported)
    late = import_(store, world, exported, head, key="a")
    assert late.error.startswith(ALREADY)
    assert len(world.workspaces()) == 1


def test_a_replaced_document_with_no_claim_is_reclaimed(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    during_build(world, lambda: gone_and_exported_again(store, world, exported, head))
    res = import_(store, world, exported, head, key="a")
    assert res.outcome == "completed", res.error
    doc = store.get(HANDOFFS_COLLECTION, exported["handoff_id"])
    assert doc["imported"]["worktree"] == res.output["worktree"]


# ---- #3: durable receipt -----------------------------------------------------------------


def test_a_document_deleted_mid_build_fails_the_import(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    during_build(world, lambda: store.delete(HANDOFFS_COLLECTION, exported["handoff_id"]))
    res = import_(store, world, exported, head)
    assert res.outcome == "failed"
    assert res.error.startswith(LOST)


def test_a_lost_receipt_is_not_retryable(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    during_build(world, lambda: store.delete(HANDOFFS_COLLECTION, exported["handoff_id"]))
    assert import_(store, world, exported, head).retryable is False


def test_a_lost_receipt_removes_the_built_workspace(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    during_build(world, lambda: store.delete(HANDOFFS_COLLECTION, exported["handoff_id"]))
    import_(store, world, exported, head)
    assert world.workspaces() == []


def later_export(store, world, head):  # noqa: F811
    ctx = InvocationContext("run-2", "fix[0]/export", "code", "spark", config={})
    return export(store, world, head, ctx=ctx)


def test_an_import_in_flight_is_not_pruned(store, world):  # noqa: F811
    """Codex's interleaving: the hand-off is nearly a day old when its import starts; an
    export elsewhere prunes while the import builds. The claim renewed its retention."""
    head, exported = exported_once(store, world)
    world.clock.advance(DAY_S - 60)

    def prune_meanwhile():
        world.clock.advance(120)
        later_export(store, world, head)

    during_build(world, prune_meanwhile)
    res = import_(store, world, exported, head)
    assert res.outcome == "completed", res.error
    assert store.get(HANDOFFS_COLLECTION, exported["handoff_id"])["imported"]["idempotency_key"]


def test_a_receipt_renews_retention_for_its_replay(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    world.clock.advance(DAY_S - 60)
    assert import_(store, world, exported, head).outcome == "completed"
    world.clock.advance(120)
    later_export(store, world, head)  # prunes what expired
    again = import_(store, world, exported, head)
    assert again.outcome == "completed", again.error


# ---- #4: the prune -----------------------------------------------------------------------


class Recording:
    """A store that refuses an unfiltered read of the hand-offs and records what the
    ranged reads ask for."""

    def __init__(self, inner: MemoryStore) -> None:
        self._inner = inner
        self.ranged: list[dict] = []
        self.indexes: list[tuple] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def find(self, collection, where=None, *, limit=None):
        assert collection != HANDOFFS_COLLECTION, "a full read of the hand-offs"
        return self._inner.find(collection, where, limit=limit)

    def find_range(self, collection, where, **kwargs):
        if collection == HANDOFFS_COLLECTION:
            self.ranged.append(dict(kwargs))
        return self._inner.find_range(collection, where, **kwargs)

    def ensure_index(self, collection, keys, **kwargs):
        self.indexes.append((collection, keys, kwargs))


def test_the_export_prunes_without_reading_pack_bodies(store, world):  # noqa: F811
    head = world.fix()
    recording = Recording(store)
    res = export(recording, world, head)
    assert res.outcome == "completed", res.error
    (ranged,) = recording.ranged
    assert "pack" not in ranged["fields"]


def test_the_prune_selects_by_expiry(store, world):  # noqa: F811
    head = world.fix()
    recording = Recording(store)
    export(recording, world, head)
    (ranged,) = recording.ranged
    assert ranged["field"] == "expires_at"


def test_the_prune_is_bounded(store, world):  # noqa: F811
    head = world.fix()
    recording = Recording(store)
    export(recording, world, head)
    (ranged,) = recording.ranged
    assert 0 < ranged["limit"] <= 1000


def test_an_export_stamps_a_date_expiry_a_day_out(store, world):  # noqa: F811
    _head, exported = exported_once(store, world)
    doc = store.get(HANDOFFS_COLLECTION, exported["handoff_id"])
    assert isinstance(doc["expires_at"], datetime)
    assert doc["expires_at"] == world.clock() + timedelta(seconds=HANDOFF_TTL_S)


def test_the_ports_declare_a_ttl_index_on_the_expiry(store, world):  # noqa: F811
    recording = Recording(store)
    HandoffImportPort(recording, run_as=world.runner)
    ((collection, keys, options),) = recording.indexes
    assert (collection, keys) == (HANDOFFS_COLLECTION, [("expires_at", 1)])
    assert options["ttl_seconds"] == 0


def test_an_import_prunes_expired_handoffs(store, world):  # noqa: F811
    head, old = exported_once(store, world)
    world.clock.advance(DAY_S - 60)
    fresh = later_export(store, world, head).output
    world.clock.advance(120)
    assert import_(store, world, fresh, head).outcome == "completed"
    assert store.get(HANDOFFS_COLLECTION, old["handoff_id"]) is None


def test_an_unexpired_handoff_survives_the_prune(store, world):  # noqa: F811
    head, old = exported_once(store, world)
    world.clock.advance(DAY_S - 60)
    later_export(store, world, head)
    assert store.get(HANDOFFS_COLLECTION, old["handoff_id"]) is not None


def test_the_prune_reads_no_pack_body_on_import(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    recording = Recording(store)
    import_(recording, world, exported, head)
    (ranged,) = recording.ranged
    assert "pack" not in ranged["fields"]


def test_find_range_returns_only_the_fields_asked_for():
    mem = MemoryStore()
    mem.put("c", {"id": "a", "t": 1, "big": "x" * 10})
    (doc,) = mem.find_range("c", None, field="t", upto=5, limit=10, fields=("id", "t"))
    assert doc == {"id": "a", "t": 1}


def test_an_import_whose_hand_off_was_pruned_is_missing(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    world.clock.advance(DAY_S + 60)
    later_export(store, world, head)
    res = import_(store, world, exported, head)
    assert res.error.startswith("handoff_missing")


def test_a_workspace_path_is_never_reported_for_a_failed_import(store, world):  # noqa: F811
    head, exported = exported_once(store, world)
    during_build(world, lambda: store.delete(HANDOFFS_COLLECTION, exported["handoff_id"]))
    res = import_(store, world, exported, head)
    assert "worktree" not in (res.output or {})
    assert not any(Path(p).exists() for p in world.workspaces())


def test_the_first_importers_replay_succeeds_after_a_read_race(store, world):  # noqa: F811
    """Codex's symptom: the later importer's receipt overwrote the first's, and replaying
    the first successful import failed ``handoff_already_imported``."""
    head, exported = exported_once(store, world)
    racing = ReadThenRace(store)
    first = []
    racing.race = lambda: first.append(import_(store, world, exported, head, key="a"))
    import_(racing, world, exported, head, key="b")
    again = import_(store, world, exported, head, key="a")
    assert again.outcome == "completed", again.error
    assert dict(again.output) == dict(first[0].output)
