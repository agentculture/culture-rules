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
from culture_rules.node.actions.github import GitHubCommentPort
from culture_rules.node.checks_settle import LATE_TYPE
from tests.apps.test_gitguardian import INCIDENT_URL, SAMPLE, SHA, gg_run
from tests.events.fakes import envelope
from tests.rules.chain_world import SECRETS_LATE_RULE, SECRETS_RULE, ChainWorld, bundle
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
    # its own key, one run at a time per head SHA; the once_key makes the comment once
    assert rule.concurrency_key == (
        "pr-secrets:{trigger.data.repository}#{trigger.data.number}@{trigger.data.head_sha}"
    )
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


def test_each_head_has_its_own_key():
    c = cluster()
    publish(c, 1, settled())
    publish(c, 2, settled(head_sha="c" * 40))
    runs = c.base.find(RUNS_COLLECTION, {"rule_id": SECRETS_RULE})
    keys = {r["concurrency_key"] for r in runs}
    assert keys == {f"pr-secrets:{REPO}#7@{'a' * 40}", f"pr-secrets:{REPO}#7@{'c' * 40}"}


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


# --------------------------------------------------------------------------- Codex review (d25)

ONCE = (
    "gitguardian:{{ trigger.data.repository }}#{{ trigger.data.number }}"
    "@{{ trigger.data.head_sha }}"
)


def test_the_late_rule_mirrors_the_report_on_the_late_event():
    by = {r.id: r for r in bundle().rules}
    late, settle = by[SECRETS_LATE_RULE], by[SECRETS_RULE]
    assert late.enabled is False
    assert late.trigger.params == {"type": LATE_TYPE}
    assert late.workflow == settle.workflow
    assert late.condition == settle.condition
    assert late.concurrency_key == settle.concurrency_key
    assert late.placement == settle.placement
    assert late.action == settle.action
    assert late.on_failure == settle.on_failure


def test_the_comment_is_durable_once_per_head_not_the_attempt_budget():
    for rule in (r for r in bundle().rules if r.id in (SECRETS_RULE, SECRETS_LATE_RULE)):
        assert rule.action.params["once_key"] == ONCE
        assert rule.on_failure.params["once_key"].startswith("gitguardian-unreadable:")
        assert rule.max_attempts is None  # a green settle resets budgets: not the dedupe


def test_a_late_failure_fires_only_the_late_report():
    c = cluster()
    publish(c, 1, settled(settled_by="late", late_app=GG), kind=LATE_TYPE)
    assert fired(c, 1, SECRETS_LATE_RULE)
    assert not fired(c, 1, SECRETS_RULE)
    assert not fired(c, 1, "pr-fixer-checks")


class PostedApp:
    def __init__(self):
        self.posts = []

    def installation_token(self):
        return "token"

    def post_comment(self, repo, number, body):
        self.posts.append((repo, number, body))
        return {"comment_id": len(self.posts), "url": f"https://x/{len(self.posts)}"}


def once_world(tmp_path):
    app = PostedApp()

    class Port(GitHubCommentPort):
        def _app(self, actor_id, conn, allowed):
            return app

    w = ChainWorld(tmp_path, comment_port=_LazyPort(Port))
    w.comment.bind(w.c.base)
    w.checks.runs = [gg_run()]
    return w, app


class _LazyPort:
    """The real github.comment port, built on the world's store once it exists."""

    supports_idempotency_key = False

    def __init__(self, factory):
        self.factory = factory
        self.port = None

    def bind(self, base):
        self.port = self.factory(base)

    def invoke(self, *args, **kwargs):
        return self.port.invoke(*args, **kwargs)


def _findings(app):
    return [p for p in app.posts if p[2].startswith("**GitGuardian found")]


def test_failure_then_green_then_failure_on_one_head_comments_once(tmp_path):
    w, app = once_world(tmp_path)
    head = {"head_sha": SHA}
    for n, over in enumerate(
        [
            {"failed_apps": [GG]},
            {"failed_apps": [], "conclusion": "success"},  # resets every budget
            {"failed_apps": [GG]},
        ],
        start=1,
    ):
        w.c.publish(envelope(n, type=SETTLED, data=settled(**head, **over)))
        w.run_chain(3)
    assert len(w.runs(SECRETS_RULE)) == 2  # both failures ran the report...
    assert len(_findings(app)) == 1  # ...and the head was commented once


def test_a_late_failure_after_a_timed_out_settle_is_reported_once(tmp_path):
    w, app = once_world(tmp_path)
    w.c.publish(
        envelope(1, type=SETTLED, data=settled(head_sha=SHA, failed_apps=[], conclusion="timeout"))
    )
    w.run_chain(1)
    late = settled(head_sha=SHA, settled_by="late", late_app=GG)
    w.c.publish(envelope(2, type=LATE_TYPE, data=late))
    w.c.publish(envelope(3, type=SETTLED, data=settled(head_sha=SHA)))  # a re-armed settle
    w.run_chain(3)
    (run,) = w.runs(SECRETS_LATE_RULE)
    assert run["status"] == "succeeded"
    assert len(_findings(app)) == 1


def test_a_fix_started_before_gitguardian_failed_is_held_before_its_agent(tmp_path):
    # the settle fired while GitGuardian still ran; it failed during the quiet period
    w = ChainWorld(tmp_path)
    w.checks.runs = [gg_run(conclusion=None, status="in_progress")]
    w.c.publish(
        envelope(
            1,
            type=SETTLED,
            data=settled(
                head_sha=w.repo.start,
                base_sha=w.repo.base,
                failed_apps=["github-actions"],
                conclusion="timeout",
            ),
        )
    )
    w.cycle(1)
    (fix,) = w.runs("pr-fixer-checks")
    w.checks.runs = [gg_run()]
    w.run_chain(5)
    (fix,) = w.runs("pr-fixer-checks")
    assert fix["status"] == "failed"
    assert fix["error"]["step"] == "secrets"
    assert w.qwen.inputs == []
