"""Acceptance 1 of Boundary t7: a fake extension plugs in with no core edit.

The fixture installs one extension (an action kind with its port, a built-in code step, an
event type, a webhook route and a node listener) into the default registry and uninstalls it
afterwards, so every other test sees only the built-in apps. Then the runner runs the
built-in and the kind's port, the API serves the route without a principal, the catalogue
lists the kind and validation enforces its params, the event type is known and the node
ticks the listener.

d9 adds the seams for what apps used to leave in core: the fake kind's params validator, a
``fake.*`` trigger filter (matching, validation, describe words), the kind's phrases and the
built-in's gloss, a health section, an event reservation and an API route's role - each
reaches core with no core edit.
"""

from __future__ import annotations

import pytest

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.runs import ACTION_STEP, Executor, step_state
from culture_rules.extensions import registry
from culture_rules.machines.probe import ProbeResult
from culture_rules.model.action import Action
from culture_rules.model.action_kinds import ACTION_KINDS, ActionKind, ParamSpec
from culture_rules.model.rule import Rule, Trigger
from culture_rules.model.validate import validate
from culture_rules.node.daemon import HeartbeatOptions, Node, node_collections
from culture_rules.node.runner import default_ports
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, port, rule, step, workflow

NAME = "fake"
KIND = ActionKind("fake.do", {"text": ParamSpec("str", True)}, "Do the fake thing")
BUILTIN = "fake.count"
EVENT = "fake.thing.happened"
ROUTE = "/hooks/fake"
COLLECTION = "fake_state"
CALLS: dict[str, list] = {}


class FakeDo:
    supports_idempotency_key = True

    def __init__(self, store):
        self.store = store

    def invoke(self, input, key, deadline, *, context):
        CALLS["do"].append(dict(input))
        return InvocationResult.completed({"done": True})


class FakeCount:
    supports_idempotency_key = True

    def invoke(self, input, key, deadline, *, context):
        CALLS["count"].append(dict(input))
        return InvocationResult.completed({"n": 1})


class FakeListener:
    def __init__(self, ctx):
        self.host = ctx.host

    def tick(self, report):
        CALLS["tick"].append(report.host)

    def close(self):
        CALLS["close"].append(self.host)


def fake_router(store):
    from fastapi import APIRouter

    api = APIRouter()

    @api.post(ROUTE, include_in_schema=False)
    def hook():
        return {"ok": True, "store": store is not None}

    return api


def fake_collections():
    return (COLLECTION,)


def fake_params(params, path):
    if params.get("text") == "forbidden":
        return [(f"{path}.text", "fake_text_forbidden", "the fake kind refuses that text")]
    return []


def fake_admits(params, data, own):
    del own
    return params.get("room") in (None, data.get("room"))


def fake_filter_problems(params, path):
    if "room" in params and not str(params.get("type", "")).startswith("fake."):
        return [(f"{path}.room", "trigger_param_invalid", "only a fake.* trigger takes room")]
    return []


def fake_filter_words(params):
    return [f"(in room {params['room']})"] if params.get("room") else []


def fake_guard(params):
    del params
    return ["(fake guard)"]


def fake_health(store, now):
    del now
    return {"rooms": len(store.find(COLLECTION))}


def fake_reserved(envelope):
    return "fake.secret is the engine's" if envelope.get("type") == "fake.secret" else None


def _no_routes(app, ctx):
    del app, ctx


def register(ext):
    ext.action_kind(KIND)
    ext.action_port(KIND.name, FakeDo)
    ext.builtin(BUILTIN, _fake_count, phrase="fake.count: count the fake things")
    ext.event_type(EVENT, summary="the fake thing happened")
    ext.webhook_route(ROUTE, fake_router)
    ext.node_listener("fake.poller", FakeListener)
    ext.node_collections(fake_collections)
    ext.params_validator(KIND.name, fake_params)
    ext.trigger_filter(
        "fake", match=fake_admits, validate=fake_filter_problems, describe=fake_filter_words
    )
    ext.action_phrases(KIND.name, words=(("text", '"{}"'),), after=fake_guard)
    ext.health_section("fake_rooms", fake_health, empty=dict)
    ext.event_reservation("fake.secret", fake_reserved)
    ext.api_routes("fake.routes", _no_routes, roles=(("POST", "/fake/{id}/poke", "editor"),))


def _fake_count(store):
    del store
    return FakeCount()


@pytest.fixture
def fake():
    for key in ("do", "count", "tick", "close"):
        CALLS[key] = []
    registry().install(NAME, register)
    yield CALLS
    registry().uninstall(NAME)


def test_the_runner_runs_the_fake_builtin_and_the_fake_kinds_port(fake):
    store = MemoryStore()
    count = step("count", outputs=(port("n", "integer"),), config={"builtin": BUILTIN})
    ex = Executor(store, "spark", default_ports(store, "spark"), clock=Clock())
    started = ex.start(
        rule(action=Action(kind=KIND.name, params={"text": "hi"})), workflow((count,), ())
    )
    ex.run_until_idle()
    run = ex.run(started["id"])
    assert run["status"] == "succeeded", run
    assert len(fake["count"]) == 1
    assert fake["do"][0]["text"] == "hi"
    assert step_state(run, ACTION_STEP)["outputs"] == {"done": True}


def test_the_catalogue_lists_the_fake_kind(fake):
    assert ACTION_KINDS[KIND.name] is KIND


def _rule(params):
    return Rule(
        id="r",
        name="r",
        trigger=Trigger(kind="manual"),
        action=Action(kind=KIND.name, params=params),
    )


def test_validation_accepts_the_fake_kind(fake):
    assert validate(_rule({"text": "hi"})) == []


def test_validation_enforces_the_fake_kinds_params(fake):
    codes = [e.code for e in validate(_rule({}))]
    assert codes == ["action_param_required"]


def test_the_fake_event_type_is_known(fake):
    assert registry().event_types()[EVENT].extension == NAME


def test_after_uninstall_the_kind_is_unknown_again(fake):
    registry().uninstall(NAME)
    codes = [e.code for e in validate(_rule({"text": "hi"}))]
    registry().install(NAME, register)  # the fixture uninstalls it again
    assert codes == ["action_kind_unknown"]


def test_the_api_serves_the_fake_webhook_route_without_a_principal(fake):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from culture_rules.auth.resolve import LAN, AuthSettings
    from culture_rules.server.app import create_app

    client = TestClient(create_app(MemoryStore(), auth=AuthSettings(listener=LAN)))
    response = client.post(ROUTE)
    assert response.json() == {"ok": True, "store": True}


def test_the_node_ticks_and_closes_the_fake_listener(fake):
    beats = HeartbeatOptions(
        probe=lambda: ProbeResult(tools={}), load_reader=dict, engine_version="test"
    )
    node = Node(MemoryStore(), "spark", heartbeat_options=beats)
    node.run_once()
    node.close()
    assert (fake["tick"], fake["close"]) == (["spark"], ["spark"])


def test_the_node_creates_the_fake_collections(fake):
    assert COLLECTION in node_collections()


class _FailingListener:
    def __init__(self, ctx):
        del ctx

    def tick(self, report):
        raise RuntimeError("boom")


@pytest.fixture
def failing():
    registry().install("failing", lambda ext: ext.node_listener("failing.poller", _FailingListener))
    yield
    registry().uninstall("failing")


def test_a_failing_listener_is_reported_by_its_name(failing):
    beats = HeartbeatOptions(
        probe=lambda: ProbeResult(tools={}), load_reader=dict, engine_version="test"
    )
    node = Node(MemoryStore(), "spark", heartbeat_options=beats)
    report = node.run_once()
    node.close()
    assert "failing.poller: RuntimeError: boom" in report.errors


# --------------------------------------------------------------------------- d9 seams


def test_validation_runs_the_fake_kinds_params_validator(fake):
    errors = validate(_rule({"text": "forbidden"}))
    assert [(e.path, e.code) for e in errors] == [("action.params.text", "fake_text_forbidden")]


def _event_rule(params):
    return Rule(
        id="r",
        name="r",
        trigger=Trigger(kind="event", params=params),
        action=Action(kind=KIND.name, params={"text": "hi"}),
    )


def test_validation_runs_the_fake_trigger_filters_check_on_every_event_trigger(fake):
    errors = validate(_event_rule({"type": "other.thing", "room": "r1"}))
    assert [(e.path, e.code) for e in errors] == [("trigger.params.room", "trigger_param_invalid")]


def test_matching_applies_the_fake_trigger_filter(fake):
    from culture_rules.engine.matching import trigger_matches

    trigger = Trigger(kind="event", params={"type": EVENT, "room": "r1"})
    admitted = trigger_matches(trigger, {"type": EVENT, "data": {"room": "r1"}})
    refused = trigger_matches(trigger, {"type": EVENT, "data": {"room": "r2"}})
    assert (admitted, refused) == (True, False)


def test_describe_uses_the_fake_phrases_and_filter_words(fake):
    from culture_rules.model.describe import action_text, step_text, trigger_text

    action = action_text({"kind": KIND.name, "params": {"text": "hi"}}, "on spark")
    trigger = trigger_text({"kind": "event", "params": {"type": EVENT, "room": "r1"}})
    builtin = step_text({"id": "s", "kind": "code", "config": {"builtin": BUILTIN}})
    assert [action, trigger, builtin] == [
        'fake.do "hi" on spark (fake guard)',
        f"{EVENT} (in room r1)",
        "fake.count: count the fake things",
    ]


def test_health_reports_the_fake_section(fake):
    from datetime import UTC, datetime

    from culture_rules.ops.health import health_status

    store = MemoryStore()
    store.insert(COLLECTION, {"id": "a"})
    assert health_status(store, datetime.now(UTC), "spark")["fake_rooms"] == {"rooms": 1}


def test_ingest_refuses_what_the_fake_extension_reserves(fake):
    from culture_rules.events.emit import reserved_reason

    assert reserved_reason({"id": "e1", "type": "fake.secret"}) == "fake.secret is the engine's"


def test_the_policy_gives_the_fake_route_its_declared_role(fake):
    from culture_rules.auth.policy import required_role

    assert required_role("POST", "/fake/f1/poke") == "editor"
