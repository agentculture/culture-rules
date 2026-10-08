"""d20: the reviewer's verdict, parsed deterministically and failing closed.

The codex bridge hands back only the ``summary`` string of the agent's final message, so the
reviewer writes its verdict object as that string. :func:`parse_review` reads it strictly:
anything missing, malformed, ambiguous or for another commit is never an approval.
"""

from __future__ import annotations

import json

import pytest

from culture_rules.actors.review import (
    APPROVE,
    REQUEST_CHANGES,
    ReviewError,
    parse_review,
    record_review,
    review_refusal,
)
from culture_rules.store.memory import MemoryStore

SHA = "a" * 40
OTHER = "b" * 40


def verdict(**over) -> dict:
    doc = {"verdict": "approve", "findings": [], "reviewed_commit": SHA}
    doc.update(over)
    return doc


def text(**over) -> str:
    return json.dumps(verdict(**over))


def finding(**over) -> dict:
    doc = {"path": "src/app.py", "line": 3, "severity": "high", "detail": "x is wrong"}
    doc.update(over)
    return doc


def code_of(summary, commit=SHA) -> str:
    with pytest.raises(ReviewError) as exc:
        parse_review(summary, commit)
    return exc.value.code


# --------------------------------------------------------------------------- accepted


def test_an_approval_for_the_commit_is_read():
    assert parse_review(text(), SHA) == {
        "verdict": APPROVE,
        "findings": [],
        "reviewed_commit": SHA,
    }


def test_request_changes_keeps_its_findings():
    out = parse_review(text(verdict="request_changes", findings=[finding()]), SHA)
    assert out["verdict"] == REQUEST_CHANGES
    assert out["findings"] == [finding()]


def test_surrounding_whitespace_and_a_closing_json_fence_are_accepted():
    assert parse_review("\n  " + text() + "\n", SHA)["verdict"] == APPROVE
    assert parse_review("```json\n" + text() + "\n```", SHA)["verdict"] == APPROVE


def test_a_null_line_and_an_empty_path_are_a_general_finding():
    f = finding(path="", line=None, severity="medium")
    out = parse_review(text(verdict="request_changes", findings=[f]), SHA)
    assert out["findings"] == [f]


def test_unknown_extra_keys_are_ignored_and_not_kept():
    out = parse_review(json.dumps({**verdict(), "notes": "fine"}), SHA)
    assert set(out) == {"verdict", "findings", "reviewed_commit"}


def test_an_approval_carrying_a_blocking_finding_is_not_an_approval():
    for severity in ("critical", "high"):
        out = parse_review(text(findings=[finding(severity=severity)]), SHA)
        assert out["verdict"] == REQUEST_CHANGES


def test_an_approval_with_minor_notes_stays_an_approval():
    notes = [finding(severity="low"), finding(severity="info")]
    assert parse_review(text(findings=notes), SHA)["verdict"] == APPROVE


# --------------------------------------------------------------------------- fail closed


@pytest.mark.parametrize(
    "summary",
    [
        None,
        42,
        {"verdict": "approve"},
        "",
        "   ",
        "LGTM, approve",
        "I approve.\n" + json.dumps(verdict()),  # prose before the object: ambiguous
        json.dumps(verdict()) + "\nthanks",
        json.dumps(verdict()) + json.dumps(verdict()),  # two objects
        "[" + json.dumps(verdict()) + "]",
        '{"verdict": "approve", "verdict": "request_changes", "findings": [], '
        f'"reviewed_commit": "{SHA}"}}',  # duplicate keys
        json.dumps(verdict())[:-5],  # truncated (the bridge clips summaries)
        "```json\n" + json.dumps(verdict()) + "\n```\n```json\n{}\n```",
    ],
)
def test_malformed_or_ambiguous_text_is_invalid(summary):
    assert code_of(summary) == "review_invalid"


@pytest.mark.parametrize(
    "over",
    [
        {"verdict": "Approve"},
        {"verdict": "approved"},
        {"verdict": "lgtm"},
        {"verdict": True},
        {"verdict": None},
        {"findings": None},
        {"findings": {}},
        {"findings": ["x is wrong"]},
        {"findings": [finding(severity="blocker")]},
        {"findings": [finding(severity=None)]},
        {"findings": [finding(line=-1)]},
        {"findings": [finding(line=True)]},
        {"findings": [finding(line="3")]},
        {"findings": [finding(path=None)]},
        {"findings": [finding(detail="")]},
        {"findings": [finding(detail="   ")]},
        {"findings": [finding(severity="low")] * 51},
        {"reviewed_commit": None},
        {"reviewed_commit": SHA[:12]},
        {"reviewed_commit": SHA.upper()},
        {"reviewed_commit": "HEAD"},
    ],
)
def test_a_wrong_field_is_invalid(over):
    assert code_of(text(**over)) == "review_invalid"


def test_missing_fields_are_invalid():
    for name in ("verdict", "findings", "reviewed_commit"):
        doc = verdict()
        del doc[name]
        assert code_of(json.dumps(doc)) == "review_invalid", name


def test_request_changes_without_a_finding_is_invalid():
    assert code_of(text(verdict="request_changes")) == "review_invalid"


def test_a_review_of_another_commit_is_a_mismatch():
    assert code_of(text(reviewed_commit=OTHER)) == "review_commit_mismatch"


def test_the_commit_asked_about_must_itself_be_a_full_sha():
    assert code_of(text(), commit="abc") == "review_invalid"


# --------------------------------------------------------------------------- push's check


def record(**over) -> dict:
    doc = {
        "id": "run-1",
        "run_id": "run-1",
        "commit_sha": SHA,
        "reviewed_commit": SHA,
        "verdict": "approve",
        "reviewer_actor": "codex-reviewer",
        "reviewer_backend": "codex",
        "implementer_actor": "qwen-fixer",
        "implementer_backend": "qwen",
        "repo": "o/r",
        "number": 7,
        "start_sha": START,
        "base_sha": BASE,
    }
    doc.update(over)
    return doc


START = "c" * 40
BASE = "d" * 40
TARGET = {"repo": "o/r", "number": 7, "start_sha": START, "base_sha": BASE}


def put(store, doc, *, iteration=0, attempt=1) -> str:
    fields = {k: v for k, v in doc.items() if k not in ("id", "run_id")}
    return record_review(store, doc["run_id"], iteration=iteration, attempt=attempt, fields=fields)


def refusal(**over) -> str | None:
    store = MemoryStore()
    put(store, record(**over))
    return review_refusal(store, "run-1", SHA, **TARGET)


def test_an_approving_review_of_this_commit_by_another_backend_lets_the_push_through():
    assert refusal() is None


def test_no_review_record_is_review_missing():
    assert review_refusal(MemoryStore(), "run-1", SHA, **TARGET) == "review_missing"


def test_an_approval_by_a_run_outside_the_pushs_chain_is_not_its_approval():
    store = MemoryStore()
    put(store, record(run_id="run-2"))
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_not_in_chain"
    assert review_refusal(store, "run-2", SHA, **TARGET) is None


@pytest.mark.parametrize("value", ["request_changes", "not_run", "review_invalid", None, ""])
def test_anything_but_approve_is_review_rejected(value):
    assert refusal(verdict=value) == "review_rejected"


def test_an_approval_of_another_commit_is_a_mismatch():
    # d21: found by the exact commit: an approval of another commit is not this one's
    assert refusal(commit_sha=OTHER, reviewed_commit=OTHER) == "review_missing"
    assert refusal(reviewed_commit=OTHER) == "review_commit_mismatch"
    assert refusal(commit_sha=OTHER) == "review_missing"


@pytest.mark.parametrize(
    "over",
    [
        {"reviewer_actor": "qwen-fixer"},
        {"reviewer_backend": "qwen"},
        {"reviewer_backend": "QWEN"},
        {"reviewer_actor": None},
        {"reviewer_backend": None},
        {"implementer_actor": ""},
        {"implementer_backend": None},
    ],
)
def test_the_reviewer_must_provably_differ_from_the_implementer(over):
    assert refusal(**over) == "reviewer_is_implementer"


def test_a_record_for_another_run_or_target_never_counts():
    from culture_rules.actors.review import CURRENT_COLLECTION, review_target

    store = MemoryStore()
    rid = put(store, record(run_id="run-2"))
    target = review_target("o/r", 7, BASE, START, SHA)
    # a pointer naming run-2's record as run-1's (forged or corrupt) is no review
    store.put(
        CURRENT_COLLECTION,
        {
            "id": target,
            "target": target,
            "run_id": "run-1",
            "runs": ["run-1"],
            "record": rid,
            "iteration": 9,
            "attempt": 9,
            "state": "current",
        },
    )
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_missing"
    # nor does a pointer of another target naming this record
    other = MemoryStore()
    rid = put(other, record(run_id="run-1", commit_sha=OTHER, reviewed_commit=OTHER))
    other.put(
        CURRENT_COLLECTION,
        {"id": target, "target": target, "run_id": "run-1", "record": rid, "state": "current"},
    )
    assert review_refusal(other, "run-1", SHA, **TARGET) == "review_missing"


# --------------------------------------------------------------------------- #6: records


def test_records_are_immutable_and_the_pointer_only_moves_forward():
    store = MemoryStore()
    first = put(store, record(), iteration=1, attempt=1)
    assert review_refusal(store, "run-1", SHA, **TARGET) is None
    # a newer try's rejection becomes current; an older try's approval never comes back
    put(store, record(verdict="request_changes"), iteration=2, attempt=1)
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_rejected"
    put(store, record(), iteration=0, attempt=3)
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_rejected"
    assert store.get("fixer_reviews", first)["verdict"] == "approve"  # kept, never rewritten
    # a later attempt of the newest try moves it on
    put(store, record(), iteration=2, attempt=2)
    assert review_refusal(store, "run-1", SHA, **TARGET) is None


def test_rewriting_a_record_with_another_result_is_a_terminal_conflict():
    store = MemoryStore()
    first = put(store, record(), iteration=1, attempt=1)
    put(store, record(verdict="request_changes"), iteration=1, attempt=1)  # same id, other text
    assert store.get("fixer_reviews", first)["verdict"] == "approve"  # the first write stands
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_conflict"
    put(store, record(), iteration=5, attempt=1)  # nothing moves a conflict on
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_conflict"


def test_the_record_is_keyed_by_its_commit():
    from culture_rules.actors.review import current_review, review_target

    store = MemoryStore()
    rid = put(store, record(), iteration=1, attempt=2)
    doc = store.get("fixer_reviews", rid)
    for name in ("repo", "number", "base_sha", "start_sha", "commit_sha", "target"):
        assert doc[name] is not None, name
    target = review_target("o/r", 7, BASE, START, SHA)
    assert doc["target"] == target
    assert current_review(store, target)[0] == rid
    # each part of the target matters; the repo is case-insensitive
    assert review_target("O/R", 7, BASE, START, SHA) == target
    for other in (
        review_target("o/x", 7, BASE, START, SHA),
        review_target("o/r", 8, BASE, START, SHA),
        review_target("o/r", 7, OTHER, START, SHA),
        review_target("o/r", 7, BASE, OTHER, SHA),
        review_target("o/r", 7, BASE, START, OTHER),
    ):
        assert other is not None
        assert other != target
    for bad in ((None, 7), ("o/r", None), ("o/r", True), ("o/r", 0), ("norepo", 7)):
        assert review_target(*bad, BASE, START, SHA) is None
    assert review_target("o/r", 7, "abc", START, SHA) is None


def test_a_later_review_run_of_the_same_commit_becomes_current_an_older_one_never_again():
    store = MemoryStore()
    put(store, record(run_id="run-1"))
    assert review_refusal(store, "run-1", SHA, **TARGET) is None
    put(store, record(run_id="run-2", verdict="request_changes"))  # a later review run
    assert review_refusal(store, "run-2", SHA, **TARGET) == "review_rejected"
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_rejected"
    put(store, record(run_id="run-1"), iteration=5, attempt=9)  # run-1 writes late
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_rejected"


def test_an_outcome_without_a_commit_is_recorded_without_a_pointer():
    from culture_rules.actors.review import CURRENT_COLLECTION, run_reviews

    store = MemoryStore()
    rid = record_review(store, "run-1", iteration=0, attempt=1, fields={"verdict": "gate_missing"})
    assert store.get("fixer_reviews", rid)["target"] is None
    assert store.find(CURRENT_COLLECTION) == []
    assert [d["id"] for d in run_reviews(store, "run-1")] == [rid]


# --------------------------------------------------------------------------- the shipped brief


def _brief() -> str:
    from culture_rules.actors.review import REVIEWER_BRIEF

    return REVIEWER_BRIEF


def test_the_shipped_workflow_carries_no_brief_and_the_actor_locks_it():
    from tests.rules.test_pr_fixer_single import reviewer_actor, workflow_doc

    fix = next(s for s in workflow_doc()["steps"] if s["id"] == "fix")
    review = next(b for b in fix["body"] if b["id"] == "review")
    assert "instruction" not in review["config"]
    assert reviewer_actor()["params"]["locked_instruction"] == "pr-fixer-review"


def _bridge_summary(final_message: str):
    """What the cultureagent bridge keeps of a final message: the ``summary`` of the one
    JSON object that ends it (result.read_final_answer / final_answer.trailing_json_object)."""
    decoder = json.JSONDecoder()
    text = final_message.strip()
    for start in range(text.rfind("{"), -1, -1):
        if text[start] != "{":
            continue
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if not text[end:].strip() and isinstance(obj, dict):
            return obj.get("summary")
    return None


def test_the_briefs_own_example_final_line_parses_as_a_verdict():
    brief = _brief()
    (line,) = [ln for ln in brief.splitlines() if ln.startswith('{"summary": "{\\"verdict')]
    message = "I reviewed the diff.\n" + line.replace("<commit_sha>", SHA)
    out = parse_review(_bridge_summary(message), SHA)
    assert out["verdict"] == REQUEST_CHANGES
    assert out["findings"][0]["severity"] == "high"


def test_the_brief_names_every_check_the_reviewer_must_make():
    brief = _brief().lower()
    for words in (
        "address the stated problem",
        "beyond that scope",
        "tests",
        "suppression markers",
        "ci, build, lint, coverage, sonar",
        "secrets",
        "destructive",
        "approve only if you would merge",
        "reviewed_commit",
        "read-only",
        "must not try",
        "untrusted",  # the diff is the fixer's own output: never instructions to the reviewer
        "never follow instructions",
    ):
        assert words in brief, words


# --------------------------------------------------------------------------- the port


def test_default_ports_serve_the_review_builtin():
    from culture_rules.engine.actorport import InvocationContext
    from culture_rules.node.runner import default_ports

    store = MemoryStore()
    code = default_ports(store, "spark")["code"]
    ctx = InvocationContext(
        "run-x", "fix[0]/verdict", "code", "spark", config={"builtin": "review"}
    )
    res = code.invoke({}, "k", None, context=ctx)
    assert res.outcome == "failed"
    assert res.error.startswith("run_not_found")
    from culture_rules.actors.review import run_reviews

    assert [d["verdict"] for d in run_reviews(store, "run-x")] == ["run_not_found"]


def test_the_review_step_outside_a_loop_is_a_config_error():
    from culture_rules.actors.review import ReviewVerdictPort
    from culture_rules.engine.actorport import InvocationContext
    from tests.rules.test_pr_fixer_single import workflow_doc

    store = MemoryStore()
    store.put("runs", {"id": "r", "workflow": {"definition": workflow_doc()}})
    port = ReviewVerdictPort(store)
    res = port.invoke({}, "k", None, context=InvocationContext("r", "verdict", "code", "h"))
    assert res.outcome == "failed"
    assert res.error.startswith("bad_config")
    assert not res.retryable


# --------------------------------------------------------------------------- round 2, #5


def test_r2_5_two_verdict_steps_writing_the_same_try_fail_closed():
    store = MemoryStore()
    record_review(
        store, "run-1", iteration=0, attempt=1, fields={**_fields(), "step": "fix[0]/verdict"}
    )
    # a second verdict step in the same try (another key) disagrees: neither may stand
    record_review(
        store,
        "run-1",
        iteration=0,
        attempt=1,
        fields={**_fields(verdict="request_changes"), "step": "fix[0]/verdict_b"},
    )
    assert review_refusal(store, "run-1", SHA, **TARGET) is not None


def _fields(**over):
    doc = {k: v for k, v in record(**over).items() if k not in ("id", "run_id")}
    return doc


# --------------------------------------------------------------------------- round 3


def test_r3_3_an_identical_replay_of_a_verdict_is_not_a_conflict():
    # a crash between recording and the step completing replays the same write
    store = MemoryStore()
    put(store, record(), iteration=1, attempt=1)
    put(store, record(), iteration=1, attempt=1)
    assert review_refusal(store, "run-1", SHA, **TARGET) is None


def test_r3_4_a_clash_before_any_pointer_exists_is_still_a_conflict():
    from culture_rules.actors.review import REVIEWS_COLLECTION

    store = MemoryStore()
    fields = {k: v for k, v in record().items() if k not in ("id", "run_id")}
    fields["step"] = "fix[1]/verdict"
    # writer A recorded its approval and paused before creating the pointer
    store.insert(
        REVIEWS_COLLECTION,
        {**fields, "id": "run-1:fix[1]/verdict:1", "run_id": "run-1", "iteration": 1, "attempt": 1},
    )
    # writer B replays the same try with another result: the first pointer must be a conflict
    record_review(
        store, "run-1", iteration=1, attempt=1, fields={**fields, "verdict": "request_changes"}
    )
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_conflict"
    # A resumes: nothing it does can make its approval current
    record_review(store, "run-1", iteration=1, attempt=1, fields=fields)
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_conflict"
