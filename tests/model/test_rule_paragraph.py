"""The rule sentences around the step paragraph (#69 t7, c37, h35).

Each rule that starts a workflow reads as one built sentence before the step paragraph, and
the Then side (continues into, ends here, on failure, runs) as built sentences after it. Every
word comes from :data:`PHRASES` and describe.py's vocabularies; no rule name or description
is used. Which rules start a workflow, and which continue into another, mirrors the editor's
fold (web/src/fold/model.ts): tests/fixtures/fold_docs_rules.json holds the TS fold of the
committed docs/rules corpus, and web/src/fold/parity.test.ts checks the TS side against it.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from culture_rules.model.paragraph import (
    PHRASES,
    SENTENCE_KEYS,
    fold_rules,
    mark_disabled,
    onward_of,
    rule_sentence,
    step_paragraph,
    then_sentence,
    workflow_paragraph,
)
from culture_rules.model.rule import Rule

ROOT = Path(__file__).resolve().parents[2]
RULES_DOCS = ROOT / "docs" / "rules"
DESCRIPTIONS = ROOT / "tests" / "fixtures" / "workflow_descriptions.json"
FOLD_FIXTURE = ROOT / "tests" / "fixtures" / "fold_docs_rules.json"
SENTINEL = "ZZSENTINEL"


def _cmp(field: str, value: object, cmp: str = "==") -> dict:
    return {"op": "compare", "cmp": cmp, "left": {"field": field}, "right": {"literal": value}}


def _rule(rid: str = "r", **kw: object) -> dict:
    out: dict = {
        "id": rid,
        "name": f"{SENTINEL} name",
        "description": f"{SENTINEL} description",
        "trigger": {"kind": "event", "params": {"type": "github.pr.opened"}},
        "workflow": {"id": "w"},
        "action": {"kind": "noop", "name": f"{SENTINEL} action", "params": {}},
    }
    out.update(kw)
    return out


def _run_rule(rid: str, source: str, outcome: str = "succeeded", target: str = "w2", **kw):
    return _rule(
        rid,
        trigger={"kind": "event", "params": {"type": f"rules.run.{outcome}"}},
        condition=_cmp("data.workflow_id", source),
        workflow={"id": target},
        **kw,
    )


def _committed_rules() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(RULES_DOCS.glob("*/rules/*.json"))]


def _committed_workflow(wid: str) -> dict:
    written = json.loads(DESCRIPTIONS.read_text())[wid]
    for path in RULES_DOCS.glob("*/workflows/*.json"):
        wf = json.loads(path.read_text())
        if wf["id"] != wid:
            continue

        def fill(steps: list[dict]) -> None:
            for s in steps:
                s["description"] = written["steps"].get(s["id"], "")
                fill(s.get("body") or [])

        fill(wf["steps"])
        wf["description"] = written["description"]
        return wf
    raise AssertionError(wid)


def _template_regex(template: str, first: bool) -> str:
    body = template[:-1]
    if not first:
        body = body[0].lower() + body[1:]
    return ".*".join(re.escape(p) for p in re.split(r"\{[a-z_]+\}", body))


def _assert_from_table(seg: dict) -> None:
    """A rule or Then segment's text is exactly its PHRASES keys, composed."""
    assert seg["keys"] and all(k in SENTENCE_KEYS for k in seg["keys"]), seg
    pattern = ", ".join(_template_regex(PHRASES[k], i == 0) for i, k in enumerate(seg["keys"]))
    assert re.fullmatch(pattern + r"\.", seg["text"]), seg


# --------------------------------------------------------------------------- the table


def test_sentence_entries_are_table_values_ending_with_a_period():
    assert SENTENCE_KEYS <= set(PHRASES)
    for key in SENTENCE_KEYS:
        assert PHRASES[key].endswith("."), key
    # d11: the step paragraph's lead-ins, connectors and trailing clauses carry no colon
    joining = set(PHRASES) - SENTENCE_KEYS
    assert joining and not any(PHRASES[k].endswith((":", ".")) for k in joining)


# --------------------------------------------------------------------------- the fold


def test_fold_entry_and_continuation():
    rules = [
        _rule("a", workflow={"id": "w1"}),
        _run_rule("b", "w1", target="w2"),
        _rule("c", workflow=None),  # no workflow: a D7 candidate, folded nowhere
    ]
    fold = fold_rules(rules)
    assert [(e["rule"]["id"], e["workflow_id"], e["kind"]) for e in fold] == [
        ("a", "w1", "entry"),
        ("b", "w2", "continuation"),
    ]
    assert fold[1]["predecessor"] == {"kind": "linked", "workflow_id": "w1"}
    assert fold[1]["from_workflow_id"] == "w1"


def test_fold_predecessor_kinds_mirror_predecessor_terms():
    any_ = _rule("x", trigger={"kind": "event", "params": {"type": "rules.run.failed"}})
    both = _rule(
        "y",
        trigger={"kind": "event", "params": {"type": "rules.run.succeeded"}},
        condition={
            "op": "and",
            "args": [
                _cmp("data.workflow_id", "p"),
                {"op": "and", "args": [_cmp("data.workflow_id", "q")]},
            ],
        },
    )
    # OR does not require a predecessor; the literal may sit on the left; != is not one
    or_ = _rule(
        "z",
        trigger={"kind": "event", "params": {"type": "rules.run.succeeded"}},
        condition={"op": "or", "args": [_cmp("data.workflow_id", "p")]},
    )
    left = _rule(
        "l",
        trigger={"kind": "event", "params": {"type": "rules.run.succeeded"}},
        condition={
            "op": "compare",
            "cmp": "==",
            "left": {"literal": "p"},
            "right": {"field": "data.workflow_id"},
        },
    )
    ne = _rule(
        "n",
        trigger={"kind": "event", "params": {"type": "rules.run.succeeded"}},
        condition=_cmp("data.workflow_id", "p", "!="),
    )
    kinds = {e["rule"]["id"]: e["predecessor"] for e in fold_rules([any_, both, or_, left, ne])}
    assert kinds == {
        "x": {"kind": "any"},
        "y": {"kind": "ambiguous", "workflow_ids": ["p", "q"]},
        "z": {"kind": "any"},
        "l": {"kind": "linked", "workflow_id": "p"},
        "n": {"kind": "any"},
    }


def test_python_fold_matches_the_ts_fold_over_the_committed_corpus():
    """Parity with web/src/fold/model.ts: the fixture is the TS foldModel over docs/rules
    (web/src/fold/parity.test.ts asserts the TS side produces exactly this)."""
    expected = json.loads(FOLD_FIXTURE.read_text())
    got = [
        {
            "rule": e["rule"]["id"],
            "workflow": e["workflow_id"],
            "kind": e["kind"],
            "enabled": e["enabled"],
            "predecessor": e.get("predecessor"),
            "from": e.get("from_workflow_id"),
        }
        for e in fold_rules(_committed_rules())
    ]
    assert got == expected["entries"]


def test_the_committed_entry_and_continuation_sets_by_hand():
    fold = fold_rules(_committed_rules())

    def owned(wid: str, kind: str) -> set[str]:
        return {e["rule"]["id"] for e in fold if e["workflow_id"] == wid and e["kind"] == kind}

    assert owned("pr-fix", "entry") == {"pr-fixer-dispatch"}
    assert owned("pr-fix", "continuation") == set()
    assert owned("review-commit", "continuation") == {"pr-fixer-review-commit"}
    assert owned("publish-fix", "continuation") == {"pr-fixer-publish"}
    assert owned("daily-newspaper", "entry") == {"daily-newspaper"}
    assert {e["rule"]["id"] for e in onward_of(fold, "pr-fix")} == {
        "pr-fixer-queue-progress-cancelled",
        "pr-fixer-queue-progress-failed",
        "pr-fixer-queue-progress-fixed",
        "pr-fixer-queue-progress-superseded",
        "pr-fixer-retry-capacity",
        "pr-fixer-retry-failed",
        "pr-fixer-retry",
        "pr-fixer-review-commit",
    }
    assert onward_of(fold, "daily-newspaper") == []


def test_onward_includes_any_and_ambiguous_but_not_the_workflow_itself():
    rules = [
        _run_rule("linked", "w1", target="w2"),
        _rule("any", trigger={"kind": "event", "params": {"type": "rules.run.failed"}}),
        _rule(
            "amb",
            trigger={"kind": "event", "params": {"type": "rules.run.succeeded"}},
            condition={
                "op": "and",
                "args": [_cmp("data.workflow_id", "w1"), _cmp("data.workflow_id", "w9")],
            },
            workflow={"id": "w3"},
        ),
        _run_rule("self", "w1", target="w1"),
        _run_rule("other", "w8", target="w4"),
    ]
    assert [e["rule"]["id"] for e in onward_of(fold_rules(rules), "w1")] == [
        "linked",
        "any",
        "amb",
    ]


# --------------------------------------------------------------------------- rule sentences


@pytest.mark.parametrize(
    "trigger, text",
    [
        (
            {"kind": "event", "params": {"type": "github.pr.opened"}},
            "It starts when github.pr.opened arrives.",
        ),
        ({"kind": "event", "params": {}}, "It starts when an event arrives."),
        (
            {"kind": "schedule", "params": {"cron": "0 5 * * *", "tz": "Asia/Jerusalem"}},
            "It starts on cron 0 5 * * * (Asia/Jerusalem).",
        ),
        ({"kind": "manual", "params": {}}, "It is started by hand."),
        (
            {"kind": "probe", "params": {"command": "ls", "schedule": "*/5 * * * *"}},
            "It starts on probe `ls` on cron */5 * * * *, on a change.",
        ),
        # an unknown trigger kind falls back to its raw name
        ({"kind": "webhook.thing", "params": {}}, "It starts on webhook.thing."),
    ],
)
def test_rule_sentence_per_trigger_kind(trigger, text):
    seg = rule_sentence(_rule(trigger=trigger))
    assert seg["text"] == text
    _assert_from_table(seg)


def test_rule_sentence_condition_placement_and_disabled():
    rule = _rule(
        condition=_cmp("data.draft", False),
        placement={"machine": "spark2"},
        enabled=False,
    )
    seg = rule_sentence(rule)
    assert seg["text"] == ("It starts when github.pr.opened arrives, if draft is false, on spark2.")
    assert seg["disabled"] is True and "disabled" not in rule_sentence(_rule())
    assert seg["rule"] == "r" and seg["enabled"] is False and seg["kind"] == "entry"
    _assert_from_table(seg)
    # d13: the CLI's mark, after the words and before the period
    assert mark_disabled(seg["text"]) == (
        "It starts when github.pr.opened arrives, if draft is false, on spark2 (disabled)."
    )


def test_rule_sentence_reads_the_empty_string_as_empty():
    """d13: ``empty``, never ``""``, in a rule's condition words."""
    rule = _rule(condition=_cmp("data.fixer", ""))
    assert (
        rule_sentence(rule)["text"] == "It starts when github.pr.opened arrives, if fixer is empty."
    )


@pytest.mark.parametrize(
    "placement, where",
    [
        ({"actor": "qwen"}, "on qwen's machine"),
        ({"requirement": ["gpu", "cuda"]}, "on a machine with gpu, cuda"),
    ],
)
def test_rule_sentence_placement_uses_d19_words(placement, where):
    assert rule_sentence(_rule(placement=placement))["text"].endswith(f", {where}.")


def test_rule_sentence_long_condition_reads_its_condition_holds():
    long = {"op": "and", "args": [_cmp(f"data.field_{i}", "x" * 10) for i in range(8)]}
    seg = rule_sentence(_rule(condition=long))
    assert seg["text"] == "It starts when github.pr.opened arrives, if its condition holds."
    _assert_from_table(seg)


def test_rule_sentence_relationships():
    rule = _rule(
        must_after=["a", "b"],
        may_after=["c"],
        supersedes=["d"],
        exclusive_group="g",
        priority=2,
    )
    seg = rule_sentence(rule)
    assert seg["text"] == (
        "It starts when github.pr.opened arrives, only after a and b succeeded,"
        " reading the outputs of c if it ran, instead of d for the same event,"
        " only if no higher-priority rule of group g matches (priority 2)."
    )
    _assert_from_table(seg)


@pytest.mark.parametrize(
    "outcome, words",
    [("succeeded", "succeeds"), ("failed", "fails"), ("cancelled", "ends (cancelled)")],
)
def test_continuation_reads_its_predecessor_and_drops_its_term(outcome, words):
    rule = _run_rule("k", "pr-fix", outcome)
    rule["condition"] = {
        "op": "and",
        "args": [
            _cmp("data.workflow_id", "pr-fix"),
            {
                "op": "in",
                "value": {"field": "data.outputs.verdict"},
                "items": {"literal": ["pass"]},
            },
        ],
    }
    seg = rule_sentence(rule)
    assert seg["kind"] == "continuation"
    assert seg["text"] == f"It starts when pr-fix {words}, if verdict is one of pass."
    _assert_from_table(seg)
    # a predecessor term alone leaves no condition to say
    assert rule_sentence(_run_rule("k", "pr-fix", outcome))["text"] == (
        f"It starts when pr-fix {words}."
    )


def test_continuation_from_any_workflow_reads_as_its_event():
    rule = _rule(trigger={"kind": "event", "params": {"type": "rules.run.failed"}})
    assert rule_sentence(rule)["text"] == "It starts when rules.run.failed arrives."


def test_rule_sentence_accepts_a_rule_model():
    rule = _rule(condition=_cmp("data.draft", False), placement={"machine": "spark2"})
    model = Rule.from_dict(rule)
    assert rule_sentence(model)["text"] == rule_sentence(rule)["text"]


@pytest.mark.parametrize(
    "junk",
    [{}, {"id": "x"}, {"id": "x", "trigger": "nope", "condition": 3, "placement": "s"}, None, 7],
)
def test_rule_sentence_never_raises(junk):
    assert rule_sentence(junk)["text"].endswith(".")


# --------------------------------------------------------------------------- then sentences


def _then(rules, onward=()):
    return then_sentence(rules, onward)


def test_then_noop_and_nothing_continues():
    segs = _then([_rule()])
    assert [s["text"] for s in segs] == [
        "Nothing continues from here.",
        "When it is done, nothing else happens.",
    ]
    for seg in segs:
        _assert_from_table(seg)


def test_then_continues_into_each_workflow_once_in_order():
    fold = fold_rules(
        [
            _run_rule("a", "w", "succeeded", "w2"),
            _run_rule("b", "w", "failed", "w3"),
            _run_rule("c", "w", "failed", "w2"),
        ]
    )
    seg = _then([_rule()], onward_of(fold, "w"))[0]
    assert seg["text"] == "Then it continues into w2 or w3."
    assert seg["rules"] == ["a", "b", "c"]
    _assert_from_table(seg)


def test_then_action_on_failure_and_runs():
    comment = {
        "kind": "github.comment",
        "name": f"{SENTINEL} comment",
        "only_at_chain_end": True,
        "params": {"actor": "github-app", "status": True, "body": f"{SENTINEL} body"},
    }
    rule = _rule(
        action=comment,
        on_failure=comment,
        concurrency_key="pr:{trigger.data.repository}#{trigger.data.number}",
        max_attempts=3,
    )
    segs = _then([rule])
    assert [s["text"] for s in segs] == [
        "Nothing continues from here.",
        "Where the chain ends here, it updates the chain's status comment on GitHub"
        " (as github-app).",
        "If it fails, it updates that comment with the failure.",
        "It runs one at a time per pr:{repository}#{number}, with at most 3 attempts.",
    ]
    for seg in segs:
        _assert_from_table(seg)


def _comment(**kw: object) -> dict:
    params = {"actor": "github-app", "body": f"{SENTINEL} body", **kw.pop("params", {})}
    return {"kind": "github.comment", "name": f"{SENTINEL} comment", "params": params, **kw}


@pytest.mark.parametrize(
    "action, on_failure, texts",
    [
        # a status comment written whenever it is done, and a plain failure action after it
        (
            _comment(params={"status": True}),
            {"kind": "message", "params": {"channel": "#ops"}},
            [
                "When it is done, it updates the chain's status comment on GitHub"
                " (as github-app).",
                "If it fails, it runs message to #ops.",
            ],
        ),
        # a status comment only on failure: it names the comment in full
        (
            {"kind": "noop"},
            _comment(params={"status": True, "once_key": "k"}, only_at_chain_end=True),
            [
                "When it is done, nothing else happens.",
                "If it fails, it updates the chain's status comment on GitHub with the failure"
                " (as github-app, once per once_key).",
            ],
        ),
        # an ordinary comment at the chain's end: the lead-in says so, the brackets merge
        (
            _comment(params={"once_key": "k"}, only_at_chain_end=True),
            _comment(params={"once_key": "k"}, only_at_chain_end=True),
            [
                "Where the chain ends here, it runs github.comment as github-app"
                " (once per once_key).",
                "If it fails, it runs github.comment as github-app"
                " (once per once_key, only where its chain ends).",
            ],
        ),
        # a status comment with no actor reads with no brackets
        (
            {"kind": "github.comment", "params": {"status": True}},
            None,
            ["When it is done, it updates the chain's status comment on GitHub."],
        ),
    ],
)
def test_then_status_comments_and_chain_ends(action, on_failure, texts):
    segs = _then([_rule(action=action, on_failure=on_failure)])
    assert [s["text"] for s in segs[1:]] == texts
    assert not any(") (" in s["text"] for s in segs)  # no stacked brackets
    for seg in segs:
        _assert_from_table(seg)


def test_then_values_that_differ_per_rule():
    a = _rule("a", on_failure={"kind": "noop"}, concurrency_key="k1")
    b = _rule(
        "b",
        action={"kind": "frobnicate", "params": {}},  # an unknown action kind: its raw name
        concurrency_key="k2",
    )
    segs = _then([a, b])
    assert [s["text"] for s in segs] == [
        "Nothing continues from here.",
        "When it is done, it runs noop or frobnicate, depending on the rule that started it.",
        "If it fails, it runs noop, depending on the rule that started it.",
        "How it runs differs per rule that starts it.",
    ]
    for seg in segs:
        _assert_from_table(seg)


def test_then_with_no_rules_says_only_where_it_continues():
    assert [s["text"] for s in _then([])] == ["Nothing continues from here."]


# --------------------------------------------------------------------------- the paragraph


def test_no_rule_name_description_or_action_name_ever_shows():
    rules = [
        _rule("a", condition=_cmp("data.draft", False), placement={"machine": "m"}),
        _run_rule("b", "w", target="w2"),
        _run_rule("c", "w0", target="w"),
    ]
    wf = {
        "id": "w",
        "name": "W",
        "steps": [{"id": "s", "kind": "code", "description": "Does it."}],
        "edges": [],
    }
    out = workflow_paragraph(wf, rules)
    assert SENTINEL not in out["text"]
    assert SENTINEL not in json.dumps(out)


def test_workflow_paragraph_orders_starts_steps_then():
    wf = {
        "id": "w",
        "name": "W",
        "steps": [{"id": "s", "kind": "code", "description": "Does it."}],
        "edges": [],
    }
    rules = [_rule("a"), _run_rule("c", "w0", target="w"), _run_rule("b", "w", target="w2")]
    out = workflow_paragraph(wf, rules)
    assert [s["part"] for s in out["segments"]] == ["start", "start", "step", "then", "then"]
    assert out["text"] == (
        "It starts when github.pr.opened arrives.\nIt starts when w0 succeeds."
        "\nFirst does it.\nThen it continues into w2.\nWhen it is done, nothing else happens."
    )
    assert [s["sentence"] for s in out["segments"]] == [0, 1, 2, 3, 4]
    assert step_paragraph(wf)["text"] in out["text"]


def test_workflow_paragraph_without_rules():
    wf = {"id": "w", "steps": [{"id": "s", "kind": "code", "description": "Does it."}]}
    out = workflow_paragraph(wf, [])
    assert out["text"] == "No rule starts it yet.\nFirst does it.\nNothing continues from here."


def test_pr_fix_paragraph_opens_with_its_starting_rule_and_ends_with_then():
    rules = _committed_rules()
    wf = _committed_workflow("pr-fix")
    out = workflow_paragraph(wf, rules)
    starts = [s for s in out["segments"] if s["part"] == "start"]
    assert [s["rule"] for s in starts] == ["pr-fixer-dispatch"]
    assert out["text"].startswith(
        "It starts when rules.queue.dispatch arrives, if its condition holds, on spark2."
        "\nFirst waits for the pull request to settle, waiting 5 minutes"
    )
    steps = step_paragraph(wf)["text"]
    head, sep, tail = out["text"].partition(steps)
    assert sep and head.endswith(".\n")
    assert tail.split("\n") == [
        "",
        "Then it continues into queue-progress, queue-add-capacity, queue-add or review-commit.",
        "Where the chain ends here, it updates the chain's status comment on GitHub"
        " (as github-app).",
        "If it fails, it updates that comment with the failure.",
        "It runs one at a time per pr-fixer:{repository}#{number}, with at most 3 attempts.",
    ]
    for seg in out["segments"]:
        if seg["part"] != "step":
            _assert_from_table(seg)
    for rule in rules:  # no rule's name or description text
        assert rule["name"] not in out["text"]
        assert rule["description"] not in out["text"]
        for action in (rule.get("action"), rule.get("on_failure")):
            if action and action.get("name"):
                assert action["name"] not in out["text"]


def test_daily_newspaper_paragraph():
    out = workflow_paragraph(_committed_workflow("daily-newspaper"), _committed_rules())
    assert out["text"].startswith(
        "It starts on cron 0 5 * * * (Asia/Jerusalem), on spark.\nFirst reads "
    )
    assert out["text"].endswith(
        "\nNothing continues from here.\nWhen it is done, nothing else happens."
    )


def test_workflow_paragraph_does_not_mutate_its_inputs():
    rules = _committed_rules()
    wf = _committed_workflow("pr-fix")
    before = copy.deepcopy((rules, wf))
    workflow_paragraph(wf, rules)
    assert (rules, wf) == before
