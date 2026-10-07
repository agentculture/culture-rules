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
    }
    doc.update(over)
    return doc


START = "c" * 40
TARGET = {"repo": "o/r", "number": 7, "start_sha": START}


def refusal(**over) -> str | None:
    store = MemoryStore()
    store.put("fixer_reviews", record(**over))
    return review_refusal(store, "run-1", SHA, **TARGET)


def test_an_approving_review_of_this_commit_by_another_backend_lets_the_push_through():
    assert refusal() is None


def test_no_review_record_is_review_missing():
    assert review_refusal(MemoryStore(), "run-1", SHA, **TARGET) == "review_missing"
    store = MemoryStore()
    store.put("fixer_reviews", record(id="run-2", run_id="run-2"))
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_missing"


@pytest.mark.parametrize("value", ["request_changes", "not_run", "review_invalid", None, ""])
def test_anything_but_approve_is_review_rejected(value):
    assert refusal(verdict=value) == "review_rejected"


def test_an_approval_of_another_commit_is_a_mismatch():
    assert refusal(commit_sha=OTHER, reviewed_commit=OTHER) == "review_commit_mismatch"
    assert refusal(reviewed_commit=OTHER) == "review_commit_mismatch"
    assert refusal(commit_sha=OTHER) == "review_commit_mismatch"


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


def test_a_record_for_another_run_never_counts():
    store = MemoryStore()
    store.put("fixer_reviews", record(run_id="run-2"))  # id says run-1, body says run-2
    assert review_refusal(store, "run-1", SHA, **TARGET) == "review_missing"


# --------------------------------------------------------------------------- the shipped brief


def _brief() -> str:
    from tests.rules.test_pr_fixer_bundle import workflow_doc

    fix = next(s for s in workflow_doc()["steps"] if s["id"] == "fix")
    review = next(b for b in fix["body"] if b["id"] == "review")
    return review["config"]["instruction"]


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
    assert out["verdict"] == REQUEST_CHANGES and out["findings"][0]["severity"] == "high"


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
    assert res.outcome == "failed" and res.error.startswith("run_not_found")
    assert store.get("fixer_reviews", "run-x")["verdict"] == "run_not_found"


def test_the_review_step_outside_a_loop_is_a_config_error():
    from culture_rules.actors.review import ReviewVerdictPort
    from culture_rules.engine.actorport import InvocationContext

    port = ReviewVerdictPort(MemoryStore())
    res = port.invoke({}, "k", None, context=InvocationContext("r", "verdict", "code", "h"))
    assert res.outcome == "failed" and res.error.startswith("bad_config") and not res.retryable
