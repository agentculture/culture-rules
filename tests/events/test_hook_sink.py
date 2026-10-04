"""Webhook sink on the in-memory store."""

from __future__ import annotations

import logging

import pytest

from culture_rules.engine.matching import trigger_matches
from culture_rules.events.hook_sink import HOOK_STATS_COLLECTION, event_id_for, sink
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.model.rule import Trigger
from culture_rules.store.memory import MemoryStore

TYPE = "github.pr.opened"


def actor(**over):
    doc = {
        "id": "gh-app",
        "kind": "app",
        "enabled": True,
        "params": {"surface": "github", "events": [TYPE], "self_identity": "Culture-Bot"},
    }
    doc.update(over)
    return doc


def call(store, a=None, *, type=TYPE, data=None, delivery="d1", author="alice"):
    return sink(store, a or actor(), type, data or {"n": 1}, delivery, author)


def stat(store, outcome):
    doc = store.get(HOOK_STATS_COLLECTION, f"gh-app:{outcome}")
    return doc["count"] if doc else 0


def test_duplicate_delivery_yields_one_event():
    s = MemoryStore()
    assert call(s) == "accepted"
    assert call(s) == "duplicate"
    assert len(s.find(EVENTS_COLLECTION)) == 1
    assert (stat(s, "accepted"), stat(s, "duplicate")) == (1, 1)


def test_disabled_writes_nothing():
    s = MemoryStore()
    assert call(s, actor(enabled=False)) == "disabled"
    assert s.find(EVENTS_COLLECTION) == []
    assert stat(s, "disabled") == 1


def test_undeclared_type_is_ignored():
    s = MemoryStore()
    assert call(s, type="github.pr.closed") == "ignored"
    assert s.find(EVENTS_COLLECTION) == []
    assert stat(s, "ignored") == 1


def test_self_authored_tagged_case_insensitively():
    s = MemoryStore()
    call(s, author="culture-bot")
    call(s, delivery="d2", author="alice")
    by_id = {d["id"]: d["envelope"]["data"] for d in s.find(EVENTS_COLLECTION)}
    assert by_id[event_id_for("github", "d1")]["self_authored"] is True
    assert "self_authored" not in by_id[event_id_for("github", "d2")]


def test_payload_cannot_forge_self_authored():
    s = MemoryStore()
    call(s, data={"self_authored": True})
    (doc,) = s.find(EVENTS_COLLECTION)
    assert "self_authored" not in doc["envelope"]["data"]


def test_envelope_shape_and_matching():
    s = MemoryStore()
    call(s, data={"number": 7})
    (doc,) = s.find(EVENTS_COLLECTION)
    env = doc["envelope"]
    assert doc["id"] == env["id"] == event_id_for("github", "d1")
    assert env["id"].startswith("hook_github_")
    assert env["type"] == TYPE and env["source"] == "app://gh-app"
    assert env["data"] == {"number": 7, "delivery_id": "d1", "actor": "gh-app"}
    assert trigger_matches(Trigger(kind="event", params={"type": TYPE}), env)


def test_self_authored_matches_only_include_self():
    s = MemoryStore()
    call(s, author="Culture-Bot")
    (doc,) = s.find(EVENTS_COLLECTION)
    env = doc["envelope"]
    assert not trigger_matches(Trigger(kind="event", params={"type": TYPE}), env)
    assert trigger_matches(Trigger(kind="event", params={"type": TYPE, "include_self": True}), env)


def test_surfaces_do_not_collide():
    assert event_id_for("github", "x") != event_id_for("jira", "x")


def test_accepts_actor_model():
    from culture_rules.model.actor import Actor

    a = Actor(id="gh-app", name="GH", kind="app", params=actor()["params"])
    assert call(MemoryStore(), a) == "accepted"


def test_invalid_input_raises():
    with pytest.raises(ValueError):
        call(MemoryStore(), delivery="")
    with pytest.raises(ValueError):
        call(MemoryStore(), actor(params={}))


def test_logs_carry_no_payload_or_delivery_id(caplog):
    caplog.set_level(logging.DEBUG)
    call(MemoryStore(), data={"secret": "s3cr3t-token"}, delivery="deliv-xyz", author="alice")
    text = caplog.text
    assert "outcome=accepted" in text and "gh-app" in text and TYPE in text
    for leak in ("s3cr3t-token", "deliv-xyz", "alice"):
        assert leak not in text
