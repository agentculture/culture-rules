"""#87: the newspaper and transcript books keep their exact bytes.

The two renderers share one deterministic EPUB writer (``node/actions/_epub.py``). These
digests were taken before the writer was lifted out, so a change to the shared helpers that
moves a single byte of either book (or of the newspaper's HTML, or the transcript Markdown)
fails here. Update a digest only when a book's bytes are meant to change.

A book is pinned by its members, not by the zip file: each member's name, compression, time,
permissions and uncompressed bytes, in order. The deflated bytes also depend on the zlib build
(CI's may differ from a workstation's), which the renderers do not control.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from typing import Any

import pytest

from culture_rules.node.actions import newspaper_render, transcript_render
from culture_rules.node.actions.transcript_file import build_result

HOSTILE = "<b>&\"'</b>\x01"


def _sha(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def _members(book: bytes) -> str:
    """One digest over the book's members (see the module docstring)."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(io.BytesIO(book)) as archive:
        for info in archive.infolist():
            meta = (info.filename, info.compress_type, info.date_time, info.external_attr)
            digest.update(repr(meta).encode("utf-8"))
            digest.update(_sha(archive.read(info)).encode("ascii"))
    return digest.hexdigest()


def _issue(**extra: Any) -> dict[str, Any]:
    url = "https://a.example/story?id=1&x=2"
    hit = {"title": f"Story {HOSTILE}", "url": url, "snippet": "A snippet", "age": "2h"}
    return newspaper_render.render_issue(
        {
            "date": "2026-10-11",
            "title": f"Daily {HOSTILE}",
            "sections": [
                {"name": "AI", "results": [hit], "summary": f"- **Big**: [A]({url})"},
                {"name": "Robots", "results": [hit], "summary": None},
                {"name": "Empty", "results": []},
            ],
            **extra,
        }
    )


def _transcript(language: str | None, fmt: str | None = None) -> dict[str, Any]:
    segs = [
        {"start": 0.0, "end": 1.0, "text": "שלום עולם"},
        {"start": 1.0, "end": 2.0, "text": f"two {HOSTILE}"},
    ]
    doc = build_result("ab" * 32, "m", language, 9.0, [(0.0, 9.0, {"text": "x", "segments": segs})])
    answer = {"summary": f"A line {HOSTILE}.\nSecond.", "word": "Topic", "facts": ["f <1>"]}
    return transcript_render.render_transcript(
        doc, recorded_at="2026-10-23T22:30:00Z", summary={"text": json.dumps(answer)}, format=fmt
    )


def test_the_newspaper_epub_bytes_are_pinned() -> None:
    book = base64.b64decode(_issue()["epub_base64"])
    assert _members(book) == "c98240f75a8bee9959ea2fe3c46bbb015d4188fcfce6210ef7c32dc716e9a85d"


def test_the_newspaper_html_is_pinned() -> None:
    page = _issue()["html"]
    assert _sha(page) == "3b8dd1ebcb2bbc901c8c7faaabc3cd83ee17d516172f6bcaf203af9b00bf5970"


@pytest.mark.parametrize(
    ("language", "digest"),
    [
        ("he", "5e88621af44c0028b73c6726d6b84b73320ee4eca993b4c10269899adfc6f1ff"),
        ("en", "658195d94f6834b799125853a0678096527162d046bd386f63bb89a5360e6975"),
        (None, "5e88621af44c0028b73c6726d6b84b73320ee4eca993b4c10269899adfc6f1ff"),
    ],
)
def test_the_transcript_epub_bytes_are_pinned(language: str | None, digest: str) -> None:
    assert _members(base64.b64decode(_transcript(language)["content_base64"])) == digest


@pytest.mark.parametrize(
    ("language", "digest"),
    [
        ("he", "82a0b8fff4059d1e1bcaf4872cb48f89b33278c72225f038fe6b8f9dbf61db25"),
        ("en", "ec9128471f0532abbb2cef5250de8236b91dd45f589add445811775a89fc9eca"),
    ],
)
def test_the_transcript_markdown_bytes_are_pinned(language: str, digest: str) -> None:
    assert _sha(base64.b64decode(_transcript(language, "md")["content_base64"])) == digest
