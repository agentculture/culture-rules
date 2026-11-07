"""Shared contract tests for the variable CRUD methods on every store.

Usage: subclass :class:`VariableContract` in a test module and implement
``make_store``.  Both ``TestMemoryStore`` in ``test_memory.py`` and
``TestMongoStore`` in ``test_mongo.py`` include it.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from culture_rules.engine.variables import defined_variables, variable_values
from culture_rules.model.variable import VALID_VARIABLE_NAME_RE
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import VARIABLE_GUARDS_COLLECTION, VariableVersionConflict

# ----------------------------------------------------------------------- helpers


def _make_store() -> MemoryStore:  # pragma: no cover
    """Implement in the subclass."""
    raise NotImplementedError


class VariableContract:
    """Behavioural contract every store adapter's variable methods must satisfy."""

    # ------------------------------------------------------------------ hook

    def make_store(self) -> MemoryStore:  # pragma: no cover
        raise NotImplementedError

    @pytest.fixture
    def store(self) -> MemoryStore:
        return self.make_store()

    # --------------------------------------------------------------- put / get

    def test_put_creates_version_1(self, store):
        doc = store.put_variable("a", "hello", updated_by="me")
        assert doc["version"] == 1
        assert doc["value"] == "hello"
        assert doc["updated_by"] == "me"
        assert doc["name"] == "a"
        assert doc["id"] == "a"
        assert datetime.fromisoformat(doc["updated_at"])  # valid ISO

    def test_put_appends_version_2(self, store):
        store.put_variable("a", "hello", updated_by="me")
        doc2 = store.put_variable("a", "world", updated_by="me")
        assert doc2["version"] == 2
        v1 = store.get_variable_version("a", 1)
        assert v1["version"] == 1
        assert v1["value"] == "hello"
        # get_variable returns latest
        latest = store.get_variable("a")
        assert latest["version"] == 2
        assert latest["value"] == "world"

    def test_get_variable_returns_latest(self, store):
        store.put_variable("a", "v1", updated_by="me")
        store.put_variable("a", "v2", updated_by="me")
        store.put_variable("a", "v3", updated_by="me")
        latest = store.get_variable("a")
        assert latest["version"] == 3
        assert latest["value"] == "v3"

    def test_versions_are_contiguous_from_1(self, store):
        for i in range(1, 6):
            store.put_variable("a", f"v{i}", updated_by="me")
        for i in range(1, 6):
            v = store.get_variable_version("a", i)
            assert v is not None
            assert v["version"] == i
            assert v["value"] == f"v{i}"

    def test_get_variable_returns_none_for_missing(self, store):
        assert store.get_variable("nope") is None

    def test_get_variable_version_returns_none_for_missing_name(self, store):
        assert store.get_variable_version("nope", 1) is None

    def test_get_variable_version_returns_none_for_missing_version(self, store):
        store.put_variable("a", "v1", updated_by="me")
        assert store.get_variable_version("a", 99) is None

    # -------------------------------------------------------------- list

    def test_list_variables(self, store):
        store.put_variable("b", 2, updated_by="me")
        store.put_variable("a", 1, updated_by="me")
        store.put_variable("c", 3, updated_by="me")
        all_vars = store.list_variables()
        assert len(all_vars) == 3
        assert [v["name"] for v in all_vars] == ["a", "b", "c"]
        # Updates update the entry in list
        store.put_variable("a", 10, updated_by="me")
        all_vars2 = store.list_variables()
        a_latest = [v for v in all_vars2 if v["name"] == "a"][0]
        assert a_latest["value"] == 10

    # ---------------------------------------------------------- name validation

    def test_name_validation_accepts_valid(self, store):
        store.put_variable("a", 1, updated_by="me")
        store.put_variable("foo_bar", 2, updated_by="me")
        store.put_variable("a" + "1" * 62, 3, updated_by="me")  # 64 chars total
        assert store.get_variable("a")["version"] == 1
        assert store.get_variable("foo_bar")["version"] == 1
        assert store.get_variable("a" + "1" * 62)["version"] == 1

    @pytest.mark.parametrize(
        "bad_name",
        ["Bad", "bad!", "1abc", "ab-c", "", "x" * 65],
    )
    def test_name_validation_refuses_invalid(self, store, bad_name):
        with pytest.raises(ValueError, match="invalid variable name"):
            store.put_variable(bad_name, 1, updated_by="me")

    # -------------------------------------------------------- value validation

    def test_value_validation_accepts_scalars(self, store):
        store.put_variable("s", "str", updated_by="me")
        store.put_variable("i", 42, updated_by="me")
        store.put_variable("f", 3.14, updated_by="me")
        store.put_variable("b", True, updated_by="me")
        store.put_variable("n", None, updated_by="me")
        assert store.get_variable("s")["value"] == "str"
        assert store.get_variable("i")["value"] == 42
        assert store.get_variable("f")["value"] == 3.14
        assert store.get_variable("b")["value"] is True
        assert store.get_variable("n")["value"] is None

    def test_value_validation_accepts_list(self, store):
        store.put_variable("l", [1, 2, 3], updated_by="me")
        assert store.get_variable("l")["value"] == [1, 2, 3]

    def test_value_validation_refuses_dict(self, store):
        with pytest.raises(ValueError, match="JSON scalar or list"):
            store.put_variable("d", {"key": "val"}, updated_by="me")

    def test_value_validation_refuses_tuple(self, store):
        with pytest.raises(ValueError, match="JSON scalar or list"):
            store.put_variable("t", (1, 2), updated_by="me")

    def test_value_validation_refuses_nested_list(self, store):
        with pytest.raises(ValueError, match="JSON scalar or list"):
            store.put_variable("nl", [1, [2]], updated_by="me")

    def test_value_validation_refuses_list_with_dict(self, store):
        with pytest.raises(ValueError, match="JSON scalar or list"):
            store.put_variable("ld", ["x", {"k": 1}], updated_by="me")

    def test_value_validation_refuses_nan(self, store):
        with pytest.raises(ValueError):
            store.put_variable("nan", float("nan"), updated_by="me")

    def test_value_validation_accepts_none_list(self, store):
        store.put_variable("nl", [1, None, "x"], updated_by="me")
        assert store.get_variable("nl")["value"] == [1, None, "x"]

    def test_value_validation_accepts_empty_list(self, store):
        store.put_variable("el", [], updated_by="me")
        assert store.get_variable("el")["value"] == []

    # ----------------------------------------------------------- description

    def test_description_is_stored(self, store):
        store.put_variable("a", "v", updated_by="me", description="a var")
        v = store.get_variable("a")
        assert v["description"] == "a var"

    def test_description_can_be_none(self, store):
        store.put_variable("a", "v", updated_by="me")
        assert store.get_variable("a")["description"] is None

    # ---------------------------------------------------------- regex constant

    def test_name_regex_matches_expected(self):
        """The regex matches exactly what the spec says."""
        assert VALID_VARIABLE_NAME_RE.fullmatch("a")
        assert VALID_VARIABLE_NAME_RE.fullmatch("foo_bar_1")
        assert VALID_VARIABLE_NAME_RE.fullmatch("a" * 64)
        assert VALID_VARIABLE_NAME_RE.fullmatch("z_9")
        # And rejects the invalid ones.
        assert not VALID_VARIABLE_NAME_RE.fullmatch("Bad")
        assert not VALID_VARIABLE_NAME_RE.fullmatch("bad!")
        assert not VALID_VARIABLE_NAME_RE.fullmatch("1abc")
        assert not VALID_VARIABLE_NAME_RE.fullmatch("ab-c")
        assert not VALID_VARIABLE_NAME_RE.fullmatch("")
        assert not VALID_VARIABLE_NAME_RE.fullmatch("a" * 65)

    # ---------------------------------------------------------- newline in name

    def test_name_rejects_trailing_newline(self, store):
        """Name with a trailing newline must be rejected (regression test)."""
        with pytest.raises(ValueError, match="invalid variable name"):
            store.put_variable("a\n", 1, updated_by="me")

    # ---------------------------------------------------------- delete (a tombstone version)

    def test_delete_appends_a_tombstone_and_hides_the_variable(self, store):
        store.put_variable("gone", ["a"], updated_by="me", description="d")
        store.put_variable("kept", 1, updated_by="me")
        doc = store.delete_variable("gone", deleted_by="root", expected_version=1)
        assert doc["version"] == 2
        assert doc["deleted"] is True
        assert doc["updated_by"] == "root"
        assert store.get_variable("gone") is None
        assert [v["name"] for v in store.list_variables()] == ["kept"]

    def test_delete_keeps_every_version_readable(self, store):
        store.put_variable("gone", ["a"], updated_by="me")
        store.delete_variable("gone", deleted_by="root", expected_version=1)
        assert store.get_variable_version("gone", 1)["value"] == ["a"]
        assert store.get_variable_version("gone", 2)["deleted"] is True

    def test_variable_versions_lists_every_version_oldest_first(self, store):
        store.put_variable("v", 1, updated_by="me")
        store.put_variable("v", 2, updated_by="me")
        store.delete_variable("v", deleted_by="root", expected_version=2)
        assert [d["version"] for d in store.variable_versions("v")] == [1, 2, 3]

    def test_variable_versions_ends_with_the_tombstone(self, store):
        store.put_variable("v", 1, updated_by="me")
        store.delete_variable("v", deleted_by="root", expected_version=1)
        assert store.variable_versions("v")[-1]["deleted"] is True

    def test_variable_versions_of_a_missing_variable_is_none(self, store):
        assert store.variable_versions("ghost") is None

    def test_a_live_version_carries_no_deleted_flag(self, store):
        store.put_variable("live", 1, updated_by="me")
        assert "deleted" not in store.get_variable("live")

    def test_delete_of_a_missing_or_deleted_variable_returns_none(self, store):
        assert store.delete_variable("ghost", deleted_by="root", expected_version=1) is None
        store.put_variable("gone", 1, updated_by="me")
        store.delete_variable("gone", deleted_by="root", expected_version=1)
        assert store.delete_variable("gone", deleted_by="root", expected_version=2) is None

    def test_delete_is_a_compare_and_set_on_the_version(self, store):
        store.put_variable("cas", 1, updated_by="me")
        store.put_variable("cas", 2, updated_by="me")
        with pytest.raises(VariableVersionConflict):
            store.delete_variable("cas", deleted_by="root", expected_version=1)
        assert store.get_variable("cas")["value"] == 2

    def test_delete_calls_on_write_inside_its_transaction(self, store):
        store.put_variable("gone", 7, updated_by="me")
        seen = []

        def on_write(ops, before, after):
            ops.insert("audit_probe", {"id": "p", "before": before["version"]})
            seen.append((before["value"], after["version"]))

        store.delete_variable("gone", deleted_by="root", expected_version=1, on_write=on_write)
        # a transient conflict re-runs the whole transaction, hook included; one commit lands
        assert seen[-1] == (7, 2)
        assert store.get("audit_probe", "p")["before"] == 1

    def test_delete_writes_the_variable_guard(self, store):
        store.put_variable("gone", 7, updated_by="me")
        store.delete_variable("gone", deleted_by="root", expected_version=1)
        guard = store.get(VARIABLE_GUARDS_COLLECTION, "gone")
        assert guard["by"] == "variables.delete"
        assert datetime.fromisoformat(guard["at"])

    def test_a_delete_refused_by_on_write_writes_nothing(self, store):
        store.put_variable("kept", 7, updated_by="me")
        store.put(VARIABLE_GUARDS_COLLECTION, {"id": "kept", "at": "seed", "by": "seed"})

        def refuse(ops, _before, _after):
            ops.insert("audit_probe", {"id": "p"})
            raise LookupError("an enabled referrer appeared")

        with pytest.raises(LookupError):
            store.delete_variable("kept", deleted_by="root", expected_version=1, on_write=refuse)
        assert store.get_variable("kept")["version"] == 1
        assert store.get_variable_version("kept", 2) is None
        assert store.get("audit_probe", "p") is None
        assert store.get(VARIABLE_GUARDS_COLLECTION, "kept")["by"] == "seed"

    def test_a_put_after_delete_continues_the_versions(self, store):
        store.put_variable("back", 1, updated_by="me")
        store.delete_variable("back", deleted_by="root", expected_version=1)
        doc = store.put_variable("back", 3, updated_by="me")
        assert doc["version"] == 3
        assert store.get_variable("back")["value"] == 3

    def test_the_engine_reads_a_deleted_variable_as_undefined(self, store):
        store.put_variable("gone", ["a"], updated_by="me")
        store.put_variable("kept", 1, updated_by="me")
        store.delete_variable("gone", deleted_by="root", expected_version=1)
        assert variable_values(store, ["gone", "kept"]) == {"kept": 1}
        assert defined_variables(store) == {"kept"}
