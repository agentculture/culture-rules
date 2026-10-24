"""transcript_file.read_excerpt: the transcript's text, read under a root, cut to a limit (d2)."""

from __future__ import annotations

import json

import pytest

from culture_rules.node.actions.transcript_file import ExcerptRefused, read_excerpt

SHA = "cd" * 32


def _write(root, texts, name="transcript.m.json"):
    folder = root / SHA
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": 1,
        "audio_sha256": SHA,
        "model": "m",
        "segments": [{"start": i, "end": i + 1, "text": t} for i, t in enumerate(texts)],
    }
    path = folder / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_joins_the_non_empty_segment_texts_one_per_line(tmp_path):
    path = _write(tmp_path, ["first line", "", "  ", "second line"])
    assert read_excerpt(str(path), str(tmp_path), 1000) == "first line\nsecond line"


def test_cuts_the_text_to_the_limit(tmp_path):
    path = _write(tmp_path, ["a" * 30, "b" * 30])
    assert read_excerpt(str(path), str(tmp_path), 40) == "a" * 30 + "\n" + "b" * 9


def test_a_path_outside_the_root_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = _write(tmp_path / "elsewhere", ["text"])
    with pytest.raises(ExcerptRefused) as exc:
        read_excerpt(str(path), str(root), 100)
    assert exc.value.code == "path_refused"


def test_a_symlink_leaving_the_root_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    real = _write(tmp_path / "elsewhere", ["text"])
    link = root / "link.json"
    link.symlink_to(real)
    with pytest.raises(ExcerptRefused) as exc:
        read_excerpt(str(link), str(root), 100)
    assert exc.value.code == "path_refused"


@pytest.mark.parametrize("path", ["relative.json", "", None, 5])
def test_a_bad_path_is_refused(tmp_path, path):
    with pytest.raises(ExcerptRefused) as exc:
        read_excerpt(path, str(tmp_path), 100)
    assert exc.value.code == "path_refused"


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(ExcerptRefused) as exc:
        read_excerpt(str(tmp_path / SHA / "none.json"), str(tmp_path), 100)
    assert exc.value.code == "path_refused"


def test_a_file_that_is_not_a_schema_1_transcript_is_invalid(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"schema": 2, "segments": []}), encoding="utf-8")
    with pytest.raises(ExcerptRefused) as exc:
        read_excerpt(str(path), str(tmp_path), 100)
    assert exc.value.code == "invalid_request"


def test_an_unreadable_file_is_invalid(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ExcerptRefused) as exc:
        read_excerpt(str(path), str(tmp_path), 100)
    assert exc.value.code == "invalid_request"
