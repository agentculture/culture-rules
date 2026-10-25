"""newspaper.digest: a rendered issue as Discord-sized Markdown chunks (headlines + summaries)."""

from __future__ import annotations

from datetime import UTC, datetime

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.newspaper import NewspaperRenderPort
from culture_rules.node.actions.newspaper_digest import (
    DIGEST_BUILTIN,
    NewspaperDigestPort,
    digest_issue,
)
from culture_rules.node.actions.newspaper_issue import ISSUES_COLLECTION
from culture_rules.store.memory import MemoryStore

DEADLINE = datetime(2026, 10, 11, 23, tzinfo=UTC)
DATE = "2026-10-11"
URL_A = "https://a.example/story"
URL_B = "https://b.example/news?id=1&x=2"


def ctx():
    return InvocationContext(run_id="r1", step_id="digest", kind="code", host="spark")


def hit(url, title="Story"):
    return {"title": title, "url": url, "snippet": "A snippet", "age": "1h", "source": "a.example"}


def sec(name, results=None, summary=None):
    return {"name": name, "results": [] if results is None else results, "summary": summary}


def html_of(sections):
    payload = {"date": DATE, "title": "Daily newspaper", "sections": sections}
    result = NewspaperRenderPort().invoke(payload, "k", DEADLINE, context=ctx())
    assert result.outcome == COMPLETED, result.error
    return result.output["html"]


def test_the_builtin_name():
    assert DIGEST_BUILTIN == "newspaper.digest"


def test_headline_then_each_section_with_its_summary_as_markdown():
    html = html_of(
        [
            sec(
                "AI",
                [hit(URL_A, "Big model")],
                f"- A new model shipped [a]({URL_A})\n- It is fast [a]({URL_A})",
            ),
            sec("Robots", [hit(URL_B, "Arm")], f"- Robots got arms [b]({URL_B})"),
        ]
    )
    out = digest_issue(html)
    text = "\n".join(out["chunks"])
    assert out["title"] == "Daily newspaper 2026-10-11"
    assert text.startswith("**Daily newspaper 2026-10-11**")
    assert "**AI**" in text
    assert f"- A new model shipped [a](<{URL_A}>)" in text
    assert f"- It is fast [a](<{URL_A}>)" in text
    assert "**Robots**" in text
    assert text.index("**AI**") < text.index("**Robots**")
    assert "<p>" not in text
    assert "<li>" not in text


def test_a_section_without_a_summary_lists_its_headlines_as_unembedded_links():
    out = digest_issue(html_of([sec("AI", [hit(URL_A, "Big model"), hit(URL_B, "Arm")])]))
    text = "\n".join(out["chunks"])
    assert f"[Big model](<{URL_A}>)" in text
    assert f"[Arm](<{URL_B}>)" in text


def test_an_empty_section_says_so():
    text = "\n".join(digest_issue(html_of([sec("Quiet")]))["chunks"])
    assert "**Quiet**" in text
    assert "No results" in text


def test_every_chunk_fits_and_sections_stay_whole_and_in_order():
    sections = [
        sec(f"S{n}", [hit(URL_A)], f"- Summary number {n} [a]({URL_A})\n" * 4) for n in range(12)
    ]
    out = digest_issue(html_of(sections), max_chars=400)
    assert len(out["chunks"]) > 1
    assert all(len(c) <= 400 for c in out["chunks"])
    joined = "\n".join(out["chunks"])
    positions = [joined.index(f"**S{n}**") for n in range(12)]
    assert positions == sorted(positions)
    for chunk in out["chunks"][1:]:
        assert chunk.startswith("**S")


def test_one_section_longer_than_a_chunk_is_split_by_lines():
    long_summary = "\n".join(f"- point {n} " + "x" * 60 + f" [a]({URL_A})" for n in range(30))
    out = digest_issue(html_of([sec("Long", [hit(URL_A)], long_summary)]), max_chars=500)
    assert all(len(c) <= 500 for c in out["chunks"])
    assert "point 29" in out["chunks"][-1]


def test_markup_in_the_issue_stays_text():
    out = digest_issue(
        html_of([sec("AI", [hit(URL_A)], f"- Tags like <b>this</b> are text [a]({URL_A})")])
    )
    assert "<b>this</b>" in "\n".join(out["chunks"])


def stored(day=DATE, html=None, subject="Daily newspaper 2026-10-11"):
    s = MemoryStore()
    s.put(
        ISSUES_COLLECTION,
        {"id": day, "email_payload": {"subject": subject, "html": html or html_of([sec("AI")])}},
    )
    return s


def test_the_port_reads_the_stored_issue_for_the_date():
    res = NewspaperDigestPort(stored()).invoke({"date": DATE}, "k", DEADLINE, context=ctx())
    assert res.outcome == COMPLETED, res.error
    assert res.output["title"] == "Daily newspaper 2026-10-11"
    assert res.output["count"] == len(res.output["chunks"])
    assert res.output["count"] >= 1


def test_the_port_takes_max_chars_from_config():
    sections = [sec(f"S{n}", [hit(URL_A)], f"- {'words ' * 30}[a]({URL_A})") for n in range(6)]
    res = NewspaperDigestPort(stored(html=html_of(sections))).invoke(
        {"date": DATE},
        "k",
        DEADLINE,
        context=InvocationContext(
            run_id="r1", step_id="digest", kind="code", host="spark", config={"max_chars": 300}
        ),
    )
    assert res.outcome == COMPLETED, res.error
    assert all(len(c) <= 300 for c in res.output["chunks"])


def test_no_issue_for_the_date_fails_without_retry():
    res = NewspaperDigestPort(MemoryStore()).invoke({"date": DATE}, "k", DEADLINE, context=ctx())
    assert res.outcome == FAILED
    assert res.retryable is False
    assert "no_issue" in res.error


def test_a_bad_date_fails_without_retry():
    res = NewspaperDigestPort(stored()).invoke({"date": "today"}, "k", DEADLINE, context=ctx())
    assert res.outcome == FAILED
    assert res.retryable is False
    assert "bad_params" in res.error


def test_bold_in_a_summary_stays_bold():
    html = html_of([sec("AI", [hit(URL_A)], f"- NVIDIA ships **Jetson Thor** [a]({URL_A})")])
    assert "- NVIDIA ships **Jetson Thor** [a](" in "\n".join(digest_issue(html)["chunks"])
