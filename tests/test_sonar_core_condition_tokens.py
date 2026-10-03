"""Characterization: the split per-kind tokenizer matches the old single-regex one."""

from __future__ import annotations

import random
import re

import pytest

from culture_rules.model import condition
from culture_rules.model.condition import ConditionParseError

_OLD_TOKEN = re.compile(
    r"""\s*(?:
    (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>&&|\|\||==|!=|<=|>=|<|>|!|\(|\)|\[|\]|,|\.)
    )""",
    re.VERBOSE,
)


def _old_tokenize(text: str) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    pos = 0
    while True:
        m = _OLD_TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                bad = pos + len(text[pos:]) - len(text[pos:].lstrip())
                raise ConditionParseError(f"unexpected character {text[bad]!r}", bad)
            break
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind), m.start(kind)))
        pos = m.end()
    out.append(("end", "", len(text)))
    return out


def _outcome(fn, text):
    try:
        return ("ok", fn(text))
    except ConditionParseError as exc:
        return ("err", str(exc), exc.args)


CASES = [
    "",
    "   ",
    'a.b == "x" && c != 1.5e-3 || !(d < -2)',
    "exists(trigger.pr) && x in [1, 2, 3]",
    'matches(a, "^f\\"oo$")',
    "x >= 10 && y <= 2 && z > 1 && w < 0",
    "é == 1",
    "a_é",
    "x == ٣",
    "@",
    "a = b",
    '"unterminated',
    "a\n&&\tb",
    "1.2.3",
    "-x",
    "foo\u00a0bar",
]

_ALPHABET = list('ab_Z09 .,()[]!=<>&|"\\-eE+é٣\t\n@#') + ["&&", "||", "=="]


@pytest.mark.parametrize("text", CASES)
def test_tokenize_matches_old(text):
    assert _outcome(condition._tokenize, text) == _outcome(_old_tokenize, text)


def test_tokenize_matches_old_fuzz():
    rng = random.Random(1234)  # deterministic
    for _ in range(3000):
        text = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 24)))
        assert _outcome(condition._tokenize, text) == _outcome(_old_tokenize, text), text
