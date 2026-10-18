"""The step paragraph (#69 t6, d11): step descriptions after fixed lead-ins, one sentence per
line, plain consecutive steps joined with ``; then``."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from culture_rules.model.describe import BUILTIN_WORDS, action_text, step_text
from culture_rules.model.paragraph import (
    BARE_LEADS,
    JOIN_KEYS,
    LEAD_KEYS,
    MARK_KEYS,
    PHRASES,
    SENTENCE_KEYS,
    SIBLING_KEYS,
    STANDIN_KEYS,
    TAIL_KEYS,
    compose,
    generic_line,
    lower_first,
    mark_disabled,
    render_marked,
    standin_text,
    step_paragraph,
)
from culture_rules.model.workflow import Workflow

ROOT = Path(__file__).resolve().parents[2]
RULES_DOCS = ROOT / "docs" / "rules"
FIXTURE = ROOT / "tests" / "fixtures" / "workflow_descriptions.json"
DELIVER = {"op": "compare", "cmp": "==", "left": {"field": "deliver"}, "right": {"literal": True}}


def _cmp(field: str, value: object) -> dict:
    return {"op": "compare", "cmp": "==", "left": {"field": field}, "right": {"literal": value}}


FIXER = _cmp("fixer", "claude")


def _step(sid: str, kind: str = "code", **kw: object) -> dict:
    out: dict = {"id": sid, "kind": kind, "description": kw.pop("description", f"{sid} runs.")}
    out.update(kw)
    return out


def _edges(*pairs: tuple[str, str]) -> list[dict]:
    return [{"source": s, "source_port": "out", "target": t, "target_port": "in"} for s, t in pairs]


def _wf(steps: list[dict], edges: list[dict] | None = None) -> dict:
    return {"id": "w", "name": "W", "steps": steps, "edges": edges or []}


def _phrases(wf: dict) -> dict[str, str]:
    return {seg["step"]: seg["phrase"] for seg in step_paragraph(wf)["segments"]}


def _text(wf: dict) -> str:
    return step_paragraph(wf)["text"]


def _chain(*steps: dict) -> dict:
    """Steps in sequence, ending with one that has no condition (so no condition is shared
    by every remaining step and the "From here on" rule stays out of the way)."""
    steps = (*steps, _step("end", description="End."))
    ids = [s["id"] for s in steps]
    return _wf(list(steps), _edges(*zip(ids, ids[1:])))


# --------------------------------------------------------------------------- the table


def test_the_table_is_lead_ins_connectors_trailing_clauses_and_sentences():
    assert PHRASES
    for key, value in PHRASES.items():
        assert isinstance(value, str), key
        assert not value.endswith(":"), key  # d11: no colon
    every = LEAD_KEYS | JOIN_KEYS | TAIL_KEYS | STANDIN_KEYS | SENTENCE_KEYS | MARK_KEYS | {"by"}
    assert every == set(PHRASES)
    for key in LEAD_KEYS:  # a lead-in opens a sentence: capitalised, no closing punctuation
        assert PHRASES[key][0].isupper(), key
        assert PHRASES[key][-1] not in ".,;:", key
    for key in TAIL_KEYS:  # a trailing clause follows a comma: lower-case
        assert PHRASES[key][0].islower(), key
    assert {k: PHRASES[k] for k in JOIN_KEYS} == {
        "then_grouped": "; then",
        "list_grouped": ",",
        "and_grouped": " and",
    }
    assert SIBLING_KEYS == {"list_grouped", "and_grouped"}
    assert BARE_LEADS == {"first", "then", "same_time"} | JOIN_KEYS
    assert {k: PHRASES[k] for k in MARK_KEYS} == {"disabled_mark": "(disabled)"}
    for key in SENTENCE_KEYS:
        assert PHRASES[key].endswith("."), key


def test_compose_joins_parts_with_commas_and_no_colon():
    assert compose([("then", {}), ("if", {"condition": "x is 1"})]) == "Then, if x is 1"
    assert compose([("if", {"condition": "x is 1"})]) == "If x is 1"
    assert compose([("and_grouped", {}), ("if", {"condition": "x"})]) == " and, if x"
    assert compose([("list_grouped", {}), ("if", {"condition": "x"})]) == ", if x"


# --------------------------------------------------------------------------- constructs


def test_sequence_joins_one_sentence():
    wf = _wf([_step("a"), _step("b")], _edges(("inputs", "a"), ("a", "b")))
    out = step_paragraph(wf)
    assert [s["phrase"] for s in out["segments"]] == ["First", "; then"]
    assert [s["sentence"] for s in out["segments"]] == [0, 0]
    assert out["text"] == "First a runs; then b runs."


def test_same_predecessor_siblings_without_when_run_at_the_same_time():
    """d13: siblings read together, ``Then, at the same time, A and B``."""
    wf = _wf([_step("a"), _step("b"), _step("c")], _edges(("a", "b"), ("a", "c")))
    assert _phrases(wf) == {"a": "First", "b": "Then, at the same time,", "c": " and"}
    assert _text(wf) == "First a runs.\nThen, at the same time, b runs and c runs."
    seg = {s["step"]: s for s in step_paragraph(wf)["segments"]}
    assert seg["b"]["keys"] == ["then", "same_time"]
    assert seg["c"]["keys"] == ["and_grouped"]
    assert seg["c"]["grouped"] is True
    assert seg["b"]["grouped"] is False


def test_three_siblings_read_a_b_and_c():
    steps = [_step("a"), _step("b"), _step("c"), _step("d")]
    wf = _wf(steps, _edges(("a", "b"), ("a", "c"), ("a", "d")))
    assert _phrases(wf) == {"a": "First", "b": "Then, at the same time,", "c": ",", "d": " and"}
    assert _text(wf) == "First a runs.\nThen, at the same time, b runs, c runs and d runs."


def test_a_disabled_sibling_reads_on_its_own():
    steps = [_step("a"), _step("b"), _step("c", enabled=False), _step("d")]
    wf = _wf(steps, _edges(("a", "b"), ("a", "c"), ("a", "d")))
    assert _text(wf) == (
        "First a runs.\nThen b runs.\nAt the same time c runs.\nAt the same time d runs."
    )
    steps = [_step("a"), _step("b", enabled=False), _step("c"), _step("d")]
    wf = _wf(steps, _edges(("a", "b"), ("a", "c"), ("a", "d")))
    assert _text(wf) == "First a runs.\nThen b runs.\nAt the same time c runs and d runs."


def test_a_loop_sibling_ends_the_sibling_sentence_before_its_body():
    loop = _step("l", "for_each", max_iterations=2, body=[_step("x")])
    steps = [_step("a"), _step("b"), loop, _step("d")]
    wf = _wf(steps, _edges(("a", "b"), ("a", "l"), ("a", "d")))
    assert _text(wf) == (
        "First a runs.\nThen, at the same time, b runs and l runs, for each item (up to 2).\n"
        "Each time, x runs.\nAt the same time d runs."
    )


def test_siblings_with_two_whens_read_depending_on_the_conditions_then_then():
    steps = [
        _step("r1", config={"when": _cmp("reviewer", "codex")}),
        _step("r2", config={"when": _cmp("reviewer", "qwen")}),
        _step("v"),
    ]
    p = _phrases(_wf(steps, _edges(("inputs", "r1"), ("inputs", "r2"), ("r1", "v"), ("r2", "v"))))
    assert p == {
        "r1": "Depending on the conditions, if reviewer is codex,",
        "r2": " and, if reviewer is qwen,",
        "v": "Then",
    }
    text = _text(_wf(steps, _edges(("r1", "v"), ("r2", "v"))))
    assert text == (
        "Depending on the conditions, if reviewer is codex, r1 runs and, if reviewer is qwen,"
        " r2 runs.\nThen v runs."
    )
    steps.insert(2, _step("r3"))  # one with no condition: "in any case"
    text = _text(_wf(steps, _edges(("r1", "v"), ("r2", "v"), ("r3", "v"))))
    assert text == (
        "Depending on the conditions, if reviewer is codex, r1 runs, if reviewer is qwen,"
        " r2 runs and, in any case, r3 runs.\nThen v runs."
    )


def test_siblings_with_one_when_still_run_at_the_same_time():
    steps = [_step("a"), _step("b"), _step("c", config={"when": _cmp("x", 1)})]
    p = _phrases(_wf(steps, _edges(("a", "b"), ("a", "c"))))
    assert (p["b"], p["c"]) == ("Then, at the same time,", " and, if x is 1,")


def test_join_on_the_transitive_reduction_diamond():
    steps = [_step("a"), _step("b"), _step("c"), _step("d")]
    edges = _edges(("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("a", "d"))
    assert _phrases(_wf(steps, edges)) == {
        "a": "First",
        "b": "Then, at the same time,",
        "c": " and",
        "d": "Once both are done,",
    }
    assert _text(_wf(steps, edges)) == (
        "First a runs.\nThen, at the same time, b runs and c runs.\nOnce both are done, d runs."
    )


def test_an_implied_edge_never_adds_a_join():
    steps = [_step("a"), _step("b"), _step("c")]
    edges = _edges(("a", "b"), ("b", "c"), ("a", "c"))
    assert _phrases(_wf(steps, edges))["c"] == "; then"


def test_join_of_three():
    steps = [_step("a"), _step("b"), _step("c"), _step("d"), _step("e", name="Tail")]
    edges = _edges(("a", "b"), ("a", "c"), ("a", "d"), ("b", "e"), ("c", "e"), ("d", "e"))
    assert _phrases(_wf(steps, edges))["e"] == "Once those are done,"


def test_a_join_on_the_step_just_read_is_then():
    steps = [_step("a", name="Alpha"), _step("b"), _step("c")]
    edges = _edges(("a", "b"), ("a", "c"), ("b", "c"))  # c waits on b only (a is implied)
    assert _phrases(_wf(steps, edges))["c"] == "; then"
    steps = [_step("a"), _step("b"), _step("c"), _step("d")]
    edges = _edges(("a", "c"), ("b", "c"), ("c", "d"), ("a", "d"))
    p = _phrases(_wf(steps, edges))
    assert (p["a"], p["b"]) == ("First, at the same time,", " and")
    assert (p["c"], p["d"]) == ("Once both are done,", "; then")


def test_a_join_on_an_earlier_step_names_it():
    steps = [_step("a", name="Alpha"), _step("b"), _step("c"), _step("d")]
    edges = _edges(("a", "b"), ("b", "c"), ("a", "d"))
    assert _phrases(_wf(steps, edges))["d"] == "Once Alpha is done,"
    steps = [_step("a", name="Alpha"), _step("b", name="Beta"), _step("c"), _step("d")]
    edges = _edges(("a", "b"), ("a", "c"), ("a", "d"), ("b", "d"))  # a -> b: a is implied
    p = _phrases(_wf(steps, edges))
    assert (p["b"], p["c"], p["d"]) == ("Then, at the same time,", " and", "Once Beta is done,")
    steps = [_step("a", name="Alpha"), _step("b", name="Beta"), _step("c"), _step("d")]
    edges = _edges(("a", "c"), ("a", "d"), ("b", "d"))
    p = _phrases(_wf(steps, edges))
    assert (p["a"], p["b"], p["c"], p["d"]) == (
        "First, at the same time,",
        " and",
        "Once Alpha is done,",
        "Once Alpha and Beta are done,",
    )
    steps = [_step("a"), _step("b"), _step("c"), _step("d")]
    edges = _edges(("a", "d"), ("b", "d"), ("c", "d"))
    p = _phrases(_wf(steps, edges))  # a, b and c all start with the run
    assert p["d"] == "Once those are done,"
    steps = [_step("a"), _step("b"), _step("c"), _step("d", name="D"), _step("e")]
    edges = _edges(("a", "b"), ("b", "c"), ("a", "d"), ("a", "e"), ("c", "e"), ("d", "e"))
    assert _phrases(_wf(steps, edges))["e"] == "Once c and D are done,"


def test_a_later_step_with_no_predecessor_starts_with_the_run():
    steps = [_step("a"), _step("b"), _step("c")]
    assert _phrases(_wf(steps, _edges(("a", "b"))))["c"] == "Separately, from the start,"


def test_when_starts_a_new_sentence():
    wf = _wf([_step("a"), _step("b", config={"when": _cmp("send", True)})], _edges(("a", "b")))
    assert _phrases(wf)["b"] == "Then, if send is true,"
    assert _text(wf) == "First a runs.\nThen, if send is true, b runs."


def test_when_past_the_cap_reads_its_condition_holds():
    long = {"op": "and", "args": [_cmp("field_number_" + str(i), "x" * 10) for i in range(8)]}
    wf = _wf([_step("a"), _step("b", config={"when": long})], _edges(("a", "b")))
    assert _phrases(wf)["b"] == "Then, if its condition holds,"


def test_retry_is_a_trailing_clause_and_starts_a_sentence():
    wf = _wf(
        [_step("a"), _step("b", retry={"max_attempts": 3, "backoff_s": 1.0})], _edges(("a", "b"))
    )
    assert _text(wf) == "First a runs.\nThen b runs, trying up to 3 times."
    seg = step_paragraph(wf)["segments"][1]
    assert (seg["phrase"], seg["tail"], seg["tail_keys"]) == (
        "Then",
        ", trying up to 3 times.",
        ["retry"],
    )
    wf = _wf([_step("a"), _step("b", retry={"max_attempts": 1})], _edges(("a", "b")))
    assert _text(wf) == "First a runs; then b runs."


def test_on_error_skip_is_a_trailing_clause_that_may_join():
    wf = _wf([_step("a"), _step("b", config={"on_error": "skip"})], _edges(("a", "b")))
    assert _text(wf) == "First a runs; then b runs, carrying on even if it fails."


def test_combined_modifiers_follow_a_fixed_order():
    step = _step(
        "a", config={"when": _cmp("send", True), "on_error": "skip"}, retry={"max_attempts": 3}
    )
    assert _text(_wf([step])) == (
        "First, if send is true, a runs, trying up to 3 times, carrying on even if it fails."
    )


@pytest.mark.parametrize(
    ("seconds", "words"),
    [
        (300, "5 minutes"),
        (60, "1 minute"),
        (120, "2 minutes"),
        (45, "45 seconds"),
        (1, "1 second"),
        (7200, "2 hours"),
        (3600, "1 hour"),
        (90.0, "90 seconds"),
        (1.5, "1.5 seconds"),
    ],
)
def test_wait_seconds(seconds, words):
    wf = _wf([_step("w", "wait", config={"seconds": seconds})])
    assert _text(wf) == f"First w runs, waiting {words}."


def test_wait_guard_and_the_step_after_a_wait_joins_it():
    guard = {"value": "head_unchanged", "actor": "github-app", "ref": "inputs.head_sha"}
    wf = _wf(
        [_step("w", "wait", config={"seconds": 300, "guard": guard}), _step("b")],
        _edges(("w", "b")),
    )
    assert _text(wf) == (
        "First w runs, waiting 5 minutes, and the run stops if the PR head moves; then b runs."
    )


def test_a_wait_starts_its_own_sentence():
    wf = _wf([_step("a"), _step("w", "wait", config={"seconds": 60})], _edges(("a", "w")))
    assert _text(wf) == "First a runs.\nThen w runs, waiting 1 minute."


def test_for_each_and_its_body_in_declared_order():
    body = [_step("x"), _step("y")]  # unconnected: still one after another
    loop = _step("loop", "for_each", max_iterations=20, body=body, config={"items": "items"})
    wf = _wf([_step("a"), loop], _edges(("a", "loop")))
    out = step_paragraph(wf)
    assert [(s["step"], s["phrase"], s["depth"], s["sentence"]) for s in out["segments"]] == [
        ("a", "First", 0, 0),
        ("loop", "Then", 0, 1),
        ("x", "Each time,", 1, 2),
        ("y", "; then", 1, 2),
    ]
    assert out["text"] == (
        "First a runs.\nThen loop runs, for each item (up to 20).\nEach time, x runs; then y runs."
    )


def test_loop_body_ignores_edges_for_its_phrases():
    body = [_step("x"), _step("y"), _step("z")]
    edges = _edges(("x", "z"), ("y", "z"), ("inputs", "x"), ("inputs", "y"))
    loop = _step("loop", "for_each", max_iterations=2, body=body)
    p = _phrases(_wf([loop], edges))
    assert (p["x"], p["y"], p["z"]) == ("Each time,", "; then", "; then")


def test_retry_until():
    until = {"op": "exists", "arg": {"field": "verdict"}}
    loop = _step("fix", "retry_until", max_iterations=1, config={"until": until}, body=[_step("b")])
    assert _text(_wf([loop])) == (
        "First fix runs, until verdict is set, at most once.\nEach time, b runs."
    )
    loop["max_iterations"] = 2
    assert "until verdict is set, at most twice.\n" in _text(_wf([loop]))
    loop["max_iterations"] = 5
    assert "until verdict is set, at most 5 times.\n" in _text(_wf([loop]))


def test_the_body_starts_its_own_sentence_even_with_a_condition_or_retry():
    """d13: a loop's body always starts its own sentence (indented in the editor)."""
    body = [_step("x", config={"when": FIXER}, retry={"max_attempts": 2}), _step("y")]
    loop = _step("loop", "for_each", max_iterations=3, body=body)
    out = step_paragraph(_wf([loop]))
    assert out["text"] == (
        "First loop runs, for each item (up to 3).\nEach time, if fixer is claude, x runs,"
        " trying up to 2 times.\nThen y runs."
    )
    assert [(s["depth"], s["sentence"], s["grouped"]) for s in out["segments"]] == [
        (0, 0, False),
        (1, 1, False),
        (1, 2, False),
    ]


def test_a_condition_shared_by_every_remaining_step_is_said_once():
    steps = [
        _step("a"),
        _step("b", config={"when": DELIVER}),
        _step("c", config={"when": DELIVER}),
        _step("d", config={"when": DELIVER}),
    ]
    wf = _wf(steps, _edges(("a", "b"), ("b", "c"), ("c", "d")))
    assert _phrases(wf) == {
        "a": "First",
        "b": "Then, from here on, only if deliver is true,",
        "c": "; then",
        "d": "; then",
    }
    assert _text(wf) == (
        "First a runs.\nThen, from here on, only if deliver is true, b runs; then c runs; then"
        " d runs."
    )


def test_a_step_whose_condition_differs_gets_its_own_if():
    steps = [
        _step("b", config={"when": DELIVER}),
        _step("c", config={"when": _cmp("send", True)}),
        _step("d", config={"when": DELIVER}),
    ]
    p = _phrases(_wf(steps, _edges(("b", "c"), ("c", "d"))))
    assert p == {
        "b": "First, if deliver is true,",
        "c": "Then, if send is true,",
        "d": "Then, if deliver is true,",
    }


def test_a_shared_condition_in_a_loop_body_is_said_once_there():
    body = [_step("x", config={"when": DELIVER}), _step("y", config={"when": DELIVER})]
    loop = _step("loop", "for_each", max_iterations=3, body=body)
    p = _phrases(_wf([loop]))
    assert p["x"] == "Each time, from here on, only if deliver is true,"
    assert p["y"] == "; then"


def test_disabled_step_has_its_normal_phrase_and_a_flag_not_words():
    wf = _wf([_step("a", enabled=False)])
    out = step_paragraph(wf)
    assert _phrases(wf)["a"] == "First"
    assert out["segments"][0]["disabled"] is True
    assert "isabled" not in out["text"]
    assert "disabled" not in PHRASES
    assert "rule_disabled" not in PHRASES
    on = step_paragraph(_wf([_step("a")]))
    assert "disabled" not in on["segments"][0]
    assert on["text"] == out["text"]


def test_a_disabled_step_is_its_own_sentence():
    def make(*on: bool):
        steps = [
            _step(sid, config={"when": FIXER}, description=f"Step {sid}.", enabled=flag)
            for sid, flag in zip("bcd", on)
        ]
        return step_paragraph(_chain(_step("a", description="Starts."), *steps))

    control = make(True, True, True)
    assert [s["grouped"] for s in control["segments"][1:4]] == [False, True, True]
    for flags in ((True, False, True), (False, True, True), (True, True, False), (False,) * 3):
        out = make(*flags)
        for s, flag in zip(out["segments"][1:4], flags):
            assert s.get("disabled", False) is (not flag)
            assert not (s["grouped"] and (not flag))
        # a disabled step breaks the run: the step after it starts a new sentence
        for i, flag in enumerate(flags[:-1]):
            if not flag:
                assert not out["segments"][i + 2]["grouped"]
        assert "isabled" not in out["text"]
        marked = render_marked(out, lambda c: f"~~{c}~~")
        assert marked.replace("~~", "") == out["text"]
        for line in marked.splitlines():  # a struck step is a whole line of its own
            assert "~~" not in line or (line.startswith("~~") and line.endswith("~~"))


def test_mark_disabled_goes_before_the_closing_period():
    """d13: the CLI marks a disabled segment ``(disabled)``, after its words."""
    assert (
        mark_disabled("It starts on cron, on spark2.") == "It starts on cron, on spark2 (disabled)."
    )
    assert mark_disabled("Then b runs, trying up to 3 times.") == (
        "Then b runs, trying up to 3 times (disabled)."
    )
    assert mark_disabled("Ready?") == "Ready (disabled)?"
    assert mark_disabled("First a") == "First a (disabled)"
    out = step_paragraph(_wf([_step("a"), _step("b", enabled=False)], _edges(("a", "b"))))
    marked = render_marked(out, mark_disabled)
    assert marked == "First a runs.\nThen b runs (disabled)."
    assert marked.replace(" (disabled)", "") == out["text"]


def test_a_workflow_model_reads_like_its_dict_form():
    wf = _wf([_step("a"), _step("b")], _edges(("a", "b")))
    assert step_paragraph(Workflow.from_dict(wf)) == step_paragraph(wf)


def test_steps_read_in_a_stable_topological_order():
    wf = _wf([_step("b"), _step("a")], _edges(("a", "b")))
    assert [s["step"] for s in step_paragraph(wf)["segments"]] == ["a", "b"]


def test_consecutive_steps_sharing_a_condition_join_one_sentence():
    wf = _chain(
        _step("a", description="Starts."),
        _step("b", config={"when": FIXER}, description="The agent fixes it."),
        _step("c", config={"when": FIXER}, description="Packs the commits."),
        _step("d", config={"when": FIXER}, description="Unpacks them."),
        _step("e", config={"when": _cmp("fixer", "codex")}, description="Codex fixes it."),
    )
    out = step_paragraph(wf)
    assert out["text"] == (
        "First starts.\n"
        "Then, if fixer is claude, the agent fixes it; then packs the commits; then unpacks them.\n"
        "Then, if fixer is codex, codex fixes it.\n"
        "Then end."
    )
    seg = {s["step"]: s for s in out["segments"]}
    assert (seg["c"]["phrase"], seg["c"]["keys"], seg["c"]["grouped"]) == (
        "; then",
        ["then_grouped"],
        True,
    )
    assert seg["c"]["text"] == "Packs the commits."  # the segment keeps it unchanged
    assert seg["c"]["shown"] == "packs the commits"
    assert seg["c"]["tail"] == ""
    assert seg["d"]["tail"] == "."
    assert seg["b"]["grouped"] is False
    assert seg["e"]["grouped"] is False
    assert [seg[k]["sentence"] for k in "abcde"] == [0, 1, 1, 1, 2]


def test_a_retry_or_wait_starts_its_own_sentence_but_skip_joins():
    wf = _chain(
        _step("b", config={"when": FIXER}, description="One."),
        _step("c", config={"when": FIXER}, retry={"max_attempts": 2}, description="Two."),
        _step("d", config={"when": FIXER, "on_error": "skip"}, description="Three."),
        _step("e", config={"when": FIXER}, description="Four."),
    )
    assert _text(wf) == (
        "First, if fixer is claude, one.\n"
        "Then, if fixer is claude, two, trying up to 2 times; then three, carrying on even if"
        " it fails; then four.\n"
        "Then end."
    )


def test_a_join_or_sibling_is_not_grouped():
    steps = [
        _step("a", config={"when": FIXER}, description="One."),
        _step("b", config={"when": FIXER}, description="Two."),
        _step("c", config={"when": FIXER}, description="Three."),
        _step("d", description="Four."),
    ]
    # b and c both follow a and both carry a condition: alternatives, read together (d13);
    # d, after the group, starts its own sentence
    out = step_paragraph(_wf(steps, _edges(("a", "b"), ("a", "c"))))
    assert [s["grouped"] for s in out["segments"]] == [False, False, True, False]
    assert [s["sentence"] for s in out["segments"]] == [0, 1, 1, 2]


def test_a_stand_in_stays_inside_its_group():
    wf = _chain(
        _step("a", config={"when": FIXER}, description="One."),
        _step("b", name="Beta step", config={"when": FIXER}, description=""),
        _step("c", config={"when": FIXER}, description="Three."),
    )
    out = step_paragraph(wf)
    assert out["text"] == "First, if fixer is claude, one; then beta step; then three.\nThen end."
    seg = out["segments"][1]
    assert seg["standin"] is True
    assert seg["gap"] is True
    assert seg["grouped"] is True
    assert (seg["text"], seg["shown"]) == ("Beta step", "beta step")
    marked = render_marked(out, lambda c: c, lambda c: f"[{c}]")
    assert marked == "First, if fixer is claude, one; then [beta step]; then three.\nThen end."


def test_a_description_without_a_final_period_still_joins():
    wf = _chain(
        _step("a", config={"when": FIXER}, description="One"),
        _step("b", config={"when": FIXER}, description="Two"),
    )
    assert _text(wf) == "First, if fixer is claude, one; then two.\nThen end."


def test_a_question_mark_ends_its_sentence_without_another_period():
    assert _text(_wf([_step("a", description="Ready?")])) == "First ready?"


@pytest.mark.parametrize(
    ("description", "shown"),
    [
        ("Packs it.", "packs it"),
        ("EPUB goes out.", "EPUB goes out"),
        ("PR is read.", "PR is read"),
        ("GitGuardian holds.", "GitGuardian holds"),
        ("SonarCloud lists.", "SonarCloud lists"),
        ("PR's branch moves.", "PR's branch moves"),
        ('"Quoted" first.', '"Quoted" first'),
        ("3 tries.", "3 tries"),
        ("already lower.", "already lower"),
        ("A step.", "a step"),
        ("Hand-off: export.", "hand-off: export"),
        ("The Qwen agent fixes it.", "the Qwen agent fixes it"),
    ],
)
def test_only_the_first_letter_after_a_lead_in_is_lowered(description, shown):
    wf = _chain(
        _step("a", config={"when": FIXER}, description="One."),
        _step("b", config={"when": FIXER}, description=description),
    )
    assert _text(wf) == f"First, if fixer is claude, one; then {shown}.\nThen end."
    assert lower_first(description) == shown + "."


def test_grouping_in_a_loop_body_and_never_across_a_loop():
    body = [
        _step("x", config={"when": FIXER}, description="Ex."),
        _step("y", config={"when": FIXER}, description="Why."),
        _step("w", description="Double-u."),
    ]
    loop = _step("loop", "for_each", max_iterations=2, body=body, config={"when": FIXER})
    after = _step("z", config={"when": FIXER}, description="Zed.")
    out = step_paragraph(_chain(loop, after))
    seg = {s["step"]: s for s in out["segments"]}
    assert seg["x"]["grouped"] is False  # d13: the body starts its own sentence
    assert seg["y"]["grouped"] is True  # the body: one sentence
    assert seg["w"]["grouped"] is False  # condition change
    assert seg["z"]["grouped"] is False  # never glued onto the loop's last body step


def test_the_from_here_on_rule_is_unchanged():
    steps = [
        _step("a", description="Start."),
        _step("b", config={"when": DELIVER}, description="B."),
        _step("c", config={"when": DELIVER}, description="C."),
    ]
    wf = _wf(steps, _edges(("a", "b"), ("b", "c")))
    assert _text(wf) == "First start.\nThen, from here on, only if deliver is true, b; then c."


# --------------------------------------------------------------------------- stand-ins


def test_an_empty_description_stands_in_with_the_step_name():
    wf = _wf([_step("a"), _step("b", name="Beta", description="")], _edges(("a", "b")))
    seg = step_paragraph(wf)["segments"][1]
    assert seg["text"] == "Beta"
    assert seg["standin"] is True
    assert seg["phrase"] == "; then"
    assert _text(wf) == "First a runs; then beta."
    wf = _wf([_step("a", description="")])  # no name: its generic line
    seg = step_paragraph(wf)["segments"][0]
    assert seg["text"] == "code"
    assert seg["standin"] is True
    assert _text(wf) == "First code."
    written = step_paragraph(_wf([_step("a")]))["segments"][0]
    assert written["standin"] is False
    assert written["gap"] is False


def test_standin_text_is_the_name_else_the_generic_line():
    assert standin_text({"kind": "code", "name": "Quiet period"}) == "Quiet period"
    assert standin_text({"kind": "code", "name": "  "}) == "code"
    assert standin_text({"kind": "wait", "config": {"seconds": 5}}) == PHRASES["standin_wait"]


@pytest.mark.parametrize(
    ("step", "words"),
    [
        # ai / agent: describe.py's agent words (actor, sandbox), or its placement
        (
            {"kind": "ai", "placement": {"actor": "qwen-fixer"}, "config": {"sandbox": "plan"}},
            "qwen-fixer (agent, plan)",
        ),
        ({"kind": "ai", "placement": {"machine": "spark2"}}, "agent on spark2"),
        # action: describe.action_text
        (
            {
                "kind": "code",
                "config": {
                    "builtin": "action",
                    "action": {"kind": "search.web", "params": {"actor": "brave", "query": "q"}},
                },
            },
            action_text({"kind": "search.web", "params": {"actor": "brave", "query": "q"}}),
        ),
        # builtin code: describe.BUILTIN_WORDS
        ({"kind": "code", "config": {"builtin": "gate"}}, BUILTIN_WORDS["gate"]),
        ({"kind": "code", "config": {"builtin": "select"}}, BUILTIN_WORDS["select"]),
        ({"kind": "code", "config": {"builtin": "newspaper.issue"}}, "builtin newspaper.issue"),
        ({"kind": "code", "config": {"command": "make test"}}, "code `make test`"),
        # wait and loops: their d19 words would repeat the trailing clauses
        ({"kind": "wait", "config": {"seconds": 300}}, PHRASES["standin_wait"]),
        ({"kind": "for_each", "max_iterations": 3}, PHRASES["standin_loop"]),
        ({"kind": "retry_until", "max_iterations": 1}, PHRASES["standin_loop"]),
        # an actor task, logic, an unknown kind and junk: their kind words
        ({"kind": "actor_task", "placement": {"actor": "ori"}}, "task for ori"),
        ({"kind": "logic"}, "logic"),
        ({"kind": "teleport"}, "teleport"),
        ({}, "step"),
        (None, "step"),
    ],
)
def test_generic_line_per_kind(step, words):
    assert generic_line(step) == words


def test_generic_line_never_says_a_condition_or_free_text():
    step = {
        "kind": "code",
        "description": "",
        "config": {"builtin": "gate", "when": FIXER, "notes": "ZZFREE"},
        "notes": "ZZFREE",
    }
    assert generic_line(step) == BUILTIN_WORDS["gate"]


@pytest.mark.parametrize(
    "bad",
    [
        _step("odd", "teleport"),
        _step("odd", config={"when": "deliver"}),
        _step("odd", config={"when": {"op": "nonsense"}}),
        _step("odd", config={"on_error": "retry"}),
        _step("odd", config=["not", "a", "mapping"]),
        _step("odd", retry={"max_attempts": "three"}),
        _step("odd", retry="always"),
        _step("odd", "wait", config={}),
        _step("odd", "wait", config={"seconds": "soon"}),
        _step("odd", "wait", config={"seconds": True}),
        _step("odd", "wait", config={"seconds": -1}),
        _step("odd", "wait", config={"seconds": 5, "guard": {"value": "moon_phase"}}),
        _step("odd", "wait", config={"seconds": 5, "guard": "head_unchanged"}),
        _step("odd", "for_each"),
        _step("odd", "for_each", max_iterations=0),
        _step("odd", "retry_until", max_iterations=2),
        _step("odd", "retry_until", max_iterations=2, config={"until": {"op": "?"}}),
    ],
)
def test_an_unknown_kind_or_malformed_config_reads_as_the_d19_line(bad):
    wf = _wf([_step("a"), bad], _edges(("a", "odd")))
    seg = step_paragraph(wf)["segments"][1]
    assert seg["phrase"] == ""
    assert seg["keys"] == []
    assert seg["fallback"] is True
    assert seg["standin"] is False
    try:
        words = step_text(bad)
    except TypeError:  # d19 itself cannot read a non-numeric retry bound
        words = bad["kind"]
    assert seg["text"] == f"2 odd — {words}"
    assert _text(wf) == f"First a runs.\n{seg['text']}."


@pytest.mark.parametrize(
    "junk",
    [
        None,
        3,
        "x",
        {},
        {"steps": "x"},
        {"steps": [None, 3]},
        {"steps": [{}], "edges": "x"},
        {"steps": [{"id": "a", "kind": "code"}], "edges": [None, {"source": "a"}, 5]},
        {"steps": [{"id": "a", "kind": "for_each", "max_iterations": 1, "body": "x"}]},
    ],
)
def test_describing_never_raises(junk):
    out = step_paragraph(junk)
    assert isinstance(out["segments"], list)
    assert isinstance(out["text"], str)


def test_a_cycle_still_reads_every_step():
    wf = _wf([_step("a"), _step("b")], _edges(("a", "b"), ("b", "a")))
    assert [s["step"] for s in step_paragraph(wf)["segments"]] == ["a", "b"]


# --------------------------------------------------------------------------- committed corpus


def _template_regex(template: str, first: bool) -> str:
    body = template
    if not first:
        body = body[0].lower() + body[1:]
    parts = re.split(r"\{[a-z_]+\}", body)
    return ".+".join(re.escape(p) for p in parts)


def _committed(written: bool = True) -> list[dict]:
    """The nine committed workflows, with the fixture's descriptions (or none at all)."""
    fixture = json.loads(FIXTURE.read_text())
    out = []
    for path in sorted(RULES_DOCS.glob("*/workflows/*.json")):
        wf = json.loads(path.read_text())
        mine = fixture[wf["id"]] if written else {"steps": {}, "description": ""}

        def fill(steps: list[dict], mine: dict = mine) -> None:
            for s in steps:
                s["description"] = mine["steps"].get(s["id"], "")
                fill(s.get("body") or [])

        fill(wf["steps"])
        wf["description"] = mine["description"]
        out.append(wf)
    return out


def _all_steps(steps: list[dict]):
    for s in steps:
        yield s
        yield from _all_steps(s.get("body") or [])


def test_the_fixture_covers_the_nine_committed_workflows():
    committed = _committed()
    assert len(committed) == 9
    fixture = json.loads(FIXTURE.read_text())
    assert set(fixture) == {wf["id"] for wf in committed}
    ids = {wf["id"]: {s["id"] for s in _all_steps(wf["steps"])} for wf in committed}
    for wid, written in fixture.items():
        assert written["description"]
        assert set(written["steps"]) <= ids[wid], wid  # no description for a step that is not there
    gaps = [s for wf in committed for s in _all_steps(wf["steps"]) if not s["description"]]
    assert gaps, "the fixture leaves at least one step without a description"


CORPUS = _committed() + _committed(written=False)


@pytest.mark.parametrize(
    "wf", CORPUS, ids=lambda wf: wf["id"] + ("" if wf["description"] else "-standins")
)
def test_committed_paragraphs_use_only_table_phrases_and_verbatim_descriptions(wf):
    out = step_paragraph(wf)
    segments = out["segments"]
    assert [s["step"] for s in segments] == [s["id"] for s in _all_steps(wf["steps"])]
    sentences: list[str] = []
    for i, seg in enumerate(segments):
        assert seg["fallback"] is False, seg  # every committed step has a phrase
        assert seg["keys"]
        assert all(k in PHRASES for k in seg["keys"])
        # the lead-in: its keys composed, then a comma unless it is one bare lead
        pattern = ""
        for j, k in enumerate(seg["keys"]):  # compose: a space after a bare comma (d13)
            if j:
                pattern += " " if PHRASES[seg["keys"][j - 1]].endswith(",") else ", "
            pattern += _template_regex(PHRASES[k], j == 0)
        if not (len(seg["keys"]) == 1 and seg["keys"][0] in BARE_LEADS):
            pattern += ","
        assert re.fullmatch(pattern, seg["phrase"]), seg
        assert seg["grouped"] is (seg["keys"][0] in JOIN_KEYS)
        # the tail: its trailing clauses, then the sentence's period where it ends
        assert all(k in TAIL_KEYS for k in seg["tail_keys"])
        tail = "".join(", " + _template_regex(PHRASES[k], True) for k in seg["tail_keys"])
        ends = i + 1 == len(segments) or segments[i + 1]["sentence"] != seg["sentence"]
        assert re.fullmatch(tail + (r"\." if ends else ""), seg["tail"]), seg
        # the text: the description verbatim, or the step's stand-in
        step = next(s for s in _all_steps(wf["steps"]) if s["id"] == seg["step"])
        if step["description"]:
            assert seg["text"] == step["description"]
            assert seg["standin"] is False
        else:
            assert seg["standin"] is True
            assert seg["text"] == standin_text(step)
            assert seg["text"] == step["name"]  # every committed step has a name
        # the two documented edits: first letter lowered, the final period dropped
        assert seg["shown"] == lower_first(seg["text"]).removesuffix(".")
        chunk = f"{seg['phrase']} {seg['shown']}{seg['tail']}"
        if seg["grouped"]:
            assert sentences
            assert seg["sentence"] == segments[i - 1]["sentence"]
            sentences[-1] += chunk
        else:
            assert seg["sentence"] == len(sentences)
            sentences.append(chunk)
    assert out["text"] == "\n".join(sentences)


def _levels(steps: list[dict]):
    """Each level's steps in reading order (committed workflows are declared in order)."""
    yield steps
    for s in steps:
        if s.get("body"):
            yield s["body"]


@pytest.mark.parametrize("wf", _committed(), ids=lambda wf: wf["id"])
def test_no_condition_repeats_on_consecutive_steps_that_share_it(wf):
    """h25 (amended by d5, d11): a step sharing the previous step's condition never repeats
    it: it joins that sentence with ``; then``, or the condition is said once for the level."""
    keys = {seg["step"]: seg["keys"] for seg in step_paragraph(wf)["segments"]}
    for level in _levels(wf["steps"]):
        for prev, step in zip(level, level[1:]):
            when = (step.get("config") or {}).get("when")
            if when is not None and when == (prev.get("config") or {}).get("when"):
                assert not {"if", "if_unspoken"} & set(keys[step["id"]]), step["id"]


def _paragraph(wid: str, written: bool = True) -> str:
    return step_paragraph(next(wf for wf in _committed(written) if wf["id"] == wid))["text"]


def test_review_commit_reads_depending_on_the_conditions():
    text = _paragraph("review-commit")
    assert "Depending on the conditions" in text
    assert "at the same time" not in text.lower()
    assert "once both" not in text.lower()


def test_pr_fix_threads_and_sonar_run_at_the_same_time_then_join():
    p = {
        s["step"]: s["phrase"]
        for s in step_paragraph(next(wf for wf in _committed() if wf["id"] == "pr-fix"))["segments"]
    }
    assert p["threads"] == "Then, at the same time,"
    assert p["sonar"] == " and"
    assert p["fix"] == "Once both are done,"


def test_pr_fix_with_no_descriptions_reads_as_the_approved_mock():
    """d11: the operator's approved mock (stand-ins in italics there, plain here), in d13's
    shape: siblings in one sentence, the loop's body on its own line, ``empty``."""
    assert _paragraph("pr-fix", written=False) == "\n".join(
        [
            "First quiet period, waiting 5 minutes, and the run stops if the PR head moves;"
            " then hold while GitGuardian fails.",
            "Then, at the same time, trusted review threads and failing SonarCloud conditions.",
            "Once both are done, fix and test, one try, until verdict is set, at most once.",
            "Each time, if fixer is not set or fixer is empty or qwen-fixer, fix the PR"
            " (Qwen bridge agent).",
            "Then, if fixer is claude-fixer, fix the PR (Claude bridge agent); then pack the"
            " Claude commits for the hand-off; then hand off to spark2"
            " to import the Claude commits.",
            "Then, if fixer is codex-fixer, fix the PR (Codex bridge agent); then pack the"
            " Codex commits for the hand-off; then hand off to spark2 to import the Codex commits.",
            "Then the path that ran; then test gate and diff guard.",
        ]
    )


def test_daily_newspaper_render_joins_by_then_alone():
    wf = next(wf for wf in _committed() if wf["id"] == "daily-newspaper")
    p = {s["step"]: s["keys"] for s in step_paragraph(wf)["segments"]}
    assert p["render"][0] == "then"
    assert not any(k.startswith("once") for k in p["render"])
    assert p["email_gate"] == ["then_grouped"]


def test_a_nested_loop_keeps_every_step_of_its_body():
    """The engine refuses a nested loop, but a stored one still reads every step (review 2)."""
    wf = {
        "id": "w",
        "name": "w",
        "steps": [
            {
                "id": "outer",
                "kind": "for_each",
                "max_iterations": 2,
                "body": [
                    {
                        "id": "inner",
                        "kind": "retry_until",
                        "max_iterations": 3,
                        "body": [{"id": "x", "kind": "code", "description": "Do x."}],
                    }
                ],
            }
        ],
    }
    segments = step_paragraph(wf)["segments"]
    assert [s["step"] for s in segments] == ["outer", "inner", "x"]
    assert "do x" in step_paragraph(wf)["text"]
