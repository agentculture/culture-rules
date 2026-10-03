"""The answer endpoint resumes a waiting run exactly once through the human actor (t17)."""

from __future__ import annotations

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
