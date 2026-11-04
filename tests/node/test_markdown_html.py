"""markdown.from_html: HTML as Markdown chunks, checked on a real newspaper.render issue."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.markdown_html import (
    FROM_HTML_BUILTIN,
    MarkdownFromHtmlPort,
    html_to_markdown,
)
from culture_rules.node.actions.newspaper import NewspaperRenderPort

DEADLINE = datetime(2026, 10, 11, 23, tzinfo=UTC)
DATE = "2026-10-11"
URL_A = "https://a.example/story"
URL_B = "https://b.example/news?id=1&x=2"


def ctx():
    return InvocationContext(run_id="r1", step_id="digest", kind="code", host="spark")


def hit(url, title="Story"):
    return {"title": title, "url": url, "snippet": "A snippet", "age": "1h", "source": "a.example"}


def sec(name, results=None, summary=None):
    return {"name": name, "results": [] if results is None else results, "summary": summary}


NEWSPAPER = {
    "drop_classes": frozenset({"meta"}),
    "italic_classes": frozenset({"note", "empty"}),
    "item_paragraphs": 1,
}


def digest_issue(html, **over):
    return html_to_markdown(html, **{**NEWSPAPER, **over})


# The renderer's notes are step config (no texts in code).
TEXTS = {
    "no_results": "No results.",
    "summary_unavailable": "Summary unavailable.",
    "summaries_unavailable": "Summaries were unavailable for this issue.",
}


def html_of(sections):
    payload = {"date": DATE, "title": "Daily newspaper", "sections": sections, "texts": TEXTS}
    result = NewspaperRenderPort().invoke(payload, "k", DEADLINE, context=ctx())
    assert result.outcome == COMPLETED, result.error
    return result.output["html"]


def test_the_builtin_name():
    assert FROM_HTML_BUILTIN == "markdown.from_html"


def test_headline_then_each_section_with_its_summary_as_markdown():
    html = html_of(
        [
            sec(
                "AI",
                [hit(URL_A, "Big model")],
                f"- A new model shipped [a]({URL_A})\n- It is fast [a]({URL_A})",
            ),
            sec("Robots", [hit(URL_B, "Arm")], f"- Robots got arms [b]({URL_B})"),
        ]
    )
    out = digest_issue(html)
    text = "\n".join(out["chunks"])
    assert text.startswith("**Daily newspaper**\n2026-10-11")
    assert out["markdown"] == "\n\n".join(out["chunks"])
    assert "**AI**" in text
    assert f"- A new model shipped [a](<{URL_A}>)" in text
    assert f"- It is fast [a](<{URL_A}>)" in text
    assert "**Robots**" in text
    assert text.index("**AI**") < text.index("**Robots**")
    assert "<p>" not in text
    assert "<li>" not in text


def test_a_section_without_a_summary_lists_its_headlines_as_unembedded_links():
    out = digest_issue(html_of([sec("AI", [hit(URL_A, "Big model"), hit(URL_B, "Arm")])]))
    text = "\n".join(out["chunks"])
    assert f"[Big model](<{URL_A}>)" in text
    assert f"[Arm](<{URL_B}>)" in text


def test_an_empty_section_says_so():
    text = "\n".join(digest_issue(html_of([sec("Quiet")]))["chunks"])
    assert "**Quiet**" in text
    assert "No results" in text


def test_every_chunk_fits_and_sections_stay_whole_and_in_order():
    sections = [
        sec(f"S{n}", [hit(URL_A)], f"- Summary number {n} [a]({URL_A})\n" * 4) for n in range(12)
    ]
    out = digest_issue(html_of(sections), max_chars=400)
    assert len(out["chunks"]) > 1
    assert all(len(c) <= 400 for c in out["chunks"])
    joined = "\n".join(out["chunks"])
    positions = [joined.index(f"**S{n}**") for n in range(12)]
    assert positions == sorted(positions)
    for chunk in out["chunks"][1:]:
        assert chunk.startswith("**S")


def test_one_section_longer_than_a_chunk_is_split_by_lines():
    long_summary = "\n".join(f"- point {n} " + "x" * 60 + f" [a]({URL_A})" for n in range(30))
    out = digest_issue(html_of([sec("Long", [hit(URL_A)], long_summary)]), max_chars=500)
    assert all(len(c) <= 500 for c in out["chunks"])
    assert "point 29" in out["chunks"][-1]


def test_markup_in_the_issue_stays_text():
    out = digest_issue(
        html_of([sec("AI", [hit(URL_A)], f"- Tags like <b>this</b> are text [a]({URL_A})")])
    )
    assert r"<b\>this</b\>" in "\n".join(out["chunks"])  # shown as <b>this</b> on Discord


def ctx_with(**config):
    return InvocationContext(run_id="r1", step_id="md", kind="code", host="spark", config=config)


def test_the_port_converts_its_html_input():
    html = html_of([sec("AI", [hit(URL_A)], f"- ships [a]({URL_A})")])
    res = MarkdownFromHtmlPort().invoke(
        {"html": html}, "k", DEADLINE, context=ctx_with(drop_classes=["meta"], item_paragraphs=1)
    )
    assert res.outcome == COMPLETED, res.error
    assert res.output["count"] == len(res.output["chunks"])
    assert "**AI**" in res.output["markdown"]


def test_the_port_takes_max_chars_from_config():
    sections = [sec(f"S{n}", [hit(URL_A)], f"- {'words ' * 30}[a]({URL_A})") for n in range(6)]
    res = MarkdownFromHtmlPort().invoke(
        {"html": html_of(sections)}, "k", DEADLINE, context=ctx_with(max_chars=300)
    )
    assert res.outcome == COMPLETED, res.error
    assert all(len(c) <= 300 for c in res.output["chunks"])


def test_bad_params_fail_without_retry():
    for inp, config in (
        ({"html": 5}, {}),
        ({"html": "<p>x</p>"}, {"max_chars": 10}),
        ({"html": "<p>x</p>"}, {"drop_classes": [1]}),
        ({"html": "<p>x</p>"}, {"item_paragraphs": 0}),
    ):
        res = MarkdownFromHtmlPort().invoke(inp, "k", DEADLINE, context=ctx_with(**config))
        assert res.outcome == FAILED
        assert res.retryable is False
        assert "bad_params" in res.error


def test_generic_html_reads_as_markdown():
    html = (
        "<html><head><title>x</title><style>p{}</style></head><body>"
        "<h3>Notes</h3><p>One <em> two </em> <b>three</b><br>four</p>"
        "<ol><li>first</li><li>second</li></ol><script>alert(1)</script></body></html>"
    )
    md = html_to_markdown(html)["markdown"]
    assert md == "**Notes**\nOne _two_ **three** four\n- first\n- second"


# PR #98 review


def test_markdown_in_headlines_and_summaries_is_escaped():
    out = digest_issue(html_of([sec("AI", [hit(URL_A, "[Video] top 10] __init__ *x*")])]))
    text = "\n".join(out["chunks"])
    assert r"- [\[Video\] top 10\] \_\_init\_\_ \*x\*](<" in text


def _link_line(label: str) -> str:
    return html_to_markdown(f'<p><a href="{URL_A}">{label}</a></p>')["markdown"]


def test_a_numbered_link_label_keeps_its_brackets_unescaped():
    assert _link_line("[1]") == f"[[1]](<{URL_A}>)"


def test_a_balanced_label_still_escapes_other_markdown():
    assert _link_line("[Video] __init__") == rf"[[Video] \_\_init\_\_](<{URL_A}>)"


def test_an_unpaired_bracket_in_a_label_stays_escaped():
    assert _link_line("a] b") == rf"[a\] b](<{URL_A}>)"


def test_brackets_two_levels_deep_in_a_label_stay_escaped():
    assert _link_line("[[1]]") == rf"[\[\[1\]\]](<{URL_A}>)"


def test_a_label_cannot_form_a_link_of_its_own():
    assert _link_line("[x](https://b.example)") == rf"[\[x\](https://b.example)](<{URL_A}>)"


def test_brackets_outside_a_link_stay_escaped():
    assert html_to_markdown("<p>[1]</p>")["markdown"] == r"\[1\]"


def test_long_lines_never_break_inside_a_label_with_brackets():
    html = f"<p>{'word ' * 70}<a href=\"{URL_A}\">[Big] model here</a></p>"
    chunks = html_to_markdown(html, max_chars=300)["chunks"]
    assert sum(c.count(f"[[Big] model here](<{URL_A}>)") for c in chunks) == 1


def test_a_url_cannot_close_its_angle_brackets():
    html = (
        '<h1>T</h1><p class="date">2026-10-11</p><section><h2>AI</h2><ul><li><p>'
        '<a href="https://a.example/q?x=1&gt;2 y">t</a></p></li></ul></section>'
    )
    assert "(<https://a.example/q?x=1%3E2%20y>)" in "\n".join(digest_issue(html)["chunks"])


def test_long_lines_break_at_spaces_and_never_inside_a_link():
    summary = "\n".join(f"- {'word ' * 70}[Big model here]({URL_A})" for _ in range(3))
    out = digest_issue(html_of([sec("AI", [hit(URL_A)], summary)]), max_chars=300)
    for chunk in out["chunks"]:
        assert len(chunk) <= 300
        assert chunk.count("[Big model here](") == chunk.count(f"](<{URL_A}>)")
    assert sum(c.count(f"[Big model here](<{URL_A}>)") for c in out["chunks"]) == 3


def test_a_section_heading_always_opens_its_content():
    long_line = f"- {'word ' * 80}[a]({URL_A})"
    sections = [sec("A", [hit(URL_A)], f"- short [a]({URL_A})"), sec("C", [hit(URL_A)], long_line)]
    out = digest_issue(html_of(sections), max_chars=300)
    for chunk in out["chunks"]:
        assert not chunk.rstrip().endswith("**C**")
    assert any(c.startswith("**C**\n- word") for c in out["chunks"])


def test_padded_bold_is_trimmed_so_discord_renders_it():
    html = html_of([sec("AI", [hit(URL_A)], f"- ships ** Jetson Thor ** today [a]({URL_A})")])
    assert "ships **Jetson Thor** today" in "\n".join(digest_issue(html)["chunks"])


def test_max_chars_may_come_from_the_input():
    sections = [sec(f"S{n}", [hit(URL_A)], f"- {'words ' * 30}[a]({URL_A})") for n in range(6)]
    res = MarkdownFromHtmlPort().invoke(
        {"html": html_of(sections), "max_chars": 300}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == COMPLETED, res.error
    assert all(len(c) <= 300 for c in res.output["chunks"])


SPLIT_HTML = (
    "<h1>Paper</h1><p class='date'>2026-10-11</p>"
    "<h2>Alpha</h2><ul><li>a1</li><li>a2</li></ul>"
    "<h2>Beta</h2><ul><li>b1</li></ul>"
    "<h2>Gamma</h2><ul><li>c1</li></ul>"
)


def test_split_at_h2_gives_masthead_then_each_section_in_order():
    parts = html_to_markdown(SPLIT_HTML, split_at="h2")["parts"]
    assert [p["heading"] for p in parts] == ["", "Alpha", "Beta", "Gamma"]


def test_split_at_parts_are_indexed_from_zero():
    parts = html_to_markdown(SPLIT_HTML, split_at="h2")["parts"]
    assert [p["index"] for p in parts] == [0, 1, 2, 3]


def test_split_at_masthead_part_holds_the_text_before_the_first_section():
    parts = html_to_markdown(SPLIT_HTML, split_at="h2")["parts"]
    assert parts[0]["text"] == "**Paper**\n2026-10-11"


def test_split_at_section_part_holds_its_whole_text():
    parts = html_to_markdown(SPLIT_HTML, split_at="h2")["parts"]
    assert parts[1]["text"] == "**Alpha**\n- a1\n- a2"


def test_split_at_heading_is_plain_text_without_escapes():
    parts = html_to_markdown("<h2>A_b *c*</h2><p>x</p>", split_at="h2")["parts"]
    assert parts[0]["heading"] == "A_b *c*"


def test_split_at_h1_keeps_deeper_headings_inside_the_part():
    parts = html_to_markdown(SPLIT_HTML, split_at="h1")["parts"]
    assert [p["heading"] for p in parts] == ["Paper"]


def test_long_section_stays_one_part_while_chunks_still_split():
    items = "".join(f"<li>{'word ' * 20}{n}</li>" for n in range(30))
    out = html_to_markdown(f"<h2>Big</h2><ul>{items}</ul>", split_at="h2", max_chars=300)
    assert len(out["parts"]) == 1


def test_long_section_still_splits_into_chunks():
    items = "".join(f"<li>{'word ' * 20}{n}</li>" for n in range(30))
    out = html_to_markdown(f"<h2>Big</h2><ul>{items}</ul>", split_at="h2", max_chars=300)
    assert len(out["chunks"]) > 1


def test_long_section_part_is_longer_than_max_chars():
    items = "".join(f"<li>{'word ' * 20}{n}</li>" for n in range(30))
    out = html_to_markdown(f"<h2>Big</h2><ul>{items}</ul>", split_at="h2", max_chars=300)
    assert len(out["parts"][0]["text"]) > 300


def test_no_parts_key_without_split_at():
    assert "parts" not in html_to_markdown(SPLIT_HTML)


def test_split_at_leaves_markdown_and_chunks_as_they_were():
    plain = html_to_markdown(SPLIT_HTML)
    split = html_to_markdown(SPLIT_HTML, split_at="h2")
    assert (split["markdown"], split["chunks"]) == (plain["markdown"], plain["chunks"])


def test_port_returns_parts_from_config_split_at():
    res = MarkdownFromHtmlPort().invoke(
        {"html": SPLIT_HTML}, "k", DEADLINE, context=ctx_with(split_at="h2")
    )
    assert len(res.output["parts"]) == 4


def test_port_input_split_at_wins_over_config():
    res = MarkdownFromHtmlPort().invoke(
        {"html": SPLIT_HTML, "split_at": "h1"}, "k", DEADLINE, context=ctx_with(split_at="h2")
    )
    assert len(res.output["parts"]) == 1


def test_port_without_split_at_has_no_parts_key():
    res = MarkdownFromHtmlPort().invoke({"html": SPLIT_HTML}, "k", DEADLINE, context=ctx_with())
    assert "parts" not in res.output


@pytest.mark.parametrize("bad", ["h9", 3, "H2", "p", ""])
def test_bad_split_at_fails_bad_params_without_retry(bad):
    res = MarkdownFromHtmlPort().invoke(
        {"html": "<p>x</p>"}, "k", DEADLINE, context=ctx_with(split_at=bad)
    )
    assert (res.outcome, res.retryable, "bad_params" in res.error) == (FAILED, False, True)


def test_split_at_without_a_heading_of_that_rank_is_one_lead_part():
    parts = html_to_markdown("<h1>Paper</h1><p>x</p><h3>Deep</h3>", split_at="h2")["parts"]
    assert [(p["heading"], p["text"]) for p in parts] == [("", "**Paper**\nx\n\n**Deep**")]
