"""d21 phase 2: the shipped PR fixer data (docs/rules/pr-fixer) - three workflows, seven rules,
plus d25's GitGuardian report (rules ``pr-fixer-secrets`` and ``pr-fixer-secrets-late``,
workflow ``report-secrets``).

Five trigger rules (one trigger type each, d13; ``pr-fixer-conflict`` is d31) start a
``pr-fix`` chain on the PR's key;
three stage rules continue it on ``rules.run.succeeded``: review-commit, refix, publish.
Everything ships disabled. Trigger rules fire only for an open, same-repo, non-draft PR of an
allow-listed repo; a comment or review comment only when it asks (``/fix`` or an
@mention of the App, as listed in ``vars.fixer_comment_triggers``). The chain itself is run
end to end in tests/rules/test_pr_fixer_chain.py.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from culture_rules.actors.trusted import (
    ROLE_FIX,
    ROLE_PUBLISH,
    ROLE_REVIEW,
    TRUSTED_ACTOR_DIGESTS,
    TRUSTED_WORKFLOWS,
    actor_digest,
    workflow_digest,
)
from culture_rules.cli import main
from culture_rules.io.exchange import bundle_files
from culture_rules.model.condition import evaluate
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.node.actions.fixer_levels import load_ladder
from culture_rules.server.service import Definitions, Invalid
from culture_rules.store.memory import MemoryStore
from tests.cli.test_nouns_api import store, wire  # noqa: F401
from tests.events.fakes import envelope
from tests.node.test_node import Cluster
from tests.rules.chain_world import (
    BUNDLE,
    CHAIN_VARIABLES,
    DISPATCH_RULE,
    PROGRESS_RULES,
    RETRY_CAPACITY_RULE,
    RETRY_FAILED_RULE,
    RETRY_RULE,
    SECRETS_LATE_RULE,
    SECRETS_RULE,
    STAGE_RULES,
    STOP_RULES,
    TRIGGER_RULES,
    bundle,
    rule_docs,
    workflow_docs,
)
from tests.rules.test_pr_fixer_single import TRUSTED, pr_facts, seed
from tests.server.test_github_hook_intent import REAL_BODIES

SEED = BUNDLE / "seed-variables.sh"
KEY = "pr-fixer:{trigger.data.repository}#{trigger.data.number}"
TYPES = {
    "pr-fixer-checks": "github.pr.checks_settled",
    "pr-fixer-conflict": "github.pr.conflicting",
    "pr-fixer-comment": "github.comment.created",
    "pr-fixer-review": "github.review.submitted",
    "pr-fixer-review-comment": "github.review_comment.created",
}
INTENT_RULES = ("pr-fixer-comment", "pr-fixer-review-comment")
AUTHOR_RULES = ("pr-fixer-comment", "pr-fixer-review", "pr-fixer-review-comment")


# --------------------------------------------------------------------------- the data


def test_the_bundle_is_twenty_four_disabled_rules_and_eight_workflows():
    b = bundle()
    assert sorted(w.id for w in b.workflows) == [
        "pr-fix",
        "publish-fix",
        "queue-add",
        "queue-add-capacity",
        "queue-progress",
        "queue-stop",
        "report-secrets",
        "review-commit",
    ]
    reports = (SECRETS_RULE, SECRETS_LATE_RULE)
    queue = (DISPATCH_RULE, RETRY_RULE, RETRY_FAILED_RULE, RETRY_CAPACITY_RULE, *PROGRESS_RULES)
    assert sorted(r.id for r in b.rules) == sorted(
        TRIGGER_RULES + STAGE_RULES + reports + queue + STOP_RULES
    )
    for r in b.rules:
        assert r.enabled is False
        assert r.placement is not None
        assert r.placement.machine == "spark2"
    by = {r.id: r for r in b.rules}
    retries = (RETRY_RULE, RETRY_FAILED_RULE, RETRY_CAPACITY_RULE)
    keyed = (*TRIGGER_RULES, *STAGE_RULES, DISPATCH_RULE, *retries)
    for rid in keyed:
        r = by[rid]
        assert r.concurrency_key == KEY
        assert r.on_failure.kind == "github.comment"
        assert r.on_failure.only_at_chain_end
        assert r.on_failure.params["body"].startswith("PR fixer handed back (")
        assert r.on_failure.params["status"] is True  # d26: the chain's status comment
    # the stages that build, review or push end a chain with a comment (d26 status)
    for rid in ("pr-fixer-review-commit", "pr-fixer-publish", DISPATCH_RULE):
        assert by[rid].action.kind == "github.comment"
        assert by[rid].action.only_at_chain_end is True
        assert by[rid].action.params["status"] is True
    # #35 d29: the trigger rules only queue the PR: not an attempt, nothing to say
    for rid, kind in TYPES.items():
        assert by[rid].trigger.params == {"type": kind}
        assert by[rid].workflow.id == "queue-add"
        assert by[rid].counts_toward_budget is False
        assert by[rid].max_attempts is None
        assert by[rid].action.kind == "noop"
        assert "trusted_authors" not in by[rid].workflow.inputs
    # the dispatch rule runs pr-fix: the counted attempt (3 per PR)
    dispatch = by[DISPATCH_RULE]
    assert dispatch.trigger.params == {"type": "rules.queue.dispatch"}
    assert dispatch.workflow.id == "pr-fix"
    assert dispatch.max_attempts == 3
    assert dispatch.counts_toward_budget is True
    # d30: a re-fix and a try that did not pass go back in the queue as retries
    for rid in ("pr-fixer-refix", RETRY_RULE):
        assert by[rid].workflow.id == "queue-add"
        assert by[rid].workflow.inputs["retry"] == {"$literal": True}
        assert by[rid].counts_toward_budget is False
        assert by[rid].max_attempts is None
    for rid, wid in (
        ("pr-fixer-review-commit", "review-commit"),
        ("pr-fixer-publish", "publish-fix"),
    ):
        assert by[rid].workflow.id == wid
        assert by[rid].counts_toward_budget is False
        assert by[rid].max_attempts is None
    for rid in (*STAGE_RULES, RETRY_RULE):
        assert by[rid].trigger.params == {"type": "rules.run.succeeded"}
    # the queue moves without a key: one queue for every PR
    for rid in PROGRESS_RULES:
        assert by[rid].concurrency_key is None
        assert by[rid].workflow.id == "queue-progress"
        assert by[rid].action.kind == "noop"
    assert by["pr-fixer-queue-sweep"].trigger.kind == "schedule"
    # d34: a stop has no key (it never waits behind the chain it stops) and never writes the
    # story's status comment itself (queue.stop ends it); a failed stop says so plainly
    for rid, kind in zip(STOP_RULES, ("github.comment.created", "github.reaction.added")):
        r = by[rid]
        assert r.trigger.params == {"type": kind}
        assert r.workflow.id == "queue-stop"
        assert r.concurrency_key is None
        assert r.action.kind == "noop"
        assert r.on_failure.kind == "github.comment"
        assert "status" not in r.on_failure.params
        assert r.on_failure.params["body"].startswith("PR fixer stop failed (")


def test_queue_stop_stops_then_sweeps_after_a_pause():
    wf = workflow_docs()["queue-stop"]
    steps = {s["id"]: s for s in wf["steps"]}
    assert list(steps) == ["stop", "settle", "sweep"]
    assert steps["stop"]["config"] == {
        "builtin": "queue.stop",
        "key_prefix": "pr-fixer:",
        "lookup_actor": "github-app",
        "queue": "pr-fixer",
        "levels": "fixer_levels",  # #39: every level's queue too
    }
    assert steps["settle"]["kind"] == "wait"
    assert steps["sweep"]["config"] == {**steps["stop"]["config"], "sweep": True}
    assert all(s.get("placement") is None for s in wf["steps"])  # no actor: non-agentic


def test_the_files_are_in_canonical_export_form():
    for rel, text in bundle_files(bundle(), "json").items():
        assert (BUNDLE / rel).read_text(encoding="utf-8") == text, rel


def test_each_shipped_workflow_is_trusted_in_its_role_only():
    for wid, role in (
        ("pr-fix", ROLE_FIX),
        ("review-commit", ROLE_REVIEW),
        ("publish-fix", ROLE_PUBLISH),
    ):
        digest = workflow_digest(workflow_docs()[wid])
        assert digest in TRUSTED_WORKFLOWS[role], (
            f"docs/rules/pr-fixer/workflows/{wid}.json now hashes to {digest}: if the change is "
            f"intended, put it in TRUSTED_WORKFLOWS[{role!r}] in culture_rules/actors/trusted.py"
        )
        others = [r for r in TRUSTED_WORKFLOWS if r != role]
        assert all(digest not in TRUSTED_WORKFLOWS[r] for r in others)


def test_pr_fix_builds_and_gates_and_never_reviews_or_pushes():
    wf = workflow_docs()["pr-fix"]
    top = {s["id"]: s for s in wf["steps"]}
    assert list(top) == ["quiet", "secrets", "threads", "sonar", "fix"]
    assert top["secrets"]["config"] == {"builtin": "gitguardian.hold", "actor": "github-app"}
    assert top["sonar"]["config"] == {"builtin": "sonar.gate_issues"}
    fix = top["fix"]
    body = {b["id"]: b for b in fix["body"]}
    assert list(body) == [  # #39: one agent step per level, the hand-off, select, one gate
        "agent_qwen",
        "agent_codex",
        "handoff_export",
        "handoff_import",
        "select",
        "gate",
    ]
    qwen, codex = body["agent_qwen"], body["agent_codex"]
    assert {k: v for k, v in qwen["config"].items() if k != "when"} == {
        "mode": "yolo",
        "require_commit": True,
    }
    assert qwen["placement"]["actor"] == "qwen-fixer"
    assert {k: v for k, v in codex["config"].items() if k != "when"} == {
        "require_commit": True,
        "sandbox": "workspace-write",
    }
    assert codex["placement"]["actor"] == "codex-fixer"
    for sid in ("agent_codex", "handoff_export", "handoff_import"):
        assert body[sid]["config"]["when"] == {
            "cmp": "==",
            "left": {"field": "fixer"},
            "op": "compare",
            "right": {"literal": "codex-fixer"},
        }
    assert body["handoff_export"]["placement"]["machine"] == "spark"
    assert body["handoff_import"]["placement"]["machine"] == "spark2"
    assert body["select"]["config"]["builtin"] == "select"
    gate = body["gate"]
    assert gate["config"] == {"builtin": "gate"}
    assert gate["placement"]["machine"] == "spark2"
    text = json.dumps(wf)
    for absent in ("github.push", "codex-reviewer", '"builtin": "review"', "github.review_reply"):
        assert absent not in text
    names = {o["name"] for o in wf["outputs"]}
    assert {"commit_sha", "start_sha", "base_sha", "diff", "bundle", "verdict"} <= names
    assert {"implementer", "reviewer"} <= names  # #39: for review-commit's routing


PUBLISH_FIX_DIGEST = "sha256:0fa92074e1cfa67c1388f0423fc379063fd82c5886bc619c5ef4ffcc83c23680"
"""publish-fix as shipped by d21: #39 adds no push path, so it is unchanged."""
D30_PR_FIX_DIGEST = "sha256:307e82fc96eba7a36be1781b2f550728fbf232d37d776ed878071ae7f74ae032"


def test_publish_fix_is_unchanged_by_the_escalation():
    """#39 t4: no new code path pushes. publish-fix, its placement and its trust are as
    shipped; pr-fix still holds no push, and the d30 pr-fix stays trusted (runs pinned to
    it may still push) next to the new one, each in its own role only."""
    assert workflow_digest(workflow_docs()["publish-fix"]) == PUBLISH_FIX_DIGEST
    assert TRUSTED_WORKFLOWS[ROLE_PUBLISH] == frozenset({PUBLISH_FIX_DIGEST})
    assert D30_PR_FIX_DIGEST in TRUSTED_WORKFLOWS[ROLE_FIX]
    pr_fix = json.dumps(workflow_docs()["pr-fix"])
    assert "github.push" not in pr_fix
    assert '"kind": "github.' not in pr_fix


REVIEW_CODEX, REVIEW_QWEN = "review_codex", "review_qwen"
REVIEW_STEPS = {REVIEW_CODEX: "codex-reviewer", REVIEW_QWEN: "qwen-reviewer"}
"""#39: one review step per reviewer, routed by the try's reviewer."""


def test_review_commit_reviews_and_publish_fix_pushes():
    review = workflow_docs()["review-commit"]
    steps = {s["id"]: s for s in review["steps"]}
    assert list(steps) == [*REVIEW_STEPS, "verdict"]
    for sid, actor in REVIEW_STEPS.items():
        assert steps[sid]["placement"]["actor"] == actor
        assert steps[sid]["config"]["sandbox"] == "read-only"
        assert "instruction" not in {p["name"] for p in steps[sid]["inputs"]}
    assert steps["verdict"]["config"] == {"builtin": "review", "review_steps": list(REVIEW_STEPS)}
    kinds = [s.get("config", {}).get("action", {}).get("kind") for s in review["steps"]]
    assert "github.push" not in kinds
    publish = workflow_docs()["publish-fix"]
    steps = {s["id"]: s for s in publish["steps"]}
    assert list(steps) == ["push", "threads", "pick", "replies"]
    params = steps["push"]["config"]["action"]["params"]
    assert steps["push"]["config"]["action"]["kind"] == "github.push"
    assert params["gate_verdict"] == "inputs.verdict"
    assert not [k for k in params if "review" in k]  # enforced from the store, never wired
    assert steps["push"]["placement"]["machine"] == "spark2"


D21_REVIEW_COMMIT_DIGEST = "sha256:79064f76264a469fc83ef2ed7e2b2d9e2979fbf58f339428319ac3c2a7b158e6"
"""review-commit as shipped by d21 (one review step): still trusted while runs pinned to it
may still record a review."""


def test_the_d21_review_commit_stays_trusted_next_to_the_new_one():
    assert D21_REVIEW_COMMIT_DIGEST in TRUSTED_WORKFLOWS[ROLE_REVIEW]
    assert workflow_digest(workflow_docs()["review-commit"]) != D21_REVIEW_COMMIT_DIGEST
    assert len(TRUSTED_WORKFLOWS[ROLE_REVIEW]) == 2


def _routes(reviewer) -> list[str]:
    """The review steps whose ``when`` holds for a passing gate, a whole diff and the try's
    ``reviewer`` (``...`` when the input is absent)."""
    values = {"gate_verdict": "pass", "diff_truncated": False}
    if reviewer is not ...:
        values["reviewer"] = reviewer
    steps = workflow_docs()["review-commit"]["steps"]
    return [
        s["id"]
        for s in steps
        if s["id"] in REVIEW_STEPS
        and evaluate(s["config"]["when"], {"trigger": values, "variables": values})
    ]


@pytest.mark.parametrize(
    ("reviewer", "routes"),
    [
        (..., [REVIEW_CODEX]),  # no fixer_levels: as before #39
        (None, [REVIEW_CODEX]),
        ("", [REVIEW_CODEX]),
        ("codex-reviewer", [REVIEW_CODEX]),
        ("qwen-reviewer", [REVIEW_QWEN]),
        ("claude-reviewer", []),  # an unknown reviewer runs no review: review_missing
    ],
)
def test_the_tries_reviewer_routes_exactly_one_review_step(reviewer, routes):
    assert _routes(reviewer) == routes


@pytest.mark.parametrize("sid", list(REVIEW_STEPS))
@pytest.mark.parametrize(
    "values",
    [
        {"gate_verdict": "fail", "diff_truncated": False},
        {"gate_verdict": "pass", "diff_truncated": True},
    ],
)
def test_no_review_step_runs_on_a_failed_gate_or_a_cut_diff(sid, values):
    (step,) = (s for s in workflow_docs()["review-commit"]["steps"] if s["id"] == sid)
    for reviewer in REVIEW_STEPS.values():
        scope = {**values, "reviewer": reviewer}
        assert not evaluate(step["config"]["when"], {"trigger": scope, "variables": scope})


def test_the_review_commit_rule_maps_the_tries_reviewer():
    inputs = rule_docs()["pr-fixer-review-commit"]["workflow"]["inputs"]
    assert inputs["reviewer"] == "trigger.data.outputs.reviewer"
    wf = workflow_docs()["review-commit"]
    assert "reviewer" in {p["name"] for p in wf["inputs"]}
    into = {(e["target"], e["target_port"]): e["source"] for e in wf["edges"]}
    for sid in REVIEW_STEPS:
        assert into[(sid, "reviewer")] == "inputs"


CODEX_FIXER_DIGEST = "sha256:35c050a2d423e7e2c075ae6b7bf2358dc92041af1f6e79dc1dd73a6e71f77e8e"
"""#39 t6: the committed codex-fixer's digest, as docs/operations/pr-fixer.md lists it (not
pinned: like qwen-fixer, everything it produces is reviewed; the digest is recorded on each
bridge invocation)."""


def _actor(actor_id: str) -> dict:
    (actor,) = (a for a in bundle().actors if a.id == actor_id)
    return actor.to_dict()


FIXER_ACTORS = ("claude-fixer", "codex-fixer")
"""#39 t6: the fixer actors the bundle ships (qwen-fixer is registered by hand on spark2)."""


def test_the_bundle_ships_both_reviewer_actors_and_both_are_trusted():
    assert sorted(a.id for a in bundle().actors) == sorted([*REVIEW_STEPS.values(), *FIXER_ACTORS])
    for actor_id in REVIEW_STEPS.values():
        digest = actor_digest(_actor(actor_id))
        assert digest in TRUSTED_ACTOR_DIGESTS[actor_id], (
            f"docs/rules/pr-fixer/actors/{actor_id}.json now hashes to {digest}: if intended, "
            f"add it to TRUSTED_ACTOR_DIGESTS[{actor_id!r}] in culture_rules/actors/trusted.py"
        )


def test_qwen_reviewer_is_a_read_only_plan_mode_reviewer_on_the_qwen_pool():
    doc = _actor("qwen-reviewer")
    assert (doc["kind"], doc["harness"], doc["machine"]) == ("agent", "qwen", "spark2")
    params = doc["params"]
    assert params["mode"] == "plan"  # Qwen's read-only: an approval policy, not a sandbox
    assert params["sandbox"] == "read-only"
    assert params["reviewer"] is True
    assert params["locked_instruction"] == "pr-fixer-review"
    assert params["concurrency_pool"] == "qwen-spark2"  # one Qwen job at a time
    assert params["max_concurrency"] == 1
    assert params["bridge_token"].startswith("grant:")


def test_codex_fixer_is_level_twos_workspace_write_codex_agent_on_spark():
    doc = _actor("codex-fixer")
    assert doc["kind"] == "agent"
    assert doc["harness"] == "codex"
    assert doc["machine"] == "spark"
    assert doc["enabled"] is True
    params = doc["params"]
    assert params["sandbox"] == "workspace-write"
    assert params["concurrency_pool"] == "codex-spark"  # the level-2 queue's cap
    assert params["max_concurrency"] == 1
    assert params["bridge_token"] == "grant:RULES_CODEX_FIXER_TOKEN"  # a grant name only
    assert params["bridge_url"] == "http://127.0.0.1:8097"  # 8095 is weather-web on spark
    assert params["callback_url"] == "http://127.0.0.1:18765"


def test_codex_fixer_is_never_a_reviewer_and_takes_the_tries_instruction():
    params = _actor("codex-fixer")["params"]
    assert "reviewer" not in params  # a reviewer flag would let it review (verdict step)
    assert "locked_instruction" not in params  # the only locked brief is the reviewer's


def test_codex_fixer_sits_on_the_pool_its_seeded_level_names():
    mem = MemoryStore()
    seed(mem, {"fixer_levels": CHAIN_VARIABLES["fixer_levels"]})
    ladder = load_ladder(mem, {"levels": "fixer_levels"})
    (two,) = (lv for lv in ladder.levels if lv.actor == "codex-fixer")
    assert two.pool == _actor("codex-fixer")["params"]["concurrency_pool"]


def test_codex_fixer_like_qwen_fixer_is_not_pinned_its_output_is_reviewed():
    assert "codex-fixer" not in TRUSTED_ACTOR_DIGESTS
    assert actor_digest(_actor("codex-fixer")) == CODEX_FIXER_DIGEST, (
        "docs/rules/pr-fixer/actors/codex-fixer.json changed its digest: update "
        "CODEX_FIXER_DIGEST here and the digest in docs/operations/pr-fixer.md"
    )


CLAUDE_FIXER_DIGEST = "sha256:2d03fa3066d563411afe580572aa297bfcdc49dc375bd10e1398a72423713c1d"
"""#56 t2: the committed claude-fixer's digest (not pinned: its output is reviewed)."""


def test_claude_fixer_is_level_ones_workspace_write_claude_agent_on_spark():
    doc = _actor("claude-fixer")
    assert doc["kind"] == "agent"
    assert doc["harness"] == "claude"
    assert doc["machine"] == "spark"
    assert doc["enabled"] is True
    params = doc["params"]
    assert params["sandbox"] == "workspace-write"
    assert params["concurrency_pool"] == "claude-spark"
    assert params["max_concurrency"] == 1
    assert params["bridge_token"] == "grant:RULES_CLAUDE_FIXER_TOKEN"  # a grant name only
    assert params["bridge_url"] == "http://127.0.0.1:8096"
    assert params["callback_url"] == "http://127.0.0.1:18765"


def test_claude_fixer_is_never_a_reviewer_and_takes_the_tries_instruction():
    params = _actor("claude-fixer")["params"]
    assert "reviewer" not in params
    assert "locked_instruction" not in params


def test_claude_fixer_is_not_pinned_its_output_is_reviewed():
    assert "claude-fixer" not in TRUSTED_ACTOR_DIGESTS
    assert actor_digest(_actor("claude-fixer")) == CLAUDE_FIXER_DIGEST


def test_the_two_fixers_use_distinct_bridge_ports():
    urls = {_actor(a)["params"]["bridge_url"] for a in FIXER_ACTORS}
    assert len(urls) == len(FIXER_ACTORS)


def test_conditions_reference_the_variables_never_a_copied_list():
    for r in bundle().rules:
        if r.id in PROGRESS_RULES:
            continue  # the queue as a whole: no repo to check (the dispatch rule checks it)
        refs = rule_variable_refs(r)
        assert {"fixer_repos", "fixer_excluded_repos"} <= refs, r.id
        if r.id in INTENT_RULES:
            assert {"fixer_comment_triggers", "fixer_stop_triggers"} <= refs
        if r.id == "pr-fixer-stop":
            assert "fixer_stop_triggers" in refs
        if r.workflow.id in ("pr-fix", "publish-fix"):
            assert r.workflow.inputs["trusted_authors"] == {"$var": "trusted_authors"}


# --------------------------------------------------------------------------- import


def _files() -> dict[str, str]:
    """The definition files: the manifests (variables.json, release.json) are not imported."""
    return {
        p.relative_to(BUNDLE).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(BUNDLE.glob("*/*.json"))
    }


def test_import_with_apply_validates_and_writes_the_definitions():
    mem = MemoryStore()
    seed(mem, CHAIN_VARIABLES)
    defs = Definitions(mem)
    files = _files()
    for prefix in ("actors/", "workflows/", "rules/"):
        part = {k: v for k, v in files.items() if k.startswith(prefix)}
        plan = defs.import_files(part, "admin@test", apply=True)
        assert plan["applied"] is True, plan
        assert plan["errors"] == [], plan
    assert sorted(d["id"] for d in mem.find("workflows")) == [
        "pr-fix",
        "publish-fix",
        "queue-add",
        "queue-add-capacity",
        "queue-progress",
        "queue-stop",
        "report-secrets",
        "review-commit",
    ]
    assert all(d["enabled"] is False for d in mem.find("rules"))
    again = defs.import_files(files, "admin@test", apply=False)
    assert {c["action"] for c in again["changes"]} == {"unchanged"}


def test_import_before_the_comment_triggers_are_seeded_is_refused():
    mem = MemoryStore()
    seed(mem, {k: v for k, v in CHAIN_VARIABLES.items() if k != "fixer_comment_triggers"})
    defs, files = Definitions(mem), _files()
    with pytest.raises(Invalid) as err:
        defs.import_files(files, "admin@test", apply=True)
    assert "variable_undefined" in {e["code"] for e in err.value.errors}


# --------------------------------------------------------------------------- the seed script


def seed_calls(tmp_path, *args, existing=("trusted_authors",), script=SEED) -> list[list[str]]:
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
    subprocess.run(["bash", str(script), *args], env=env, check=True, capture_output=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return [c for c in calls if c[:2] == ["variables", "set"]]


def test_seed_script_is_a_dry_run_and_never_overwrites_without_force(tmp_path):
    dry = seed_calls(tmp_path)
    names = [c[2] for c in dry]
    assert "trusted_authors" not in names  # already set: kept unless --force
    assert set(names) == set(CHAIN_VARIABLES) - {"trusted_authors"}
    assert all("--apply" not in c for c in dry)
    forced = seed_calls(tmp_path, "--apply", "--force")
    assert {c[2] for c in forced} == set(CHAIN_VARIABLES)
    assert all("--apply" in c for c in forced)


def test_seed_script_calls_work_through_the_real_cli(tmp_path, store, wire, capsys):  # noqa: F811
    for argv in seed_calls(tmp_path, "--apply", existing=()):
        assert main([*argv, "--json"]) == 0, capsys.readouterr()
    capsys.readouterr()
    values = {name: store.get_variable(name)["value"] for name in CHAIN_VARIABLES}
    assert values["fixer_comment_triggers"] == ["/fix", "@rules-culture-dev"]
    assert values["fixer_repos"] == ["agentculture/culture-rules-tester"]
    assert values["fixer_excluded_repos"] == []
    assert "qodo-code-review[bot]" in values["trusted_authors"]
    assert store.get_variable("fixer_comment_triggers")["description"]


def manifest() -> list[dict]:
    return json.loads((BUNDLE / "variables.json").read_text(encoding="utf-8"))


def test_variables_json_holds_every_seed_variable_with_a_description():
    items = manifest()
    assert all(set(i) == {"name", "value", "description"} for i in items)
    assert all(isinstance(i["description"], str) for i in items)
    assert all(i["description"] for i in items)
    names = [i["name"] for i in items]
    assert len(names) == len(set(names))
    assert set(names) == set(CHAIN_VARIABLES)  # every variable the rules reference


def test_seed_script_writes_exactly_what_variables_json_holds(tmp_path):
    calls = seed_calls(tmp_path, "--apply", existing=())
    assert calls == [
        [
            "variables",
            "set",
            i["name"],
            "--value",
            json.dumps(i["value"]),
            "--description",
            i["description"],
            "--apply",
        ]
        for i in manifest()
    ]


def test_seed_script_reads_the_manifest_beside_it(tmp_path):
    """One source: the script carries no values of its own, it reads variables.json."""
    copy = tmp_path / "bundle"
    copy.mkdir()
    (copy / "seed-variables.sh").write_text(SEED.read_text(encoding="utf-8"))
    one = [{"name": "only_one", "value": {"a": [1, "b c"]}, "description": 'it\'s "quoted"'}]
    (copy / "variables.json").write_text(json.dumps(one))
    (calls,) = seed_calls(tmp_path, existing=(), script=copy / "seed-variables.sh")
    assert calls == [
        "variables",
        "set",
        "only_one",
        "--value",
        '{"a": [1, "b c"]}',
        "--description",
        'it\'s "quoted"',
    ]
    assert "trusted_authors" not in SEED.read_text(encoding="utf-8")  # no value in the script


def test_seed_script_fails_on_a_broken_manifest(tmp_path):
    copy = tmp_path / "bundle"
    copy.mkdir()
    (copy / "seed-variables.sh").write_text(SEED.read_text(encoding="utf-8"))
    (copy / "variables.json").write_text('[{"name": "x"}]')
    with pytest.raises(subprocess.CalledProcessError):
        seed_calls(tmp_path, existing=(), script=copy / "seed-variables.sh")


# --------------------------------------------------------------------------- firing


EVENTS = {
    "pr-fixer-checks": lambda **o: pr_facts(
        **{"conclusion": "failure", "settled_by": "all_completed", "state": "open", **o}
    ),
    "pr-fixer-conflict": lambda **o: pr_facts(**{"mergeable_state": "dirty", "state": "open", **o}),
    "pr-fixer-comment": lambda **o: pr_facts(
        **{"comment": "/fix", "command": "/fix", "pr_enriched": True, "state": "open", **o}
    ),
    "pr-fixer-review": lambda **o: pr_facts(
        **{"review_state": "changes_requested", "state": "open", **o}
    ),
    "pr-fixer-review-comment": lambda **o: pr_facts(
        **{"mention": "@rules-culture-dev", "state": "open", **o}
    ),
}


def cluster(variables: dict | None = None) -> Cluster:
    c = Cluster("spark2")
    for doc in rule_docs(enabled=True).values():
        c.base.put("rules", doc)
    for doc in workflow_docs().values():
        c.base.put("workflows", doc)
    seed(c.base, variables or CHAIN_VARIABLES)
    c.start()
    return c


def fires(rule_id: str, *, c: Cluster | None = None, drop=(), **data) -> bool:
    c = c or cluster()
    facts = EVENTS[rule_id](**data)
    for key in drop:
        facts.pop(key, None)
    c.publish(envelope(1, type=TYPES[rule_id], data=facts))
    reports = c.cycle()
    assert all(not r.errors for r in reports.values()), reports
    return c.run(rule_id, "evt_1") is not None


@pytest.mark.parametrize("rule_id", sorted(TYPES))
def test_each_trigger_rule_fires_on_its_own_event_for_an_open_pr(rule_id):
    assert fires(rule_id)


@pytest.mark.parametrize("rule_id", sorted(TYPES))
def test_a_closed_pr_or_one_of_unknown_state_never_starts_a_run(rule_id):
    assert not fires(rule_id, state="closed")
    assert not fires(rule_id, drop=("state",))


@pytest.mark.parametrize("rule_id", INTENT_RULES)
@pytest.mark.parametrize("name", sorted(REAL_BODIES))
def test_the_three_real_bodies_start_nothing(rule_id, name):
    # the receiver reads no command and no mention from them (test_github_hook_intent)
    assert not fires(rule_id, drop=("command", "mention"), comment=REAL_BODIES[name][:500])


@pytest.mark.parametrize("rule_id", INTENT_RULES)
def test_a_comment_starts_a_run_only_by_command_or_mention(rule_id):
    assert fires(rule_id, command="/fix", drop=("mention",))
    assert fires(rule_id, mention="@rules-culture-dev", drop=("command",))
    assert not fires(rule_id, command="/help", drop=("mention",))
    assert not fires(rule_id, mention="@someone-else", drop=("command",))


def test_narrowing_the_variable_to_fix_only_ignores_mentions():
    c = cluster({**CHAIN_VARIABLES, "fixer_comment_triggers": ["/fix"]})
    assert not fires("pr-fixer-comment", c=c, mention="@rules-culture-dev", drop=("command",))
    c.publish(envelope(2, type=TYPES["pr-fixer-comment"], data=EVENTS["pr-fixer-comment"]()))
    c.cycle()
    assert c.run("pr-fixer-comment", "evt_2") is not None


@pytest.mark.parametrize("rule_id", AUTHOR_RULES)
def test_a_non_trusted_author_or_the_app_itself_does_not_fire(rule_id):
    assert not fires(rule_id, author="mallory")
    assert not fires(rule_id, self_authored=True)
    assert fires(rule_id, author=TRUSTED)


@pytest.mark.parametrize("rule_id", sorted(TYPES))
@pytest.mark.parametrize(
    "data",
    [
        {"repository": "o/excluded", "head_repo": "o/excluded", "base_repo": "o/excluded"},
        {"repository": "o/other", "head_repo": "o/other", "base_repo": "o/other"},
        {"draft": True},
        {"head_repo": "fork/r"},
    ],
    ids=["excluded", "not_allow_listed", "draft", "fork"],
)
def test_excluded_unlisted_draft_and_fork_prs_never_fire(rule_id, data):
    assert not fires(rule_id, **data)


def test_green_checks_and_no_checks_do_not_start_a_run():
    assert not fires("pr-fixer-checks", conclusion="success")
    assert not fires("pr-fixer-checks", conclusion="no_checks")
    assert fires("pr-fixer-checks", conclusion="timeout")


# --------------------------------------------------------------------------- through the receiver


def _replay(c: Cluster, body: str, author: str, delivery: str, state: str = "open") -> list[dict]:
    """POST one signed ``issue_comment`` on PR o/r#7 through the webhook receiver (the PR
    lookup answers ``state``), cycle, and return the pr-fixer-comment runs."""
    import copy
    import hashlib
    import hmac

    from culture_rules.engine.runs import RUNS_COLLECTION
    from culture_rules.server.hooks import github as gh_hook
    from tests.rules.test_pr_fixer_single import HOOK_ACTOR, HOOK_KEY, REPO

    if c.base.get("actors", "github-app") is None:
        c.base.put("actors", copy.deepcopy(HOOK_ACTOR))
    payload = json.dumps(
        {
            "action": "created",
            "issue": {"number": 7, "title": "T", "html_url": "u", "pull_request": {}},
            "comment": {"body": body, "user": {"login": author}},
            "repository": {"full_name": REPO},
            "sender": {"login": author},
        }
    ).encode()
    headers = {
        "x-github-event": "issue_comment",
        "x-github-delivery": delivery,
        "x-hub-signature-256": "sha256="
        + hmac.new(HOOK_KEY.encode(), payload, hashlib.sha256).hexdigest(),
        "x-github-hook-installation-target-id": "111",
    }

    def pr(repo, number):
        return {
            "number": number,
            "state": state,
            "draft": False,
            "head": {"sha": "a" * 40, "ref": "fix", "repo": {"full_name": REPO}},
            "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": REPO}},
            "user": {"login": "someone"},
        }

    status, _ = gh_hook.handle(
        c.base, body=payload, headers=headers, query={}, secrets=lambda ref: HOOK_KEY, pull=pr
    )
    assert status == 202
    c.cycle()
    return c.base.find(RUNS_COLLECTION, {"rule_id": "pr-fixer-comment"})


@pytest.mark.parametrize(
    "name, author",
    [("qodo_billing", "qodo-code-review[bot]"), ("status_note", TRUSTED), ("closing", TRUSTED)],
)
def test_the_three_real_comments_replayed_through_the_receiver_start_nothing(name, author):
    c = cluster()
    assert _replay(c, REAL_BODIES[name], author, f"d-{name}") == []


def test_a_fix_comment_through_the_receiver_starts_a_run_only_while_the_pr_is_open():
    c = cluster()
    assert _replay(c, "/fix the lint please", TRUSTED, "d-closed", state="closed") == []
    (run,) = _replay(c, "/fix the lint please", TRUSTED, "d-open")
    assert run["trigger"]["data"]["command"] == "/fix"
    assert run["trigger"]["data"]["state"] == "open"
    c2 = cluster()
    (run,) = _replay(c2, "@rules-culture-dev please look", TRUSTED, "d-mention")
    assert run["trigger"]["data"]["mention"] == "@rules-culture-dev"


# --------------------------------------------------------------------------- d34: stop


def stop_fires(c: Cluster | None = None, **data) -> bool:
    c = c or cluster()
    facts = pr_facts(**{"comment": "/stop", "command": "/stop", "state": "open", **data})
    c.publish(envelope(1, type="github.comment.created", data=facts))
    reports = c.cycle()
    assert all(not r.errors for r in reports.values()), reports
    return c.run("pr-fixer-stop", "evt_1") is not None


def test_a_trusted_stop_or_the_apps_mention_with_stop_fires_the_stop_rule():
    assert stop_fires()
    assert stop_fires(
        command=None, mention="@rules-culture-dev", mention_command="@rules-culture-dev stop"
    )
    assert stop_fires(state="closed")  # a stop never needs the PR's state
    assert stop_fires(pr_enriched=False)  # nor its facts: a failed lookup still stops


@pytest.mark.parametrize(
    "data",
    [
        {"author": "mallory"},
        {"self_authored": True},
        {"command": "/fix"},
        {
            "command": None,
            "mention": "@rules-culture-dev",
            "mention_command": "@rules-culture-dev fix",
        },
        {"repository": "o/excluded"},
        {"repository": "o/other"},
    ],
    ids=["untrusted", "the_app", "fix", "mention_fix", "excluded", "not_allow_listed"],
)
def test_the_stop_rule_ignores_everything_else(data):
    assert not stop_fires(**data)


@pytest.mark.parametrize("rule_id", INTENT_RULES)
def test_a_comment_asking_to_stop_never_starts_a_fix(rule_id):
    assert not fires(rule_id, command="/stop", drop=("mention",))
    assert not fires(
        rule_id,
        mention="@rules-culture-dev",
        mention_command="@rules-culture-dev stop",
        drop=("command",),
    )
    assert fires(
        rule_id,
        mention="@rules-culture-dev",
        mention_command="@rules-culture-dev fix",
        drop=("command",),
    )


# --------------------------------------------------------------------------- #39 levels

REPO_UNDER_TEST = "agentculture/culture-rules-tester"


def test_fixer_levels_are_seeded_with_level_1_live_and_level_2_disabled():
    seeded = {i["name"]: i["value"] for i in manifest()}
    assert seeded["fixer_unavailable"] == "next"
    mem = MemoryStore()
    seed(mem, {k: seeded[k] for k in ("fixer_levels", "fixer_unavailable")})
    ladder = load_ladder(mem, {"levels": "fixer_levels", "unavailable": "fixer_unavailable"})
    one, two = ladder.levels
    assert (one.number, one.actor, one.reviewer) == (1, "qwen-fixer", "codex-reviewer")
    assert (one.pool, one.queue, one.enabled) == ("qwen-spark2", "pr-fixer", True)
    assert (two.number, two.actor, two.reviewer) == (2, "codex-fixer", "qwen-reviewer")
    assert (two.pool, two.queue, two.enabled) == ("codex-spark", "pr-fixer-codex", False)
    assert seeded["fixer_levels"] == CHAIN_VARIABLES["fixer_levels"]


def test_every_queue_step_reads_the_levels():
    wfs = workflow_docs()
    configs = [
        s["config"]
        for wid in ("queue-add", "queue-add-capacity", "queue-progress", "queue-stop")
        for s in wfs[wid]["steps"]
    ]
    queue_steps = [c for c in configs if str(c.get("builtin", "")).startswith("queue.")]
    assert len(queue_steps) == 5  # add (twice: a capacity retry too), progress, stop, sweep
    assert all(c["levels"] == "fixer_levels" for c in queue_steps)
    assert all(c["queue"] == "pr-fixer" for c in queue_steps)  # level 1's, and the fallback
    progress = next(c for c in queue_steps if c["builtin"] == "queue.progress")
    assert progress["unavailable"] == "fixer_unavailable"


@pytest.mark.parametrize(
    ("data", "fires"),
    [
        ({"queue": "pr-fixer"}, True),  # without fixer_levels, as before
        ({"queue": "pr-fixer", "levels_var": "fixer_levels"}, True),
        ({"queue": "pr-fixer-codex", "levels_var": "fixer_levels"}, True),
        ({"queue": "pr-fixer-codex"}, False),  # another queue that has no levels
        ({"queue": "other", "levels_var": "other_levels"}, False),
    ],
)
def test_the_dispatch_rule_covers_every_level_queue(data, fires):
    dispatch = next(r for r in bundle().rules if r.id == DISPATCH_RULE)
    trigger = {"data": {**data, "repository": REPO_UNDER_TEST}}
    variables = {"fixer_repos": [REPO_UNDER_TEST], "fixer_excluded_repos": []}
    assert evaluate(dispatch.condition, {"trigger": trigger, "variables": variables}) is fires
