"""Shared StoragePort contract suite.

Every storage adapter (memory, mongo, ...) runs these exact tests. To bind an
adapter, subclass :class:`StoragePortContract` in a ``test_*.py`` module and
implement two hooks::

    from tests.store.contract import StoragePortContract

    class TestMyStore(StoragePortContract):
        def make_store(self, node_schema_version="1.0"):
            return MyStore(..., node_schema_version=node_schema_version)

        def open_peer(self, store, node_schema_version):
            # a second handle (another "node") on the SAME backing data
            return MyStore(..., node_schema_version=node_schema_version)

``make_store`` must return a store over EMPTY backing data each time it is
called (one per test). ``open_peer`` must return another handle that sees the
same data, possibly running a different node schema version - this is how the
rolling-upgrade guarantees (two node versions against one store) are tested.

The class name does not start with ``Test`` so pytest never collects the base
class on its own.
"""

from __future__ import annotations

import threading
from datetime import datetime

import pytest

from culture_rules.store.migrations import (
    BackupRequiredError,
    MigrationError,
    MigrationRegistry,
    migrate,
)
from culture_rules.store.port import (
    Change,
    DuplicateKeyError,
    SchemaDowngradeError,
    StoragePort,
    StoreError,
    UpdateResult,
    VersionSkewError,
)
from culture_rules.store.versioning import Envelope, SchemaVersion


class StoragePortContract:
    """Behavioural contract every StoragePort adapter must satisfy."""

    # ------------------------------------------------------------------ hooks

    def make_store(self, node_schema_version: str = "1.0") -> StoragePort:  # pragma: no cover
        raise NotImplementedError

    def open_peer(
        self, store: StoragePort, node_schema_version: str
    ) -> StoragePort:  # pragma: no cover
        raise NotImplementedError

    @pytest.fixture
    def store(self) -> StoragePort:
        return self.make_store()

    # ------------------------------------------------------------- protocol

    def test_satisfies_protocol(self, store):
        assert isinstance(store, StoragePort)
        assert str(store.node_schema_version) == "1.0"

    # ------------------------------------------------------------ documents

    def test_insert_then_get_round_trips(self, store):
        store.insert("rules", {"id": "r1", "name": "first", "nested": {"a": [1, 2]}})
        doc = store.get("rules", "r1")
        assert doc["id"] == "r1"
        assert doc["name"] == "first"
        assert doc["nested"] == {"a": [1, 2]}

    def test_get_missing_returns_none(self, store):
        assert store.get("rules", "nope") is None

    def test_insert_duplicate_raises(self, store):
        store.insert("rules", {"id": "r1"})
        with pytest.raises(DuplicateKeyError):
            store.insert("rules", {"id": "r1", "name": "again"})
        assert "name" not in store.get("rules", "r1")

    def test_duplicate_key_error_is_store_error(self):
        assert issubclass(DuplicateKeyError, StoreError)
        assert issubclass(VersionSkewError, StoreError)
        assert issubclass(SchemaDowngradeError, StoreError)

    def test_put_upserts_and_replaces(self, store):
        store.put("rules", {"id": "r1", "a": 1, "b": 2})
        store.put("rules", {"id": "r1", "a": 10})
        doc = store.get("rules", "r1")
        assert doc["a"] == 10
        assert "b" not in doc

    def test_delete(self, store):
        store.insert("rules", {"id": "r1"})
        assert store.delete("rules", "r1") is True
        assert store.get("rules", "r1") is None
        assert store.delete("rules", "r1") is False

    def test_collections_are_isolated(self, store):
        store.insert("rules", {"id": "x", "kind": "rule"})
        store.insert("workflows", {"id": "x", "kind": "workflow"})
        assert store.get("rules", "x")["kind"] == "rule"
        assert store.get("workflows", "x")["kind"] == "workflow"

    def test_find_equality_filter_ordered_by_id(self, store):
        for i, colour in [("c", "red"), ("a", "red"), ("b", "blue"), ("d", None)]:
            store.insert("things", {"id": i, "colour": colour})
        store.insert("things", {"id": "e"})
        assert [d["id"] for d in store.find("things")] == ["a", "b", "c", "d", "e"]
        assert [d["id"] for d in store.find("things", {"colour": "red"})] == ["a", "c"]
        # None matches a missing field as well as an explicit null.
        assert [d["id"] for d in store.find("things", {"colour": None})] == ["d", "e"]
        assert [d["id"] for d in store.find("things", limit=2)] == ["a", "b"]
        assert store.find("empty") == []

    def test_returned_documents_are_copies(self, store):
        given = {"id": "r1", "tags": ["a"]}
        store.insert("rules", given)
        given["tags"].append("mutated-input")
        doc = store.get("rules", "r1")
        doc["tags"].append("mutated-output")
        store.find("rules")[0]["tags"].append("mutated-find")
        assert store.get("rules", "r1")["tags"] == ["a"]

    def test_invalid_ids_and_collections_rejected(self, store):
        with pytest.raises(ValueError):
            store.insert("rules", {"name": "no id"})
        with pytest.raises(ValueError):
            store.insert("rules", {"id": ""})
        with pytest.raises(ValueError):
            store.insert("rules", {"id": 5})
        with pytest.raises(ValueError):
            store.insert("", {"id": "a"})

    # ------------------------------------------------------------- envelope

    def test_every_document_carries_envelope(self, store):
        store.insert("rules", {"id": "r1"})
        doc = store.get("rules", "r1")
        assert doc["schema_version"] == "1.0"  # stamped with the node's version
        datetime.fromisoformat(doc["updated_at"])  # ISO-8601
        env = Envelope.from_document(doc)
        assert env.id == "r1"
        assert env.schema_version == SchemaVersion(1, 0)

    def test_explicit_schema_version_is_kept(self, store):
        store.insert("rules", {"id": "r1", "schema_version": "1.3"})
        assert SchemaVersion.parse(store.get("rules", "r1")["schema_version"]) == (1, 3)

    def test_updated_at_is_refreshed_and_non_decreasing(self, store):
        store.insert("rules", {"id": "r1", "updated_at": "1999-01-01T00:00:00+00:00"})
        first = store.get("rules", "r1")["updated_at"]
        assert first != "1999-01-01T00:00:00+00:00"  # the store owns updated_at
        store.put("rules", {"id": "r1", "n": 2})
        second = store.get("rules", "r1")["updated_at"]
        store.update_if("rules", "r1", {"n": 2}, {"n": 3})
        third = store.get("rules", "r1")["updated_at"]
        assert datetime.fromisoformat(first) <= datetime.fromisoformat(second)
        assert datetime.fromisoformat(second) <= datetime.fromisoformat(third)

    def test_unknown_fields_are_preserved_and_ignored_by_readers(self, store):
        store.insert("rules", {"id": "r1", "future_field": {"x": 1}})
        doc = store.get("rules", "r1")
        assert doc["future_field"] == {"x": 1}
        env = Envelope.from_document(doc)  # does not choke on unknown fields
        assert env.id == "r1"

    def test_newer_major_write_raises_version_skew(self, store):
        with pytest.raises(VersionSkewError):
            store.insert("rules", {"id": "r1", "schema_version": "2.0"})
        with pytest.raises(VersionSkewError):
            store.put("rules", {"id": "r1", "schema_version": "2.0"})
        assert store.get("rules", "r1") is None

    def test_newer_minor_write_is_accepted(self, store):
        store.put("rules", {"id": "r1", "schema_version": "1.9"})
        assert SchemaVersion.parse(store.get("rules", "r1")["schema_version"]) == (1, 9)

    def test_writer_never_downgrades_schema_version(self, store):
        store.put("rules", {"id": "r1", "schema_version": "1.5"})
        with pytest.raises(SchemaDowngradeError):
            store.put("rules", {"id": "r1", "schema_version": "1.2"})
        with pytest.raises(SchemaDowngradeError):
            store.update_if("rules", "r1", {}, {"schema_version": "1.0"})
        # Omitting schema_version on put would stamp 1.0 < 1.5: also a downgrade.
        with pytest.raises(SchemaDowngradeError):
            store.put("rules", {"id": "r1", "x": 1})
        # A partial update that leaves schema_version alone keeps it.
        assert store.update_if("rules", "r1", {}, {"x": 2}).won
        assert SchemaVersion.parse(store.get("rules", "r1")["schema_version"]) == (1, 5)

    def test_update_if_cannot_change_id(self, store):
        store.insert("rules", {"id": "r1"})
        with pytest.raises(ValueError):
            store.update_if("rules", "r1", {}, {"id": "r2"})

    def test_rolling_upgrade_old_node_refuses_newer_major(self):
        old = self.make_store(node_schema_version="1.4")
        new = self.open_peer(old, node_schema_version="2.0")
        old.put("rules", {"id": "shared", "v": "old"})
        new.put("rules", {"id": "shared", "v": "new", "schema_version": "2.0"})
        # The old node can still read the newer document...
        before = old.get("rules", "shared")
        assert before["v"] == "new"
        # ...but refuses to write it, by any write path, instead of corrupting it.
        with pytest.raises(VersionSkewError):
            old.put("rules", {"id": "shared", "v": "clobber"})
        with pytest.raises(VersionSkewError):
            old.put("rules", {"id": "shared", "v": "clobber", "schema_version": "1.4"})
        with pytest.raises(VersionSkewError):
            old.update_if("rules", "shared", {}, {"v": "clobber"})
        assert new.get("rules", "shared") == before
        # Old-major documents remain writable by both nodes during the roll.
        old.put("rules", {"id": "other", "v": 1})
        assert new.update_if("rules", "other", {"v": 1}, {"v": 2}).won

    # --------------------------------------------------- conditional update

    def test_update_if_wins_when_expected_matches(self, store):
        store.insert("claims", {"id": "k", "owner": None, "n": 1})
        result = store.update_if("claims", "k", {"owner": None}, {"owner": "spark"})
        assert isinstance(result, UpdateResult)
        assert result.won is True
        assert result.document["owner"] == "spark"
        assert result.document["n"] == 1
        assert store.get("claims", "k")["owner"] == "spark"

    def test_update_if_loses_when_expected_differs(self, store):
        store.insert("claims", {"id": "k", "owner": "thor"})
        result = store.update_if("claims", "k", {"owner": None}, {"owner": "spark"})
        assert result.won is False
        assert result.document["owner"] == "thor"  # the current holder
        assert store.get("claims", "k")["owner"] == "thor"

    def test_update_if_expected_none_matches_missing_field(self, store):
        store.insert("claims", {"id": "k"})
        assert store.update_if("claims", "k", {"owner": None}, {"owner": "spark"}).won

    def test_update_if_multiple_expected_fields_all_must_match(self, store):
        store.insert("claims", {"id": "k", "owner": "a", "lease": 1})
        assert not store.update_if("claims", "k", {"owner": "a", "lease": 2}, {"owner": "b"}).won
        assert store.update_if("claims", "k", {"owner": "a", "lease": 1}, {"owner": "b"}).won

    def test_update_if_missing_document(self, store):
        result = store.update_if("claims", "k", {"owner": None}, {"owner": "spark"})
        assert result.won is False
        assert result.document is None
        assert store.get("claims", "k") is None

    def test_update_if_upsert(self, store):
        first = store.update_if("claims", "k", {"owner": None}, {"owner": "spark"}, upsert=True)
        assert first.won is True
        assert store.get("claims", "k")["owner"] == "spark"
        assert store.get("claims", "k")["schema_version"] == "1.0"
        second = store.update_if("claims", "k", {"owner": None}, {"owner": "thor"}, upsert=True)
        assert second.won is False
        assert store.get("claims", "k")["owner"] == "spark"

    def test_update_if_race_exactly_one_winner(self):
        base = self.make_store()
        handles = [base] + [self.open_peer(base, "1.0") for _ in range(3)]
        base.insert("claims", {"id": "k", "owner": None})
        contenders = 16
        barrier = threading.Barrier(contenders)
        results: list[tuple[int, UpdateResult]] = []
        lock = threading.Lock()

        def contend(i: int) -> None:
            barrier.wait()
            r = handles[i % len(handles)].update_if("claims", "k", {"owner": None}, {"owner": i})
            with lock:
                results.append((i, r))

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(contenders)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [i for i, r in results if r.won]
        assert len(winners) == 1
        assert base.get("claims", "k")["owner"] == winners[0]

    def test_upsert_race_exactly_one_winner(self):
        base = self.make_store()
        handles = [base] + [self.open_peer(base, "1.0") for _ in range(3)]
        contenders = 16
        barrier = threading.Barrier(contenders)
        wins: list[int] = []
        lock = threading.Lock()

        def contend(i: int) -> None:
            barrier.wait()
            r = handles[i % len(handles)].update_if(
                "claims", "new-key", {"owner": None}, {"owner": i}, upsert=True
            )
            if r.won:
                with lock:
                    wins.append(i)

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(contenders)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(wins) == 1
        assert base.get("claims", "new-key")["owner"] == wins[0]

    # ---------------------------------------------------------- change feed

    def test_change_feed_yields_writes_in_commit_order(self, store):
        start = store.head("rules")
        store.insert("rules", {"id": "a", "n": 1})
        store.put("rules", {"id": "b", "n": 1})
        store.update_if("rules", "a", {"n": 1}, {"n": 2})
        store.put("rules", {"id": "b", "n": 5})
        store.delete("rules", "b")
        changes = list(store.changes("rules", start))
        assert all(isinstance(c, Change) for c in changes)
        assert [(c.op, c.id) for c in changes] == [
            ("insert", "a"),
            ("insert", "b"),
            ("update", "a"),
            ("update", "b"),
            ("delete", "b"),
        ]
        assert all(c.collection == "rules" for c in changes)
        assert changes[0].document["n"] == 1
        assert changes[2].document["n"] == 2  # post-image at that commit
        assert changes[3].document["n"] == 5
        assert changes[4].document is None
        assert len({c.token for c in changes}) == len(changes)

    def test_change_feed_post_image_is_snapshot(self, store):
        start = store.head("rules")
        store.put("rules", {"id": "a", "n": 1})
        store.put("rules", {"id": "a", "n": 2})
        first, second = list(store.changes("rules", start))
        assert first.document["n"] == 1
        assert second.document["n"] == 2

    def test_change_feed_head_excludes_earlier_writes(self, store):
        store.put("rules", {"id": "before"})
        start = store.head("rules")
        store.put("rules", {"id": "after"})
        assert [c.id for c in store.changes("rules", start)] == ["after"]

    def test_change_feed_exactly_once_and_resume(self, store):
        start = store.head("rules")
        for i in range(5):
            store.put("rules", {"id": f"d{i}"})
        all_changes = list(store.changes("rules", start))
        assert [c.id for c in all_changes] == [f"d{i}" for i in range(5)]
        # Resuming from the last token yields nothing more (exactly once).
        assert list(store.changes("rules", all_changes[-1].token)) == []
        # Resuming from a middle token yields exactly the rest.
        rest = list(store.changes("rules", all_changes[1].token))
        assert [c.id for c in rest] == ["d2", "d3", "d4"]
        # New writes appear once after the last token.
        store.put("rules", {"id": "d5"})
        assert [c.id for c in store.changes("rules", all_changes[-1].token)] == ["d5"]

    def test_change_feed_is_per_collection(self, store):
        rules_start = store.head("rules")
        runs_start = store.head("runs")
        store.put("rules", {"id": "r"})
        store.put("runs", {"id": "x"})
        store.put("rules", {"id": "s"})
        assert [c.id for c in store.changes("rules", rules_start)] == ["r", "s"]
        assert [c.id for c in store.changes("runs", runs_start)] == ["x"]

    def test_change_feed_failed_writes_emit_nothing(self, store):
        store.insert("rules", {"id": "a", "owner": "x"})
        start = store.head("rules")
        with pytest.raises(DuplicateKeyError):
            store.insert("rules", {"id": "a"})
        with pytest.raises(VersionSkewError):
            store.put("rules", {"id": "b", "schema_version": "9.0"})
        assert not store.update_if("rules", "a", {"owner": None}, {"owner": "y"}).won
        assert store.delete("rules", "missing") is False
        assert list(store.changes("rules", start)) == []

    def test_change_feed_waits_for_a_write(self, store):
        start = store.head("rules")

        def later() -> None:
            store.put("rules", {"id": "late"})

        timer = threading.Timer(0.2, later)
        timer.start()
        try:
            changes = list(store.changes("rules", start, timeout=5.0))
        finally:
            timer.join()
        assert [c.id for c in changes] == ["late"]

    def test_change_feed_timeout_returns_empty(self, store):
        start = store.head("rules")
        assert list(store.changes("rules", start, timeout=0.05)) == []

    def test_change_feed_concurrent_writers_each_change_once_in_order(self):
        base = self.make_store()
        start = base.head("events")
        writers, per_writer = 4, 25
        handles = [base] + [self.open_peer(base, "1.0") for _ in range(writers - 1)]

        def write(w: int) -> None:
            for j in range(per_writer):
                handles[w].insert("events", {"id": f"w{w}-{j:03d}", "w": w, "j": j})

        threads = [threading.Thread(target=write, args=(w,)) for w in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seen: list[Change] = []
        token = start
        while True:  # drain in batches, resuming from the last token each time
            batch = list(base.changes("events", token))
            if not batch:
                break
            seen.extend(batch)
            token = batch[-1].token
        ids = [c.id for c in seen]
        assert len(ids) == writers * per_writer
        assert len(set(ids)) == len(ids)
        for w in range(writers):  # each writer's commits appear in its commit order
            mine = [c.document["j"] for c in seen if c.document["w"] == w]
            assert mine == list(range(per_writer))

    def test_cursor_persists_across_nodes(self, store):
        assert store.load_cursor("triggers", "events") is None
        start = store.head("events")
        store.put("events", {"id": "e1"})
        store.put("events", {"id": "e2"})
        first = next(iter(store.changes("events", start)))
        store.save_cursor("triggers", "events", first.token)
        # Another node (e.g. after failover) resumes from the persisted token.
        peer = self.open_peer(store, "1.0")
        token = peer.load_cursor("triggers", "events")
        assert token == first.token
        assert [c.id for c in peer.changes("events", token)] == ["e2"]
        # Cursors are keyed by consumer and collection.
        assert peer.load_cursor("other-consumer", "events") is None
        assert peer.load_cursor("triggers", "rules") is None

    # ---------------------------------------------------------- transactions

    def test_transaction_commits(self, store):
        start = store.head("rules")
        with store.transaction() as tx:
            tx.insert("rules", {"id": "a"})
            tx.put("rules", {"id": "b"})
            assert tx.get("rules", "a")["id"] == "a"  # reads its own writes
            assert [d["id"] for d in tx.find("rules")] == ["a", "b"]
            assert tx.update_if("rules", "a", {"x": None}, {"x": 1}).won
        assert store.get("rules", "a")["x"] == 1
        assert store.get("rules", "b") is not None
        assert [(c.op, c.id) for c in store.changes("rules", start)] == [
            ("insert", "a"),
            ("insert", "b"),
            ("update", "a"),
        ]

    def test_transaction_rolls_back_on_error(self, store):
        store.put("rules", {"id": "keep", "v": 1})
        store.put("rules", {"id": "gone", "v": 1})
        start = store.head("rules")

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            with store.transaction() as tx:
                tx.insert("rules", {"id": "new"})
                tx.put("rules", {"id": "keep", "v": 2})
                tx.update_if("rules", "keep", {"v": 2}, {"v": 3})
                tx.delete("rules", "gone")
                raise Boom()
        assert store.get("rules", "new") is None
        assert store.get("rules", "keep")["v"] == 1
        assert store.get("rules", "gone")["v"] == 1
        assert list(store.changes("rules", start)) == []

    def test_transaction_rolls_back_on_store_error(self, store):
        store.put("rules", {"id": "dup"})
        with pytest.raises(DuplicateKeyError):
            with store.transaction() as tx:
                tx.put("rules", {"id": "partial"})
                tx.insert("rules", {"id": "dup"})
        assert store.get("rules", "partial") is None

    def test_transaction_spans_collections(self, store):
        rules_start = store.head("rules")
        runs_start = store.head("runs")
        with store.transaction() as tx:
            tx.put("rules", {"id": "r"})
            tx.put("runs", {"id": "x"})
        assert [c.id for c in store.changes("rules", rules_start)] == ["r"]
        assert [c.id for c in store.changes("runs", runs_start)] == ["x"]

    # ------------------------------------------------------------ migrations

    @staticmethod
    def _registry() -> MigrationRegistry:
        registry = MigrationRegistry()

        @registry.register("rules", from_major=1, to_major=2)
        def _rename(doc):
            doc["title"] = doc.pop("name")
            return doc

        @registry.register("rules", from_major=2, to_major=3)
        def _tag(doc):
            doc.setdefault("tags", [])
            return doc

        return registry

    def test_migration_runs_after_backup_ok(self):
        old = self.make_store(node_schema_version="1.0")
        old.put("rules", {"id": "a", "name": "alpha"})
        old.put("rules", {"id": "b", "name": "beta"})
        new = self.open_peer(old, node_schema_version="3.0")
        seen_by_backup = []

        def backup():
            # The backup sees the store before any migration write.
            seen_by_backup.extend(d.get("name") for d in new.find("rules"))
            return True

        report = migrate(new, self._registry(), backup=backup)
        assert seen_by_backup == ["alpha", "beta"]
        assert report.migrated == {"rules": 2}
        doc = new.get("rules", "a")
        assert doc["title"] == "alpha" and "name" not in doc and doc["tags"] == []
        assert SchemaVersion.parse(doc["schema_version"]).major == 3
        # The old node now refuses to write the migrated documents.
        with pytest.raises(VersionSkewError):
            old.put("rules", {"id": "a", "name": "x"})

    @pytest.mark.parametrize("verdict", [False, None, {"ok": False}])
    def test_migration_refused_without_backup_ok(self, verdict):
        old = self.make_store(node_schema_version="1.0")
        old.put("rules", {"id": "a", "name": "alpha"})
        new = self.open_peer(old, node_schema_version="3.0")
        before = new.get("rules", "a")
        with pytest.raises(BackupRequiredError):
            migrate(new, self._registry(), backup=lambda: verdict)
        assert new.get("rules", "a") == before

    def test_migration_refused_when_backup_raises(self):
        old = self.make_store(node_schema_version="1.0")
        old.put("rules", {"id": "a", "name": "alpha"})
        new = self.open_peer(old, node_schema_version="3.0")

        def broken():
            raise OSError("s3 unreachable")

        with pytest.raises(BackupRequiredError):
            migrate(new, self._registry(), backup=broken)
        assert new.get("rules", "a")["name"] == "alpha"

    def test_migration_target_beyond_node_raises_skew(self):
        store = self.make_store(node_schema_version="2.0")
        store.put("rules", {"id": "a", "name": "alpha"})
        with pytest.raises(VersionSkewError):
            migrate(store, self._registry(), backup=lambda: True, target_major=3)

    def test_migration_partial_target_and_idempotent(self):
        old = self.make_store(node_schema_version="1.0")
        old.put("rules", {"id": "a", "name": "alpha"})
        new = self.open_peer(old, node_schema_version="3.0")
        report = migrate(new, self._registry(), backup=lambda: True, target_major=2)
        assert report.migrated == {"rules": 1}
        doc = new.get("rules", "a")
        assert SchemaVersion.parse(doc["schema_version"]).major == 2
        assert "tags" not in doc
        again = migrate(new, self._registry(), backup=lambda: True, target_major=2)
        assert again.migrated == {"rules": 0}

    def test_migration_gap_fails_before_backup(self):
        registry = MigrationRegistry()
        registry.register("rules", from_major=2, to_major=3, fn=lambda d: d)
        old = self.make_store(node_schema_version="1.0")
        old.put("rules", {"id": "a"})
        new = self.open_peer(old, node_schema_version="3.0")
        calls = []
        with pytest.raises(MigrationError):
            migrate(new, registry, backup=lambda: calls.append(1) or True)
        assert calls == []
        assert SchemaVersion.parse(new.get("rules", "a")["schema_version"]).major == 1
