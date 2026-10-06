"""Async bridge agent actor (pr-fixer t9): dispatch to a cultureagent bridge for an arbitrary
repo and PR head; completion via callbacks recorded in the store and delivered to the run.

No real network: the bridge is an injected transport, or a loopback stdlib server.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.request
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from culture_rules.actors.agent import (
    BRIDGE_CALLBACK_PATH,
    BRIDGE_INVOCATIONS,
    DUPLICATE,
    EXPIRED,
    INVALID,
    RECORDED,
    UNAUTHORIZED,
    UNKNOWN,
    BridgeAgentActor,
    BridgeCallbackServer,
    BridgeUnreachable,
    ColleagueActor,
    _urllib_transport,
    record_bridge_event,
    redeliver_bridge,
    result_from_terminal,
)
from culture_rules.engine.actorport import ActorPort, InvocationContext
from culture_rules.engine.runs import Executor, step_state
from culture_rules.model.actor import Actor
from culture_rules.model.common import RetryPolicy
from culture_rules.node.actors import default_factories
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import T0, Clock, FakeActor, edge, port, rule, step, workflow

ROOT = Path(__file__).resolve().parents[2]
SHA = "0123456789abcdef0123456789abcdef01234567"
PR = {
    "repo": "https://github.com/agentculture/demo",
    "head_branch": "feature/x",
    "head_sha": SHA,
    "instruction": "Fix the failing check",
}
CALLBACK = "http://127.0.0.1:9"


class FakeBridge:
    """A transport double: records each request, answers from a script (default 202)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[dict] = []

    def __call__(self, method, url, body, headers, timeout):
        self.requests.append(
            {"method": method, "url": url, "body": json.loads(body), "headers": dict(headers)}
        )
        answer = self.answers.pop(0) if self.answers else (202, {"invocation_id": "inv-1"})
        if isinstance(answer, BaseException):
            raise answer
        status, doc = answer
        return status, json.dumps(doc).encode()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def make_actor(store, clock, bridge=None, **kw) -> BridgeAgentActor:
    kw.setdefault("callback_url", CALLBACK)
    return BridgeAgentActor(
        store,
        bridge_url="http://127.0.0.1:8765/",
        token="bridge-secret",
        resolve_secret=lambda ref: ref,
        transport=bridge if bridge is not None else FakeBridge(),
        clock=clock,
        actor_id="qwen-fixer",
        **kw,
    )


def pr_wf(**kw):
    """src (emits the PR head) -> fix (bridge agent) -> after (consumes the summary)."""
    addr = tuple(port(n, "string") for n in PR)
    fix = step(
        "fix",
        "actor_task",
        inputs=addr,
        outputs=(port("summary", "any", required=False), port("head_after", "any", False)),
        **kw,
    )
    return workflow(
        (
            step("src", "code", outputs=addr),
            fix,
            step("after", inputs=(port("summary", "any"),)),
        ),
        tuple(edge("src", n, "fix", n) for n in PR) + (edge("fix", "summary", "after", "summary"),),
    )


def executor(store, clock, bridge_actor) -> Executor:
    src = FakeActor(default=lambda inp, ctx: dict(PR) if ctx.step_id == "src" else {})
    return Executor(store, "spark", {"actor_task": bridge_actor, "*": src}, clock=clock)


def start(ex, **kw) -> str:
    run = ex.start(rule(), pr_wf(timeout_s=3600, **kw))
    ex.run_until_idle()
    return run["id"]


def invocation(store):
    (doc,) = store.find(BRIDGE_INVOCATIONS)
    return doc


def token_of(bridge: FakeBridge, n: int = -1) -> str:
    return bridge.requests[n]["body"]["callback"]["token"]


def completed_event(seq=3, **output):
    output.setdefault("summary", "fixed the lint")
    return {
        "event_id": f"evt_{seq}",
        "sequence": seq,
        "kind": "completed",
        "payload": {
            "outcome": "success",
            "output": output,
            "workspace_measured": {"head_before": SHA, "head_after": "f" * 40},
        },
    }


# ---- acceptance: accepted frees the worker; a later completed callback finishes the step ----


def test_accepted_frees_the_worker_and_a_completed_callback_finishes_the_step(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ex = executor(store, clock, actor)
    run_id = start(ex)
    assert step_state(ex.run(run_id), "fix")["status"] == "waiting"  # run_until_idle returned
    assert len(bridge.requests) == 1
    doc = invocation(store)
    assert doc["status"] == "accepted" and doc["invocation_id"] == "inv-1"

    assert record_bridge_event(store, doc["id"], token_of(bridge), completed_event()) == RECORDED
    assert redeliver_bridge(store, ex) == 1
    ex.run_until_idle()
    run = ex.run(run_id)
    assert run["status"] == "succeeded"
    fix = step_state(run, "fix")
    assert fix["outputs"]["summary"] == "fixed the lint"
    assert fix["outputs"]["head_after"] == "f" * 40
    assert step_state(run, "after")["inputs"] == {"summary": "fixed the lint"}
    assert redeliver_bridge(store, ex) == 0  # delivered once
    assert len(bridge.requests) == 1


def test_node_restart_between_accepted_and_completed_still_finishes(store, clock):
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge))
    run_id = start(ex)
    doc = invocation(store)
    del ex  # the node dies; the bridge keeps working

    # the callback lands (recorded from the store by whichever process hosts the endpoint)
    assert record_bridge_event(store, doc["id"], token_of(bridge), completed_event()) == RECORDED
    clock.advance(60)
    bridge2 = FakeBridge()
    ex2 = executor(store, clock, make_actor(store, clock, bridge2))  # a fresh node process
    assert redeliver_bridge(store, ex2) == 1
    ex2.run_until_idle()
    assert ex2.run(run_id)["status"] == "succeeded"
    assert bridge2.requests == []  # nothing re-dispatched


def test_restart_before_the_callback_then_the_callback_server_delivers(store, clock):
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge))
    run_id = start(ex)
    doc = invocation(store)
    ex2 = executor(store, clock, make_actor(store, clock, FakeBridge()))
    server = BridgeCallbackServer(store, executor=ex2, clock=clock)
    url = server.start()
    try:
        req = urllib.request.Request(
            url + BRIDGE_CALLBACK_PATH.format(id=doc["id"]),
            data=json.dumps(completed_event()).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {token_of(bridge)}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 - loopback test
            assert resp.status == 200
            assert json.loads(resp.read()) == {"status": RECORDED}
    finally:
        server.close()
    ex2.run_until_idle()
    assert ex2.run(run_id)["status"] == "succeeded"
    assert store.get(BRIDGE_INVOCATIONS, doc["id"])["pending_delivery"] is False


# ---- acceptance: the request carries repo, head_branch and head_sha from the step inputs ----


def test_request_carries_repo_head_branch_and_head_sha_from_step_inputs(store, clock):
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge, defaults={"model": "qwen"}))
    run_id = start(ex, config={"sandbox": "workspace-write"})
    (req,) = bridge.requests
    assert req["method"] == "POST"
    assert req["url"] == "http://127.0.0.1:8765/v1/invocations"
    body = req["body"]
    assert body["protocol_version"] == "1.0"
    assert body["run_id"] == run_id
    assert body["input"]["repo"] == PR["repo"]
    assert body["input"]["head_branch"] == "feature/x"
    assert body["input"]["head_sha"] == SHA
    assert body["input"]["instruction"] == "Fix the failing check"
    assert body["input"]["sandbox"] == "workspace-write"
    assert body["input"]["model"] == "qwen"
    doc = invocation(store)
    assert body["callback"]["url"] == CALLBACK + BRIDGE_CALLBACK_PATH.format(id=doc["id"])
    assert body["callback"]["token"] not in json.dumps(doc)  # only its hash is stored
    assert req["headers"]["Authorization"] == "Bearer bridge-secret"
    assert req["headers"]["Idempotency-Key"] == f"{doc['idempotency_key']}#1"
    assert body["attempt_id"] == req["headers"]["Idempotency-Key"]


def test_address_comes_from_the_step_not_the_actor(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ctx = InvocationContext("r", "fix", "actor_task", "spark", 1, "qwen-fixer", {})
    res = actor.invoke(
        {"instruction": "x", "repo": "r"}, "k1", T0 + timedelta(hours=1), context=ctx
    )
    assert res.outcome == "failed" and not res.retryable
    assert "head_branch" in res.error
    assert bridge.requests == []
    bad_sha = {**PR, "head_sha": "not a sha"}
    res = actor.invoke(bad_sha, "k2", T0 + timedelta(hours=1), context=ctx)
    assert res.outcome == "failed" and "head_sha" in res.error
    cfg_ctx = InvocationContext("r", "fix", "actor_task", "spark", 1, "a", dict(PR))
    assert actor.invoke({}, "k3", T0 + timedelta(hours=1), context=cfg_ctx).outcome == "accepted"
    assert bridge.requests[0]["body"]["input"]["head_sha"] == SHA


def test_no_callback_url_fails_without_dispatching(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge, callback_url=None)
    ctx = InvocationContext("r", "fix", "actor_task", "spark", 1, None, {})
    res = actor.invoke(PR, "k", T0 + timedelta(hours=1), context=ctx)
    assert res.outcome == "failed" and "callback_url" in res.error
    assert bridge.requests == []


# ---- the callback receiver ----------------------------------------------------------------


def accepted_run(store, clock):
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge))
    run_id = start(ex)
    return ex, run_id, invocation(store), token_of(bridge)


def test_callback_token_is_checked(store, clock):
    _, _, doc, _ = accepted_run(store, clock)
    assert record_bridge_event(store, doc["id"], "wrong", completed_event()) == UNAUTHORIZED
    assert record_bridge_event(store, doc["id"], None, completed_event()) == UNAUTHORIZED
    assert record_bridge_event(store, "bri_nope", "x", completed_event()) == UNKNOWN
    assert invocation(store)["status"] == "accepted"


def test_heartbeats_update_liveness_and_stale_sequences_are_ignored(store, clock):
    _, run_id, doc, token = accepted_run(store, clock)
    clock.advance(30)
    hb = {"event_id": "evt_2", "sequence": 2, "kind": "heartbeat", "payload": {}}
    assert record_bridge_event(store, doc["id"], token, hb, clock=clock) == RECORDED
    after = invocation(store)
    assert after["last_heartbeat_at"] == "2026-10-03T12:00:30Z"
    assert after["last_sequence"] == 2
    assert record_bridge_event(store, doc["id"], token, hb, clock=clock) == DUPLICATE
    old = {**hb, "sequence": 1, "kind": "progress"}
    assert record_bridge_event(store, doc["id"], token, old, clock=clock) == DUPLICATE
    bad = {"kind": "heartbeat", "sequence": "3"}
    assert record_bridge_event(store, doc["id"], token, bad) == INVALID
    assert record_bridge_event(store, doc["id"], token, {"kind": "nope", "sequence": 9}) == INVALID
    assert invocation(store)["status"] == "accepted"
    assert invocation(store)["pending_delivery"] is False


def test_a_repeated_terminal_event_is_recorded_and_delivered_once(store, clock):
    ex, run_id, doc, token = accepted_run(store, clock)
    assert record_bridge_event(store, doc["id"], token, completed_event()) == RECORDED
    again = completed_event(seq=4, summary="something else")
    assert record_bridge_event(store, doc["id"], token, again) == DUPLICATE
    failed = {"event_id": "e", "sequence": 5, "kind": "failed", "payload": {"class": "timeout"}}
    assert record_bridge_event(store, doc["id"], token, failed) == DUPLICATE
    assert redeliver_bridge(store, ex) == 1
    assert redeliver_bridge(store, ex) == 0
    ex.run_until_idle()
    assert step_state(ex.run(run_id), "fix")["outputs"]["summary"] == "fixed the lint"


def test_failed_callback_fails_or_retries_by_class(store, clock):
    ex, run_id, doc, token = accepted_run(store, clock)
    event = {
        "event_id": "e",
        "sequence": 2,
        "kind": "failed",
        "payload": {"class": "auth_or_policy", "message": "repo not allowed"},
    }
    assert record_bridge_event(store, doc["id"], token, event) == RECORDED
    redeliver_bridge(store, ex)
    fix = step_state(ex.run(run_id), "fix")
    assert fix["status"] == "failed"
    assert "repo not allowed" in fix["error"]["message"]


def test_a_new_attempt_expires_the_previous_invocation(store, clock):
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge))
    run_id = start(ex, retry=RetryPolicy(max_attempts=2, backoff_s=1))
    first = invocation(store)
    first_token = token_of(bridge)
    clock.advance(3601)  # the accepted attempt times out
    ex.run_until_idle()
    clock.advance(5)
    ex.run_until_idle()  # the retry is refused rather than re-posted
    # the actor is not idempotent on the key: a timed-out attempt is never re-asked blindly
    assert len(bridge.requests) == 1
    fix = step_state(ex.run(run_id), "fix")
    assert fix["status"] == "failed" and fix["error"]["code"] == "unsafe_retry"
    assert store.get(BRIDGE_INVOCATIONS, first["id"])["status"] == "accepted"
    # an attempt-2 invoke (a step declared idempotent) supersedes attempt 1
    actor = make_actor(store, clock, bridge)
    ctx = InvocationContext(run_id, "fix", "actor_task", "spark", 2, None, {})
    assert actor.invoke(PR, first["idempotency_key"], clock() + timedelta(hours=1), context=ctx)
    assert store.get(BRIDGE_INVOCATIONS, first["id"])["status"] == "expired"
    assert record_bridge_event(store, first["id"], first_token, completed_event()) == EXPIRED
    assert bridge.requests[-1]["headers"]["Idempotency-Key"].endswith("#2")


def test_reinvoking_an_accepted_attempt_does_not_post_again(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ctx = InvocationContext("r", "fix", "actor_task", "spark", 1, None, {})
    deadline = T0 + timedelta(hours=1)
    assert actor.invoke(PR, "k", deadline, context=ctx).outcome == "accepted"
    assert actor.invoke(PR, "k", deadline, context=ctx).outcome == "accepted"
    assert len(bridge.requests) == 1
    doc = invocation(store)
    record_bridge_event(store, doc["id"], token_of(bridge), completed_event())
    res = actor.invoke(PR, "k", deadline, context=ctx)  # the callback beat the re-invoke
    assert res.outcome == "completed" and res.output["summary"] == "fixed the lint"
    assert len(bridge.requests) == 1


# ---- bridge answers -----------------------------------------------------------------------


def invoke_once(store, clock, *answers, attempt=1, key="k"):
    bridge = FakeBridge(*answers)
    actor = make_actor(store, clock, bridge)
    ctx = InvocationContext("r", "fix", "actor_task", "spark", attempt, None, {})
    return actor.invoke(PR, key, T0 + timedelta(hours=1), context=ctx), bridge


def test_synchronous_200_completes_at_once(store, clock):
    body = {"outcome": "success", "output": {"summary": "done", "commits": ["abc"]}}
    res, _ = invoke_once(store, clock, (200, body))
    assert res.outcome == "completed"
    assert res.output["summary"] == "done" and res.output["commits"] == ["abc"]
    assert res.output["outcome"] == "success"
    assert invocation(store)["status"] == "completed"
    assert invocation(store)["pending_delivery"] is False


def test_rejections_map_to_failed_or_blocked(store, clock):
    res, _ = invoke_once(store, clock, (400, {"error": "input.instruction is required"}), key="a")
    assert res.outcome == "failed" and not res.retryable and "400" in res.error
    res, _ = invoke_once(store, clock, (403, {"error": "not in allowlist"}), key="b")
    assert res.outcome == "failed" and not res.retryable
    res, _ = invoke_once(store, clock, (503, {"error": "busy"}), key="c")
    assert res.outcome == "blocked"
    res, _ = invoke_once(store, clock, (500, {"error": "boom"}), key="d")
    assert res.outcome == "failed" and res.retryable


def test_blocked_then_accepted_on_the_same_attempt_rotates_the_callback_token(store, clock):
    bridge = FakeBridge((503, {"error": "busy"}), (202, {"invocation_id": "inv-2"}))
    actor = make_actor(store, clock, bridge)
    ctx = InvocationContext("r", "fix", "actor_task", "spark", 1, None, {})
    deadline = T0 + timedelta(hours=1)
    assert actor.invoke(PR, "k", deadline, context=ctx).outcome == "blocked"
    assert actor.invoke(PR, "k", deadline, context=ctx).outcome == "accepted"
    doc = invocation(store)
    assert doc["status"] == "accepted" and doc["invocation_id"] == "inv-2"
    assert record_bridge_event(store, doc["id"], token_of(bridge), completed_event()) == RECORDED


def test_unreachable_bridge(store, clock):
    refused = BridgeUnreachable("refused", definite=True)
    res, _ = invoke_once(store, clock, refused, key="a")
    assert res.outcome == "failed" and res.retryable
    with pytest.raises(BridgeUnreachable):  # maybe received: no ack, outcome unknown
        invoke_once(store, clock, BridgeUnreachable("reset", definite=False), key="b")


def test_result_mapping():
    res = result_from_terminal(
        "completed",
        {
            "outcome": "no_changes",
            "output": {"summary": "nothing to do", "threads_addressed": []},
            "workspace_measured": {"changed_files": [], "diffstat": ""},
        },
    )
    assert res.output["outcome"] == "no_changes"
    assert res.output["changed_files"] == [] and res.output["threads_addressed"] == []
    assert res.output["commits"] is None
    assert not result_from_terminal("blocked", {"message": "needs a human"}).retryable
    assert result_from_terminal("failed", {"class": "timeout"}).retryable
    assert not result_from_terminal("failed", {"class": "actor_rejected_input"}).retryable


# ---- the stdlib transport against a loopback fake bridge ----------------------------------


def test_urllib_transport_speaks_http_to_a_loopback_bridge():
    seen = {}

    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            return

        def do_POST(self):  # noqa: N802
            seen["auth"] = self.headers.get("Authorization")
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            code = 202 if self.path == "/v1/invocations" else 404
            data = json.dumps({"invocation_id": "inv-9"}).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, raw = _urllib_transport(
            "POST", base + "/v1/invocations", b'{"a": 1}', {"Authorization": "Bearer t"}, 5
        )
        assert status == 202 and json.loads(raw) == {"invocation_id": "inv-9"}
        assert seen == {"auth": "Bearer t", "body": {"a": 1}}
        status, _ = _urllib_transport("POST", base + "/nope", b"{}", {}, 5)
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(BridgeUnreachable) as exc:
        _urllib_transport("POST", base + "/v1/invocations", b"{}", {}, 5)  # now closed
    assert exc.value.definite
    with pytest.raises(BridgeUnreachable):
        _urllib_transport("POST", "file:///etc/passwd", None, {}, 5)


def test_callback_server_status_codes(store, clock):
    _, _, doc, token = accepted_run(store, clock)
    server = BridgeCallbackServer(store)
    path = BRIDGE_CALLBACK_PATH.format(id=doc["id"])
    try:
        assert server.handle("/elsewhere", "", b"{}")[0] == 404
        assert server.handle(path, "Bearer nope", b"{}")[0] == 401
        assert server.handle(path, f"Bearer {token}", b"not json")[0] == 400
        hb = json.dumps({"event_id": "e", "sequence": 2, "kind": "heartbeat"}).encode()
        assert server.handle(path, f"Bearer {token}", hb) == (200, {"status": RECORDED})
        done = json.dumps(completed_event()).encode()
        assert server.handle(path, f"Bearer {token}", done) == (200, {"status": RECORDED})
        assert invocation(store)["pending_delivery"] is True  # no executor: redeliver later
    finally:
        server.close()


# ---- production factory and zero-dependency import ---------------------------------------


def test_factory_builds_a_bridge_actor_from_params(store):
    factories = default_factories(store)
    bridge = Actor.from_dict(
        {
            "id": "qwen-fixer",
            "name": "qwen fixer",
            "kind": "agent",
            "params": {
                "bridge_url": "http://127.0.0.1:8765",
                "callback_url": "http://127.0.0.1:8766",
                "bridge_token": "grant:QWEN_BRIDGE_TOKEN",
                "model": "qwen3-coder",
            },
        },
        strict=False,
    )
    adapter = factories["agent"](bridge)
    assert isinstance(adapter, BridgeAgentActor) and isinstance(adapter, ActorPort)
    assert adapter.bridge_url == "http://127.0.0.1:8765"
    assert adapter.callback_url == "http://127.0.0.1:8766"
    assert adapter.actor_id == "qwen-fixer"
    assert adapter.supports_idempotency_key is False
    plain = Actor.from_dict({"id": "c", "name": "c", "kind": "agent"}, strict=False)
    assert isinstance(factories["agent"](plain), ColleagueActor)


def test_imports_with_no_extras_installed():
    blocked = ("fastapi", "uvicorn", "pymongo", "agentirc", "paho", "yaml", "mcp", "cultureagent")
    code = (
        f"import sys\nfor n in {blocked!r}: sys.modules[n] = None\n"
        "import culture_rules, culture_rules.actors.agent, culture_rules.node.actors\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert proc.returncode == 0, proc.stderr
