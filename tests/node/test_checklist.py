"""library items t4: the checklist builtins over a stored list.

``checklist.merge`` folds a fresh extraction into a list (ids and done marks kept, c16/c29),
``checklist.set`` applies one flip (newest ``at`` wins, no flip lost under contention, c27),
``checklist.render`` writes the markdown the library gets and ``checklist.bind`` records what
was pushed so a stale replace cannot win (c28).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.checklist import (
    BIND_BUILTIN,
    CHECKLISTS_COLLECTION,
    MERGE_BUILTIN,
    RENDER_BUILTIN,
    SET_BUILTIN,
    ChecklistBindPort,
    ChecklistMergePort,
    ChecklistRenderPort,
    ChecklistSetPort,
    normalize,
)
from culture_rules.store.memory import MemoryStore

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
KEY = "weekly"
PREFIX = "todo"


def ctx(config=None):
    return InvocationContext("run-1", "s", "code", "spark", config=config or {})


@pytest.fixture
def store():
    return MemoryStore()


def merge(store, items, *, key=KEY, prefix=PREFIX, title=None, config=None):
    inputs = {"list_key": key, "prefix": prefix, "items": items}
    if title is not None:
        inputs["title"] = title
    return ChecklistMergePort(store).invoke(inputs, "k", DEADLINE, context=ctx(config))


def flip(store, item_id, done, at, by="operator", *, key=KEY):
    inputs = {"list_key": key, "id": item_id, "done": done, "at": at, "by": by}
    return ChecklistSetPort(store).invoke(inputs, "k", DEADLINE, context=ctx())


def render(store, key=KEY):
    return ChecklistRenderPort(store).invoke({"list_key": key}, "k", DEADLINE, context=ctx())


def bind(store, item_id, revision, key=KEY):
    inputs = {"list_key": key, "item_id": item_id, "revision": revision}
    return ChecklistBindPort(store).invoke(inputs, "k", DEADLINE, context=ctx())


def items_of(store, key=KEY):
    return {i["id"]: i for i in store.get(CHECKLISTS_COLLECTION, key)["items"]}


def ok(res):
    assert res.outcome == "completed", res.error
    return res.output


def test_builtin_names():
    assert (MERGE_BUILTIN, SET_BUILTIN, RENDER_BUILTIN, BIND_BUILTIN) == (
        "checklist.merge",
        "checklist.set",
        "checklist.render",
        "checklist.bind",
    )


def test_normalize_casefolds_collapses_space_and_strips_end_punctuation():
    assert normalize("  Buy   MILK!! ") == "buy milk"
    assert normalize("- Call   the  bank.") == "call the bank"
    assert normalize("Straße\tnow") == "strasse now"
    assert normalize("a-b, c") == "a-b, c"
    assert normalize("?!.") == ""


# ---------------------------------------------------------------- merge


def test_merge_creates_ids_from_prefix_key_and_index(store):
    out = ok(merge(store, ["Buy milk", "Call the bank"], title="This week"))
    assert out["added"] == 2
    assert out["kept"] == 0
    assert out["total"] == 2
    assert out["revision"] == 1
    assert out["empty"] is False
    assert set(items_of(store)) == {"todo.weekly.1", "todo.weekly.2"}
    assert store.get(CHECKLISTS_COLLECTION, KEY)["title"] == "This week"


def test_merge_keeps_id_and_done_mark_of_a_matching_item(store):
    ok(merge(store, ["Buy milk", "Call the bank"]))
    ok(flip(store, "todo.weekly.1", True, 100.0))
    out = ok(merge(store, ["  buy MILK. ", "call the bank"]))
    assert out["added"] == 0
    assert out["kept"] == 2
    assert out["total"] == 2
    item = items_of(store)["todo.weekly.1"]
    assert item["done"] is True
    assert item["at"] == 100.0
    assert item["by"] == "operator"
    # the original wording is kept: a match differs only by case, space or end punctuation
    assert item["text"] == "Buy milk"


def test_merge_without_changes_does_not_bump_the_revision(store):
    ok(merge(store, ["Buy milk"]))
    assert ok(merge(store, ["buy milk"]))["revision"] == 1


def test_merge_new_item_gets_next_unused_index_never_reused(store):
    ok(merge(store, ["a1", "a2", "a3"]))
    # upstream dropped a3; the stored list keeps it, and the new item is .4
    out = ok(merge(store, ["a1", "a2", "b"]))
    assert out["added"] == 1
    assert out["total"] == 4
    assert items_of(store)["todo.weekly.4"]["text"] == "b"
    assert "todo.weekly.3" in items_of(store)


def test_merge_index_never_reused_even_if_an_item_is_removed_from_the_list(store):
    ok(merge(store, ["a1", "a2", "a3"]))
    doc = store.get(CHECKLISTS_COLLECTION, KEY)
    store.update_if(
        CHECKLISTS_COLLECTION,
        KEY,
        {"rev": doc["rev"]},
        {"items": doc["items"][:2], "rev": doc["rev"] + 1},
    )
    ok(merge(store, ["c"]))
    ids = set(items_of(store))
    assert "todo.weekly.4" in ids
    assert "todo.weekly.3" not in ids
    assert store.get(CHECKLISTS_COLLECTION, KEY)["next_index"] == 5


def test_merge_keeps_an_existing_item_not_found_again(store):
    ok(merge(store, ["keep me", "and me"]))
    ok(flip(store, "todo.weekly.2", True, 5))
    out = ok(merge(store, []))
    assert out["kept"] == 0
    assert out["total"] == 2
    assert out["added"] == 0
    assert items_of(store)["todo.weekly.2"]["done"] is True


def test_merge_dedups_within_one_extraction_and_skips_blank_items(store):
    out = ok(merge(store, ["Milk", "milk!", "  ", "..."]))
    assert out["added"] == 1
    assert out["total"] == 1
    assert out["ignored"] == 3


def test_merge_empty_with_no_list_creates_nothing(store):
    out = ok(merge(store, []))
    assert out["empty"] is True
    assert out["revision"] == 0
    assert out["total"] == 0
    assert store.get(CHECKLISTS_COLLECTION, KEY) is None


def test_merge_keeps_the_stored_prefix(store):
    ok(merge(store, ["a"]))
    ok(merge(store, ["b"], prefix="other"))
    assert set(items_of(store)) == {"todo.weekly.1", "todo.weekly.2"}


def test_merge_takes_prefix_from_config(store):
    inputs = {"list_key": KEY, "items": ["a"]}
    res = ChecklistMergePort(store).invoke(inputs, "k", DEADLINE, context=ctx({"prefix": "gw"}))
    ok(res)
    assert set(items_of(store)) == {"gw.weekly.1"}


@pytest.mark.parametrize(
    "inputs",
    [
        {"list_key": "", "prefix": PREFIX, "items": []},
        {"list_key": "has space", "prefix": PREFIX, "items": []},
        {"list_key": KEY, "prefix": "", "items": []},
        {"list_key": KEY, "prefix": "a.b", "items": []},
        {"list_key": KEY, "prefix": PREFIX, "items": "milk"},
        {"list_key": KEY, "prefix": PREFIX, "items": [3]},
        {"list_key": KEY, "prefix": PREFIX, "items": [], "title": 5},
    ],
)
def test_merge_refuses_bad_input(store, inputs):
    res = ChecklistMergePort(store).invoke(inputs, "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.retryable is False
    assert res.error.startswith("bad_input")


# ---------------------------------------------------------------- set


def test_set_applies_done_with_at_and_by(store):
    ok(merge(store, ["a"]))
    out = ok(flip(store, "todo.weekly.1", True, 10.5, "reader"))
    assert out["applied"] is True
    assert out["reason"] is None
    assert out["revision"] == 2
    item = items_of(store)["todo.weekly.1"]
    assert (item["done"], item["at"], item["by"]) == (True, 10.5, "reader")


def test_set_applies_only_when_at_is_newer(store):
    ok(merge(store, ["a"]))
    ok(flip(store, "todo.weekly.1", True, 10))
    out = ok(flip(store, "todo.weekly.1", False, 10))
    assert out["applied"] is False
    assert out["reason"] == "stale"
    out = ok(flip(store, "todo.weekly.1", False, 9))
    assert out["applied"] is False
    assert out["reason"] == "stale"
    assert items_of(store)["todo.weekly.1"]["done"] is True
    out = ok(flip(store, "todo.weekly.1", False, 11, "assistant"))
    assert out["applied"] is True
    item = items_of(store)["todo.weekly.1"]
    assert (item["done"], item["at"], item["by"]) == (False, 11, "assistant")


def test_set_unknown_list_or_id_is_ignored_with_a_reason(store):
    out = ok(flip(store, "todo.weekly.1", True, 1))
    assert out["applied"] is False
    assert out["reason"] == "unknown_list"
    ok(merge(store, ["a"]))
    out = ok(flip(store, "todo.weekly.9", True, 1))
    assert out["applied"] is False
    assert out["reason"] == "unknown_id"


@pytest.mark.parametrize(
    "done,at,by",
    [
        ("yes", 1, "x"),
        (True, float("nan"), "x"),
        (True, "1", "x"),
        (True, True, "x"),
        (True, 1, ""),
    ],
)
def test_set_refuses_bad_input(store, done, at, by):
    ok(merge(store, ["a"]))
    res = flip(store, "todo.weekly.1", done, at, by)
    assert res.outcome == "failed"
    assert res.retryable is False
    assert res.error.startswith("bad_input")


@pytest.mark.parametrize("order", [(1, 2), (2, 1)])
def test_two_sets_on_different_items_both_survive_in_either_order(store, order):
    ok(merge(store, ["a", "b"]))
    for n in order:
        ok(flip(store, f"todo.weekly.{n}", True, 10 + n))
    items = items_of(store)
    assert items["todo.weekly.1"]["done"]
    assert items["todo.weekly.2"]["done"]


class _Interleaving:
    """A store whose first update_if lets another set land first (a real rev conflict)."""

    def __init__(self, inner, interloper):
        self._inner = inner
        self._interloper = interloper
        self.calls = 0

    def get(self, *a, **kw):
        return self._inner.get(*a, **kw)

    def update_if(self, *a, **kw):
        self.calls += 1
        if self._interloper is not None:
            run, self._interloper = self._interloper, None
            run()
        return self._inner.update_if(*a, **kw)


@pytest.mark.parametrize("first,second", [(1, 2), (2, 1)])
def test_concurrent_sets_on_one_list_both_land(store, first, second):
    ok(merge(store, ["a", "b"]))

    def interloper():
        ok(flip(store, f"todo.weekly.{second}", True, 20))

    racing = _Interleaving(store, interloper)
    inputs = {"list_key": KEY, "id": f"todo.weekly.{first}", "done": True, "at": 21, "by": "x"}
    out = ok(ChecklistSetPort(racing).invoke(inputs, "k", DEADLINE, context=ctx()))
    assert out["applied"] is True
    assert racing.calls == 2  # lost once, retried, won
    items = items_of(store)
    assert items["todo.weekly.1"]["done"]
    assert items["todo.weekly.2"]["done"]
    assert out["revision"] == 3


def test_set_gives_up_retryably_when_always_contended(store):
    ok(merge(store, ["a"]))

    class Losing:
        def get(self, *a, **kw):
            return store.get(*a, **kw)

        def update_if(self, *a, **kw):
            from culture_rules.store.port import UpdateResult

            return UpdateResult(False, None)

    inputs = {"list_key": KEY, "id": "todo.weekly.1", "done": True, "at": 1, "by": "x"}
    res = ChecklistSetPort(Losing()).invoke(inputs, "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.retryable is True
    assert res.error.startswith("checklist_contended")


# ---------------------------------------------------------------- render


def test_render_writes_todo_and_done_lines_under_a_title(store):
    ok(merge(store, ["Buy milk", "Call the bank"], title="This week"))
    ok(flip(store, "todo.weekly.2", True, 1_700_000_000, "assistant"))
    out = ok(render(store))
    assert out["markdown"] == (
        "# This week\n\n"
        "- [ ] Buy milk <!-- todo:todo.weekly.1 -->\n"
        "- [x] Call the bank"
        " <!-- todo:todo.weekly.2 done_at=2023-11-14T22:13:20Z by=assistant -->\n"
    )
    assert base64.b64decode(out["content_base64"]).decode("utf-8") == out["markdown"]
    assert out["revision"] == 2
    assert out["empty"] is False
    assert out["item_count"] == 2
    assert out["done_count"] == 1


def test_render_without_a_title_has_only_the_lines(store):
    ok(merge(store, ["a"]))
    assert ok(render(store))["markdown"] == "- [ ] a <!-- todo:todo.weekly.1 -->\n"


def test_render_of_a_missing_list_is_empty(store):
    out = ok(render(store))
    assert out["empty"] is True
    assert out["revision"] == 0
    assert out["item_count"] == 0
    assert out["markdown"] == ""


def test_render_of_a_list_with_no_items_is_empty(store):
    ok(merge(store, ["a"], title="T"))
    doc = store.get(CHECKLISTS_COLLECTION, KEY)
    store.update_if(CHECKLISTS_COLLECTION, KEY, {"rev": doc["rev"]}, {"items": [], "rev": 9})
    out = ok(render(store))
    assert out["empty"] is True
    assert out["item_count"] == 0
    assert out["revision"] == 1


def test_render_neutralizes_comment_breakers_in_item_text(store):
    ok(merge(store, ["evil --> x <!-- y", "two\nlines"]))
    md = ok(render(store))["markdown"]
    lines = md.splitlines()
    assert len(lines) == 2
    assert lines[0].count("-->") == 1
    assert lines[0].count("<!--") == 1
    assert lines[0].endswith("<!-- todo:todo.weekly.1 -->")
    assert lines[1].startswith("- [ ] two lines <!--")


# ---------------------------------------------------------------- bind


def test_bind_stores_item_and_pushed_revision(store):
    ok(merge(store, ["a"]))
    out = ok(bind(store, "lib-1", 1))
    assert out == {
        "bound": True,
        "stale": False,
        "pushed_revision": 1,
        "item_id": "lib-1",
        "revision": 1,
        "reason": None,
    }
    doc = store.get(CHECKLISTS_COLLECTION, KEY)
    assert doc["item_id"] == "lib-1"
    assert doc["pushed_revision"] == 1


def test_bind_only_if_newer(store):
    ok(merge(store, ["a"]))
    ok(flip(store, "todo.weekly.1", True, 1))  # revision 2
    ok(bind(store, "lib-2", 2))
    out = ok(bind(store, "lib-1", 1))
    assert out["bound"] is False
    assert out["pushed_revision"] == 2
    assert out["item_id"] == "lib-2"
    assert out["reason"] == "not_newer"
    assert out["stale"] is False
    assert store.get(CHECKLISTS_COLLECTION, KEY)["item_id"] == "lib-2"


def test_bind_reports_stale_when_the_list_moved_on(store):
    ok(merge(store, ["a", "b"]))
    rendered = ok(render(store))["revision"]
    ok(flip(store, "todo.weekly.1", True, 1))  # a flip lands between render and bind
    out = ok(bind(store, "lib-1", rendered))
    assert out["bound"] is True
    assert out["stale"] is True
    again = ok(render(store))["revision"]
    out = ok(bind(store, "lib-2", again))
    assert out["bound"] is True
    assert out["stale"] is False


def test_bind_again_with_the_same_push_is_idempotent(store):
    ok(merge(store, ["a"]))
    ok(bind(store, "lib-1", 1))
    out = ok(bind(store, "lib-1", 1))
    assert out["bound"] is True
    assert out["stale"] is False


def test_bind_unknown_list(store):
    out = ok(bind(store, "lib-1", 1))
    assert out["bound"] is False
    assert out["reason"] == "unknown_list"


@pytest.mark.parametrize("item_id,revision", [("", 1), ("x", -1), ("x", True), ("x", 1.5), (0, 1)])
def test_bind_refuses_bad_input(store, item_id, revision):
    ok(merge(store, ["a"]))
    res = bind(store, item_id, revision)
    assert res.outcome == "failed"
    assert res.retryable is False
    assert res.error.startswith("bad_input")


def test_bind_refuses_a_revision_ahead_of_the_list(store):
    ok(merge(store, ["a"]))
    res = bind(store, "x", 5)
    assert res.outcome == "failed"
    assert res.retryable is False


def test_merge_empty_with_a_title_and_no_list_creates_nothing(store):
    out = ok(merge(store, [], title="T"))
    assert out["empty"] is True
    assert store.get(CHECKLISTS_COLLECTION, KEY) is None


def test_a_set_landing_during_a_merge_survives(store):
    ok(merge(store, ["a"]))

    def interloper():
        ok(flip(store, "todo.weekly.1", True, 5))

    racing = _Interleaving(store, interloper)
    inputs = {"list_key": KEY, "prefix": PREFIX, "items": ["a", "b"]}
    out = ok(ChecklistMergePort(racing).invoke(inputs, "k", DEADLINE, context=ctx()))
    assert out["added"] == 1
    assert out["revision"] == 3
    assert items_of(store)["todo.weekly.1"]["done"] is True


def test_bind_takes_library_push_s_integer_item_id_and_keeps_it(store):
    """t7 integration: library.push returns item_id as an int; bind keeps it for a replace."""
    ok(merge(store, ["Buy milk"]))
    out = ok(bind(store, 42, 1))
    assert out["bound"] is True
    assert out["item_id"] == 42
    assert store.get(CHECKLISTS_COLLECTION, KEY)["item_id"] == 42


@pytest.mark.parametrize("bad", [0, -3, True, 2.5, ""])
def test_bind_refuses_a_bad_item_id(store, bad):
    ok(merge(store, ["Buy milk"]))
    result = bind(store, bad, 1)
    assert result.outcome == "failed"
    assert result.error.startswith("bad_input")


def test_render_returns_the_bound_item_so_the_next_push_replaces_it(store):
    ok(merge(store, ["Buy milk"]))
    assert ok(render(store))["item_id"] is None
    ok(bind(store, 42, 1))
    assert ok(render(store))["item_id"] == 42
