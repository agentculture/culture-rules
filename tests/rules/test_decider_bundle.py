"""decider-step-in-workflows t2: the committed decider actor and decider.assess's registration."""

from __future__ import annotations

from pathlib import Path

from culture_rules.actors.model_rule import ENDPOINT_KINDS
from culture_rules.io.exchange import import_bundle, read_bundle
from culture_rules.model.action_kinds import ACTION_KINDS
from culture_rules.model.describe import ACTION_WORDS
from culture_rules.node.actions.decider import DeciderAssessPort
from culture_rules.node.runner import default_ports
from culture_rules.store.memory import MemoryStore

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "rules" / "decider"
KIND = "decider.assess"


def _actor():
    read = read_bundle(BUNDLE)
    assert read.errors == [], [e.to_dict() for e in read.errors]
    (actor,) = read.bundle.actors
    return actor


def test_the_bundle_holds_one_decider_actor_on_the_spark_gateway():
    actor = _actor()
    assert actor.id == "decider"
    assert actor.kind == "agent"
    assert actor.machine == "spark"
    assert actor.params["base_url"] == "http://127.0.0.1:8001"
    assert actor.params["api_key"] == "grant:LOBES_GATEWAY_API_KEY"
    assert actor.params["domain"] == "general"
    assert actor.params["model"] == "clef"
    assert actor.params["max_concurrency"] == 1


def test_it_imports_and_a_second_enabled_clef_actor_is_refused_model_taken():
    base = MemoryStore()
    plan = import_bundle(BUNDLE, base, apply=True)
    assert plan.errors == []
    assert base.get("actors", "decider")["params"]["model"] == "clef"
    from culture_rules.actors.model_rule import model_holder

    other = {**_actor().to_dict(), "id": "decider-2", "name": "decider-2"}
    assert model_holder(base.find("actors"), other) == "decider"


def test_decider_assess_is_catalogued_routed_described_and_an_endpoint_kind():
    spec = ACTION_KINDS[KIND]
    assert {n for n, p in spec.params.items() if p.required} == {"actor", "state", "questions"}
    assert spec.params["questions"].type == "dict"
    assert "not_after" in spec.params
    assert isinstance(default_ports(MemoryStore(), "spark")[f"action:{KIND}"], DeciderAssessPort)
    assert KIND in ACTION_WORDS
    assert KIND in ENDPOINT_KINDS
