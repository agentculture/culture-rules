"""Unit tests for schema versions, the document envelope and the migration registry."""

from __future__ import annotations

import pytest

from culture_rules.store.migrations import MigrationError, MigrationRegistry
from culture_rules.store.port import SchemaDowngradeError, VersionSkewError
from culture_rules.store.versioning import Envelope, SchemaVersion, prepare_write

NOW = "2026-10-03T00:00:00.000000+00:00"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1.0", (1, 0)),
        ("2", (2, 0)),
        ("1.7.3", (1, 7)),
        (3, (3, 0)),
        (SchemaVersion(4, 1), (4, 1)),
    ],
)
def test_parse(value, expected):
    assert SchemaVersion.parse(value) == SchemaVersion(*expected)


@pytest.mark.parametrize("bad", ["", "x", "1.x", "-1.0", True, None, 1.5, [1], "1.0.0.0"])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        SchemaVersion.parse(bad)


def test_ordering_and_str():
    assert SchemaVersion(1, 9) < SchemaVersion(2, 0)
    assert SchemaVersion(1, 2) < SchemaVersion(1, 10)
    assert str(SchemaVersion(1, 2)) == "1.2"


def test_prepare_write_stamps_envelope_and_copies():
    given = {"id": "a", "nested": {"k": 1}}
    out = prepare_write(given, existing=None, node_version=SchemaVersion(1, 0), now=NOW)
    assert out["schema_version"] == "1.0"
    assert out["updated_at"] == NOW
    out["nested"]["k"] = 2
    assert given["nested"]["k"] == 1
    assert "schema_version" not in given


def test_prepare_write_guards():
    node = SchemaVersion(1, 2)
    with pytest.raises(VersionSkewError):
        prepare_write({"id": "a", "schema_version": "2.0"}, None, node, NOW)
    with pytest.raises(VersionSkewError):
        prepare_write({"id": "a"}, {"id": "a", "schema_version": "2.0"}, node, NOW)
    with pytest.raises(SchemaDowngradeError):
        prepare_write({"id": "a", "schema_version": "1.1"}, {"schema_version": "1.3"}, node, NOW)
    with pytest.raises(ValueError):
        prepare_write({"id": "a", "schema_version": "nope"}, None, node, NOW)
    with pytest.raises(ValueError):
        prepare_write(["not", "a", "mapping"], None, node, NOW)


def test_envelope_ignores_unknown_fields():
    env = Envelope.from_document(
        {"id": "a", "schema_version": "1.4", "updated_at": NOW, "whatever": 1, "_id": "x"}
    )
    assert env == Envelope(id="a", schema_version=SchemaVersion(1, 4), updated_at=NOW)


def test_envelope_requires_fields():
    with pytest.raises(ValueError):
        Envelope.from_document({"id": "a"})


def test_registry_is_forward_only():
    registry = MigrationRegistry()
    with pytest.raises(MigrationError):
        registry.register("rules", from_major=2, to_major=2, fn=lambda d: d)
    with pytest.raises(MigrationError):
        registry.register("rules", from_major=2, to_major=1, fn=lambda d: d)
    with pytest.raises(MigrationError):
        registry.register("rules", from_major=0, to_major=1, fn=lambda d: d)
    registry.register("rules", from_major=1, to_major=2, fn=lambda d: d)
    with pytest.raises(MigrationError):
        registry.register("rules", from_major=1, to_major=3, fn=lambda d: d)
    assert registry.collections() == ["rules"]
    assert [step.to_major for step in registry.chain("rules", 1, 2)] == [2]
    assert registry.chain("rules", 2, 2) == []
    with pytest.raises(MigrationError):
        registry.chain("rules", 1, 3)
