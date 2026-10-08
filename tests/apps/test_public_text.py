"""d26: text from an agent or a bridge made safe for a public PR comment.

``clean_note`` is strict (one line, capped, no links, no markup; a note carrying anything
token-shaped is dropped whole). ``clean_block`` keeps lines and the engine's bare run links
but neutralises the same markup and redacts token-shaped text.
"""

from __future__ import annotations

import pytest

from culture_rules.apps.public_text import (
    NOTE_CAP,
    REDACTED,
    clean_block,
    clean_note,
    looks_secret,
    status_note,
)

# token-shaped values are built from parts so the repo's secret scanner never sees one
GHP = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
GHS = "ghs" + "_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
GHO = "gho" + "_" + "a" * 36
PAT = "github" + "_pat_" + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRS"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
ENTROPIC = "q8Zr3Lp0Wv7Xs2Nk9Tb4Hy6Jm1Fd5Gc0Qe8Ra3Sw"
SHA = "0ef24e484fb36e045257ac7789c0f6ed107a1fd8"


# --------------------------------------------------------------------------- secrets


@pytest.mark.parametrize("token", [GHP, GHS, GHO, PAT, AWS, PEM, ENTROPIC])
def test_token_shaped_text_is_recognised(token):
    assert looks_secret(f"set it to {token} then") is True


@pytest.mark.parametrize(
    "text",
    [
        f"fixed in {SHA}",
        "renamed test_handles_missing_configuration_file_gracefully",
        "src/culture_rules/node/actions/github_status_comment.py",
        "plain words only",
    ],
)
def test_ordinary_text_is_not_a_secret(text):
    assert looks_secret(text) is False


@pytest.mark.parametrize("token", [GHP, PAT, AWS, PEM, ENTROPIC])
def test_a_note_carrying_a_token_is_dropped_whole(token):
    assert clean_note(f"using {token} for the push") is None


# --------------------------------------------------------------------------- notes


def test_a_note_is_one_line_capped_and_trimmed():
    note = clean_note("  first line\nsecond   line\t" + "x" * 400)
    assert "\n" not in note
    assert note.startswith("first line second line")
    assert len(note) == NOTE_CAP
    assert note.endswith("…")


def test_mentions_never_ping():
    note = clean_note("thanks @OriNachum and @org/team, see user@example.com")
    assert "@OriNachum" not in note
    assert "@org/team" not in note
    assert "@​OriNachum" in note
    assert "user@example.com" in note  # an address is not a mention


def test_links_and_bare_urls_are_dropped_but_their_text_is_kept():
    note = clean_note("see [the docs](https://evil.example/x) and https://evil.example/y now")
    assert "evil.example" not in note
    assert "the docs" in note
    assert "[link]" in note


def test_html_images_and_code_are_neutralised():
    note = clean_note('<img src=x onerror=alert(1)>![pic](https://x/y.png) `rm -rf` <b>bold</b>')
    assert "<" not in note
    assert ">" not in note
    assert "`" not in note
    assert "![" not in note
    assert "x/y.png" not in note
    assert "bold" in note
    assert "rm -rf" in note


def test_an_empty_or_markup_only_note_is_dropped():
    assert clean_note("   ") is None
    assert clean_note("<br><hr>") is None
    assert clean_note(None) is None
    assert clean_note(42) is None


def test_clean_note_is_idempotent():
    once = clean_note("@a [b](https://c) <d> `e` " + "f" * 300)
    assert clean_note(once) == once


# --------------------------------------------------------------------------- blocks


def test_a_block_keeps_lines_and_engine_links_but_neutralises_markup():
    text = (
        "PR fixer handed back (actor_failed): changes_requested: @mallory says\n"
        "<script>x</script> ![i](https://x/i.png) [here](https://y)\n\n"
        "Run: https://rules.culture.dev/api/runs/run-1"
    )
    out = clean_block(text, cap=2000)
    assert out.splitlines()[0].startswith("PR fixer handed back (actor_failed)")
    assert "@​mallory" in out
    assert "<script>" not in out
    assert "x/i.png" not in out
    assert "here" in out
    assert "https://y" not in out
    assert "Run: https://rules.culture.dev/api/runs/run-1" in out


def test_a_block_redacts_tokens_instead_of_dropping_everything():
    out = clean_block(f"made x 3\nleaked {GHP} here", cap=500)
    assert GHP not in out
    assert REDACTED in out
    assert out.startswith("made x 3")


def test_a_block_without_urls_drops_bare_links():
    out = clean_block("see https://evil.example/x", cap=500, keep_urls=False)
    assert "evil.example" not in out


def test_an_unbalanced_fence_is_closed():
    out = clean_block("before\n```\ncode never closed", cap=500)
    assert out.count("```") % 2 == 0


def test_a_block_is_capped():
    out = clean_block("y" * 5000, cap=300)
    assert len(out) <= 300
    assert out.endswith("…")


# --------------------------------------------------------------------------- STATUS: notes


@pytest.mark.parametrize(
    ("progress", "note"),
    [
        ('tool_call: Shell: echo "STATUS: fixing the flaky test"', "fixing the flaky test"),
        ("tool_call: Shell: echo 'STATUS: rerunning the gate' (report)", "rerunning the gate"),
        (
            'tool_call_update: Shell: echo "STATUS: two of three fixed" [timeout: 5000ms]',
            "two of three fixed",
        ),
        ("tool_call: Shell: echo STATUS: unquoted note (say it)", "unquoted note"),
        ("STATUS: bare", "bare"),
    ],
)
def test_status_notes_are_read_from_progress_notes(progress, note):
    assert status_note(progress) == note


@pytest.mark.parametrize(
    "progress",
    ["tool_call: Shell: pytest -q", "plan", "tool_call: Shell: echo STATUS:", "", None, 7],
)
def test_progress_without_a_status_note_gives_none(progress):
    assert status_note(progress) is None
