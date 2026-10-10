"""d19: deterministic, config-only descriptions of rules and workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from culture_rules.model.describe import (
    condition_text,
    describe_rule,
    describe_workflow,
    render,
    step_text,
    trigger_text,
)
from culture_rules.model.rule import Rule
from culture_rules.model.workflow import Workflow

BUNDLE = Path(__file__).resolve().parents[2] / "docs" / "rules" / "pr-fixer"


def _load(rel: str) -> dict:
    return json.loads((BUNDLE / rel).read_text())


PR_FIX_WORKFLOW = [
    "1 quiet — wait 300 s; stop if the PR head moves (head_unchanged, as github-app)",
    "2 secrets — gitguardian.hold as github-app: stop while GitGuardian fails on the head",
    "3 threads — github.threads as github-app: unresolved threads by trusted authors",
    "4 sonar — sonar.gate_issues: the issues behind the PR's failing SonarCloud gate",
    "5 fix — one try, until verdict exists:",
    "  5.1 agent — qwen-fixer (agent, must commit)",
    "  5.2 gate — test gate on spark2",
]

REPORT_SECRETS_WORKFLOW = [
    "1 findings — gitguardian.findings as github-app: the head's GitGuardian findings, no secret"
    " values",
]

REVIEW_COMMIT_WORKFLOW = [
    "1 review — codex-reviewer (agent, read-only), when gate_verdict ∈ {pass, no_gate}"
    " and diff_truncated = false",
    "2 verdict — review verdict, recorded for its commit (github.push checks it)",
]

PUBLISH_FIX_WORKFLOW = [
    "1 push — github.push as github-app on spark2 (only on a passing gate and an approving"
    " review of exactly that commit)",
    "2 threads — github.threads as github-app: unresolved threads by trusted authors",
    "3 pick — github.threads_addressed",
    "4 replies — for each item (≤200): github.review_reply as github-app and resolve",
]

CHAIN_END = "(as its chain's status comment) (only where its chain ends)"
"""d26: every chain-end comment of the fixer writes the chain's status comment."""

PR_FIXER_CHECKS = [
    "When github.pr.checks_settled",
    "If head_repo = base_repo",
    "and draft = false",
    "and state = open",
    "and repository ∈ vars.fixer_repos",
    "and not (repository ∈ vars.fixer_excluded_repos)",
    "and conclusion ≠ success",
    "and conclusion ≠ no_checks",
    "and not (gitguardian ∈ failed_apps)",
    "Run workflow queue-add (1 step)",
    "On spark2",
    "Then noop",
    f"On failure github.comment as github-app {CHAIN_END}",
    "Key pr-fixer:{repository}#{number}, outside the attempt budget",
    "Disabled",
]
"""#35 d29: a red settle puts the PR in the fixer queue; pr-fixer-dispatch runs pr-fix."""

PR_FIXER_SECRETS = [
    "When github.pr.checks_settled",
    "If head_repo = base_repo",
    "and state = open",
    "and repository ∈ vars.fixer_repos",
    "and not (repository ∈ vars.fixer_excluded_repos)",
    "and gitguardian ∈ failed_apps",
    "Run workflow report-secrets (1 step)",
    "On spark2",
    "Then github.comment as github-app (once per once_key)",
    "On failure github.comment as github-app (once per once_key)",
    "Key pr-secrets:{repository}#{number}@{head_sha}",
    "Disabled",
]

PR_FIXER_PUBLISH = [
    "When rules.run.succeeded",
    "If workflow_id = review-commit",
    "and review = approve",
    "and verdict = pass",
    "and repository ∈ vars.fixer_repos",
    "and not (repository ∈ vars.fixer_excluded_repos)",
    "Run workflow publish-fix (4 steps)",
    "On spark2",
    f"Then github.comment as github-app {CHAIN_END}",
    f"On failure github.comment as github-app {CHAIN_END}",
    "Key pr-fixer:{repository}#{number}, outside the attempt budget",
    "Disabled",
]


@pytest.mark.parametrize(
    "name, golden",
    [
        ("pr-fix", PR_FIX_WORKFLOW),
        ("review-commit", REVIEW_COMMIT_WORKFLOW),
        ("publish-fix", PUBLISH_FIX_WORKFLOW),
        ("report-secrets", REPORT_SECRETS_WORKFLOW),
    ],
)
def test_golden_pr_fixer_workflows(name, golden):
    assert render(describe_workflow(_load(f"workflows/{name}.json"))) == golden


def test_golden_pr_fixer_rules():
    publish = _load("workflows/publish-fix.json")
    queue_add = Workflow.from_dict(_load("workflows/queue-add.json"))
    assert render(describe_rule(_load("rules/pr-fixer-checks.json"), queue_add)) == (
        PR_FIXER_CHECKS
    )
    assert render(describe_rule(_load("rules/pr-fixer-publish.json"), publish)) == (
        PR_FIXER_PUBLISH
    )
    secrets = _load("workflows/report-secrets.json")
    assert render(describe_rule(_load("rules/pr-fixer-secrets.json"), secrets)) == (
        PR_FIXER_SECRETS
    )
    late = render(describe_rule(_load("rules/pr-fixer-secrets-late.json"), secrets))
    assert late == ["When github.pr.checks_failed_late", *PR_FIXER_SECRETS[1:]]


@pytest.mark.parametrize("name", sorted(p.name for p in (BUNDLE / "rules").glob("*.json")))
def test_every_shipped_rule_describes_and_names_its_event(name):
    doc = _load(f"rules/{name}")
    lines = render(describe_rule(doc))
    assert lines[0] == f"When {trigger_text(doc['trigger'])}"
    assert f"Run workflow {doc['workflow']['id']}" in lines


def test_models_and_dicts_describe_the_same():
    wf = _load("workflows/pr-fix.json")
    rule = _load("rules/pr-fixer-checks.json")
    assert describe_workflow(Workflow.from_dict(wf)) == describe_workflow(wf)
    assert describe_rule(Rule.from_dict(rule), Workflow.from_dict(wf)) == describe_rule(rule, wf)


def test_deterministic_and_pure():
    wf = _load("workflows/pr-fix.json")
    before = json.dumps(wf, sort_keys=True)
    first = describe_workflow(wf)
    second = describe_workflow(wf)
    assert first == second
    assert json.dumps(wf, sort_keys=True) == before


def test_entries_shape():
    entries = describe_workflow(_load("workflows/pr-fix.json"))
    assert entries[4] == {
        "label": "5",
        "text": "one try, until verdict exists:",
        "depth": 0,
        "step": "fix",
    }
    assert entries[5]["depth"] == 1
    assert entries[5]["label"] == "5.1"
    assert all("step" not in e for e in describe_rule(_load("rules/pr-fixer-checks.json")))


def test_prose_fields_are_never_used():
    wf = _load("workflows/pr-fix.json")
    wf["name"] = wf["description"] = "SECRET PROSE"
    for s in wf["steps"]:
        s["name"] = s["description"] = "SECRET PROSE"
    assert "SECRET PROSE" not in "\n".join(render(describe_workflow(wf)))


# --------------------------------------------------------------------------- step kinds


def _step(kind: str, **kw) -> dict:
    return {"id": "s", "kind": kind, **kw}


@pytest.mark.parametrize(
    "step,text",
    [
        (_step("wait", config={"seconds": 2.0}), "wait 2 s"),
        (_step("wait", config={"seconds": 1.5}), "wait 1.5 s"),
        (
            _step("wait", config={"seconds": 9, "guard": {"value": "other"}}),
            "wait 9 s; guard (other)",
        ),
        (_step("code"), "code"),
        (_step("code", config={"command": "make test"}), "code `make test`"),
        (_step("code", placement={"machine": "thor"}), "code on thor"),
        (_step("code", config={"builtin": "gate"}), "test gate"),
        (_step("code", config={"builtin": "gate", "actor": "x"}), "test gate as x"),
        (_step("code", config={"builtin": "github.threads_addressed"}), "github.threads_addressed"),
        (
            _step("code", config={"builtin": "github.threads"}, placement={"machine": "orin"}),
            "github.threads on orin: unresolved threads by trusted authors",
        ),
        (_step("code", config={"builtin": "mystery"}), "builtin mystery"),
        (
            _step("code", config={"builtin": "review"}),
            "review verdict, recorded for its commit (github.push checks it)",
        ),
        (
            _step("ai", placement={"actor": "rev"}, config={"sandbox": "read-only"}),
            "rev (agent, read-only)",
        ),
        (
            _step(
                "ai",
                placement={"actor": "rev"},
                config={
                    "when": {
                        "op": "compare",
                        "cmp": "==",
                        "left": {"field": "ok"},
                        "right": {"literal": True},
                    }
                },
            ),
            "rev (agent), when ok = true",
        ),
        (
            _step(
                "code",
                config={
                    "builtin": "action",
                    "action": {"kind": "github.push", "params": {"actor": "app"}},
                },
            ),
            "github.push as app (only on an approving review of exactly that commit)",
        ),
        (_step("code", config={"builtin": "action"}), "?"),
        (
            _step(
                "code",
                config={
                    "builtin": "action",
                    "action": {"kind": "message", "params": {"channel": "#ops", "text": "hi"}},
                },
            ),
            "message to #ops",
        ),
        (_step("ai"), "agent"),
        (_step("ai", placement={"actor": "qwen"}), "qwen (agent)"),
        (_step("ai", placement={"requirement": ["gpu"]}), "agent on a machine with gpu"),
        (_step("actor_task", placement={"actor": "ori"}), "task for ori"),
        (_step("actor_task", config={"actor": "ori"}), "task for ori"),
        (_step("actor_task"), "task"),
        (_step("logic"), "logic"),
        (_step("teleport", placement={"machine": "m"}), "teleport on m"),
        (_step("for_each", max_iterations=5), "for each item (≤5)"),
        (_step("for_each", max_iterations=5, config={"items": "prs"}), "for each of prs (≤5)"),
        (_step("retry_until", max_iterations=2), "retry up to 2×"),
        (_step("logic", enabled=False), "logic (disabled)"),
        (_step("logic", retry={"max_attempts": 4}), "logic (≤4 attempts)"),
        ({}, "step"),
    ],
)
def test_step_kinds(step, text):
    assert step_text(step) == text


def test_loop_with_several_body_steps_nests_and_one_step_inlines():
    wf = {
        "steps": [
            _step("for_each", id="each", max_iterations=3, body=[_step("logic", id="only")]),
            _step(
                "retry_until",
                id="again",
                max_iterations=2,
                body=[_step("logic", id="a"), _step("ai", id="b")],
            ),
        ],
        "enabled": False,
    }
    assert render(describe_workflow(wf)) == [
        "1 each — for each item (≤3): logic",
        "2 again — retry up to 2×:",
        "  2.1 a — logic",
        "  2.2 b — agent",
        "Disabled",
    ]


@pytest.mark.parametrize(
    "action,text",
    [
        ({"kind": "noop"}, "noop"),
        ({"kind": "message", "params": {"channel": "trigger.data.ch"}}, "message"),
        (
            {"kind": "discord.message", "params": {"actor": "bot", "channel": "123"}},
            "discord.message as bot to 123",
        ),
        (
            {"kind": "jira.comment", "params": {"actor": "j", "issue": "CR-1"}},
            "jira.comment as j on CR-1",
        ),
        (
            {
                "kind": "http.call",
                "params": {"actor": "h", "method": "POST", "url": "http://localhost/x"},
            },
            "http.call as h POST http://localhost/x",
        ),
        (
            {"kind": "machine.command", "params": {"actor": "thor", "command": "reboot"}},
            "machine.command as thor `reboot`",
        ),
        (
            {"kind": "github.push", "params": {"actor": "{{ x }}"}},
            "github.push (only on an approving review of exactly that commit)",
        ),
        ({"kind": "future.kind", "params": {"actor": "a"}}, "future.kind as a"),
    ],
)
def test_action_kinds(action, text):
    rule = {"trigger": {"kind": "manual"}, "action": action}
    assert render(describe_rule(rule))[-1] == f"Then {text}"


# --------------------------------------------------------------------------- conditions


def _f(path):
    return {"field": path}


def _lit(v):
    return {"literal": v}


@pytest.mark.parametrize(
    "cmp,sym", [("==", "="), ("!=", "≠"), ("<", "<"), ("<=", "≤"), (">", ">"), (">=", "≥")]
)
def test_compare_operators(cmp, sym):
    node = {"op": "compare", "cmp": cmp, "left": _f("data.n"), "right": _lit(3)}
    assert condition_text(node) == f"n {sym} 3"


@pytest.mark.parametrize(
    "node,text",
    [
        ({"op": "exists", "arg": _f("data.a")}, "a exists"),
        ({"op": "matches", "value": _f("title"), "pattern": "fix: .*"}, "title matches /fix: .*/"),
        ({"op": "in", "value": _f("x"), "items": _lit(["a", "b"])}, "x ∈ {a, b}"),
        (
            {"op": "not", "arg": {"op": "in", "value": _f("x"), "items": {"var": "v"}}},
            "not (x ∈ vars.v)",
        ),
        ({"op": "not", "arg": {"op": "exists", "arg": _f("a")}}, "a missing"),
        (
            {"op": "not", "arg": {"op": "matches", "value": _f("t"), "pattern": "x"}},
            "not (t matches /x/)",
        ),
        (
            {
                "op": "not",
                "arg": {"op": "compare", "cmp": "==", "left": _f("a"), "right": _lit("")},
            },
            'not (a = "")',
        ),
        (
            {
                "op": "not",
                "arg": {"op": "compare", "cmp": "!=", "left": _f("a"), "right": _lit(1)},
            },
            "not (a ≠ 1)",
        ),
        (
            {"op": "not", "arg": {"op": "not", "arg": {"op": "exists", "arg": _f("a")}}},
            "not (a missing)",
        ),
        (
            {"op": "not", "arg": {"op": "compare", "cmp": "<", "left": _f("a"), "right": _lit(1)}},
            "not (a < 1)",
        ),
        (
            {
                "op": "or",
                "args": [
                    {"op": "exists", "arg": _f("a")},
                    {
                        "op": "and",
                        "args": [
                            {"op": "exists", "arg": _f("b")},
                            {"op": "exists", "arg": {"var": "c"}},
                        ],
                    },
                ],
            },
            "a exists or (b exists and vars.c exists)",
        ),
        ({"op": "compare", "cmp": "==", "left": _f("a"), "right": _lit(None)}, "a = null"),
        ({"op": "compare", "cmp": "==", "left": _f("a"), "right": _lit(True)}, "a = true"),
        ({"op": "compare", "cmp": "==", "left": _f("a"), "right": _lit({"k": 1})}, 'a = {"k": 1}'),
        ({"op": "teleport"}, "teleport"),
        ({"op": "compare", "cmp": "==", "left": "bad", "right": {}}, "? = ?"),
        ("not a node", "?"),
        ({}, "?"),
    ],
)
def test_condition_operators(node, text):
    assert condition_text(node) == text


def test_rule_or_condition_reads_as_or_lines():
    rule = {
        "trigger": {"kind": "manual"},
        "condition": {
            "op": "or",
            "args": [{"op": "exists", "arg": _f("a")}, {"op": "exists", "arg": _f("b")}],
        },
        "action": {"kind": "noop"},
    }
    assert render(describe_rule(rule))[:3] == ["When started by hand", "If a exists", "or b exists"]


def test_rule_single_condition():
    rule = {"trigger": {"kind": "manual"}, "condition": {"op": "exists", "arg": _f("a")}}
    assert render(describe_rule(rule)) == ["When started by hand", "If a exists"]


# --------------------------------------------------------------------------- rules


@pytest.mark.parametrize(
    "trigger,text",
    [
        ({"kind": "event", "params": {"type": "pr.opened"}}, "pr.opened"),
        ({"kind": "event"}, "an event"),
        ({"kind": "schedule", "params": {"cron": "0 9 * * 1"}}, "cron 0 9 * * 1 (UTC)"),
        (
            {"kind": "schedule", "params": {"cron": "0 9 * * *", "tz": "Asia/Jerusalem"}},
            "cron 0 9 * * * (Asia/Jerusalem)",
        ),
        (
            {
                "kind": "probe",
                "params": {
                    "actor": "thor",
                    "command": "df",
                    "schedule": "*/5 * * * *",
                    "mode": "change",
                },
            },
            "probe `df` as thor on cron */5 * * * *, on a change",
        ),
        (
            {
                "kind": "probe",
                "params": {"command": "df", "schedule": "* * * * *", "mode": "condition"},
            },
            "probe `df` on cron * * * * *, when the condition holds",
        ),
        ({"kind": "manual"}, "started by hand"),
        ({"kind": "webhook"}, "webhook"),
        (None, "?"),
    ],
)
def test_trigger_kinds(trigger, text):
    assert trigger_text(trigger) == text


def test_rule_relationships_placement_group_and_missing_workflow():
    rule = {
        "trigger": {"kind": "manual"},
        "must_after": ["a"],
        "may_after": ["b"],
        "supersedes": ["c", "d"],
        "workflow": {"id": "w", "version": 2},
        "placement": {"actor": "qwen"},
        "action": {"kind": "noop"},
        "exclusive_group": "g",
        "priority": 5,
        "concurrency_key": "k",
    }
    assert render(describe_rule(rule, {})) == [
        "When started by hand",
        "After a (must), b (may)",
        "Supersedes c, d",
        "Run workflow w v2 (not found)",
        "On qwen's machine",
        "Then noop",
        "Key k",
        "Group g, priority 5",
    ]


def test_rule_workflow_one_step_disabled_and_requirement_placement():
    rule = {
        "trigger": {"kind": "manual"},
        "workflow": {"id": "w"},
        "placement": {"requirement": ["gpu", "cuda"]},
    }
    wf = {"steps": [_step("logic")], "enabled": False}
    assert render(describe_rule(rule, wf))[1:] == [
        "Run workflow w (1 step, disabled)",
        "On a machine with gpu, cuda",
    ]


def test_garbage_never_raises():
    for junk in (None, 3, "x", [], {"steps": "nope"}, {"steps": [None, 4]}):
        describe_workflow(junk)
        describe_rule(junk)
    assert render(describe_rule(None)) == ["When ?"]


def test_a_single_loop_child_keeps_its_own_body():
    inner = _step(
        "for_each",
        id="inner",
        max_iterations=4,
        body=[_step("code", id="work", config={"command": "make"})],
    )
    wf = {"steps": [_step("for_each", id="outer", max_iterations=2, body=[inner])]}
    assert render(describe_workflow(wf)) == [
        "1 outer — for each item (≤2):",
        "  1.1 inner — for each item (≤4): code `make`",
    ]


@pytest.mark.parametrize(
    "stored,line",
    [
        ({"version": 2, "steps": [_step("logic")]}, "Run workflow w v1 (version unavailable)"),
        ({"steps": [_step("logic")]}, "Run workflow w v1 (1 step)"),  # no version field means 1
        ({"version": 1, "steps": [_step("logic")]}, "Run workflow w v1 (1 step)"),
    ],
)
def test_a_pinned_version_is_described_only_when_it_is_the_stored_one(stored, line):
    rule = {"trigger": {"kind": "manual"}, "workflow": {"id": "w", "version": 1}}
    assert render(describe_rule(rule, stored))[1] == line


def test_negation_is_folded_only_where_the_evaluator_agrees():
    """``not (a = b)`` is not ``a ≠ b``: a missing operand makes both comparisons false, so
    only the explicit negation is true. ``not exists`` reads ``missing``: the same truth."""
    from culture_rules.model.condition import evaluate

    eq = {"op": "compare", "cmp": "==", "left": _f("a"), "right": _lit(1)}
    ne = {"op": "compare", "cmp": "!=", "left": _f("a"), "right": _lit(1)}
    missing = {"trigger": {}}
    assert evaluate({"op": "not", "arg": eq}, missing) is True
    assert evaluate(ne, missing) is False  # so folding not(=) into ≠ would misdescribe it
    assert condition_text({"op": "not", "arg": eq}) == "not (a = 1)"
    absent = {"op": "not", "arg": {"op": "exists", "arg": _f("a")}}
    for ctx, want in (({"trigger": {}}, True), ({"trigger": {"a": None}}, False)):
        assert evaluate(absent, ctx) is want
    assert condition_text(absent) == "a missing"


# --------------------------------------------------------------------------- d21 run events


def test_a_rule_on_a_finished_run_reads_its_type_and_conditions():
    rule = {
        "trigger": {"kind": "event", "params": {"type": "rules.run.succeeded"}},
        "condition": {
            "op": "and",
            "args": [
                {
                    "op": "in",
                    "value": {"field": "data.rule_id"},
                    "items": {"literal": ["pr-fixer-checks", "pr-fixer-comment"]},
                },
                {
                    "op": "compare",
                    "cmp": "==",
                    "left": {"field": "data.outputs.verdict"},
                    "right": {"literal": "approve"},
                },
            ],
        },
        "action": {"kind": "noop", "params": {}},
        "concurrency_key": "pr-fixer:{trigger.data.repository}#{trigger.data.number}",
        "counts_toward_budget": False,
    }
    assert render(describe_rule(rule)) == [
        "When rules.run.succeeded",
        "If rule_id ∈ {pr-fixer-checks, pr-fixer-comment}",
        "and verdict = approve",
        "Then noop",
        "Key pr-fixer:{repository}#{number}, outside the attempt budget",
    ]


def test_a_status_comment_action_says_so():
    from culture_rules.model.describe import action_text

    action = {"kind": "github.comment", "params": {"actor": "github-app", "status": True}}
    assert action_text(action) == "github.comment as github-app (as its chain's status comment)"
