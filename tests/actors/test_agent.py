"""Agent actor adapter: one-shot colleague work and mesh tasks with correlation (t15)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

import pytest

from culture_rules.actors.agent import (
    AgentActorError,
    ColleagueActor,
    MeshAgentActor,
    MeshReply,
    parse_task_result,
    valid_mesh_nick,
)
from culture_rules.engine.actorport import (
    ACCEPTED,
    COMPLETED,
    FAILED,
    ActorPort,
    InvocationContext,
)

DEADLINE = datetime(2030, 1, 1, tzinfo=timezone.utc)


def ctx(**config) -> InvocationContext:
    return InvocationContext(
        run_id="r1", step_id="s1", kind="action", host="h", config=config, actor="a"
    )


OK_JSON = json.dumps(
    {
        "task_id": "t-1",
        "status": "ok",
        "summary": "did it",
        "changed_files": ["a.py"],
        "branch": "colleague/x",
        "pr_url": None,
        "steps": [],
    }
)


class FakeRunner:
    def __init__(self, stdout=OK_JSON, returncode=0, stderr="", exc=None):
        self.calls = []
        self.stdout, self.returncode, self.stderr, self.exc = stdout, returncode, stderr, exc

    def __call__(self, argv, *, timeout):
        self.calls.append((argv, timeout))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


def test_implements_actorport():
    assert isinstance(ColleagueActor(runner=FakeRunner()), ActorPort)
    assert isinstance(MeshAgentActor(client=FakeClient()), ActorPort)


def test_parse_task_result_ok_and_error():
    ok = parse_task_result(OK_JSON)
    assert ok["status"] == "ok"
    assert ok["summary"] == "did it"
    assert parse_task_result("noise\n" + OK_JSON + "\n")["task_id"] == "t-1"
    with pytest.raises(AgentActorError):
        parse_task_result("not json")
    with pytest.raises(AgentActorError):
        parse_task_result(json.dumps({"summary": "no status"}))


def test_colleague_argv_exact_no_shell():
    runner = FakeRunner()
    actor = ColleagueActor(repo="/r", engine="vllm-openai", model="qwen", runner=runner)
    res = actor.invoke({"instruction": "fix it"}, "k1", DEADLINE, context=ctx())
    argv, _ = runner.calls[0]
    assert argv == [
        "colleague", "work", "--repo", "/r", "--engine", "vllm-openai",
        "--model", "qwen", "--json", "--", "fix it",
    ]  # fmt: skip
    assert res.outcome == COMPLETED
    assert res.output["summary"] == "did it"
    assert res.output["changed_files"] == ["a.py"]
    assert res.output["branch"] == "colleague/x"


def test_colleague_config_from_context_overrides():
    runner = FakeRunner()
    actor = ColleagueActor(runner=runner)
    actor.invoke(
        {"instruction": "go"},
        "k",
        DEADLINE,
        context=ctx(repo="/x", engine="e", model="m"),
    )
    argv = runner.calls[0][0]
    assert argv[argv.index("--repo") + 1] == "/x"
    assert argv[argv.index("--engine") + 1] == "e"
    assert argv[argv.index("--model") + 1] == "m"


def test_colleague_requires_instruction_and_repo():
    actor = ColleagueActor(runner=FakeRunner())
    res = actor.invoke({}, "k", DEADLINE, context=ctx(repo="/r"))
    assert res.outcome == FAILED
    assert res.retryable is False
    res = actor.invoke({"instruction": "x"}, "k", DEADLINE, context=ctx())
    assert res.outcome == FAILED
    assert res.retryable is False


def test_colleague_error_status_is_failed():
    out = json.dumps({"task_id": "t", "status": "error", "error": "boom"})
    actor = ColleagueActor(repo="/r", runner=FakeRunner(stdout=out, returncode=1))
    res = actor.invoke({"instruction": "x"}, "k", DEADLINE, context=ctx())
    assert res.outcome == FAILED
    assert "boom" in res.error


def test_colleague_unparseable_output_is_failed_with_stderr():
    actor = ColleagueActor(repo="/r", runner=FakeRunner(stdout="", returncode=2, stderr="bad"))
    res = actor.invoke({"instruction": "x"}, "k", DEADLINE, context=ctx())
    assert res.outcome == FAILED
    assert "bad" in res.error


def test_colleague_missing_binary_non_retryable_and_timeout_raises():
    actor = ColleagueActor(repo="/r", runner=FakeRunner(exc=FileNotFoundError("colleague")))
    res = actor.invoke({"instruction": "x"}, "k", DEADLINE, context=ctx())
    assert res.outcome == FAILED
    assert res.retryable is False
    actor = ColleagueActor(
        repo="/r", runner=FakeRunner(exc=subprocess.TimeoutExpired(["colleague"], 1))
    )
    with pytest.raises(TimeoutError):  # no ack: the work may have happened
        actor.invoke({"instruction": "x"}, "k", DEADLINE, context=ctx())


def test_colleague_idempotent_on_key():
    runner = FakeRunner()
    actor = ColleagueActor(repo="/r", runner=runner)
    a = actor.invoke({"instruction": "x"}, "same", DEADLINE, context=ctx())
    b = actor.invoke({"instruction": "x"}, "same", DEADLINE, context=ctx(attempt=2))
    assert len(runner.calls) == 1
    assert a == b
    actor.invoke({"instruction": "x"}, "other", DEADLINE, context=ctx())
    assert len(runner.calls) == 2


def test_colleague_timeout_derived_from_deadline():
    runner = FakeRunner()
    actor = ColleagueActor(
        repo="/r", runner=runner, now=lambda: datetime(2029, 12, 31, 23, 59, 0, tzinfo=timezone.utc)
    )
    actor.invoke({"instruction": "x"}, "k", DEADLINE, context=ctx())
    assert runner.calls[0][1] == pytest.approx(60.0)


# -- mesh ----------------------------------------------------------------------------


class FakeClient:
    def __init__(self):
        self.sent = []
        self.inbox: list[MeshReply] = []

    def send(self, nick, text, correlation_id):
        self.sent.append((nick, text, correlation_id))

    def replies(self):
        out, self.inbox = self.inbox, []
        return out


def test_valid_mesh_nick():
    assert valid_mesh_nick("spark-daria")
    assert not valid_mesh_nick("daria")
    assert not valid_mesh_nick("spark daria")
    assert not valid_mesh_nick("-x")


def test_mesh_send_carries_correlation_id_and_returns_accepted():
    client = FakeClient()
    actor = MeshAgentActor(client=client)
    res = actor.invoke(
        {"instruction": "review PR 4"}, "key-1", DEADLINE, context=ctx(nick="spark-daria")
    )
    assert res.outcome == ACCEPTED
    nick, text, corr = client.sent[0]
    assert nick == "spark-daria"
    assert "review PR 4" in text
    assert corr
    assert corr == actor.correlation_id("key-1")  # deterministic from the key


def test_mesh_idempotent_resend_does_not_duplicate():
    client = FakeClient()
    actor = MeshAgentActor(client=client)
    c = ctx(nick="spark-daria")
    actor.invoke({"instruction": "x"}, "k", DEADLINE, context=c)
    actor.invoke({"instruction": "x"}, "k", DEADLINE, context=c)
    assert len(client.sent) == 1


def test_mesh_rejects_bad_nick_non_retryable():
    actor = MeshAgentActor(client=FakeClient())
    res = actor.invoke({"instruction": "x"}, "k", DEADLINE, context=ctx(nick="nodash"))
    assert res.outcome == FAILED
    assert res.retryable is False
    res = actor.invoke({"instruction": "x"}, "k", DEADLINE, context=ctx())
    assert res.outcome == FAILED
    assert res.retryable is False


def test_mesh_reply_matched_by_correlation_id():
    client = FakeClient()
    actor = MeshAgentActor(client=client)
    c = ctx(nick="spark-daria")
    actor.invoke({"instruction": "a"}, "k1", DEADLINE, context=c)
    actor.invoke({"instruction": "b"}, "k2", DEADLINE, context=c)
    c2 = actor.correlation_id("k2")
    client.inbox = [
        MeshReply(correlation_id="unrelated", text="noise", sender="spark-daria"),
        MeshReply(correlation_id=c2, text="B done", sender="spark-daria"),
    ]
    delivered = []
    n = actor.poll(lambda key, result: delivered.append((key, result)))
    assert n == 1
    key, result = delivered[0]
    assert key == "k2"
    assert result.outcome == COMPLETED
    assert result.output["reply"] == "B done"
    assert result.output["sender"] == "spark-daria"
    # k1 stays pending; a duplicate reply for k2 is not delivered twice
    client.inbox = [MeshReply(correlation_id=c2, text="again", sender="spark-daria")]
    assert actor.poll(lambda k, r: delivered.append((k, r))) == 0
    assert actor.pending() == ["k1"]


def test_mesh_failed_reply_and_late_resend_after_reply():
    client = FakeClient()
    actor = MeshAgentActor(client=client)
    actor.invoke({"instruction": "a"}, "k", DEADLINE, context=ctx(nick="spark-daria"))
    client.inbox = [
        MeshReply(correlation_id=actor.correlation_id("k"), text="nope", sender="s-d", error=True)
    ]
    got = []
    actor.poll(lambda k, r: got.append(r))
    assert got[0].outcome == FAILED
    assert "nope" in got[0].error
    # a retry with the same key after the reply returns the recorded result, no resend
    res = actor.invoke({"instruction": "a"}, "k", DEADLINE, context=ctx(nick="spark-daria"))
    assert res.outcome == FAILED
    assert len(client.sent) == 1


def test_real_client_is_lazy_optional():
    from culture_rules.actors import agent

    # importing the module must not import agentirc
    assert "agentirc" not in agent.__dict__
    with pytest.raises(AgentActorError):
        agent.load_agentirc_client(importer=lambda name: (_ for _ in ()).throw(ImportError(name)))
