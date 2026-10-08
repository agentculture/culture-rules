"""Chain holds: a chain of rules on one concurrency key is one unit per key (d21 phase 2).

Rules chain through run events (:mod:`culture_rules.engine.run_completions`): a run ends,
emits ``rules.run.<status>``, and a rule on that event continues the work - the PR fixer's
fix, then its review, then its publish. Every stage shares the PR's concurrency key. Between
two stages the key used to be **free**: the finished run no longer held it and the next
stage's run did not exist yet (its event is delivered by the outbox one node cycle later).
A fresh external event arriving in that window (new checks on the PR) took the key, and the
continuation was deduplicated behind the new run - the chain's stages interleaved with
another chain's.

So the terminal transition of a keyed run **holds** its key for the rules that would
continue it, in the same transaction that records its completion:

* :func:`continuations` decides which live rules would fire on the run's event - the same
  matching a trigger consumer runs (trigger type, condition, shared variables, hop cap), with
  no predecessor facts - and whose concurrency key resolves to the run's own key on it.
* With any, the key's budget document (:data:`~culture_rules.engine.claims.RULE_ATTEMPT_BUDGETS`)
  gets ``hold = {run_id, rules, since}`` (:func:`set_hold`). It stays held by the finished
  run.
* While the hold lasts, :func:`~culture_rules.engine.claims.reserve_concurrency` admits only
  a **continuation** - a firing on a ``rules.run.*`` event of exactly the held run (the
  firing transaction verifies the event against its completion record first: a forged copy
  never fires) - and deduplicates every other firing as usual (it becomes the key's pending
  event). The continuation takes the key and **keeps** the pending event, so it fires when
  the chain finally ends, never in the middle of it.
* A holder's end does not release a held key
  (:func:`~culture_rules.engine.claims.release_concurrency` only writes its guard): there is
  a continuation to come.
* A rule named in the hold that does not take the key when its event is evaluated - its
  condition no longer holds, it was disabled or deleted, the budget refused it - is removed
  from the hold by the consumer that evaluated it (:func:`decline`). When none is left the
  hold is **released** (``hold_released`` = the held run); the chain consumers watch the
  budget documents and the one owning the pending event's rule fires it then.
* Backstop: a hold older than :data:`HOLD_TTL` no longer blocks the key (a continuation
  placed on a host that never came back), so a key is never held forever, and every
  node's cycle releases it (:func:`expire_holds`) so its pending event fires. A late
  continuation of an expired or released hold still succeeds its holder and keeps the
  pending event for the chain's end (:func:`is_continuation`).

Holding a key changes nothing for a run without a concurrency key, and nothing for a run
whose completion no rule continues: then there is no hold, and the key is released at the
run's end exactly as before. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.engine.claims import (
    RULE_ATTEMPT_BUDGETS,
    budget_id,
    resolve_concurrency_key,
)
from culture_rules.engine.matching import match
from culture_rules.engine.variables import variable_values
from culture_rules.model.rule import Rule
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.model.workflow import Workflow
from culture_rules.store.port import StoreOps, TransientStoreError
from culture_rules.store.retry import DEFAULT_ATTEMPTS
from culture_rules.store.versioning import utc_timestamp

__all__ = [
    "HOLD_TTL",
    "RUN_EVENT_TYPE_PREFIX",
    "continuations",
    "decline",
    "expire_holds",
    "hold_active",
    "is_continuation",
    "set_hold",
]

HOLD_TTL = timedelta(minutes=15)
"""How long a hold may block its key at most: past it the key is free again for the next
firing (longer than :data:`~culture_rules.engine.runs.PLACEMENT_ABANDON_AFTER`, so a
continuation placed on a host that died is given up on first)."""

RUN_EVENT_TYPE_PREFIX = "rules.run."


def _rules(ops: StoreOps) -> list[Rule]:
    out = []
    for doc in ops.find("rules"):
        if doc.get("deleted_at"):
            continue
        try:
            out.append(Rule.from_dict(doc, strict=False))
        except ValueError:
            continue  # an unparseable rule fires nowhere, so it continues nothing
    return out


def _workflows(ops: StoreOps) -> dict[str, Workflow]:
    out: dict[str, Workflow] = {}
    for doc in ops.find("workflows"):
        try:
            wf = Workflow.from_dict(doc, strict=False)
        except ValueError:
            continue
        out[wf.id] = wf
    return out


def _key_of(rule: Rule, envelope: Mapping[str, Any]) -> str | None:
    if rule.concurrency_key is None:
        return None
    try:
        return resolve_concurrency_key(rule.concurrency_key, envelope)
    except ValueError:
        return None


def continuations(ops: StoreOps, envelope: Mapping[str, Any], key: str | None) -> list[str]:
    """The ids of the live rules that would fire on ``envelope`` (a run's ``rules.run.*``
    event) and continue its chain: with ``key``, only those whose concurrency key resolves to
    it on the event; without one, every rule that would fire. Sorted; read through ``ops``."""
    rules = _rules(ops)
    if not rules:
        return []
    wanted = set().union(*(rule_variable_refs(r) for r in rules))
    values = variable_values(ops, wanted) if wanted else {}
    decisions = match(envelope, rules, workflows=_workflows(ops), variables=values)
    by_id = {r.id: r for r in rules}
    out = []
    for d in decisions:
        if not d.fire:
            continue
        if key is not None and _key_of(by_id[d.rule_id], envelope) != key:
            continue
        out.append(d.rule_id)
    return sorted(out)


def _parse_time(text: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def hold_active(budget: Mapping[str, Any] | None, now: datetime | None = None) -> bool:
    """Whether ``budget`` is held for a continuation of its holding run right now: a hold
    naming the holder, at least one rule still to decide, and younger than :data:`HOLD_TTL`
    (an unreadable ``since`` counts as expired: never a key held for ever)."""
    hold = (budget or {}).get("hold")
    if not isinstance(hold, Mapping) or not hold.get("rules"):
        return False
    if hold.get("run_id") != (budget or {}).get("run_id"):
        return False
    since = _parse_time(hold.get("since"))
    if since is None:
        return False
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment - since < HOLD_TTL


def is_continuation(budget: Mapping[str, Any] | None, envelope: Mapping[str, Any] | None) -> bool:
    """Whether ``envelope`` is an event of the finished run that still holds ``budget`` and
    was (or is) held for its continuation - a live, expired or released hold alike. A late
    continuation of an expired or released hold still succeeds its holder: it keeps the
    key's pending event for the chain's end instead of coalescing it away (Codex #1)."""
    if not isinstance(envelope, Mapping) or not isinstance(budget, Mapping):
        return False
    if not str(envelope.get("type") or "").startswith(RUN_EVENT_TYPE_PREFIX):
        return False
    hold = budget.get("hold")
    held = hold.get("run_id") if isinstance(hold, Mapping) else None
    holder = budget.get("run_id")
    if not holder or holder not in (held, budget.get("hold_released")):
        return False
    data = envelope.get("data")
    run_id = data.get("run_id") if isinstance(data, Mapping) else None
    return isinstance(run_id, str) and run_id == holder


def expire_holds(store: Any, now: datetime | None = None) -> list[str]:
    """Release every hold older than :data:`HOLD_TTL` (any node, each cycle): its
    continuation was never decided - placed on a host that never came back. The release
    is the same compare-and-set :func:`decline` ends with (``hold_released``), so the chain
    consumers fire the key's pending event exactly as for a declined continuation. Answer
    the budget ids released."""
    moment = now if now is not None else datetime.now(UTC)
    released = []
    for doc in store.find(RULE_ATTEMPT_BUDGETS):
        hold = doc.get("hold")
        if not isinstance(hold, Mapping) or hold.get("run_id") != doc.get("run_id"):
            continue
        since = _parse_time(hold.get("since"))
        if since is not None and moment - since < HOLD_TTL:
            continue  # still waiting for its continuation (unreadable since: expired)
        won = store.update_if(
            RULE_ATTEMPT_BUDGETS,
            doc["id"],
            {"revision": doc.get("revision"), "run_id": doc.get("run_id")},
            {
                "hold": None,
                "hold_released": doc.get("run_id"),
                "revision": (doc.get("revision") or 0) + 1,
            },
        ).won
        if won:
            released.append(doc["id"])
    return released


def set_hold(
    tx: StoreOps, run: Mapping[str, Any], envelope: Mapping[str, Any], now: datetime | None = None
) -> list[str]:
    """In the terminal transition's transaction: hold the finished ``run``'s key for the
    rules that would continue it (module doc). Answer those rule ids (``[]``: no hold)."""
    key = run.get("concurrency_key")
    if not isinstance(key, str) or not key:
        return []
    doc_id = budget_id(key)
    current = tx.get(RULE_ATTEMPT_BUDGETS, doc_id)
    if current is None or current.get("run_id") != run.get("id"):
        return []  # not this run's key any more (a restore dropped it): nothing to hold
    rules = continuations(tx, envelope, key)
    if not rules:
        return []
    hold = {"run_id": run["id"], "rules": rules, "since": utc_timestamp(now)}
    for _ in range(DEFAULT_ATTEMPTS):
        revision = current.get("revision")
        outcome = tx.update_if(
            RULE_ATTEMPT_BUDGETS,
            doc_id,
            {"revision": revision, "run_id": run["id"]},
            {"hold": hold, "hold_released": None, "revision": (revision or 0) + 1},
        )
        if outcome.won:
            return rules
        current = tx.get(RULE_ATTEMPT_BUDGETS, doc_id)
        if current is None or current.get("run_id") != run.get("id"):
            return []
    raise TransientStoreError("chain hold contention")


def decline(
    tx: StoreOps,
    envelope: Mapping[str, Any],
    decided: Iterable[str],
    live: Iterable[str],
) -> str | None:
    """A trigger consumer evaluated ``envelope`` (a verified run event): remove from the
    held run's hold the rules it decided (``decided``: it evaluated them and none took the
    key, or the key would have moved on) and every rule that is no longer ``live`` (deleted
    or disabled: no consumer would decide it). Answer the budget id when this released the
    hold (``hold_released`` set), else ``None``."""
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        return None
    key, run_id = data.get("concurrency_key"), data.get("run_id")
    if not isinstance(key, str) or not isinstance(run_id, str):
        return None
    doc_id = budget_id(key)
    decided, live = set(decided), set(live)
    for _ in range(DEFAULT_ATTEMPTS):
        current = tx.get(RULE_ATTEMPT_BUDGETS, doc_id)
        changes = _decline_changes(current, run_id, decided, live)
        if changes is None:
            return None
        if tx.update_if(
            RULE_ATTEMPT_BUDGETS, doc_id, {"revision": current.get("revision")}, changes
        ).won:
            return doc_id if "hold_released" in changes else None
    raise TransientStoreError("chain hold release contention")


def _decline_changes(
    current: Mapping[str, Any] | None, run_id: str, decided: set[str], live: set[str]
) -> dict[str, Any] | None:
    """The budget write removing the decided and dead rules from ``run_id``'s hold - the
    hold narrowed, or released when no rule is left - or None to leave it: no hold of that
    run, a continuation already took the key, or nothing to remove."""
    hold = (current or {}).get("hold")
    if not isinstance(hold, Mapping) or hold.get("run_id") != run_id:
        return None
    if current.get("run_id") != run_id:
        return None  # a continuation took the key: its reservation ended the hold
    rules = [r for r in hold.get("rules") or () if r not in decided and r in live]
    if rules == list(hold.get("rules") or ()):
        return None
    changes: dict[str, Any] = {"revision": (current.get("revision") or 0) + 1}
    if rules:
        changes["hold"] = {**dict(hold), "rules": rules}
    else:
        changes.update(hold=None, hold_released=run_id)
    return changes
