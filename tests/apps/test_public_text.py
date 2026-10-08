"""d26: text from an agent or a bridge made inert for a public PR comment.

Untrusted text is normalized first (NFKC, control/format characters and URLs dropped),
then checked (credential heuristics over several views, and known secret values with their
encodings), then escaped into inert Markdown. The adversarial cases compose wrappers over
synthetic secrets, property-style.
"""

from __future__ import annotations

import base64
import itertools
import re

import pytest

from culture_rules.apps.public_text import (
    NOTE_CAP,
    WITHHELD,
    clean_note,
    escape,
    inert_block,
    known_secret_in,
    looks_secret,
    normalize,
    status_note,
    withheld,
)

# token-shaped values are built from parts so the repo's secret scanner never sees one
GHP = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
GHS = "ghs" + "_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
GHO = "gho" + "_" + "a" * 36
PAT = (
    "github"
    + "_pat_"
    + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRS"
)
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
ENTROPIC = "q8Zr3Lp0Wv7Xs2Nk9Tb4Hy6Jm1Fd5Gc0Qe8Ra3Sw"
SHA = "0ef24e484fb36e045257ac7789c0f6ed107a1fd8"
# a synthetic known secret (as the bridge token or a grant value would be), and one in hex
KNOWN = "bridge-token-" + "not-random-words-but-secret-2026"
KNOWN_HEX = "0123456789abcdef" * 3
ZWSP = "​"


# --------------------------------------------------------------------------- heuristics


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
        "Run: https://rules.culture.dev/api/runs/run-854bf51979ee10900524f14f24f90632",
        "bri_0123456789abcdef01234567 and run-0123456789abcdef0123456789abcdef",
    ],
)
def test_ordinary_text_and_engine_ids_are_not_secrets(text):
    assert looks_secret(text) is False


# --------------------------------------------------------------------------- adversarial


def _split(token: str, sep: str, every: int = 3) -> str:
    return sep.join(token[i : i + every] for i in range(0, len(token), every))


WRAPPERS = {
    "tags": lambda t: _split(t, "<b></b>"),
    "comment": lambda t: _split(t, "<!-- x -->", 5),
    "zero_width": lambda t: _split(t, ZWSP, 2),
    "bidi": lambda t: _split(t, "\u202e", 4),
    "backslashes": lambda t: _split(t, "\\"),
    "backticks": lambda t: f"`{t}`",
    "emphasis": lambda t: _split(t, "**", 6),
    "link_text": lambda t: f"[{t}](https://evil.example)",
    "reference": lambda t: f"[{t}][1]\n\n[1]: //evil.example/x",
    "fence": lambda t: f"~~~~\n{t}\n```",
    "fullwidth": lambda t: "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in t),
}


@pytest.mark.parametrize(
    ("token", "wrap"), list(itertools.product([GHP, PAT, AWS, ENTROPIC], WRAPPERS))
)
def test_a_wrapped_token_is_still_refused(token, wrap):
    text = f"note: {WRAPPERS[wrap](token)} done"
    assert withheld(text) is True
    assert clean_note(text) is None
    assert inert_block(text, 2000) == WITHHELD


KNOWN_FORMS = {
    "plain": KNOWN,
    "piece": KNOWN[5:19],
    "spaced": " ".join(KNOWN),
    "tags": _split(KNOWN, "<i></i>", 2),
    "hex": KNOWN.encode().hex(),
    "upper_hex": KNOWN.encode().hex().upper(),
    "base64": base64.b64encode(KNOWN.encode()).decode(),
    "base64_offset": base64.b64encode(b"xx" + KNOWN.encode()).decode(),
    "urlsafe": base64.urlsafe_b64encode(b"x" + KNOWN.encode()).decode(),
    "raw_hex_value": KNOWN_HEX[3:20],
}


@pytest.mark.parametrize("form", KNOWN_FORMS)
def test_a_known_secret_is_refused_in_any_form(form):
    text = f"status: {KNOWN_FORMS[form]} ok"
    assert known_secret_in(text, [KNOWN, KNOWN_HEX]) is True
    assert clean_note(text, known=[KNOWN, KNOWN_HEX]) is None


def test_a_known_secret_split_across_notes_is_found_in_their_concatenation():
    notes = [f"part {KNOWN[:9]}", f"then {KNOWN[9:20]}", f"last {KNOWN[20:]}"]
    assert all(known_secret_in(n, [KNOWN]) is False for n in notes[:1])
    assert known_secret_in(" ".join(notes), [KNOWN]) is True


def test_unknown_values_and_short_values_match_nothing():
    assert known_secret_in("plain words", []) is False
    assert known_secret_in("short", ["short"]) is False  # too short to be matched on
    assert known_secret_in(f"fixed in {SHA}", [KNOWN]) is False


# --------------------------------------------------------------------------- escaping

MARKUP = [
    "![pic][x]\n\n[x]: //evil.example/pic.png",
    "# Forged heading\n## PR fixer status\n- **done** Push",
    "~~~~\ncode\n```",
    "````\nnested ``` fence\n````",
    "<!-- culture-rules:fixer-status run-1 --> <details><summary>x</summary></details>",
    "> quote\n    indented code\n1. list\n| a | b |\n|---|---|",
    "thanks @OriNachum and @org/team and user@example.com",
    "Title\n===\nSub\n---",
    "&#64;everyone &lt;script&gt; javascript:alert(1)",
    "[link](https://evil.example) <https://evil.example> www.evil.example //evil.example",
]
_ESCAPED = re.compile(r"(?<!\\)[`*_\[\]()#!|~]")


@pytest.mark.parametrize("text", MARKUP)
def test_untrusted_markup_is_rendered_inert(text):
    out = inert_block(text, 2000)
    assert "<" not in out
    assert ">" not in out
    assert "evil.example" not in out
    assert _ESCAPED.search(out) is None
    assert re.search(r"@(?!​)", out) is None
    assert all(not line.startswith((" ", "\t")) for line in out.splitlines())
    note = clean_note(text)
    assert note is None or "\n" not in note


def test_escaping_keeps_the_words():
    out = escape("PR fixer handed back (actor_failed): no_changes")
    assert out == r"PR fixer handed back \(actor\_failed\)\: no\_changes"


def test_normalize_drops_urls_controls_and_collapses_space():
    text = "see https://a.example/x and //b.example​\u202e  now\n\n\n\nnext"
    assert normalize(text) == "see and now\n\nnext"
    assert normalize("a\nb", one_line=True) == "a b"


# --------------------------------------------------------------------------- notes and blocks


def test_a_note_is_one_line_capped_and_trimmed():
    note = clean_note("  first line\nsecond   line\t" + "x" * 400)
    assert "\n" not in note
    assert note.startswith("first line second line")
    assert len(note) == NOTE_CAP
    assert note.endswith("…")


def test_an_empty_or_url_only_note_is_dropped():
    assert clean_note("   ") is None
    assert clean_note("https://evil.example/x") is None
    assert clean_note(None) is None
    assert clean_note(42) is None


def test_clean_note_is_idempotent():
    once = clean_note("@a [b](https://c) <d> `e` " + "f" * 300)
    assert clean_note(once) == once


def test_a_block_is_capped_and_empty_text_is_empty():
    out = inert_block("y " * 3000, 300)
    assert out.endswith("…")
    assert inert_block("   ", 300) == ""
    assert inert_block(None, 300) == ""


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


HEX48 = "0f1e2d3c4b5a6978" + "8796a5b4c3d2e1f0" + "00112233445566ff"


@pytest.mark.parametrize(
    "text",
    [
        f"<{HEX48}>",
        f"see <{HEX48[:20]}> here",
        f"&lt;{HEX48}&gt;",
        f"<b>{HEX48[10:30]}</b>",
        escape(f"<{HEX48}>"),  # what a rendered body carries
    ],
)
def test_a_known_hex_secret_in_angle_brackets_is_refused(text):
    # Codex round 2: the stripped view drops "<...>" whole and hex is no heuristic secret
    assert known_secret_in(text, [HEX48]) is True
    assert withheld(text, [HEX48]) is True
    assert clean_note(text, known=[HEX48]) is None
    assert inert_block(text, 500, [HEX48]) == WITHHELD


def test_a_token_inside_angle_brackets_is_refused():
    assert looks_secret(f"<{GHP}>") is True
    assert looks_secret(escape(f"<{GHP}>")) is True
