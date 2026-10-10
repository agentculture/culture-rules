"""#39: the PR fixer's escalation levels, resolved by the queue at dispatch.

The shared variable named by a queue step's ``config.levels`` (the bundle's
``fixer_levels``) lists the levels in order. A shared variable holds only JSON scalars or a
list of them (:mod:`culture_rules.model.variable`), so each level is one string of six
``key=value`` fields, separated by spaces, in any order::

    "level=1 actor=qwen-fixer reviewer=codex-reviewer pool=qwen-spark2 queue=pr-fixer enabled=true"

``level`` (a positive integer, unique; the list is taken in ``level`` order), ``actor`` (the
fixer actor of a try at this level), ``reviewer`` (the actor that reviews its commit),
``pool`` (the actor ``concurrency_pool`` whose cap is the level queue's cap), ``queue`` (the
level's own queue document) and ``enabled`` (``true`` or ``false``). Any other shape (a
missing, repeated or unknown field, an empty value) fails closed: the queue step fails
``bad_levels`` and nothing is dispatched. With the variable undefined (no ``config.levels``,
or no such variable) the queue works as before #39: one queue, no level.

The shared variable named by ``config.unavailable`` (the bundle's ``fixer_unavailable``) is
``"next"`` (the default) or ``"hand_back"``.

Which level a try gets
======================

Only the **enabled** levels form the ladder. Try *n* (the PR key's counted attempts plus
one) is due at ladder position ``n - 1``; past the ladder's end the last level repeats, so
with only level 1 enabled every try is level 1's (as before #39). A level is
**unavailable** when its actor is unknown, deleted (``deleted_at``) or disabled
(``enabled: false``), when the actor's daily ``token_budget`` is spent (its
``actor_usage`` document, as :class:`~culture_rules.actors.limits.LimitedActor` would refuse
``over_budget``), or while it is in a capacity cooldown (below). An unavailable due level is
replaced, with ``fixer_unavailable: next``, by the first available level after it on the
ladder; with ``hand_back``, or when no level after it is available, the try has no level and
the story is handed back once (:data:`NO_FIXER`). The try number never changes: skipping a
level never adds a try.

The capacity cooldown contract
==============================

A cooldown is a document in :data:`FIXER_COOLDOWNS` whose id is the level's **actor** id::

    {"id": "codex-fixer", "until": "2026-10-11T12:00:00+00:00", ...}

``until`` is an ISO 8601 time with an offset; while it is in the future the level is
unavailable (``capacity_cooldown``). Other fields (``reason``, ``run_id``, ``at``) are
informational. Whoever sees a try fail ``capacity_exhausted`` writes (upserts) it; the queue
only reads it, and a document whose ``until`` is missing, malformed or past is no cooldown.

Standard-library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "FIXER_COOLDOWNS",
    "NO_FIXER",
    "UNAVAILABLE_HAND_BACK",
    "UNAVAILABLE_NEXT",
    "Ladder",
    "Level",
    "LevelsError",
    "load_ladder",
]

FIXER_COOLDOWNS = "fixer_cooldowns"
"""Capacity cooldowns per level actor (module doc: the contract)."""
NO_FIXER = "no_fixer_available"
"""Why a request leaves the queue when no level can take its try (the story is handed back)."""
UNAVAILABLE_NEXT = "next"
UNAVAILABLE_HAND_BACK = "hand_back"
_POLICIES = (UNAVAILABLE_NEXT, UNAVAILABLE_HAND_BACK)
_ACTORS = "actors"  # culture_rules.store collection of actor documents
_USAGE = "actor_usage"  # culture_rules.actors.limits.USAGE_COLLECTION
_FIELDS = {"level", "actor", "reviewer", "pool", "queue", "enabled"}


class LevelsError(ValueError):
    """``fixer_levels`` or ``fixer_unavailable`` is malformed (fail closed)."""


@dataclass(frozen=True)
class Level:
    level: int
    actor: str
    reviewer: str
    pool: str
    queue: str
    enabled: bool

    def dispatch_fields(self) -> dict[str, Any]:
        """What a dispatch event names of its level."""
        return {"level": self.level, "fixer": self.actor, "reviewer": self.reviewer}


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")


def _fields(entry: Any, where: str) -> dict[str, str]:
    if not isinstance(entry, str):
        raise LevelsError(f"{where}: must be a string of key=value fields")
    fields: dict[str, str] = {}
    for part in entry.split():
        key, sep, value = part.partition("=")
        if not sep or key not in _FIELDS or key in fields or not _TOKEN_RE.fullmatch(value):
            raise LevelsError(f"{where}: {part!r} is not one of {sorted(_FIELDS)}=<value>")
        fields[key] = value
    if set(fields) != _FIELDS:
        raise LevelsError(f"{where}: needs exactly the fields {sorted(_FIELDS)}")
    return fields


def _level(entry: Any, i: int) -> Level:
    where = f"fixer level #{i + 1}"
    fields = _fields(entry, where)
    if not fields["level"].isdigit() or int(fields["level"]) < 1:
        raise LevelsError(f"{where}: level must be a positive integer")
    if fields["enabled"] not in ("true", "false"):
        raise LevelsError(f"{where}: enabled must be true or false")
    return Level(
        level=int(fields["level"]),
        actor=fields["actor"],
        reviewer=fields["reviewer"],
        pool=fields["pool"],
        queue=fields["queue"],
        enabled=fields["enabled"] == "true",
    )


def _parse_until(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    if moment is None or moment.tzinfo is None:
        return None
    return moment


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class Ladder:
    """The levels of one ``fixer_levels`` value, and the availability of each now."""

    def __init__(self, var: str, levels: list[Level], policy: str, store: Any) -> None:
        self.var, self.policy, self.store = var, policy, store
        self.levels = sorted(levels, key=lambda lvl: lvl.level)
        self.enabled = [lvl for lvl in self.levels if lvl.enabled]

    def queues(self) -> list[str]:
        """Every level's queue (disabled levels included), in level order, once each."""
        return list(dict.fromkeys(lvl.queue for lvl in self.levels))

    def pool_of(self, queue: str) -> str | None:
        """The pool of the first level naming ``queue`` (its cap is that queue's)."""
        return next((lvl.pool for lvl in self.levels if lvl.queue == queue), None)

    def due(self, attempt: int) -> Level | None:
        """The level try ``attempt`` is due at, availability aside (None: no enabled level)."""
        if not self.enabled:
            return None
        return self.enabled[min(max(attempt, 1), len(self.enabled)) - 1]

    def resolve(self, attempt: int, now: datetime) -> tuple[Level | None, list[str]]:
        """The level that takes try ``attempt`` now, and why each skipped level was skipped;
        ``(None, why)`` when none can (the story is handed back)."""
        due = self.due(attempt)
        if due is None:
            return None, ["no fixer level is enabled"]
        why: list[str] = []
        for lvl in self.enabled[self.enabled.index(due) :]:
            reason = self.unavailable(lvl, now)
            if reason is None:
                return lvl, why
            why.append(f"level {lvl.level} ({lvl.actor}): {reason}")
            if self.policy == UNAVAILABLE_HAND_BACK:
                break
        return None, why

    def unavailable(self, lvl: Level, now: datetime) -> str | None:
        """Why ``lvl`` cannot take a try now, or None."""
        actor = self.store.get(_ACTORS, lvl.actor)
        if actor is None:
            return "actor_unknown"
        if actor.get("deleted_at"):
            return "actor_deleted"
        if actor.get("enabled", True) is False:
            return "actor_disabled"
        if self._over_budget(lvl.actor, actor, now):
            return "over_budget"
        cooldown = self.store.get(FIXER_COOLDOWNS, lvl.actor) or {}
        until = _parse_until(cooldown.get("until"))
        if until is not None and until > now:
            return "capacity_cooldown"
        return None

    def _over_budget(self, actor_id: str, actor: Mapping[str, Any], now: datetime) -> bool:
        params = actor.get("params") if isinstance(actor.get("params"), Mapping) else {}
        budget = _int(params.get("token_budget"))
        if budget is None or budget <= 0:
            return False
        usage = self.store.get(_USAGE, actor_id) or {}
        if usage.get("day") != now.astimezone(UTC).strftime("%Y-%m-%d"):
            return False  # a new UTC day: nothing spent yet
        return (_int(usage.get("tokens")) or 0) >= budget


def load_ladder(store: Any, config: Mapping[str, Any]) -> Ladder | None:
    """The ladder a queue step's ``config.levels`` names, or None (no levels: the queue works
    as before #39). Raises :class:`LevelsError` on a malformed value."""
    var = config.get("levels")
    if not isinstance(var, str) or not var:
        return None
    doc = store.get_variable(var)
    if doc is None:
        return None
    value = doc.get("value")
    if not isinstance(value, list):
        raise LevelsError(f"{var} must be a list of levels")
    levels = [_level(entry, i) for i, entry in enumerate(value)]
    if len({lvl.level for lvl in levels}) != len(levels):
        raise LevelsError(f"{var}: each level number must be unique")
    return Ladder(var, levels, _policy(store, config), store)


def _policy(store: Any, config: Mapping[str, Any]) -> str:
    var = config.get("unavailable")
    doc = store.get_variable(var) if isinstance(var, str) and var else None
    if doc is None:
        return UNAVAILABLE_NEXT
    value = doc.get("value")
    if value not in _POLICIES:
        raise LevelsError(f"{var} must be one of {list(_POLICIES)}")
    return value
