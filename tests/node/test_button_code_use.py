"""Tests for the built-in ``button.code.use`` step (discord/code_use.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from culture_rules.engine.actorport import InvocationContext
from culture_rules.extensions.discord import buttons as db
from culture_rules.extensions.discord.code_use import (
    BUTTON_CODE_USE_BUILTIN,
    NOT_CLICK,
    ButtonCodeUsePort,
)
from culture_rules.extensions.discord.filters import BUTTON_CLICKED
from culture_rules.node.firing import run_id_for
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
RULE = "piloop-approve"
RULE2 = "piloop-reject"
EVENT = "ev-1"
EVENT2 = "ev-2"
MESSAGE_ID = "4242"
CUSTOM_ID = "piloop:approve"
VALUE = {"proposal": "p-20261111-120000", "decision": "approve"}


def ctx(run_id, config=None):
    return InvocationContext(
        run_id=run_id, step_id="use", kind="action", host="core", config=config or {}
    )


def issue_code(store, value=VALUE, custom_id=CUSTOM_ID, now=NOW):
    """The code a discord.message with ``code: true`` stores for its button."""
    db.remember_buttons(store, "discord-bot", MESSAGE_ID, [(custom_id, value, True)], now=now)
    return store.get(db.BUTTONS_COLLECTION, f"discord-bot:{custom_id}")["code"]


def click_envelope(code=None, custom_id=CUSTOM_ID, value=VALUE, event=EVENT):
    """The click event as the gateway stores it: the code and the value are the bot's own,
    never the clicker's."""
    data = {"custom_id": custom_id, "author_id": "42", "code": code}
    if value is not None:
        data["value"] = value
    return {"id": event, "type": BUTTON_CLICKED, "source": "discord", "data": data}


def click_run(rule, event, envelope):
    return {
        "id": run_id_for(rule, event),
        "rule_id": rule,
        "workflow_id": "piloop-decide",
        "trigger": envelope,
        "workflow": {"id": "piloop-decide", "definition": {}},
    }


def world(store, *, code=True, value=VALUE, custom_id=CUSTOM_ID, stored_code=..., run=None):
    """The button's code, the stored click event and the run it started. ``stored_code``
    overrides the event's code (``None`` strips it) to test forged and stripped codes."""
    issued = issue_code(store, value=value, custom_id=custom_id) if code else None
    envelope = click_envelope(code=issued, custom_id=custom_id, value=value)
    if stored_code is not ...:
        envelope["data"]["code"] = stored_code
    store.put("events", {"id": envelope["id"], "envelope": envelope})
    doc = run or click_run(RULE, EVENT, envelope)
    store.put("runs", doc)
    return doc["id"]


def use(store, run_id, **inp):
    return ButtonCodeUsePort(store).invoke(inp, "k", NOW, context=ctx(run_id))


def code_doc(store):
    (doc,) = store.find(db.CODES_COLLECTION)
    return doc


# --------------------------------------------------------------------------- the happy path


def test_the_code_is_used_up_and_the_value_reported():
    s = MemoryStore()
    run_id = world(s)
    res = use(s, run_id)
    assert res.outcome == "completed", res.error
    assert res.output == {"value": VALUE, "custom_id": CUSTOM_ID, "used": True}
    assert code_doc(s)["used_by"] == run_id
    assert code_doc(s)["used_at"] is not None


def test_the_same_run_passes_again_its_retry():
    s = MemoryStore()
    run_id = world(s)
    assert use(s, run_id).outcome == "completed"
    assert use(s, run_id).outcome == "completed"


def test_a_second_run_gets_nothing():
    s = MemoryStore()
    first = world(s)
    assert use(s, first).outcome == "completed"
    envelope2 = click_envelope(code=code_doc(s)["id"], event=EVENT2)
    s.put("events", {"id": EVENT2, "envelope": envelope2})
    second_run = click_run(RULE2, EVENT2, envelope2)
    s.put("runs", second_run)
    res = use(s, second_run["id"])
    assert (res.error, res.retryable) == (db.CODE_USED, False)


# ------------------------------------------------------------------------------ the fences


def test_a_click_without_a_code_is_refused():
    s = MemoryStore()
    run_id = world(s, code=False)
    res = use(s, run_id)
    assert (res.error, res.retryable) == (db.CODE_NOT_FOUND, False)


def test_the_code_is_read_from_the_stored_event_never_from_the_run():
    s = MemoryStore()
    run_id = world(s, stored_code=None)  # the run's own trigger copy still holds the code
    assert use(s, run_id).error == db.CODE_NOT_FOUND


def test_a_forged_code_is_refused():
    s = MemoryStore()
    run_id = world(s, stored_code="forged-code")
    assert use(s, run_id).error == db.CODE_NOT_FOUND


def test_an_expired_code_is_refused():
    s = MemoryStore()
    expired = issue_code(s, now=NOW - db.BUTTON_VALUE_TTL - timedelta(days=1))
    envelope = click_envelope(code=expired)
    s.put("events", {"id": EVENT, "envelope": envelope})
    run = click_run(RULE, EVENT, envelope)
    s.put("runs", run)
    assert use(s, run["id"]).error == db.CODE_NOT_FOUND


def test_a_manual_run_is_refused():
    s = MemoryStore()
    run_id = world(
        s,
        code=False,
        run={"id": "manual-1", "rule_id": RULE, "trigger": {"type": "manual"}},
    )
    res = use(s, run_id)
    assert (res.error, res.retryable) == (NOT_CLICK, False)


def test_a_run_of_another_kind_is_refused():
    s = MemoryStore()
    run_id = world(s, run={"id": "r-1", "rule_id": RULE, "trigger": {"type": "http.request"}})
    assert use(s, run_id).error == NOT_CLICK


# ----------------------------------------------------------------------------- the value


def test_the_declared_value_must_be_held_by_the_code():
    s = MemoryStore()
    run_id = world(s)
    assert use(s, run_id, value={"proposal": VALUE["proposal"]}).outcome == "completed"


def test_a_value_the_code_does_not_hold_is_refused():
    s = MemoryStore()
    run_id = world(s)
    res = use(s, run_id, value={"proposal": "p-other"})
    assert (res.error, res.retryable) == (db.CODE_MISMATCH, False)
    assert code_doc(s)["used_at"] is None  # nothing was consumed


def test_a_declared_value_that_is_not_a_mapping_is_refused():
    s = MemoryStore()
    run_id = world(s)
    res = use(s, run_id, value="approve")
    assert res.error == "bad_params: value must be a mapping"
    assert res.retryable is False
    assert code_doc(s)["used_at"] is None


def test_the_value_may_sit_in_the_config():
    s = MemoryStore()
    run_id = world(s)
    res = ButtonCodeUsePort(s).invoke(
        {}, "k", NOW, context=ctx(run_id, config={"value": {"decision": "approve"}})
    )
    assert res.outcome == "completed", res.error


def test_the_input_wins_over_the_config():
    s = MemoryStore()
    run_id = world(s)
    res = ButtonCodeUsePort(s).invoke(
        {"value": {"proposal": "p-other"}},
        "k",
        NOW,
        context=ctx(run_id, config={"value": VALUE}),
    )
    assert res.error == db.CODE_MISMATCH


# --------------------------------------------------------------------------- registration


def test_the_builtin_is_registered_under_its_name():
    from culture_rules.extensions.builtin import install_builtins
    from culture_rules.extensions.registry import Registry

    reg = Registry()
    install_builtins(reg)
    names = [b.name for b in reg.builtins()]
    assert BUTTON_CODE_USE_BUILTIN in names
