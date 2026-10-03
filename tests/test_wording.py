"""Template wording must not leak into the CLI self-description."""

from __future__ import annotations

from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "culture_rules"
FILES = [
    "cli/__init__.py",
    "cli/_commands/learn.py",
    "explain/catalog.py",
    "cli/_commands/overview.py",
    "cli/_commands/whoami.py",
]
BANNED = ["clonable template", "Clone it, rename the package", "clone this template"]


@pytest.mark.parametrize("rel", FILES)
def test_no_template_wording(rel: str) -> None:
    text = (PKG / rel).read_text(encoding="utf-8").lower()
    for phrase in BANNED:
        assert phrase.lower() not in text, f"{phrase!r} found in {rel}"
