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
