"""The built-ins ``text.join``, ``time.add`` and ``date.local``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.extensions.newspaper.issue import local_date
from culture_rules.extensions.transcript.port import _local_moment
from culture_rules.node.actions.value_steps import (
    DATE_LOCAL_BUILTIN,
    TEXT_JOIN_BUILTIN,
    TIME_ADD_BUILTIN,
    DateLocalPort,
    TextJoinPort,
    TimeAddPort,
)

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
JERUSALEM = "Asia/Jerusalem"
# Instants either side of DST changes (UTC), with the transition hour itself.
JERUSALEM_SPRING = [  # 2025-03-28 02:00 local jumps to 03:00 (23:59Z on the 27th is 01:59 +02)
    "2025-03-27T23:59:59Z",
    "2025-03-28T00:00:00Z",
    "2025-03-28T00:30:00Z",
    "2025-03-28T01:00:00Z",
]
JERUSALEM_AUTUMN = [  # 2025-10-26 02:00 +03 falls back to 01:00 +02
    "2025-10-25T21:59:59Z",
    "2025-10-25T22:00:00Z",
    "2025-10-25T22:30:00Z",
    "2025-10-25T23:00:00Z",
    "2025-10-25T23:30:00Z",
]
LONDON_SPRING = ["2025-03-30T00:59:59Z", "2025-03-30T01:00:00Z", "2025-03-30T01:30:00Z"]
LONDON_AUTUMN = ["2025-10-26T00:30:00Z", "2025-10-26T01:00:00Z", "2025-10-26T01:30:00Z"]
DAY_BOUNDARY = ["2025-03-27T21:59:59Z", "2025-03-27T22:00:00Z", "2025-10-25T20:59:59Z"]


def run(port, builtin, inputs, config=None):
    cfg = {"builtin": builtin, **(config or {})}
    ctx = InvocationContext("run-1", "wf[0]/v", "code", "spark2", config=cfg)
    return port.invoke(inputs, "k", DEADLINE, context=ctx)


def join(inputs, config=None):
    return run(TextJoinPort(), TEXT_JOIN_BUILTIN, inputs, config)


def add(inputs, config=None):
    return run(TimeAddPort(), TIME_ADD_BUILTIN, inputs, config)


def local(inputs, config=None):
    return run(DateLocalPort(), DATE_LOCAL_BUILTIN, inputs, config)


def refused(res):
    assert res.outcome == "failed"
    assert res.error.startswith("bad_params:"), res.error
    assert res.retryable is False


# ------------------------------------------------------------------ text.join


def test_join_uses_the_separator_in_order():
    assert join({"items": ["a", "b", "c"], "separator": ", "}).output["text"] == "a, b, c"


def test_join_counts_the_items():
    assert join({"items": ["a", "b"], "separator": "-"}).output["count"] == 2


def test_join_empty_list_is_empty_text():
    assert join({"items": [], "separator": ", "}).output["text"] == ""


def test_join_allows_an_empty_separator():
    assert join({"items": ["a", "b"], "separator": ""}).output["text"] == "ab"


def test_join_writes_numbers_with_str():
    assert join({"items": ["n", 1, 2.5], "separator": " "}).output["text"] == "n 1 2.5"


def test_join_takes_config_when_input_is_absent():
    res = join({}, {"items": ["x", "y"], "separator": "+"})
    assert res.output["text"] == "x+y"


def test_join_input_wins_over_config():
    res = join({"separator": "|"}, {"items": ["x", "y"], "separator": "+"})
    assert res.output["text"] == "x|y"


@pytest.mark.parametrize("bad", [True, None, ["a"], {"a": 1}])
def test_join_refuses_non_scalar_items(bad):
    refused(join({"items": ["a", bad], "separator": ","}))


def test_join_refuses_a_non_list():
    refused(join({"items": "abc", "separator": ","}))


def test_join_requires_a_string_separator():
    refused(join({"items": ["a"]}))


# ------------------------------------------------------------------ time.add


def test_add_hours_is_exact():
    res = add({"instant": "2025-01-01T00:00:00Z", "hours": 5})
    assert res.output["instant"] == "2025-01-01T05:00:00+00:00"


def test_add_sums_the_units():
    res = add(
        {"instant": "2025-01-01T00:00:00Z", "days": 1, "hours": 1, "minutes": 1, "seconds": 1}
    )
    assert res.output["instant"] == "2025-01-02T01:01:01+00:00"


def test_add_negative_moves_back():
    res = add({"instant": "2025-01-01T00:00:00Z", "minutes": -90})
    assert res.output["instant"] == "2024-12-31T22:30:00+00:00"


def test_add_fractions():
    res = add({"instant": "2025-01-01T00:00:00Z", "hours": 0.5})
    assert res.output["instant"] == "2025-01-01T00:30:00+00:00"


def test_add_result_is_utc_for_an_offset_input():
    res = add({"instant": "2025-01-01T12:00:00+02:00", "seconds": 0})
    assert res.output["instant"] == "2025-01-01T10:00:00+00:00"


def test_add_a_day_is_24_hours_across_dst():
    # 2025-03-28 00:00 Jerusalem (+02) plus one day is 24 h later: 01:00 local next day (+03).
    res = add({"instant": "2025-03-27T22:00:00Z", "days": 1})
    assert res.output["instant"] == "2025-03-28T22:00:00+00:00"


def test_add_a_day_is_not_the_same_wall_clock_across_dst():
    res = add({"instant": "2025-03-27T22:00:00Z", "days": 1})
    wall = datetime.fromisoformat(res.output["instant"]).astimezone(ZoneInfo(JERUSALEM))
    assert wall.strftime("%H:%M") == "01:00"


def test_add_takes_config():
    res = add({"instant": "2025-01-01T00:00:00Z"}, {"hours": 2})
    assert res.output["instant"] == "2025-01-01T02:00:00+00:00"


def test_add_needs_a_duration():
    refused(add({"instant": "2025-01-01T00:00:00Z"}))


def test_add_refuses_a_bool_duration():
    refused(add({"instant": "2025-01-01T00:00:00Z", "hours": True}))


def test_add_refuses_a_text_duration():
    refused(add({"instant": "2025-01-01T00:00:00Z", "hours": "2"}))


def test_add_refuses_an_instant_without_offset():
    refused(add({"instant": "2025-01-01T00:00:00", "hours": 1}))


def test_add_refuses_a_bad_instant():
    refused(add({"instant": "yesterday", "hours": 1}))


def test_add_refuses_a_missing_instant():
    refused(add({"hours": 1}))


def test_add_refuses_overflow():
    refused(add({"instant": "9999-12-31T00:00:00Z", "days": 400}))


# ------------------------------------------------------------------ date.local


def test_local_date_in_jerusalem():
    res = local({"instant": "2025-01-01T22:30:00Z", "tz": JERUSALEM, "format": "%Y-%m-%d"})
    assert res.output["text"] == "2025-01-02"


def test_local_time_of_day():
    res = local({"instant": "2025-01-01T22:30:00Z", "tz": JERUSALEM, "format": "%H%M"})
    assert res.output["text"] == "0030"


def test_local_iso_carries_the_offset():
    res = local({"instant": "2025-07-01T00:00:00Z", "tz": JERUSALEM, "format": "%H"})
    assert res.output["local"] == "2025-07-01T03:00:00+03:00"


def test_local_takes_config():
    res = local({"instant": "2025-01-01T00:00:00Z"}, {"tz": "UTC", "format": "%H%M"})
    assert res.output["text"] == "0000"


def test_local_transition_hour_spring_jerusalem():
    res = local({"instant": "2025-03-28T00:00:00Z", "tz": JERUSALEM, "format": "%H%M"})
    assert res.output["text"] == "0300"


def test_local_transition_hour_autumn_repeats_wall_time():
    first = local({"instant": "2025-10-25T22:30:00Z", "tz": JERUSALEM, "format": "%H%M %z"})
    second = local({"instant": "2025-10-25T23:30:00Z", "tz": JERUSALEM, "format": "%H%M %z"})
    assert (first.output["text"], second.output["text"]) == ("0130 +0300", "0130 +0200")


@pytest.mark.parametrize(
    "instant",
    JERUSALEM_SPRING + JERUSALEM_AUTUMN + DAY_BOUNDARY,
)
def test_local_date_matches_the_newspaper_in_jerusalem(instant):
    res = local({"instant": instant, "tz": JERUSALEM, "format": "%Y-%m-%d"})
    assert res.output["text"] == local_date(instant, JERUSALEM)


@pytest.mark.parametrize("instant", LONDON_SPRING + LONDON_AUTUMN)
def test_local_date_matches_the_newspaper_in_london(instant):
    res = local({"instant": instant, "tz": "Europe/London", "format": "%Y-%m-%d"})
    assert res.output["text"] == local_date(instant, "Europe/London")


@pytest.mark.parametrize(
    "instant",
    JERUSALEM_SPRING + JERUSALEM_AUTUMN + DAY_BOUNDARY,
)
def test_local_time_matches_the_transcript_title_time(instant):
    res = local({"instant": instant, "tz": JERUSALEM, "format": "%H%M"})
    assert res.output["text"] == f"{_local_moment(instant):%H%M}"


@pytest.mark.parametrize("instant", JERUSALEM_SPRING + JERUSALEM_AUTUMN)
def test_local_date_matches_the_transcript_date(instant):
    res = local({"instant": instant, "tz": JERUSALEM, "format": "%Y-%m-%d"})
    assert res.output["text"] == _local_moment(instant).date().isoformat()


def test_local_refuses_an_unknown_zone():
    refused(local({"instant": "2025-01-01T00:00:00Z", "tz": "Mars/Base", "format": "%H"}))


def test_local_refuses_a_missing_zone():
    refused(local({"instant": "2025-01-01T00:00:00Z", "format": "%H"}))


def test_local_refuses_a_missing_format():
    refused(local({"instant": "2025-01-01T00:00:00Z", "tz": JERUSALEM}))


def test_local_refuses_an_instant_without_offset():
    refused(local({"instant": "2025-01-01T00:00:00", "tz": JERUSALEM, "format": "%H"}))


def test_time_add_then_local_crosses_midnight_exactly():
    shifted = add({"instant": "2025-03-27T21:30:00Z", "minutes": 30}).output["instant"]
    res = local({"instant": shifted, "tz": JERUSALEM, "format": "%Y-%m-%d %H%M"})
    assert res.output["text"] == "2025-03-28 0000"


def test_add_exact_result_equals_timedelta():
    start = datetime(2025, 3, 27, 22, 0, tzinfo=UTC)
    res = add({"instant": start.isoformat(), "hours": 25})
    assert res.output["instant"] == (start + timedelta(hours=25)).isoformat()
