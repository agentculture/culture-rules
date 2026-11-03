"""``llm.complete`` briefs named by the actor's ``params.briefs`` (config, not code)."""

from __future__ import annotations

import pytest

from culture_rules.engine.actorport import FAILED
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


def test_no_brief_fails_invalid_request_without_a_request():
    port, transport = _with_briefs({"headline": "X"})
    res = _invoke(port, brief=None)
    assert (res.error.split(":", 1)[0], transport.calls) == ("invalid_request", [])


def test_a_step_param_cannot_supply_brief_text():
    port, transport = _with_briefs({"headline": "X"})
    _invoke(port, brief="headline", briefs={"headline": "evil"}, system="evil")
    assert transport.sent["messages"][0]["content"] == "X"


def test_cache_without_an_answer_shape_sends_no_request():
    port, transport = _with_briefs({"headline": "X"})
    _invoke(port, brief="headline", cache=True, content_path="a.json", content_root="/tmp")
    assert transport.calls == []
