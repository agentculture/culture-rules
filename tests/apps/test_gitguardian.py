"""d25: GitGuardian's PR check read as data (culture_rules/apps/gitguardian.py).

GitGuardian writes its findings as a markdown table into its check run's ``output.text``.
The parser keeps only the table's columns - incident id and link, status, the secret's
*type* (the ``Secret`` column is the detector, never the value), commit, file, and the line
from the "View secret" anchor - and is tolerant of missing, reordered and padded columns.
"""

from __future__ import annotations

import pytest

from culture_rules.apps.gitguardian import (
    APP_SLUG,
    check_state,
    failing_runs,
    parse_findings,
    title_count,
)

SHA = "0ef24e484fb36e045257ac7789c0f6ed107a1fd8"
INCIDENT_URL = "https://dashboard.gitguardian.com/workspace/111/incidents/12345678?occurrence=222"
VIEW = f"https://github.com/agentculture/r/commit/{SHA}#diff-1a66b2c3d4e5f60718R8"
ROW = (
    f"| [12345678]({INCIDENT_URL}) | Triggered | Generic Password | {SHA} | esphome/x.yaml "
    f"| [View secret]({VIEW}) |"
)
SAMPLE = f"""### 🔎 Detected hardcoded secrets in your pull request

-   Pull request #25: `feat/x` 👉 `main`

| GitGuardian id | GitGuardian status | Secret | Commit | Filename | |
| -------------- | ------------------ | ------ | ------ | -------- | ---- |
{ROW}

### 🛠 Guidelines to remediate hardcoded secrets

1. Understand the implications of revoking this secret by investigating where it is used.
"""
FINDING = {
    "incident": "12345678",
    "incident_url": INCIDENT_URL,
    "status": "Triggered",
    "type": "Generic Password",
    "commit": SHA,
    "file": "esphome/x.yaml",
    "line": 8,
}
HEADER = (
    "| GitGuardian id | GitGuardian status | Secret | Commit | Filename | |\n"
    "|---|---|---|---|---|---|"
)


def gg_run(conclusion="failure", status="completed", text=SAMPLE, title="1 secret uncovered!"):
    return {
        "name": "GitGuardian Security Checks",
        "app_slug": APP_SLUG,
        "status": status,
        "conclusion": conclusion,
        "title": title,
        "text": text,
        "html_url": "https://github.com/agentculture/r/runs/1",
    }


# --------------------------------------------------------------------------- the table


def test_the_sample_table_reads_as_one_finding_with_every_column():
    assert parse_findings(SAMPLE) == ([FINDING], 1)


def test_no_text_or_no_table_is_no_finding():
    assert parse_findings(None) == ([], 0)
    assert parse_findings("") == ([], 0)
    assert parse_findings("Could not complete scanning of your commits") == ([], 0)
    other = "| Name | Value |\n|---|---|\n| a | b |"
    assert parse_findings(other) == ([], 0)


def test_columns_are_found_by_header_in_any_order_with_padding():
    text = (
        "|   Filename   |  Secret |   Commit  | GitGuardian id |\n"
        "| :--- | :---: | ---: | --- |\n"
        f"|   src/a.py   |   AWS Keys   | {SHA[:12]} |  [9]({INCIDENT_URL})  |\n"
    )
    (found,), total = parse_findings(text)
    assert total == 1
    assert found == {
        "incident": "9",
        "incident_url": INCIDENT_URL,
        "status": None,
        "type": "AWS Keys",
        "commit": SHA[:12],
        "file": "src/a.py",
        "line": None,  # no "View secret" column
    }


def test_a_short_row_reads_its_missing_cells_as_none():
    (found,), _ = parse_findings(f"{HEADER}\n| 1 | Triggered | Slack Token |")
    assert found["type"] == "Slack Token"
    assert found["file"] is None
    assert found["commit"] is None
    assert found["line"] is None


def test_a_view_link_without_a_line_anchor_gives_no_line():
    row = ROW.replace("R8)", ")")
    (found,), _ = parse_findings(f"{HEADER}\n{row}")
    assert found["line"] is None


@pytest.mark.parametrize(
    "url",
    [
        "http://dashboard.gitguardian.com/x",
        "https://evil.example/incidents/1",
        "https://gitguardian.com.evil.example/x",
        "javascript:alert(1)",
    ],
)
def test_only_an_https_gitguardian_incident_link_is_kept(url):
    row = ROW.replace(INCIDENT_URL, url)
    (found,), _ = parse_findings(f"{HEADER}\n{row}")
    assert found["incident"] == "12345678"
    assert found["incident_url"] is None


def test_a_commit_that_is_not_hex_is_dropped():
    row = ROW.replace(SHA, "not-a-sha")
    (found,), _ = parse_findings(f"{HEADER}\n{row}")
    assert found["commit"] is None


def test_cells_lose_markdown_that_could_break_out_of_the_comment():
    row = (
        "| 1 | Trig`ge<b>red | Generic `Password` <img src=x> | "
        f"{SHA} | [a`b|c](https://x.example/y) | |"
    )
    (found,), _ = parse_findings(f"{HEADER}\n{row}")
    for field in ("type", "file", "status"):
        assert not set("`<>|[]") & set(found[field] or ""), field
    assert found["type"] == "Generic Password img src=x"
    assert found["status"] == "Triggebred"


def test_a_long_cell_is_clipped():
    row = ROW.replace("esphome/x.yaml", "d/" * 300)
    (found,), _ = parse_findings(f"{HEADER}\n{row}")
    assert len(found["file"]) == 200


def test_an_empty_row_is_dropped_and_the_cap_keeps_the_total():
    rows = "\n".join(ROW.replace("12345678", str(n)) for n in range(5))
    text = f"{HEADER}\n|  |  |  |  |  |  |\n{rows}"
    found, total = parse_findings(text, cap=2)
    assert [f["incident"] for f in found] == ["0", "1"]
    assert total == 5


def test_several_tables_are_read_in_turn():
    text = f"{HEADER}\n{ROW}\n\nSome prose.\n\n{HEADER}\n{ROW.replace('12345678', '7')}"
    found, total = parse_findings(text)
    assert [f["incident"] for f in found] == ["12345678", "7"]
    assert total == 2


def test_the_title_count():
    assert title_count("1 secret uncovered!") == 1
    assert title_count("12 secrets uncovered!") == 12
    assert title_count("Could not complete scanning of your commits") is None
    assert title_count(None) is None


# --------------------------------------------------------------------------- the check state


def test_the_check_state():
    assert check_state([]) == "absent"
    assert check_state([{**gg_run(), "app_slug": "github-actions"}]) == "absent"
    assert check_state([gg_run()]) == "failing"
    assert check_state([gg_run(conclusion=None, status="in_progress")]) == "pending"
    assert check_state([gg_run(conclusion="success", text="")]) == "clean"


def test_a_scan_too_large_to_complete_is_neutral_and_never_a_finding():
    neutral = gg_run(
        conclusion="neutral", title="Could not complete scanning of your commits", text="..."
    )
    assert check_state([neutral]) == "clean"
    assert failing_runs([neutral]) == []


def test_another_apps_failing_run_is_not_gitguardians():
    other = {**gg_run(), "app_slug": "github-actions"}
    assert failing_runs([other, gg_run(conclusion="success")]) == []
    assert check_state([other, gg_run(conclusion="success")]) == "clean"
