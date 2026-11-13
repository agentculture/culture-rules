"""ADF helpers: flatten ADF to text, and plain text to ADF."""

from culture_rules.extensions.jira.adf import flatten, to_adf


def doc(*content):
    return {"type": "doc", "version": 1, "content": list(content)}


def para(*inline):
    return {"type": "paragraph", "content": list(inline)}


def txt(s):
    return {"type": "text", "text": s}


def test_flatten_paragraphs_and_hard_breaks():
    d = doc(para(txt("a"), {"type": "hardBreak"}, txt("b")), para(txt("c")))
    assert flatten(d) == "a\nb\nc"


def test_flatten_lists():
    item = lambda s: {"type": "listItem", "content": [para(txt(s))]}  # noqa: E731
    d = doc(
        {"type": "bulletList", "content": [item("x"), item("y")]},
        {"type": "orderedList", "content": [item("p"), item("q")]},
    )
    assert flatten(d) == "- x\n- y\n1. p\n2. q"


def test_flatten_mention_and_code_block():
    d = doc(
        para(txt("hi "), {"type": "mention", "attrs": {"id": "1", "text": "@ori"}}),
        {"type": "codeBlock", "content": [txt("l1\nl2")]},
    )
    assert flatten(d) == "hi @ori\nl1\nl2"


def test_flatten_unknown_nodes_keep_text():
    d = doc(
        {"type": "panel", "content": [para(txt("inside"))]},
        {"type": "weird", "content": [txt("t")]},
    )
    assert flatten(d) == "inside\nt"


def test_flatten_non_adf_string_passes_through():
    assert flatten("just text\nhere") == "just text\nhere"


def test_to_adf_single_line_is_legacy_document():
    assert to_adf("a") == {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "a"}]}],
    }


def test_to_adf_n_lines_n_paragraphs():
    d = to_adf("a\nb\nc")
    assert [c["type"] for c in d["content"]] == ["paragraph"] * 3
    assert d["content"][2]["content"][0]["text"] == "c"


def test_to_adf_groups_lists():
    d = to_adf("intro\n1. one\n2. two\n- x\n- y")
    assert [c["type"] for c in d["content"]] == ["paragraph", "orderedList", "bulletList"]
    assert len(d["content"][1]["content"]) == 2
    assert d["content"][2]["content"][1]["content"][0]["content"][0]["text"] == "y"


def test_round_trip():
    text = "intro\n1. one\n2. two\n- x"
    assert flatten(to_adf(text)) == text
