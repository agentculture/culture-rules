"""Webhook sink on the in-memory store."""

from __future__ import annotations

import logging

import pytest

from culture_rules.engine.matching import trigger_matches
from culture_rules.events.hook_sink import HOOK_STATS_COLLECTION, event_id_for, sink
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.model.rule import Trigger
from culture_rules.store.memory import MemoryStore

TYPE = "github.pr.synchronize"


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
    # synchronize + bot author -> self_authored True
    assert by_id[event_id_for("github", "d1")]["self_authored"] is True
    # synchronize + human author -> explicit False
    assert by_id[event_id_for("github", "d2")]["self_authored"] is False


def test_self_authored_true_for_non_exempt_github_type():
    """A non-exempt github type (pr.opened) with matching author gets self_authored True."""
    s = MemoryStore()
    a = actor(
        params={
            "surface": "github",
            "events": [TYPE, "github.pr.opened"],
            "self_identity": "Culture-Bot",
        }
    )
    call(s, a=a, type="github.pr.opened", author="Culture-Bot")
    (doc,) = s.find(EVENTS_COLLECTION)
    assert doc["envelope"]["data"]["self_authored"] is True


def test_payload_cannot_forge_self_authored():
    s = MemoryStore()
    call(s, data={"self_authored": True})
    (doc,) = s.find(EVENTS_COLLECTION)
    assert doc["envelope"]["data"]["self_authored"] is False


def test_payload_cannot_forge_self_authored_non_exempt_type():
    """A forged self_authored=False on a non-exempt type must be overwritten by True."""
    s = MemoryStore()
    a = actor(
        params={
            "surface": "github",
            "events": [TYPE, "github.pr.opened"],
            "self_identity": "Culture-Bot",
        }
    )
    call(s, a=a, type="github.pr.opened", author="Culture-Bot", data={"self_authored": False})
    (doc,) = s.find(EVENTS_COLLECTION)
    # The True from the sink overrides the forged False
    assert doc["envelope"]["data"]["self_authored"] is True


def test_payload_cannot_forge_self_authored_sync_type():
    """Even on synchronize, a forged self_authored=True is overwritten by True (no change)."""
    s = MemoryStore()
    call(s, author="Culture-Bot", data={"self_authored": False})
    (doc,) = s.find(EVENTS_COLLECTION)
    # sync type + matching author -> True regardless of forged value
    assert doc["envelope"]["data"]["self_authored"] is True


def test_payload_cannot_forge_self_authored_exempt_type():
    """A forged self_authored=True on an exempt check type must be overwritten by False."""
    s = MemoryStore()
    a = actor(
        params={
            "surface": "github",
            "events": [TYPE, "github.checks.suite_completed"],
            "self_identity": "Culture-Bot",
        }
    )
    call(
        s,
        a=a,
        type="github.checks.suite_completed",
        author="Culture-Bot",
        data={"self_authored": True},
    )
    (doc,) = s.find(EVENTS_COLLECTION)
    # Exempt type + matching author -> False regardless of forged value
    assert doc["envelope"]["data"]["self_authored"] is False
    assert "self_authored" in doc["envelope"]["data"]


def test_exempt_check_types_get_self_authored_false():
    """Exempt check-completion types store self_authored=False even when author matches."""
    s = MemoryStore()
    a = actor(
        params={
            "surface": "github",
            "events": [TYPE, "github.checks.suite_completed", "github.checks.workflow_completed"],
            "self_identity": "Culture-Bot",
        }
    )
    # check_suite exempt type
    sink(s, a, "github.checks.suite_completed", {"n": 1}, "d-suite", "Culture-Bot")
    # workflow_run exempt type
    sink(s, a, "github.checks.workflow_completed", {"n": 2}, "d-workflow", "Culture-Bot")
    by_id = {d["id"]: d["envelope"]["data"] for d in s.find(EVENTS_COLLECTION)}
    suite_doc = by_id[event_id_for("github", "d-suite")]
    workflow_doc = by_id[event_id_for("github", "d-workflow")]
    assert suite_doc["self_authored"] is False
    assert workflow_doc["self_authored"] is False
    # The field must be present and explicit (not absent)
    assert "self_authored" in suite_doc
    assert "self_authored" in workflow_doc


def test_jira_matching_author_gets_self_authored_true():
    """A jira event from the bot has self_authored=True."""
    s = MemoryStore()
    a = actor(
        params={
            "surface": "jira",
            "events": ["jira.issue.created", "jira.issue.updated", "jira.comment.created"],
            "self_identity": "bot-acct",
        }
    )
    sink(s, a, "jira.issue.updated", {"key": "OPS-7"}, "d-jira", "bot-acct")
    (doc,) = s.find(EVENTS_COLLECTION)
    assert doc["envelope"]["data"]["self_authored"] is True


def test_discord_matching_author_gets_self_authored_true():
    """A discord event from the bot has self_authored=True."""
    s = MemoryStore()
    a = actor(
        params={
            "surface": "discord",
            "events": ["discord.message.created"],
            "self_identity": "MyBot",
        }
    )
    sink(s, a, "discord.message.created", {"content": "hello"}, "d-disc", "MyBot")
    (doc,) = s.find(EVENTS_COLLECTION)
    assert doc["envelope"]["data"]["self_authored"] is True


def test_case_insensitive_matching_preserved():
    """Author matching against self_identity is case-insensitive for all non-exempt types."""
    s = MemoryStore()
    a = actor(
        params={
            "surface": "github",
            "events": [TYPE, "github.pr.opened", "github.comment.created"],
            "self_identity": "Culture-Bot",
        }
    )
    cases = [
        ("Culture-Bot", True),
        ("culture-bot", True),
        ("CULTURE-BOT", True),
        ("CuLtUrE-Bot", True),
        ("alice", False),
    ]
    by_id = {}
    for i, (author, expected) in enumerate(cases):
        sink(s, a, TYPE, {"i": i}, f"d-case-{i}", author)
        by_id[f"d-case-{i}"] = author
    for did, author in by_id.items():
        data = s.find(EVENTS_COLLECTION)[list(by_id.keys()).index(did)]["envelope"]["data"]
        if author != "alice":
            assert data["self_authored"] is True, f"author={author}"
        else:
            assert data["self_authored"] is False, f"author={author}"


def test_envelope_shape_and_matching():
    s = MemoryStore()
    call(s, data={"number": 7})
    (doc,) = s.find(EVENTS_COLLECTION)
    env = doc["envelope"]
    assert doc["id"] == env["id"] == event_id_for("github", "d1")
    assert env["id"].startswith("hook_github_")
    assert env["type"] == TYPE
    assert env["source"] == "app://gh-app"
    assert env["data"] == {
        "number": 7,
        "delivery_id": "d1",
        "actor": "gh-app",
        "self_authored": False,
    }
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
    store = MemoryStore()
    bad_actor = actor(params={})
    with pytest.raises(ValueError):
        call(store, delivery="")
    with pytest.raises(ValueError):
        call(store, bad_actor)


def test_logs_carry_no_payload_or_delivery_id(caplog):
    caplog.set_level(logging.DEBUG)
    call(MemoryStore(), data={"secret": "s3cr3t-token"}, delivery="deliv-xyz", author="alice")
    text = caplog.text
    assert "outcome=accepted" in text
    assert "gh-app" in text
    assert TYPE in text
    for leak in ("s3cr3t-token", "deliv-xyz", "alice"):
        assert leak not in text


def test_an_app_may_not_inject_the_engines_run_events_even_if_it_declares_them():
    store = MemoryStore()
    a = actor(params={"surface": "github", "events": ["rules.run.succeeded"]})
    assert call(store, a, type="rules.run.succeeded") == "quarantined"
    assert store.find(EVENTS_COLLECTION) == []
