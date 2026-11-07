"""The built-in ``file.read``: a file read only under configured roots."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.file_read import (
    FILE_READ_BUILTIN,
    HARD_MAX_BYTES,
    FileReadPort,
)

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)


def invoke(inputs, config):
    cfg = {"builtin": FILE_READ_BUILTIN, **config}
    ctx = InvocationContext("run-1", "wf[0]/read", "code", "spark2", config=cfg)
    return FileReadPort().invoke(inputs, "k", DEADLINE, context=ctx)


def failed(res, code):
    assert res.outcome == "failed"
    assert res.error.startswith(f"{code}:"), res.error
    assert res.retryable is False


def test_text_file_under_the_root_is_read(tmp_path):
    (tmp_path / "a.txt").write_text("héllo", encoding="utf-8")
    res = invoke({"path": str(tmp_path / "a.txt")}, {"root": str(tmp_path)})
    assert res.outcome == "completed", res.error
    assert dict(res.output) == {"content": "héllo", "bytes": 6}


def test_relative_path_is_under_the_first_root(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    res = invoke({"path": "a.txt"}, {"roots": [str(tmp_path)]})
    assert res.output["content"] == "x"


def test_any_one_root_suffices(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (two / "b.txt").write_text("b", encoding="utf-8")
    res = invoke({"path": str(two / "b.txt")}, {"roots": [str(one), str(two)]})
    assert res.output["content"] == "b"


def test_path_outside_every_root_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    failed(invoke({"path": str(tmp_path / "out.txt")}, {"root": str(root)}), "path_refused")


def test_dotdot_path_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    failed(invoke({"path": "../out.txt"}, {"root": str(root)}), "path_refused")


def test_symlink_escaping_the_root_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(tmp_path / "out.txt")
    failed(invoke({"path": str(root / "link.txt")}, {"root": str(root)}), "path_refused")


def test_directory_is_refused(tmp_path):
    (tmp_path / "d").mkdir()
    failed(invoke({"path": str(tmp_path / "d")}, {"root": str(tmp_path)}), "path_refused")


def test_oversize_file_is_refused_before_reading(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 11, encoding="utf-8")
    res = invoke({"path": "big.txt"}, {"root": str(tmp_path), "max_bytes": 10})
    failed(res, "too_large")


def test_file_at_the_cap_is_read(tmp_path):
    (tmp_path / "ok.txt").write_text("x" * 10, encoding="utf-8")
    res = invoke({"path": "ok.txt"}, {"root": str(tmp_path), "max_bytes": 10})
    assert res.output["bytes"] == 10


def test_undecodable_file_is_not_text(tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe\x00")
    failed(invoke({"path": "b.bin"}, {"root": str(tmp_path)}), "not_text")


def test_json_mode_returns_the_value(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps({"a": [1, 2]}), encoding="utf-8")
    res = invoke({"path": "d.json"}, {"root": str(tmp_path), "mode": "json"})
    assert res.output["value"] == {"a": [1, 2]}


def test_json_mode_with_fields(tmp_path):
    doc = {"a": {"b": [1, 2]}, "n": 3}
    (tmp_path / "d.json").write_text(json.dumps(doc), encoding="utf-8")
    cfg = {"root": str(tmp_path), "mode": "json", "fields": {"b": "a.b", "n": "n", "x": "q"}}
    res = invoke({"path": "d.json"}, cfg)
    assert res.output["fields"] == {"b": [1, 2], "n": 3, "x": None}


def test_invalid_json_fails(tmp_path):
    (tmp_path / "d.json").write_text("{nope", encoding="utf-8")
    failed(invoke({"path": "d.json"}, {"root": str(tmp_path), "mode": "json"}), "not_json")


def test_missing_roots_fail(tmp_path):
    failed(invoke({"path": "a.txt"}, {}), "bad_params")


def test_relative_root_fails(tmp_path):
    failed(invoke({"path": "a.txt"}, {"root": "rel"}), "bad_params")


def test_bad_mode_fails(tmp_path):
    failed(invoke({"path": "a"}, {"root": str(tmp_path), "mode": "xml"}), "bad_params")


def test_fields_need_json_mode(tmp_path):
    cfg = {"root": str(tmp_path), "fields": {"a": "a"}}
    failed(invoke({"path": "a"}, cfg), "bad_params")


def test_cap_above_the_hard_maximum_fails(tmp_path):
    cfg = {"root": str(tmp_path), "max_bytes": HARD_MAX_BYTES + 1}
    failed(invoke({"path": "a"}, cfg), "bad_params")


def test_roots_come_from_config_not_input(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    res = invoke({"path": str(tmp_path / "out.txt"), "root": str(tmp_path)}, {"root": str(root)})
    failed(res, "path_refused")
