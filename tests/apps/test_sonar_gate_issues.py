"""d21 phase 2 (E5): the agent gets only the SonarCloud issues behind the FAILING quality-gate
conditions of its PR, not the project's whole backlog.

Live finding (#17): the fixer was asked to fix "the SonarCloud issues" - about 430 open
issues - while the gate failed on four; three full-budget timeouts followed. The built-in
``sonar.gate_issues`` (an app client outside the core model and engine, claim c10) reads the
PR's quality gate from SonarCloud's public Web API and, for each failing condition, the
issues of the matching type: ``new_reliability_rating`` -> bugs, ``new_security_rating`` ->
vulnerabilities, ``new_maintainability_rating`` -> code smells,
``new_security_hotspots_reviewed`` -> hotspots to review. A condition with no issue behind
it (coverage, duplication) is named, with no issues. Any lookup failure is ``available:
false`` with a note - never a failed run, and never a guess.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from culture_rules.apps.sonarcloud import SonarCloud, SonarError
from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.sonar import SonarGateIssuesPort

KEY = "agentculture_culture-rules"


def gate(*conditions, status="ERROR"):
    return {"projectStatus": {"status": status, "conditions": list(conditions)}}


def cond(metric, status="ERROR", actual="3", threshold="1"):
    return {
        "status": status,
        "metricKey": metric,
        "comparator": "GT",
        "errorThreshold": threshold,
        "actualValue": actual,
    }


def issue(key, kind, line=3, path="src/app.py"):
    return {
        "key": key,
        "rule": f"python:S{key}",
        "severity": "MAJOR",
        "type": kind,
        "message": f"{kind} {key}",
        "component": f"{KEY}:{path}",
        "line": line,
    }


class FakeSonar:
    """SonarCloud's Web API: answers by path; records every request."""

    def __init__(self, gate_doc, issues=(), hotspots=(), fail=None):
        self.gate = gate_doc
        self.issues = list(issues)
        self.hotspots = list(hotspots)
        self.fail = fail
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, method, url, headers, timeout):
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.requests.append((parts.path, query, dict(headers)))
        if self.fail is not None:
            if isinstance(self.fail, BaseException):
                raise self.fail
            return self.fail, b"{}"
        if parts.path == "/api/qualitygates/project_status":
            return 200, json.dumps(self.gate).encode()
        if parts.path == "/api/issues/search":
            wanted = set(query["types"].split(","))
            found = [i for i in self.issues if i["type"] in wanted]
            return 200, json.dumps({"issues": found, "paging": {"total": len(found)}}).encode()
        if parts.path == "/api/hotspots/search":
            return 200, json.dumps({"hotspots": self.hotspots}).encode()
        return 404, b"{}"


def run(fake, repo="agentculture/culture-rules", number=17, config=None):
    port = SonarGateIssuesPort(client=lambda: SonarCloud(transport=fake))
    ctx = InvocationContext("run-1", "sonar", "code", "spark", config=config or {})
    return port.invoke({"repo": repo, "number": number}, "k", None, context=ctx)


def test_only_the_issues_behind_failing_conditions_are_handed_on():
    fake = FakeSonar(
        gate(
            cond("new_reliability_rating"),
            cond("new_security_rating"),
            cond("new_maintainability_rating", status="OK"),
            cond("new_coverage", actual="61.0", threshold="80"),
        ),
        issues=[
            issue("1", "BUG"),
            issue("2", "VULNERABILITY", path="src/auth.py"),
            issue("3", "CODE_SMELL"),  # the backlog: its condition passes
        ],
    )
    res = run(fake)
    assert res.outcome == "completed", res.error
    out = res.output
    assert out["available"] is True
    assert out["gate"] == "ERROR"
    assert [f["metric"] for f in out["failing"]] == [
        "new_reliability_rating",
        "new_security_rating",
        "new_coverage",
    ]
    assert [(i["kind"], i["key"]) for i in out["issues"]] == [("BUG", "1"), ("VULNERABILITY", "2")]
    assert out["issues"][1]["path"] == "src/auth.py"
    assert out["issues"][1]["line"] == 3
    (searched,) = [q for path, q, _h in fake.requests if path == "/api/issues/search"]
    assert set(searched["types"].split(",")) == {"BUG", "VULNERABILITY"}
    assert searched["pullRequest"] == "17"
    assert searched["componentKeys"] == KEY
    assert searched["resolved"] == "false"
    note = out["note"]
    assert "new_coverage" in note
    assert "2 issue" in note
    assert "Fix exactly these" in note
    assert "nothing else" in note


def test_failing_hotspot_review_lists_the_hotspots_to_review():
    fake = FakeSonar(
        gate(cond("new_security_hotspots_reviewed", actual="0.0", threshold="100")),
        hotspots=[
            {
                "key": "h1",
                "component": f"{KEY}:src/net.py",
                "line": 9,
                "message": "Make sure this is safe",
                "vulnerabilityProbability": "HIGH",
                "ruleKey": "python:S5332",
            }
        ],
    )
    out = run(fake).output
    assert [(i["kind"], i["path"], i["line"]) for i in out["issues"]] == [
        ("SECURITY_HOTSPOT", "src/net.py", 9)
    ]


def test_a_passing_gate_hands_on_nothing():
    fake = FakeSonar(gate(cond("new_reliability_rating", status="OK"), status="OK"))
    out = run(fake).output
    assert out["available"] is True
    assert out["gate"] == "OK"
    assert out["failing"] == []
    assert out["issues"] == []
    assert [p for p, _q, _h in fake.requests] == ["/api/qualitygates/project_status"]
    assert "passes" in out["note"]


@pytest.mark.parametrize(
    "fail, why",
    [(404, "no SonarCloud analysis"), (500, "unavailable"), (OSError("down"), "unavailable")],
)
def test_a_failed_lookup_is_unavailable_never_a_failed_step(fail, why):
    out = run(FakeSonar(gate(), fail=fail)).output
    assert out["available"] is False
    assert out["issues"] == []
    assert out["failing"] == []
    assert why in out["note"]


def test_the_project_key_and_the_cap_come_from_the_config():
    many = [issue(str(n), "BUG", line=n) for n in range(80)]
    fake = FakeSonar(gate(cond("new_reliability_rating")), issues=many)
    out = run(fake, config={"project_key": "{owner}__{name}", "max_issues": 5}).output
    assert len(out["issues"]) == 5
    assert out["truncated"] is True
    assert fake.requests[0][1]["projectKey"] == "agentculture__culture-rules"
    assert "first 5" in out["note"]


def test_bad_input_fails_closed():
    for repo, number in (("nope", 1), ("o/r", 0), ("o/r", True), (None, 1)):
        res = run(FakeSonar(gate()), repo=repo, number=number)
        assert res.outcome == "failed"
        assert res.error == "bad_input"
        assert not res.retryable


def test_a_token_rides_only_in_the_header_when_configured():
    fake = FakeSonar(gate(status="OK"))
    client = SonarCloud(transport=fake, token="sq-token-value")
    client.quality_gate(KEY, 17)
    _path, query, headers = fake.requests[0]
    assert headers["Authorization"] == "Bearer sq-token-value"
    assert "sq-token-value" not in json.dumps(query)


def test_the_client_refuses_a_malformed_answer():
    class Bad:
        def __call__(self, method, url, headers, timeout):
            return 200, b"not json"

    client = SonarCloud(transport=Bad())
    with pytest.raises(SonarError):
        client.quality_gate(KEY, 1)


def test_the_core_library_holds_no_sonar_client():
    """claim c10: the client is an app module, never part of the model or the engine."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "culture_rules"
    for part in ("model", "engine"):
        for path in (root / part).rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            # a describe word may name it; no client, endpoint or import may live there
            for needle in ("sonarcloud.io", "apps.sonarcloud", "node.actions.sonar", "/api/"):
                assert needle not in text, (path, needle)


# --------------------------------------------------------------------------- paging (Codex #5)


class PagedSonar(FakeSonar):
    """Pages issues and hotspots as SonarCloud does (``ps``/``p``, ``paging.total``)."""

    def __call__(self, method, url, headers, timeout):
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.requests.append((parts.path, query, dict(headers)))
        if parts.path == "/api/qualitygates/project_status":
            return 200, json.dumps(self.gate).encode()
        size, page = int(query.get("ps", 100)), int(query.get("p", 1))
        if parts.path == "/api/issues/search":
            wanted = set(query["types"].split(","))
            rows = [i for i in self.issues if i["type"] in wanted]
            key = "issues"
        else:
            rows, key = self.hotspots, "hotspots"
        chunk = rows[(page - 1) * size : page * size]
        doc = {key: chunk, "paging": {"pageIndex": page, "pageSize": size, "total": len(rows)}}
        return 200, json.dumps(doc).encode()


def hotspot(n):
    return {"key": f"h{n}", "component": f"{KEY}:src/x.py", "line": n, "message": "check"}


def test_hotspots_are_paged_and_their_total_reported():
    fake = PagedSonar(
        gate(cond("new_security_hotspots_reviewed")), hotspots=[hotspot(n) for n in range(250)]
    )
    out = run(fake, config={"max_issues": 150}).output
    assert len(out["issues"]) == 150
    assert out["truncated"] is True
    assert out["omitted"] == 100
    assert out["total"] == 250
    pages = [q for p, q, _h in fake.requests if p == "/api/hotspots/search"]
    assert len(pages) == 2  # 100 + 50: a cap above one page is reached by paging
    assert "first 150 of 250" in out["note"]


def test_totals_and_truncation_count_every_type():
    fake = PagedSonar(
        gate(cond("new_reliability_rating"), cond("new_security_hotspots_reviewed")),
        issues=[issue(str(n), "BUG", line=n) for n in range(30)],
        hotspots=[hotspot(n) for n in range(30)],
    )
    out = run(fake, config={"max_issues": 40}).output
    assert len(out["issues"]) == 40
    assert out["total"] == 60
    assert out["omitted"] == 20
    assert out["truncated"] is True
    assert "first 40 of 60" in out["note"]


def test_a_full_list_is_not_truncated():
    fake = PagedSonar(gate(cond("new_reliability_rating")), issues=[issue("1", "BUG")])
    out = run(fake).output
    assert (out["total"], out["omitted"], out["truncated"]) == (1, 0, False)
    assert "first" not in out["note"]


def test_issues_beyond_one_page_are_paged_to_the_cap():
    fake = PagedSonar(
        gate(cond("new_reliability_rating")), issues=[issue(str(n), "BUG") for n in range(180)]
    )
    out = run(fake, config={"max_issues": 150}).output
    assert len(out["issues"]) == 150
    assert out["total"] == 180
    assert out["omitted"] == 30
