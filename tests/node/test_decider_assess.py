"""decider.assess action port: one POST to the lobes gateway's /v1/systemone, no side effect."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.decider import (
    MAX_REQUEST_CHARS,
    DeciderAssessPort,
    urllib_transport,
)
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

BASE = "http://127.0.0.1:8001"
GRANT = "grant:LOBES_GATEWAY_API_KEY"
STATE_TEXT = "Disk is 91 percent full; the nightly job needs 4 GiB. UNIQUE-STATE-MARKER"

QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": "What should tonight's job do?",
        "criteria": {"run_as_usual": "run it", "skip_tonight": "skip it"},
    },
    "disk_ok": {"type": "noul", "instructions": "Is the disk margin enough?"},
    "risk": {
        "type": "score",
        "instructions": "How risky?",
        "criteria": {"low": "little", "medium": "some", "high": "much"},
    },
}

ANSWERS = {
    "action": {
        "type": "choice",
        "choice": "run_as_usual",
        "confidence": 0.9841,
        "probabilities": {"run_as_usual": 0.9841, "skip_tonight": 0.0159},
    },
    "disk_ok": {"type": "noul", "noul": 0.9808},
    "risk": {
        "type": "score",
        "score": 0.0446,
        "confidence": 0.9679,
        "legend": {"0": "low", "1": "medium", "2": "high"},
        "probabilities": {"0": 0.9679, "1": 0.0196, "2": 0.0125},
    },
}


def _reply(answers=None, usage=None):
    body = {
        "model": "clef",
        "answers": ANSWERS if answers is None else answers,
        "usage": {"input_tokens": 339, "output_tokens": 0} if usage is None else usage,
    }
    return json.dumps(body).encode()


class FakeTransport:
    def __init__(self, status=200, body=None, headers=None, exc=None):
        self.status = status
        self.body = _reply() if body is None else body
        self.headers = headers or {}
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append(
            {"url": url, "raw": data, "json": json.loads(data), "headers": headers, "t": timeout}
        )
        if self.exc is not None:
            raise self.exc
        return self.status, self.headers, self.body

    @property
    def sent(self):
        return self.calls[-1]["json"]


def _actor(actor_id="decider", **params):
    base = {"base_url": BASE, "api_key": GRANT, "model": "clef"}
    base.update(params)
    return {"id": actor_id, "name": actor_id, "kind": "agent", "enabled": True, "params": base}


def _store(*docs):
    store = MemoryStore()
    for doc in docs or (_actor(),):
        store.put(ACTORS_COLLECTION, doc)
    return store


def _ctx(actor="decider"):
    return InvocationContext(
        run_id="r1",
        step_id="assess",
        kind="action",
        host="h1",
        actor=actor,
        config={"kind": "decider.assess", "params": {"actor": actor}},
    )


def _port(store=None, transport=None, **kw):
    transport = transport or FakeTransport()
    kw.setdefault("secret_runner", lambda name: f"value-of-{name}")
    return DeciderAssessPort(store or _store(), transport=transport, **kw), transport


def _invoke(port, key="k1", seconds=120, **params):
    params.setdefault("actor", "decider")
    params.setdefault("state", STATE_TEXT)
    params.setdefault("questions", json.loads(json.dumps(QUESTIONS)))
    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    return port.invoke(params, key, deadline, context=_ctx(params["actor"]))


# ---- the request ----------------------------------------------------------------------


def test_posts_exactly_model_state_questions_to_systemone():
    port, transport = _port()
    _invoke(port)
    assert transport.calls[0]["url"] == BASE + "/v1/systemone"
    assert transport.sent == {"model": "clef", "state": STATE_TEXT, "questions": QUESTIONS}


def test_a_base_url_ending_in_v1_is_not_doubled():
    port, transport = _port(_store(_actor(base_url=BASE + "/v1/")))
    _invoke(port)
    assert transport.calls[0]["url"] == BASE + "/v1/systemone"


def test_bearer_comes_from_the_grant_and_the_domain_header_defaults_to_general():
    port, transport = _port()
    _invoke(port)
    headers = transport.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer value-of-LOBES_GATEWAY_API_KEY"
    assert headers["X-Lobes-Model"] == "decider:general"


def test_the_domain_header_follows_the_actor_domain():
    port, transport = _port(_store(_actor(domain="strands-general")))
    _invoke(port)
    assert transport.calls[0]["headers"]["X-Lobes-Model"] == "decider:strands-general"


def test_a_step_domain_param_is_ignored():
    port, transport = _port()
    _invoke(port, domain="other", model="gpt")
    assert transport.calls[0]["headers"]["X-Lobes-Model"] == "decider:general"
    assert transport.sent["model"] == "clef"


@pytest.mark.parametrize("domain", ["Bad", "a b", "-x", "", "a/b", 5])
def test_a_malformed_actor_domain_is_refused_before_a_request(domain):
    port, transport = _port(_store(_actor(domain=domain)))
    result = _invoke(port)
    assert result.outcome == FAILED
    assert result.error.startswith("decider_actor_invalid")
    assert transport.calls == []


def test_one_request_per_invocation():
    port, transport = _port()
    _invoke(port)
    assert len(transport.calls) == 1


def test_actor_falls_back_to_the_context_actor():
    port, transport = _port()
    deadline = datetime.now(UTC) + timedelta(seconds=60)
    res = port.invoke({"state": "s", "questions": QUESTIONS}, "k", deadline, context=_ctx())
    assert res.outcome == COMPLETED
    assert len(transport.calls) == 1


def test_a_non_grant_api_key_is_refused():
    port, transport = _port(_store(_actor(api_key="plain-text")))
    assert _invoke(port).error.startswith("decider_actor_invalid")
    assert transport.calls == []


def test_a_non_agent_actor_is_refused():
    doc = _actor()
    doc["kind"] = "human"
    port, transport = _port(_store(doc))
    assert _invoke(port).error.startswith("actor_kind_mismatch")
    assert transport.calls == []


def test_an_unresolvable_grant_fails_secret_unresolved():
    from culture_rules.actors.secrets import SecretError

    def boom(_name):
        raise SecretError("nope")

    port, transport = _port(secret_runner=boom)
    assert _invoke(port).error.startswith("secret_unresolved")
    assert transport.calls == []


# ---- invalid_request before any request --------------------------------------------------


def _q(**over):
    q = {"type": "noul", "instructions": "Is it fine?"}
    q.update(over)
    return q


def _two():
    return {"a": "first", "b": "second"}


BAD_INPUTS = {
    "empty state": {"state": "   "},
    "missing state": {"state": None},
    "non-string state": {"state": 5},
    "missing questions": {"questions": None},
    "empty questions": {"questions": {}},
    "questions a list": {"questions": [_q()]},
    "key upper": {"questions": {"Bad": _q()}},
    "key digit first": {"questions": {"1a": _q()}},
    "key dash": {"questions": {"a-b": _q()}},
    "key empty": {"questions": {"": _q()}},
    "bad type": {"questions": {"a": _q(type="rank")}},
    "missing type": {"questions": {"a": {"instructions": "x"}}},
    "missing instructions": {"questions": {"a": {"type": "noul"}}},
    "blank instructions": {"questions": {"a": _q(instructions=" ")}},
    "choice no criteria": {"questions": {"a": _q(type="choice")}},
    "choice one criterion": {"questions": {"a": _q(type="choice", criteria={"a": "x"})}},
    "score one criterion": {"questions": {"a": _q(type="score", criteria={"a": "x"})}},
    "criteria a list": {"questions": {"a": _q(type="choice", criteria=["a", "b"])}},
    "criterion not text": {"questions": {"a": _q(type="choice", criteria={"a": "x", "b": 3})}},
    "question not a mapping": {"questions": {"a": "yes"}},
    "images on the step": {"images": ["x"]},
    "videos on the step": {"videos": ["x"]},
    "images in a question": {"questions": {"a": _q(images=["x"])}},
    "videos in a question": {"questions": {"a": _q(videos=["x"])}},
    "not_after malformed": {"not_after": "tomorrow"},
}


@pytest.mark.parametrize("name", sorted(BAD_INPUTS))
def test_invalid_requests_fail_before_any_request(name):
    port, transport = _port()
    result = _invoke(port, **BAD_INPUTS[name])
    assert result.outcome == FAILED
    assert result.error.startswith("invalid_request")
    assert result.retryable is False
    assert transport.calls == []


def test_a_request_over_the_size_cap_is_refused():
    port, transport = _port()
    result = _invoke(port, state="x" * (MAX_REQUEST_CHARS + 1))
    assert result.error.startswith("invalid_request")
    assert transport.calls == []


def test_a_request_near_the_size_cap_is_sent():
    port, transport = _port()
    assert _invoke(port, state="x" * (MAX_REQUEST_CHARS // 2)).outcome == COMPLETED


def test_a_noul_needs_no_criteria():
    port, _ = _port()
    result = _invoke(port, questions={"ok": _q()})
    assert result.outcome == FAILED  # the reply lacks "ok": bad response, not a request error
    assert result.error.startswith("decider_bad_response")


NESTED = b"[" * 50_000 + b"]" * 50_000  # under the 1 MiB reply cap, deeper than json can parse


def test_a_deeply_nested_reply_fails_bad_response():
    port, _ = _port(transport=FakeTransport(body=NESTED))
    result = _invoke(port)
    assert result.outcome == FAILED
    assert result.error.startswith("decider_bad_response")


def test_a_deeply_nested_error_reply_still_maps_its_status():
    port, _ = _port(transport=FakeTransport(status=404, body=NESTED))  # 4xx reads the code
    result = _invoke(port)
    assert result.outcome == FAILED
    assert result.error == "decider_rejected: status 404"


# ---- outputs ------------------------------------------------------------------------------


def test_outputs_carry_answers_picks_host_domain_usage():
    port, _ = _port()
    out = _invoke(port).output
    assert out["answers"] == ANSWERS
    assert out["picks"] == {"action": "run_as_usual", "disk_ok": True, "risk": "low"}
    assert out["host"] == "local"
    assert out["domain"] == "general"
    assert out["usage"] == {"input_tokens": 339, "output_tokens": 0, "total_tokens": 339}


def test_the_host_is_the_mesh_member_header_case_insensitively():
    port, _ = _port(transport=FakeTransport(headers={"x-lobes-mesh-member": "orin"}))
    assert _invoke(port).output["host"] == "orin"


@pytest.mark.parametrize(
    "noul,pick", [(0.5, True), (0.4999, False), (0.0, False), (1.0, True), (0.9808, True)]
)
def test_noul_pick_is_at_least_one_half(noul, pick):
    answers = {"ok": {"type": "noul", "noul": noul}}
    port, _ = _port(transport=FakeTransport(body=_reply(answers)))
    assert _invoke(port, questions={"ok": _q()}).output["picks"] == {"ok": pick}


def _score_answers(probs, legend=None):
    legend = legend or {"0": "low", "1": "medium", "2": "high"}
    return {"risk": {"type": "score", "score": 1.0, "legend": legend, "probabilities": probs}}


def _score_pick(probs, legend=None):
    port, _ = _port(transport=FakeTransport(body=_reply(_score_answers(probs, legend))))
    q = {"risk": _q(type="score", criteria={"low": "a", "medium": "b", "high": "c"})}
    return _invoke(port, questions=q).output["picks"]["risk"]


def test_score_pick_is_the_legend_of_the_most_probable_index():
    assert _score_pick({"0": 0.1, "1": 0.2, "2": 0.7}) == "high"


def test_score_ties_go_to_the_lower_index():
    assert _score_pick({"0": 0.1, "1": 0.45, "2": 0.45}) == "medium"


def test_score_indexes_compare_numerically_not_as_text():
    legend = {str(i): f"c{i}" for i in range(11)}
    probs = {str(i): 0.0 for i in range(11)}
    probs["2"] = probs["10"] = 0.5
    port, _ = _port(transport=FakeTransport(body=_reply(_score_answers(probs, legend))))
    q = {"risk": _q(type="score", criteria={f"c{i}": "x" for i in range(11)})}
    assert _invoke(port, questions=q).output["picks"]["risk"] == "c2"


def test_usage_total_is_input_plus_output_as_integers():
    body = _reply(usage={"input_tokens": 12, "output_tokens": 3})
    port, _ = _port(transport=FakeTransport(body=body))
    usage = _invoke(port).output["usage"]
    assert usage == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    assert all(type(v) is int for v in usage.values())


# ---- statuses -----------------------------------------------------------------------------


def _err(code=None):
    return json.dumps({"error": {"code": code, "message": "SECRET-DETAIL"}}).encode()


@pytest.mark.parametrize("status", [500, 502, 503, 504, 408, 429])
def test_unavailable_statuses_are_retryable(status):
    port, _ = _port(transport=FakeTransport(status=status, body=_err("backend_unavailable")))
    res = _invoke(port)
    assert res.outcome == FAILED
    assert res.retryable is True
    assert res.error.startswith("decider_unavailable")


@pytest.mark.parametrize("exc", [ConnectionRefusedError(), TimeoutError(), OSError("dns")])
def test_network_errors_are_unavailable_and_retryable(exc):
    port, _ = _port(transport=FakeTransport(exc=exc))
    res = _invoke(port)
    assert res.error.startswith("decider_unavailable")
    assert res.retryable is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 301])
def test_other_statuses_are_rejected_with_status_and_code_not_retryable(status):
    port, _ = _port(transport=FakeTransport(status=status, body=_err("role_infeasible")))
    res = _invoke(port)
    assert res.retryable is False
    assert res.error == f"decider_rejected: status {status} (role_infeasible)"


def test_a_rejection_without_a_code_names_only_the_status():
    port, _ = _port(transport=FakeTransport(status=422, body=b'{"detail": "private text"}'))
    assert _invoke(port).error == "decider_rejected: status 422"


def test_a_rejection_with_non_json_body_names_only_the_status():
    port, _ = _port(transport=FakeTransport(status=404, body=b"<html>nope</html>"))
    assert _invoke(port).error == "decider_rejected: status 404"


def test_an_odd_error_code_is_not_echoed():
    body = json.dumps({"error": {"code": "has spaces and UNIQUE-STATE-MARKER"}}).encode()
    port, _ = _port(transport=FakeTransport(status=404, body=body))
    assert _invoke(port).error == "decider_rejected: status 404"


def _without(answers, *path_to_drop):
    out = json.loads(json.dumps(answers))
    node = out
    for p in path_to_drop[:-1]:
        node = node[p]
    del node[path_to_drop[-1]]
    return out


BAD_REPLIES = {
    "not json": b"not json",
    "not an object": b"[1]",
    "no answers": json.dumps({"usage": {}}).encode(),
    "answers not a mapping": json.dumps({"answers": []}).encode(),
    "missing question": _reply(_without(ANSWERS, "risk")),
    "choice field missing": _reply(_without(ANSWERS, "action", "choice")),
    "choice not an option": _reply(
        {**ANSWERS, "action": {**ANSWERS["action"], "choice": "invented"}}
    ),
    "noul missing": _reply(_without(ANSWERS, "disk_ok", "noul")),
    "noul not a number": _reply({**ANSWERS, "disk_ok": {"type": "noul", "noul": "high"}}),
    "noul out of range": _reply({**ANSWERS, "disk_ok": {"type": "noul", "noul": 1.5}}),
    "score probabilities missing": _reply(_without(ANSWERS, "risk", "probabilities")),
    "score legend missing": _reply(_without(ANSWERS, "risk", "legend")),
    "score legend short": _reply(
        {**ANSWERS, "risk": {**ANSWERS["risk"], "legend": {"0": "low", "1": "medium"}}}
    ),
    "type mismatch": _reply({**ANSWERS, "disk_ok": ANSWERS["action"]}),
    "answer not a mapping": _reply({**ANSWERS, "disk_ok": 0.9}),
}


@pytest.mark.parametrize("name", sorted(BAD_REPLIES))
def test_a_bad_reply_fails_bad_response_never_partial_picks(name):
    port, _ = _port(transport=FakeTransport(body=BAD_REPLIES[name]))
    res = _invoke(port)
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("decider_bad_response")
    assert not res.output


# ---- nothing leaks --------------------------------------------------------------------------


def test_no_failure_string_carries_the_state_the_body_or_the_key():
    leaks = ("UNIQUE-STATE-MARKER", "SECRET-DETAIL", "value-of-LOBES_GATEWAY_API_KEY")
    cases = [
        FakeTransport(status=500, body=_err("backend_unavailable")),
        FakeTransport(status=404, body=_err("model_not_found")),
        FakeTransport(body=b"SECRET-DETAIL not json"),
        FakeTransport(exc=OSError("value-of-LOBES_GATEWAY_API_KEY UNIQUE-STATE-MARKER")),
    ]
    for transport in cases:
        port, _ = _port(transport=transport)
        res = _invoke(port)
        assert res.outcome == FAILED
        assert not any(leak in res.error for leak in leaks), res.error


def test_the_port_does_not_log(caplog):
    port, _ = _port()
    with caplog.at_level("DEBUG"):
        _invoke(port)
    assert caplog.records == []


# ---- not_after, refuse_before_admit, replay -------------------------------------------------

T0 = datetime(2026, 10, 24, 3, 30, tzinfo=UTC)


class ManualClock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def slow_secret(self, seconds):
        def runner(_name):
            self.now += timedelta(seconds=seconds)
            return "k"

        return runner


def _at(port, clock, not_after_s, key="kc"):
    params = {
        "actor": "decider",
        "state": "s",
        "questions": QUESTIONS,
        "not_after": (clock.now + timedelta(seconds=not_after_s)).isoformat(),
    }
    return port.invoke(params, key, clock.now + timedelta(seconds=120), context=_ctx())


def test_a_passed_not_after_fails_deadline_without_a_request():
    clock = ManualClock()
    port, transport = _port(clock=clock)
    res = _at(port, clock, -1)
    assert res.error.startswith("decider_deadline")
    assert res.retryable is False
    assert transport.calls == []


def test_refuse_before_admit_answers_a_passed_not_after_and_nothing_else():
    clock = ManualClock()
    port, _ = _port(clock=clock)
    past = (clock.now - timedelta(seconds=1)).isoformat()
    ahead = (clock.now + timedelta(seconds=60)).isoformat()
    assert port.refuse_before_admit({"not_after": past}).error.startswith("decider_deadline")
    assert port.refuse_before_admit({"not_after": ahead}) is None
    assert port.refuse_before_admit({}) is None


def test_a_secret_lookup_past_not_after_sends_no_request():
    clock = ManualClock()
    port, transport = _port(clock=clock, secret_runner=clock.slow_secret(20))
    res = _at(port, clock, 10)
    assert res.error.startswith("decider_deadline")
    assert transport.calls == []


def test_the_timeout_is_what_remains_after_the_secret_lookup():
    clock = ManualClock()
    port, transport = _port(clock=clock, secret_runner=clock.slow_secret(4))
    _at(port, clock, 10)
    assert transport.calls[0]["t"] == pytest.approx(6.0)


def test_the_timeout_is_capped():
    port, transport = _port()
    _invoke(port, seconds=100000)
    assert transport.calls[0]["t"] <= 120.0


def test_a_passed_attempt_deadline_is_unavailable_and_retryable():
    port, transport = _port()
    res = _invoke(port, seconds=-5)
    assert res.error.startswith("decider_unavailable")
    assert res.retryable is True
    assert transport.calls == []


def test_a_completed_key_replays_without_a_second_request_even_after_not_after():
    clock = ManualClock()
    port, transport = _port(clock=clock)
    first = _at(port, clock, 60)
    assert first.outcome == COMPLETED
    clock.now += timedelta(seconds=600)
    again = _at(port, clock, -540)
    assert again.outcome == COMPLETED
    assert again.output == first.output
    assert len(transport.calls) == 1
    assert port.replay("kc") is not None


def test_a_failed_key_is_not_replayed():
    port, transport = _port(transport=FakeTransport(status=500))
    _invoke(port)
    assert port.replay("k1") is None
    _invoke(port)
    assert len(transport.calls) == 2


def test_the_ledger_is_bounded(monkeypatch):
    from culture_rules.node.actions import decider as mod

    monkeypatch.setattr(mod, "LEDGER_SIZE", 2)
    port, _ = _port()
    for key in ("k1", "k2", "k3"):
        _invoke(port, key=key)
    assert port.replay("k1") is None
    assert port.replay("k3") is not None


# ---- the model rule -----------------------------------------------------------------------


def test_fails_model_taken_when_a_senior_endpoint_actor_holds_clef():
    other = _actor("a-holder", base_url="http://127.0.0.1:8002")
    port, transport = _port(_store(_actor("decider"), other))
    res = _invoke(port)
    assert res.error == "model_taken: a-holder"
    assert res.retryable is False
    assert transport.calls == []


def test_a_decider_for_another_domain_is_not_taken():
    other = _actor("a-code", base_url="http://127.0.0.1:8002", domain="code")
    port, transport = _port(_store(_actor("decider", domain="general"), other))
    assert _invoke(port).outcome == COMPLETED
    assert len(transport.calls) == 1


def test_a_second_decider_for_the_same_domain_is_taken():
    other = _actor("a-holder", base_url="http://127.0.0.1:8002", domain="general")
    port, transport = _port(_store(_actor("decider", domain="general"), other))
    assert _invoke(port).error == "model_taken: a-holder"
    assert transport.calls == []


def test_the_senior_actor_keeps_serving():
    port, _ = _port(_store(_actor("a-decider"), _actor("zz-newer")))
    assert _invoke(port, actor="a-decider").outcome == COMPLETED


def test_a_missing_actor_fails_without_a_request():
    port, transport = _port()
    assert _invoke(port, actor="ghost").error.startswith("actor_not_found")
    assert transport.calls == []


@pytest.mark.parametrize(
    "url", ["", "ftp://h", "http://u:p@h", "http://h?x=1", "http://h:99999999", None]
)
def test_a_bad_base_url_is_refused(url):
    port, transport = _port(_store(_actor(base_url=url)))
    assert _invoke(port).error.startswith("decider_actor_invalid")
    assert transport.calls == []


# ---- the default transport ------------------------------------------------------------------


def test_the_default_transport_returns_status_headers_body_and_does_not_follow_redirects():
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("X-Lobes-Mesh-Member", "orin")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        status, headers, body = urllib_transport(url + "/x", b"{}", {}, 5)
        assert (status, body) == (200, b"{}")
        assert {k.lower(): v for k, v in headers.items()}["x-lobes-mesh-member"] == "orin"
        assert urllib_transport(url + "/redir", b"{}", {}, 5)[0] == 302
    finally:
        server.shutdown()
        server.server_close()
