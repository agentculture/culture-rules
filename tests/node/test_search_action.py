"""search.web action port: bound actor, grant at call time, params, outputs (fake transport)."""

from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import UTC, datetime

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.search import SearchWebPort
from culture_rules.store.memory import MemoryStore

FAKE_KEY = "brave" + "-" + "FAKEKEY0123456789abcdef"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
ACTOR_ID = "search-app"
GRANT_REF = "grant:BRAVE_API_KEY"
NOT_FOUND = "actor_not_found"
QUERY = "jetson"


class Fake:
    def __init__(self, status=200, n=2):
        self.calls = []
        self.status = status
        self.n = n

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers))
        if self.status >= 400:
            return self.status, b"{}"
        results = [
            {
                "title": f"Story {i}",
                "url": f"https://example.org/{i}",
                "description": "<strong>snippet</strong>",
                "meta_url": {"hostname": "example.org"},
            }
            for i in range(self.n)
        ]
        return self.status, json.dumps({"web": {"results": results}}).encode()


def actor_doc(surface="search", enabled=True):
    return {
        "id": ACTOR_ID,
        "name": "brave",
        "kind": "service",
        "enabled": enabled,
        "params": {"surface": surface, "connection": {"api_key": GRANT_REF}},
        "schema_version": "1.0",
    }


def setup(fake, doc=None, fail=False):
    store = MemoryStore()
    store.put("actors", doc or actor_doc())
    resolved = []

    def secrets(ref):
        resolved.append(ref)
        if fail:
            raise RuntimeError("grant exploded " + FAKE_KEY)
        return FAKE_KEY

    return SearchWebPort(store, transport=fake, secrets=secrets), resolved


def ctx(actor=ACTOR_ID):
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor=actor)


def params(**over):
    out = {"actor": ACTOR_ID, "query": QUERY, "count": 5, "freshness": "pd"}
    out.update(over)
    return out


def test_port_flags():
    assert SearchWebPort(MemoryStore()).supports_idempotency_key is True


def test_search_completes_with_results_query_and_count():
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert dict(res.output) == {
        "results": [
            {
                "title": f"Story {i}",
                "url": f"https://example.org/{i}",
                "snippet": "snippet",
                "age": None,
                "source": "example.org",
            }
            for i in range(2)
        ],
        "query": QUERY,
        "count": 2,
    }
    assert resolved == [GRANT_REF]


def test_request_goes_to_brave_with_the_resolved_key():
    fake = Fake()
    port, _ = setup(fake)
    port.invoke(params(country="IL", lang="he"), "k", DEADLINE, context=ctx())
    _, url, headers = fake.calls[-1]
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    assert parts.netloc == "api.search.brave.com"
    assert query == {
        "q": QUERY,
        "count": "5",
        "freshness": "pd",
        "country": "IL",
        "search_lang": "he",
    }
    assert headers["X-Subscription-Token"] == FAKE_KEY


def test_actor_falls_back_to_input():
    port, _ = setup(Fake())
    res = port.invoke(params(), "k", DEADLINE, context=ctx(actor=None))
    assert res.outcome == "completed"


def test_unknown_actor_no_call_no_secret():
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke(params(), "k", DEADLINE, context=ctx(actor="nope"))
    assert (res.error, res.retryable) == (NOT_FOUND, False)
    assert fake.calls == []
    assert resolved == []


def test_wrong_surface_is_actor_not_found():
    fake = Fake()
    port, resolved = setup(fake, doc=actor_doc(surface="jira"))
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert res.error == NOT_FOUND
    assert resolved == []


def test_disabled_actor_is_actor_not_found():
    fake = Fake()
    port, resolved = setup(fake, doc=actor_doc(enabled=False))
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert res.error == NOT_FOUND
    assert resolved == []


def test_missing_query_is_bad_query_before_any_secret():
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke({"actor": ACTOR_ID}, "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("bad_query", False)
    assert fake.calls == []
    assert resolved == []


def test_bad_freshness_is_refused_before_any_secret():
    fake = Fake()
    port, resolved = setup(fake)
    res = port.invoke(params(freshness="yesterday"), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("bad_freshness", False)
    assert resolved == []


def test_count_is_capped_in_the_output():
    port, _ = setup(Fake(n=40))
    res = port.invoke(params(count=100), "k", DEADLINE, context=ctx())
    assert res.output["count"] == 20


def test_retryability_4xx_vs_429_vs_5xx():
    port, _ = setup(Fake(status=401))
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("http_401", False)
    port, _ = setup(Fake(status=429))
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("http_429", True)
    port, _ = setup(Fake(status=502))
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("http_502", True)


def test_secret_failure_and_no_key_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake()
    port, _ = setup(fake, fail=True)
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("secret_unavailable", False)
    assert fake.calls == []
    port, _ = setup(Fake(status=500))
    failed = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in repr(res)
    assert FAKE_KEY not in repr(failed)


def test_only_a_query_uses_the_default_count():
    fake = Fake()
    port, _ = setup(fake)
    res = port.invoke({"actor": ACTOR_ID, "query": QUERY}, "k", DEADLINE, context=ctx())
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(fake.calls[-1][1]).query))
    assert res.outcome == "completed"
    assert query == {"q": QUERY, "count": "10"}
