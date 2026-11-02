"""llm.complete's ``answer`` and ``cache`` step options: a JSON answer checked against a shape
from step config (no answer rule in code), kept beside its content file for any brief.

``RECORDING`` is test data only: the recording answer of 0.41.0 written as a shape, so the old
validator's accepted and refused answers carry over as config.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from culture_rules.engine.actorport import FAILED
from culture_rules.model.action_kinds import ACTION_KINDS
from culture_rules.node.actions.llm import AnswerRefused, parse_answer
from tests.node.test_llm_complete import (
    NOTES,
    FakeTransport,
    _actor,
    _failed,
    _from_file,
    _invoke,
    _port,
    _reply,
    _store,
    _transcript,
)

_ITEM = {"type": "text", "strip": True, "min_chars": 1, "max_chars": 300}
RECORDING = {
    "type": "object",
    "fields": {
        "summary": {"type": "lines", "required": True, "min": 1, "max": 2, "max_chars": 300},
        "word": {
            "type": "word",
            "required": True,
            "strip": True,
            "lower": True,
            "pattern": "^[A-Za-z][A-Za-z-]{1,23}$",
        },
        "facts": {"type": "list", "required": True, "max_items": 30, "items": _ITEM},
        "action_items": {"type": "list", "required": True, "max_items": 30, "items": _ITEM},
    },
}
SHAPED = {"shape": RECORDING}
ANSWER = {
    "summary": "Planning the move.",
    "word": "Moving",
    "facts": ["Truck on Friday"],
    "action_items": ["Book the truck"],
}
NORMALISED = {**ANSWER, "summary": ["Planning the move."], "word": "moving"}

_KEYS = iter(f"ak{n}" for n in range(1000))


def _rec(port, path, root, **params):
    """An answer-shaped run from a file, each call under its own idempotency key."""
    params.setdefault("answer", SHAPED)
    return _from_file(port, path, root, key=next(_KEYS), **params)


def _answer_reply(**over):
    return _reply(json.dumps({**ANSWER, **over}, ensure_ascii=False))


def _cache_files(path):
    return sorted(p.name for p in path.parent.glob("answer.*"))


def _code(res):
    return res.error.split(":", 1)[0]


# ---- the answer option ---------------------------------------------------------------------


def test_the_answer_option_returns_the_normalised_answer():
    port, _ = _port(transport=FakeTransport(body=_answer_reply()))
    res = _invoke(port, answer=SHAPED, content="hello")
    assert res.output["answer"] == NORMALISED


def test_a_fresh_answer_is_not_cached():
    port, _ = _port(transport=FakeTransport(body=_answer_reply()))
    assert _invoke(port, answer=SHAPED, content="hello").output["cached"] is False


def test_the_answer_option_keeps_the_text():
    port, _ = _port(transport=FakeTransport(body=_answer_reply()))
    assert _invoke(port, answer=SHAPED, content="hello").output["text"]


def test_without_the_answer_option_there_is_no_answer():
    port, _ = _port(transport=FakeTransport(body=_answer_reply()))
    assert "answer" not in _invoke(port, content="hello").output


def test_a_reply_that_is_not_json_fails_answer_not_json():
    port, _ = _port(transport=FakeTransport(body=_reply("not json at all")))
    res = _invoke(port, answer=SHAPED, content="hello")
    assert _failed(res) == (FAILED, "answer_not_json", False)


def test_a_reply_that_does_not_fit_fails_answer_invalid():
    port, _ = _port(transport=FakeTransport(body=_answer_reply(facts=["ok", 3])))
    res = _invoke(port, answer=SHAPED, content="hello")
    assert _failed(res) == (FAILED, "answer_invalid", False)


def test_answer_invalid_names_the_field():
    port, _ = _port(transport=FakeTransport(body=_answer_reply(facts=["ok", 3])))
    res = _invoke(port, answer=SHAPED, content="hello")
    assert res.error == "answer_invalid: facts[1]: must be text"


def test_any_shape_works_with_any_brief():
    shape = {"type": "list", "items": {"type": "int", "min": 0}}
    port, _ = _port(transport=FakeTransport(body=_reply("[1, 2]")))
    res = _invoke(port, brief=NOTES, answer={"shape": shape}, content="count")
    assert res.output["answer"] == [1, 2]


@pytest.mark.parametrize(
    "answer",
    [
        "recording",
        {"shape": "object"},
        {"shape": {"type": "nope"}},
        {"shape": {"type": "object"}},
        {"shape": RECORDING, "extra": 1},
        {},
    ],
)
def test_a_bad_answer_option_fails_invalid_request(answer):
    port, _ = _port()
    assert _code(_invoke(port, answer=answer, content="hello")) == "invalid_request"


@pytest.mark.parametrize("answer", ["recording", {"shape": {"type": "nope"}}])
def test_a_bad_answer_option_sends_no_request(answer):
    port, transport = _port()
    _invoke(port, answer=answer, content="hello")
    assert transport.calls == []


# ---- parse_answer: the old recording validator's cases, the shape given as config ----------


def test_parse_answer_drops_a_think_block_and_a_fence():
    raw = "<think>hm</think>\n```json\n" + json.dumps(ANSWER) + "\n```"
    assert parse_answer({"text": raw}, RECORDING) == NORMALISED


def test_parse_answer_accepts_empty_lists():
    out = parse_answer(json.dumps({**ANSWER, "facts": [], "action_items": []}), RECORDING)
    assert out["facts"] == []


@pytest.mark.parametrize(
    "wrap",
    ["```\n{}\n```", "```{}```", "```JSON  \n{}```", "  ```json\n{}\n```  "],
)
def test_parse_answer_unwraps_any_one_fence(wrap):
    out = parse_answer(wrap.format(json.dumps(ANSWER)).strip(), RECORDING)
    assert out["action_items"] == ["Book the truck"]


def test_parse_answer_refuses_an_unclosed_fence():
    unclosed = "```json\n" + json.dumps(ANSWER)
    with pytest.raises(AnswerRefused) as caught:
        parse_answer(unclosed, RECORDING)
    assert caught.value.code == "answer_not_json"


@pytest.mark.parametrize(
    "raw, code",
    [
        (None, "answer_not_json"),
        ("plain", "answer_not_json"),
        ("[1]", "answer_not_json"),
        (json.dumps({**ANSWER, "summary": ""}), "answer_invalid"),
        (json.dumps({**ANSWER, "summary": "a\nb\nc"}), "answer_invalid"),
        (json.dumps({**ANSWER, "summary": 3}), "answer_invalid"),
        (json.dumps({**ANSWER, "word": "שלום"}), "answer_invalid"),
        (json.dumps({**ANSWER, "word": "two words"}), "answer_invalid"),
        (json.dumps({k: v for k, v in ANSWER.items() if k != "facts"}), "answer_invalid"),
        (json.dumps({k: v for k, v in ANSWER.items() if k != "summary"}), "answer_invalid"),
        (json.dumps({**ANSWER, "facts": "one"}), "answer_invalid"),
        (json.dumps({**ANSWER, "facts": ["ok", 3]}), "answer_invalid"),
        (json.dumps({**ANSWER, "facts": [""]}), "answer_invalid"),
        (json.dumps({**ANSWER, "action_items": ["x" * 301]}), "answer_invalid"),
        (json.dumps({**ANSWER, "action_items": ["a"] * 31}), "answer_invalid"),
    ],
)
def test_parse_answer_refuses_with_a_code(raw, code):
    with pytest.raises(AnswerRefused) as info:
        parse_answer(raw, RECORDING)
    assert info.value.code == code


def test_parse_answer_names_the_failing_field():
    with pytest.raises(AnswerRefused) as info:
        parse_answer(json.dumps({**ANSWER, "action_items": ["a"] * 31}), RECORDING)
    assert info.value.detail.startswith("action_items:")


# ---- the cache option ----------------------------------------------------------------------


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_first_answer_is_stored_under_the_default_name(tmp_path):
    path = _transcript(tmp_path, ["private one"])
    port, _ = _port(transport=FakeTransport(body=_answer_reply()))
    _rec(port, path, tmp_path, cache=True)
    assert _cache_files(path) == [f"answer.recording.{_digest(path)[:16]}.json"]


def test_the_stored_doc_carries_the_default_label(tmp_path):
    path = _transcript(tmp_path, ["private one"])
    port, _ = _port(transport=FakeTransport(body=_answer_reply()))
    _rec(port, path, tmp_path, cache=True)
    doc = json.loads((path.parent / _cache_files(path)[0]).read_text(encoding="utf-8"))
    assert doc["brief"] == "recording"


def _first_then_second(tmp_path, **params):
    path = _transcript(tmp_path, ["private one"])
    first, _ = _port(transport=FakeTransport(body=_answer_reply()))
    one = _rec(first, path, tmp_path, cache=True, **params)
    other = FakeTransport(body=_answer_reply(summary="Something else."))
    second, _ = _port(transport=other)
    two = _rec(second, path, tmp_path, cache=True, **params)
    return one, two, other


def test_a_second_run_sends_no_request(tmp_path):
    _, _, other = _first_then_second(tmp_path)
    assert other.calls == []


def test_a_second_run_is_cached(tmp_path):
    _, two, _ = _first_then_second(tmp_path)
    assert two.output["cached"] is True


def test_a_second_run_returns_the_first_answer(tmp_path):
    one, two, _ = _first_then_second(tmp_path)
    assert two.output["answer"] == one.output["answer"]


def test_a_second_run_returns_the_first_model(tmp_path):
    one, two, _ = _first_then_second(tmp_path)
    assert two.output["model"] == one.output["model"]


def test_a_cached_run_spends_no_tokens(tmp_path):
    _, two, _ = _first_then_second(tmp_path)
    assert two.output["usage"] == {}


def test_the_cache_leaves_no_temporary_file(tmp_path):
    _first_then_second(tmp_path)
    assert not [p for p in tmp_path.rglob(".*") if p.is_file()]


def test_the_cache_works_for_any_brief(tmp_path):
    _, two, other = _first_then_second(tmp_path, brief="news")
    assert (two.output["cached"], other.calls) == (True, [])


def test_cache_name_and_label_come_from_step_config(tmp_path):
    path = _transcript(tmp_path, ["text"])
    port, _ = _port(transport=FakeTransport(body=_answer_reply()))
    _rec(port, path, tmp_path, cache=True, cache_name="digest", cache_label="digest-v2")
    doc = json.loads((path.parent / _cache_files(path)[0]).read_text(encoding="utf-8"))
    assert (_cache_files(path)[0].split(".")[1], doc["brief"]) == ("digest", "digest-v2")


def test_a_different_cache_label_misses(tmp_path):
    path = _transcript(tmp_path, ["text"])
    port, transport = _port(transport=FakeTransport(body=_answer_reply()))
    _rec(port, path, tmp_path, cache=True)
    _rec(port, path, tmp_path, cache=True, cache_label="other")
    assert len(transport.calls) == 2


def _write_0_41_0_cache(path):
    """A cache file exactly as 0.41.0 wrote it for ``brief: recording``."""
    digest = _digest(path)
    doc = {
        "schema": 1,
        "brief": "recording",
        "content_sha256": digest,
        "model": "old-model",
        "usage": {"prompt_tokens": 9},
        "answer": {
            "summary": ["Line one.", "Line two."],
            "word": "budget",
            "facts": ["A fact"],
            "action_items": [],
        },
    }
    file = path.parent / f"answer.recording.{digest[:16]}.json"
    file.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    return doc


def test_a_cache_file_written_by_0_41_0_reads_back_with_no_request(tmp_path):
    path = _transcript(tmp_path, ["text"])
    _write_0_41_0_cache(path)
    port, transport = _port(transport=FakeTransport(body=_answer_reply()))
    _rec(port, path, tmp_path, cache=True)
    assert transport.calls == []


def test_a_cache_file_written_by_0_41_0_returns_its_answer(tmp_path):
    path = _transcript(tmp_path, ["text"])
    doc = _write_0_41_0_cache(path)
    port, _ = _port(transport=FakeTransport(body=_answer_reply()))
    res = _rec(port, path, tmp_path, cache=True)
    assert (res.output["answer"], res.output["model"]) == (doc["answer"], "old-model")


def test_a_stored_answer_that_does_not_fit_the_shape_is_asked_again(tmp_path):
    path = _transcript(tmp_path, ["text"])
    _write_0_41_0_cache(path)
    narrow = {"shape": {**RECORDING, "fields": {**RECORDING["fields"]}}}
    narrow["shape"]["fields"]["summary"] = {**RECORDING["fields"]["summary"], "max": 1}
    port, transport = _port(transport=FakeTransport(body=_answer_reply()))
    _rec(port, path, tmp_path, cache=True, answer=narrow)
    assert len(transport.calls) == 1


def test_without_cache_true_nothing_is_stored_or_read(tmp_path):
    path = _transcript(tmp_path, ["text"])
    port, transport = _port(transport=FakeTransport(body=_answer_reply()))
    _rec(port, path, tmp_path)
    _rec(port, path, tmp_path, cache=False)
    assert (len(transport.calls), _cache_files(path)) == (2, [])


def test_a_changed_content_file_misses_the_cache(tmp_path):
    path = _transcript(tmp_path, ["text"])
    port, transport = _port(transport=FakeTransport(body=_answer_reply()))
    _rec(port, path, tmp_path, cache=True)
    _transcript(tmp_path, ["different text"])
    _rec(port, path, tmp_path, cache=True)
    assert (len(transport.calls), len(_cache_files(path))) == (2, 2)


def test_an_invalid_answer_is_not_cached(tmp_path):
    path = _transcript(tmp_path, ["text"])
    port, _ = _port(transport=FakeTransport(body=_reply("nope")))
    _rec(port, path, tmp_path, cache=True)
    assert _cache_files(path) == []


def test_a_damaged_cache_file_is_ignored_and_rewritten(tmp_path):
    path = _transcript(tmp_path, ["text"])
    port, transport = _port(transport=FakeTransport(body=_answer_reply()))
    _rec(port, path, tmp_path, cache=True)
    cache = path.parent / _cache_files(path)[0]
    cache.write_text("{broken", encoding="utf-8")
    _rec(port, path, tmp_path, cache=True)
    stored = json.loads(cache.read_text(encoding="utf-8"))["answer"]["word"]
    assert (len(transport.calls), stored) == (2, "moving")


def test_an_unwritable_folder_still_returns_the_answer(tmp_path):
    path = _transcript(tmp_path, ["text"])
    path.parent.chmod(0o500)
    try:
        port, _ = _port(transport=FakeTransport(body=_answer_reply()))
        res = _rec(port, path, tmp_path, cache=True)
    finally:
        path.parent.chmod(0o700)
    assert res.output["answer"]["word"] == "moving"


@pytest.mark.parametrize(
    "params",
    [
        {"answer": None},
        {"cache": "yes"},
        {"cache_name": "../x"},
        {"cache_name": "a.b"},
        {"cache_name": ""},
        {"cache_label": ""},
        {"cache_label": 3},
    ],
)
def test_bad_cache_params_fail_invalid_request_without_a_request(tmp_path, params):
    path = _transcript(tmp_path, ["text"])
    port, transport = _port()
    res = _rec(port, path, tmp_path, **{"cache": True, **params})
    assert (_code(res), transport.calls) == ("invalid_request", [])


def test_cache_needs_a_content_path():
    port, transport = _port()
    res = _invoke(port, answer=SHAPED, content="hello", cache=True)
    assert (_code(res), transport.calls) == ("invalid_request", [])


def test_a_missing_brief_fails_even_when_the_cache_would_answer(tmp_path):
    path = _transcript(tmp_path, ["text"])
    _write_0_41_0_cache(path)
    port, _ = _port()
    res = _rec(port, path, tmp_path, cache=True, brief=None)
    assert _code(res) == "invalid_request"


def test_an_actor_brief_text_is_never_stored(tmp_path):
    path = _transcript(tmp_path, ["text"])
    port, _ = _port(
        _store(_actor(briefs={NOTES: "SECRET-BRIEF"})), FakeTransport(body=_answer_reply())
    )
    _rec(port, path, tmp_path, cache=True)
    assert "SECRET-BRIEF" not in (path.parent / _cache_files(path)[0]).read_text(encoding="utf-8")


# ---- the catalogue -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, kind",
    [("brief", "str"), ("answer", "dict"), ("cache", "bool"), ("cache_name", "str")]
    + [("cache_label", "str")],
)
def test_the_catalogue_declares_the_options(name, kind):
    assert ACTION_KINDS["llm.complete"].params[name].type == kind


@pytest.mark.parametrize("name", ["brief", "answer", "cache", "cache_name", "cache_label"])
def test_the_options_are_optional_in_the_catalogue(name):
    assert not ACTION_KINDS["llm.complete"].params[name].required
