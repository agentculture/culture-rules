"""The built-in ``text.validate``: a verdict, never a silent drop."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.text_validate import TEXT_VALIDATE_BUILTIN, TextValidatePort

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
URL_A = "https://a.example/one"
URL_B = "https://b.example/two?x=1&y=2"
HITS = [{"url": URL_A, "title": "A"}, {"url": URL_B}]
# the old newspaper render rule, as configuration
OLD_RULE = {
    "allowed": HITS,
    "allowed_field": "url",
    "require_links": True,
    "line_shape": "linked_bullets",
    "link_schemes": ["http", "https"],
}


def run(text, config=None, inputs=None):
    cfg = {"builtin": TEXT_VALIDATE_BUILTIN, **(config or {})}
    ctx = InvocationContext("run-1", "wf[0]/check", "code", "spark2", config=cfg)
    return TextValidatePort().invoke({"text": text, **(inputs or {})}, "k", DEADLINE, context=ctx)


def verdict(text, config=None, inputs=None):
    res = run(text, config, inputs)
    assert res.outcome == "completed", res.error
    return dict(res.output)


def old(text, hits=HITS):
    return verdict(text, {**OLD_RULE, "allowed": hits})


def test_no_rules_means_ok_and_text_unchanged():
    out = verdict("anything at all")
    assert out == {"ok": True, "reasons": [], "text": "anything at all", "links": []}


def test_text_is_returned_unchanged_on_refusal():
    assert old("- [x](https://evil.example/)")["text"] == "- [x](https://evil.example/)"


def test_refusal_completes_the_step():
    assert run("nope", OLD_RULE).outcome == "completed"


def test_non_string_text_is_bad_params():
    res = run(5, OLD_RULE)
    assert res.outcome == "failed"


def test_non_string_text_error_code():
    assert run(5, OLD_RULE).error.startswith("bad_params:")


def test_non_string_text_is_not_retryable():
    assert run(5, OLD_RULE).retryable is False


def test_input_wins_over_config():
    out = verdict("plain", {"require_links": True}, {"require_links": False})
    assert out["ok"] is True


def test_allowed_without_field_is_bad_params():
    assert run("x", {"allowed": HITS}).error.startswith("bad_params:")


def test_bad_line_shape_is_bad_params():
    assert run("x", {"line_shape": "poem"}).error.startswith("bad_params:")


def test_links_are_listed_in_order():
    assert verdict(f"- [a]({URL_A}) and [b]({URL_B})")["links"] == [URL_A, URL_B]


# ---- ported from tests/node/test_newspaper_render.py (the link check, h5)


def test_valid_linked_bullets_pass():
    assert old(f"- [a]({URL_A})\n* [b]({URL_B})")["ok"] is True


def test_summary_linking_outside_its_results_is_refused():
    out = old("- [x](https://evil.example/)", [HITS[0]])
    assert out["reasons"] == ["link_not_allowed"]


def test_summary_linking_another_sections_result_is_refused():
    out = old(f"- [a]({URL_A})", [{"url": URL_B}])
    assert out["ok"] is False


def test_bullet_without_a_link_is_refused():
    out = old(f"- [a]({URL_A})\n- invented claim")
    assert out["reasons"] == ["bullet_without_link"]


def test_unlinked_prose_before_a_linked_bullet_is_refused():
    out = old(f"Markets crashed today, insiders say.\n- [a]({URL_A})")
    assert out["reasons"] == ["not_bullets"]


def test_heading_before_a_linked_bullet_is_refused():
    out = old(f"# Heading\n- [a]({URL_A})")
    assert out["reasons"] == ["not_bullets"]


def test_summary_without_any_link_is_refused():
    out = old("All quiet today.")
    assert "no_links" in out["reasons"]


def test_javascript_link_is_refused():
    js = "javascript:alert(1)"
    out = old(f"- [x]({js})", [{"url": js}, HITS[0]])
    assert out["reasons"] == ["link_not_allowed"]


def test_allowed_value_with_a_bad_scheme_is_not_usable():
    out = verdict(
        "- [x](ftp://h.example/f)", {**OLD_RULE, "allowed": [{"url": "ftp://h.example/f"}]}
    )
    assert out["reasons"] == ["link_not_allowed"]


def test_custom_scheme_list_admits_it():
    cfg = {**OLD_RULE, "allowed": [{"url": "ftp://h.example/f"}], "link_schemes": ["ftp"]}
    assert verdict("- [x](ftp://h.example/f)", cfg)["ok"] is True


def test_bullets_shape_does_not_need_links():
    assert verdict("- one\n- two", {"line_shape": "bullets"})["ok"] is True


def test_bullets_shape_refuses_prose():
    out = verdict("- one\ntwo", {"line_shape": "bullets"})
    assert out["reasons"] == ["not_bullets"]


def test_numbered_lines_count_as_bullets():
    assert verdict("1. one\n2) two", {"line_shape": "bullets"})["ok"] is True


def test_allowed_item_without_the_field_is_ignored():
    out = verdict(f"- [a]({URL_A})", {"allowed": [{"title": "x"}], "allowed_field": "url"})
    assert out["reasons"] == ["link_not_allowed"]


def test_hostless_url_is_refused():
    out = verdict("- [a](https:///x)", {"allowed": [{"url": "https:///x"}], "allowed_field": "url"})
    assert out["ok"] is False


@pytest.mark.parametrize("bad", [[], "http", [1]])
def test_bad_link_schemes_is_bad_params(bad):
    assert run("x", {"link_schemes": bad}).error.startswith("bad_params:")
