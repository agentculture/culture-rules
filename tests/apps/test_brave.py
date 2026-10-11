"""Brave web search client: request shape, host lock, normalisation and caps (fake transport)."""

from __future__ import annotations

import json
import logging
import urllib.parse

import pytest

from culture_rules.apps import brave
from culture_rules.apps.brave import BraveError, BraveSearch

FAKE_KEY = "brave" + "-" + "FAKEKEY0123456789abcdef"
NEWS_URL = "https://example.org/a"
Q = "q"
HOST = "example.org"
BAD_QUERY = "bad_query"
BAD_RESPONSE = "bad_response"


def result(**over):
    item = {
        "title": "A <strong>big</strong> story",
        "url": NEWS_URL,
        "description": "Text with <strong>bold</strong> &amp; an entity",
        "age": "2 hours ago",
        "profile": {"name": "Example News"},
        "meta_url": {"hostname": HOST},
    }
    item.update(over)
    return item


class Fake:
    def __init__(self, status=200, results=None, raw=None, exc=None):
        self.calls = []
        self.status = status
        self.results = [result()] if results is None else results
        self.raw = raw
        self.exc = exc

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if self.exc is not None:
            raise self.exc
        if self.raw is not None:
            return self.status, self.raw
        return self.status, json.dumps({"web": {"results": self.results}}).encode()


def client(fake):
    return BraveSearch(api_key=FAKE_KEY, transport=fake)


def query_of(fake):
    parts = urllib.parse.urlsplit(fake.calls[-1]["url"])
    return parts, dict(urllib.parse.parse_qsl(parts.query))


def test_request_carries_every_param_and_the_key_header():
    fake = Fake()
    client(fake).search("jetson news", count=5, freshness="pd", country="US", lang="en")
    parts, query = query_of(fake)
    assert fake.calls[-1]["method"] == "GET"
    assert parts.scheme == "https"
    assert parts.netloc == "api.search.brave.com"
    assert parts.path == "/res/v1/web/search"
    assert query == {
        Q: "jetson news",
        "count": "5",
        "freshness": "pd",
        "country": "US",
        "search_lang": "en",
    }
    assert fake.calls[-1]["headers"]["X-Subscription-Token"] == FAKE_KEY
    assert fake.calls[-1]["headers"]["Accept"] == "application/json"
    assert fake.calls[-1]["body"] is None


def test_optional_params_are_omitted():
    fake = Fake()
    client(fake).search(Q)
    _, query = query_of(fake)
    assert query == {Q: Q, "count": str(brave.DEFAULT_COUNT)}


def test_url_constant_is_brave_only():
    assert urllib.parse.urlsplit(brave.BRAVE_SEARCH_URL).netloc == brave.BRAVE_HOST
    assert brave.BRAVE_HOST == "api.search.brave.com"


def test_normalised_result_shape():
    out = client(Fake()).search(Q)
    assert out == [
        {
            "title": "A big story",
            "url": NEWS_URL,
            "snippet": "Text with bold & an entity",
            "age": "2 hours ago",
            "source": "Example News",
        }
    ]


def test_source_falls_back_to_meta_url_then_url_host():
    items = [
        result(profile=None),
        result(profile=None, meta_url=None, url="https://fallback.example/x"),
    ]
    out = client(Fake(results=items)).search(Q)
    assert [r["source"] for r in out] == [HOST, "fallback.example"]


def test_missing_age_is_none():
    item = result()
    del item["age"]
    out = client(Fake(results=[item])).search(Q)
    assert out[0]["age"] is None


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "ftp://x.example/f", "", None, 7])
def test_non_http_urls_are_dropped(bad):
    out = client(Fake(results=[result(url=bad), result()])).search(Q)
    assert [r["url"] for r in out] == [NEWS_URL]


def test_overlong_url_is_dropped():
    long_url = "https://example.org/" + "a" * brave.MAX_URL_CHARS
    out = client(Fake(results=[result(url=long_url)])).search(Q)
    assert out == []


def test_non_mapping_items_are_skipped():
    out = client(Fake(results=["junk", 3, result()])).search(Q)
    assert len(out) == 1


def test_fields_are_capped():
    item = result(title="t" * 5000, description="d" * 50000, age="a" * 500)
    item["profile"] = {"name": "s" * 5000}
    out = client(Fake(results=[item])).search(Q)[0]
    assert len(out["title"]) == brave.MAX_TITLE_CHARS
    assert len(out["snippet"]) == brave.MAX_SNIPPET_CHARS
    assert len(out["age"]) == brave.MAX_AGE_CHARS
    assert len(out["source"]) == brave.MAX_SOURCE_CHARS


def test_result_count_is_capped_to_the_requested_count():
    items = [result(url=f"https://example.org/{i}") for i in range(30)]
    out = client(Fake(results=items)).search(Q, count=3)
    assert len(out) == 3


def test_count_above_brave_max_is_clamped():
    fake = Fake(results=[result(url=f"https://example.org/{i}") for i in range(40)])
    out = client(fake).search(Q, count=500)
    _, query = query_of(fake)
    assert query["count"] == str(brave.MAX_COUNT)
    assert len(out) == brave.MAX_COUNT


@pytest.mark.parametrize("count", [0, -1, True, "5", 2.5])
def test_bad_count_is_refused_without_a_call(count):
    fake = Fake()
    with pytest.raises(BraveError) as info:
        client(fake).search(Q, count=count)
    assert info.value.code == "bad_count"
    assert info.value.retryable is False
    assert fake.calls == []


@pytest.mark.parametrize(
    ("query", "kwargs", "code"),
    [
        ("", {}, BAD_QUERY),
        ("   ", {}, BAD_QUERY),
        (5, {}, BAD_QUERY),
        ("x" * 401, {}, BAD_QUERY),
        (Q, {"freshness": "pz"}, "bad_freshness"),
        (Q, {"freshness": "1d"}, "bad_freshness"),
        (Q, {"country": "USA"}, "bad_country"),
        (Q, {"country": "u1"}, "bad_country"),
        (Q, {"lang": "english"}, "bad_lang"),
        (Q, {"lang": "e"}, "bad_lang"),
    ],
)
def test_bad_params_are_refused_without_a_call(query, kwargs, code):
    fake = Fake()
    searcher = client(fake)
    with pytest.raises(BraveError) as info:
        searcher.search(query, **kwargs)
    assert info.value.code == code
    assert info.value.retryable is False
    assert fake.calls == []


@pytest.mark.parametrize("freshness", ["pd", "pw", "pm", "py"])
def test_every_brave_freshness_is_accepted(freshness):
    fake = Fake()
    client(fake).search(Q, freshness=freshness)
    assert query_of(fake)[1]["freshness"] == freshness


@pytest.mark.parametrize("lang", ["en", "he", "pt-br", "zh-hans"])
def test_lang_forms_are_accepted(lang):
    fake = Fake()
    client(fake).search(Q, lang=lang)
    assert query_of(fake)[1]["search_lang"] == lang


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (403, False), (422, False), (429, True), (500, True), (503, True)],
)
def test_http_status_mapping(status, retryable):
    searcher = client(Fake(status=status, raw=b"{}"))
    with pytest.raises(BraveError) as info:
        searcher.search(Q)
    assert info.value.code == f"http_{status}"
    assert info.value.retryable is retryable


def test_network_error_is_retryable_and_withholds_text():
    searcher = client(Fake(exc=OSError("boom " + FAKE_KEY)))
    with pytest.raises(BraveError) as info:
        searcher.search(Q)
    assert info.value.code == "network_error"
    assert info.value.retryable is True
    assert FAKE_KEY not in str(info.value)


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b"\xff\xfe"])
def test_malformed_body_is_retryable_bad_response(raw):
    searcher = client(Fake(raw=raw))
    with pytest.raises(BraveError) as info:
        searcher.search(Q)
    assert info.value.code == BAD_RESPONSE
    assert info.value.retryable is True


@pytest.mark.parametrize("body", [{}, {"web": None}, {"web": {"results": "x"}}])
def test_missing_results_is_an_empty_list(body):
    out = client(Fake(raw=json.dumps(body).encode())).search(Q)
    assert out == []


def test_missing_key_is_not_configured_without_a_call():
    fake = Fake()
    searcher = BraveSearch(api_key="", transport=fake)
    with pytest.raises(BraveError) as info:
        searcher.search(Q)
    assert info.value.code == "not_configured"
    assert fake.calls == []


def test_key_never_in_repr_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    searcher = client(Fake(status=500, raw=b"{}"))
    with pytest.raises(BraveError):
        searcher.search(Q)
    assert FAKE_KEY not in repr(searcher)
    assert FAKE_KEY not in caplog.text
