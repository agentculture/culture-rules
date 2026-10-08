"""Built-ins ``gitguardian.findings`` and ``gitguardian.hold``: leaked secrets are for humans (d25).

A failed GitGuardian check is never auto-fixed: revoking and rotating a leaked secret is a
human's work, and an agent editing the file would only hide the finding (rewriting history
does not un-leak a secret). Both built-ins read the GitGuardian check runs of a commit
through the GitHub App, read-only (:meth:`~culture_rules.apps.github.GitHubApp.list_check_runs`,
``Checks: read``), and parse the findings table GitGuardian writes into the check's text
(:mod:`culture_rules.apps.gitguardian`).

Inputs ``repo`` (``owner/name``, on the App actor's allowlist) and ``head_sha``. The App
actor is ``config.actor``, else the step's placement actor (as for ``github.threads``).
Config (optional): ``app_slug`` (default ``gitguardian``) and ``max_findings`` (default 50,
at most 200).

``gitguardian.findings`` (workflow ``report-secrets``)
    Completes with ``state`` (``failing`` / ``pending`` / ``clean`` / ``absent``),
    ``findings`` (``{incident, incident_url, status, type, commit, file, line}`` each, at
    most ``max_findings``), ``total`` (the table's rows, or the check title's count when
    larger), ``omitted``, ``truncated``, ``check_url`` and ``comment``: the markdown the
    rule posts, one table row per finding (type, file:line, short commit, incident link,
    status) and the remediation line. It never quotes a secret value: GitGuardian's table
    holds none, and only the parsed columns reach the comment. A lookup error fails the
    step (the rule's ``on_failure`` says the findings could not be read).

``gitguardian.hold`` (workflow ``pr-fix``, before the agent)
    Fails ``secrets_found`` (not retryable) while GitGuardian fails on the head, so no fix
    run starts an agent on a PR with a leaked secret; otherwise completes ``{held: false,
    state}``. Fail closed: a lookup error fails the step as well, like ``github.threads``.

Standard-library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.apps.gitguardian import (
    APP_SLUG,
    CLEAN,
    DEFAULT_MAX_FINDINGS,
    FAILING,
    MAX_FINDINGS_CAP,
    check_state,
    failing_runs,
    parse_findings,
    title_count,
)
from culture_rules.apps.github import GitHubError
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.github import GitHubCommentPort, repo_refusal

__all__ = [
    "FINDINGS_BUILTIN",
    "HOLD_BUILTIN",
    "REMEDIATION",
    "GitGuardianPort",
    "read_report",
    "render_comment",
]

FINDINGS_BUILTIN = "gitguardian.findings"
HOLD_BUILTIN = "gitguardian.hold"
REMEDIATION = (
    "Revoke and rotate each secret, then remove it from the code; rewriting history does "
    "not un-leak it."
)
HOLD_NOTE = "The PR fixer does not touch secrets and stays off this PR until GitGuardian passes."
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
_CHECK_URL = re.compile(r"^https://github\.com/[^\s()<>]+$")
_NONE = "—"
STATE, TOTAL, OMITTED = "state", "total", "omitted"


class GitGuardianPort(GitHubCommentPort):
    """Built-in ``gitguardian.findings`` (``hold=False``) or ``gitguardian.hold`` (module doc)."""

    supports_idempotency_key = True  # a read: re-asking is harmless

    def __init__(self, store: Any, *, hold: bool = False, **kwargs: Any) -> None:
        super().__init__(store, **kwargs)
        self._hold = hold

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config or {}
        settings = _settings(config)
        if settings is None:
            return InvocationResult.failed("bad_config", retryable=False)
        repo, sha = input.get("repo"), input.get("head_sha")
        if not isinstance(sha, str) or not _SHA.match(sha):
            return InvocationResult.failed("bad_input", retryable=False)
        actor_id = config.get("actor") or context.actor
        conn = self._connection(actor_id)
        if conn is None:
            self._apps.pop(str(actor_id), None)
            return InvocationResult.failed("actor_not_found", retryable=False)
        allowed, refusal = repo_refusal(conn, repo)
        if refusal:
            return InvocationResult.failed(refusal, retryable=False)
        app = self._app(str(actor_id), conn, allowed)
        if app is None:
            return InvocationResult.failed("secret_unavailable", retryable=False)
        try:
            with app.deadline(deadline):
                runs = app.list_check_runs(repo, sha)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        report = read_report(runs, settings[0], settings[1])
        return self._answer(report, sha)

    def _answer(self, report: dict[str, Any], sha: str) -> InvocationResult:
        if not self._hold:
            return InvocationResult.completed({**report, "comment": render_comment(report, sha)})
        if report[STATE] == FAILING:
            return InvocationResult.failed(_hold_message(report, sha), retryable=False)
        return InvocationResult.completed({"held": False, STATE: report[STATE]})


def _settings(config: Mapping[str, Any]) -> tuple[str, int] | None:
    """``(app_slug, max_findings)`` from the step config, or None when either is malformed."""
    slug = config.get("app_slug", APP_SLUG)
    cap = config.get("max_findings", DEFAULT_MAX_FINDINGS)
    if not isinstance(slug, str) or not slug:
        return None
    if not isinstance(cap, int) or isinstance(cap, bool) or not 0 < cap <= MAX_FINDINGS_CAP:
        return None
    return slug, cap


def read_report(runs: list[Any], slug: str, cap: int) -> dict[str, Any]:
    """The findings report of a commit's check runs (pure; the module doc's outputs)."""
    state = check_state(runs, slug)
    findings: list[dict[str, Any]] = []
    total = 0
    check_url = None
    for run in failing_runs(runs, slug):
        found, rows = parse_findings(run.get("text"), cap - len(findings))
        findings += found
        total += max(rows, title_count(run.get("title")) or 0)
        url = run.get("html_url")
        if check_url is None and isinstance(url, str) and _CHECK_URL.match(url):
            check_url = url
    omitted = max(total - len(findings), 0)
    return {
        STATE: state,
        "findings": findings,
        TOTAL: max(total, len(findings)),
        OMITTED: omitted,
        "truncated": omitted > 0,
        "check_url": check_url,
    }


def _short(sha: Any) -> str:
    return sha[:7] if isinstance(sha, str) else _NONE


def _count(total: int) -> str:
    """``1 hardcoded secret``, ``3 hardcoded secrets``, or ``hardcoded secrets`` (no count)."""
    if not total:
        return "hardcoded secrets"
    return f"{total} hardcoded secret" + ("" if total == 1 else "s")


def _where(found: Mapping[str, Any]) -> str:
    path = found.get("file")
    if not path:
        return _NONE
    line = found.get("line")
    return f"`{path}:{line}`" if line is not None else f"`{path}`"


def _incident(found: Mapping[str, Any]) -> str:
    ident, url = found.get("incident"), found.get("incident_url")
    if ident and url:
        return f"[{ident}]({url})"
    if url:
        return f"[incident]({url})"
    return str(ident) if ident else _NONE


def _row(found: Mapping[str, Any]) -> str:
    kind = f"`{found['type']}`" if found.get("type") else _NONE
    commit = f"`{_short(found['commit'])}`" if found.get("commit") else _NONE
    status = found.get("status") or _NONE
    return f"| {kind} | {_where(found)} | {commit} | {_incident(found)} | {status} |"


def _see_check(report: Mapping[str, Any]) -> str:
    url = report.get("check_url")
    return f"[the GitGuardian check]({url})" if url else "the GitGuardian check"


def render_comment(report: Mapping[str, Any], sha: str) -> str:
    """The PR comment for ``report`` on head ``sha``: one table row per finding (type,
    file:line, short commit, incident link, status), never a secret value."""
    short = _short(sha)
    if report.get(STATE) != FAILING:
        state = report.get(STATE) or CLEAN
        return f"GitGuardian does not fail on `{short}` (its check is {state}): nothing to report."
    total, findings = int(report.get(TOTAL) or 0), list(report.get("findings") or ())
    if not findings:
        return (
            f"**GitGuardian reports {_count(total)} on `{short}`**, but its findings table could "
            f"not be read: open {_see_check(report)}.\n\n{REMEDIATION} {HOLD_NOTE}"
        )
    lines = [
        f"**GitGuardian found {_count(total)} on `{short}`.**",
        "",
        "| Secret type | File | Commit | Incident | Status |",
        "|---|---|---|---|---|",
        *(_row(f) for f in findings),
    ]
    if report.get(OMITTED):
        lines += ["", f"… and {report[OMITTED]} more: see {_see_check(report)}."]
    lines += ["", f"{REMEDIATION} {HOLD_NOTE}"]
    return "\n".join(lines)


def _hold_message(report: Mapping[str, Any], sha: str) -> str:
    count = _count(int(report.get(TOTAL) or 0))
    return (
        f"secrets_found: GitGuardian reports {count} on {_short(sha)}. A human revokes and "
        "rotates them first; the fixer never edits secrets (see the GitGuardian comment on "
        "this PR)."
    )
