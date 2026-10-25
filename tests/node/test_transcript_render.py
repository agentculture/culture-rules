"""``transcript.render``: a schema-1 transcript and a summary as a deterministic EPUB."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from culture_rules.engine.actorport import COMPLETED, InvocationContext
from culture_rules.model.action_kinds import ACTION_KINDS
from culture_rules.node.actions import transcript_render as tr
from culture_rules.node.actions.recording_answer import parse_recording_answer
from culture_rules.node.actions.transcript_file import build_result
from culture_rules.node.actions.transcript_render import TranscriptRenderPort, parse_summary

SHA = "ab" * 32
PRIVATE_TEXT = "sentinel-private-words"
PRIVATE_SUMMARY = "sentinel-summary-words"
AT = "2026-10-23T22:30:00Z"


def _doc(language: str | None = "he", texts: tuple[str, ...] = ("one", "two")) -> dict[str, Any]:
    segs = [{"start": float(i), "end": float(i) + 1, "text": t} for i, t in enumerate(texts)]
    return build_result(SHA, "m", language, 9.0, [(0.0, 9.0, {"text": "x", "segments": segs})])


def _reply(summary: str = "A line.", word: str = "Topic") -> dict[str, str]:
    return {"text": json.dumps({"summary": summary, "word": word})}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    d = tmp_path / "transcripts"
    d.mkdir()
    return d


def _write(root: Path, doc: dict[str, Any]) -> Path:
    path = root / SHA / "transcript.m.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _invoke(root: Path, path: Path | str, **extra: Any):
    params = {"result_path": str(path), "root": str(root), "recorded_at": AT, **extra}
    ctx = InvocationContext(run_id="r", step_id="s", kind="action", host="h")
    return TranscriptRenderPort().invoke(params, "k", None, context=ctx)


def _zip(out: dict[str, Any]) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(base64.b64decode(out["content_base64"])))


def test_registered_kind() -> None:
    assert "transcript.render" in ACTION_KINDS


def test_render_is_byte_identical() -> None:
    a = tr.render_transcript(_doc(), recorded_at=AT, summary=_reply())
    b = tr.render_transcript(_doc(), recorded_at=AT, summary=_reply())
    assert a == b
    z = _zip(a)
    names = z.namelist()
    assert names[0] == "mimetype"
    assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_he_is_rtl_and_en_is_ltr() -> None:
    for language in ("he", None, ""):
        z = _zip(tr.render_transcript(_doc(language), recorded_at=AT))
        opf = z.read("OEBPS/content.opf").decode()
        assert 'page-progression-direction="rtl"' in opf
        assert "<dc:language>he</dc:language>" in opf
        page = z.read("OEBPS/c2.xhtml").decode()
        assert 'lang="he"' in page
        assert 'xml:lang="he"' in page
        assert 'dir="rtl"' in page
    z = _zip(tr.render_transcript(_doc("en"), recorded_at=AT))
    assert 'page-progression-direction="ltr"' in z.read("OEBPS/content.opf").decode()
    assert 'dir="ltr"' in z.read("OEBPS/c2.xhtml").decode()
    # the step's language wins over the file's
    z = _zip(tr.render_transcript(_doc("he"), recorded_at=AT, language="en"))
    assert 'dir="ltr"' in z.read("OEBPS/c1.xhtml").decode()


def test_title_uses_jerusalem_date_time_and_lowercased_word() -> None:
    out = tr.render_transcript(_doc(), recorded_at=AT, summary=_reply(word="Garden-Notes"))
    assert out["date"] == "2026-10-24"
    assert out["title"] == "2026-10-24 0130 garden-notes"
    assert out["word"] == "garden-notes"
    assert out["summary_used"] is True


@pytest.mark.parametrize("bad", [None, "", "yesterday", "2026-10-23T22:30:00"])
def test_bad_recorded_at_fails_invalid_request(bad: Any, root: Path) -> None:
    path = _write(root, _doc())
    result = _invoke(root, path, recorded_at=bad)
    assert result.outcome != COMPLETED
    assert "invalid_request" in str(result.error)


@pytest.mark.parametrize(
    "reply",
    [
        None,
        "not json",
        {"text": "[]"},
        _reply(word="ab1"),
        _reply(word="a"),
        _reply(word="עברית"),
        _reply(word="x" * 30),
        _reply(summary="a\nb\nc"),
        _reply(summary="  \n "),
        {"text": json.dumps({"summary": "ok"})},
        {"text": json.dumps({"summary": 3, "word": "ok"})},
    ],
)
def test_unusable_summary_falls_back(reply: Any) -> None:
    out = tr.render_transcript(_doc(), recorded_at=AT, summary=reply)
    assert out["title"] == "2026-10-24 0130 recording"
    assert out["summary_used"] is False
    assert tr.SUMMARY_UNAVAILABLE in _zip(out).read("OEBPS/c1.xhtml").decode()


def test_tolerates_fence_and_think_and_plain_string() -> None:
    body = json.dumps({"summary": "L1\nL2", "word": "Park"})
    for text in (f"```json\n{body}\n```", f"<think>hmm</think>\n{body}", body):
        assert parse_summary(text) == (["L1", "L2"], "park")
        assert parse_summary({"text": text}) == (["L1", "L2"], "park")


def test_segments_are_ordered_paragraphs_and_empty_skipped_and_escaped() -> None:
    doc = _doc(texts=("first <b>", "  ", "", "second & third"))
    out = tr.render_transcript(doc, recorded_at=AT)
    page = _zip(out).read("OEBPS/c2.xhtml").decode()
    assert out["segment_count"] == 2
    assert page.index("first &lt;b&gt;") < page.index("second &amp; third")
    assert page.count("<p>") == 2


def test_port_outputs_hold_no_text(root: Path) -> None:
    doc = _doc(texts=(PRIVATE_TEXT,))
    path = _write(root, doc)
    result = _invoke(root, path, summary=_reply(summary=PRIVATE_SUMMARY), language="he")
    assert result.outcome == COMPLETED
    out = result.output
    assert set(out) == {
        "content_base64",
        "title",
        "date",
        "word",
        "summary_used",
        "segment_count",
    }
    flat = json.dumps({k: v for k, v in out.items() if k != "content_base64"})
    assert PRIVATE_TEXT not in flat
    assert PRIVATE_SUMMARY not in flat
    assert PRIVATE_SUMMARY in _zip(out).read("OEBPS/c1.xhtml").decode()


def test_path_outside_root_refused_before_reading(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "other.json"
    outside.write_text("not json at all")
    result = _invoke(root, outside)
    assert result.outcome != COMPLETED
    assert "path_refused" in str(result.error)


def test_symlink_escape_refused(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "other.json"
    outside.write_text(json.dumps(_doc()))
    link = root / "link.json"
    link.symlink_to(outside)
    result = _invoke(root, link)
    assert result.outcome != COMPLETED
    assert "path_refused" in str(result.error)


def test_missing_root_or_relative_path_refused(root: Path) -> None:
    path = _write(root, _doc())
    ctx = InvocationContext(run_id="r", step_id="s", kind="action", host="h")
    port = TranscriptRenderPort()
    no_root = port.invoke({"result_path": str(path), "recorded_at": AT}, "k", None, context=ctx)
    assert "path_refused" in str(no_root.error)
    rel = _invoke(root, "transcript.json")
    assert "path_refused" in str(rel.error)


def test_bad_transcript_fails_invalid_request(root: Path) -> None:
    path = root / "t.json"
    path.write_text("{nope")
    assert "invalid_request" in str(_invoke(root, path).error)
    path.write_text(json.dumps({"schema": 2}))
    assert "invalid_request" in str(_invoke(root, path).error)


def test_actor_root_is_a_second_boundary(root: Path, tmp_path: Path) -> None:
    from culture_rules.node.actors import ACTORS_COLLECTION
    from culture_rules.store.memory import MemoryStore

    narrow = root / "narrow"
    narrow.mkdir()
    store = MemoryStore()
    store.put(
        ACTORS_COLLECTION,
        {
            "id": "reader",
            "name": "reader",
            "kind": "agent",
            "enabled": True,
            "params": {"transcripts_root": str(narrow)},
        },
    )
    path = _write(root, _doc())  # under root, not under the actor's narrower root
    ctx = InvocationContext(run_id="r", step_id="s", kind="action", host="h")
    params = {"result_path": str(path), "root": str(root), "recorded_at": AT, "actor": "reader"}
    result = TranscriptRenderPort(store).invoke(params, "k", None, context=ctx)
    assert "path_refused" in str(result.error)
    inside = narrow / "t.json"
    inside.write_text(json.dumps(_doc()))
    params["result_path"] = str(inside)
    assert TranscriptRenderPort(store).invoke(params, "k", None, context=ctx).outcome == COMPLETED


# ---- the book cache: one book per transcript file (d4) --------------------------------------


def test_a_later_run_returns_the_first_book_even_if_the_summary_changed(root):
    path = _write(root, _doc())
    first = _invoke(root, path, summary=_reply(word="Meeting"))
    second = _invoke(root, path, summary=_reply(summary="Another line.", word="Budget"))
    assert first.output["content_base64"] == second.output["content_base64"]
    assert second.output["title"] == first.output["title"]
    assert second.output["word"] == "meeting"


def test_two_recordings_of_one_day_with_one_word_get_different_titles() -> None:
    morning = tr.render_transcript(_doc(), recorded_at="2026-10-24T06:05:00Z", summary=_reply())
    evening = tr.render_transcript(_doc(), recorded_at="2026-10-24T17:40:00Z", summary=_reply())
    assert morning["date"] == evening["date"] == "2026-10-24"
    assert morning["title"] == "2026-10-24 0905 topic"
    assert evening["title"] == "2026-10-24 2040 topic"


def test_the_book_and_the_excerpt_share_one_file_cap() -> None:
    from culture_rules.node.actions import transcript_file

    cap = transcript_file.TRANSCRIPT_FILE_CAP
    assert tr.TRANSCRIPT_CAP == transcript_file.EXCERPT_FILE_CAP == cap


def test_a_book_without_its_summary_is_not_kept(root):
    path = _write(root, _doc())
    first = _invoke(root, path)  # the summary step failed or was skipped
    assert first.output["summary_used"] is False
    assert {p.name for p in path.parent.iterdir()} == {path.name}
    second = _invoke(root, path, summary=_reply(word="Meeting"))
    assert second.output["summary_used"] is True
    assert second.output["word"] == "meeting"
    assert second.output["content_base64"] != first.output["content_base64"]


def test_a_stored_summaryless_book_is_ignored(root):
    path = _write(root, _doc())
    stale = _invoke(root, path).output  # as a release before this fix would have stored it
    tr._store_book(path, path.read_bytes(), {**stale, "summary_used": True})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    meta_path = path.parent / f"book.{digest}.json"
    meta = json.loads(meta_path.read_text())
    meta_path.write_text(json.dumps({**meta, "summary_used": False}))
    again = _invoke(root, path, summary=_reply(word="Meeting"))
    assert again.output["summary_used"] is True
    assert again.output["word"] == "meeting"


def test_a_failed_summary_on_a_later_run_still_returns_the_first_book(root):
    path = _write(root, _doc())
    first = _invoke(root, path, summary=_reply(word="Meeting"))
    second = _invoke(root, path)
    assert second.output["content_base64"] == first.output["content_base64"]
    assert second.output["summary_used"] is True


def test_the_cache_sits_beside_the_transcript_named_after_its_sha256(root):
    path = _write(root, _doc())
    _invoke(root, path, summary=_reply())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    assert {p.name for p in path.parent.iterdir()} == {
        path.name,
        f"book.{digest}.epub",
        f"book.{digest}.json",
    }


def test_a_changed_transcript_gets_a_new_book(root):
    path = _write(root, _doc(texts=("one",)))
    first = _invoke(root, path, summary=_reply(word="Meeting"))
    path.write_text(json.dumps(_doc(texts=("one", "two", "three"))), encoding="utf-8")
    second = _invoke(root, path, summary=_reply(word="Budget"))
    assert second.output["content_base64"] != first.output["content_base64"]
    assert second.output["word"] == "budget"


@pytest.mark.parametrize("damage", ["epub", "json"])
def test_a_damaged_cache_is_rendered_again(root, damage):
    path = _write(root, _doc())
    _invoke(root, path, summary=_reply(word="Meeting"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    (path.parent / f"book.{digest}.{damage}").write_bytes(b"garbage")
    again = _invoke(root, path, summary=_reply(word="Budget"))
    assert again.outcome == "completed"
    assert again.output["word"] == "budget"


def test_an_unwritable_folder_still_returns_the_book(root):
    path = _write(root, _doc())
    path.parent.chmod(0o500)
    try:
        result = _invoke(root, path, summary=_reply())
    finally:
        path.parent.chmod(0o700)
    assert result.outcome == "completed"
    assert result.output["title"].endswith(" topic")


# ------------------------------------------------------------------ Markdown (format md)


def _md(out: dict[str, Any]) -> str:
    return base64.b64decode(out["content_base64"]).decode("utf-8")


def _answer(**extra: Any) -> dict[str, str]:
    body = {"summary": "Line one.\nLine two.", "word": "Plans", "facts": ["Fact A", "Fact B"]}
    return {"text": json.dumps({**body, **extra})}


def test_md_has_title_summary_key_facts_and_transcript() -> None:
    out = tr.render_transcript(
        _doc("en", ("first", "", "  ", "second")), recorded_at=AT, summary=_answer(), format="md"
    )
    assert out["format"] == "md"
    assert out["segment_count"] == 2
    assert _md(out) == (
        "# 2026-10-24 0130 plans\n\nLine one.\n\nLine two.\n\n"
        "## Key facts\n\n- Fact A\n- Fact B\n\n## Transcript\n\nfirst\n\nsecond\n"
    )


def test_md_he_and_unlabelled_are_wrapped_rtl_and_en_is_not() -> None:
    for language in ("he", None, ""):
        text = _md(tr.render_transcript(_doc(language), recorded_at=AT, format="md"))
        assert text.startswith('<div dir="rtl">\n\n# ')
        assert text.endswith("\n\n</div>\n")
    assert "dir=" not in _md(tr.render_transcript(_doc("en"), recorded_at=AT, format="md"))


def test_md_missing_answer_says_unavailable_and_has_no_facts() -> None:
    for bad in (None, {"text": "not json"}, {"text": json.dumps({"summary": "x"})}):
        out = tr.render_transcript(_doc("en"), recorded_at=AT, summary=bad, format="md")
        text = _md(out)
        assert "Summary unavailable." in text
        assert "Key facts" not in text
        assert out["summary_used"] is False
        assert out["word"] == "recording"


def test_md_accepts_old_shape_a_direct_mapping_and_ignores_action_items() -> None:
    old = tr.render_transcript(_doc("en"), recorded_at=AT, summary=_reply(), format="md")
    assert "Key facts" not in _md(old)
    assert old["summary_used"] is True
    direct = {"summary": "Direct.", "word": "Plans", "facts": ["F"], "action_items": ["DO-IT"]}
    text = _md(tr.render_transcript(_doc("en"), recorded_at=AT, summary=direct, format="md"))
    assert "Direct." in text
    assert "- F" in text
    assert "DO-IT" not in text


def test_facts_are_optional_short_strings() -> None:
    facts = ["ok", "", 5, "x" * 400, "  spaced   out "]
    parsed = tr.parse_answer(_answer(facts=facts))
    assert parsed is not None
    assert parsed[2] == ["ok", "spaced out"]
    assert tr.parse_answer(_answer(facts="nope"))[2] == []  # type: ignore[index]
    assert parse_summary(_answer()) == (["Line one.", "Line two."], "plans")


def test_the_epub_path_is_unchanged_by_facts_and_format() -> None:
    a = tr.render_transcript(_doc(), recorded_at=AT, summary=_reply())
    b = tr.render_transcript(_doc(), recorded_at=AT, summary=_reply(), format="epub")
    c = tr.render_transcript(_doc(), recorded_at=AT, summary=_answer(facts=[]))
    assert a == b
    assert "format" not in a
    assert (
        a["content_base64"]
        != tr.render_transcript(_doc(), recorded_at=AT, format="md")["content_base64"]
    )
    assert c["summary_used"] is True


def test_bad_format_fails_invalid_request(root: Path) -> None:
    result = _invoke(root, _write(root, _doc()), format="pdf")
    assert result.outcome != COMPLETED
    assert "invalid_request" in str(result.error)


def test_port_md_returns_markdown_and_keeps_it_byte_stable(root: Path) -> None:
    path = _write(root, _doc("en"))
    first = _invoke(root, path, format="md", summary=_answer())
    assert first.outcome == COMPLETED
    assert first.output["format"] == "md"
    assert PRIVATE_TEXT not in json.dumps(
        {k: v for k, v in first.output.items() if k != "content_base64"}
    )
    assert len(list(path.parent.glob("book.*.md"))) == 1
    again = _invoke(root, path, format="md", summary=_answer(facts=["changed"]))
    assert again.output == first.output
    # the EPUB is its own book beside it, not the Markdown
    epub = _invoke(root, path, summary=_answer())
    assert "format" not in epub.output
    assert epub.output["content_base64"] != first.output["content_base64"]
    assert len(list(path.parent.glob("book.*.epub"))) == 1


def test_port_md_without_summary_is_not_kept(root: Path) -> None:
    path = _write(root, _doc("en"))
    assert _invoke(root, path, format="md").outcome == COMPLETED
    assert list(path.parent.glob("book.*")) == []


def test_md_escapes_ampersand_and_angle_brackets_in_derived_text() -> None:
    raw = "<b>&x</b>"
    answer = {"text": json.dumps({"summary": raw, "word": "Plans", "facts": [raw]})}
    out = tr.render_transcript(_doc("en", (raw,)), recorded_at=AT, summary=answer, format="md")
    text = _md(out)
    assert text.count("&lt;b&gt;&amp;x&lt;/b&gt;") == 3
    assert "<b>" not in text
    assert "&amp;amp;" not in text
    assert out["title"] == "2026-10-24 0130 plans"


def _llm_recording_output(*, cached: bool) -> dict[str, Any]:
    """llm.complete's real recording output: the parsed answer keeps summary as lines, and a
    cache hit's text is that answer as JSON."""
    reply = json.dumps(
        {
            "summary": "Line one.\nLine two.",
            "word": "Plans",
            "facts": ["Fact A"],
            "action_items": ["Book the truck"],
        }
    )
    answer = parse_recording_answer(reply)
    text = json.dumps(answer, sort_keys=True) if cached else reply
    return {"text": text, "model": "m", "usage": {}, "answer": answer, "cached": cached}


@pytest.mark.parametrize("cached", [False, True])
def test_md_uses_llm_complete_recording_output_as_given(cached: bool) -> None:
    summary = _llm_recording_output(cached=cached)
    out = tr.render_transcript(_doc("en"), recorded_at=AT, summary=summary, format="md")
    assert out["summary_used"] is True
    assert out["word"] == "plans"
    text = _md(out)
    assert "Line one." in text
    assert "- Fact A" in text


def test_a_cached_answer_text_alone_still_parses() -> None:
    text = _llm_recording_output(cached=True)["text"]
    assert tr.parse_answer({"text": text}) == (["Line one.", "Line two."], "plans", ["Fact A"])


def test_a_summary_list_must_hold_only_strings() -> None:
    bad = {"summary": ["ok", 5], "word": "plans"}
    assert tr.parse_answer(bad) is None
