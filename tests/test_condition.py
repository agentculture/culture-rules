"""Tests for the condition predicate tree, evaluator and text view (task t2)."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from culture_rules.model import condition as c

CTX = {
    "trigger": {"pr": {"state": "open", "labels": ["bug", "ui"], "n": 5}, "title": "fix: thing"},
    "variables": {"limit": 3, "name": "x"},
}


def F(p):
    return {"field": p}


def V(n):
    return {"var": n}


def L(v):
    return {"literal": v}


def cmp(op, a, b):
    return {"op": "compare", "cmp": op, "left": a, "right": b}


def test_compare_ops():
    assert c.evaluate(cmp("==", F("pr.state"), L("open")), CTX) is True
    assert c.evaluate(cmp("!=", F("pr.state"), L("open")), CTX) is False
    assert c.evaluate(cmp(">", F("pr.n"), V("limit")), CTX) is True
    assert c.evaluate(cmp("<=", F("pr.n"), L(5)), CTX) is True
    assert c.evaluate(cmp("<", F("pr.n"), L("a")), CTX) is False  # type mismatch, no raise


def test_logic():
    t, f = cmp("==", L(1), L(1)), cmp("==", L(1), L(2))
    assert c.evaluate({"op": "and", "args": [t, t]}, CTX) is True
    assert c.evaluate({"op": "and", "args": [t, f]}, CTX) is False
    assert c.evaluate({"op": "or", "args": [f, t]}, CTX) is True
    assert c.evaluate({"op": "not", "arg": f}, CTX) is True


def test_exists_in_matches():
    assert c.evaluate({"op": "exists", "arg": F("pr.state")}, CTX) is True
    assert c.evaluate({"op": "exists", "arg": F("pr.nope")}, CTX) is False
    assert c.evaluate({"op": "exists", "arg": V("zzz")}, CTX) is False
    assert c.evaluate({"op": "in", "value": L("bug"), "items": F("pr.labels")}, CTX) is True
    assert c.evaluate({"op": "in", "value": L("x"), "items": L(["a", "b"])}, CTX) is False
    assert c.evaluate({"op": "matches", "value": F("title"), "pattern": "fix: .*"}, CTX) is True
    assert c.evaluate({"op": "matches", "value": F("title"), "pattern": "fix"}, CTX) is False
    assert c.evaluate({"op": "matches", "value": F("pr.n"), "pattern": "5"}, CTX) is False


def test_matches_length_cap():
    long = "a" * (c.MAX_PATTERN_LEN + 1)
    with pytest.raises(c.ConditionError):
        c.evaluate({"op": "matches", "value": L("a"), "pattern": long}, CTX)
    big = {"trigger": {"s": "a" * (c.MAX_INPUT_LEN + 1)}, "variables": {}}
    assert c.evaluate({"op": "matches", "value": F("s"), "pattern": "a*"}, big) is False


def test_invalid_trees_raise_structured():
    for bad in [
        {"op": "zap"},
        {"op": "and"},
        {"op": "compare", "cmp": "~", "left": L(1), "right": L(1)},
        "str",
        {"op": "matches", "value": L("a"), "pattern": "("},
    ]:
        with pytest.raises(c.ConditionError) as ei:
            c.evaluate(bad, CTX)
        assert "message" in ei.value.to_dict()


def test_validate():
    c.validate(cmp("==", F("a"), L(1)))
    with pytest.raises(c.ConditionError):
        c.validate({"op": "not"})


def test_pure_1000():
    tree = {
        "op": "and",
        "args": [
            cmp(">", F("pr.n"), V("limit")),
            {"op": "in", "value": L("ui"), "items": F("pr.labels")},
        ],
    }
    import copy

    snap = copy.deepcopy(CTX)
    results = {c.evaluate(tree, CTX) for _ in range(1000)}
    assert results == {True}
    assert CTX == snap


def test_text_roundtrip():
    tree = {
        "op": "or",
        "args": [
            {
                "op": "and",
                "args": [cmp("==", F("pr.state"), L("open")), cmp(">=", V("limit"), L(2.5))],
            },
            {"op": "not", "arg": {"op": "exists", "arg": F("a.b")}},
            {"op": "in", "value": L("x"), "items": L(["x", 1, None, True])},
            {"op": "matches", "value": F("title"), "pattern": r"fix: \d+ \"q\""},
        ],
    }
    text = c.to_text(tree)
    assert c.from_text(text) == tree
    assert c.to_text(c.from_text(text)) == text


def test_text_parse_examples():
    t = c.from_text('trigger.pr.state == "open" && !(vars.limit < 3)')
    assert c.evaluate(t, CTX) is True
    t = c.from_text('"bug" in trigger.pr.labels || exists(vars.zz)')
    assert c.evaluate(t, CTX) is True


def test_precedence_roundtrip():
    t = c.from_text("(trigger.a == 1 || trigger.b == 2) && trigger.c == 3")
    assert t["op"] == "and" and t["args"][0]["op"] == "or"
    assert c.from_text(c.to_text(t)) == t


@pytest.mark.parametrize(
    "text",
    [
        "",
        "trigger.a ==",
        "(trigger.a == 1",
        "trigger.a == 1 )",
        "foo == 1",
        '"unterminated == 1',
        "trigger.a = 1",
        "matches(trigger.a)",
        "trigger.a == 1 &&",
    ],
)
def test_malformed_text(text):
    with pytest.raises(c.ConditionParseError) as ei:
        c.from_text(text)
    d = ei.value.to_dict()
    assert d["code"] == "parse_error" and isinstance(d["position"], int) and d["message"]
    res = c.parse(text)
    assert res.tree is None and res.error is not None


def test_parse_ok():
    res = c.parse("trigger.a == 1")
    assert res.error is None and res.tree is not None


def test_no_eval_exec_compile_in_package():
    root = Path(c.__file__).resolve().parents[1]
    pat = re.compile(r"(?<![\w.])(eval|exec|compile)\s*\(")
    hits = []
    for p in root.rglob("*.py"):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if pat.search(code):
                hits.append(f"{p}:{i}:{line}")
    assert not hits, hits


def test_bandit_b307():
    root = Path(c.__file__).resolve().parents[1]
    r = subprocess.run(
        [sys.executable, "-m", "bandit", "-q", "-t", "B307,B102", "-r", str(root)],
        capture_output=True,
        text=True,
    )
    if "No module named bandit" in r.stderr:
        pytest.skip("bandit not installed")
    assert r.returncode == 0, r.stdout
