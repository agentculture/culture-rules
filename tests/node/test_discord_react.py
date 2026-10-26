"""The discord.react action: the bot adds one reaction to a message."""

from __future__ import annotations

from datetime import UTC, datetime

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.engine.runs import Executor, step_state
from culture_rules.model.action_step import ACTION_BUILTIN
from culture_rules.node.actions.discord_react import DiscordReactAction
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, edge, port, rule, step, workflow

NOW = datetime(2026, 1, 1, tzinfo=UTC)
THUMBS = "\N{THUMBS UP SIGN}"
INPUT = {"channel": "42", "message": "900", "emoji": THUMBS}


def ctx(actor="disc"):
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor=actor)


class FakeTransport:
    def __init__(self, *responses):
        self.calls = []
        self._responses = list(responses) or [(204, {}, {})]

    def __call__(self, url, data, headers, timeout, method=None):
        self.calls.append((method, url, headers))
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def store_with(surface="discord", **connection):
    s = MemoryStore()
    s.put(
        "actors",
        {
            "id": "disc",
            "name": "Disc",
            "kind": "service",
            "surface": surface,
            "connection": {"bot_token": "grant:DISCORD_BOT", **connection},
        },
    )
    return s


def action(store, transport, **kw):
    return DiscordReactAction(
        store, resolve_secret=lambda ref: "tok-xyz", transport=transport, **kw
    )


def test_reacts_with_the_actors_token_and_reports_it():
    t = FakeTransport()
    res = action(store_with(), t).invoke(INPUT, "k", NOW, context=ctx())
    assert res.outcome == COMPLETED
    assert res.output == {"channel": "42", "message": "900", "emoji": THUMBS, "reacted": True}
    ((method, url, headers),) = t.calls
    assert method == "PUT"
    assert url.endswith("/channels/42/messages/900/reactions/%F0%9F%91%8D/@me")
    assert headers["Authorization"] == "Bot tok-xyz"


def test_a_custom_emoji_is_name_and_id():
    t = FakeTransport()
    res = action(store_with(), t).invoke({**INPUT, "emoji": "blob:123"}, "k", NOW, context=ctx())
    assert res.outcome == COMPLETED
    assert t.calls[0][1].endswith("/reactions/blob%3A123/@me")


def test_a_429_is_waited_out_and_retried():
    waits = []
    t = FakeTransport((429, {"retry_after": 0.5}, {}), (204, {}, {}))
    res = action(store_with(), t, sleep=waits.append).invoke(INPUT, "k", NOW, context=ctx())
    assert res.outcome == COMPLETED
    assert waits == [0.5]
    assert len(t.calls) == 2


def test_a_persistent_429_fails_retryable():
    t = FakeTransport((429, {"retry_after": 1}, {}))
    res = action(store_with(), t, sleep=lambda s: None).invoke(INPUT, "k", NOW, context=ctx())
    assert res.outcome == FAILED
    assert res.retryable is True


def test_refusals_never_call_discord():
    t = FakeTransport()
    cases = [
        (store_with(surface="github"), ctx(), INPUT, "not a discord"),
        (store_with(channels=["1", "2"]), ctx(), INPUT, "allow-list"),
        (store_with(), ctx(None), INPUT, "params.actor"),
        (store_with(), ctx("nope"), INPUT, "not found"),
        (store_with(), ctx(), {"channel": "42", "message": "900"}, "emoji"),
        (store_with(), ctx(), {"emoji": THUMBS, "message": "900"}, "channel"),
    ]
    for store, c, inp, text in cases:
        res = action(store, t).invoke(inp, "k", NOW, context=c)
        assert res.outcome == FAILED
        assert res.retryable is False
        assert text in res.error
    assert t.calls == []


def test_an_empty_channels_list_means_every_channel():
    res = action(store_with(channels=[]), FakeTransport()).invoke(INPUT, "k", NOW, context=ctx())
    assert res.outcome == COMPLETED


def test_discord_error_keeps_retryable_flag_and_no_token():
    for status, retryable in [(403, False), (503, True)]:
        t = FakeTransport((status, {}, {}))
        res = action(store_with(), t).invoke(INPUT, "k", NOW, context=ctx())
        assert res.outcome == FAILED
        assert res.retryable is retryable
        assert "tok-xyz" not in res.error


def test_a_bad_emoji_fails_without_a_call():
    t = FakeTransport()
    res = action(store_with(), t).invoke({**INPUT, "emoji": "a/b"}, "k", NOW, context=ctx())
    assert res.outcome == FAILED
    assert res.retryable is False
    assert t.calls == []


# ------------------------------------------------- a workflow step with a `when` condition

ONLY_THUMBS = {
    "op": "compare",
    "cmp": "==",
    "left": {"field": "emoji"},
    "right": {"literal": THUMBS},
}


def react_workflow():
    fields = ("channel", "message", "emoji")
    react = step(
        "react",
        "code",
        inputs=tuple(port(f, "string") for f in fields),
        outputs=(port("reacted", "boolean", required=False),),
        config={
            "builtin": ACTION_BUILTIN,
            "action": {
                "kind": "discord.react",
                "params": {
                    "actor": "disc",
                    "channel": "inputs.channel",
                    "message": "inputs.message",
                    "emoji": "inputs.emoji",
                },
            },
            "when": ONLY_THUMBS,
        },
    )
    event = step("event", outputs=tuple(port(f, "string") for f in fields))
    return workflow((event, react), tuple(edge("event", f, "react", f) for f in fields))


def run_for(emoji, transport):
    store = store_with()
    event = FakeActor().on(
        "event", ("complete", {"channel": "42", "message": "900", "emoji": emoji})
    )
    ports = {"*": event, "action:discord.react": action(store, transport)}
    ex = Executor(store, "spark", ports, clock=Clock())
    started = ex.start(rule(), react_workflow())
    ex.run_until_idle()
    return ex.run(started["id"])


def test_the_when_step_reacts_for_one_emoji_and_stays_silent_for_another():
    yes, no = FakeTransport(), FakeTransport()
    doc = run_for(THUMBS, yes)
    assert doc["status"] == "succeeded", doc["steps"]
    assert step_state(doc, "react")["status"] == "succeeded"
    assert len(yes.calls) == 1

    doc = run_for("\N{CROSS MARK}", no)
    assert doc["status"] == "succeeded", doc["steps"]
    assert step_state(doc, "react")["status"] == "skipped"
    assert no.calls == []
