"""The run executor on the MongoDB StoragePort adapter (marker ``mongo``; skips without docker).

Reuses the storage task's replica-set binding, like the claims contract does.
"""

from __future__ import annotations

import pytest

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import RUN_COLLECTIONS, Containment, Executor, step_state
from tests.engine.run_helpers import Clock, human_actor_for_mongo, ports_for, rule
from tests.engine.run_helpers import three_step_workflow_for_mongo as three_steps

pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")
_mongo_tests = pytest.importorskip("tests.store.test_mongo", reason="Mongo binding missing")

pytestmark = pytest.mark.mongo


@pytest.fixture
def mongo_store():
    _mongo_tests.get_rig()  # skips when docker / the image is unavailable
    binding = _mongo_tests.TestMongoStore()
    store = binding.make_store()
    yield store
    _mongo_tests._release_stores()


def test_run_survives_restart_and_cancel_on_mongo(mongo_store):
    clock = Clock()
    a = human_actor_for_mongo()
    ex = Executor(mongo_store, "spark", ports_for(a), clock=clock)
    assert {"runs", "claims", "audit", "controls"} <= set(RUN_COLLECTIONS)
    run = ex.start(rule(), three_steps())
    ex.run_until_idle()
    assert step_state(ex.run(run["id"]), "s2")["status"] == "waiting"

    again = Executor(mongo_store, "spark", ports_for(a), clock=clock)
    assert again.deliver(idempotency_key(run["id"], "s2"), InvocationResult.completed({"ok": True}))
    again.run_until_idle()
    assert again.run(run["id"])["status"] == "succeeded"
    assert a.effects_for("s1") == 1
    assert a.effects_for("s3") == 1

    second = again.start(rule(), three_steps())
    again.run_until_idle()
    Containment(mongo_store, clock=clock).cancel(second["id"], "alice")
    assert again.run(second["id"])["status"] == "cancelled"
