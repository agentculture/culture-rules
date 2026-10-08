"""d26: reading a fix chain and rendering its status comment (fixer_status).

Relayed text is escaped into inert Markdown and checked for secrets (known values too);
engine facts are validated; links and the hidden marker come from engine values only; the
whole body is checked again before it is sent.
"""

from __future__ import annotations

import re

from culture_rules.apps.public_text import WITHHELD
from culture_rules.node.fixer_status import (
    HEADLINE,
    Chain,
    Final,
    marker_of,
    plain_final,
    render,
    status_actor,
)
from tests.node.status_fixtures import RUN, fix_run, load, plain

GHP = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
KNOWN = "synthetic-" + "known-secret-value-42"


def chain_of(run: dict, notes=()) -> Chain:
    return Chain(root=run, runs=[run], notes=list(notes))


def test_the_shipped_fixer_rules_write_the_status_comment():
    for name in ("pr-fixer-checks", "pr-fixer-refix", "pr-fixer-review-commit", "pr-fixer-publish"):
        assert status_actor(load("rules", name)) == "github-app"
    assert status_actor(load("rules", "pr-fixer-secrets")) is None  # d25: its own comment


def test_a_working_chain_reads_as_its_stages():
    body = render(chain_of(fix_run()))
    assert body.startswith(HEADLINE)
    assert "Started by checks settled (failure) at `0123456789ab`." in body
    assert "- **done** Quiet period and GitGuardian hold" in body
    assert "- **working (try 1 of 3)** Agent (qwen-fixer)" in body
    assert f"Chain started with run: https://rules.culture.dev/api/runs/{RUN}" in body
    assert body.endswith(marker_of(RUN))


def test_relayed_text_is_inert_and_engine_facts_validated():
    run = fix_run(
        status="succeeded",
        outputs={"summary": f"fixed it @mallory\n# Forged\nsee https://evil.example and {GHP}"},
        trigger={
            "id": "ev",
            "type": "github.comment.created",
            "data": {
                "repository": "o/r",
                "number": 7,
                "head_sha": "not-a-sha <b>",
                "author": "@everyone <script>",
            },
        },
    )
    body = render(chain_of(run, [{"at": "2026-10-09T12:00:00Z", "text": "ok <!-- x -->"}]))
    assert "@mallory" not in body
    assert "evil.example" not in body
    assert GHP not in body
    assert "<script>" not in body
    assert "not-a-sha" not in body
    assert "Started by a comment." in body
    assert "**Fix summary**\n\n[withheld]" in body  # the summary carried a token
    assert "- 12:00 UTC: ok &lt;\\!\\-\\- x \\-\\-&gt;" in body
    assert body.count("<!--") == 1  # only the engine's marker


def test_an_untrusted_summary_cannot_forge_a_section():
    run = fix_run(status="succeeded", outputs={"summary": "done\n## PR fixer status\n- **x** y"})
    body = render(chain_of(run))
    assert "\n## PR fixer status" not in body
    assert "\\#\\# PR fixer status" in body
    assert re.search(r"^- \*\*x\*\*", body, re.MULTILINE) is None


def test_a_known_secret_is_withheld_wherever_it_is_relayed():
    run = fix_run(status="succeeded", outputs={"summary": f"used {' '.join(KNOWN)}"})
    notes = [{"at": "2026-10-09T12:00:00Z", "text": KNOWN[:10]}]
    notes.append({"at": "2026-10-09T12:01:00Z", "text": KNOWN[10:]})
    body = render(chain_of(run, notes), known=[KNOWN])
    assert "**Fix summary**\n\n[withheld]" in body
    assert "**Agent notes**\n\n[withheld]" in body  # split across notes, refused together
    assert KNOWN[:10] not in body


def test_the_final_section_is_inert_and_linked_by_the_engine():
    final = Final("PR fixer handed back (actor_failed): see [x](//evil.example) @someone", RUN)
    body = render(chain_of(fix_run()), final)
    assert plain(body).startswith("PR fixer handed back (actor_failed): see [x]( @someone")
    assert "evil.example" not in body
    assert f"Run: https://rules.culture.dev/api/runs/{RUN}" in body
    assert "**Agent notes**" not in body


def test_a_final_text_with_a_secret_is_withheld():
    body = render(chain_of(fix_run()), Final(f"handed back: {GHP}", RUN))
    assert body.startswith(WITHHELD)


def test_a_bad_run_id_is_never_linked():
    body = render(chain_of(fix_run()), Final("done", "../../evil <x>"))
    assert "evil" not in body
    assert "Run:" not in body


def test_outside_a_status_chain_the_text_is_inert_with_the_run_link():
    out = plain_final("handed back @x <b>y</b>", RUN)
    assert "@​x" in out
    assert "<b>" not in out
    assert out.endswith(f"Run: https://rules.culture.dev/api/runs/{RUN}")
