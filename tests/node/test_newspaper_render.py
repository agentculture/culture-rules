"""#53 t6: ``newspaper.render`` - one dated issue as HTML for the email and an EPUB for the reader.

Covers c13/h7 (a structurally valid, deterministic EPUB: mimetype first and stored,
container.xml, OPF, nav and NCX, one chapter per section), c19/h5 (every summarised item links
one of its section's results, otherwise the summary is dropped), h6 (a section with no results
says so) and the results-only rendering when summaries are unavailable.
"""

from __future__ import annotations

import base64
import io
import posixpath
import zipfile
from datetime import UTC, datetime
from xml.etree import ElementTree

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions import newspaper_render
from culture_rules.node.actions.newspaper import NewspaperRenderPort
from culture_rules.node.actions.newspaper_render import (
    LINK_NOTE,
    NO_RESULTS,
    SUMMARIES_UNAVAILABLE,
    SUMMARY_UNAVAILABLE,
    summary_links,
    summary_to_xhtml,
)

DEADLINE = datetime(2026, 10, 11, 23, tzinfo=UTC)
DATE = "2026-10-11"
URL_A = "https://a.example/story"
URL_B = "https://b.example/news?id=1&x=2"
OPF_NS = "{http://www.idpf.org/2007/opf}"
CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
XHTML_TYPE = "application/xhtml+xml"
HOSTILE = "<script>alert(1)</script>"


def ctx():
    return InvocationContext(run_id="r1", step_id="render", kind="code", host="spark")


def hit(url, title="Story", snippet="A snippet", source="a.example", age="2 hours ago"):
    return {"title": title, "url": url, "snippet": snippet, "age": age, "source": source}


def sec(name="AI", results=None, summary=None):
    return {"name": name, "results": [] if results is None else results, "summary": summary}


def render(sections, **extra):
    payload = {"date": DATE, "title": "Daily newspaper", "sections": sections, **extra}
    return NewspaperRenderPort().invoke(payload, "k", DEADLINE, context=ctx())


def ok(sections, **extra):
    result = render(sections, **extra)
    assert result.outcome == COMPLETED, result.error
    return result.output


def epub(output):
    return zipfile.ZipFile(io.BytesIO(base64.b64decode(output["epub_base64"])))


def text_of(output, name):
    return epub(output).read(name).decode("utf-8")


SUMMARISED = sec(
    results=[hit(URL_A), hit(URL_B, title="Other")],
    summary=f"- **Big** news: [A story]({URL_A})\n- More: [B]({URL_B})",
)


# ------------------------------------------------------------------ the output


def test_output_shape():
    output = ok([SUMMARISED])
    assert set(output) == {"subject", "html", "epub_base64", "size", "sections_rendered"}


def test_subject_carries_title_and_date():
    assert ok([SUMMARISED])["subject"] == "Daily newspaper 2026-10-11"


def test_size_is_the_epub_size():
    output = ok([SUMMARISED])
    assert output["size"] == len(base64.b64decode(output["epub_base64"]))


def test_sections_rendered_count():
    assert ok([SUMMARISED, sec(name="Robots")])["sections_rendered"] == 2


def test_deterministic_bytes():
    assert ok([SUMMARISED])["epub_base64"] == ok([SUMMARISED])["epub_base64"]


def test_deterministic_html():
    assert ok([SUMMARISED])["html"] == ok([SUMMARISED])["html"]


# ------------------------------------------------------------------ EPUB structure (h7)


def test_mimetype_is_first_and_stored():
    first = epub(ok([SUMMARISED])).infolist()[0]
    assert (first.filename, first.compress_type) == ("mimetype", zipfile.ZIP_STORED)


def test_mimetype_content():
    assert epub(ok([SUMMARISED])).read("mimetype") == b"application/epub+zip"


def test_zip_entries_have_fixed_timestamps():
    stamps = {info.date_time for info in epub(ok([SUMMARISED])).infolist()}
    assert stamps == {(1980, 1, 1, 0, 0, 0)}


def opf_path(output):
    root = ElementTree.fromstring(epub(output).read("META-INF/container.xml"))
    rootfile = root.find(f"{CONTAINER_NS}rootfiles/{CONTAINER_NS}rootfile")
    assert rootfile.get("media-type") == "application/oebps-package+xml"
    return rootfile.get("full-path")


def test_container_points_at_the_opf():
    output = ok([SUMMARISED])
    assert opf_path(output) in epub(output).namelist()


def manifest(output):
    path = opf_path(output)
    root = ElementTree.fromstring(epub(output).read(path))
    base = posixpath.dirname(path)
    items = root.findall(f"{OPF_NS}manifest/{OPF_NS}item")
    return root, base, items


def test_every_manifest_item_exists():
    output = ok([SUMMARISED, sec(name="Robots")])
    _, base, items = manifest(output)
    names = set(epub(output).namelist())
    missing = [i.get("href") for i in items if posixpath.join(base, i.get("href")) not in names]
    assert missing == []


def test_manifest_has_nav_and_ncx():
    _, _, items = manifest(ok([SUMMARISED]))
    properties = {item.get("id"): item.get("properties") for item in items}
    types = {item.get("media-type") for item in items}
    assert "nav" in properties.values()
    assert "application/x-dtbncx+xml" in types


def test_one_chapter_per_section_in_the_spine():
    root, _, items = manifest(ok([SUMMARISED, sec(name="Robots"), sec(name="Space")]))
    spine = [ref.get("idref") for ref in root.findall(f"{OPF_NS}spine/{OPF_NS}itemref")]
    chapters = [
        i.get("id") for i in items if i.get("media-type") == XHTML_TYPE and not i.get("properties")
    ]
    assert spine == chapters
    assert len(spine) == 3


def test_opf_metadata():
    root, _, _ = manifest(ok([SUMMARISED]))
    meta = ElementTree.tostring(root.find(f"{OPF_NS}metadata"), encoding="unicode")
    assert "urn:culture-rules:newspaper:2026-10-11" in meta
    assert ">en<" in meta
    assert "dcterms:modified" in meta


def test_opf_is_epub3():
    root, _, _ = manifest(ok([SUMMARISED]))
    assert root.get("version") == "3.0"


def test_every_xhtml_and_xml_entry_parses():
    archive = epub(ok([SUMMARISED, sec(name="Robots", results=[hit(URL_A)])]))
    bad = []
    for name in archive.namelist():
        if name.endswith((".xhtml", ".xml", ".opf", ".ncx")):
            try:
                ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                bad.append(name)
    assert bad == []


def test_no_remote_resources():
    archive = epub(ok([SUMMARISED]))
    remote = [n for n in archive.namelist() if 'src="http' in archive.read(n).decode("utf-8")]
    assert remote == []


def test_ncx_lists_every_section():
    ncx = text_of(ok([SUMMARISED, sec(name="Robots")]), "OEBPS/toc.ncx")
    assert ncx.count("<navPoint") == 2


def test_nav_lists_every_section():
    nav = text_of(ok([SUMMARISED, sec(name="Robots")]), "OEBPS/nav.xhtml")
    assert 'epub:type="toc"' in nav
    assert "Robots" in nav


# ------------------------------------------------------------------ sections


def test_no_results_says_so():  # h6
    output = ok([sec(name="Empty", results=[], summary="- [made up](https://x.example)")])
    assert f'<p class="empty">{NO_RESULTS}</p>' in output["html"]
    assert "made up" not in output["html"]


def test_summary_rendered_with_links():
    html = ok([SUMMARISED])["html"]
    assert f'<a href="{URL_A}">A story</a>' in html
    assert "<strong>Big</strong>" in html


def test_summary_link_with_ampersand_is_escaped():
    assert 'href="https://b.example/news?id=1&amp;x=2"' in ok([SUMMARISED])["html"]


def test_missing_summary_renders_results_with_the_note():
    html = ok([sec(results=[hit(URL_A, title="Alpha")], summary=None)])["html"]
    assert f'<a href="{URL_A}">Alpha</a>' in html
    assert SUMMARY_UNAVAILABLE in html


def test_results_rendering_carries_snippet_source_and_age():
    html = ok([sec(results=[hit(URL_A, snippet="The gist")])])["html"]
    assert "The gist" in html
    assert "a.example · 2 hours ago" in html


def test_summaries_unavailable_flag_adds_the_issue_note():
    html = ok([sec(results=[hit(URL_A)])], summaries_unavailable=True)["html"]
    assert SUMMARIES_UNAVAILABLE in html


def test_parallel_results_and_summaries_lists():
    sections = [{"name": "AI"}, {"name": "Robots"}]
    output = ok(
        sections,
        results=[[hit(URL_A)], []],
        summaries=[f"- [a]({URL_A})", None],
    )
    assert f'<a href="{URL_A}">a</a>' in output["html"]
    assert NO_RESULTS in output["html"]


def test_parallel_results_as_for_each_result_objects():
    # t8: a for_each loop's ``results`` port is each iteration's whole output object,
    # ``{"results": [...]}`` from newspaper.filter (an empty object for a skipped iteration)
    sections = [{"name": "AI"}, {"name": "Robots"}, {"name": "Space"}]
    output = ok(sections, results=[{"results": [hit(URL_A)]}, {"results": []}, {}])
    assert URL_A in output["html"]
    assert output["html"].count(NO_RESULTS) == 2


# ------------------------------------------------------------------ the link check (h5)


def test_summary_linking_outside_its_results_is_dropped():
    bad = sec(results=[hit(URL_A, title="Alpha")], summary="- [x](https://evil.example/)")
    html = ok([bad])["html"]
    assert "evil.example" not in html
    assert LINK_NOTE in html
    assert f'<a href="{URL_A}">Alpha</a>' in html


def test_summary_linking_another_sections_result_is_dropped():
    first = sec(name="One", results=[hit(URL_A)], summary=f"- [a]({URL_A})")
    second = sec(name="Two", results=[hit(URL_B)], summary=f"- [a]({URL_A})")
    html = ok([first, second])["html"]
    assert html.count(LINK_NOTE) == 1


def test_bullet_without_a_link_drops_the_summary():
    unsourced = sec(results=[hit(URL_A)], summary=f"- [a]({URL_A})\n- invented claim")
    html = ok([unsourced])["html"]
    assert "invented claim" not in html
    assert LINK_NOTE in html


def test_unlinked_prose_before_a_linked_bullet_drops_the_summary():
    """Codex r1 #5: an unsourced paragraph must not ride along with one valid bullet."""
    summary = f"Markets crashed today, insiders say.\n- [a]({URL_A})"
    html = ok([sec(results=[hit(URL_A)], summary=summary)])["html"]
    assert "insiders say" not in html


def test_unlinked_prose_falls_back_to_the_results_with_the_note():
    summary = f"# Heading\n- [a]({URL_A})"
    html = ok([sec(results=[hit(URL_A, title="Alpha")], summary=summary)])["html"]
    assert LINK_NOTE in html


def test_summary_without_any_link_is_dropped():
    html = ok([sec(results=[hit(URL_A)], summary="All quiet today.")])["html"]
    assert "All quiet" not in html


def test_summary_links_extracted():
    assert summary_links(f"- [a]({URL_A}) and [b]({URL_B})") == [URL_A, URL_B]


def test_summary_to_xhtml_groups_bullets():
    assert summary_to_xhtml(f"Intro\n- [a]({URL_A})\n* [b]({URL_B})") == (
        f'<p>Intro</p><ul><li><a href="{URL_A}">a</a></li>'
        f'<li><a href="{URL_B.replace("&", "&amp;")}">b</a></li></ul>'
    )


# ------------------------------------------------------------------ hostile input


def test_hostile_title_is_escaped():
    output = ok([sec(name=HOSTILE, results=[hit(URL_A, title=HOSTILE)])], title=HOSTILE)
    assert "<script>" not in output["html"]
    assert "&lt;script&gt;" in output["html"]


def test_hostile_title_is_escaped_in_the_epub():
    output = ok([sec(name=HOSTILE, results=[hit(URL_A, title=HOSTILE)])], title=HOSTILE)
    archive = epub(output)
    leaked = [n for n in archive.namelist() if b"<script>" in archive.read(n)]
    assert leaked == []


def test_javascript_result_url_is_not_linked():
    html = ok([sec(results=[hit("javascript:alert(1)", title="Bad")])])["html"]
    assert "javascript:" not in html
    assert "Bad" in html


def test_javascript_summary_link_drops_the_summary():
    js = "javascript:alert(1)"
    html = ok([sec(results=[hit(js), hit(URL_A)], summary=f"- [x]({js})")])["html"]
    assert "javascript:" not in html


def test_raw_html_in_a_summary_is_escaped():
    summary = f"- <img src=x onerror=alert(1)> [a]({URL_A})"
    html = ok([sec(results=[hit(URL_A)], summary=summary)])["html"]
    assert "<img" not in html


BOLD_URL = "https://news.example/**story**"


def test_bold_markup_inside_a_link_url_is_left_alone():
    """Codex r1 #4: bold substitution must never rewrite a generated href."""
    summary = f"- **Big**: [a]({BOLD_URL})"
    html = ok([sec(results=[hit(BOLD_URL)], summary=summary)])["html"]
    assert f'href="{BOLD_URL}"' in html


def test_bold_markup_inside_a_link_url_keeps_every_chapter_xml():
    summary = f"- [a]({BOLD_URL}) and **[b]({BOLD_URL})**"
    second = sec(name="Two", results=[hit(BOLD_URL)], summary=f"- [x **y**]({BOLD_URL})")
    archive = epub(ok([sec(results=[hit(BOLD_URL)], summary=summary), second]))
    chapters = [n for n in archive.namelist() if n.endswith(".xhtml")]
    parsed = [ElementTree.fromstring(archive.read(n)).tag for n in chapters]
    assert len(parsed) == 3


def test_bold_still_applies_to_text_and_link_labels():
    xhtml = summary_to_xhtml(f"- **Big** [x **y**]({URL_A})")
    assert (
        xhtml
        == f'<ul><li><strong>Big</strong> <a href="{URL_A}">x <strong>y</strong></a></li></ul>'
    )


def test_control_characters_are_stripped():
    archive = epub(ok([sec(name="AI\x00\x07", results=[hit(URL_A, title="T\x0b")])]))
    ElementTree.fromstring(archive.read("OEBPS/s01.xhtml"))


def test_quote_in_url_cannot_break_the_attribute():
    url = 'https://a.example/x"onmouseover="alert(1)'
    html = ok([sec(results=[hit(url, title="Q")])])["html"]
    assert 'onmouseover="' not in html


# ------------------------------------------------------------------ bad input and caps


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"date": "11/10/2026"}, "date"),
        ({"date": "2026-13-01"}, "date"),
        ({"title": ""}, "title"),
        ({"sections": []}, "sections"),
        ({"sections": "AI"}, "sections"),
        ({"sections": [{"results": []}]}, "sections[0].name"),
        ({"sections": [{"name": "AI", "results": "x"}]}, "sections[0].results"),
    ],
)
def test_bad_input_names_the_field(payload, field):
    base = {"date": DATE, "title": "Daily newspaper", "sections": [SUMMARISED]}
    result = NewspaperRenderPort().invoke({**base, **payload}, "k", DEADLINE, context=ctx())
    assert result.outcome == FAILED
    assert result.retryable is False
    assert field in result.error


def test_title_defaults():
    result = NewspaperRenderPort().invoke(
        {"date": DATE, "sections": [SUMMARISED]}, "k", DEADLINE, context=ctx()
    )
    assert result.output["subject"] == "Daily newspaper 2026-10-11"


def test_html_cap_fails_clearly():
    port = NewspaperRenderPort(html_cap=100)
    payload = {"date": DATE, "sections": [SUMMARISED]}
    result = port.invoke(payload, "k", DEADLINE, context=ctx())
    assert result.outcome == FAILED
    assert result.error.startswith("html_too_large")


def test_epub_cap_fails_clearly():
    port = NewspaperRenderPort(epub_cap=100)
    payload = {"date": DATE, "sections": [SUMMARISED]}
    result = port.invoke(payload, "k", DEADLINE, context=ctx())
    assert result.outcome == FAILED
    assert result.error.startswith("epub_too_large")


def test_default_caps():
    assert (newspaper_render.HTML_CAP, newspaper_render.EPUB_CAP) == (1 << 20, 5 << 20)


def test_long_fields_are_truncated():
    html = ok([sec(results=[hit(URL_A, snippet="s" * 5000)])])["html"]
    assert "s" * 1001 not in html
