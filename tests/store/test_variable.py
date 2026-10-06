"""Shared contract tests for the variable CRUD methods on every store.

Usage: subclass :class:`VariableContract` in a test module and implement
``make_store``.  Both ``TestMemoryStore`` in ``test_memory.py`` and
``TestMongoStore`` in ``test_mongo.py`` include it.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from culture_rules.model.variable import VALID_VARIABLE_NAME_RE
from culture_rules.store.memory import MemoryStore

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
