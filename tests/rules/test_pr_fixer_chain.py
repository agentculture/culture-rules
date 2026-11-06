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

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.engine.runs import step_state
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


# --------------------------------------------------------------------------- re-fix


# --------------------------------------------------------------------------- review-only


# --------------------------------------------------------------------------- E3: no commit


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
