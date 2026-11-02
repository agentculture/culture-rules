"""``llm.complete`` briefs named by the actor's ``params.briefs`` (config, not code)."""

from __future__ import annotations

import pytest

from culture_rules.engine.actorport import FAILED
from culture_rules.node.actions.llm import NEWSPAPER_BRIEF, RECORDING_BRIEF
from tests.node.test_llm_complete import _actor, _invoke, _port, _store


def _with_briefs(briefs):
    return _port(_store(_actor(briefs=briefs)))


def test_an_actor_brief_is_sent_as_the_system_message():
    port, transport = _with_briefs({"headline": "X"})
    _invoke(port, brief="headline")
    assert transport.sent["messages"][0] == {"role": "system", "content": "X"}


def test_an_unknown_brief_fails_invalid_request():
    port, _ = _with_briefs({"headline": "X"})
    assert _invoke(port, brief="nope").outcome == FAILED


def test_an_unknown_brief_has_the_invalid_request_code():
    port, _ = _with_briefs({"headline": "X"})
    assert _invoke(port, brief="nope").error.split(":", 1)[0] == "invalid_request"


def test_an_unknown_brief_sends_no_request():
    port, transport = _with_briefs({"headline": "X"})
    _invoke(port, brief="nope")
    assert transport.calls == []


def test_an_unknown_brief_without_actor_briefs_sends_no_request():
    port, transport = _port()
    _invoke(port, brief="nope")
    assert transport.calls == []


@pytest.mark.parametrize("bad", ["", "   ", 5, None, ["X"]])
def test_a_bad_actor_brief_text_sends_no_request(bad):
    port, transport = _with_briefs({"headline": bad})
    _invoke(port, brief="headline")
    assert transport.calls == []


@pytest.mark.parametrize("bad", ["", 5, None])
def test_a_bad_actor_brief_text_fails_invalid_request(bad):
    port, _ = _with_briefs({"headline": bad})
    assert _invoke(port, brief="headline").error.split(":", 1)[0] == "invalid_request"


def test_a_non_mapping_briefs_param_sends_no_request():
    port, transport = _with_briefs(["headline"])
    _invoke(port, brief="headline")
    assert transport.calls == []


def test_a_non_string_brief_name_sends_no_request():
    port, transport = _with_briefs({"headline": "X"})
    _invoke(port, brief=["headline"])
    assert transport.calls == []


def test_an_actor_brief_named_like_a_code_brief_does_not_replace_it():
    port, transport = _with_briefs({"newspaper": "evil", "recording": "evil"})
    _invoke(port, brief="newspaper")
    assert transport.sent["messages"][0]["content"] == NEWSPAPER_BRIEF


def test_an_actor_brief_named_recording_does_not_replace_the_code_brief():
    port, transport = _with_briefs({"recording": "evil"})
    _invoke(port, brief="recording")
    assert transport.sent["messages"][0]["content"] == RECORDING_BRIEF


def test_no_brief_still_selects_the_newspaper_code_brief():
    port, transport = _with_briefs({"headline": "X"})
    _invoke(port)
    assert transport.sent["messages"][0]["content"] == NEWSPAPER_BRIEF


def test_a_step_param_cannot_supply_brief_text():
    port, transport = _with_briefs({"headline": "X"})
    _invoke(port, brief="headline", briefs={"headline": "evil"}, system="evil")
    assert transport.sent["messages"][0]["content"] == "X"


def test_an_actor_brief_is_not_cached_like_the_recording_brief():
    port, transport = _with_briefs({"headline": "X"})
    _invoke(port, brief="headline", cache=True, content_path="a.json", content_root="/tmp")
    assert transport.calls == []
