"""The step paragraph (#69 t6): step descriptions joined by fixed phrases from the step graph."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from culture_rules.model.describe import step_text
from culture_rules.model.paragraph import GAP, PHRASES, compose, step_paragraph
from culture_rules.model.workflow import Workflow

ROOT = Path(__file__).resolve().parents[2]
RULES_DOCS = ROOT / "docs" / "rules"
FIXTURE = ROOT / "tests" / "fixtures" / "workflow_descriptions.json"
DELIVER = {"op": "compare", "cmp": "==", "left": {"field": "deliver"}, "right": {"literal": True}}


def _cmp(field: str, value: object) -> dict:
    return {"op": "compare", "cmp": "==", "left": {"field": field}, "right": {"literal": value}}


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


# --------------------------------------------------------------------------- the table


def test_every_phrase_ends_with_a_colon():
    assert PHRASES
    assert all(isinstance(v, str) and v.endswith(":") for v in PHRASES.values())


def test_compose_joins_parts_with_commas_and_one_colon():
    assert compose([("then", {}), ("if", {"condition": "x is 1"})]) == "Then, if x is 1:"
    assert compose([("if", {"condition": "x is 1"})]) == "If x is 1:"


# --------------------------------------------------------------------------- constructs


def test_sequence():
    wf = _wf([_step("a"), _step("b")], _edges(("inputs", "a"), ("a", "b")))
    out = step_paragraph(wf)
    assert [s["phrase"] for s in out["segments"]] == ["First:", "Then:"]
    assert out["text"] == "First: a runs. Then: b runs."


def test_same_predecessor_siblings_without_when_run_at_the_same_time():
    wf = _wf([_step("a"), _step("b"), _step("c")], _edges(("a", "b"), ("a", "c")))
    assert _phrases(wf) == {"a": "First:", "b": "Then:", "c": "At the same time:"}


def test_siblings_with_two_whens_read_depending_on_the_conditions_then_then():
    steps = [
        _step("r1", config={"when": _cmp("reviewer", "codex")}),
        _step("r2", config={"when": _cmp("reviewer", "qwen")}),
        _step("v"),
    ]
    p = _phrases(_wf(steps, _edges(("inputs", "r1"), ("inputs", "r2"), ("r1", "v"), ("r2", "v"))))
    assert p == {
        "r1": "Depending on the conditions, if reviewer is codex:",
        "r2": "If reviewer is qwen:",
        "v": "Then:",
    }
    text = step_paragraph(_wf(steps, _edges(("r1", "v"), ("r2", "v"))))["text"]
    assert "at the same time" not in text.lower() and "once both" not in text.lower()


def test_siblings_with_one_when_still_run_at_the_same_time():
    steps = [_step("a"), _step("b"), _step("c", config={"when": _cmp("x", 1)})]
    p = _phrases(_wf(steps, _edges(("a", "b"), ("a", "c"))))
    assert p["c"] == "At the same time, if x is 1:"


def test_join_on_the_transitive_reduction_diamond():
    steps = [_step("a"), _step("b"), _step("c"), _step("d")]
    edges = _edges(("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("a", "d"))
    assert _phrases(_wf(steps, edges)) == {
        "a": "First:",
        "b": "Then:",
        "c": "At the same time:",
        "d": "Once both are done:",
    }


def test_an_implied_edge_never_adds_a_join():
    steps = [_step("a"), _step("b"), _step("c")]
    edges = _edges(("a", "b"), ("b", "c"), ("a", "c"))
    assert _phrases(_wf(steps, edges))["c"] == "Then:"


def test_join_of_three():
    steps = [_step("a"), _step("b"), _step("c"), _step("d"), _step("e", name="Tail")]
    edges = _edges(("a", "b"), ("a", "c"), ("a", "d"), ("b", "e"), ("c", "e"), ("d", "e"))
    assert _phrases(_wf(steps, edges))["e"] == "Once those are done:"


def test_a_join_on_the_step_just_read_is_then():
    steps = [_step("a", name="Alpha"), _step("b"), _step("c")]
    edges = _edges(("a", "b"), ("a", "c"), ("b", "c"))  # c waits on b only (a is implied)
    assert _phrases(_wf(steps, edges))["c"] == "Then:"
    steps = [_step("a"), _step("b"), _step("c"), _step("d")]
    edges = _edges(("a", "c"), ("b", "c"), ("c", "d"), ("a", "d"))
    p = _phrases(_wf(steps, edges))
    assert (p["b"], p["c"], p["d"]) == ("At the same time:", "Once both are done:", "Then:")


def test_a_join_on_an_earlier_step_names_it():
    steps = [_step("a", name="Alpha"), _step("b"), _step("c"), _step("d")]
    edges = _edges(("a", "b"), ("b", "c"), ("a", "d"))
    assert _phrases(_wf(steps, edges))["d"] == "Once Alpha is done:"
    steps = [_step("a", name="Alpha"), _step("b", name="Beta"), _step("c"), _step("d")]
    edges = _edges(("a", "b"), ("a", "c"), ("a", "d"), ("b", "d"))  # a -> b: a is implied
    p = _phrases(_wf(steps, edges))
    assert (p["c"], p["d"]) == ("At the same time:", "Once Beta is done:")
    steps = [_step("a", name="Alpha"), _step("b", name="Beta"), _step("c"), _step("d")]
    edges = _edges(("a", "c"), ("a", "d"), ("b", "d"))
    p = _phrases(_wf(steps, edges))
    assert (p["b"], p["c"], p["d"]) == (
        "At the same time:",
        "Once Alpha is done:",
        "Once Alpha and Beta are done:",
    )
    steps = [_step("a"), _step("b"), _step("c"), _step("d")]
    edges = _edges(("a", "d"), ("b", "d"), ("c", "d"))
    p = _phrases(_wf(steps, edges))  # a, b and c all start with the run
    assert p["d"] == "Once those are done:"
    steps = [_step("a"), _step("b"), _step("c"), _step("d", name="D"), _step("e")]
    edges = _edges(("a", "b"), ("b", "c"), ("a", "d"), ("a", "e"), ("c", "e"), ("d", "e"))
    assert _phrases(_wf(steps, edges))["e"] == "Once c and D are done:"


def test_a_later_step_with_no_predecessor_starts_with_the_run():
    steps = [_step("a"), _step("b"), _step("c")]
    assert _phrases(_wf(steps, _edges(("a", "b"))))["c"] == "Separately, from the start:"


def test_when():
    wf = _wf([_step("a"), _step("b", config={"when": _cmp("send", True)})], _edges(("a", "b")))
    assert _phrases(wf)["b"] == "Then, if send is true:"


def test_when_past_the_cap_reads_its_condition_holds():
    long = {"op": "and", "args": [_cmp("field_number_" + str(i), "x" * 10) for i in range(8)]}
    wf = _wf([_step("a"), _step("b", config={"when": long})], _edges(("a", "b")))
    assert _phrases(wf)["b"] == "Then, if its condition holds:"


def test_retry():
    wf = _wf([_step("a", retry={"max_attempts": 3, "backoff_s": 1.0})])
    assert _phrases(wf)["a"] == "First, trying up to 3 times:"
    wf = _wf([_step("a", retry={"max_attempts": 1})])
    assert _phrases(wf)["a"] == "First:"


def test_on_error_skip():
    wf = _wf([_step("a", config={"on_error": "skip"})])
    assert _phrases(wf)["a"] == "First, carrying on even if it fails:"


def test_combined_modifiers_follow_a_fixed_order():
    step = _step(
        "a", config={"when": _cmp("send", True), "on_error": "skip"}, retry={"max_attempts": 3}
    )
    assert _phrases(_wf([step]))["a"] == (
        "First, if send is true, trying up to 3 times, carrying on even if it fails:"
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
    assert _phrases(wf)["w"] == f"First, it waits {words}:"


def test_wait_guard():
    guard = {"value": "head_unchanged", "actor": "github-app", "ref": "inputs.head_sha"}
    wf = _wf([_step("w", "wait", config={"seconds": 300, "guard": guard})])
    assert _phrases(wf)["w"] == "First, it waits 5 minutes, and the run stops if the PR head moves:"


def test_for_each_and_its_body_in_declared_order():
    body = [_step("x"), _step("y")]  # unconnected: still one after another
    loop = _step("loop", "for_each", max_iterations=20, body=body, config={"items": "items"})
    wf = _wf([_step("a"), loop], _edges(("a", "loop")))
    out = step_paragraph(wf)
    assert [(s["step"], s["phrase"], s["depth"]) for s in out["segments"]] == [
        ("a", "First:", 0),
        ("loop", "Then, for each item (up to 20):", 0),
        ("x", "Each time:", 1),
        ("y", "Then:", 1),
    ]
    assert "at the same time" not in out["text"].lower()


def test_loop_body_ignores_edges_for_its_phrases():
    body = [_step("x"), _step("y"), _step("z")]
    edges = _edges(("x", "z"), ("y", "z"), ("inputs", "x"), ("inputs", "y"))
    loop = _step("loop", "for_each", max_iterations=2, body=body)
    p = _phrases(_wf([loop], edges))
    assert (p["x"], p["y"], p["z"]) == ("Each time:", "Then:", "Then:")


def test_retry_until():
    until = {"op": "exists", "arg": {"field": "verdict"}}
    loop = _step("fix", "retry_until", max_iterations=1, config={"until": until}, body=[_step("b")])
    assert _phrases(_wf([loop]))["fix"] == "First, it tries until verdict is set, at most once:"
    loop["max_iterations"] = 2
    assert _phrases(_wf([loop]))["fix"] == "First, it tries until verdict is set, at most twice:"
    loop["max_iterations"] = 5
    assert _phrases(_wf([loop]))["fix"] == (
        "First, it tries until verdict is set, at most 5 times:"
    )


def test_a_condition_shared_by_every_remaining_step_is_said_once():
    steps = [
        _step("a"),
        _step("b", config={"when": DELIVER}),
        _step("c", config={"when": DELIVER}),
        _step("d", config={"when": DELIVER}),
    ]
    wf = _wf(steps, _edges(("a", "b"), ("b", "c"), ("c", "d")))
    assert _phrases(wf) == {
        "a": "First:",
        "b": "Then, from here on, only if deliver is true:",
        "c": "Then:",
        "d": "Then:",
    }
    assert step_paragraph(wf)["text"].count("deliver") == 1


def test_a_step_whose_condition_differs_gets_its_own_if():
    steps = [
        _step("b", config={"when": DELIVER}),
        _step("c", config={"when": _cmp("send", True)}),
        _step("d", config={"when": DELIVER}),
    ]
    p = _phrases(_wf(steps, _edges(("b", "c"), ("c", "d"))))
    assert p == {
        "b": "First, if deliver is true:",
        "c": "Then, if send is true:",
        "d": "Then, if deliver is true:",
    }


def test_a_shared_condition_in_a_loop_body_is_said_once_there():
    body = [_step("x", config={"when": DELIVER}), _step("y", config={"when": DELIVER})]
    loop = _step("loop", "for_each", max_iterations=3, body=body)
    p = _phrases(_wf([loop]))
    assert p["x"] == "Each time, from here on, only if deliver is true:"
    assert p["y"] == "Then:"


def test_disabled():
    wf = _wf([_step("a", enabled=False)])
    assert _phrases(wf)["a"] == "First, disabled, so it never runs:"


def test_a_workflow_model_reads_like_its_dict_form():
    wf = _wf([_step("a"), _step("b")], _edges(("a", "b")))
    assert step_paragraph(Workflow.from_dict(wf)) == step_paragraph(wf)


def test_steps_read_in_a_stable_topological_order():
    wf = _wf([_step("b"), _step("a")], _edges(("a", "b")))
    assert [s["step"] for s in step_paragraph(wf)["segments"]] == ["a", "b"]


# --------------------------------------------------------------------------- gaps, fallbacks


def test_an_empty_description_is_a_visible_gap_naming_the_step():
    wf = _wf([_step("a"), _step("b", name="Beta", description="")], _edges(("a", "b")))
    seg = step_paragraph(wf)["segments"][1]
    assert seg["text"] == "" and seg["gap"] is True and seg["phrase"] == "Then:"
    assert step_paragraph(wf)["text"] == f"First: a runs. Then: {GAP.format(step='Beta')}"
    wf = _wf([_step("a", description="")])
    assert GAP.format(step="a") in step_paragraph(wf)["text"]


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
    assert seg["phrase"] == "" and seg["keys"] == [] and seg["fallback"] is True
    try:
        words = step_text(bad)
    except TypeError:  # d19 itself cannot read a non-numeric retry bound
        words = bad["kind"]
    assert seg["text"] == f"2 odd — {words}"
    assert step_paragraph(wf)["text"].endswith(seg["text"])


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
    assert isinstance(out["segments"], list) and isinstance(out["text"], str)


def test_a_cycle_still_reads_every_step():
    wf = _wf([_step("a"), _step("b")], _edges(("a", "b"), ("b", "a")))
    assert [s["step"] for s in step_paragraph(wf)["segments"]] == ["a", "b"]


# --------------------------------------------------------------------------- committed corpus


def _template_regex(template: str, first: bool) -> str:
    body = template[:-1]
    if not first:
        body = body[0].lower() + body[1:]
    parts = re.split(r"\{[a-z_]+\}", body)
    return ".+".join(re.escape(p) for p in parts)


def _committed() -> list[dict]:
    fixture = json.loads(FIXTURE.read_text())
    out = []
    for path in sorted(RULES_DOCS.glob("*/workflows/*.json")):
        wf = json.loads(path.read_text())
        written = fixture[wf["id"]]

        def fill(steps: list[dict]) -> None:
            for s in steps:
                s["description"] = written["steps"].get(s["id"], "")
                fill(s.get("body") or [])

        fill(wf["steps"])
        wf["description"] = written["description"]
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


@pytest.mark.parametrize("wf", _committed(), ids=lambda wf: wf["id"])
def test_committed_paragraphs_use_only_table_phrases_and_verbatim_descriptions(wf):
    out = step_paragraph(wf)
    segments = out["segments"]
    assert [s["step"] for s in segments] == [s["id"] for s in _all_steps(wf["steps"])]
    for seg in segments:
        assert seg["fallback"] is False, seg  # every committed step has a phrase
        assert seg["keys"] and all(k in PHRASES for k in seg["keys"])
        pattern = ", ".join(_template_regex(PHRASES[k], i == 0) for i, k in enumerate(seg["keys"]))
        assert re.fullmatch(pattern + ":", seg["phrase"]), seg
    rest = out["text"]
    for seg in segments:  # the text is exactly phrase + description (or gap), in order
        step = next(s for s in _all_steps(wf["steps"]) if s["id"] == seg["step"])
        assert seg["text"] == step["description"]
        shown = seg["text"] or GAP.format(step=step.get("name") or step["id"])
        chunk = f"{seg['phrase']} {shown}"
        assert rest.startswith(chunk), (rest[:80], chunk)
        rest = rest[len(chunk) :].removeprefix(" ")
    assert rest == ""


def _paragraph(wid: str) -> str:
    return step_paragraph(next(wf for wf in _committed() if wf["id"] == wid))["text"]


def test_review_commit_reads_depending_on_the_conditions():
    text = _paragraph("review-commit")
    assert "Depending on the conditions" in text
    assert "at the same time" not in text.lower() and "once both" not in text.lower()


def test_pr_fix_threads_and_sonar_run_at_the_same_time_then_join():
    p = {
        s["step"]: s["phrase"]
        for s in step_paragraph(next(wf for wf in _committed() if wf["id"] == "pr-fix"))["segments"]
    }
    assert p["threads"] == "Then:"
    assert p["sonar"] == "At the same time:"
    assert p["fix"].startswith("Once both are done, it tries until")


def test_daily_newspaper_render_joins_by_then_alone():
    wf = next(wf for wf in _committed() if wf["id"] == "daily-newspaper")
    p = {s["step"]: s["keys"] for s in step_paragraph(wf)["segments"]}
    assert p["render"][0] == "then"
    assert not any(k.startswith("once") for k in p["render"])
