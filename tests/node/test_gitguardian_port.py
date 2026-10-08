"""d25: the ``gitguardian.findings`` and ``gitguardian.hold`` built-in code steps.

A failed GitGuardian check is never auto-fixed. ``gitguardian.findings`` reads the head's
GitGuardian check through the App and renders one PR comment listing each finding (type,
file:line, short commit, incident link, status) and the remediation, never a secret value;
``gitguardian.hold`` fails ``secrets_found`` while GitGuardian fails on the head, so a fix run
never reaches its agent.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime

import pytest

from culture_rules.apps.github import GitHubError
from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.gitguardian import (
    FINDINGS_BUILTIN,
    HOLD_BUILTIN,
    GitGuardianPort,
    render_comment,
)
from culture_rules.node.runner import BuiltinCodePort, default_ports
from culture_rules.store.memory import MemoryStore
from tests.apps.test_gitguardian import FINDING, INCIDENT_URL, ROW, SAMPLE, SHA, gg_run
from tests.node.test_github_threads import actor_doc

REPO = "acme/widgets"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
CHECK_URL = "https://github.com/agentculture/r/runs/1"
SECRET_VALUE = "hunter2-S3cr3t-Value"

EXPECTED_COMMENT = f"""**GitGuardian found 1 hardcoded secret on `0ef24e4`.**

| Secret type | File | Commit | Incident | Status |
|---|---|---|---|---|
| `Generic Password` | `esphome/x.yaml:8` | `0ef24e4` | [12345678]({INCIDENT_URL}) | Triggered |

Revoke and rotate each secret, then remove it from the code; rewriting history does not \
un-leak it. The PR fixer does not touch secrets and stays off this PR until GitGuardian \
passes."""


class ChecksApp:
    def __init__(self, runs=(), error=None):
        self.runs = list(runs)
        self.error = error
        self.calls = []

    def deadline(self, deadline):
        return nullcontext()

    def list_check_runs(self, repo, sha):
        self.calls.append((repo, sha))
        if self.error is not None:
            raise self.error
        return [dict(r) for r in self.runs]


def port(fake: ChecksApp, *, hold: bool = False, **actor) -> GitGuardianPort:
    store = MemoryStore()
    store.put("actors", actor_doc(**actor))
    p = GitGuardianPort(store, hold=hold)
    p._app = lambda actor_id, conn, allowed: fake  # the App seam
    return p


def ctx(builtin=FINDINGS_BUILTIN, **config):
    return InvocationContext(
        run_id="run-1",
        step_id="findings",
        kind="code",
        host="spark",
        actor="github-app",
        config={"builtin": builtin, **config},
    )


def invoke(p, *, sha=SHA, repo=REPO, builtin=FINDINGS_BUILTIN, **config):
    return p.invoke({"repo": repo, "head_sha": sha}, "k", DEADLINE, context=ctx(builtin, **config))


# --------------------------------------------------------------------------- findings


def test_the_findings_of_a_failing_check_and_their_comment():
    fake = ChecksApp([gg_run(), {**gg_run(conclusion="success"), "app_slug": "github-actions"}])
    res = invoke(port(fake))
    assert res.outcome == "completed"
    out = res.output
    assert out["state"] == "failing"
    assert out["findings"] == [FINDING]
    assert out["total"] == 1
    assert out["omitted"] == 0
    assert out["truncated"] is False
    assert out["check_url"] == CHECK_URL
    assert out["comment"] == EXPECTED_COMMENT
    assert fake.calls == [(REPO, SHA)]


def test_the_comment_never_carries_anything_outside_the_table_columns():
    # GitGuardian's table holds no value; a value elsewhere in the check text never leaks
    text = SAMPLE + f"\n\n```\npassword: {SECRET_VALUE}\n```\n"
    out = invoke(port(ChecksApp([gg_run(text=text)]))).output
    assert SECRET_VALUE not in out["comment"]
    assert SECRET_VALUE not in repr(out["findings"])
    assert "View secret" not in out["comment"]


def test_a_capped_list_says_how_many_more_and_where():
    rows = "\n".join(ROW.replace("12345678", str(n)) for n in range(5))
    text = SAMPLE.replace(ROW, rows)
    out = invoke(port(ChecksApp([gg_run(text=text, title="5 secrets uncovered!")])), max_findings=2)
    out = out.output
    assert [f["incident"] for f in out["findings"]] == ["0", "1"]
    assert out["total"] == 5
    assert out["omitted"] == 3
    assert out["truncated"] is True
    assert out["comment"].startswith("**GitGuardian found 5 hardcoded secrets on `0ef24e4`.**")
    assert f"… and 3 more: see [the GitGuardian check]({CHECK_URL})." in out["comment"]


def test_a_title_count_above_the_rows_counts_as_omitted():
    out = invoke(port(ChecksApp([gg_run(title="3 secrets uncovered!")]))).output
    assert out["total"] == 3
    assert out["omitted"] == 2


def test_a_failing_check_with_an_unreadable_table_still_says_so():
    out = invoke(port(ChecksApp([gg_run(text="something else")]))).output
    assert out["findings"] == []
    assert out["comment"].startswith(
        "**GitGuardian reports 1 hardcoded secret on `0ef24e4`**, but its findings table "
        f"could not be read: open [the GitGuardian check]({CHECK_URL})."
    )
    assert "Revoke and rotate" in out["comment"]


@pytest.mark.parametrize(
    "runs, state",
    [
        ([], "absent"),
        ([gg_run(conclusion="neutral", title="Could not complete scanning")], "clean"),
        ([gg_run(conclusion="success", text="")], "clean"),
        ([gg_run(conclusion=None, status="in_progress")], "pending"),
    ],
    ids=["absent", "too_large", "passed", "running"],
)
def test_no_failing_check_is_no_finding(runs, state):
    out = invoke(port(ChecksApp(runs))).output
    assert out["state"] == state
    assert out["findings"] == []
    assert out["total"] == 0
    assert out["comment"] == (
        f"GitGuardian does not fail on `0ef24e4` (its check is {state}): nothing to report."
    )


def test_a_check_url_that_is_not_githubs_is_dropped():
    run = {**gg_run(text="x"), "html_url": "https://evil.example/runs/1"}
    out = invoke(port(ChecksApp([run]))).output
    assert out["check_url"] is None
    assert "evil" not in out["comment"]


def test_a_lookup_error_fails_the_step_as_github_says():
    res = invoke(port(ChecksApp(error=GitHubError("http_502", retryable=True))))
    assert res.outcome == "failed"
    assert res.error == "http_502"
    assert res.retryable is True


@pytest.mark.parametrize(
    "kwargs, error",
    [
        ({"sha": "../x"}, "bad_input"),
        ({"sha": None}, "bad_input"),
        ({"repo": "other/repo"}, "repo_not_allowed"),
        ({"max_findings": 0}, "bad_config"),
        ({"max_findings": 201}, "bad_config"),
        ({"max_findings": True}, "bad_config"),
        ({"app_slug": ""}, "bad_config"),
    ],
)
def test_bad_input_or_config_fails_closed_before_any_call(kwargs, error):
    fake = ChecksApp([gg_run()])
    res = invoke(port(fake), **kwargs)
    assert res.outcome == "failed"
    assert res.error == error
    assert res.retryable is False
    assert fake.calls == []


def test_an_unknown_actor_fails():
    store = MemoryStore()
    p = GitGuardianPort(store)
    res = invoke(p)
    assert res.error == "actor_not_found"


def test_another_app_slug_can_be_named():
    run = {**gg_run(), "app_slug": "gitguardian-onprem"}
    out = invoke(port(ChecksApp([run])), app_slug="gitguardian-onprem").output
    assert out["state"] == "failing"
    assert len(out["findings"]) == 1


# --------------------------------------------------------------------------- hold


def test_the_hold_fails_while_gitguardian_fails_on_the_head():
    res = invoke(port(ChecksApp([gg_run()]), hold=True), builtin=HOLD_BUILTIN)
    assert res.outcome == "failed"
    assert res.retryable is False
    assert res.error.startswith("secrets_found: GitGuardian reports 1 hardcoded secret on 0ef24e4")
    assert "Generic Password" not in res.error  # the hand-back names no finding


@pytest.mark.parametrize(
    "runs",
    [
        [],
        [gg_run(conclusion="neutral", title="Could not complete scanning")],
        [gg_run(conclusion="success", text="")],
        [gg_run(conclusion=None, status="queued")],
    ],
    ids=["absent", "too_large", "passed", "queued"],
)
def test_the_hold_lets_the_fix_run_on(runs):
    res = invoke(port(ChecksApp(runs), hold=True), builtin=HOLD_BUILTIN)
    assert res.outcome == "completed"
    assert res.output["held"] is False


def test_the_hold_fails_closed_on_a_lookup_error():
    fake = ChecksApp(error=GitHubError("network_error", retryable=True))
    res = invoke(port(fake, hold=True), builtin=HOLD_BUILTIN)
    assert res.outcome == "failed"
    assert res.error == "network_error"


# --------------------------------------------------------------------------- rendering, wiring


def test_render_comment_marks_missing_cells():
    finding = dict.fromkeys(FINDING)
    report = {"state": "failing", "findings": [finding], "total": 1, "omitted": 0}
    assert "| — | — | — | — | — |" in render_comment(report, SHA)


def test_render_comment_links_an_incident_without_an_id():
    finding = {**FINDING, "incident": None}
    report = {"state": "failing", "findings": [finding], "total": 1, "omitted": 0}
    assert f"[incident]({INCIDENT_URL})" in render_comment(report, SHA)


def test_the_node_wires_both_builtins():
    pytest.importorskip("cryptography")
    code = default_ports(MemoryStore(), "spark")["code"]
    assert isinstance(code, BuiltinCodePort)
    assert isinstance(code._builtins[FINDINGS_BUILTIN], GitGuardianPort)
    assert isinstance(code._builtins[HOLD_BUILTIN], GitGuardianPort)
    assert code._builtins[HOLD_BUILTIN]._hold is True
    assert code._builtins[FINDINGS_BUILTIN]._hold is False


# --------------------------------------------------------------------------- Codex review (d25)


def test_the_view_secret_link_is_never_rendered():
    out = invoke(port(ChecksApp([gg_run()]))).output
    assert "/commit/" not in out["comment"]
    assert "#diff-" not in out["comment"]
    assert "/commit/" not in repr(out["findings"])


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/agentculture/r/runs/1&sol;@evil.example",
        "https://github.com/agentculture/r/runs/1?x=https://evil.example",
        "https://github.com/agentculture/r/pull/1",
        "https://github.com.evil.example/agentculture/r/runs/1",
    ],
)
def test_a_check_url_that_does_not_parse_exactly_is_dropped(url):
    run = {**gg_run(text="x"), "html_url": url}
    out = invoke(port(ChecksApp([run]))).output
    assert out["check_url"] is None
    assert "evil" not in out["comment"]
    assert "](" not in out["comment"]
