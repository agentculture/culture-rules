"""``GET /runs?since=&cursor=``: runs finished since a moment, paged until exhausted (#45)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from culture_rules.engine.runs import RUNS_COLLECTION  # noqa: E402

T0 = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)


def put_run(store, rid: str, status: str, *, created: datetime, finished: datetime | None):
    store.put(
        RUNS_COLLECTION,
        {
            "id": rid,
            "status": status,
            "rule": {"id": "r1"},
            "workflow": {"id": "wf"},
            "steps": [],
            "created_at": created.isoformat(),
            "finished_at": finished.isoformat() if finished else None,
        },
    )


def pages(client, **query):
    seen, cursor = [], None
    for _ in range(100):
        params = {**query, **({"cursor": cursor} if cursor else {})}
        r = client.get("/runs", params=params)
        assert r.status_code == 200, r.text
        answer = r.json()
        seen += [item["id"] for item in answer["items"]]
        cursor = answer["next_cursor"]
        if cursor is None:
            return seen
    raise AssertionError("paging never ended")


def test_without_since_the_listing_is_unchanged(store, client):
    for i in range(3):
        put_run(store, f"r{i}", "failed", created=T0 + timedelta(minutes=i), finished=T0)
    answer = client.get("/runs", params={"limit": 2}).json()
    assert [item["id"] for item in answer["items"]] == ["r2", "r1"]  # newest created first
    assert answer["next_cursor"] is None


def test_since_pages_every_failed_run_finished_after_it(store, client):
    since = T0 + timedelta(hours=1)
    for i in range(1_050):  # created after since, finished after since
        moment = since + timedelta(seconds=i + 1)
        put_run(store, f"new{i:04d}", "failed", created=moment, finished=moment)
    put_run(store, "early", "failed", created=T0, finished=T0)  # finished before since
    # created long before since, finished after it: a created_at window would miss it
    put_run(store, "old-created", "failed", created=T0 - timedelta(days=3), finished=since)
    put_run(store, "ok", "succeeded", created=since, finished=since + timedelta(seconds=5))
    got = pages(client, status="failed", since=since.isoformat(), limit=1000)
    assert len(got) == 1_051
    assert "old-created" in got
    assert "early" not in got
    assert "ok" not in got
    assert len(set(got)) == len(got)


def test_since_orders_by_finish_time(store, client):
    put_run(store, "b", "failed", created=T0, finished=T0 + timedelta(seconds=2))
    put_run(
        store, "a", "failed", created=T0 + timedelta(hours=1), finished=T0 + timedelta(seconds=1)
    )
    assert pages(client, since=T0.isoformat(), limit=1) == ["a", "b"]


def test_a_bad_since_or_cursor_is_422(client):
    assert client.get("/runs", params={"since": "yesterday"}).status_code == 422
    assert client.get("/runs", params={"since": T0.isoformat(), "cursor": "x"}).status_code == 422
