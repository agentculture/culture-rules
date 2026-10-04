"""Stdlib 5-field cron parser with DST-safe slot iteration.

Fields: minute hour day-of-month month day-of-week (0-6, Sunday=0; 7 is also
Sunday). Each supports ``*``, lists, ranges and steps. When both day-of-month
and day-of-week are restricted a day matches if either matches (standard cron).

Slots are wall-clock times in ``tz`` (UTC by default). A wall-clock slot that
falls in a DST gap does not exist and is skipped; one that falls in a DST
repeat is yielded once (its first occurrence). No third-party dependencies.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

__all__ = ["Cron", "parse"]

_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day-of-month", 1, 31),
    ("month", 1, 12),
    ("day-of-week", 0, 7),
)


def _parse_int(text: str, name: str) -> int:
    if not text.isascii() or not text.isdigit():
        raise ValueError(f"invalid {name} field: {text!r} is not a number")
    return int(text)


def _parse_field(text: str, name: str, lo: int, hi: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise ValueError(f"invalid {name} field: empty list item in {text!r}")
        base, sep, step_text = part.partition("/")
        step = 1
        if sep:
            step = _parse_int(step_text, name)
            if step < 1:
                raise ValueError(f"invalid {name} field: step must be >= 1 in {part!r}")
        if base == "*":
            first, last = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            first, last = _parse_int(a, name), _parse_int(b, name)
        else:
            first = _parse_int(base, name)
            last = hi if sep else first
        if first < lo or last > hi or first > last:
            raise ValueError(f"invalid {name} field: {part!r} outside {lo}-{hi}")
        values.update(range(first, last + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class Cron:
    """A parsed cron expression."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0-6, Sunday=0
    dom_restricted: bool = False
    dow_restricted: bool = False

    def _day_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom = day.day in self.days
        dow = (day.isoweekday() % 7) in self.weekdays
        if self.dom_restricted and self.dow_restricted:
            return dom or dow
        return dom and dow

    def slots_between(
        self,
        start: datetime,
        end: datetime,
        tz: tzinfo | str | None = None,
    ) -> Iterator[datetime]:
        """Yield aware slots in ``[start, end)`` in increasing order, in ``tz``."""
        zone: tzinfo = ZoneInfo(tz) if isinstance(tz, str) else (tz or timezone.utc)
        start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        day = start.astimezone(zone).date()
        last = end.astimezone(zone).date()
        times = [(h, m) for h in sorted(self.hours) for m in sorted(self.minutes)]
        while day <= last:
            if self._day_matches(day):
                for h, m in times:
                    slot = datetime(day.year, day.month, day.day, h, m, tzinfo=zone)
                    # fold=0 picks the first occurrence in a repeat; a gap time
                    # does not survive a UTC round trip.
                    back = slot.astimezone(timezone.utc).astimezone(zone)
                    if back.replace(tzinfo=None) != slot.replace(tzinfo=None):
                        continue
                    if slot < start:
                        continue
                    if slot >= end:
                        return
                    yield slot
            day += timedelta(days=1)


def parse(expr: str) -> Cron:
    """Parse a 5-field cron expression; raise ``ValueError`` naming a bad field."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron expression needs 5 fields, got {len(parts)}: {expr!r}")
    sets = [_parse_field(p, n, lo, hi) for p, (n, lo, hi) in zip(parts, _FIELDS)]
    weekdays = frozenset(0 if d == 7 else d for d in sets[4])
    return Cron(
        minutes=sets[0],
        hours=sets[1],
        days=sets[2],
        months=sets[3],
        weekdays=weekdays,
        dom_restricted=not parts[2].startswith("*"),
        dow_restricted=not parts[4].startswith("*"),
    )
