"""The discord.reply action: an ephemeral reply to a slash/context command's interaction."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from culture_rules.apps.discord_commands import INTERACTIONS_COLLECTION
from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.engine.runs import Executor, step_state
from culture_rules.model.action_step import ACTION_BUILTIN
from culture_rules.node.actions.discord_reply import DiscordReplyAction
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, edge, port, rule, step, workflow

NOW = datetime.now(UTC)
EVENT = "evt-1"
INPUT = {"event_id": EVENT, "text": "Saved"}
KEPT = "itok-" + "reply-1"  # the kept interaction token (built, not a literal)


def ctx(actor="disc"):
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor=actor)


class FakeTransport:
    def __init__(self, *responses):
        self.calls = []
        self._responses = list(responses) or [(200, {}, {})]

    def __call__(self, url, data, headers, timeout, method=None):
        self.calls.append({"method": method, "url": url, "data": data, "headers": headers})
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def make_store(*, actor="disc", expires=None):
    s = MemoryStore()
    s.put(
        INTERACTIONS_COLLECTION,
        {
            "id": EVENT,
            "token": KEPT,
            "application_id": "9001",
            "actor": actor,
            "expires_at": (expires or NOW + timedelta(minutes=10)).isoformat(),
        },
    )
    return s


def action(store, transport, **kw):
    return DiscordReplyAction(store, transport=transport, **kw)


def test_replies_by_editing_the_deferred_original_without_a_bot_token():
    t = FakeTransport()
    res = action(make_store(), t).invoke(INPUT, "k", NOW, context=ctx())
    assert res.outcome == COMPLETED, res.error
    assert res.output == {"replied": True, "event_id": EVENT}
    (call,) = t.calls
    assert call["method"] == "PATCH"
    assert call["url"].endswith(f"/webhooks/9001/{KEPT}/messages/@original")
    assert json.loads(call["data"])["content"] == "Saved"
    assert "Authorization" not in call["headers"]


def test_long_text_is_cut_with_an_ellipsis():
    t = FakeTransport()
    action(make_store(), t).invoke({**INPUT, "text": "y" * 2500}, "k", NOW, context=ctx())
    content = json.loads(t.calls[0]["data"])["content"]
    assert len(content) == 2000 and content.endswith("…")


def test_missing_or_expired_token_is_a_non_retryable_interaction_expired():
    t = FakeTransport()
    past = NOW - timedelta(minutes=1)
    for store in (MemoryStore(), make_store(expires=past)):
        res = action(store, t).invoke(INPUT, "k", NOW, context=ctx())
        assert res.outcome == FAILED and res.retryable is False
        assert "interaction_expired" in res.error
    assert t.calls == []


def test_discord_401_404_mean_interaction_expired_and_5xx_429_retry():
    for status, retryable, expired in [
        (401, False, True),
        (404, False, True),
        (403, False, False),
        (503, True, False),
        (429, True, False),
    ]:
        t = FakeTransport((status, {"retry_after": 0}, {}))
        res = action(make_store(), t, sleep=lambda s: None).invoke(INPUT, "k", NOW, context=ctx())
        assert res.outcome == FAILED
        assert res.retryable is retryable
        assert ("interaction_expired" in res.error) is expired
        assert KEPT not in res.error


def test_actor_must_match_the_stored_token_actor():
    t = FakeTransport()
    res = action(make_store(actor="other-bot"), t).invoke(INPUT, "k", NOW, context=ctx())
    assert res.outcome == FAILED and res.retryable is False
    assert "actor" in res.error
    assert t.calls == []


def test_input_refusals_never_call_discord():
    t = FakeTransport()
    cases = [
        (ctx(None), INPUT, "params.actor"),
        (ctx(), {"text": "hi"}, "event_id"),
        (ctx(), {"event_id": EVENT}, "text"),
        (ctx(), {"event_id": EVENT, "text": "  "}, "text"),
    ]
    for c, inp, text in cases:
        res = action(make_store(), t).invoke(inp, "k", NOW, context=c)
        assert res.outcome == FAILED and res.retryable is False
        assert text in res.error
    assert t.calls == []


# ------------------------------------------------- a workflow step with a `when` condition

SAVED = {"op": "compare", "cmp": "==", "left": {"field": "outcome"}, "right": {"literal": "saved"}}


def reply_workflow(when=SAVED, text_ref="inputs.text"):
    fields = ("event_id", "outcome", "text")
    reply = step(
        "reply",
        "code",
        inputs=tuple(port(f, "string") for f in fields),
        outputs=(port("replied", "boolean", required=False),),
        config={
            "builtin": ACTION_BUILTIN,
            "action": {
                "kind": "discord.reply",
                "params": {"actor": "disc", "event_id": "inputs.event_id", "text": text_ref},
            },
            "when": when,
        },
    )
    event = step("event", outputs=tuple(port(f, "string") for f in fields))
    return workflow((event, reply), tuple(edge("event", f, "reply", f) for f in fields))


def run_for(values, transport, monkeypatch, **wf):
    store = make_store()
    event = FakeActor().on("event", ("complete", {"event_id": EVENT, **values}))
    ports = {"*": event, "action:discord.reply": action(store, transport)}
    ex = Executor(store, "spark", ports, clock=Clock())
    started = ex.start(rule(), reply_workflow(**wf))
    ex.run_until_idle()
    return ex.run(started["id"])


def test_the_when_step_replies_saved_and_is_skipped_otherwise(monkeypatch):
    yes, no = FakeTransport(), FakeTransport()
    doc = run_for({"outcome": "saved", "text": "Saved"}, yes, monkeypatch)
    assert doc["status"] == "succeeded", doc["steps"]
    assert step_state(doc, "reply")["status"] == "succeeded"
    assert json.loads(yes.calls[0]["data"])["content"] == "Saved"

    doc = run_for({"outcome": "skipped", "text": "Saved"}, no, monkeypatch)
    assert doc["status"] == "succeeded", doc["steps"]
    assert step_state(doc, "reply")["status"] == "skipped"
    assert no.calls == []


def test_a_reply_text_built_from_a_skipped_reason_is_sent(monkeypatch):
    t = FakeTransport()
    doc = run_for(
        {"outcome": "skipped", "text": "Skipped: already saved"},
        t,
        monkeypatch,
        when={
            "op": "compare",
            "cmp": "==",
            "left": {"field": "outcome"},
            "right": {"literal": "skipped"},
        },
    )
    assert step_state(doc, "reply")["status"] == "succeeded", doc["steps"]
    assert json.loads(t.calls[0]["data"])["content"] == "Skipped: already saved"


def test_the_token_is_judged_by_the_ports_clock():
    store = make_store()
    later = lambda: NOW + timedelta(minutes=20)  # noqa: E731 - past the 15-minute TTL
    res = DiscordReplyAction(store, transport=FakeTransport(), clock=later).invoke(
        {"event_id": EVENT, "text": "Saved"}, "k", NOW, context=ctx()
    )
    assert res.outcome == FAILED and res.error.startswith("interaction_expired")
