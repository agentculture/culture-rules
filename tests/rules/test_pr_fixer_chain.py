"""d21 phase 2: the shipped PR fixer as chained rules and workflows, end to end.

pr-fixer-{checks,comment,review,review-comment} -> ``pr-fix`` (quiet, threads, Sonar, agent,
gate); pr-fixer-review-commit (on pr-fix succeeded) -> ``review-commit``; pr-fixer-refix
(review = request_changes) -> ``pr-fix`` with the findings; pr-fixer-publish (review =
approve) -> ``publish-fix`` (push, pick, replies). Every guarantee of the d20 single
workflow holds across the chain: one gate-built commit, trusted workflows and actors, the
locked brief, reviewer != implementer from trusted state, immutable review records - now
per commit -, a consumed approval, and push verifying the whole chain from the store.
"""

from __future__ import annotations

import pytest

from culture_rules.actors.merge_hint import merge_paragraph
from culture_rules.actors.review import current_review, review_target, run_reviews
from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.engine.runs import ACTION_STEP, FAILURE_STEP, step_state
from culture_rules.node.fixer_status import STATUS_NOTE_HINT
from tests.rules.chain_world import KEY, ChainWorld
from tests.rules.test_pr_fixer_single import REPO, verdict_text

FINDING = {
    "path": "src/app.py",
    "line": 1,
    "severity": "high",
    "detail": "x must come from the config, not a literal",
}


def changes(commit: str) -> str:
    return verdict_text(commit, "request_changes", [FINDING])


def gate_of(run: dict) -> dict:
    """The outputs of a pr-fix run's last gate."""
    loop = step_state(run, "fix")
    return step_state(run, f"fix[{loop['iteration']}]/gate")["outputs"]


def budget(w: ChainWorld) -> dict:
    return w.c.base.get(RULE_ATTEMPT_BUDGETS, budget_id(KEY)) or {}


# --------------------------------------------------------------------------- the happy path


def test_a_red_pr_is_fixed_reviewed_and_published_by_three_chained_runs(tmp_path):
    w = ChainWorld(tmp_path)
    w.fire()
    (fix,) = w.run_of("pr-fix")
    (review,) = w.run_of("review-commit")
    (publish,) = w.run_of("publish-fix")
    for run in (fix, review, publish):
        assert run["status"] == "succeeded", (run["workflow_id"], run.get("error"))
    assert fix["rule_id"] == "pr-fixer-dispatch"  # #35: started at the head of the queue
    assert fix["trigger"]["type"] == "rules.queue.dispatch"
    assert review["rule_id"] == "pr-fixer-review-commit"
    assert publish["rule_id"] == "pr-fixer-publish"
    g = gate_of(fix)
    assert g["verdict"] == "pass"
    # the reviewer saw exactly the gate's commit and diff, with the locked brief
    (given,) = w.reviewer.inputs
    assert given["commit_sha"] == g["commit_sha"]
    assert given["diff"] == g["diff"]
    assert given["head_sha"] == w.repo.start
    verdict = step_state(review, "verdict")["outputs"]
    assert verdict["review"] == "approve"
    assert verdict["reviewed_commit"] == g["commit_sha"]
    # exactly that commit is pushed, from the gate's bundle, on spark2
    (push_call,) = w.push.calls
    assert push_call[1]["commit_sha"] == g["commit_sha"]
    assert push_call[1]["expected_head_sha"] == w.repo.start
    assert push_call[1]["source"] == g["bundle"]
    assert push_call[1]["gate_verdict"] == "pass"
    assert step_state(publish, "push")["host"] == "spark2"
    # the trusted thread is answered, naming the pushed commit
    (reply,) = w.reply.calls
    assert reply[1]["thread_id"] == "PRRT_1"
    assert g["commit_sha"] in reply[1]["body"]
    # one comment for the whole chain, from the stage that ends it
    assert step_state(fix, ACTION_STEP)["status"] == "skipped"
    assert step_state(review, ACTION_STEP)["status"] == "skipped"
    (body,) = w.comments()
    assert "pushed True" in body
    assert publish["id"] in body
    # one fix attempt counted; the review and the publish are outside the budget
    assert budget(w)["count"] == 1


def test_the_review_record_is_keyed_by_the_commit_and_consumed_by_the_push(tmp_path):
    w = ChainWorld(tmp_path)
    w.fire()
    (fix,) = w.run_of("pr-fix")
    (review,) = w.run_of("review-commit")
    g = gate_of(fix)
    target = review_target(REPO, 7, w.repo.base, w.repo.start, g["commit_sha"])
    rid, rec, state = current_review(w.c.base, target)
    assert rec["run_id"] == review["id"]
    assert rec["fix_run"] == fix["id"]
    assert rec["verdict"] == "approve"
    assert rec["base_sha"] == w.repo.base
    assert (rec["reviewer_actor"], rec["reviewer_backend"]) == ("codex-reviewer", "codex")
    assert (rec["implementer_actor"], rec["implementer_backend"]) == ("qwen-fixer", "qwen")
    assert state == "current"  # a recorder push does not consume (the real port does)


def test_runs_chain_by_verified_lineage(tmp_path):
    w = ChainWorld(tmp_path)
    w.fire()
    (fix,) = w.run_of("pr-fix")
    (review,) = w.run_of("review-commit")
    (publish,) = w.run_of("publish-fix")
    assert review["trigger"]["data"]["run_id"] == fix["id"]
    assert publish["trigger"]["data"]["run_id"] == review["id"]
    assert review["trigger"]["hops"] == 1
    assert publish["trigger"]["hops"] == 2
    assert {r["concurrency_key"] for r in (fix, review, publish)} == {KEY}


# --------------------------------------------------------------------------- re-fix


def test_requested_changes_fix_again_with_the_findings_then_publish(tmp_path):
    w = ChainWorld(tmp_path, reviews=[changes, verdict_text])
    w.fire()
    fixes = w.run_of("pr-fix")
    reviews = w.run_of("review-commit")
    # #35 d30: the re-fix goes back through the queue; the dispatch rule starts both tries
    assert [r["rule_id"] for r in fixes] == ["pr-fixer-dispatch", "pr-fixer-dispatch"]
    assert [r["trigger"]["data"]["retry"] for r in fixes] == [False, True]
    assert [r["rule_id"] for r in w.run_of("queue-add")] == ["pr-fixer-checks", "pr-fixer-refix"]
    assert len(reviews) == 2
    assert len(w.run_of("publish-fix")) == 1
    first, second = (q["instruction"] for q in w.qwen.inputs)
    assert "An independent reviewer requested changes" in second
    assert FINDING["detail"] in second
    assert "[high] src/app.py:1" in second
    assert "The original task:" in second
    assert "o/r#7" in second
    # the original task travels on (the agent also got the d36 merge paragraph and the d26
    # status-notes hint, both appended at the bridge call and never stored)
    task = fixes[1]["inputs"]["task"]
    assert task == fixes[0]["inputs"]["instruction"]
    assert first == task + merge_paragraph(fixes[0]["inputs"]["base_sha"]) + STATUS_NOTE_HINT
    assert second.endswith(STATUS_NOTE_HINT)
    (push_call,) = w.push.calls
    assert push_call[1]["commit_sha"] == gate_of(fixes[1])["commit_sha"]
    assert budget(w)["count"] == 2
    assert len(w.comments()) == 1  # #35 d35: one status comment per story


def test_three_requests_for_changes_hand_back_once_with_the_findings(tmp_path):
    w = ChainWorld(tmp_path, reviews=[changes, changes, changes])
    w.fire()
    assert len(w.run_of("pr-fix")) == 3
    reviews = w.run_of("review-commit")
    assert [r["status"] for r in reviews] == ["succeeded", "succeeded", "failed"]
    last = reviews[-1]
    assert last["error"]["code"] == "actor_failed"
    assert last["error"]["message"].startswith("changes_requested: ")
    assert w.run_of("publish-fix") == []
    assert w.push.calls == []
    (body,) = w.comments()  # one hand-back for the story (#35 d35)
    assert body.startswith("PR fixer handed back (actor_failed): changes_requested")
    assert FINDING["detail"] in body
    assert last["id"] in body
    # the failed review's record still says what the reviewer asked
    assert run_reviews(w.c.base, last["id"])[-1]["verdict"] == "request_changes"


# --------------------------------------------------------------------------- review-only


def test_review_only_mode_posts_the_verdict_and_pushes_nothing(tmp_path):
    w = ChainWorld(tmp_path, disabled=("pr-fixer-publish",))
    w.fire()
    (fix,) = w.run_of("pr-fix")
    (review,) = w.run_of("review-commit")
    assert review["status"] == "succeeded"
    assert w.run_of("publish-fix") == []
    assert w.push.calls == []
    assert w.reply.calls == []
    assert step_state(fix, ACTION_STEP)["status"] == "skipped"
    assert step_state(review, ACTION_STEP)["status"] == "succeeded"
    (body,) = w.comments()
    assert body.startswith("PR fixer review: approve for ")
    assert "review-only mode" in body
    # the chain ended: the key is free again
    assert not budget(w).get("hold")


def test_an_approval_without_a_test_gate_ends_at_the_review_and_says_so(tmp_path):
    w = ChainWorld(tmp_path)
    from tests.actors.test_gate import git

    git(w.repo.wt, "checkout", "-q", "-b", "no-gate", w.repo.base)
    w.repo.base = w.repo.commit("no gate section", {"culture.yaml": None})
    git(w.repo.wt, "checkout", "-q", "--detach", w.repo.start)
    w.repo.start = w.repo.commit("pr head", {"src/app.py": "x = 2\n"})
    w.fire()
    (fix,) = w.run_of("pr-fix")
    assert gate_of(fix)["verdict"] == "no_gate"
    (review,) = w.run_of("review-commit")
    assert review["status"] == "succeeded"
    assert review["outputs"]["review"] == "approve"
    assert w.run_of("publish-fix") == []
    assert w.push.calls == []
    (body,) = w.comments()
    assert "(gate no_gate); not pushed: nothing publishes it" in body


def test_a_stage_whose_inputs_are_missing_starts_and_hands_back(tmp_path):
    """No silent chain end: a stage run whose inputs are missing starts, fails inside and
    posts the chain's one hand-back."""
    from culture_rules.engine.actorport import InvocationResult

    class NoCommit:
        supports_idempotency_key = True

        def invoke(self, input, key, deadline, *, context):
            return InvocationResult.completed({"verdict": "pass", "review": "approve"})

    w = ChainWorld(tmp_path, verdict_port=NoCommit())
    w.fire()
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == "failed"
    assert w.push.calls == []
    (body,) = w.comments()
    assert body.startswith("PR fixer handed back (")


# --------------------------------------------------------------------------- E3: no commit


def test_an_agent_that_makes_no_commit_ends_the_attempt_and_hands_back_once(tmp_path):
    w = ChainWorld(tmp_path, turns=["none"])
    w.fire()
    (fix,) = w.run_of("pr-fix")
    assert fix["status"] == "failed"
    assert "no_changes" in fix["error"]["message"]
    gate = step_state(fix, "fix[0]/gate")
    assert gate is None or (gate["attempt"] == 0 and gate["status"] != "succeeded")  # no gate
    assert w.run_of("review-commit") == []
    assert w.reviewer.inputs == []  # no review
    assert len(w.qwen.inputs) == 1  # no second try
    (body,) = w.comments()
    assert body.startswith("PR fixer handed back (")
    assert "no_changes" in body
    assert step_state(fix, FAILURE_STEP)["status"] == "succeeded"


# --------------------------------------------------------------------------- E5: Sonar


def test_the_agent_gets_only_the_issues_behind_failing_sonar_conditions(tmp_path):
    w = ChainWorld(tmp_path)
    w.sonar.gate = {
        "projectStatus": {
            "status": "ERROR",
            "conditions": [
                {"status": "ERROR", "metricKey": "new_reliability_rating", "actualValue": "3"},
                {"status": "OK", "metricKey": "new_maintainability_rating", "actualValue": "1"},
            ],
        }
    }
    w.sonar.issues = [
        {"key": "b1", "type": "BUG", "message": "null deref", "component": "o_r:src/app.py"}
    ]
    w.fire()
    given = w.qwen.inputs[0]
    assert [i["key"] for i in given["sonar_issues"]] == ["b1"]
    assert "new_reliability_rating" in given["sonar_note"]
    assert "Never the rest of the Sonar backlog" in given["instruction"]
    assert any("types=BUG" in u for u in w.sonar.requests)
    assert not any("CODE_SMELL" in u for u in w.sonar.requests)
    assert not any("inNewCodePeriod" in u for u in w.sonar.requests)


def test_on_a_passing_gate_the_agent_gets_the_prs_new_code_issues_to_fix_only_if_named(
    tmp_path,
):
    """#35 d33: irc-lens#62's live case - gate OK, one new-code issue (S9073)."""
    w = ChainWorld(tmp_path)
    w.sonar.issues = [
        {
            "key": "s9073",
            "rule": "python:S9073",
            "type": "CODE_SMELL",
            "message": "Split this composite assertion into separate assertions.",
            "component": "o_r:tests/test_mail.py",
            "line": 113,
        }
    ]
    w.fire()
    given = w.qwen.inputs[0]
    (listed,) = given["sonar_issues"]
    assert (listed["rule"], listed["path"], listed["line"]) == (
        "python:S9073",
        "tests/test_mail.py",
        113,
    )
    assert "passes" in given["sonar_note"]
    assert "only if the trusted request names it" in given["sonar_note"]
    assert "only if the trusted request names it" in given["instruction"]
    (search,) = [u for u in w.sonar.requests if "/api/issues/search" in u]
    assert "inNewCodePeriod=true" in search
    assert "pullRequest=7" in search


def test_a_sonar_outage_never_stops_the_fix(tmp_path):
    w = ChainWorld(tmp_path)
    w.sonar.gate = None  # answers 200 with "null": malformed

    def down(method, url, headers, timeout):
        raise OSError("sonarcloud down")

    w.sonar.__class__ = type("Down", (), {"__call__": staticmethod(down)})
    w.fire()
    (fix,) = w.run_of("pr-fix")
    assert fix["status"] == "succeeded"
    assert "unavailable" in w.qwen.inputs[0]["sonar_note"]


# --------------------------------------------------------------------------- the key is held


def test_a_new_trigger_during_the_review_waits_for_the_chain_and_runs_after_it(tmp_path):
    from tests.events.fakes import envelope
    from tests.rules.test_pr_fixer_single import pr_facts

    w = ChainWorld(tmp_path)
    fired = {"n": 0}

    def comment_arrives():
        # while Codex reviews, a trusted /fix comment lands on the same PR
        if fired["n"]:
            return
        fired["n"] += 1
        facts = pr_facts(
            head_sha=w.repo.start,
            base_sha=w.repo.base,
            comment="/fix",
            command="/fix",
            pr_enriched=True,
            state="open",
        )
        w.c.publish(envelope(9, type="github.comment.created", data=facts))

    w.reviewer.on_request = comment_arrives
    w.fire()
    fixes = w.run_of("pr-fix")
    assert [r["rule_id"] for r in fixes] == ["pr-fixer-dispatch", "pr-fixer-dispatch"]
    queued = [r["rule_id"] for r in w.run_of("queue-add")]
    assert queued == ["pr-fixer-checks", "pr-fixer-comment"]
    publish = w.run_of("publish-fix")[0]
    assert publish["trigger"]["data"]["run_id"] == w.run_of("review-commit")[0]["id"]
    # the comment's fix started only after the first chain had ended (published)
    assert fixes[1]["created_at"] >= publish["finished_at"]
    reviews = w.run_of("review-commit")
    assert reviews[0]["trigger"]["data"]["run_id"] == fixes[0]["id"]


@pytest.mark.parametrize("rule_id", ["pr-fixer-checks", "pr-fixer-review-commit"])
def test_disabling_any_rule_of_the_chain_mid_chain_pushes_nothing(tmp_path, rule_id, pem):
    w = ChainWorld(tmp_path, real_push_pem=pem)
    trust_app(w)

    def disable():
        doc = w.c.base.get("rules", rule_id)
        w.c.base.put("rules", {**doc, "enabled": False})

    w.reviewer.on_request = disable  # while Codex reviews, after the fix was gated
    w.fire()
    assert w.remote_head() == w.repo.start
    # the publish still fires (its own rule is enabled); the push checks every rule of
    # the chain - the fix's and the review's too - and refuses
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == "failed"
    assert publish["error"]["message"] == "rule_disabled"
    (body,) = w.comments()
    assert body.startswith("PR fixer handed back (actor_failed): rule_disabled")


# --------------------------------------------------------------------------- the real push


@pytest.fixture(scope="module")
def pem():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def trust_app(w: ChainWorld) -> None:
    """Trust the test App actor's shape for this world (production pins the live one)."""
    import copy

    from culture_rules.actors import trusted
    from tests.rules.test_pr_fixer_single import APP_ACTOR

    digest = trusted.actor_digest(copy.deepcopy(APP_ACTOR))
    table = dict(trusted.TRUSTED_ACTOR_DIGESTS)
    table["github-app"] = table.get("github-app", frozenset()) | {digest}
    trusted.TRUSTED_ACTOR_DIGESTS = table  # restored by _untrust_app


@pytest.fixture(autouse=True)
def _untrust_app():
    from culture_rules.actors import trusted

    before = trusted.TRUSTED_ACTOR_DIGESTS
    yield
    trusted.TRUSTED_ACTOR_DIGESTS = before


def test_the_real_push_publishes_exactly_the_approved_commit_and_consumes_it(tmp_path, pem):
    w = ChainWorld(tmp_path, real_push_pem=pem)
    trust_app(w)
    w.fire()
    (fix,) = w.run_of("pr-fix")
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == "succeeded", publish.get("error")
    g = gate_of(fix)
    assert w.remote_head() == g["commit_sha"]
    target = review_target(REPO, 7, w.repo.base, w.repo.start, g["commit_sha"])
    _rid, _rec, state = current_review(w.c.base, target)
    assert state == "consumed"


def test_f1_an_authentic_completion_claiming_approval_without_a_review_record_pushes_nothing(
    tmp_path, pem
):
    """The trusted review-commit run genuinely completes with outputs saying ``approve``,
    but no review record approves the commit (here: the verdict step's port answered without
    writing one). The publish fires on the genuine event; the push finds no approval."""
    from culture_rules.engine.actorport import InvocationResult

    holder: dict = {}

    class ClaimsApproval:
        """Answers exactly what an approval of the reviewed commit looks like - and writes
        no review record."""

        supports_idempotency_key = True

        def invoke(self, input, key, deadline, *, context):
            given = holder["w"].c.base.get("runs", context.run_id)["inputs"]
            return InvocationResult.completed(
                {
                    "verdict": "pass",
                    "review": "approve",
                    "findings": [],
                    "summary": "",
                    "reviewed_commit": given["commit_sha"],
                    "commit_sha": given["commit_sha"],
                    "start_sha": given["head_sha"],
                    "base_sha": given["base_sha"],
                }
            )

    w = ChainWorld(tmp_path, real_push_pem=pem, verdict_port=ClaimsApproval())
    holder["w"] = w
    trust_app(w)
    w.fire()
    (review,) = w.run_of("review-commit")
    assert review["status"] == "succeeded"
    assert review["outputs"]["review"] == "approve"  # it claims approval, authentically
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == "failed"
    assert publish["error"]["message"] == "review_missing"
    assert w.remote_head() == w.repo.start
    (body,) = w.comments()
    assert body.startswith("PR fixer handed back (actor_failed): review_missing")


def test_f2_a_pr_head_that_moved_after_the_review_pushes_nothing(tmp_path, pem):
    w = ChainWorld(tmp_path, real_push_pem=pem)
    trust_app(w)

    def someone_pushes():
        w.github.head_override = "f" * 40  # the PR head moved on GitHub after the review

    w.reviewer.on_request = someone_pushes
    w.fire()
    (review,) = w.run_of("review-commit")
    assert review["status"] == "succeeded"
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == "failed"
    assert publish["error"]["message"] == "head_moved"
    assert w.remote_head() == w.repo.start
    (fix,) = w.run_of("pr-fix")
    target = review_target(REPO, 7, w.repo.base, w.repo.start, gate_of(fix)["commit_sha"])
    assert current_review(w.c.base, target)[2] == "current"  # never consumed


def test_f3_a_review_only_chain_pushes_nothing(tmp_path, pem):
    w = ChainWorld(tmp_path, real_push_pem=pem, disabled=("pr-fixer-publish",))
    trust_app(w)
    w.fire()
    assert w.run_of("publish-fix") == []
    assert w.remote_head() == w.repo.start
    assert not [c for c in w.github.calls if c[1].endswith("/access_tokens")]


def test_a_publish_run_started_by_hand_pushes_nothing(tmp_path, pem):
    """A direct run of the trusted publish-fix workflow with the approved commit's exact
    inputs: no verified chain behind it (chain_unverified)."""
    w = ChainWorld(tmp_path, real_push_pem=pem, disabled=("pr-fixer-publish",))
    trust_app(w)
    w.fire()
    (fix,) = w.run_of("pr-fix")
    g = gate_of(fix)
    node = w.c.nodes["spark2"]
    run = node.executor.start_workflow(
        "publish-fix",
        {
            "repo": REPO,
            "number": 7,
            "head_branch": "fix",
            "commit_sha": g["commit_sha"],
            "expected_head_sha": w.repo.start,
            "source": g["bundle"],
            "verdict": "pass",
            "addressed": [],
            "trusted_authors": ["OriNachum"],
        },
        "mallory@test",
    )
    w.cycle(6)
    doc = w.c.base.get("runs", run["id"])
    assert doc["status"] == "failed"
    assert doc["error"]["message"] == "chain_unverified"
    assert w.remote_head() == w.repo.start


# --------------------------------------------------------------------------- the chain's trust


def test_an_edited_pr_fix_workflow_is_never_reviewed_into_a_push(tmp_path):
    """An admin tweaks the stored pr-fix (here only its description): its runs still fix
    and gate, but its commit is not one a trusted chain may review or publish."""
    w = ChainWorld(tmp_path)
    wf = w.c.base.get("workflows", "pr-fix")
    w.c.base.put("workflows", {**wf, "description": "tweaked"})
    w.fire()
    (fix,) = w.run_of("pr-fix")
    assert fix["status"] == "succeeded"
    (review,) = w.run_of("review-commit")
    assert review["status"] == "failed"
    assert review["error"]["message"].startswith("workflow_not_trusted")
    assert w.run_of("publish-fix") == []
    assert w.push.calls == []
    (body,) = w.comments()
    assert body.startswith("PR fixer handed back (actor_failed): workflow_not_trusted")


def test_a_reviewer_that_wrote_to_its_checkout_is_not_an_approval(tmp_path):
    w = ChainWorld(tmp_path, reviews=[{"status": "completed", "commits": [{"sha": "x"}]}])
    w.fire()
    (review,) = w.run_of("review-commit")
    assert review["status"] == "failed"
    assert review["error"]["message"].startswith("reviewer_not_read_only")
    assert w.run_of("publish-fix") == []
    assert w.push.calls == []
    assert len(w.comments()) == 1


def test_an_edited_review_commit_workflow_records_nothing_a_push_accepts(tmp_path):
    w = ChainWorld(tmp_path)
    wf = w.c.base.get("workflows", "review-commit")
    w.c.base.put("workflows", {**wf, "description": "tweaked"})
    w.fire()
    (review,) = w.run_of("review-commit")
    assert review["status"] == "failed"
    assert review["error"]["message"].startswith("workflow_not_trusted")
    assert w.push.calls == []


def test_disabling_the_initiating_trigger_rule_after_a_refix_pushes_nothing(tmp_path, pem):
    """checks -> fix A -> review A (changes) -> refix: fix B -> review B (approve) ->
    publish. The push follows the verified re-fix lineage back to fix A, so disabling the
    rule that started the chain (pr-fixer-checks) during review B stops it (Codex #2)."""
    w = ChainWorld(tmp_path, reviews=[changes, verdict_text], real_push_pem=pem)
    trust_app(w)
    calls = {"n": 0}

    def disable_on_second_review():
        calls["n"] += 1
        if calls["n"] == 2:
            doc = w.c.base.get("rules", "pr-fixer-checks")
            w.c.base.put("rules", {**doc, "enabled": False})

    w.reviewer.on_request = disable_on_second_review
    w.fire()
    assert [r["rule_id"] for r in w.run_of("pr-fix")] == ["pr-fixer-dispatch"] * 2
    assert [r["rule_id"] for r in w.run_of("queue-add")] == ["pr-fixer-checks", "pr-fixer-refix"]
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == "failed"
    assert publish["error"]["message"] == "rule_disabled"
    assert w.remote_head() == w.repo.start


def test_d36_a_refix_reads_the_merge_paragraph_once(tmp_path):
    """Codex round 2 #2 (d36): across requests for changes the agent reads the paragraph
    once per try; no try's instruction or task stores it, so it never piles up."""
    from culture_rules.actors.merge_hint import MERGE_PARAGRAPH_HEAD

    w = ChainWorld(tmp_path, reviews=[changes, changes, verdict_text])
    w.fire()
    fixes = w.run_of("pr-fix")
    assert len(fixes) == 3
    for run in fixes:
        assert MERGE_PARAGRAPH_HEAD not in run["inputs"]["instruction"], run["id"]
        assert MERGE_PARAGRAPH_HEAD not in (run["inputs"].get("task") or "")
    assert len(w.qwen.inputs) == 3
    for sent in w.qwen.inputs:
        assert sent["instruction"].count(MERGE_PARAGRAPH_HEAD) == 1
