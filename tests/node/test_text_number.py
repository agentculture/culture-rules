"""The built-in ``text.number``: a numbered text from a list and an item template."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.text_steps import TEXT_NUMBER_BUILTIN, TextNumberPort

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
ITEMS = [{"title": "A", "url": "u1"}, {"title": "B", "url": "u2"}, {"title": "C", "url": "u3"}]
TEMPLATE = "[{n}] {title} - {url}"


def invoke(inputs, config=None):
    cfg = {
        "builtin": TEXT_NUMBER_BUILTIN,
        "template": TEMPLATE,
        "separator": "\n",
        **(config or {}),
    }
    ctx = InvocationContext("run-1", "wf[0]/num", "code", "spark2", config=cfg)
    return TextNumberPort().invoke(inputs, "k", DEADLINE, context=ctx)


def failed(res):
    assert res.outcome == "failed"
    assert res.error.startswith("bad_params:"), res.error
    assert res.retryable is False


def test_items_are_numbered_from_one_in_order():
    assert invoke({"items": ITEMS}).output["text"] == "[1] A - u1\n[2] B - u2\n[3] C - u3"


def test_count_is_the_number_of_items():
    assert invoke({"items": ITEMS}).output["count"] == 3


def test_same_input_gives_the_same_text():
    assert invoke({"items": ITEMS}).output == invoke({"items": ITEMS}).output


def test_separator_comes_from_config():
    res = invoke({"items": ITEMS[:2]}, {"separator": " | "})
    assert res.output["text"] == "[1] A - u1 | [2] B - u2"


def test_input_template_wins_over_config():
    res = invoke({"items": ITEMS[:1], "template": "{n}:{url}"})
    assert res.output["text"] == "1:u1"


def test_empty_list_gives_empty_text():
    res = invoke({"items": []})
    assert (res.output["text"], res.output["count"]) == ("", 0)


def test_doubled_braces_are_literal():
    res = invoke({"items": ITEMS[:1]}, {"template": "{{{n}}}"})
    assert res.output["text"] == "{1}"


def test_number_and_bool_values_are_text():
    res = invoke({"items": [{"title": 7, "url": True}]})
    assert res.output["text"] == "[1] 7 - True"


def test_non_list_is_refused():
    failed(invoke({"items": "abc"}))


def test_missing_items_is_refused():
    failed(invoke({}))


def test_non_object_item_is_refused():
    failed(invoke({"items": ["x"]}))


def test_missing_field_is_refused():
    failed(invoke({"items": [{"title": "A"}]}))


def test_non_scalar_field_is_refused():
    failed(invoke({"items": [{"title": ["x"], "url": "u"}]}))


@pytest.mark.parametrize("template", ["{title.x}", "{title[0]}", "{0}", "{title!r}", "{}", "a{"])
def test_unsafe_placeholder_is_refused(template):
    failed(invoke({"items": ITEMS}, {"template": template}))


def test_missing_template_is_refused():
    failed(invoke({"items": ITEMS}, {"template": None}))


def test_missing_separator_is_refused():
    failed(invoke({"items": ITEMS}, {"separator": None}))


def test_item_field_named_n_never_shadows_the_position():
    res = invoke({"items": [{"n": "x", "title": "A", "url": "u"}]})
    assert res.output["text"].startswith("[1]")
