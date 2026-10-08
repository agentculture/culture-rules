"""d25: a failed GitGuardian check is never auto-fixed; the fixer comments the findings.

``pr-fixer-secrets`` fires once per PR head SHA whose checks settle with GitGuardian's suite
failed (``"gitguardian" in failed_apps``) and runs ``report-secrets``, which posts one
comment listing each finding without a secret value. ``pr-fixer-checks`` stays off such a
head, and the ``gitguardian.hold`` step of ``pr-fix`` stops every other fix run (a ``/fix``
comment, a review) before its agent: a human revokes and rotates the secret first.
"""

from __future__ import annotations

import json

import pytest

from culture_rules.actors.trusted import TRUSTED_WORKFLOWS, workflow_digest
from culture_rules.engine.runs import RUNS_COLLECTION
from tests.apps.test_gitguardian import INCIDENT_URL, SAMPLE, SHA, gg_run
from tests.events.fakes import envelope
from tests.rules.chain_world import SECRETS_RULE, ChainWorld, bundle
from tests.rules.test_pr_fixer_bundle import cluster
from tests.rules.test_pr_fixer_single import REPO, TRUSTED, pr_facts

SETTLED = "github.pr.checks_settled"
GG = "gitguardian"


def settled(**over) -> dict:
    return pr_facts(
        **{
            "conclusion": "failure",
            "settled_by": "all_completed",
            "state": "open",
            "failed_apps": [GG],
            **over,
        }
    )


def fired(c, n: int, rule_id: str) -> bool:
    return c.run(rule_id, f"evt_{n}") is not None


def publish(c, n: int, data: dict, *, kind: str = SETTLED) -> None:
    c.publish(envelope(n, type=kind, data=data))
    reports = c.cycle()
    assert all(not r.errors for r in reports.values()), reports


# --------------------------------------------------------------------------- the data


def test_the_rule_and_its_workflow():
    b = bundle()
    rule = next(r for r in b.rules if r.id == SECRETS_RULE)
    assert rule.enabled is False
    assert rule.trigger.params == {"type": SETTLED}
    assert rule.workflow.id == "report-secrets"
    assert rule.workflow.inputs == {
        "repo": "trigger.data.repository",
        "head_sha": "trigger.data.head_sha",
    }
    assert rule.placement.machine == "spark2"
    # its own key, one attempt per head SHA: the same head is commented once
    assert rule.concurrency_key == (
        "pr-secrets:{trigger.data.repository}#{trigger.data.number}@{trigger.data.head_sha}"
    )
    assert rule.max_attempts == 1
    assert rule.action.kind == "github.comment"
    assert rule.action.params["body"].startswith("{{ workflow.outputs.comment }}")
    assert rule.on_failure.kind == "github.comment"
    assert "{{ run.error.code }}" in rule.on_failure.params["body"]
    assert "rewriting history does not un-leak it" in rule.on_failure.params["body"]
    (wf,) = [w for w in b.workflows if w.id == "report-secrets"]
    (step,) = wf.steps
    assert step.config == {"builtin": "gitguardian.findings", "actor": "github-app"}
    assert {o.name for o in wf.outputs} >= {"comment", "findings", "state", "total"}


def test_report_secrets_holds_no_role_and_cannot_push_or_review():
    wf = next(w for w in bundle().workflows if w.id == "report-secrets").to_dict()
    digest = workflow_digest(wf)
    assert all(digest not in digests for digests in TRUSTED_WORKFLOWS.values())
    text = json.dumps(wf)
    for absent in ("github.push", '"builtin": "review"', "github.review_reply", '"kind": "ai"'):
        assert absent not in text


# --------------------------------------------------------------------------- firing


def test_a_gitguardian_failure_fires_the_report_and_never_the_fixer():
    c = cluster()
    publish(c, 1, settled(failed_apps=[GG, "github-actions"]))
    assert fired(c, 1, SECRETS_RULE)
    assert not fired(c, 1, "pr-fixer-checks")


def test_other_failures_start_the_fixer_and_no_report():
    c = cluster()
    publish(c, 1, settled(failed_apps=["github-actions"]))
    assert fired(c, 1, "pr-fixer-checks")
    assert not fired(c, 1, SECRETS_RULE)


def test_an_event_without_failed_apps_is_read_as_no_gitguardian_failure():
    # a settle emitted before d25 carries no failed_apps: the fixer starts as before
    c = cluster()
    facts = settled()
    facts.pop("failed_apps")
    publish(c, 1, facts)
    assert fired(c, 1, "pr-fixer-checks")
    assert not fired(c, 1, SECRETS_RULE)


@pytest.mark.parametrize(
    "over",
    [
        {"state": "closed"},
        {"repository": "o/other", "head_repo": "o/other", "base_repo": "o/other"},
        {"repository": "o/excluded", "head_repo": "o/excluded", "base_repo": "o/excluded"},
        {"head_repo": "fork/r"},
    ],
    ids=["closed", "not_allow_listed", "excluded", "fork"],
)
def test_the_report_keeps_to_the_fixers_repos_and_open_prs(over):
    c = cluster()
    publish(c, 1, settled(**over))
    assert not fired(c, 1, SECRETS_RULE)


def test_a_draft_pr_is_reported_too():
    c = cluster()
    publish(c, 1, settled(draft=True))
    assert fired(c, 1, SECRETS_RULE)


def test_the_same_head_is_reported_once_and_a_new_head_again():
    c = cluster()
    publish(c, 1, settled())
    publish(c, 2, settled())  # a re-armed settle of the same head
    publish(c, 3, settled(head_sha="c" * 40))
    runs = c.base.find(RUNS_COLLECTION, {"rule_id": SECRETS_RULE})
    assert sorted(r["trigger"]["id"] for r in runs) == ["evt_1", "evt_3"]


# --------------------------------------------------------------------------- end to end


def test_end_to_end_the_findings_comment_and_no_fix(tmp_path):
    w = ChainWorld(tmp_path)
    w.checks.runs = [gg_run()]
    facts = settled(head_sha=SHA, failed_apps=[GG, "github-actions"])
    w.c.publish(envelope(1, type=SETTLED, data=facts))
    w.run_chain(5)
    (run,) = w.runs(SECRETS_RULE)
    assert run["status"] == "succeeded"
    assert w.runs("pr-fixer-checks") == []
    assert w.qwen.inputs == []
    (body,) = w.comments()
    assert body.startswith("**GitGuardian found 1 hardcoded secret on `0ef24e4`.**")
    row = f"| `Generic Password` | `esphome/x.yaml:8` | `0ef24e4` | [12345678]({INCIDENT_URL}) |"
    assert row in body
    assert "rewriting history does not un-leak it" in body
    assert f"Run: https://rules.culture.dev/api/runs/{run['id']}" in body
    assert w.checks.calls == [(REPO, SHA)]
    assert "View secret" not in body
    assert "Guidelines" not in body  # nothing of the check text beyond the table


def test_end_to_end_a_fix_comment_is_held_while_gitguardian_fails(tmp_path):
    w = ChainWorld(tmp_path)
    w.checks.runs = [gg_run(text=SAMPLE)]
    facts = pr_facts(
        comment="/fix",
        command="/fix",
        pr_enriched=True,
        state="open",
        author=TRUSTED,
        head_sha=w.repo.start,
        base_sha=w.repo.base,
    )
    w.c.publish(envelope(1, type="github.comment.created", data=facts))
    w.run_chain(5)
    (run,) = w.runs("pr-fixer-comment")
    assert run["status"] == "failed"
    assert run["error"]["step"] == "secrets"
    assert "secrets_found" in run["error"]["message"]
    assert w.qwen.inputs == []  # the agent never ran
    assert w.app.calls == []  # nor did the threads lookup
    (body,) = w.comments()
    assert body.startswith("PR fixer handed back (actor_failed): secrets_found: ")
    assert "secrets_found: GitGuardian reports 1 hardcoded secret" in body


def test_end_to_end_the_fix_runs_once_gitguardian_passes(tmp_path):
    w = ChainWorld(tmp_path)
    w.checks.runs = [gg_run(conclusion="success", text="")]
    w.fire(failed_apps=["github-actions"])
    assert w.runs(SECRETS_RULE) == []
    (fix,) = w.runs("pr-fixer-checks")
    assert fix["status"] == "succeeded"
    assert w.qwen.inputs  # the agent ran
