"""The model-holder rule: one endpoint actor per model."""

from culture_rules.actors.model_rule import (
    effective_model,
    is_endpoint_actor,
    model_holder,
    senior_holder,
)

QWEN_FIXER = {
    "id": "qwen-fixer",
    "kind": "agent",
    "harness": "qwen",
    "model": "cortex",
    "machine": "spark2",
    "params": {"model": "cortex", "concurrency_pool": "qwen-spark2"},
    "enabled": True,
}
QWEN_REVIEWER = {**QWEN_FIXER, "id": "qwen-reviewer"}
NEWSPAPER = {
    "id": "newspaper-llm",
    "kind": "agent",
    "machine": "spark",
    "params": {
        "api_key": "grant:LOBES_GATEWAY_API_KEY",
        "base_url": "http://127.0.0.1:8001",
        "concurrency_pool": "qwen-spark2",
        "max_concurrency": 1,
        "model": "cortex-spark2",
    },
    "enabled": True,
}


def _endpoint(id_, model, **extra):
    return {
        "id": id_,
        "kind": "agent",
        "params": {"base_url": "http://127.0.0.1:1", "model": model},
        "enabled": True,
        **extra,
    }


def test_is_endpoint_actor():
    assert is_endpoint_actor(NEWSPAPER)
    assert not is_endpoint_actor(QWEN_FIXER)
    assert not is_endpoint_actor({**NEWSPAPER, "kind": "human"})
    assert not is_endpoint_actor({"kind": "agent", "params": {"base_url": ""}})
    assert not is_endpoint_actor({"kind": "agent", "params": {"base_url": "  "}})
    assert not is_endpoint_actor({"kind": "agent", "params": {"base_url": 5}})
    assert not is_endpoint_actor({"kind": "agent"})
    assert not is_endpoint_actor({"kind": "agent", "params": None})


def test_qwen_actors_never_conflict():
    actors = [QWEN_FIXER, QWEN_REVIEWER, NEWSPAPER]
    assert model_holder(actors, QWEN_FIXER) is None
    assert model_holder(actors, QWEN_REVIEWER) is None
    assert model_holder([QWEN_FIXER], QWEN_REVIEWER) is None


def test_newspaper_holds_cortex_spark2():
    new = _endpoint("cortex-spark2", "cortex-spark2")
    assert model_holder([QWEN_FIXER, NEWSPAPER], new) == "newspaper-llm"


def test_different_model_no_conflict():
    assert model_holder([NEWSPAPER], _endpoint("other", "cortex")) is None


def test_disabled_holder_holds_nothing():
    assert model_holder([{**NEWSPAPER, "enabled": False}], _endpoint("n", "cortex-spark2")) is None


def test_deleted_holder_holds_nothing():
    gone = {**NEWSPAPER, "deleted_at": "2026-10-19T00:00:00Z"}
    assert model_holder([gone], _endpoint("n", "cortex-spark2")) is None
    live = {**NEWSPAPER, "deleted_at": None}
    assert model_holder([live], _endpoint("n", "cortex-spark2")) == "newspaper-llm"


def test_candidate_is_not_its_own_holder():
    assert model_holder([NEWSPAPER], NEWSPAPER) is None


def test_non_endpoint_candidate_never_conflicts():
    cand = {"id": "x", "kind": "agent", "params": {"model": "cortex-spark2"}}
    assert model_holder([NEWSPAPER], cand) is None


def test_candidate_without_model_never_conflicts():
    cand = {"id": "x", "kind": "agent", "params": {"base_url": "http://h"}}
    assert model_holder([_endpoint("a", "")], cand) is None


def test_effective_model_reads_params_then_the_actor_model():
    assert effective_model({"params": {"model": " m1 "}, "model": "m2"}) == "m1"
    assert effective_model({"params": {}, "model": "m2"}) == "m2"
    assert effective_model({"params": {"model": ""}}) is None


def test_senior_holder_is_only_a_holder_with_a_lower_id():
    actors = [_endpoint("b", "m"), _endpoint("c", "m")]
    assert senior_holder(actors, _endpoint("a", "m")) is None
    assert senior_holder(actors, _endpoint("c", "m")) == "b"
    assert model_holder(actors, _endpoint("a", "m")) == "b"
