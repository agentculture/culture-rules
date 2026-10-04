"""Tests for the stdlib cron parser and DST-safe slot iteration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from culture_rules.engine.cron import Cron, parse

UTC = timezone.utc


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:  # pragma: no cover - tzdata missing
        pytest.skip(f"tzdata unavailable for {name}")


def test_parse_star_list_range_step() -> None:
    c = parse("*/15 0-2,22 * 1,6 1-5")
    assert isinstance(c, Cron)
    assert c.minutes == frozenset({0, 15, 30, 45})
    assert c.hours == frozenset({0, 1, 2, 22})
    assert c.months == frozenset({1, 6})
    assert c.weekdays == frozenset({1, 2, 3, 4, 5})
    assert len(c.days) == 31


def test_range_with_step_and_sunday_seven() -> None:
    c = parse("10-30/10 * * * 7")
    assert c.minutes == frozenset({10, 20, 30})
    assert c.weekdays == frozenset({0})


@pytest.mark.parametrize(
    ("expr", "field"),
    [
        ("60 * * * *", "minute"),
        ("* 24 * * *", "hour"),
        ("* * 0 * *", "day-of-month"),
        ("* * * 13 *", "month"),
        ("* * * * 8", "day-of-week"),
        ("*/0 * * * *", "minute"),
        ("5-1 * * * *", "minute"),
        ("a * * * *", "minute"),
        ("* * * * 1,,2", "day-of-week"),
    ],
)
def test_invalid_names_field(expr: str, field: str) -> None:
    with pytest.raises(ValueError, match=field):
        parse(expr)


def test_wrong_field_count() -> None:
    with pytest.raises(ValueError, match="5 fields"):
        parse("* * * *")


def test_utc_default() -> None:
    c = parse("30 6 * * *")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    got = list(c.slots_between(start, start + timedelta(days=3)))
    assert got == [datetime(2026, 1, d, 6, 30, tzinfo=UTC) for d in (1, 2, 3)]
    assert all(s.utcoffset() == timedelta(0) for s in got)


def test_half_open_bounds() -> None:
    c = parse("* * * * *")
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    got = list(c.slots_between(start, start + timedelta(minutes=3)))
    assert len(got) == 3 and got[-1].minute == 2


def test_dom_or_dow_semantics() -> None:
    c = parse("0 0 13 * 5")  # 13th OR Friday
    start = datetime(2026, 2, 1, tzinfo=UTC)
    days = {s.day for s in c.slots_between(start, datetime(2026, 3, 1, tzinfo=UTC))}
    assert days == {13, 6, 20, 27}


def test_explicit_tz() -> None:
    berlin = _zone("Europe/Berlin")
    c = parse("0 9 * * *")
    start = datetime(2026, 1, 10, tzinfo=UTC)
    got = list(c.slots_between(start, start + timedelta(days=1), tz=berlin))
    assert [s.astimezone(UTC) for s in got] == [datetime(2026, 1, 10, 8, 0, tzinfo=UTC)]
    summer = datetime(2026, 7, 10, tzinfo=UTC)
    got = list(c.slots_between(summer, summer + timedelta(days=1), tz="Europe/Berlin"))
    assert [s.astimezone(UTC).hour for s in got] == [7]


def test_dst_gap_skipped() -> None:
    ny = _zone("America/New_York")
    c = parse("30 2 * * *")  # 2026-03-08 02:30 does not exist
    start = datetime(2026, 3, 7, tzinfo=UTC)
    got = list(c.slots_between(start, datetime(2026, 3, 10, tzinfo=UTC), tz=ny))
    assert [s.day for s in got] == [7, 9]  # local days; 8th skipped


def test_dst_repeat_once() -> None:
    ny = _zone("America/New_York")
    c = parse("30 1 * * *")  # 2026-11-01 01:30 occurs twice
    start = datetime(2026, 10, 31, tzinfo=UTC)
    got = list(c.slots_between(start, datetime(2026, 11, 3, tzinfo=UTC), tz=ny))
    walls = [(s.month, s.day) for s in got]
    assert walls.count((11, 1)) == 1
    assert len(got) == 3


@pytest.mark.parametrize("zone", ["UTC", "Europe/Berlin", "America/New_York"])
@pytest.mark.parametrize("expr", ["*/30 * * * *", "15 1,2,3 * * *", "0 * * * *"])
def test_year_property(zone: str, expr: str) -> None:
    tz = _zone(zone)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2027, 1, 1, tzinfo=UTC)
    got = list(parse(expr).slots_between(start, end, tz=tz))
    instants = [s.astimezone(UTC) for s in got]
    assert instants == sorted(set(instants)), "strictly increasing, no duplicate instants"
    walls = [s.replace(tzinfo=None) for s in got]
    assert len(walls) == len(set(walls)), "each wall-clock slot at most once"
    assert all(start <= i < end for i in instants)
    cron = parse(expr)
    for s in got:  # every slot genuinely exists and matches
        assert s.minute in cron.minutes and s.hour in cron.hours
        # a real wall time survives a UTC round trip; a time inside a DST gap would move
        assert s.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == s.replace(tzinfo=None)
    if expr == "0 * * * *" and zone == "UTC":
        assert len(got) == 365 * 24
    if expr == "0 * * * *" and zone == "America/New_York":
        assert len(got) == 365 * 24 - 1  # gap loses one, repeat dedups to none extra
