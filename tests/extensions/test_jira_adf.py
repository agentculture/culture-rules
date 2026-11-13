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


def _item(*blocks):
    return {"type": "listItem", "content": list(blocks)}


def test_flatten_ordered_list_start():
    d = doc(
        {
            "type": "orderedList",
            "attrs": {"order": 3},
            "content": [_item(para(txt("a"))), _item(para(txt("b")))],
        }
    )
    assert flatten(d) == "3. a\n4. b"


def test_flatten_bad_order_falls_back_to_one():
    for bad in ("a", {"x": 1}, -2, 0, True, 1.5, None, [3]):
        d = doc(
            {"type": "orderedList", "attrs": {"order": bad}, "content": [_item(para(txt("a")))]}
        )
        assert flatten(d) == "1. a"


def test_flatten_blockquote_and_panel():
    for kind in ("blockquote", "panel"):
        d = doc({"type": kind, "content": [para(txt("one")), para(txt("two"))]})
        assert flatten(d) == "one\ntwo"


def test_flatten_nested_list_does_not_raise():
    inner = {"type": "bulletList", "content": [_item(para(txt("in")))]}
    d = doc({"type": "bulletList", "content": [_item(para(txt("out")), inner)]})
    assert flatten(d) == "- out\n- in"


def test_to_adf_matches_client_add_comment_literal():
    body = "x"
    expected = {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": body}]}],
    }
    assert to_adf(body) == expected


def test_flatten_non_dict_attrs_never_raise():
    for attrs in ([], ["x"], "text", 5, True):
        d = doc(para(txt("hi "), {"type": "mention", "attrs": attrs}, txt("!")))
        assert flatten(d) == "hi !"
        d = doc({"type": "orderedList", "attrs": attrs, "content": [_item(para(txt("a")))]})
        assert flatten(d) == "1. a"


def test_flatten_non_dict_list_items_are_skipped():
    d = doc(
        {
            "type": "bulletList",
            "content": [None, "junk", 7, ["x"], _item(para(txt("a"))), {"type": "listItem"}],
        }
    )
    assert flatten(d) == "- a\n- "


def test_flatten_list_item_with_non_list_content():
    d = doc({"type": "bulletList", "content": [{"type": "listItem", "content": "oops"}]})
    assert flatten(d) == "- "


def test_to_adf_ordered_list_keeps_its_start():
    d = to_adf("3. a\n4. b")
    (ol,) = d["content"]
    assert ol["type"] == "orderedList"
    assert ol["attrs"] == {"order": 3}
    assert flatten(d) == "3. a\n4. b"


def test_to_adf_ordered_list_from_one_has_no_attrs():
    d = to_adf("1. a\n2. b")
    assert "attrs" not in d["content"][0]


def test_to_adf_blank_only_body_never_sends_empty_content():
    for body in ("\n", "\n\n", "  \n \t\n"):
        assert to_adf(body)["content"] != []


def test_to_adf_huge_ordered_start_never_raises():
    d = to_adf("9" * 5000 + ". a\nb")
    assert d["content"][0]["type"] == "orderedList"
    assert "attrs" not in d["content"][0]
