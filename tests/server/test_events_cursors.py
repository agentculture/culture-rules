"""The editor's change stream keeps its per-collection cursors at the feed's end.

``stream_changes`` polls every watched collection about five times a second. A cursor
that only moved on a returned change stayed at the last change of a quiet collection, so
every poll re-read the feed from there (on MongoDB, an oplog rescan per poll per viewer).
"""

from __future__ import annotations

import json

from culture_rules.server.events import _change_frames, _drain
from culture_rules.store.memory import MemoryStore


def test_a_quiet_collection_cursor_moves_to_the_feed_end():
    store = MemoryStore()
    start = store.head("runs")
    store.put("rules", {"id": "r1"})
    changes, end = _drain(store, "runs", start)
    assert changes == []
    cursors = {"runs": start}
    assert list(_change_frames(("runs",), [(changes, end)], cursors)) == []
    assert cursors["runs"] == end != start
    assert _drain(store, "runs", cursors["runs"]) == ([], end)


def test_cursors_reach_the_end_only_after_every_change_is_framed():
    store = MemoryStore()
    start = store.head("runs")
    store.put("runs", {"id": "a"})
    store.put("runs", {"id": "b"})
    store.put("rules", {"id": "r1"})
    changes, end = _drain(store, "runs", start)
    cursors = {"runs": start}
    frames = _change_frames(("runs",), [(changes, end)], cursors)
    first = next(frames)  # the stream stops here (max_events): no jump past "b"
    assert json.loads(first.split("id: ", 1)[1].split("\n", 1)[0]) == {"runs": changes[0].token}
    assert cursors["runs"] == changes[0].token
    assert len(list(frames)) == 1  # framing the rest moves the cursor on to the end
    assert cursors["runs"] == end
