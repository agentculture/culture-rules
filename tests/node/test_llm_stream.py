"""llm.complete streams (d7, operator 2026-11-02: "Always use stream").

The request asks for ``stream: true`` with ``stream_options.include_usage`` and the port reads
the server-sent events: the content deltas joined in order are the text (reasoning deltas are
not), the model comes from the chunks and the usage from the usage chunk. A plain JSON reply
still parses, a stream without ``[DONE]`` fails ``llm_bad_response`` like a truncated JSON
body, and a non-2xx answer maps as before.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions import llm as llm_mod
from culture_rules.node.actions.llm import (
    MAX_RESPONSE_BYTES,
    MAX_TOKENS_CAP,
    LlmCompletePort,
    urllib_transport,
)
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

LOCAL = "http://127.0.0.1:8001"
MODEL = "qwen-test"
SERVED = "qwen-served"
COMPLETIONS = "/v1/chat/completions"
USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
DONE = b"data: [DONE]\n\n"


def _event(chunk) -> bytes:
    return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def _delta(content=None, reasoning=None, reasoning_content=None, model=SERVED):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning"] = reasoning
    if reasoning_content is not None:
        delta["reasoning_content"] = reasoning_content
    return {"model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}


def _sse(*contents, reasoning=None, usage=USAGE, done=True) -> bytes:
    chunks = [_delta(content="", model=SERVED)]
    if reasoning is not None:
        chunks.append(_delta(reasoning=reasoning, reasoning_content=reasoning))
    chunks.extend(_delta(content=c) for c in contents)
    if usage is not None:
        chunks.append({"model": SERVED, "choices": [], "usage": usage})
    return b"".join(_event(c) for c in chunks) + (DONE if done else b"")


class FakeTransport:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = _sse("Hello") if body is None else body
        self.calls: list[dict] = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"json": json.loads(data), "headers": headers, "timeout": timeout})
        return self.status, self.body

    @property
    def sent(self):
        return self.calls[-1]["json"]


def _store(**params):
    store = MemoryStore()
    base = {"base_url": LOCAL, "model": MODEL, **params}
    store.put(
        ACTORS_COLLECTION,
        {"id": "llm", "name": "llm", "kind": "agent", "enabled": True, "params": base},
    )
    return store


def _ctx():
    return InvocationContext(
        run_id="run1",
        step_id="summarise",
        kind="action",
        host="h1",
        actor="llm",
        config={"kind": "llm.complete", "params": {"actor": "llm"}},
    )


def _invoke(port, seconds=120, **params):
    params.setdefault("actor", "llm")
    params.setdefault("content", "1. A https://a.example/1")
    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    return port.invoke(params, "k1", deadline, context=_ctx())


def _port(body=None, status=200):
    transport = FakeTransport(status=status, body=body)
    return LlmCompletePort(_store(), transport=transport), transport


def _failed(res):
    return (res.outcome, res.error.split(":", 1)[0], res.retryable)


# ---- the request ---------------------------------------------------------------------------


def test_the_request_asks_for_a_stream():
    port, transport = _port()
    _invoke(port)
    assert transport.sent["stream"] is True


def test_the_request_asks_for_usage_in_the_stream():
    port, transport = _port()
    _invoke(port)
    assert transport.sent["stream_options"] == {"include_usage": True}


def test_the_request_accepts_an_event_stream():
    port, transport = _port()
    _invoke(port)
    assert "text/event-stream" in transport.calls[0]["headers"]["Accept"]


# ---- the reply -----------------------------------------------------------------------------


def _streamed():
    port, _ = _port(_sse("- [A](https://a", ".example/1)", " and more", reasoning="let me see"))
    return _invoke(port)


def test_a_stream_completes():
    assert _streamed().outcome == COMPLETED


def test_the_text_is_the_content_deltas_joined_in_order():
    assert _streamed().output["text"] == "- [A](https://a.example/1) and more"


def test_reasoning_deltas_are_not_part_of_the_text():
    assert "let me see" not in _streamed().output["text"]


def test_the_model_comes_from_the_chunks():
    assert _streamed().output["model"] == SERVED


def test_the_usage_comes_from_the_usage_chunk():
    assert _streamed().output["usage"] == USAGE


def test_a_leading_think_block_in_the_content_is_dropped():
    port, _ = _port(_sse("<think>hm", "m</think>\n\n", "No results."))
    assert _invoke(port).output["text"] == "No results."


def test_a_stream_without_a_usage_chunk_has_empty_usage():
    port, _ = _port(_sse("x", usage=None))
    assert _invoke(port).output["usage"] == {}


def test_a_stream_without_a_model_falls_back_to_the_actor_model():
    body = _event({"choices": [{"index": 0, "delta": {"content": "x"}}]}) + DONE
    port, _ = _port(body)
    assert _invoke(port).output["model"] == MODEL


def test_crlf_line_ends_and_comments_are_read():
    body = (b": keep-alive\r\n\r\n" + _sse("a", "b")).replace(b"\n\n", b"\r\n\r\n")
    port, _ = _port(body)
    assert _invoke(port).output["text"] == "ab"


def test_a_raw_line_separator_inside_a_chunk_stays_in_its_text():
    chunk = json.dumps(_delta(content="a\u2028b"), ensure_ascii=False).encode()
    port, _ = _port(b"data: " + chunk + b"\n\n" + DONE)
    assert _invoke(port).output["text"] == "a\u2028b"


def test_the_recording_brief_parses_a_streamed_answer():
    answer = {"summary": "A call.", "word": "call", "facts": [], "action_items": []}
    text = json.dumps(answer)
    port, _ = _port(_sse(text[:10], text[10:]))
    assert _invoke(port, brief="recording").output["answer"]["word"] == "call"


def test_a_plain_json_reply_still_parses():
    body = {"model": SERVED, "choices": [{"message": {"content": "plain"}}], "usage": USAGE}
    port, _ = _port(json.dumps(body).encode())
    assert _invoke(port).output["text"] == "plain"


# ---- failures ------------------------------------------------------------------------------


def test_a_stream_without_done_is_llm_bad_response():
    port, _ = _port(_sse("half", done=False))
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


def test_a_stream_with_no_content_delta_is_llm_bad_response():
    body = _event({"model": SERVED, "choices": [{"index": 0, "delta": {"role": "x"}}]}) + DONE
    port, _ = _port(body)
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


def test_a_stream_with_only_reasoning_is_llm_bad_response():
    body = _event(_delta(reasoning="thinking only")) + DONE
    port, _ = _port(body)
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


def test_a_chunk_that_is_not_json_is_llm_bad_response():
    port, _ = _port(b"data: {not json\n\n" + DONE)
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


def test_a_chunk_that_is_not_an_object_is_llm_bad_response():
    port, _ = _port(b"data: [1]\n\n" + DONE)
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


def test_an_error_event_in_the_stream_is_llm_bad_response():
    body = _sse("partial", done=False) + _event({"error": {"message": "boom"}}) + DONE
    port, _ = _port(body)
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


def test_a_stream_over_the_byte_cap_is_llm_bad_response():
    port, _ = _port(_sse("x" * (MAX_RESPONSE_BYTES + 1)))
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


def _vllm_chunk(content=None, reasoning=None):
    """One chunk as vLLM sends it: ids, timestamps and nulls make it ~250-300 bytes."""
    delta = {"content": content} if reasoning is None else {"reasoning_content": reasoning}
    return {
        "id": "chatcmpl-0123456789abcdef0123456789abcdef",
        "object": "chat.completion.chunk",
        "created": 1793577600,
        "model": SERVED,
        "system_fingerprint": None,
        "choices": [
            {"index": 0, "delta": delta, "logprobs": None, "finish_reason": None, "stop": None}
        ],
    }


def test_a_stream_of_max_tokens_cap_tokens_fits_the_byte_cap():
    half = MAX_TOKENS_CAP // 2
    chunks = [_vllm_chunk(reasoning="hm") for _ in range(half)]
    chunks += [_vllm_chunk(content="w ") for _ in range(MAX_TOKENS_CAP - half)]
    body = b"".join(_event(c) for c in chunks) + DONE
    port, _ = _port(body)
    assert _invoke(port, max_tokens=MAX_TOKENS_CAP).outcome == COMPLETED


def test_the_byte_cap_holds_a_kib_per_capped_token():
    assert MAX_RESPONSE_BYTES >= MAX_TOKENS_CAP * 1024


def _error_event(error) -> bytes:
    return _sse("partial", done=False) + _event({"error": error}) + DONE


@pytest.mark.parametrize(
    "error",
    [
        {"message": "busy", "type": "InternalServerError", "code": 500},
        {"message": "busy", "code": 503},
        {"message": "slow down", "code": 429},
        {"message": "busy", "status": "502"},
        {"message": "slow down", "code": "rate_limit_exceeded", "status": 429},
        {"message": "x", "type": "overloaded_error"},
        {"message": "The server is overloaded"},
        {"message": "Service Unavailable"},
        {"message": "the request timed out"},
        {"message": "x", "type": "timeout"},
        "overloaded",
    ],
)
def test_a_transient_error_event_is_retryable_llm_unavailable(error):
    port, _ = _port(_error_event(error))
    assert _failed(_invoke(port)) == (FAILED, "llm_unavailable", True)


@pytest.mark.parametrize(
    "error",
    [
        {"message": "bad", "type": "BadRequestError", "code": 400},
        {"message": "context length exceeded", "code": "context_length_exceeded"},
        "boom",
    ],
)
def test_another_error_event_stays_llm_bad_response(error):
    port, _ = _port(_error_event(error))
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


@pytest.mark.parametrize(
    "error",
    [
        {"message": "model foo unavailable"},
        {"message": "x", "type": "model_unavailable"},
        "feature unavailable",
    ],
)
def test_a_bare_unavailable_about_a_model_is_not_transient(error):
    port, _ = _port(_error_event(error))
    assert _failed(_invoke(port)) == (FAILED, "llm_bad_response", False)


@pytest.mark.parametrize(
    "error",
    [
        {"message": "x", "type": "server_overloaded"},
        {"message": "The service is temporarily unavailable"},
        {"message": "Timeout while waiting for the engine"},
    ],
)
def test_load_wording_is_still_transient(error):
    port, _ = _port(_error_event(error))
    assert _failed(_invoke(port)) == (FAILED, "llm_unavailable", True)


def _with_null_error(chunk):
    return {**chunk, "error": None}


def test_a_chunk_with_a_null_error_is_not_an_error():
    chunks = [_with_null_error(_delta(content="Hi")), {"model": SERVED, "choices": []}]
    body = b"".join(_event(c) for c in chunks) + DONE
    port, _ = _port(body)
    assert _invoke(port).output["text"] == "Hi"


def test_event_id_and_retry_fields_before_data_are_still_a_stream():
    body = b"event: message\nid: 1\nretry: 1000\n" + _sse("a", "b")
    port, _ = _port(body)
    assert _invoke(port).output["text"] == "ab"


def test_a_bare_field_name_line_before_data_is_still_a_stream():
    port, _ = _port(b"event\n" + _sse("a"))
    assert _invoke(port).output["text"] == "a"


def test_a_json_body_with_an_id_key_is_not_a_stream():
    body = {"id": "x", "model": SERVED, "choices": [{"message": {"content": "plain"}}]}
    port, _ = _port(json.dumps(body, indent=1).encode())
    assert _invoke(port).output["text"] == "plain"


def test_the_body_is_split_into_lines_once(monkeypatch):
    calls = []
    real = llm_mod._event_lines

    def counting(body):
        calls.append(1)
        return real(body)

    monkeypatch.setattr(llm_mod, "_event_lines", counting)
    port, _ = _port()
    _invoke(port)
    assert len(calls) == 1


def test_a_5xx_is_still_retryable_llm_unavailable():
    port, _ = _port(b'{"error": {"message": "busy"}}', status=503)
    assert _failed(_invoke(port)) == (FAILED, "llm_unavailable", True)


def test_a_400_is_still_llm_rejected():
    port, _ = _port(b'{"error": {"message": "bad"}}', status=400)
    assert _failed(_invoke(port)) == (FAILED, "llm_rejected", False)


def test_a_streamed_completion_replays_without_a_second_request():
    port, transport = _port()
    _invoke(port)
    _invoke(port)
    assert len(transport.calls) == 1


# ---- the default transport against a local streaming server ---------------------------------


class _StreamHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    pieces: list[bytes] = []
    pause = 0.0
    first_pause = 0.0

    def do_POST(self):  # noqa: N802 - http.server naming
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            time.sleep(type(self).first_pause)
            for piece in type(self).pieces:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
                self.wfile.flush()
                time.sleep(type(self).pause)
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass  # the client gave up (the trickle test)

    def log_message(self, *_args):
        pass


@pytest.fixture
def stream_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StreamHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_default_transport_reads_a_chunked_stream(stream_server, monkeypatch):
    monkeypatch.setattr(
        _StreamHandler, "pieces", [_event(_delta(content=c)) for c in ("Hel", "lo")] + [DONE]
    )
    monkeypatch.setattr(_StreamHandler, "pause", 0.0)
    monkeypatch.setattr(_StreamHandler, "first_pause", 0.0)
    port = LlmCompletePort(_store(base_url=stream_server))
    assert _invoke(port).output["text"] == "Hello"


def test_the_default_transport_bounds_the_whole_stream_by_the_timeout(stream_server, monkeypatch):
    monkeypatch.setattr(_StreamHandler, "pieces", [_event(_delta(content="x"))] * 40 + [DONE])
    monkeypatch.setattr(_StreamHandler, "pause", 0.1)
    monkeypatch.setattr(_StreamHandler, "first_pause", 0.0)
    with pytest.raises(TimeoutError):
        urllib_transport(stream_server + COMPLETIONS, b"{}", {}, 0.5)


def test_a_stream_stalling_late_still_ends_within_the_timeout(stream_server, monkeypatch):
    # the first chunk lands at 0.8 s of a 1.0 s timeout, then the stream stalls for 3 s:
    # the stalled read may wait only the 0.2 s left, not a whole socket timeout
    monkeypatch.setattr(
        _StreamHandler, "pieces", [_event(_delta(content="x")), _event(_delta(content="y"))]
    )
    monkeypatch.setattr(_StreamHandler, "pause", 3.0)
    monkeypatch.setattr(_StreamHandler, "first_pause", 0.8)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        urllib_transport(stream_server + COMPLETIONS, b"{}", {}, 1.0)
    assert time.monotonic() - started < 1.3
