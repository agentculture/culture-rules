"""Describe phrases for the condition ops ``contains``, ``host_suffix`` and ``any``/``all``
(R2a, t12's ops): symbols in ``condition_text``, words in ``condition_words`` and the
paragraph builder, never the raw op name."""

from __future__ import annotations

import pytest

from culture_rules.model.describe import condition_text, condition_words, step_text
from culture_rules.model.paragraph import step_paragraph

TITLE_HAS_BUG = {
    "op": "contains",
    "value": {"field": "trigger.title"},
    "needle": {"literal": "bug"},
}
HOST_IN_DOMAIN = {
    "op": "host_suffix",
    "value": {"item": "host"},
    "suffix": {"literal": "example.com"},
}
ANY_LINK = {"op": "any", "items": {"field": "trigger.links"}, "where": HOST_IN_DOMAIN}
ALL_TAGS = {
    "op": "all",
    "items": {"var": "tags"},
    "where": {"op": "contains", "value": {"item": "."}, "needle": {"var": "word"}},
}


@pytest.mark.parametrize(
    ("tree", "text"),
    [
        (TITLE_HAS_BUG, "title contains bug"),
        (HOST_IN_DOMAIN, "item.host in domain example.com"),
        (ANY_LINK, "any of links: item.host in domain example.com"),
        (ALL_TAGS, "all of vars.tags: item contains vars.word"),
        ({"op": "exists", "arg": {"item": "a.url"}}, "item.url exists"),
    ],
    ids=["contains", "host-suffix", "any", "all", "item-operand"],
)
def test_condition_text_says_the_new_ops_in_symbols(tree, text):
    assert condition_text(tree) == text


@pytest.mark.parametrize(
    ("tree", "words"),
    [
        (TITLE_HAS_BUG, "title contains bug"),
        (HOST_IN_DOMAIN, "its host is in the domain example.com"),
        (ANY_LINK, "for at least one of links, its host is in the domain example.com"),
        (ALL_TAGS, "for every one of vars.tags, it contains vars.word"),
        ({"op": "not", "arg": {"op": "exists", "arg": {"item": "url"}}}, "its url is not set"),
        (
            {"op": "in", "value": {"item": "kind"}, "items": {"literal": ["a", "b"]}},
            "its kind is one of a, b",
        ),
        (
            {"op": "compare", "cmp": "==", "left": {"item": "n"}, "right": {"literal": 2}},
            "its n is 2",
        ),
    ],
    ids=["contains", "host-suffix", "any", "all", "not-exists", "in", "compare"],
)
def test_condition_words_says_the_new_ops_in_words(tree, words):
    assert condition_words(tree) == words


def test_a_nested_any_is_parenthesised_in_a_group():
    tree = {"op": "and", "args": [TITLE_HAS_BUG, ANY_LINK]}
    assert condition_text(tree) == (
        "title contains bug and (any of links: item.host in domain example.com)"
    )


def test_a_nested_all_is_parenthesised_in_words():
    tree = {"op": "or", "args": [TITLE_HAS_BUG, ALL_TAGS]}
    assert condition_words(tree) == (
        "title contains bug or (for every one of vars.tags, it contains vars.word)"
    )


def test_an_any_over_an_items_field_reads_its_field():
    inner = {"op": "any", "items": {"item": "links"}, "where": HOST_IN_DOMAIN}
    assert condition_words(inner).startswith("for at least one of its links, ")


@pytest.mark.parametrize("op", ["contains", "host_suffix", "any", "all"])
def test_no_new_op_reads_as_its_raw_name(op):
    tree = {"op": op}
    assert condition_words(tree) != op


def test_a_step_when_with_a_new_op_reads_in_the_paragraph():
    wf = {
        "id": "w",
        "name": "W",
        "steps": [
            {"id": "a", "kind": "code", "description": "a runs."},
            {
                "id": "b",
                "kind": "code",
                "description": "b runs.",
                "config": {"when": TITLE_HAS_BUG},
            },
        ],
        "edges": [{"source": "a", "source_port": "out", "target": "b", "target_port": "in"}],
    }
    assert step_paragraph(wf)["text"] == "First a runs.\nThen, if title contains bug, b runs."


def test_a_retry_until_with_a_new_op_reads_in_symbols():
    step = {"id": "s", "kind": "retry_until", "max_iterations": 3, "config": {"until": ANY_LINK}}
    assert "until any of links: item.host in domain example.com" in step_text(step)
