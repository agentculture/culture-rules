"""Rollout documents behind ``/rollouts`` (standard-library only; no web framework here).

A rollout document records one release rollout across the mesh, keyed by the release version
(``id`` = ``version``, e.g. ``0.18.12``): the ``snapshot`` of rule ids that were enabled when
the rollout paused the engine, and ``steps`` - each named step (``idle``, ``pause``,
``upgrade:<host>``, ``seed``, ``import``, ``enable``, ``resume``, ``verify``, ...) with when
it was applied and a small outcome. ``PUT`` replaces the whole document (the caller merges);
the write and exactly one audit entry (verb ``rollouts.put``) land in one transaction.

Validation is strict and bounded: unknown fields are refused, ids and step names follow safe
patterns, ``applied_at`` must be an ISO-8601 timestamp with an offset, and an ``outcome`` is a
short string or a small object of scalars (or flat lists of scalars). A secret-looking key in
an outcome may hold only a ``grant:<NAME>`` reference
(:func:`culture_rules.actors.secrets.assert_refs_only`), so no secret is ever stored.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors.secrets import assert_refs_only
from culture_rules.engine.audit import AuditLog, mutating_verb, require_identity
from culture_rules.server.service import Invalid, NotFound
from culture_rules.store.port import Document, StoragePort

__all__ = [
    "MAX_OUTCOME_BYTES",
    "MAX_SNAPSHOT",
    "MAX_STEPS",
    "ROLLOUTS_COLLECTION",
    "ROLLOUT_ID_RE",
    "STEP_NAME_RE",
    "Rollouts",
    "validate_rollout",
]

ROLLOUTS_COLLECTION = "rollouts"
ROLLOUT_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
"""A release version string: ``0.18.12``, ``1.0.0rc1``, ``0.19.0.dev3``, ``0.18.12+local``."""
STEP_NAME_RE = re.compile(r"^[a-z0-9:_.-]{1,64}$")
RULE_ID_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{1,200}$")
OUTCOME_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
MAX_SNAPSHOT = 10_000
MAX_STEPS = 256
MAX_OUTCOME_KEYS = 32
MAX_OUTCOME_LIST = 64
MAX_OUTCOME_STRING = 1_000
MAX_OUTCOME_BYTES = 4_096
_BODY_FIELDS = frozenset({"version", "snapshot", "steps"})
_STEP_FIELDS = frozenset({"applied_at", "outcome"})


def _err(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def check_id(rollout_id: str) -> str:
    """Return ``rollout_id`` if it is a safe release version string, else raise ``Invalid``."""
    if not isinstance(rollout_id, str) or not ROLLOUT_ID_RE.match(rollout_id):
        message = f"rollout id must match {ROLLOUT_ID_RE.pattern}"
        raise Invalid(message, [_err("id", "invalid_id", message)])
    return rollout_id


def _is_scalar(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) <= MAX_OUTCOME_STRING
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, bool | int)


def _outcome_value_errors(path: str, value: Any) -> list[dict[str, str]]:
    if _is_scalar(value):
        return []
    if isinstance(value, list):
        if len(value) > MAX_OUTCOME_LIST:
            return [_err(path, "too_long", f"at most {MAX_OUTCOME_LIST} items")]
        if all(_is_scalar(v) for v in value):
            return []
    return [_err(path, "invalid_outcome", "a scalar or a flat list of scalars")]


def _outcome_errors(path: str, outcome: Any) -> list[dict[str, str]]:
    if isinstance(outcome, str):
        if len(outcome) > MAX_OUTCOME_STRING:
            return [_err(path, "too_long", f"at most {MAX_OUTCOME_STRING} characters")]
        return []
    if not isinstance(outcome, Mapping):
        return [_err(path, "invalid_outcome", "a string or an object")]
    if len(outcome) > MAX_OUTCOME_KEYS:
        return [_err(path, "too_long", f"at most {MAX_OUTCOME_KEYS} keys")]
    errors: list[dict[str, str]] = []
    for key, value in outcome.items():
        if not isinstance(key, str) or not OUTCOME_KEY_RE.match(key):
            errors.append(_err(path, "invalid_key", f"keys must match {OUTCOME_KEY_RE.pattern}"))
            continue
        errors.extend(_outcome_value_errors(f"{path}.{key}", value))
    if not errors and len(json.dumps(outcome)) > MAX_OUTCOME_BYTES:
        errors.append(_err(path, "too_long", f"at most {MAX_OUTCOME_BYTES} bytes as JSON"))
    return errors


def _timestamp_errors(path: str, value: Any) -> list[dict[str, str]]:
    message = "an ISO-8601 timestamp with a UTC offset"
    if not isinstance(value, str) or len(value) > 64:
        return [_err(path, "invalid_timestamp", message)]
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return [_err(path, "invalid_timestamp", message)]
    return [] if parsed.tzinfo is not None else [_err(path, "invalid_timestamp", message)]


def _step_errors(name: Any, step: Any) -> list[dict[str, str]]:
    if not isinstance(name, str) or not STEP_NAME_RE.match(name):
        return [_err("steps", "invalid_step_name", f"step names must match {STEP_NAME_RE.pattern}")]
    path = f"steps.{name}"
    if not isinstance(step, Mapping):
        return [_err(path, "invalid_step", "an object {applied_at, outcome}")]
    errors = [
        _err(f"{path}.{k}", "unknown_field", "not allowed")
        for k in sorted(set(step) - _STEP_FIELDS)
    ]
    errors += [_err(f"{path}.{k}", "missing", "required") for k in sorted(_STEP_FIELDS - set(step))]
    if "applied_at" in step:
        errors += _timestamp_errors(f"{path}.applied_at", step["applied_at"])
    if "outcome" in step:
        errors += _outcome_errors(f"{path}.outcome", step["outcome"])
    return errors


def _snapshot_errors(snapshot: Any) -> list[dict[str, str]]:
    if not isinstance(snapshot, list):
        return [_err("snapshot", "invalid_snapshot", "a list of rule ids")]
    if len(snapshot) > MAX_SNAPSHOT:
        return [_err("snapshot", "too_long", f"at most {MAX_SNAPSHOT} rule ids")]
    errors = [
        _err(f"snapshot[{i}]", "invalid_rule_id", "a non-empty rule id without whitespace")
        for i, rid in enumerate(snapshot)
        if not isinstance(rid, str) or not RULE_ID_RE.match(rid)
    ]
    if not errors and len(set(snapshot)) != len(snapshot):
        errors.append(_err("snapshot", "duplicate", "rule ids must be unique"))
    return errors


def validate_rollout(rollout_id: str, body: Any) -> dict[str, Any]:
    """The normalised rollout document for ``PUT /rollouts/{rollout_id}``; raises ``Invalid``
    listing every problem (secret literals raise ``SecretError``)."""
    check_id(rollout_id)
    if not isinstance(body, Mapping):
        raise Invalid("rollout body must be an object", [_err("", "invalid", "an object")])
    errors = [_err(k, "unknown_field", "not allowed") for k in sorted(set(body) - _BODY_FIELDS)]
    errors += [_err(k, "missing", "required") for k in sorted(_BODY_FIELDS - set(body))]
    if "version" in body and body["version"] != rollout_id:
        errors.append(_err("version", "id_mismatch", "version must equal the rollout id"))
    if "snapshot" in body:
        errors += _snapshot_errors(body["snapshot"])
    steps = body.get("steps", {})
    if not isinstance(steps, Mapping):
        errors.append(_err("steps", "invalid_steps", "an object {name: {applied_at, outcome}}"))
    elif len(steps) > MAX_STEPS:
        errors.append(_err("steps", "too_long", f"at most {MAX_STEPS} steps"))
    else:
        for name, step in steps.items():
            errors += _step_errors(name, step)
    if errors:
        raise Invalid("rollout validation failed", errors)
    for name, step in steps.items():
        assert_refs_only(step["outcome"], f"steps.{name}.outcome")
    return {
        "id": rollout_id,
        "version": rollout_id,
        "snapshot": list(body["snapshot"]),
        "steps": {
            name: {"applied_at": step["applied_at"], "outcome": step["outcome"]}
            for name, step in steps.items()
        },
    }


class Rollouts:
    """Read and replace rollout documents; every write is audited in its own transaction."""

    def __init__(self, store: StoragePort, audit: AuditLog | None = None) -> None:
        self._store = store
        self._audit = audit or AuditLog()

    def list(self) -> list[Document]:
        """Every rollout document, ordered by id."""
        return sorted(self._store.find(ROLLOUTS_COLLECTION), key=lambda d: str(d.get("id")))

    def get(self, rollout_id: str) -> Document:
        check_id(rollout_id)
        doc = self._store.get(ROLLOUTS_COLLECTION, rollout_id)
        if doc is None:
            raise NotFound(f"rollouts/{rollout_id} does not exist")
        return doc

    @mutating_verb("rollouts.put", "Create or replace a rollout document (snapshot and steps)")
    def put(self, rollout_id: str, body: Any, identity: str) -> Document:
        principal = require_identity(identity)
        doc = validate_rollout(rollout_id, body)
        doc["updated_by"] = principal
        with self._store.transaction() as tx:
            before = tx.get(ROLLOUTS_COLLECTION, rollout_id)
            after = tx.put(ROLLOUTS_COLLECTION, doc)
            self._audit.write(
                tx,
                identity=principal,
                verb="rollouts.put",
                collection=ROLLOUTS_COLLECTION,
                target_id=rollout_id,
                before=before,
                after=after,
            )
        return after
