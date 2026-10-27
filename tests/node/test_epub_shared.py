"""The shared EPUB writer escapes every value it writes (#87 review)."""

from culture_rules.node.actions import _epub

_HOSTILE = 'en" foo="x'


def test_opf_escapes_the_language():
    assert 'xml:lang="en&quot; foo=&quot;x"' in _epub.opf("u", "t", "2026-10-27", [], lang=_HOSTILE)


def test_opf_escapes_the_direction():
    assert 'dir="en&quot; foo=&quot;x"' in _epub.opf("u", "t", "d", [], direction=_HOSTILE)


def test_opf_escapes_the_day():
    assert "<dc:date>a&lt;b</dc:date>" in _epub.opf("u", "t", "a<b", [])


def test_opf_escapes_a_chapter_id():
    assert '<itemref idref="c&quot;1"/>' in _epub.opf("u", "t", "d", ['c"1'])


def test_nav_body_escapes_a_chapter_id():
    assert 'href="c&quot;1.xhtml"' in _epub.nav_body([("label", 'c"1')])


def test_ncx_escapes_a_chapter_id():
    assert 'src="c&quot;1.xhtml"' in _epub.ncx("u", "t", [("label", 'c"1')])
