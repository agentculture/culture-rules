"""Criterion 2 (c70/h52): a rule fires within 5 s of a matching event, and the SSE stream
of an API instance shows each step within 2 s of that step completing (real clocks)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from culture_rules.engine.runs import ACTION_STEP
from culture_rules.model.placement import Placement
from tests.events.fakes import envelope
from tests.multihost.harness import HOSTS, event_rule, run_id_for, three_host_workflow

FIRE_WITHIN_S = 5.0
SSE_WITHIN_S = 2.0


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def test_rule_fires_within_5s_and_sse_shows_each_step_within_2s(cluster):
    cluster.define(three_host_workflow(), event_rule("timed", "wf", Placement(machine="thor")))
    cluster.start(*HOSTS)
    api = cluster.serve("spark2")  # the stream is read from a host that runs no rule
    run_id = run_id_for("timed", "evt_42")
    with api.sse(
        ["runs"], until=lambda doc: doc.get("status") == "succeeded", run_id=run_id
    ) as sse:
        published = datetime.now(UTC)
        t0 = time.monotonic()
        cluster.publish(envelope(42))
        run = cluster.wait_run("timed", "evt_42", timeout=30)
        sse.wait(timeout=30)

    assert run["status"] == "succeeded"
    fired_after = (_at(run["created_at"]) - published).total_seconds()
    assert 0 <= fired_after < FIRE_WITHIN_S, f"fired {fired_after:.3f}s after the event"
    assert time.monotonic() - t0 < 30

    lags = {}
    for key in ("s1", "s2", "s3", ACTION_STEP):
        completed = next(
            _at(h["at"]) for h in run["history"] if h["step"] == key and h["event"] == "succeeded"
        )
        shown = sse.first_seen(run_id, key, "succeeded")
        assert shown is not None, f"SSE never showed {key} succeeded"
        lag = (shown - completed).total_seconds()
        assert lag < SSE_WITHIN_S, f"SSE showed {key} {lag:.3f}s after it completed"
        lags[key] = round(lag, 3)
    print(f"[{cluster.backend}] fired after {fired_after:.3f}s; SSE lag per step {lags}")
