"""The built-in ``text.replace_refs``: numbered references swapped for a configured template."""

from __future__ import annotations

from datetime import UTC, datetime

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.text_refs import TEXT_REPLACE_REFS_BUILTIN, TextReplaceRefsPort

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
PATTERN = r"\[(\d+)\]"
TEMPLATE = "[{n}]({url})"
ITEMS = [{"url": "https://a.example/x"}, {"url": "https://b.example/y"}]


def invoke(inputs, config=None):
    cfg = {"builtin": TEXT_REPLACE_REFS_BUILTIN, "pattern": PATTERN, "template": TEMPLATE}
    cfg.update(config or {})
    ctx = InvocationContext("run-1", "wf[0]/refs", "code", "spark2", config=cfg)
    return TextReplaceRefsPort().invoke(inputs, "k", DEADLINE, context=ctx)


def failed(res, code="bad_params"):
    assert res.outcome == "failed"
    assert res.error.startswith(f"{code}:"), res.error
    assert res.retryable is False


def test_in_range_reference_becomes_the_template():
    res = invoke({"text": "See [2] now", "items": ITEMS})
    assert res.output["text"] == "See [2](https://b.example/y) now"


def test_every_occurrence_is_replaced_and_counted():
    res = invoke({"text": "[1] and [1] and [2]", "items": ITEMS})
    assert res.output["replaced"] == 3


def test_out_of_range_reference_is_left_unchanged():
    res = invoke({"text": "a [3] b [0]", "items": ITEMS})
    assert res.output["text"] == "a [3] b [0]"


def test_out_of_range_numbers_are_listed_sorted_and_unique():
    res = invoke({"text": "[9] [3] [9] [0] [1]", "items": ITEMS})
    assert res.output["missing_refs"] == [0, 3, 9]


def test_no_missing_refs_is_an_empty_list():
    res = invoke({"text": "[1]", "items": ITEMS})
    assert res.output["missing_refs"] == []


def test_item_field_missing_leaves_the_reference():
    res = invoke({"text": "[1] [2]", "items": [{"url": "u"}, {"other": "v"}]})
    assert res.output["text"] == "[1](u) [2]"


def test_item_field_missing_is_listed():
    res = invoke({"text": "[1] [2]", "items": [{"url": "u"}, {"other": "v"}]})
    assert res.output["missing_refs"] == [2]


def test_non_scalar_field_counts_as_missing():
    res = invoke({"text": "[1]", "items": [{"url": ["a"]}]})
    assert res.output["missing_refs"] == [1]


def test_substituted_value_is_not_rescanned():
    res = invoke({"text": "[1]", "items": [{"url": "[2]"}, {"url": "z"}]})
    assert res.output["text"] == "[1]([2])"


def test_only_named_placeholders_are_substituted():
    res = invoke({"text": "[1]", "items": ITEMS}, {"template": "{{n}} {n} {url}"})
    assert res.output["text"] == "{1} 1 https://a.example/x"


def test_input_wins_over_config():
    res = invoke({"text": "<1>", "items": ITEMS, "pattern": "<(\\d+)>"}, {"template": "{n}"})
    assert res.output["text"] == "1"


def test_config_supplies_text_and_items():
    res = invoke({}, {"text": "[1]", "items": ITEMS})
    assert res.output["replaced"] == 1


def test_missing_pattern_is_bad_params():
    failed(invoke({"text": "x", "items": ITEMS}, {"pattern": None}))


def test_missing_template_is_bad_params():
    failed(invoke({"text": "x", "items": ITEMS}, {"template": None}))


def test_uncompilable_pattern_is_bad_params():
    failed(invoke({"text": "x", "items": ITEMS}, {"pattern": "("}))


def test_pattern_without_one_group_is_bad_params():
    failed(invoke({"text": "x", "items": ITEMS}, {"pattern": r"\[\d+\]"}))


def test_backtracking_pattern_is_bad_params():
    failed(invoke({"text": "x", "items": ITEMS}, {"pattern": r"(\d+)+x"}))


def test_non_string_text_is_bad_params():
    failed(invoke({"text": 5, "items": ITEMS}))


def test_non_list_items_is_bad_params():
    failed(invoke({"text": "x", "items": "nope"}))


def test_non_object_item_is_bad_params():
    failed(invoke({"text": "x", "items": ["a"]}))


def test_non_numeric_group_is_left_alone():
    res = invoke({"text": "[a]", "items": ITEMS}, {"pattern": r"\[(\w)\]"})
    assert res.output["text"] == "[a]"
