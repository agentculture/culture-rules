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


def test_a_known_hex_secret_in_angle_brackets_never_reaches_the_body():
    # Codex round 2: the delivering node's guard checks the entity-decoded body too
    hex48 = "0f1e2d3c4b5a6978" + "8796a5b4c3d2e1f0" + "00112233445566ff"
    run = fix_run(status="succeeded", outputs={"summary": f"see <{hex48}>"})
    body = render(chain_of(run), Final(f"handed back <{hex48}>", RUN), known=[hex48])
    assert hex48 not in body
    assert hex48[:12] not in body
    assert body.startswith(WITHHELD)


LOGIN = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R"  # a valid login that looks random


def comment_root(**over):
    data = {"repository": "o/r", "number": 7, "head_sha": "0" * 40, "author": LOGIN, **over}
    return fix_run(trigger={"id": "ev", "type": "github.comment.created", "data": data})


def test_engine_facts_are_exempt_from_the_heuristic():
    # Codex round 5: a random-looking but valid login must not withhold the body
    body = render(chain_of(comment_root()), Final("PR fixer handed back (x): y", RUN))
    assert f"Started by a comment by {LOGIN}" in body
    assert plain(body).startswith("PR fixer handed back (x): y")


def test_a_final_body_falls_back_to_a_final_built_from_engine_facts():
    # a validated fact that is a known secret: the final still reads final, never "working"
    run = comment_root(status="failed")
    body = render(chain_of(run), Final("PR fixer handed back (x): y", RUN), known=[LOGIN])
    assert LOGIN not in body
    assert body.startswith("**PR fixer finished**")
    assert HEADLINE not in body
    assert f"https://rules.culture.dev/api/runs/{RUN}" in body
    assert body.endswith(marker_of(RUN))


# A synthetic secret shaped like the live false positive (2026-10-09, tester#8): part of
# it is a fragment of the engine's own public hostname, the rest is secret.
HOSTLIKE = "zq7-rules.culture.dev-" + "k8w3p0x2v9m4"


def test_a_secret_sharing_a_piece_with_the_run_link_does_not_hide_the_comment():
    body = render(chain_of(fix_run()), known=[HOSTLIKE])
    assert body.startswith(HEADLINE)
    assert "- **done** Quiet period and GitGuardian hold" in body  # not the bare fallback
    assert f"Chain started with run: https://rules.culture.dev/api/runs/{RUN}" in body


def test_a_secret_sharing_a_piece_with_the_run_link_still_withholds_its_own_text():
    leaked = fix_run(status="succeeded", outputs={"summary": f"token {HOSTLIKE} used"})
    body = render(chain_of(leaked), known=[HOSTLIKE])
    assert HOSTLIKE not in body
    assert "k8w3p0x2v9m4" not in body
    assert WITHHELD in body


def test_relayed_text_made_of_the_engine_wording_is_still_checked():
    # Codex: the exemption must never reach relayed text (a short secret of public words)
    short = "fixerisworking"
    leaked = fix_run(status="succeeded", outputs={"summary": f"the password is {short}"})
    body = render(chain_of(leaked), known=[short])
    assert short not in body
    assert WITHHELD in body
    final = plain_final(f"The password is {short}", None, [short])
    assert short not in final


def test_a_known_secret_in_an_engine_fact_still_falls_back():
    # the literals are dropped only from the check, never the facts beside them
    author = "rules-culture-dev-k8w3p0x2"
    assert f"by {author}" in render(chain_of(comment_root(author=author)))
    body = render(chain_of(comment_root(author=author)), known=[author])
    assert "k8w3p0x2" not in body
    assert body.startswith(HEADLINE)


def test_the_public_url_must_be_a_plain_origin():
    from culture_rules.node.fixer_status import DEFAULT_PUBLIC_URL, public_url

    assert public_url("https://rules.example.test/") == "https://rules.example.test"
    assert public_url("http://127.0.0.1:8791") == "http://127.0.0.1:8791"
    assert public_url("HTTPS://Rules.Example.Test/base/") == "https://rules.example.test/base"
    assert public_url("http://[::1]:8791") == "http://[::1]:8791"
    for bad in (
        "https://user:pw@rules.example.test",
        "https://@rules.example.test",
        "https://rules.example.test/\nabcdef0123456789",
        "https://bad host",
        "https://rules.example.test:",
        "https://rules.example.test:70000",
        "https://rules.example.test?",
        "https://rules.example.test#",
        "https://[::1",
        "https://rules.example.test/?token=x",
        "https://rules.example.test/#frag",
        "ftp://rules.example.test",
        "https://",
        "rules.example.test",
        "https://rules.example.test:0",
        "https://rules.example.test/a b",
        None,
        "",
    ):
        assert public_url(bad) == DEFAULT_PUBLIC_URL, bad


def test_a_secret_crossing_the_edge_of_an_engine_literal_is_still_caught():
    # Codex round 2: removing a literal must not remove the pieces that cross its edges
    assert plain_final("done", "run-cafe012345", ["runs/run-cafe"]).startswith("PR fixer finished")
    assert "run-cafe" not in plain_final("done", "run-cafe012345", ["runs/run-cafe"])
    assert "xyz" not in plain_final("xyz", "run-123", ["xyzrunhttps"])
