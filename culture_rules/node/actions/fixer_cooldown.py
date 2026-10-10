"""Built-in ``fixer.cooldown``: a capacity cooldown for the level whose try hit its limit (#39).

A fixer try (a ``pr-fix`` run) whose agent step fails ``capacity_exhausted`` (the bridge's
class for a backend's usage or rate limit) ends failed. ``pr-fixer-retry-capacity`` fires on
its ``rules.run.failed`` event and runs ``queue-add-capacity``: this step first, then the
built-in ``queue.add`` puts the story back in line as a retry, so ``queue.progress`` resolves
its next try past the cooled-down level (:mod:`.fixer_levels`).

What it writes
==============
The cooldown contract of :mod:`.fixer_levels`: the document of :data:`FIXER_COOLDOWNS` whose
id is the level's actor, ``{"until": <ISO 8601 with offset>, "reason", "run_id", "at",
"level"}`` (upserted by compare-and-set). ``until`` is the failed run's end (``finished_at``;
now when it is missing, unreadable or later than now) plus the cooldown, so replaying an old
failure writes nothing new. A cooldown already ending later is kept, never shortened.

The cooldown is the shared variable named by ``config.cooldown`` (the bundle's
``fixer_capacity_cooldown_s``), seconds, :data:`DEFAULT_COOLDOWN_S` when that variable is
undefined; any other value than an integer from 1 to :data:`MAX_COOLDOWN_S` fails the step
``bad_cooldown`` (fail closed, like ``bad_levels``).

What it trusts
==============
Nothing from its input picks the target. The failed try is the run whose verified
``rules.run.failed`` event started this step's own run
(:func:`~culture_rules.actors.lineage.upstream`, ``failed=True``); it must be a ``pr-fix``
run. Its fixer is the placed actor of the ``ai`` step that failed ``capacity_exhausted`` in
that run's pinned workflow (the actor that actually ran; the run's ``fixer`` input only when
that step names no actor). The actor id must be well formed and be the ``actor`` of a level
in ``config.levels`` (the bundle's ``fixer_levels``): any other actor is not a level, so no
cooldown is written (``recorded: false``) and the story still goes back in line. With no
levels defined, nothing is written either: the queue reads no cooldown then. The failed run
must also be this step's PR (input ``repo`` and ``number``), so the retry it gates is that
story's.

Outputs: ``recorded`` (whether the level is in a cooldown now: this one, or a later one),
``actor``, ``level``, ``until``, ``why`` (when not recorded), and ``retry`` (always true: the
request ``queue.add`` adds next is a retry, and the edge orders the two steps).

Standard-library only.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.fixer_levels import (
    FIXER_COOLDOWNS,
    LevelsError,
    load_ladder,
    parse_until,
)

__all__ = [
    "CAPACITY_EXHAUSTED",
    "DEFAULT_COOLDOWN_S",
    "FIXER_COOLDOWN_BUILTIN",
    "MAX_COOLDOWN_S",
    "FixerCooldownPort",
]

FIXER_COOLDOWN_BUILTIN = "fixer.cooldown"
CAPACITY_EXHAUSTED = "capacity_exhausted"
"""The bridge's error class for a backend's usage or rate limit (the step error's prefix)."""
DEFAULT_COOLDOWN_S = 3600
MAX_COOLDOWN_S = 7 * 86400
_FIX_WORKFLOW = "pr-fix"  # culture_rules.actors.trusted.ROLE_FIX
_RUNS = "runs"  # culture_rules.engine.runs.RUNS_COLLECTION
_ACTOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")
_MAX_CAS_TRIES = 20
_UNTIL = "until"  # the cooldown document's end (the contract of .fixer_levels)

Clock = Callable[[], datetime]


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _step_defs(steps: Any) -> Iterator[Mapping[str, Any]]:
    """Every step definition of a pinned workflow, loop bodies included."""
    for step in steps if isinstance(steps, list) else ():
        if isinstance(step, Mapping):
            yield step
            yield from _step_defs(step.get("body"))


def _capacity_failed(state: Mapping[str, Any]) -> bool:
    error = state.get("error") if isinstance(state.get("error"), Mapping) else {}
    message = error.get("message")
    return (
        state.get("status") == "failed"
        and isinstance(message, str)
        and message.startswith(CAPACITY_EXHAUSTED)
    )


def capacity_actor(run: Mapping[str, Any]) -> str:
    """The fixer actor whose agent step failed ``capacity_exhausted`` in ``run`` (module
    doc), else :class:`_Refused` ``not_capacity_exhausted`` / ``bad_actor``."""
    definition = (run.get("workflow") or {}).get("definition") or {}
    defs = {d.get("id"): d for d in _step_defs(definition.get("steps"))}
    for state in run.get("steps") or ():
        step = defs.get(state.get("def")) if isinstance(state, Mapping) else None
        if step is None or step.get("kind") != "ai" or not _capacity_failed(state):
            continue
        placement = step.get("placement") if isinstance(step.get("placement"), Mapping) else {}
        inputs = run.get("inputs") if isinstance(run.get("inputs"), Mapping) else {}
        actor = placement.get("actor") or inputs.get("fixer")
        if not isinstance(actor, str) or not _ACTOR_RE.fullmatch(actor):
            raise _Refused("bad_actor", f"the failed agent step names no valid actor: {actor!r}")
        return actor
    raise _Refused("not_capacity_exhausted", "no agent step of the failed try hit its limit")


def _cooldown_s(store: Any, config: Mapping[str, Any]) -> int:
    var = config.get("cooldown")
    doc = store.get_variable(var) if isinstance(var, str) and var else None
    if doc is None:
        return DEFAULT_COOLDOWN_S
    value = doc.get("value")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_COOLDOWN_S:
        raise _Refused("bad_cooldown", f"{var} must be an integer from 1 to {MAX_COOLDOWN_S}")
    return value


class FixerCooldownPort:
    """``fixer.cooldown`` (module doc): record the capacity cooldown of a failed try's level."""

    supports_idempotency_key = True

    def __init__(self, store: Any, *, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del idempotency_key, deadline
        config = context.config or {}
        try:
            failed = self._failed_try(context.run_id, input)
            actor = capacity_actor(failed)
            seconds = _cooldown_s(self._store, config)
            ladder = load_ladder(self._store, config)
        except LevelsError as exc:
            return InvocationResult.failed(f"bad_levels: {exc}", retryable=False)
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
        out: dict[str, Any] = {"retry": True, "actor": actor, "recorded": False}
        level = next((lvl for lvl in ladder.levels if lvl.actor == actor), None) if ladder else None
        if level is None:
            out["why"] = "not_a_level" if ladder else "no_levels"
            return InvocationResult.completed(out)
        now = self._clock()
        until = self._ended(failed, now) + timedelta(seconds=seconds)
        stands = self._upsert(actor, until, now, str(failed.get("id")), level.level)
        if stands is None:
            return InvocationResult.failed("contended: the cooldown kept changing", retryable=True)
        out.update(level=level.level, until=_iso(stands), recorded=stands > now)
        if stands <= now:
            out["why"] = "already_over"
        return InvocationResult.completed(out)

    def _failed_try(self, run_id: str, input: Mapping[str, Any]) -> Mapping[str, Any]:
        # the actors layer, lazily (it reaches into the node's run events)
        from culture_rules.actors.lineage import LineageError, upstream  # noqa: PLC0415

        own = self._store.get(_RUNS, run_id)
        try:
            failed = upstream(self._store, own or {}, failed=True)
        except LineageError as exc:
            raise _Refused(exc.code, exc.detail or str(exc)) from exc
        if failed.get("workflow_id") != _FIX_WORKFLOW:
            raise _Refused("not_a_fix", "the failed run is not a pr-fix try")
        data = (failed.get("trigger") or {}).get("data") or {}
        if (data.get("repository"), data.get("number")) != (input.get("repo"), input.get("number")):
            raise _Refused("bad_input", "the failed try is another PR's")
        return failed

    def _ended(self, failed: Mapping[str, Any], now: datetime) -> datetime:
        ended = parse_until(failed.get("finished_at"))
        return ended if ended is not None and ended <= now else now

    def _upsert(
        self, actor: str, until: datetime, now: datetime, run_id: str, level: int
    ) -> datetime | None:
        """Write ``until`` unless a later one stands; the ``until`` standing, or None when
        the compare-and-set kept losing."""
        for _ in range(_MAX_CAS_TRIES):
            current = self._store.get(FIXER_COOLDOWNS, actor)
            seen = (current or {}).get(_UNTIL)
            standing = parse_until(seen)
            if standing is not None and standing >= until:
                return standing
            changes = {
                _UNTIL: _iso(until),
                "reason": CAPACITY_EXHAUSTED,
                "run_id": run_id,
                "level": level,
                "at": _iso(now),
            }
            res = self._store.update_if(
                FIXER_COOLDOWNS, actor, {_UNTIL: seen}, changes, upsert=current is None
            )
            if res.won:
                return until
        return None
