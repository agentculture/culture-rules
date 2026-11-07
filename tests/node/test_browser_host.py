"""browser_host: a link's host only when it is the host a browser would open."""

from __future__ import annotations

import pytest

from culture_rules.extensions.newspaper.port import filter_results
from culture_rules.node.actions._urls import browser_host


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.com\\@good.com/x",
        "http://user@good.com/x",
        "http://evil.com%40good.com/x",
        "http://straße.de/x",
        "http://good.com:port/x",
        "ftp://good.com/x",
        "http://good .com/x",
        "http://good.com/\x00",
    ],
)
def test_an_ambiguous_or_unsafe_url_has_no_host(url):
    assert browser_host(url) is None


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://Good.com/x", "good.com"),
        ("http://xn--strae-oqa.de/x", "xn--strae-oqa.de"),
        ("http://good.com:8080/x", "good.com"),
        ("http://[::1]/x", "::1"),
        ("https://good.com/path/@user", "good.com"),
    ],
)
def test_a_plain_url_gives_its_host(url, host):
    assert browser_host(url) == host


def test_the_newspaper_filter_drops_a_backslash_url_its_allowlist_would_have_kept():
    section = {"must": [], "must_not": [], "site_include": ["good.com"], "site_exclude": []}
    section["count"] = 5
    results = [{"url": "http://evil.com\\@good.com/x", "title": "t", "snippet": "s"}]
    assert filter_results(results, section) == []


def test_the_newspaper_filter_drops_a_userinfo_url():
    section = {"must": [], "must_not": [], "site_include": [], "site_exclude": [], "count": 5}
    results = [{"url": "http://user@good.com/x", "title": "t", "snippet": "s"}]
    assert filter_results(results, section) == []


def test_a_trailing_dot_is_the_same_host():
    assert browser_host("http://evil.com./x") == "evil.com"


def test_port_zero_has_no_host():
    assert browser_host("http://good.com:0/x") is None


@pytest.mark.parametrize("url", ["https://good.com/docs\\page", "https://good.com/s?q=a\\b"])
def test_a_backslash_outside_the_authority_keeps_the_host(url):
    assert browser_host(url) == "good.com"


def test_a_trailing_dot_cannot_slip_past_the_newspapers_site_exclude():
    section = {"must": [], "must_not": [], "site_include": [], "site_exclude": ["evil.com"]}
    section["count"] = 5
    results = [{"url": "http://evil.com./x", "title": "t", "snippet": "s"}]
    assert filter_results(results, section) == []


def test_a_summary_link_a_browser_reads_differently_renders_as_text():
    from culture_rules.extensions.newspaper.render import _inline

    html = _inline("see [the story](http://evil.com\\@good.com/x)")
    assert "<a " not in html


def test_a_plain_summary_link_renders_as_an_anchor():
    from culture_rules.extensions.newspaper.render import _inline

    assert '<a href="https://good.com/x">' in _inline("see [the story](https://good.com/x)")
