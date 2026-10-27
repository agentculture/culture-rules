"""#97 t3: the built-in ``document.sections`` - split an EPUB, HTML or Markdown document at a
configured heading into sections: the heading text, its http(s) links and an excerpt.

Covers c27 (a generic step, every rule config or input), h5/h6 (a real rendered issue: one
entry per section with results, none for 'No results.', only the section's own links), c8/h7
(culture-library's chunk and match rules find every entry), c13 (the manifest passes
culture-library's validation) and c12/h11 (two configs, two outputs; the book's bytes unchanged).
"""

from __future__ import annotations

import base64
import io
import os
import zipfile
from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions import newspaper_render, sections
from culture_rules.node.actions.sections import SECTIONS_BUILTIN, DocumentSectionsPort
from tests.node import culture_library_rules as library

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
EXCERPT = {"paragraphs": 1, "max_chars": 2000}
"""The two excerpt settings every config must name (neither has a default)."""
MANIFEST_CONFIG = {
    "split_on": "h2",
    "paragraphs": 1,
    "max_chars": 300,
    "require_links": True,
    "fields": {"heading": "article_title", "links": "source_urls", "excerpt": "excerpt"},
}
LONG = (
    "Researchers released a new open model that runs on a single small board, and early "
    "testers report that it answers questions about local documents faster than the cloud "
    "service it was meant to replace, while drawing a fraction of the power; the team plans "
    "to publish the training recipe next month along with the evaluation harness they used."
)


def invoke(inputs, config=None):
    cfg = {"builtin": SECTIONS_BUILTIN, **(config or {})}
    ctx = InvocationContext("run-1", "wf[0]/sections", "code", "spark", config=cfg)
    return DocumentSectionsPort().invoke(inputs, "k", DEADLINE, context=ctx)


def completed(inputs, config=None):
    res = invoke(inputs, config)
    assert res.outcome == "completed", res.error
    return res.output


def failed(res, code):
    assert res.outcome == "failed"
    assert res.error.startswith(f"{code}:"), res.error
    assert res.retryable is False


def hit(url, title, snippet="A snippet about it."):
    return {"title": title, "url": url, "snippet": snippet, "age": "2h", "source": "x.example"}


def issue():
    """A real issue: a summarised section, two results-only sections, one with no results."""
    a1, a2 = "https://a.example/one?id=1&x=2", "https://a.example/two"
    b1 = "https://b.example/robots"
    c1, c2 = "https://c.example/chips", "https://c.example/boards"
    summary = (
        f"- **Open models**: {LONG} [read]({a1})\n"
        f"- A second item on the same subject links two pages [one]({a1}) [two]({a2})"
    )
    payload = {
        "date": "2026-10-27",
        "title": "Daily newspaper",
        "sections": [
            {
                "name": "AI & models",
                "results": [hit(a1, "One"), hit(a2, "Two")],
                "summary": summary,
            },
            {"name": "Robots", "results": [hit(b1, "Robot story", LONG)], "summary": None},
            {"name": "Quiet day", "results": []},
            {"name": "Chips", "results": [hit(c1, "Chip <news>"), hit(c2, "Boards")]},
        ],
    }
    urls = {"AI & models": [a1, a2], "Robots": [b1], "Chips": [c1, c2]}
    return newspaper_render.render_issue(payload), urls


def manifest():
    rendered, urls = issue()
    out = completed({"content_base64": rendered["epub_base64"]}, MANIFEST_CONFIG)
    return rendered, urls, out


# ------------------------------------------------------------------ the real issue (h5, h6)


def test_real_issue_has_one_entry_per_section_with_results():
    _, urls, out = manifest()
    assert out["count"] == len(urls)


def test_real_issue_counts_every_section_found():
    _, _, out = manifest()
    assert out["found"] == 4


def test_real_issue_titles_are_the_h2_texts():
    _, urls, out = manifest()
    assert [entry["article_title"] for entry in out["sections"]] == list(urls)


def test_real_issue_has_no_entry_for_no_results():
    _, _, out = manifest()
    assert "Quiet day" not in [entry["article_title"] for entry in out["sections"]]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_real_issue_links_are_only_the_sections_own(index):
    _, urls, out = manifest()
    entry = out["sections"][index]
    assert entry["source_urls"] == urls[entry["article_title"]]


def test_real_issue_entries_carry_only_the_renamed_fields():
    _, _, out = manifest()
    assert sorted(out["sections"][0]) == ["article_title", "excerpt", "source_urls"]


def test_real_issue_excerpts_are_cut_to_max_chars():
    _, _, out = manifest()
    assert max(len(entry["excerpt"]) for entry in out["sections"]) <= 300


def test_real_issue_excerpt_is_the_first_paragraph_cut_at_a_word():
    _, _, out = manifest()
    excerpt = out["sections"][0]["excerpt"]
    assert LONG.startswith(excerpt.removeprefix("Open models: "))


def test_real_issue_excerpt_unescapes_entities():
    _, _, out = manifest()
    assert out["sections"][2]["excerpt"] == "Chip <news>"


def test_real_issue_outputs_are_the_list_and_counts_only():
    _, _, out = manifest()
    assert sorted(out) == ["count", "found", "sections"]


# ------------------------------------------------------------------ the library's rules (c8, c13)


def test_every_entry_matches_one_library_chunk():
    rendered, _, out = manifest()
    chunks = library.chunk_text(library.epub_text(base64.b64decode(rendered["epub_base64"])))
    unmatched = [e["article_title"] for e in out["sections"] if not library.matches(e, chunks)]
    assert unmatched == []


def test_manifest_passes_the_library_validation():
    _, _, out = manifest()
    assert library.validate_manifest(out["sections"]) is None


def test_each_probe_falls_inside_one_library_paragraph():
    rendered, _, out = manifest()
    text = library.epub_text(base64.b64decode(rendered["epub_base64"]))
    paragraphs = [library.norm(p) for p in text.split("\n\n")]
    probes = [library.norm(e["excerpt"])[: library.MATCH_CHARS] for e in out["sections"]]
    assert all(any(p in para for para in paragraphs) for p in probes)


# ------------------------------------------------------------------ config shapes the output (c12)


def test_two_configs_on_one_epub_give_two_outputs():
    rendered, _, first = manifest()
    other = {**EXCERPT, "split_on": "h2", "paragraphs": 2, "max_chars": 80}
    second = completed({"content_base64": rendered["epub_base64"]}, other)
    assert second["sections"] != first["sections"]


def test_without_require_links_the_empty_section_is_kept():
    rendered, _ = issue()
    out = completed({"content_base64": rendered["epub_base64"]}, {**EXCERPT, "split_on": "h2"})
    assert out["sections"][2] == {"heading": "Quiet day", "links": [], "excerpt": "No results."}


def test_split_on_h1_gives_the_masthead_not_the_nav():
    rendered, _ = issue()
    out = completed({"content_base64": rendered["epub_base64"]}, {**EXCERPT, "split_on": "h1"})
    assert [s["heading"] for s in out["sections"]] == ["Daily newspaper"]


def test_rendering_is_unchanged_by_reading():
    first, _ = issue()
    completed({"content_base64": first["epub_base64"]}, MANIFEST_CONFIG)
    second, _ = issue()
    assert second["epub_base64"] == first["epub_base64"]


# ------------------------------------------------------------------ HTML

PAGE = (
    "<html><head><title>T</title><style>p{}</style></head><body>"
    "<h1>Top</h1><p>preamble <a href='https://pre.example/'>x</a></p>"
    "<h2>First &amp; <em>best</em></h2>"
    "<p>One  <b>two</b>\n three &lt;four&gt;<br/>five</p><p>Second para.</p>"
    "<p><a href='https://a.example/1'>a</a> <a href='mailto:x@y'>m</a> "
    "<a href='/rel'>r</a> <a href=' https://a.example/2 '>b</a> <a href='https://a.example/1'>"
    "again</a></p>"
    "<h3>Sub</h3><p>sub text</p>"
    "<h2>Second</h2><script>var u='https://no.example'</script><p>Just text.</p>"
    "<h1>Back to top</h1><p>dropped <a href='https://after.example/'>z</a></p>"
    "</body></html>"
)


def html_sections(**config):
    return completed({"html": PAGE}, {**EXCERPT, "split_on": "h2", **config})["sections"]


def test_html_headings_are_text_with_entities_unescaped():
    assert [s["heading"] for s in html_sections()] == ["First & best", "Second"]


def test_html_links_are_http_in_order_without_duplicates():
    assert html_sections()[0]["links"] == ["https://a.example/1", "https://a.example/2"]


def test_html_higher_heading_ends_the_section():
    assert html_sections()[1]["links"] == []


def test_html_excerpt_first_paragraph_collapsed_and_unescaped():
    assert html_sections()[0]["excerpt"] == "One two three <four> five"


def test_html_excerpt_takes_n_paragraphs():
    excerpt = html_sections(paragraphs=2)[0]["excerpt"]
    assert excerpt == "One two three <four> five\n\nSecond para."


def test_html_excerpt_cut_at_a_word_boundary():
    assert html_sections(max_chars=17)[0]["excerpt"] == "One two three"


def test_html_excerpt_without_a_space_is_cut_hard():
    out = completed(
        {"html": "<h2>H</h2><p>abcdefghij</p>"}, {**EXCERPT, "split_on": "h2", "max_chars": 4}
    )
    assert out["sections"][0]["excerpt"] == "abcd"


def test_html_zero_paragraphs_gives_an_empty_excerpt():
    assert html_sections(paragraphs=0)[0]["excerpt"] == ""


def test_html_script_text_is_not_an_excerpt():
    assert html_sections()[1]["excerpt"] == "Just text."


def test_html_lower_heading_stays_in_the_section():
    assert "Sub" in html_sections(paragraphs=10)[0]["excerpt"]


def test_require_links_drops_sections_without_one():
    out = completed({"html": PAGE}, {**EXCERPT, "split_on": "h2", "require_links": True})
    assert [s["heading"] for s in out["sections"]] == ["First & best"]


def test_require_links_reports_what_it_found():
    out = completed({"html": PAGE}, {**EXCERPT, "split_on": "h2", "require_links": True})
    assert out["found"] == 2


def test_input_overrides_config():
    out = completed({"html": PAGE, "split_on": "h1"}, {**EXCERPT, "split_on": "h2"})
    assert [s["heading"] for s in out["sections"]] == ["Top", "Back to top"]


def test_split_on_takes_a_level_number():
    out = completed({"html": PAGE}, {**EXCERPT, "split_on": 2})
    assert out["count"] == 2


# ------------------------------------------------------------------ Markdown

MARKDOWN = """# Title

intro [skip](https://intro.example/)

## First **bold** [here](https://m.example/h) ##

Some *emphasis* and `code` with a [link](https://m.example/1) and
a second line &amp; <https://m.example/2>, also https://m.example/3.

- a list item ![img](https://img.example/i.png)
- [dup](<https://m.example/1>)

```
## not a heading https://code.example/
```

### Sub

## Second

Plain text only.
"""


def md_sections(**config):
    return completed({"markdown": MARKDOWN}, {**EXCERPT, "split_on": 2, **config})["sections"]


def test_markdown_headings_at_the_configured_level():
    assert [s["heading"] for s in md_sections()] == ["First bold here", "Second"]


def test_markdown_links_labels_angles_and_bare_urls_in_order():
    assert md_sections()[0]["links"] == [
        "https://m.example/h",
        "https://m.example/1",
        "https://m.example/2",
        "https://m.example/3",
    ]


def test_markdown_excerpt_reads_as_a_reader_sees_it():
    assert md_sections()[0]["excerpt"] == (
        "Some emphasis and code with a link and a second line & https://m.example/2, "
        "also https://m.example/3."
    )


def test_markdown_list_items_are_paragraphs():
    excerpt = md_sections(paragraphs=3)[0]["excerpt"]
    assert excerpt.split("\n\n")[1:] == ["a list item img", "dup"]


def test_markdown_fenced_heading_does_not_split():
    assert md_sections()[1]["heading"] == "Second"


def test_markdown_split_on_a_tag_name_too():
    out = completed({"markdown": MARKDOWN}, {**EXCERPT, "split_on": "h1"})
    assert [s["heading"] for s in out["sections"]] == ["Title"]


def test_markdown_and_html_give_the_same_section_shape():
    html = "<h2>A</h2><p>See <a href='https://x.example/'>x</a> and https://y.example/</p>"
    md = "## A\n\nSee [x](https://x.example/) and https://y.example/\n"
    from_md = completed({"markdown": md}, {**EXCERPT, "split_on": 2})["sections"]
    from_html = completed({"html": html}, {**EXCERPT, "split_on": 2})["sections"]
    assert sorted(from_md[0]) == sorted(from_html[0])


def test_markdown_links_match_the_equivalent_html_page():
    md = "## A\n\nSee [x](https://x.example/) and https://y.example/\n"
    out = completed({"markdown": md}, {**EXCERPT, "split_on": 2})
    assert out["sections"][0]["links"] == ["https://x.example/", "https://y.example/"]


# ------------------------------------------------------------------ files under a root


def write_epub(root):
    rendered, _ = issue()
    path = root / "issue.epub"
    path.write_bytes(base64.b64decode(rendered["epub_base64"]))
    return path


def test_content_path_under_the_root(tmp_path):
    path = write_epub(tmp_path)
    out = completed({"content_path": str(path), "content_root": str(tmp_path)}, MANIFEST_CONFIG)
    assert out["count"] == 3


def test_content_path_relative_to_the_root(tmp_path):
    write_epub(tmp_path)
    out = completed(
        {"content_path": "issue.epub"}, {**MANIFEST_CONFIG, "content_root": str(tmp_path)}
    )
    assert out["count"] == 3


def test_content_path_markdown_by_extension(tmp_path):
    (tmp_path / "doc.md").write_text(MARKDOWN, encoding="utf-8")
    out = completed(
        {"content_path": "doc.md", "content_root": str(tmp_path)}, {**EXCERPT, "split_on": 2}
    )
    assert out["count"] == 2


def test_content_path_html_by_extension(tmp_path):
    (tmp_path / "page.html").write_text(PAGE, encoding="utf-8")
    out = completed(
        {"content_path": "page.html", "content_root": str(tmp_path)}, {**EXCERPT, "split_on": 2}
    )
    assert out["count"] == 2


def test_format_overrides_the_extension(tmp_path):
    (tmp_path / "page.txt").write_text(MARKDOWN, encoding="utf-8")
    inputs = {"content_path": "page.txt", "content_root": str(tmp_path), "format": "markdown"}
    assert completed(inputs, {**EXCERPT, "split_on": 2})["count"] == 2


def test_unknown_extension_without_format_is_refused(tmp_path):
    (tmp_path / "page.txt").write_text(MARKDOWN, encoding="utf-8")
    inputs = {"content_path": "page.txt", "content_root": str(tmp_path)}
    failed(invoke(inputs, {**EXCERPT, "split_on": 2}), "bad_params")


def test_content_path_outside_the_root_is_refused(tmp_path):
    inside, outside = tmp_path / "in", tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    path = write_epub(outside)
    failed(
        invoke(
            {"content_path": str(path), "content_root": str(inside)}, {**EXCERPT, "split_on": 2}
        ),
        "path_refused",
    )


def test_dot_dot_out_of_the_root_is_refused(tmp_path):
    inside = tmp_path / "in"
    inside.mkdir()
    write_epub(tmp_path)
    inputs = {"content_path": "../issue.epub", "content_root": str(inside)}
    failed(invoke(inputs, {**EXCERPT, "split_on": 2}), "path_refused")


def test_symlinked_file_is_refused(tmp_path):
    path = write_epub(tmp_path)
    (tmp_path / "link.epub").symlink_to(path)
    inputs = {"content_path": "link.epub", "content_root": str(tmp_path)}
    failed(invoke(inputs, {**EXCERPT, "split_on": 2}), "path_refused")


def test_symlinked_directory_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    write_epub(real)
    (tmp_path / "via").symlink_to(real, target_is_directory=True)
    inputs = {"content_path": "via/issue.epub", "content_root": str(tmp_path)}
    failed(invoke(inputs, {**EXCERPT, "split_on": 2}), "path_refused")


def test_missing_file_is_refused(tmp_path):
    inputs = {"content_path": "gone.epub", "content_root": str(tmp_path)}
    failed(invoke(inputs, {**EXCERPT, "split_on": 2}), "path_refused")


def test_relative_root_is_refused(tmp_path):
    write_epub(tmp_path)
    inputs = {"content_path": "issue.epub", "content_root": "relative/dir"}
    failed(invoke(inputs, {**EXCERPT, "split_on": 2}), "path_refused")


def test_content_path_needs_a_root(tmp_path):
    path = write_epub(tmp_path)
    failed(invoke({"content_path": str(path)}, {**EXCERPT, "split_on": 2}), "bad_params")


def test_oversized_file_is_refused(tmp_path, monkeypatch):
    path = write_epub(tmp_path)
    monkeypatch.setattr(sections, "MAX_INPUT_BYTES", os.path.getsize(path) - 1)
    inputs = {"content_path": str(path), "content_root": str(tmp_path)}
    failed(invoke(inputs, {**EXCERPT, "split_on": 2}), "too_large")


# ------------------------------------------------------------------ EPUB limits and refusals


def test_epub_text_over_the_cap_is_refused(monkeypatch):
    rendered, _ = issue()
    monkeypatch.setattr(sections, "MAX_EPUB_TEXT_BYTES", 100)
    failed(
        invoke({"content_base64": rendered["epub_base64"]}, {**EXCERPT, "split_on": 2}), "too_large"
    )


def test_not_a_zip_is_refused():
    data = base64.b64encode(b"not a zip at all").decode()
    failed(invoke({"content_base64": data}, {**EXCERPT, "split_on": 2}), "not_epub")


def test_bad_base64_is_refused():
    failed(invoke({"content_base64": "***"}, {**EXCERPT, "split_on": 2}), "bad_params")


def test_epub_without_a_package_reads_every_page():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr("b.xhtml", "<h2>B</h2><p><a href='https://b.example/'>b</a></p>")
        book.writestr("a.xhtml", "<h2>A</h2><p>a</p>")
        book.writestr("style.css", "h2{}")
    data = base64.b64encode(buffer.getvalue()).decode()
    out = completed({"content_base64": data}, {**EXCERPT, "split_on": 2})
    assert [s["heading"] for s in out["sections"]] == ["A", "B"]


# ------------------------------------------------------------------ bad params


@pytest.mark.parametrize("split_on", [None, "h7", "p", 0, 7, True, "##"])
def test_bad_split_on_is_refused(split_on):
    failed(invoke({"html": PAGE}, {**EXCERPT, "split_on": split_on}), "bad_params")


@pytest.mark.parametrize(
    "config",
    [
        {"paragraphs": -1},
        {"paragraphs": "1"},
        {"max_chars": 0},
        {"max_chars": True},
        {"require_links": "yes"},
        {"fields": {"title": "x"}},
        {"fields": {"heading": "Bad Name"}},
        {"fields": {"heading": "x", "links": "x"}},
        {"fields": {"heading": "links"}},
        {"fields": ["heading"]},
        {"format": "pdf"},
    ],
)
def test_bad_config_is_refused(config):
    failed(invoke({"html": PAGE}, {**EXCERPT, "split_on": 2, **config}), "bad_params")


def test_no_source_is_refused():
    failed(invoke({}, {**EXCERPT, "split_on": 2}), "bad_params")


def test_two_sources_are_refused():
    failed(invoke({"html": PAGE, "markdown": MARKDOWN}, {**EXCERPT, "split_on": 2}), "bad_params")


def test_html_must_be_a_string():
    failed(invoke({"html": 3}, {**EXCERPT, "split_on": 2}), "bad_params")


def test_oversized_html_is_refused(monkeypatch):
    monkeypatch.setattr(sections, "MAX_INPUT_BYTES", 10)
    failed(invoke({"html": PAGE}, {**EXCERPT, "split_on": 2}), "too_large")


@pytest.mark.parametrize("missing", ["paragraphs", "max_chars"])
def test_each_excerpt_setting_is_required(missing):
    config = {**EXCERPT, "split_on": "h2"}
    del config[missing]
    failed(invoke({"html": PAGE}, config), "bad_params")


def test_the_node_registers_document_sections():
    from culture_rules.store.memory import MemoryStore
    from culture_rules.node.runner import default_ports

    builtins = default_ports(MemoryStore(), "spark")["code"]._builtins
    assert isinstance(builtins[SECTIONS_BUILTIN], DocumentSectionsPort)


def test_document_sections_has_a_phrase():
    from culture_rules.model.describe import BUILTIN_WORDS

    assert BUILTIN_WORDS[SECTIONS_BUILTIN].startswith("document.sections:")


def test_http_call_catalogues_save_to():
    from culture_rules.model.action_kinds import ACTION_KINDS

    assert "save_to" in ACTION_KINDS["http.call"].params
