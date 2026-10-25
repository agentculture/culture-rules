"""The built-in ``json.read``: named scalar fields from a JSON file under a root."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.json_read import JSON_READ_BUILTIN, MAX_BYTES, JsonReadPort

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
FIELDS = {"language": "language", "recorded_at": "recording_at", "audio": "files.audio.rel_path"}
META = {
    "language": "he",
    "recording_at": "2026-10-24T10:00:00Z",
    "files": {"audio": {"rel_path": "a/b.opus"}},
    "tags": ["x"],
}


def invoke(inputs, config=None):
    cfg = {"builtin": JSON_READ_BUILTIN, "fields": FIELDS, **(config or {})}
    ctx = InvocationContext("run-1", "wf[0]/read", "code", "spark2", config=cfg)
    return JsonReadPort().invoke(inputs, "k", DEADLINE, context=ctx)


def meta(root, doc=META, name="meta.json"):
    path = root / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def failed(res, code):
    assert res.outcome == "failed"
    assert res.error.startswith(f"{code}:"), res.error
    assert res.retryable is False


def test_named_dotted_fields_are_returned_as_scalars(tmp_path):
    path = meta(tmp_path)
    res = invoke({"path": str(path), "root": str(tmp_path)})
    assert res.outcome == "completed", res.error
    assert dict(res.output) == {
        "language": "he",
        "recorded_at": "2026-10-24T10:00:00Z",
        "audio": "a/b.opus",
    }


def test_relative_path_and_config_root(tmp_path):
    meta(tmp_path)
    res = invoke({"path": "meta.json"}, {"root": str(tmp_path)})
    assert res.outcome == "completed", res.error
    assert res.output["language"] == "he"


def test_missing_field_is_null_and_numbers_and_bools_pass(tmp_path):
    path = meta(tmp_path, {"n": 3, "ok": False, "files": [1]})
    fields = {"n": "n", "ok": "ok", "gone": "x.y", "via_list": "files.0"}
    res = invoke({"path": str(path), "root": str(tmp_path)}, {"fields": fields})
    assert dict(res.output) == {"n": 3, "ok": False, "gone": None, "via_list": None}


def test_non_scalar_value_is_refused_naming_the_field(tmp_path):
    path = meta(tmp_path)
    res = invoke({"path": str(path), "root": str(tmp_path)}, {"fields": {"t": "tags"}})
    failed(res, "not_scalar")
    assert "'t'" in res.error
    res = invoke({"path": str(path), "root": str(tmp_path)}, {"fields": {"f": "files"}})
    failed(res, "not_scalar")


def test_path_outside_root_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = meta(tmp_path)
    failed(invoke({"path": str(outside), "root": str(root)}), "path_refused")
    failed(invoke({"path": "../meta.json", "root": str(root)}), "path_refused")


def test_symlink_out_of_root_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = meta(tmp_path)
    (root / "link.json").symlink_to(outside)
    failed(invoke({"path": str(root / "link.json"), "root": str(root)}), "path_refused")


def test_missing_file_and_directory_are_refused(tmp_path):
    failed(invoke({"path": str(tmp_path / "no.json"), "root": str(tmp_path)}), "path_refused")
    (tmp_path / "d").mkdir()
    failed(invoke({"path": str(tmp_path / "d"), "root": str(tmp_path)}), "path_refused")


def test_non_json_file_fails(tmp_path):
    path = tmp_path / "meta.json"
    path.write_text("not json {", encoding="utf-8")
    failed(invoke({"path": str(path), "root": str(tmp_path)}), "not_json")


def test_too_large_is_refused_before_parsing(tmp_path):
    path = tmp_path / "big.json"
    path.write_text('{"language": "' + "x" * MAX_BYTES + '"}', encoding="utf-8")
    failed(invoke({"path": str(path), "root": str(tmp_path)}), "too_large")


def test_bad_params_fail(tmp_path):
    path = meta(tmp_path)
    good = {"path": str(path), "root": str(tmp_path)}
    failed(invoke({"root": str(tmp_path)}), "bad_params")
    failed(invoke({"path": str(path), "root": "relative"}), "bad_params")
    failed(invoke(good, {"fields": {}}), "bad_params")
    failed(invoke(good, {"fields": {"Bad Name": "language"}}), "bad_params")
    failed(invoke(good, {"fields": {"a": "x..y"}}), "bad_params")
