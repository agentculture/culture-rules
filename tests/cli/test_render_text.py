"""Text rendering of verb results."""

from __future__ import annotations

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
