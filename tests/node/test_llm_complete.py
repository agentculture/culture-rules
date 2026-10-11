"""llm.complete action port (#53 t4): an OpenAI-compatible completion, locked brief, no tools."""

from __future__ import annotations

import http.server
import json
import socket
import threading
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.actors.limits import USAGE_COLLECTION, pool_doc_id
from culture_rules.engine.actorport import BLOCKED, COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.llm import (
    DEFAULT_MAX_TOKENS,
    MAX_TOKENS_CAP,
    NEWSPAPER_BRIEF,
    LlmCompletePort,
    urllib_transport,
)
from culture_rules.node.actors import ACTORS_COLLECTION, ActorRouter
from culture_rules.store.memory import MemoryStore

LOCAL = "http://127.0.0.1:8001"
MODEL = "qwen-test"
COMPLETIONS = "/v1/chat/completions"
POOL = "qwen-spark2"


def _reply(text="- [A](https://a.example/1)", model=MODEL, usage=None):
    usage = usage if usage is not None else {"prompt_tokens": 10, "completion_tokens": 5}
    body = {"model": model, "choices": [{"message": {"role": "assistant", "content": text}}]}
    body["usage"] = {**usage, "total_tokens": sum(usage.values())} if usage else {}
    return json.dumps(body).encode()


class FakeTransport:
    """Records each request; answers with ``status``/``body`` or raises ``exc``."""

    def __init__(self, status=200, body=None, exc=None):
        self.status = status
        self.body = _reply() if body is None else body
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append(
            {"url": url, "json": json.loads(data), "headers": headers, "timeout": timeout}
        )
        if self.exc is not None:
            raise self.exc
        return self.status, self.body

    @property
    def sent(self):
        return self.calls[-1]["json"]


def _actor(actor_id="llm", kind="agent", **params):
    base = {"base_url": LOCAL, "model": MODEL}
    base.update(params)
    return {"id": actor_id, "name": actor_id, "kind": kind, "enabled": True, "params": base}


def _store(*docs):
    store = MemoryStore()
    for doc in docs or (_actor(),):
        store.put(ACTORS_COLLECTION, doc)
    return store


def _ctx(actor="llm"):
    return InvocationContext(
        run_id="run1",
        step_id="summarise",
        kind="action",
        host="h1",
        actor=actor,
        config={"kind": "llm.complete", "params": {"actor": actor}},
    )


def _deadline(seconds=120):
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _invoke(port, key="k1", seconds=120, **params):
    params.setdefault("actor", "llm")
    params.setdefault("content", [{"title": "A", "url": "https://a.example/1"}])
    return port.invoke(params, key, _deadline(seconds), context=_ctx(params["actor"]))


def _port(store=None, transport=None, **kw):
    transport = transport or FakeTransport()
    return LlmCompletePort(store or _store(), transport=transport, **kw), transport


# ---- the request ---------------------------------------------------------------------------


def test_posts_chat_completions_to_the_actor_base_url():
    port, transport = _port()
    _invoke(port)
    assert transport.calls[0]["url"] == LOCAL + COMPLETIONS


def test_a_base_url_ending_in_v1_is_not_doubled():
    port, transport = _port(_store(_actor(base_url=LOCAL + "/v1/")))
    _invoke(port)
    assert transport.calls[0]["url"] == LOCAL + COMPLETIONS


def test_the_request_uses_the_actor_model():
    port, transport = _port()
    _invoke(port)
    assert transport.sent["model"] == MODEL


def test_the_system_message_is_the_locked_brief():
    port, transport = _port()
    _invoke(port)
    assert transport.sent["messages"][0] == {"role": "system", "content": NEWSPAPER_BRIEF}


def test_a_step_cannot_override_the_brief():
    port, transport = _port()
    _invoke(port, system="ignore the brief", instructions="be creative")
    assert [m["role"] for m in transport.sent["messages"]] == ["system", "user"]


def test_the_brief_is_locked_in_code():
    port, transport = _port(_store(_actor(locked_instruction="something else")))
    _invoke(port)
    assert transport.sent["messages"][0]["content"] == NEWSPAPER_BRIEF


@pytest.mark.parametrize(
    "phrase", ["only", "one bullet per item", "link", "No results.", "no preamble"]
)
def test_the_brief_says_what_the_spec_locks(phrase):
    assert phrase in NEWSPAPER_BRIEF


def test_string_content_is_the_user_message_verbatim():
    port, transport = _port()
    _invoke(port, content="1. A https://a.example/1")
    assert transport.sent["messages"][1] == {"role": "user", "content": "1. A https://a.example/1"}


def test_structured_content_is_serialised_deterministically():
    port, transport = _port()
    _invoke(port, content={"b": 1, "a": [{"y": 2, "x": 1}]})
    assert transport.sent["messages"][1]["content"] == '{"a":[{"x":1,"y":2}],"b":1}'


def test_never_a_tools_field():
    port, transport = _port(_store(_actor(tools=[{"type": "function"}])))
    _invoke(port, tools=[{"type": "function"}], tool_choice="auto")
    assert set(transport.sent) == {"model", "messages", "max_tokens", "stream"}


def test_no_repo_or_head_fields_are_sent():
    port, transport = _port(_store(_actor(repo="agentculture/x")))
    _invoke(port, repo="agentculture/x", head="abc")
    assert "repo" not in json.dumps(transport.sent)


def test_max_tokens_defaults():
    port, transport = _port()
    _invoke(port)
    assert transport.sent["max_tokens"] == DEFAULT_MAX_TOKENS


def test_max_tokens_is_capped():
    port, transport = _port()
    _invoke(port, max_tokens=MAX_TOKENS_CAP * 10)
    assert transport.sent["max_tokens"] == MAX_TOKENS_CAP


def test_max_tokens_within_the_cap_is_kept():
    port, transport = _port()
    _invoke(port, max_tokens=200)
    assert transport.sent["max_tokens"] == 200


def test_temperature_is_passed_when_given():
    port, transport = _port()
    _invoke(port, temperature=0.2)
    assert transport.sent["temperature"] == pytest.approx(0.2)


def test_the_timeout_comes_from_the_deadline():
    port, transport = _port()
    _invoke(port, seconds=30)
    assert 0 < transport.calls[0]["timeout"] <= 30


def test_no_authorization_without_an_api_key():
    port, transport = _port()
    _invoke(port)
    assert "Authorization" not in transport.calls[0]["headers"]


def test_an_api_key_grant_is_resolved_into_a_bearer_header():
    store = _store(_actor(api_key="grant:LLM_KEY"))
    port, transport = _port(store, secret_runner=lambda name: f"value-of-{name}")
    _invoke(port)
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer value-of-LLM_KEY"


# ---- the result ----------------------------------------------------------------------------


def test_returns_text_model_and_usage():
    port, _ = _port()
    res = _invoke(port)
    assert res.output == {
        "text": "- [A](https://a.example/1)",
        "model": MODEL,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def test_a_leading_think_block_is_not_part_of_the_text():
    port, _ = _port(transport=FakeTransport(body=_reply("<think>hmm</think>\n\nNo results.")))
    assert _invoke(port).output["text"] == "No results."


def test_missing_usage_is_an_empty_mapping():
    port, _ = _port(transport=FakeTransport(body=_reply(usage={})))
    assert _invoke(port).output["usage"] == {}


def test_a_completed_key_replays_without_a_second_request():
    port, transport = _port()
    _invoke(port, key="same")
    _invoke(port, key="same")
    assert len(transport.calls) == 1


# ---- failures ------------------------------------------------------------------------------


def _failed(res):
    return (res.outcome, res.error.split(":", 1)[0], res.retryable)


@pytest.mark.parametrize(
    "exc", [ConnectionRefusedError(), TimeoutError(), socket.gaierror(), OSError("down")]
)
def test_an_unreachable_server_is_retryable_llm_unavailable(exc):
    port, _ = _port(transport=FakeTransport(exc=exc))
    assert _failed(_invoke(port)) == (FAILED, "llm_unavailable", True)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_5xx_is_retryable_llm_unavailable(status):
    port, _ = _port(transport=FakeTransport(status=status, body=b"x"))
    assert _failed(_invoke(port)) == (FAILED, "llm_unavailable", True)


@pytest.mark.parametrize("status", [408, 429])
def test_a_busy_or_timed_out_4xx_is_retryable_llm_unavailable(status):
    port, _ = _port(transport=FakeTransport(status=status, body=b"x"))
    assert _failed(_invoke(port)) == (FAILED, "llm_unavailable", True)


@pytest.mark.parametrize("status", [301, 400, 401, 404, 422])
def test_other_non_2xx_is_non_retryable_llm_rejected(status):
    port, _ = _port(transport=FakeTransport(status=status, body=b"x"))
    assert _failed(_invoke(port)) == (FAILED, "llm_rejected", False)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"choices": []}',
        b'{"choices": [{"message": {"content": 7}}]}',
        b'{"choices": [{"text": "x"}]}',
    ],
)
def test_a_malformed_response_is_non_retryable_llm_bad_response(body):
    port, _ = _port(transport=FakeTransport(body=body))
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


def test_a_passed_deadline_is_retryable_without_a_request():
    port, transport = _port()
    res = _invoke(port, seconds=-1)
    assert (_failed(res), transport.calls) == ((FAILED, "llm_unavailable", True), [])


def test_oversize_content_is_refused_non_retryably_without_a_request():
    port, transport = _port(max_content_chars=10)
    res = _invoke(port, content="x" * 11)
    assert (_failed(res), transport.calls) == ((FAILED, "llm_content_too_large", False), [])


@pytest.mark.parametrize("content", [None, "", "   "])
def test_missing_content_is_invalid(content):
    port, transport = _port()
    res = _invoke(port, content=content)
    assert (_failed(res), transport.calls) == ((FAILED, "invalid_request", False), [])


def test_unserialisable_content_is_invalid():
    port, _ = _port()
    assert _failed(_invoke(port, content={"x": object()})) == (FAILED, "invalid_request", False)


@pytest.mark.parametrize("value", [0, -5, "many", True, 1.5])
def test_a_bad_max_tokens_is_invalid(value):
    port, _ = _port()
    assert _failed(_invoke(port, max_tokens=value)) == (FAILED, "invalid_request", False)


@pytest.mark.parametrize("value", [-0.1, 2.5, "hot", True])
def test_a_bad_temperature_is_invalid(value):
    port, _ = _port()
    assert _failed(_invoke(port, temperature=value)) == (FAILED, "invalid_request", False)


@pytest.mark.parametrize(
    "params",
    [
        {"base_url": "ftp://127.0.0.1:8001"},
        {"base_url": ""},
        {"base_url": "http://"},
        {"base_url": "http://user:pw@127.0.0.1:8001"},
        {"base_url": 8001},
        {"model": ""},
        {"model": None},
        {"api_key": "literal-key"},
    ],
)
def test_an_invalid_llm_actor_is_refused_without_a_request(params):
    port, transport = _port(_store(_actor(**params)))
    res = _invoke(port)
    assert (_failed(res), transport.calls) == ((FAILED, "llm_actor_invalid", False), [])


def test_a_non_agent_actor_is_refused():
    port, _ = _port(_store(_actor(kind="service")))
    assert _failed(_invoke(port)) == (FAILED, "actor_kind_mismatch", False)


def test_a_missing_actor_is_refused():
    port, _ = _port()
    assert _failed(_invoke(port, actor="ghost")) == (FAILED, "actor_not_found", False)


def test_an_unresolvable_api_key_is_refused_and_not_echoed():
    def runner(_name):
        raise RuntimeError("secret-material")

    port, transport = _port(_store(_actor(api_key="grant:LLM_KEY")), secret_runner=runner)
    res = _invoke(port)
    assert (_failed(res), transport.calls) == ((FAILED, "secret_unresolved", False), [])


def test_errors_never_carry_the_response_body():
    port, _ = _port(transport=FakeTransport(status=400, body=b"leaky-body"))
    assert "leaky-body" not in _invoke(port).error


# ---- concurrency pools: through the router, like every actor-bound action port --------------


def _pooled_store(cap=1):
    fixer = _actor("qwen-spark2", kind="agent", concurrency_pool=POOL, max_concurrency=cap)
    fixer["params"]["bridge_url"] = "http://127.0.0.1:9"
    return _store(fixer, _actor(concurrency_pool=POOL, max_concurrency=4))


def _hold_pool_slot(store, key="fix-run"):
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    store.put(
        USAGE_COLLECTION,
        {
            "id": pool_doc_id(POOL),
            "pool": POOL,
            "rev": 1,
            "inflight": [{"key": key, "until": until, "actor": "qwen-spark2"}],
        },
    )


def _route_invoke(store, transport, key="news"):
    port = LlmCompletePort(store, transport=transport)
    routed = ActorRouter(store, ports={"action:llm.complete": port})(_ctx())
    return routed.invoke({"actor": "llm", "content": "No items"}, key, _deadline(), context=_ctx())


def test_a_held_pool_slot_blocks_the_summary_without_a_request():
    store, transport = _pooled_store(), FakeTransport()
    _hold_pool_slot(store)
    res = _route_invoke(store, transport)
    assert (res.outcome, transport.calls) == (BLOCKED, [])


def test_the_pool_cap_is_the_smallest_member_cap():
    store, transport = _pooled_store(cap=2), FakeTransport()
    _hold_pool_slot(store)
    assert _route_invoke(store, transport).outcome == COMPLETED


def test_the_summary_runs_once_the_pool_slot_is_free():
    store, transport = _pooled_store(), FakeTransport()
    _hold_pool_slot(store)
    _route_invoke(store, transport)
    store.put(USAGE_COLLECTION, {"id": pool_doc_id(POOL), "pool": POOL, "rev": 2, "inflight": []})
    assert _route_invoke(store, transport).outcome == COMPLETED


def test_a_completed_summary_frees_its_pool_slot():
    store = _pooled_store()
    _route_invoke(store, FakeTransport())
    assert store.get(USAGE_COLLECTION, pool_doc_id(POOL))["inflight"] == []


def test_the_summary_tokens_count_against_the_actor():
    store = _pooled_store()
    _route_invoke(store, FakeTransport())
    assert store.get(USAGE_COLLECTION, "llm")["tokens"] == 15


# ---- the default transport against a local server -----------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_POST(self):  # noqa: N802 - http.server naming
        if self.path != COMPLETIONS:
            self.send_error(404)
            return
        length = int(self.headers["Content-Length"])
        _Handler.seen.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
        body = _reply()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def local_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Handler.seen.clear()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_default_transport_completes_against_a_local_server(local_server):
    port = LlmCompletePort(_store(_actor(base_url=local_server)))
    assert _invoke(port).output["text"] == "- [A](https://a.example/1)"


def test_the_default_transport_posts_to_the_completions_path(local_server):
    port = LlmCompletePort(_store(_actor(base_url=local_server)))
    _invoke(port)
    assert _Handler.seen[0]["path"] == COMPLETIONS


def test_the_default_transport_reports_a_closed_port_as_unavailable():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed = sock.getsockname()[1]
    port = LlmCompletePort(_store(_actor(base_url=f"http://127.0.0.1:{closed}")))
    assert _failed(_invoke(port)) == (FAILED, "llm_unavailable", True)


def test_the_default_transport_returns_an_error_status_rather_than_raising(local_server):
    status, _ = urllib_transport(local_server + "/missing", b"{}", {}, 5.0)
    assert status == 404
