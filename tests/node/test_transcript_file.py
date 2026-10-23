"""Transcript result file: schema-1 builder, atomic write, resume work dir."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from culture_rules.node.actions import transcript_file as tf

SHA = "a" * 64


def test_model_slug():
    assert tf.model_slug("openai/whisper large:v3") == "openai-whisper-large-v3"
    assert tf.model_slug("ok.name_1-x") == "ok.name_1-x"


def test_result_path(tmp_path):
    p = tf.result_path(str(tmp_path), SHA, "a/b")
    assert p == os.path.join(str(tmp_path), SHA, "transcript.a-b.json")


def test_plain_text_window_spans_window():
    doc = tf.build_result(SHA, "m", "en", 960.0, [(480, 960, {"text": "hi"})], now="T")
    assert doc["segments"] == [{"start": 480, "end": 960, "text": "hi"}]
    assert doc["schema"] == 1
    assert doc["created_at"] == "T"
    assert doc["window_count"] == 1
    assert doc["empty_window_count"] == 0
    assert doc["audio_sha256"] == SHA
    assert doc["language"] == "en"
    assert doc["duration"] == 960.0


def test_segments_offset_by_window_start():
    reply = {"text": "ab", "segments": [{"start": 1, "end": 2.5, "text": "a"}]}
    doc = tf.build_result(SHA, "m", None, 10, [(480, 960, reply)])
    assert doc["segments"] == [{"start": 481, "end": 482.5, "text": "a"}]


def test_empty_reply_kept_as_empty_segment():
    doc = tf.build_result(SHA, "m", None, 10, [(0, 480, {"text": ""}), (480, 960, {"text": "x"})])
    assert doc["segments"][0] == {"start": 0, "end": 480, "text": ""}
    assert doc["window_count"] == 2
    assert doc["empty_window_count"] == 1


def test_created_at_default_is_iso():
    doc = tf.build_result(SHA, "m", None, 1, [(0, 1, {"text": "x"})])
    assert doc["created_at"].endswith("Z")


@pytest.mark.parametrize(
    "reply",
    [
        None,
        "x",
        {},
        {"text": 3},
        {"text": "a", "segments": "no"},
        {"text": "a", "segments": [{}]},
        {"text": "a", "segments": [{"start": "1", "end": 2, "text": "a"}]},
    ],
)
def test_bad_reply(reply):
    with pytest.raises(tf.BadReply):
        tf.build_result(SHA, "m", None, 1, [(0, 1, reply)])


def test_write_atomic_returns_sha_and_reads_back(tmp_path):
    path = str(tmp_path / "d" / "t.json")
    digest = tf.write_atomic(path, {"a": 1})
    with open(path, "rb") as f:
        data = f.read()
    assert digest == hashlib.sha256(data).hexdigest()
    assert tf.read_existing(path) == {"a": 1}
    assert os.listdir(tmp_path / "d") == ["t.json"]


def test_write_atomic_failure_leaves_nothing(tmp_path):
    path = str(tmp_path / "t.json")

    class Boom:
        pass

    doc = {"a": Boom()}
    with pytest.raises(TypeError):
        tf.write_atomic(path, doc)
    assert os.listdir(tmp_path) == []


def test_write_atomic_replace_failure_cleans_temp(tmp_path, monkeypatch):
    path = str(tmp_path / "t.json")

    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        tf.write_atomic(path, {"a": 1})
    assert os.listdir(tmp_path) == []


def test_read_existing_missing_or_corrupt(tmp_path):
    assert tf.read_existing(str(tmp_path / "none.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert tf.read_existing(str(bad)) is None


def test_workdir_roundtrip_resume_clear(tmp_path):
    w = tf.WorkDir(str(tmp_path), SHA, "a/b")
    assert w.get(3) is None
    w.put(3, {"text": "x"})
    assert w.get(3) == {"text": "x"}
    assert w.path.endswith(os.path.join(SHA, ".work.a-b"))
    assert os.path.exists(os.path.join(w.path, "window-0003.json"))
    again = tf.WorkDir(str(tmp_path), SHA, "a/b")
    assert again.get(3) == {"text": "x"}
    again.clear()
    assert not os.path.exists(w.path)
    again.clear()  # idempotent
    assert json.dumps(again.get(3)) == "null"
