"""Rollout documents behind ``/rollouts`` (standard-library only; no web framework here).

A rollout document records one release rollout across the mesh, keyed by the release version
(``id`` = ``version``, e.g. ``0.18.12``): the ``snapshot`` of rule ids that were enabled when
the rollout paused the engine, and ``steps`` - each named step (``idle``, ``pause``,
``upgrade:<host>``, ``seed``, ``import``, ``enable``, ``resume``, ``verify``, ...) with when
it was applied and a small outcome. Three writes, all admin, each landing with exactly one
audit entry in one store transaction:

* ``POST /rollouts/{id}/steps/{name}`` (:meth:`Rollouts.record_step`, verb
  ``rollouts.step``) merges one step ``{outcome}`` into the document, creating
  ``{version, snapshot: [], steps: {}}`` when it is absent. The server sets ``applied_at``
  from its own clock, so concurrent recorders never lose each other's steps and a step's
  time never depends on a client clock. A re-record replaces that step, except the steps in
  :data:`FIRST_WINS_STEPS` (``resume_requested``, verify's window): the first wins and a
  later record is refused (409 ``step_recorded``) inside the same transaction.
* ``POST /rollouts/{id}/snapshot`` (:meth:`Rollouts.record_snapshot`, verb
  ``rollouts.snapshot``) sets the snapshot only while none is recorded: the first wins and a
  later call is refused (409 ``snapshot_recorded``) without touching the stored one. A
  snapshot counts as recorded once ``snapshot_at`` is set, or (documents written before
  this endpoint) once a ``pause`` step is recorded.
* ``PUT /rollouts/{id}`` (:meth:`Rollouts.put`, verb ``rollouts.put``) replaces the whole
  document; its snapshot counts as recorded. The CLI does not use it.

Validation is strict and bounded: unknown fields are refused, ids and step names follow safe
patterns, ``applied_at`` must be an ISO-8601 timestamp with an offset, and an ``outcome`` is a
short string or a small object of scalars (or flat lists of scalars). A secret-looking key in
an outcome may hold only a ``grant:<NAME>`` reference
(:func:`culture_rules.actors.secrets.assert_refs_only`), so no secret is ever stored. Outcome
keys may use ``.``, ``:`` and ``-``, so each key is normalised (camelCase humps, ``.``, ``:``
and ``-`` to ``_``, lower case) before that check: ``github.token``, ``api:key`` and
``Private-Key`` are secret keys too.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.actors.secrets import SecretError, assert_refs_only
from culture_rules.engine.audit import AuditLog, mutating_verb, require_identity
from culture_rules.server.service import Conflict, Invalid, NotFound
from culture_rules.store.port import Document, StoragePort, StoreOps, TransientStoreError

__all__ = [
    "MAX_OUTCOME_BYTES",
    "MAX_SNAPSHOT",
    "MAX_STEPS",
    "ROLLOUTS_COLLECTION",
    "ROLLOUT_ID_RE",
    "FIRST_WINS_STEPS",
    "STEP_NAME_RE",
    "STEP_RECORDED",
    "Rollouts",
    "snapshot_recorded",
    "validate_rollout",
]

ROLLOUTS_COLLECTION = "rollouts"
ROLLOUT_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
"""A release version string: ``0.18.12``, ``1.0.0rc1``, ``0.19.0.dev3``, ``0.18.12+local``."""
STEP_NAME_RE = re.compile(r"^[a-z0-9:_.-]{1,64}$")
RULE_ID_RE = re.compile(r"^[^\s\x00-\x08\x0e-\x1b\x7f]{1,200}$")
OUTCOME_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
MAX_SNAPSHOT = 10_000
MAX_STEPS = 256
MAX_OUTCOME_KEYS = 32
MAX_OUTCOME_LIST = 64
MAX_OUTCOME_STRING = 1_000
MAX_OUTCOME_BYTES = 4_096
_BODY_FIELDS = frozenset({"version", "snapshot", "steps"})
_STEP_FIELDS = frozenset({"applied_at", "outcome"})
_STEP_BODY_FIELDS = frozenset({"outcome"})
_SNAPSHOT_BODY_FIELDS = frozenset({"snapshot"})
_KEY_SEPARATORS_RE = re.compile(r"[.:-]")
_CAMEL_HUMP_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TX_ATTEMPTS = 5
_NOT_ALLOWED = "not allowed"
SNAPSHOT_AT = "snapshot_at"
SNAPSHOT_RECORDED = "snapshot_recorded"
STEP_RECORDED = "step_recorded"
FIRST_WINS_STEPS = frozenset({"resume_requested"})
"""Steps recorded once: a later record is refused (409 ``step_recorded``), never applied.
``resume_requested`` opens verify's window; moving it later could hide a failed run."""


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
        _err(f"{path}.{k}", "unknown_field", _NOT_ALLOWED) for k in sorted(set(step) - _STEP_FIELDS)
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


def _secret_key_form(key: str) -> str:
    """``key`` as the secret-key check reads it: humps, ``.``, ``:`` and ``-`` become ``_``."""
    return _KEY_SEPARATORS_RE.sub("_", _CAMEL_HUMP_RE.sub("_", key)).lower()


def assert_outcome_refs_only(outcome: Any, path: str) -> None:
    """Raise ``SecretError`` if a secret-looking outcome key (in any permitted spelling) holds
    a literal. Each key is checked on its own, so two keys that normalise alike cannot mask
    each other."""
    if not isinstance(outcome, Mapping):
        return
    for key, value in outcome.items():
        try:
            assert_refs_only({_secret_key_form(str(key)): value})
        except SecretError:
            raise SecretError(f"{path}.{key}: secret must be a 'grant:<NAME>' reference") from None


def _strict_fields(body: Any, fields: frozenset[str], what: str) -> list[dict[str, str]]:
    if not isinstance(body, Mapping):
        raise Invalid(f"{what} body must be an object", [_err("", "invalid", "an object")])
    errors = [_err(k, "unknown_field", _NOT_ALLOWED) for k in sorted(set(body) - fields)]
    return errors + [_err(k, "missing", "required") for k in sorted(fields - set(body))]


def validate_step(name: Any, body: Any) -> Any:
    """The outcome of ``POST /rollouts/{id}/steps/{name}`` (body ``{outcome}``); raises
    ``Invalid`` (or ``SecretError`` for a secret literal)."""
    errors = _strict_fields(body, _STEP_BODY_FIELDS, "step")
    if not isinstance(name, str) or not STEP_NAME_RE.match(name):
        errors.append(
            _err("name", "invalid_step_name", f"step names must match {STEP_NAME_RE.pattern}")
        )
    if "outcome" in body:
        errors += _outcome_errors("outcome", body["outcome"])
    if errors:
        raise Invalid("rollout step validation failed", errors)
    assert_outcome_refs_only(body["outcome"], f"steps.{name}.outcome")
    return body["outcome"]


def validate_snapshot(body: Any) -> list[str]:
    """The snapshot of ``POST /rollouts/{id}/snapshot`` (body ``{snapshot}``)."""
    errors = _strict_fields(body, _SNAPSHOT_BODY_FIELDS, "snapshot")
    if "snapshot" in body:
        errors += _snapshot_errors(body["snapshot"])
    if errors:
        raise Invalid("rollout snapshot validation failed", errors)
    return list(body["snapshot"])


def snapshot_recorded(doc: Mapping[str, Any] | None) -> bool:
    """Whether ``doc`` holds a recorded snapshot: ``snapshot_at`` is set, or (documents
    written before the snapshot endpoint) its ``pause`` step is recorded."""
    if not doc:
        return False
    return bool(doc.get(SNAPSHOT_AT)) or "pause" in (doc.get("steps") or {})


def validate_rollout(rollout_id: str, body: Any) -> dict[str, Any]:
    """The normalised rollout document for ``PUT /rollouts/{rollout_id}``; raises ``Invalid``
    listing every problem (secret literals raise ``SecretError``)."""
    check_id(rollout_id)
    if not isinstance(body, Mapping):
        raise Invalid("rollout body must be an object", [_err("", "invalid", "an object")])
    errors = [_err(k, "unknown_field", _NOT_ALLOWED) for k in sorted(set(body) - _BODY_FIELDS)]
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
        assert_outcome_refs_only(step["outcome"], f"steps.{name}.outcome")
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
    """Read and write rollout documents; every write is audited in its own transaction."""

    def __init__(
        self,
        store: StoragePort,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._audit = audit or AuditLog()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> str:
        return self._clock().astimezone(UTC).isoformat()

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
        doc[SNAPSHOT_AT] = self._now()  # a whole-document write records its snapshot
        with self._store.transaction() as tx:
            before = tx.get(ROLLOUTS_COLLECTION, rollout_id)
            return self._write(tx, "rollouts.put", principal, before, doc)

    @mutating_verb("rollouts.step", "Record one rollout step (merged server-side, server time)")
    def record_step(self, rollout_id: str, name: str, body: Any, identity: str) -> Document:
        """Merge step ``name`` = ``{applied_at: <server now>, outcome}`` into the document,
        creating it when absent; other steps and the snapshot are kept. A step in
        :data:`FIRST_WINS_STEPS` that is recorded already is kept (409 ``step_recorded``)."""
        principal = require_identity(identity)
        check_id(rollout_id)
        outcome = validate_step(name, body)

        def merge(tx: StoreOps) -> Document:
            before = tx.get(ROLLOUTS_COLLECTION, rollout_id)
            doc = self._base(rollout_id, before)
            if name in FIRST_WINS_STEPS and name in doc["steps"]:
                kept = doc["steps"][name]
                raise Conflict(
                    f"rollouts/{rollout_id} already records step {name} "
                    f"(applied_at {kept.get('applied_at')}); it is recorded once",
                    [_err(f"steps.{name}", STEP_RECORDED, str(kept.get("applied_at")))],
                    code=STEP_RECORDED,
                )
            doc["steps"][name] = {"applied_at": self._now(), "outcome": outcome}
            if len(doc["steps"]) > MAX_STEPS:
                message = f"at most {MAX_STEPS} steps"
                raise Invalid(message, [_err("steps", "too_long", message)])
            doc["updated_by"] = principal
            return self._write(tx, "rollouts.step", principal, before, doc)

        return self._in_transaction(merge)

    @mutating_verb("rollouts.snapshot", "Record a rollout's enabled-rule snapshot (first wins)")
    def record_snapshot(self, rollout_id: str, body: Any, identity: str) -> Document:
        """Set the snapshot while none is recorded; a recorded one is never replaced (409)."""
        principal = require_identity(identity)
        check_id(rollout_id)
        snapshot = validate_snapshot(body)

        def first_wins(tx: StoreOps) -> Document:
            before = tx.get(ROLLOUTS_COLLECTION, rollout_id)
            if snapshot_recorded(before):
                kept = list((before or {}).get("snapshot") or [])
                shown = ", ".join(kept[:20]) + (" ..." if len(kept) > 20 else "")
                raise Conflict(
                    f"rollouts/{rollout_id} already records a snapshot of {len(kept)} "
                    f"rule(s): {shown or '-'}",
                    [_err("snapshot", SNAPSHOT_RECORDED, json.dumps(kept[:MAX_OUTCOME_LIST]))],
                    code=SNAPSHOT_RECORDED,
                )
            doc = self._base(rollout_id, before)
            doc.update({"snapshot": snapshot, SNAPSHOT_AT: self._now(), "updated_by": principal})
            return self._write(tx, "rollouts.snapshot", principal, before, doc)

        return self._in_transaction(first_wins)

    @staticmethod
    def _base(rollout_id: str, before: Mapping[str, Any] | None) -> dict[str, Any]:
        """The stored document's fields (or a new, empty one) to merge a write into."""
        doc: dict[str, Any] = {"id": rollout_id, "version": rollout_id, "snapshot": []}
        for key in ("snapshot", SNAPSHOT_AT):
            if before and before.get(key) is not None:
                doc[key] = before[key]
        doc["steps"] = dict((before or {}).get("steps") or {})
        return doc

    def _write(
        self,
        tx: StoreOps,
        verb: str,
        principal: str,
        before: Document | None,
        doc: Mapping[str, Any],
    ) -> Document:
        after = tx.put(ROLLOUTS_COLLECTION, doc)
        self._audit.write(
            tx,
            identity=principal,
            verb=verb,
            collection=ROLLOUTS_COLLECTION,
            target_id=str(doc["id"]),
            before=before,
            after=after,
        )
        return after

    def _in_transaction(self, write: Callable[[StoreOps], Document]) -> Document:
        """Run ``write`` in a transaction, retrying a write conflict (another recorder won the
        race; the retry re-reads and merges onto its write)."""
        attempt = 1
        while True:
            try:
                with self._store.transaction() as tx:
                    return write(tx)
            except TransientStoreError:
                if attempt >= _TX_ATTEMPTS:
                    raise
                attempt += 1
