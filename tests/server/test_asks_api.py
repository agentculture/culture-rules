"""The answer endpoint resumes a waiting run exactly once through the human actor (t17)."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.actors.human import ASKS_COLLECTION, HumanAdapter  # noqa: E402
from culture_rules.engine.runs import Executor, step_state  # noqa: E402
from culture_rules.events.emit import Emitter  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.actors.test_human import FakeSink, ask_wf  # noqa: E402
from tests.engine.run_helpers import FakeActor, rule  # noqa: E402
from tests.server.conftest import ALICE, dev_app  # noqa: E402


def test_answer_endpoint_resumes_run_once_then_conflicts():
    store = MemoryStore()
    human = HumanAdapter(store, Emitter(FakeSink(), source="culture-rules/test"))
    ex = Executor(store, "spark", {"actor_task": human, "*": FakeActor()})
    run = ex.start(rule(), ask_wf(timeout_s=600))
    ex.run_until_idle()
    (ask,) = store.find(ASKS_COLLECTION)

    client = TestClient(dev_app(store, host="spark"))
    r = client.post(f"/asks/{ask['id']}/answer", json={"answer": "yes"}, headers=ALICE)
    assert r.status_code == 200
    assert r.json()["status"] == "answered"
    again = client.post(f"/asks/{ask['id']}/answer", json={"answer": "no"}, headers=ALICE)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ask_already_answered"
    bad = client.post("/asks/nope/answer", json={"answer": "yes"}, headers=ALICE)
    assert bad.status_code == 404

    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert step_state(doc, "h")["outputs"] == {"answer": "yes"}


def test_answer_frees_the_human_slot_so_a_second_ask_is_admitted():
    """#6: the API answer path releases the human's LimitedActor slot (max_concurrency 1)."""
    from datetime import UTC, datetime

    from culture_rules.model.actor import Actor
    from culture_rules.model.placement import Placement
    from culture_rules.node.actors import ActorRouter
    from tests.engine.run_helpers import Clock, enrol_online, machine, port, step, workflow

    clock = Clock(datetime.now(UTC))  # the API executor uses the wall clock
    store = MemoryStore(clock=clock)
    enrol_online(store, clock, machine("spark"))
    alice = Actor(id="alice", name="alice", kind="human", machine="spark")
    store.put("actors", replace(alice, params={"max_concurrency": 1}).to_dict())
    emitter = Emitter(FakeSink(), source="culture-rules/test")
    router = ActorRouter(
        store,
        ports={"*": FakeActor()},
        factories={"human": lambda a: HumanAdapter(store, emitter, clock=clock)},
        clock=clock,
    )
    ex = Executor(store, "spark", router, clock=clock)
    wf = workflow(
        (
            step(
                "h",
                "actor_task",
                outputs=(port("answer", "any"),),
                placement=Placement(actor="alice"),
                config={"question": "Ship it?", "options": ["yes", "no"]},
            ),
        )
    )
    first = ex.start(rule(), wf)
    ex.run_until_idle()
    (ask,) = store.find(ASKS_COLLECTION)
    assert len(store.get("actor_usage", "alice")["inflight"]) == 1

    client = TestClient(dev_app(store, host="spark"))
    r = client.post(f"/asks/{ask['id']}/answer", json={"answer": "yes"}, headers=ALICE)
    assert r.status_code == 200
    usage = store.get("actor_usage", "alice")
    assert usage["inflight"] == []
    assert ask["idempotency_key"] in usage["done"]  # completed: remembered as done

    ex.run_until_idle()
    assert ex.run(first["id"])["status"] == "succeeded"
    second = ex.start(rule(), wf)
    ex.run_until_idle()
    assert step_state(ex.run(second["id"]), "h")["status"] == "waiting"  # admitted, not blocked
