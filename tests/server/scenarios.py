"""Audit scenarios for the definition verbs (consumed by tests/engine/test_audit_lifecycle)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import culture_rules.server.rollouts  # noqa: F401 - registers the rollouts.* verbs
import culture_rules.server.service  # noqa: F401 - registers the definitions.* verbs
from tests.engine.run_helpers import rule


def _body(id: str = "r9") -> dict[str, Any]:
    return rule(id=id, workflow_id=None).to_dict()


def _defs(store: Any) -> Any:
    from culture_rules.server.service import Definitions

    return Definitions(store)


def _create(life: Any, store: Any) -> Callable[[], Any]:
    return lambda: _defs(store).create("rules", _body(), "alice")


def _update(life: Any, store: Any) -> Callable[[], Any]:
    _defs(store).create("rules", _body(), "alice")
    return lambda: _defs(store).update("rules", "r9", {**_body(), "name": "x"}, "alice")


def _set_enabled(life: Any, store: Any) -> Callable[[], Any]:
    _defs(store).create("rules", _body(), "alice")
    return lambda: _defs(store).set_enabled("rules", "r9", False, "alice")


def _import(life: Any, store: Any) -> Callable[[], Any]:
    files = {"rules/r9.json": json.dumps(_body())}
    return lambda: _defs(store).import_files(files, "alice", apply=True)


SERVER_AUDIT_SCENARIOS = {
    "definitions.create": _create,
    "definitions.update": _update,
    "definitions.set_enabled": _set_enabled,
    "definitions.import": _import,
}


def _export_repo(life: Any, store: Any) -> Callable[[], Any]:
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from culture_rules.server.repos import RepoTarget  # noqa: PLC0415

    work = tempfile.mkdtemp(prefix="culture-rules-audit-")
    subprocess.run(["git", "init", "--quiet", work], check=True)  # noqa: S603,S607
    _defs(store).create("rules", _body(), "alice")
    target = RepoTarget("work", work)
    return lambda: _defs(store).export_to_repo(target, "alice", apply=True)


SERVER_AUDIT_SCENARIOS["definitions.export_repo"] = _export_repo


def _put_rollout(life: Any, store: Any) -> Callable[[], Any]:
    from culture_rules.server.rollouts import Rollouts  # noqa: PLC0415

    doc = {"version": "0.18.12", "snapshot": ["r9"], "steps": {}}
    return lambda: Rollouts(store).put("0.18.12", doc, "alice")


SERVER_AUDIT_SCENARIOS["rollouts.put"] = _put_rollout


def _record_rollout_step(life: Any, store: Any) -> Callable[[], Any]:
    from culture_rules.server.rollouts import Rollouts  # noqa: PLC0415

    return lambda: Rollouts(store).record_step("0.18.12", "seed", {"outcome": "ok"}, "alice")


def _record_rollout_snapshot(life: Any, store: Any) -> Callable[[], Any]:
    from culture_rules.server.rollouts import Rollouts  # noqa: PLC0415

    return lambda: Rollouts(store).record_snapshot("0.18.12", {"snapshot": ["r9"]}, "alice")


SERVER_AUDIT_SCENARIOS["rollouts.step"] = _record_rollout_step
SERVER_AUDIT_SCENARIOS["rollouts.snapshot"] = _record_rollout_snapshot


def _migrate_notes(life: Any, store: Any) -> Callable[[], Any]:
    store.put(
        "workflows", {"id": "w9", "name": "W", "description": "why", "notes": "", "steps": []}
    )
    return lambda: _defs(store).migrate_notes("alice", apply=True)


SERVER_AUDIT_SCENARIOS["definitions.migrate_notes"] = _migrate_notes


def _migrate_orphans(life: Any, store: Any) -> Callable[[], Any]:
    # the shared fixture's one rule has no workflow: it is the single orphan
    return lambda: _defs(store).migrate_orphans("alice", apply=True)


SERVER_AUDIT_SCENARIOS["definitions.migrate_orphans"] = _migrate_orphans
