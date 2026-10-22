"""t1: the pure planner behind ``actors enrol-models`` (gateway model lists -> endpoint actors)."""

from __future__ import annotations

import copy

import pytest

from culture_rules.actors.model_enrol import parse_pools, plan_model_enrolment

LIVE = [
    "unsloth/Qwen3.8-27B-NVFP4",
    "Qwen/Qwen3-Embedding-0.6B",
    "LiquidAI/LFM2.5-1.2B-Instruct",
    "Qwen/Qwen3-Reranker-0.6B",
    "nvidia/Qwen3.6-35B-A3B-NVFP4",
    "cortex-spark",
    "cortex-spark2",
    "worker-thor",
    "hand-spark2",
    "embedder-orin",
    "embedder-spark2",
    "reranker-spark2",
]
KEY = "grant:LOBES_GATEWAY_API_KEY"
GW = [("spark", "http://localhost:8000/v1", LIVE)]
MATCH = ["cortex-*", "embedder-*", "reranker-*", "unsloth/*"]


def apply(existing, plan):
    out = {d["id"]: d for d in existing}
    for c in plan.changes:
        if c.action in ("create", "update"):
            out[c.id] = c.body
    return list(out.values())


def test_plans_only_match_hits_with_the_enrolled_shape():
    plan = plan_model_enrolment([], GW, MATCH, api_key=KEY)
    ids = {c.id for c in plan.changes}
    assert ids == {
        "cortex-spark",
        "cortex-spark2",
        "embedder-orin",
        "embedder-spark2",
        "reranker-spark2",
        "unsloth/Qwen3.8-27B-NVFP4",
    }
    hf = next(c for c in plan.changes if c.id == "unsloth/Qwen3.8-27B-NVFP4")
    assert hf.action == "refused"
    assert "not a valid actor id" in hf.reason
    assert {c.action for c in plan.changes if c is not hf} == {"create"}
    body = next(c.body for c in plan.changes if c.id == "cortex-spark")
    assert body["id"] == "cortex-spark"
    assert body["name"]
    assert body["kind"] == "agent"
    assert body["machine"] == "spark"
    assert body["enabled"] is True
    assert body["config_source"] == "db"
    assert body["schema_version"] == "1.0"
    assert body["params"] == {
        "base_url": "http://localhost:8000/v1",
        "model": "cortex-spark",
        "concurrency_pool": "cortex-spark",
        "max_concurrency": 1,
        "api_key": KEY,
    }


def test_empty_match_is_an_error():
    with pytest.raises(ValueError):
        plan_model_enrolment([], GW, [])


def test_first_gateway_wins_and_later_ones_are_also_served_by():
    gws = [("spark", "http://a/v1", ["cortex-spark"]), ("thor", "http://b/v1", ["cortex-spark"])]
    plan = plan_model_enrolment([], gws, ["cortex-*"])
    (change,) = plan.changes
    assert change.body["machine"] == "spark"
    assert change.body["params"]["base_url"] == "http://a/v1"
    assert plan.also_served_by == {"cortex-spark": ["thor"]}


def test_unseen_endpoint_actors_are_reported_never_disabled():
    held = {
        "id": "old-model",
        "name": "old-model",
        "kind": "agent",
        "params": {"base_url": "http://localhost:8000/v1/", "model": "old-model"},
        "enabled": True,
    }
    mesh = {"id": "spark-culture", "name": "n", "kind": "agent", "params": {}}
    plan = plan_model_enrolment([held, mesh], GW, MATCH)
    assert plan.unseen == ["old-model"]
    assert all(c.id != "old-model" for c in plan.changes)
    # a model listed by a gateway but outside --match is seen, not unseen
    listed = dict(held, id="worker-thor", params={**held["params"], "model": "worker-thor"})
    assert plan_model_enrolment([listed], GW, MATCH).unseen == []


def test_actors_on_gateways_not_asked_are_not_unseen():
    elsewhere = {
        "id": "worker-orin",
        "name": "worker-orin",
        "kind": "agent",
        "params": {"base_url": "http://127.0.0.1:9000/v1", "model": "worker-orin"},
        "enabled": True,
    }
    assert plan_model_enrolment([elsewhere], GW, MATCH).unseen == []


def test_pool_defaults_to_model_with_override():
    plan = plan_model_enrolment([], GW, ["cortex-*"], pools={"cortex-spark2": "shared"})
    pools = {c.id: c.body["params"]["concurrency_pool"] for c in plan.changes}
    assert pools == {"cortex-spark": "cortex-spark", "cortex-spark2": "shared"}
    assert parse_pools(["a=b"]) == {"a": "b"}
    with pytest.raises(ValueError):
        parse_pools(["nope"])


def test_second_run_plans_zero_changes():
    first = plan_model_enrolment([], GW, MATCH, api_key=KEY)
    second = plan_model_enrolment(apply([], first), GW, MATCH, api_key=KEY)
    assert {c.action for c in second.changes} == {"unchanged", "refused"}
    assert [c.id for c in second.changes if c.action == "refused"] == ["unsloth/Qwen3.8-27B-NVFP4"]
    assert len(second.changes) == len(first.changes)


def test_update_touches_only_enrolled_fields():
    have = {
        "id": "cortex-spark",
        "name": "My cortex",
        "description": "hand written",
        "kind": "agent",
        "capabilities": ["chat"],
        "machine": "old",
        "params": {
            "base_url": "http://old/v1",
            "model": "cortex-spark",
            "concurrency_pool": "p",
            "max_concurrency": 4,
            "api_key": "grant:OLD",
            "extra": {"keep": 1},
        },
        "enabled": False,
        "config_source": "db",
    }
    before = copy.deepcopy(have)
    plan = plan_model_enrolment([have], GW, ["cortex-spark"], api_key=KEY)
    (change,) = plan.changes
    assert change.action == "update"
    assert have == before
    body = change.body
    assert (body["name"], body["description"]) == ("My cortex", "hand written")
    assert body["capabilities"] == ["chat"]
    assert body["enabled"] is False
    assert body["machine"] == "spark"
    assert body["params"]["extra"] == {"keep": 1}
    assert body["params"]["base_url"] == "http://localhost:8000/v1"
    assert body["params"]["concurrency_pool"] == "cortex-spark"
    assert body["params"]["max_concurrency"] == 4
    assert body["params"]["api_key"] == KEY
    # without an api_key argument the existing key is left alone
    plan2 = plan_model_enrolment([have], GW, ["cortex-spark"])
    assert plan2.changes[0].body["params"]["api_key"] == "grant:OLD"


@pytest.mark.parametrize(
    "held",
    [
        {"id": "cortex-spark", "name": "n", "kind": "human", "params": {}},
        {"id": "cortex-spark", "name": "n", "kind": "agent", "params": {}},
        {
            "id": "cortex-spark",
            "name": "n",
            "kind": "agent",
            "params": {"base_url": "http://x", "model": "cortex-spark"},
            "deleted_at": "2026-01-01T00:00:00Z",
        },
    ],
)
def test_conflicting_holder_is_refused_and_untouched(held):
    before = copy.deepcopy(held)
    plan = plan_model_enrolment([held], GW, ["cortex-spark"])
    (change,) = plan.changes
    assert change.action == "refused"
    assert change.body is None
    assert change.reason
    assert held == before


def test_invalid_actor_id_is_refused():
    plan = plan_model_enrolment([], [("spark", "http://a", [" ", "cortex-x"])], ["*"])
    actions = {c.id: c.action for c in plan.changes}
    assert actions == {" ": "refused", "cortex-x": "create"}
