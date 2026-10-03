"""`matches` patterns are screened for catastrophic backtracking (security finding #7).

A rule author's pattern runs on external event data inside the node's trigger transaction,
and Python's ``re`` has no time bound, so save-time validation refuses super-linear shapes
and evaluation refuses (never runs) a stored pattern that fails the same screen.
"""

from __future__ import annotations

import time

import pytest

from culture_rules.engine.matching import CONDITION_FALSE, match
from culture_rules.model import condition as c
from culture_rules.model.regex_safety import unsafe_reason
from culture_rules.model.validate import validate
from tests.model.factories import make_rule

UNSAFE = [
    "(a+)+b",
    "(a*)*",
    "(a|aa)+b",
    "(?:a|b|ab)+",
    "((?:a|b|ab){2})+",
    r"(\w+\s?)+",
    "(ab|a.)c",
    "(?:a|b?)+",
    "a*a*a*b",
    ".*a.*a.*b",
    ".*foo.*bar.*",
    "a*a*xa*a*xy",  # trades multiply across a separator (33 s at 7 blocks)
    "(a?|a)x(a?|a)x",
    "(a|ab)(c|bc)x(a|ab)(c|bc)x",
    "a?a?a?aaa",
    r"(a)\1",
    "(?P<x>a)(?P=x)",
    "(?=a*a*a*)x",
]
SAFE = [
    "fix: .*",
    ".*foo.*",
    r"\w+@\w+\.com",
    r"[a-z]+\s+[a-z]+",
    "(ab|c)+",
    r"-?\d+(\.\d+)?",
    "https?://.*",
    r"[^/]+/[^/]+/[^/]+",
    r"(feat|fix|chore)(\(.+\))?: .*",
    r"v\d+\.\d+\.\d+",
    "(?i)(a|A)+",
    r"\w+\s\w+\s\w+",
    "[0-9a-f]{40}",
]


def matches(pattern: str, field: str = "title") -> dict:
    return {"op": "matches", "value": {"field": field}, "pattern": pattern}


@pytest.mark.parametrize("pattern", UNSAFE)
def test_validate_rejects_backtracking_shapes(pattern: str) -> None:
    with pytest.raises(c.UnsafePatternError) as info:
        c.validate(matches(pattern))
    assert info.value.to_dict()["code"] == "unsafe_pattern"


@pytest.mark.parametrize("pattern", SAFE)
def test_validate_accepts_ordinary_patterns(pattern: str) -> None:
    c.validate(matches(pattern))
    assert unsafe_reason(pattern) is None


def test_rule_save_reports_the_unsafe_pattern_as_a_condition_error() -> None:
    errors = validate(make_rule(condition=matches("(a+)+b")))
    assert [e.code for e in errors] == ["condition_invalid"]
    assert "backtrack" in errors[0].message


def test_text_form_rejects_an_unsafe_pattern() -> None:
    parsed = c.parse('matches(trigger.title, "(a|aa)+b")')
    assert parsed.tree is None
    assert "backtrack" in parsed.error.message


@pytest.mark.parametrize("pattern", [".*foo.*", r"[a-z]+\s+[a-z]+", "a*a*b", "(ab|c)+x"])
def test_allowed_pattern_on_max_input_finishes_fast(pattern: str) -> None:
    ctx = {"trigger": {"title": "a" * (c.MAX_INPUT_LEN - 1) + "!"}}  # a failing match
    started = time.perf_counter()
    assert c.evaluate(matches(pattern), ctx) is False
    assert time.perf_counter() - started < 0.1


def test_matches_input_cap_is_two_thousand_characters() -> None:
    assert c.MAX_INPUT_LEN == 2_000
    ctx = {"trigger": {"title": "a" * (c.MAX_INPUT_LEN + 1)}}
    assert c.evaluate(matches("a*"), ctx) is False


def test_stored_unsafe_pattern_is_refused_quickly_at_evaluation() -> None:
    """A rule saved before the screen existed: evaluation refuses, never runs the regex."""
    ctx = {"trigger": {"title": "a" * 28}}  # (a+)+b took 6.8 s at 28 characters
    started = time.perf_counter()
    with pytest.raises(c.UnsafePatternError):
        c.evaluate(matches("(a+)+b"), ctx)
    assert time.perf_counter() - started < 0.1


def test_stored_unsafe_pattern_is_a_recorded_non_match_in_matching() -> None:
    rule = make_rule(
        condition=matches("(a+)+b", field="data.title"),
        must_after=(),
        may_after=(),
        supersedes=(),
        exclusive_group=None,
    )
    event = {"kind": "event", "type": "github.pr.merged", "data": {"title": "a" * 28}}
    started = time.perf_counter()
    (decision,) = match(event, [rule])
    assert time.perf_counter() - started < 0.1
    assert not decision.fire
    assert decision.reason == CONDITION_FALSE
    assert "backtrack" in decision.detail
