"""culture-rules#124: the shipped repodocs bundle (docs/rules/repodocs) and the registration of
``repodocs.sync`` (catalogue, default port, describe words)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from culture_rules.engine.cron import parse
from culture_rules.io.exchange import import_bundle, read_bundle
from culture_rules.model.action_kinds import ACTION_KINDS
from culture_rules.model.action_step import action_spec
from culture_rules.model.describe import ACTION_WORDS
from culture_rules.node.actions.repodocs import RepodocsSyncPort
from culture_rules.node.runner import default_ports
from culture_rules.store.memory import MemoryStore

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "rules" / "repodocs"
KIND = "repodocs.sync"
ACTOR, WORKFLOW, RULE = "culture-library-repodocs", "repodocs-sync", "repodocs-sync"


def bundle():
    read = read_bundle(BUNDLE)
    assert read.errors == [], [e.to_dict() for e in read.errors]
    return read.bundle


def test_repodocs_sync_is_catalogued_routed_and_described():
    spec = ACTION_KINDS[KIND]
    assert {n for n, p in spec.params.items() if p.required} == {"actor"}
    assert isinstance(default_ports(MemoryStore(), "spark")[f"action:{KIND}"], RepodocsSyncPort)
    assert KIND in ACTION_WORDS


def test_the_bundle_holds_one_actor_one_workflow_and_one_rule():
    b = bundle()
    assert [a.id for a in b.actors] == [ACTOR]
    assert [w.id for w in b.workflows] == [WORKFLOW]
    assert [r.id for r in b.rules] == [RULE]


def test_the_actor_is_a_service_on_spark_with_a_grant_key_and_a_loopback_url():
    (actor,) = bundle().actors
    assert actor.kind == "service"
    assert actor.machine == "spark"
    assert actor.params["api_key"] == "grant:LIBRARY_REPODOCS_PUSH_API_KEY"
    assert urlsplit(actor.params["base_url"]).hostname == "127.0.0.1"
    assert actor.params["repos_root"] == "~/git"
    assert actor.params["state_path"].startswith("~/")


def test_the_rule_ships_disabled_every_15_minutes_on_spark_and_never_overlaps():
    (rule,) = bundle().rules
    assert rule.enabled is False
    assert rule.trigger.kind == "schedule"
    assert rule.trigger.params["cron"] == "*/15 * * * *"
    parse(rule.trigger.params["cron"])
    assert rule.placement.machine == "spark"
    assert rule.concurrency_key == "repodocs-sync"
    assert rule.exclusive_group == "repodocs-sync"
    assert rule.workflow.id == WORKFLOW


def test_the_workflow_is_one_sync_step_through_the_shipped_actor():
    (wf,) = bundle().workflows
    specs = [action_spec(s) for s in wf.steps]
    assert [s["kind"] for s in specs] == [KIND]
    assert specs[0]["params"] == {"actor": ACTOR}
    (step,) = wf.steps
    assert step.timeout_s and step.timeout_s > 300  # beyond the actor's max_seconds
    assert {o.name for o in wf.outputs} >= {"pushed", "errors"}


def test_it_imports_into_a_store_disabled():
    base = MemoryStore()
    plan = import_bundle(BUNDLE, base, apply=True)
    assert plan.errors == []
    assert base.get("rules", RULE)["enabled"] is False
    assert base.get("actors", ACTOR)["params"]["max_seconds"] == 300
