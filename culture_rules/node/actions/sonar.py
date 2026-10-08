"""Built-in ``sonar.gate_issues``: what the PR fixer's agent is asked to fix in SonarCloud.

d21 phase 2 (E5). Live, the fixer was told to fix "the SonarCloud issues" of a PR and went
for the project's whole backlog (about 430 issues on #17) while the quality gate failed on
four; three full-budget timeouts followed. This step hands the agent only the issues behind
the **failing** conditions of the PR's quality gate, as data, with a short note it reads.

Inputs ``repo`` (``owner/name``) and ``number`` (the PR). Config (all optional):
``project_key`` - a template over ``{owner}`` and ``{name}``, default ``{owner}_{name}``
(the key SonarCloud gives a GitHub-imported project); ``max_issues`` - default 50; the
step never takes a URL from the workflow (the client's default is SonarCloud's public API).

Failing condition -> what is listed (:data:`METRIC_KINDS`): ``new_reliability_rating`` /
``reliability_rating`` -> ``BUG`` issues; ``new_security_rating`` / ``security_rating`` ->
``VULNERABILITY`` issues; ``new_maintainability_rating`` / ``sqale_rating`` -> ``CODE_SMELL``
issues; ``new_security_hotspots_reviewed`` / ``security_hotspots_reviewed`` -> the hotspots
still to review. Any other failing condition (coverage, duplication) is named with no
issues: the note says what it needs.

Outputs: ``available`` (the lookup worked), ``gate`` (``OK`` / ``ERROR`` / ...), ``failing``
(``{metric, actual, threshold}`` each), ``issues`` (``{kind, key, rule, severity, message,
path, line}`` each, at most ``max_issues``, paged), ``total`` (what SonarCloud counts across
every listed type), ``omitted`` and ``truncated`` (the cap left some out: the note says the
list is the first N of the total), and ``note``. Advisory data, not
a guard: a failed lookup (no analysis for the PR, SonarCloud down) is ``available: false``
with a note, never a failed step, so a fix of failing tests is never blocked by Sonar.
``bad_input`` (fail closed) for a malformed repo or PR number. Standard-library only.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.apps.sonarcloud import SonarCloud, SonarError
from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = ["METRIC_KINDS", "SONAR_BUILTIN", "SonarGateIssuesPort"]

SONAR_BUILTIN = "sonar.gate_issues"
HOTSPOT = "SECURITY_HOTSPOT"
METRIC_KINDS: dict[str, str] = {
    "new_reliability_rating": "BUG",
    "reliability_rating": "BUG",
    "new_security_rating": "VULNERABILITY",
    "security_rating": "VULNERABILITY",
    "new_maintainability_rating": "CODE_SMELL",
    "sqale_rating": "CODE_SMELL",
    "new_security_hotspots_reviewed": HOTSPOT,
    "security_hotspots_reviewed": HOTSPOT,
}
DEFAULT_KEY = "{owner}_{name}"
DEFAULT_MAX = 50
_MAX_CAP = 200
_REPO_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})$")
_MESSAGE_MAX = 500


def _path(component: Any, project: str) -> str:
    text = str(component or "")
    return text[len(project) + 1 :] if text.startswith(project + ":") else text


def _line(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class SonarGateIssuesPort:
    """ActorPort for the built-in ``sonar.gate_issues`` code step (module doc)."""

    supports_idempotency_key = True  # a read: re-asking is harmless

    def __init__(self, *, client: Callable[[], SonarCloud] | None = None) -> None:
        self._client = client or SonarCloud

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        repo, number = input.get("repo"), input.get("number")
        match = _REPO_RE.match(repo) if isinstance(repo, str) else None
        if match is None or not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            return InvocationResult.failed("bad_input", retryable=False)
        config = context.config or {}
        template = config.get("project_key", DEFAULT_KEY)
        cap = config.get("max_issues", DEFAULT_MAX)
        if (
            not isinstance(template, str)
            or not isinstance(cap, int)
            or isinstance(cap, bool)
            or not 0 < cap <= _MAX_CAP
        ):
            return InvocationResult.failed("bad_config", retryable=False)
        try:
            project = template.format(owner=match.group(1), name=match.group(2))
        except (KeyError, IndexError, ValueError):
            return InvocationResult.failed("bad_config", retryable=False)
        return InvocationResult.completed(self._read(project, number, cap))

    def _read(self, project: str, number: int, cap: int) -> dict[str, Any]:
        client = self._client()
        try:
            gate = client.quality_gate(project, number)
        except SonarError as exc:
            why = (
                f"no SonarCloud analysis for {project} PR {number}"
                if exc.code == "not_found"
                else f"SonarCloud is unavailable ({exc.code})"
            )
            return _unavailable(why)
        failing = [
            {
                "metric": str(c.get("metricKey")),
                "actual": c.get("actualValue"),
                "threshold": c.get("errorThreshold"),
            }
            for c in gate["conditions"]
            if isinstance(c, Mapping) and c.get("status") == "ERROR"
        ]
        kinds = sorted({METRIC_KINDS[f["metric"]] for f in failing if f["metric"] in METRIC_KINDS})
        issues: list[dict[str, Any]] = []
        total = 0
        try:
            types = [k for k in kinds if k != HOTSPOT]
            if types:
                found, count = client.issues(project, number, types, limit=cap)
                issues += [_issue(i, project) for i in found]
                total += count
            if HOTSPOT in kinds:
                # read even with the cap reached: the total says what the list leaves out
                spots, count = client.hotspots(project, number, limit=cap - len(issues))
                issues += [_hotspot(h, project) for h in spots]
                total += count
        except SonarError as exc:
            return _unavailable(f"SonarCloud is unavailable ({exc.code})")
        total = max(total, len(issues))
        omitted = total - len(issues)
        return {
            "available": True,
            "gate": gate["status"],
            "failing": failing,
            "issues": issues,
            "total": total,
            "omitted": omitted,
            "truncated": omitted > 0,
            "note": _note(gate["status"], failing, issues, total),
        }


def _issue(raw: Mapping[str, Any], project: str) -> dict[str, Any]:
    return {
        "kind": str(raw.get("type") or ""),
        "key": str(raw.get("key") or ""),
        "rule": str(raw.get("rule") or ""),
        "severity": str(raw.get("severity") or ""),
        "message": str(raw.get("message") or "")[:_MESSAGE_MAX],
        "path": _path(raw.get("component"), project),
        "line": _line(raw.get("line")),
    }


def _hotspot(raw: Mapping[str, Any], project: str) -> dict[str, Any]:
    return {
        "kind": HOTSPOT,
        "key": str(raw.get("key") or ""),
        "rule": str(raw.get("ruleKey") or ""),
        "severity": str(raw.get("vulnerabilityProbability") or ""),
        "message": str(raw.get("message") or "")[:_MESSAGE_MAX],
        "path": _path(raw.get("component"), project),
        "line": _line(raw.get("line")),
    }


def _unavailable(why: str) -> dict[str, Any]:
    return {
        "available": False,
        "gate": None,
        "failing": [],
        "issues": [],
        "truncated": False,
        "note": f"SonarCloud data is unavailable: {why}. Judge Sonar from the checks only.",
    }


def _note(
    gate: Any, failing: list[dict[str, Any]], issues: list[dict[str, Any]], total: int
) -> str:
    if not failing:
        return f"The SonarCloud quality gate passes ({gate}): do not work on Sonar issues."
    names = ", ".join(f"{f['metric']} ({f['actual']} vs {f['threshold']})" for f in failing)
    text = f"The SonarCloud quality gate fails on: {names}. "
    if issues and total > len(issues):
        text += (
            f"The sonar_issues input lists the first {len(issues)} of {total} issues behind "
            f"those conditions ({total - len(issues)} left out). Fix exactly these and nothing "
            "else of the Sonar backlog; the rest are for a later run. "
        )
    elif issues:
        text += (
            f"The sonar_issues input lists all {len(issues)} issue(s) behind those conditions. "
            "Fix exactly these and nothing else of the Sonar backlog. "
        )
    other = [f["metric"] for f in failing if f["metric"] not in METRIC_KINDS]
    if other:
        text += (
            "These conditions have no issue list: "
            + ", ".join(other)
            + " (add tests for new code for coverage; remove duplicated new code)."
        )
    return text.strip()
