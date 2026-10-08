"""Text rendering of verb results."""

from __future__ import annotations

import json

from culture_rules.cli._build import render_text


def test_a_client_side_dry_run_names_the_request_it_would_send():
    out = render_text(
        {"verb": "rules create", "dry_run": True, "would": {"method": "POST", "path": "/rules"}}
    )
    assert out.startswith("dry-run: rules create would POST /rules\n")


def test_a_server_side_dry_run_has_no_request_and_says_nothing_changed():
    out = render_text({"verb": "runs backfill-ids", "dry_run": True, "result": {"count": 2}})
    assert out.startswith("dry-run: runs backfill-ids (nothing was changed)\n")
    assert "None" not in out.splitlines()[0]


def test_only_the_variables_noun_uses_the_variable_renderer():
    result = {"items": [{"id": "x", "value": 1, "version": 2}]}
    assert render_text(result, "rules") == render_text(result)
    assert "- x\n" not in render_text(result, "variables") + "\n"
    assert "v2 = 1" in render_text(result, "variables")
    assert "v2 = 1" not in render_text(result, "rules")


# ---------------------------------------------------------------- characterization
# (the Sonar S3776 split of render_text: every result shape, pinned)


def test_render_text_shapes():
    assert render_text({"lines": ["a", 2]}) == "a\n2"
    assert render_text({"files": {"x.json": "{}", "y": "z"}}) == "# x.json\n{}\n# y\nz"
    applied = {"applied": True, "hint": "run it again"}
    assert render_text(applied) == json.dumps(applied, indent=2, ensure_ascii=False) + (
        "\nhint: run it again"
    )
    assert render_text({"applied": True, "hint": 3}) == json.dumps(
        {"applied": True, "hint": 3}, indent=2, ensure_ascii=False
    )
    assert render_text(["é"]) == json.dumps(["é"], indent=2, ensure_ascii=False)
    assert render_text("plain") == '"plain"'


def test_render_text_items_list_their_ids_and_flags():
    result = {"items": [{"id": "a", "status": "running", "rule_id": "r"}, {"enabled": False}]}
    assert render_text(result) == "2 item(s)\n- a status=running rule_id=r\n- ? enabled=False"
    assert render_text({"items": []}, "variables") == "0 item(s)"
    versions = {"items": [{"name": "n", "version": 2, "value": [1], "updated_by": "u"}]}
    assert render_text(versions, "variables") == ("1 item(s)\n- n v2 = [1]  (by u at None)")


def test_render_text_dry_run_without_a_would():
    out = render_text({"verb": "x", "dry_run": True})
    assert out.startswith("dry-run: x (nothing was changed)\n")
    assert out.endswith("\nre-run with --apply to commit")
