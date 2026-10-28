"""Builtins keyset.filter and keyset.add over the keyset library, with MemoryStore."""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from culture_rules import keysets
from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.keyset import KeysetAddPort, KeysetFilterPort
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DAY = 86400
NORM = {"drop_query": ["utm_*"], "drop_fragment": True}


def cfg(**over):
    base = {"set": "seen", "key_field": "url", "normalise": NORM, "window_days": 7}
    base.update(over)
    return base


def ctx(config):
    return InvocationContext(
        run_id="r", step_id="s", kind="action", host="h", actor=None, config=config
    )


def run(port_cls, store, config, inp):
    return port_cls(store, clock=lambda: NOW).invoke(inp, "k", None, context=ctx(config))


def item(url, **extra):
    return {"url": url, **extra}


def seed(store, *urls, window_days=7):
    keysets.add_keys(store, "seen", urls, NOW, window_days * DAY)


# ---------------------------------------------------------------- shared validation


@pytest.mark.parametrize("port", [KeysetFilterPort, KeysetAddPort])
@pytest.mark.parametrize(
    "over",
    [
        {"set": None},
        {"set": "bad name"},
        {"key_field": ""},
        {"key_field": None},
        {"normalise": None},
        {"normalise": "x"},
        {"window_days": None},
        {"window_days": 0},
        {"window_days": -1},
        {"window_days": 1.5},
        {"window_days": True},
        {"window_days": "7"},
    ],
)
def test_a_missing_or_bad_config_value_fails_bad_params(port, over):
    config = cfg(max_keys=10, **over)
    res = run(port, MemoryStore(), config, {"items": [item("http://a/")]})
    assert res.outcome == FAILED and "bad_params" in res.error


@pytest.mark.parametrize("port", [KeysetFilterPort, KeysetAddPort])
def test_items_must_be_a_list(port):
    res = run(port, MemoryStore(), cfg(max_keys=10), {"items": "nope"})
    assert "bad_params" in res.error


@pytest.mark.parametrize("port", [KeysetFilterPort, KeysetAddPort])
def test_a_failure_is_final(port):
    res = run(port, MemoryStore(), cfg(max_keys=10), {"items": 3})
    assert res.retryable is False


def test_an_input_wins_over_config():
    store = MemoryStore()
    seed(store, "http://a/")
    res = run(
        KeysetFilterPort, store, cfg(set="other"), {"items": [item("http://a/")], "set": "seen"}
    )
    assert res.output["dropped"] == 1


def test_a_missing_max_keys_fails_bad_params():
    res = run(KeysetAddPort, MemoryStore(), cfg(), {"items": [item("http://a/")]})
    assert "bad_params" in res.error


@pytest.mark.parametrize("bad", [0, -1, True, "3", 2.5])
def test_a_bad_max_keys_fails_bad_params(bad):
    res = run(KeysetAddPort, MemoryStore(), cfg(max_keys=bad), {"items": []})
    assert "bad_params" in res.error


# ---------------------------------------------------------------- keyset.filter


def test_filter_drops_seen_items_by_normalised_key():
    store = MemoryStore()
    seed(store, "http://a/x")
    items = [item("http://a/x?utm_source=z#f", n=1), item("http://a/y", n=2)]
    res = run(KeysetFilterPort, store, cfg(), {"items": items})
    assert res.output["items"] == [item("http://a/y", n=2)]


def test_filter_counts_kept_dropped_and_keyless():
    store = MemoryStore()
    seed(store, "http://a/x")
    items = [item("http://a/x"), item("http://a/y"), {"title": "t"}, item("")]
    out = run(KeysetFilterPort, store, cfg(), {"items": items}).output
    assert (out["kept"], out["dropped"], out["keyless"]) == (3, 1, 2)


def test_filter_keeps_items_with_no_or_empty_key_field_in_order():
    items = [{"title": "t"}, item(""), item("http://a/y")]
    res = run(KeysetFilterPort, MemoryStore(), cfg(), {"items": items})
    assert res.output["items"] == items


def test_filter_keeps_an_item_whose_window_has_passed():
    store = MemoryStore()
    keysets.add_keys(store, "seen", ["http://a/x"], NOW - timedelta(days=10), 7 * DAY)
    res = run(KeysetFilterPort, store, cfg(), {"items": [item("http://a/x")]})
    assert res.output["dropped"] == 0


def test_filter_keeps_order_of_survivors():
    store = MemoryStore()
    seed(store, "http://a/2")
    items = [item(f"http://a/{i}") for i in (3, 2, 1)]
    out = run(KeysetFilterPort, store, cfg(), {"items": items}).output
    assert [i["url"] for i in out["items"]] == ["http://a/3", "http://a/1"]


def test_filter_keeps_a_list_of_lists_shape():
    store = MemoryStore()
    seed(store, "http://a/2")
    nested = [[item("http://a/1"), item("http://a/2")], [], [item("http://a/3")]]
    out = run(KeysetFilterPort, store, cfg(), {"items": nested}).output
    assert out["items"] == [[item("http://a/1")], [], [item("http://a/3")]]


def test_filter_counts_across_nested_lists():
    store = MemoryStore()
    seed(store, "http://a/2")
    nested = [[item("http://a/1"), item("http://a/2")], [item("http://a/3")]]
    out = run(KeysetFilterPort, store, cfg(), {"items": nested}).output
    assert (out["kept"], out["dropped"]) == (2, 1)


def test_filter_never_outputs_a_stored_key():
    store = MemoryStore()
    seed(store, "http://a/x")
    out = run(KeysetFilterPort, store, cfg(), {"items": [item("http://a/x?utm_a=1")]}).output
    assert set(out) == {"items", "kept", "dropped", "keyless"}


def test_filter_writes_nothing():
    store = MemoryStore()
    run(KeysetFilterPort, store, cfg(), {"items": [item("http://a/x")]})
    assert store.find(keysets.KEYSETS_COLLECTION) == []


def test_filter_refuses_a_non_mapping_item():
    res = run(KeysetFilterPort, MemoryStore(), cfg(), {"items": ["http://a/x"]})
    assert "bad_params" in res.error


def test_filter_refuses_a_mixed_flat_and_nested_list():
    res = run(KeysetFilterPort, MemoryStore(), cfg(), {"items": [item("http://a/"), []]})
    assert "bad_params" in res.error


def test_filter_creates_the_ttl_index_on_first_use():
    calls = []

    class Spy(MemoryStore):
        def ensure_index(self, *a, **k):
            calls.append(k.get("name"))

    run(KeysetFilterPort, Spy(), cfg(), {"items": []})
    assert calls == [keysets.EXPIRY_INDEX]


# ---------------------------------------------------------------- keyset.add


def test_add_stores_normalised_keys_with_an_expiry_of_now_plus_window():
    store = MemoryStore()
    run(KeysetAddPort, store, cfg(max_keys=10), {"items": [item("http://a/x?utm_s=1#f")]})
    rows = keysets.show_set(store, "seen", NOW)
    assert rows[0]["key"] == "http://a/x" and rows[0]["expires_at"] == NOW + timedelta(days=7)


def test_add_reports_counts():
    store = MemoryStore()
    seed(store, "http://a/x")
    items = [item("http://a/x"), item("http://a/y"), {"title": "keyless"}]
    out = run(KeysetAddPort, store, cfg(max_keys=10), {"items": items}).output
    assert out == {"added": 1, "already_present": 1}


def test_add_accepts_a_list_of_lists():
    store = MemoryStore()
    nested = [[item("http://a/1")], [item("http://a/2"), item("http://a/3")]]
    out = run(KeysetAddPort, store, cfg(max_keys=10), {"items": nested}).output
    assert out["added"] == 3


def test_add_over_max_keys_fails_bad_params():
    items = [item(f"http://a/{i}") for i in range(3)]
    res = run(KeysetAddPort, MemoryStore(), cfg(max_keys=2), {"items": items})
    assert res.outcome == FAILED and "bad_params" in res.error


def test_add_over_max_keys_writes_nothing():
    store = MemoryStore()
    items = [item(f"http://a/{i}") for i in range(3)]
    run(KeysetAddPort, store, cfg(max_keys=2), {"items": items})
    assert store.find(keysets.KEYSETS_COLLECTION) == []


def test_add_at_max_keys_succeeds():
    items = [item(f"http://a/{i}") for i in range(2)]
    res = run(KeysetAddPort, MemoryStore(), cfg(max_keys=2), {"items": items})
    assert res.outcome == COMPLETED


def test_a_retried_add_adds_nothing_twice():
    store = MemoryStore()
    inp = {"items": [item("http://a/x"), item("http://a/y")]}
    run(KeysetAddPort, store, cfg(max_keys=10), inp)
    again = run(KeysetAddPort, store, cfg(max_keys=10), inp).output
    assert again == {"added": 0, "already_present": 2}


def test_a_retried_add_leaves_one_document_per_key():
    store = MemoryStore()
    inp = {"items": [item("http://a/x")]}
    run(KeysetAddPort, store, cfg(max_keys=10), inp)
    run(KeysetAddPort, store, cfg(max_keys=10), inp)
    assert len(store.find(keysets.KEYSETS_COLLECTION)) == 1


def test_add_then_filter_drops_the_added_items():
    store = MemoryStore()
    items = [item("http://a/x")]
    run(KeysetAddPort, store, cfg(max_keys=10), {"items": items})
    out = run(KeysetFilterPort, store, cfg(), {"items": items}).output
    assert out["items"] == []


def test_add_creates_the_ttl_index_on_first_use():
    calls = []

    class Spy(MemoryStore):
        def ensure_index(self, *a, **k):
            calls.append(k.get("name"))

    run(KeysetAddPort, Spy(), cfg(max_keys=10), {"items": []})
    assert calls == [keysets.EXPIRY_INDEX]


def test_two_concurrent_adds_of_one_key_leave_one_document():
    store = MemoryStore()
    inp = {"items": [item("http://a/x")]}
    gate = threading.Barrier(2)

    def go():
        gate.wait()
        run(KeysetAddPort, store, cfg(max_keys=10), inp)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.find(keysets.KEYSETS_COLLECTION)) == 1


# ---------------------------------------------------------------- loop output shapes (t5)

# What a for_each loop hands a later step: its ``results`` port is one output object per
# iteration (``{}`` for an iteration whose body steps were all skipped); any other declared
# port is one value per iteration (None for a skipped one).
OBJECTS = [{"found": [item("http://a/1")]}, {}, {"found": [item("http://a/2")], "n": 3}]


def test_add_reads_items_from_loop_result_objects_by_items_field():
    out = run(
        KeysetAddPort, MemoryStore(), cfg(max_keys=10, items_field="found"), {"items": OBJECTS}
    ).output
    assert out["added"] == 2


def test_filter_keeps_the_loop_result_object_shape():
    store = MemoryStore()
    seed(store, "http://a/1")
    out = run(KeysetFilterPort, store, cfg(items_field="found"), {"items": OBJECTS}).output
    assert out["items"] == [{"found": []}, {}, {"found": [item("http://a/2")], "n": 3}]


def test_filter_counts_across_loop_result_objects():
    store = MemoryStore()
    seed(store, "http://a/1")
    out = run(KeysetFilterPort, store, cfg(items_field="found"), {"items": OBJECTS}).output
    assert (out["kept"], out["dropped"]) == (1, 1)


def test_items_field_takes_a_skipped_iteration_given_as_none():
    res = run(
        KeysetAddPort,
        MemoryStore(),
        cfg(max_keys=10, items_field="found"),
        {"items": [None, OBJECTS[0]]},
    )
    assert res.output["added"] == 1


@pytest.mark.parametrize("port", [KeysetFilterPort, KeysetAddPort])
@pytest.mark.parametrize("bad", ["", 3, True, ["found"]])
def test_a_bad_items_field_fails_bad_params(port, bad):
    res = run(port, MemoryStore(), cfg(max_keys=10, items_field=bad), {"items": OBJECTS})
    assert "bad_params" in res.error


@pytest.mark.parametrize("port", [KeysetFilterPort, KeysetAddPort])
@pytest.mark.parametrize("entry", [[item("http://a/")], "x", {"found": "x"}])
def test_items_field_refuses_an_entry_that_is_not_a_result_object(port, entry):
    res = run(port, MemoryStore(), cfg(max_keys=10, items_field="found"), {"items": [entry]})
    assert "bad_params" in res.error


def test_add_takes_a_per_port_list_with_a_skipped_iteration():
    nested = [[item("http://a/1")], None, [item("http://a/2")]]
    out = run(KeysetAddPort, MemoryStore(), cfg(max_keys=10), {"items": nested}).output
    assert out["added"] == 2


def test_filter_keeps_a_skipped_iteration_as_none():
    nested = [[item("http://a/1")], None]
    out = run(KeysetFilterPort, MemoryStore(), cfg(), {"items": nested}).output
    assert out["items"] == [[item("http://a/1")], None]


def test_an_item_mixed_with_a_skipped_iteration_is_refused():
    res = run(KeysetFilterPort, MemoryStore(), cfg(), {"items": [item("http://a/"), None]})
    assert "bad_params" in res.error


# ---------------------------------------------------------------- no flow in the code

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / "culture_rules/keysets.py", ROOT / "culture_rules/node/actions/keyset.py"]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_newspaper_utm_or_url_constant_in_the_code(path):
    text = path.read_text().lower()
    assert not re.search(r"newspaper|utm_(?!\*)|[\"']ref[\"']|https?://", text)
