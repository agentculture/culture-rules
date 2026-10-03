"""h48 / c66: `culture-rules learn` and the explain root name the two audiences and the why.

They must use the README's audiences and why-sentence, and describe what is on disk now,
with no "being built" / "Planned shape" wording.
"""

from __future__ import annotations

import json
import re

import pytest

from culture_rules.cli import main
from culture_rules.explain.catalog import ENTRIES
from tests.test_docs_t40 import WHY, _flat

STALE = ["being built", "planned shape", "are planned", "engine being built"]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _assert_audiences_and_why(text: str) -> None:
    flat = _norm(text)
    low = flat.lower()
    assert "operator" in low, "names the operator audience"
    assert "mesh agents" in low, "names the mesh-agent audience"
    assert WHY in flat, "carries the README's why sentence"
    for phrase in STALE:
        assert phrase not in low, phrase


def test_readme_still_carries_the_why() -> None:
    assert WHY in _flat("README.md")


def test_learn_text_names_audiences_and_why(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["learn"]) == 0
    _assert_audiences_and_why(capsys.readouterr().out)


def test_learn_json_names_audiences_and_why(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["learn", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    audiences = payload["audiences"]
    assert len(audiences) == 2
    joined = " ".join(a["who"] + " " + a["how"] for a in audiences).lower()
    assert "operator" in joined and "mesh agents" in joined
    assert WHY in _norm(payload["why"])
    _assert_audiences_and_why(json.dumps(payload))


def test_explain_root_names_audiences_and_why() -> None:
    _assert_audiences_and_why(ENTRIES[()])
