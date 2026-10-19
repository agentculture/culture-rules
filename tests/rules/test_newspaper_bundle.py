"""#53 t8: the shipped daily newspaper data (docs/rules/newspaper).

Four actors (email, search, llm, library), one workflow and one schedule rule, shipped
disabled, plus ``variables.json`` and its seed script. Nothing personal or secret is
committed: keys are grant references, base URLs are loopback placeholders the operator
replaces, the email allowlist is empty (h18) and no address or device id is shipped (c18).
The workflow has no bridge actor and no agent step (h15). The flow itself is driven in
tests/rules/test_newspaper_flow.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from culture_rules.cli import main
from culture_rules.io.exchange import import_bundle, read_bundle
from culture_rules.model.action_step import action_spec
from culture_rules.model.app_actor import email_sender
from culture_rules.model.variable import validate_variable_value
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.node.actions.newspaper import parse_sections
from culture_rules.store.memory import MemoryStore
from tests.cli.test_nouns_api import store, wire  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "rules" / "newspaper"
VARIABLES = BUNDLE / "variables.json"
SEED = BUNDLE / "seed-variables.sh"
RULE = "daily-newspaper"
WORKFLOW = "daily-newspaper"
EMAIL, SEARCH, LLM, LIBRARY = (
    "newspaper-email",
    "newspaper-search",
    "cortex-spark2",
    "newspaper-library",
)
GATE_STEP, EMAIL_STEP = "email_gate", "email"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
APPLY = "--apply"
SECTIONS_VAR = "newspaper_sections"
TO_VAR = "newspaper_to"
DEVICE_VAR = "newspaper_device"
API_KEY = "api_key"
VALUE = "value"


def bundle():
    read = read_bundle(BUNDLE)
    assert read.errors == [], [e.to_dict() for e in read.errors]
    return read.bundle


def actors() -> dict:
    return {a.id: a for a in bundle().actors}


def workflow():
    (wf,) = bundle().workflows
    return wf


def rule():
    (r,) = bundle().rules
    return r


def every_step(wf):
    for s in wf.steps:
        yield s
        yield from s.body


def kinds_of_actions(wf) -> dict[str, str]:
    """Action step id -> its action kind."""
    out = {}
    for s in every_step(wf):
        spec = action_spec(s)
        if spec is not None:
            out[s.id] = spec["kind"]
    return out


def bundle_files() -> list[Path]:
    return sorted(p for p in BUNDLE.rglob("*") if p.is_file())


# --------------------------------------------------------------------------- the data


def test_the_bundle_reads_without_errors():
    b = bundle()
    assert sorted(actors()) == sorted([EMAIL, SEARCH, LLM, LIBRARY])
    assert [w.id for w in b.workflows] == [WORKFLOW]
    assert [r.id for r in b.rules] == [RULE]


def test_it_imports_into_a_store():
    base = MemoryStore()
    plan = import_bundle(BUNDLE, base, apply=True)
    assert plan.errors == []
    assert base.get("rules", RULE)["enabled"] is False


def test_the_rule_ships_disabled_at_0500_jerusalem():
    r = rule()
    assert r.enabled is False
    assert r.trigger.kind == "schedule"
    assert r.trigger.params == {"cron": "0 5 * * *", "tz": "Asia/Jerusalem"}
    assert r.workflow.id == WORKFLOW
    assert r.action.kind == "noop"


def test_the_rule_maps_the_variables_and_the_slot():
    inputs = rule().workflow.inputs
    assert rule_variable_refs(rule()) == {SECTIONS_VAR, TO_VAR, DEVICE_VAR}
    assert inputs["slot"] == "trigger.data.slot"
    assert inputs["tz"] == "trigger.data.tz"


def test_the_email_actor_sends_from_newsletter_no_reply_to_nobody_yet():  # h18, c28
    email = actors()[EMAIL]
    conn = email.params["connection"]
    assert email.kind == "app"
    assert email.params["surface"] == "email"
    assert email_sender(conn) == "newsletter-no-reply@culture.dev"
    assert conn["recipients"] == []
    assert conn[API_KEY] == "grant:RESEND_API_KEY"


def test_the_search_actor_uses_the_brave_grant():
    search = actors()[SEARCH]
    assert search.params["surface"] == "search"
    assert search.params["connection"] == {API_KEY: "grant:BRAVE_API_KEY"}


def test_the_llm_actor_is_cortex_in_the_qwen_pool_without_a_bridge():  # c3, h15
    llm = actors()[LLM]
    assert llm.kind == "agent"
    assert llm.machine == "spark"
    assert llm.params["model"] == "cortex-spark2"
    assert llm.params[API_KEY] == "grant:LOBES_GATEWAY_API_KEY"
    assert llm.params["concurrency_pool"] == "qwen-spark2"
    assert llm.params["max_concurrency"] == 1
    assert "bridge_url" not in llm.params


def test_the_library_actor_uses_a_dedicated_xteink_key_and_no_device():
    library = actors()[LIBRARY]
    assert library.kind == "service"
    assert library.params[API_KEY] == "grant:XTEINK_PUSH_API_KEY"
    assert "device_id" not in library.params


@pytest.mark.parametrize("actor_id", [EMAIL, SEARCH, LLM, LIBRARY])
def test_every_actor_lives_on_spark_where_its_grants_are(actor_id):
    assert actors()[actor_id].machine == "spark"


@pytest.mark.parametrize("actor_id", [LLM, LIBRARY])
def test_base_urls_are_loopback_placeholders(actor_id):
    host = urlsplit(actors()[actor_id].params["base_url"]).hostname
    assert host in LOOPBACK


# --------------------------------------------------------------------------- the workflow


def test_the_workflow_has_no_agent_step():  # h15
    kinds = {s.kind for s in every_step(workflow())}
    assert kinds <= {"code", "for_each"}


def test_the_workflow_runs_the_four_kinds_through_the_shipped_actors():
    wf = workflow()
    assert sorted(kinds_of_actions(wf).values()) == sorted(
        ["search.web", "llm.complete", "email.send", "library.push"]
    )
    named = {action_spec(s)["params"]["actor"] for s in every_step(wf) if action_spec(s)}
    assert named == {EMAIL, SEARCH, LLM, LIBRARY}


def test_the_workflow_builtins_in_order():
    builtins = [
        s.config.get("builtin")
        for s in every_step(workflow())
        if s.kind == "code" and action_spec(s) is None
    ]
    assert builtins == [
        "newspaper.sections",
        "newspaper.issue",
        "newspaper.filter",
        "newspaper.render",
        "newspaper.issue",
        "newspaper.issue",
        "newspaper.issue",
    ]


def test_the_issue_phases_fence_each_side_effect():  # Codex r1 #1
    phases = [s.config.get("phase") for s in every_step(workflow()) if s.config.get("phase")]
    assert phases == ["claim", "before_email", "before_push", "record"]


def test_the_email_sends_under_the_fenced_durable_key():  # Codex r1 #1
    (email,) = [s for s in every_step(workflow()) if s.id == EMAIL_STEP]
    assert action_spec(email)["params"]["idempotency_key"] == "inputs.idempotency_key"


def test_no_summary_starts_after_the_window():  # Codex r1 #3
    (summary,) = [s for s in every_step(workflow()) if s.id == "summary"]
    assert action_spec(summary)["params"]["not_after"] == "inputs.not_after"


def test_only_the_summary_and_the_email_tolerate_their_failure():
    """The email's failure goes on to the push fence, which fails the run unless the first
    attempt is past its 23-hour window (Codex r3)."""
    tolerant = [s.id for s in every_step(workflow()) if s.config.get("on_error") == "skip"]
    assert tolerant == ["summary", EMAIL_STEP]


def test_the_email_step_sends_until_the_fence_window_closes():  # Codex r3
    (email,) = [s for s in every_step(workflow()) if s.id == EMAIL_STEP]
    assert action_spec(email)["params"]["not_after"] == "inputs.not_after"


def test_the_email_window_comes_from_the_fence():
    edges = {
        (e.source, e.source_port, e.target_port) for e in workflow().edges if e.target == EMAIL_STEP
    }
    assert (GATE_STEP, "send_until", "not_after") in edges


def test_the_workflow_exports_the_delivery_ids():  # h17
    names = {o.name for o in workflow().outputs}
    assert names == {
        "message_id",
        "item_id",
        "date",
        "email_uncertain",
        "email_sent",
        "no_recipients",
    }  # d6


def _step(step_id):
    return next(s for s in workflow().steps if s.id == step_id)


def test_the_email_fence_freezes_the_sender_of_the_email_actor():  # Codex r2 #4
    assert _step(GATE_STEP).config["email_actor"] == EMAIL


def test_the_email_step_sends_the_frozen_recipients_and_sender():
    edges = {(e.source, e.source_port) for e in workflow().edges if e.target == EMAIL_STEP}
    assert {(GATE_STEP, "to"), (GATE_STEP, "sender")} <= edges


# --------------------------------------------------------------------------- the variables


def variables() -> dict:
    return json.loads(VARIABLES.read_text())


def test_the_variables_are_valid_and_carry_nothing_personal():
    values = variables()
    assert set(values) == {SECTIONS_VAR, TO_VAR, DEVICE_VAR}
    for item in values.values():
        validate_variable_value(item[VALUE])
        assert item["description"]
    assert values[TO_VAR][VALUE] == []
    assert values[DEVICE_VAR][VALUE] is None


def test_the_example_sections_parse():
    assert parse_sections(variables()[SECTIONS_VAR][VALUE])


def test_no_address_is_committed():  # c18
    for path in bundle_files():
        if path.suffix == ".json":
            assert "@" not in path.read_text(), path


def test_scan_secrets_finds_nothing_in_the_bundle():
    script = ROOT / "scripts" / "scan-secrets.py"
    paths = [str(p.relative_to(ROOT)) for p in bundle_files()]
    done = subprocess.run(
        [sys.executable, str(script), *paths], cwd=ROOT, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------------------- the seed script


def seed_calls(tmp_path, *args, existing=()) -> list[list[str]]:
    """The ``variables set`` argv the seed script runs, against a stub CLI."""
    log = tmp_path / "calls.jsonl"
    log.write_text("")
    stub = tmp_path / "culture-rules"
    items = json.dumps({"items": [{"name": n} for n in existing]})
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['variables', 'list']:\n"
        f"    print({items!r})\n"
    )
    stub.chmod(0o755)
    env = {**os.environ, "CULTURE_RULES": str(stub)}
    subprocess.run(["bash", str(SEED), *args], env=env, check=True, capture_output=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return [c for c in calls if c[:2] == ["variables", "set"]]


def test_the_seed_script_is_a_dry_run_and_keeps_existing_values(tmp_path):
    dry = seed_calls(tmp_path, existing=(TO_VAR,))
    assert sorted(c[2] for c in dry) == [DEVICE_VAR, SECTIONS_VAR]
    assert all(APPLY not in c for c in dry)
    forced = seed_calls(tmp_path, APPLY, "--force", existing=(TO_VAR,))
    assert len(forced) == 3
    assert all(APPLY in c for c in forced)


def test_the_seed_script_calls_work_through_the_real_cli(
    tmp_path, store, wire, capsys  # noqa: F811
):
    for argv in seed_calls(tmp_path, APPLY):
        assert main([*argv, "--json"]) == 0, capsys.readouterr()
    capsys.readouterr()
    values = {name: store.get_variable(name)[VALUE] for name in variables()}
    assert values == {name: item[VALUE] for name, item in variables().items()}


def test_the_summary_is_skipped_for_a_section_without_results():  # PR #54 review
    from culture_rules.model import condition as cond

    (summary,) = [s for s in every_step(workflow()) if s.id == "summary"]
    when = summary.config["when"]
    ctx = {"deliver": True, "item": {"results": []}}
    assert cond.evaluate(when, {"trigger": ctx, "variables": ctx}) is False


def test_the_summary_runs_for_a_section_with_results():
    from culture_rules.model import condition as cond

    (summary,) = [s for s in every_step(workflow()) if s.id == "summary"]
    ctx = {"deliver": True, "item": {"results": [{"url": "https://a.example"}]}}
    assert cond.evaluate(summary.config["when"], {"trigger": ctx, "variables": ctx}) is True
