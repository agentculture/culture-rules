"""t13: the PR fixer's test gate and diff guard (``culture_rules.actors.gate``).

Acceptance criteria:

1. a commit that deletes a failing test gets verdict ``guard`` naming the rule; nothing is
   pushed - ``test_deleting_a_failing_test_is_guarded_by_name``,
   ``test_deleting_a_test_file_is_guarded``, ``test_guard_verdict_pushes_nothing_end_to_end``;
2. no ``gate`` section gives ``no_gate`` and zero pushes - ``test_no_gate_section_*``,
   ``test_no_gate_verdict_pushes_nothing_end_to_end``;
3. exactly the declared argv, ``shell=False``, no shell spawned -
   ``test_gate_runs_exactly_the_declared_argv_and_never_a_shell``;
4. a failing run returns ``fail`` with the output tail, which becomes the agent's next
   instruction - ``test_failing_test_run_returns_fail_with_the_output_tail``,
   ``test_fail_instruction_becomes_the_agents_next_instruction``;
5. a PR editing its own gate section is judged by the base branch's -
   ``test_pr_editing_its_own_gate_is_judged_by_the_base_gate``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from culture_rules.actors import gate as gate_mod
from culture_rules.actors.gate import (
    FAIL,
    GUARD,
    NO_GATE,
    PASS,
    GateConfigError,
    GatePort,
    RunAs,
    diff_guard,
    gate_from_mapping,
    parse_gate,
    path_matches,
    run_as_from_env,
    run_process,
)
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.runs import ACTION_STEP, Executor, step_state
from culture_rules.model.action import Action
from culture_rules.model.workflow import Output
from culture_rules.node.actions.github_pr import GitHubPushPort
from culture_rules.node.runner import BuiltinCodePort, default_ports
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import T0, Clock, FakeActor, edge, port, rule, step, workflow

PY = sys.executable
GIT_ENV = {
    "PATH": os.environ.get("PATH", os.defpath),
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "agent",
    "GIT_AUTHOR_EMAIL": "agent@example.invalid",
    "GIT_COMMITTER_NAME": "agent",
    "GIT_COMMITTER_EMAIL": "agent@example.invalid",
    "LC_ALL": "C",
}
PASSING = [PY, "-c", "print('all good')"]
TESTS_PY = "def test_ok():\n    assert True\n\n\ndef test_broken():\n    assert 1 == 2\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True
    ).stdout.strip()


def gate_yaml(test: list[list[str]], setup: list[list[str]] | None = None) -> str:
    import json

    body = {"test": test} if setup is None else {"setup": setup, "test": test}
    return "agents:\n- suffix: x\n  backend: claude\ngate: " + json.dumps(body) + "\n"


class Repo:
    """A worktree with a base commit (culture.yaml + tests), a PR head and agent commits."""

    def __init__(self, root: Path, culture_yaml: str | None) -> None:
        self.wt = root / "wt"
        self.wt.mkdir()
        git(self.wt, "init", "-q", "-b", "main")
        files = {"tests/test_x.py": TESTS_PY, "src/app.py": "x = 1\n"}
        if culture_yaml is not None:
            files["culture.yaml"] = culture_yaml
        self.base = self.commit("base", files)
        self.start = self.commit("pr head", {"src/app.py": "x = 2\n"})

    def commit(self, message: str, files: dict[str, str | None]) -> str:
        for name, text in files.items():
            path = self.wt / name
            if text is None:
                git(self.wt, "rm", "-q", name)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            git(self.wt, "add", name)
        git(self.wt, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.wt, "rev-parse", "HEAD")

    def inputs(self, commit: str | None = None) -> dict[str, str]:
        head = commit or git(self.wt, "rev-parse", "HEAD")
        return {
            "worktree": str(self.wt),
            "base_sha": self.base,
            "start_sha": self.start,
            "commit_sha": head,
        }


class LocalRunner:
    """The run-as seam for tests: runs as the current user, records every argv."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.cwds: list[str] = []
        self.env = env

    def __call__(self, argv, *, cwd, timeout, stdout, stdin=None, merge_stderr=False, stderr=None):
        self.calls.append(list(argv))
        self.cwds.append(cwd)
        env = self.env if self.env is not None else {**GIT_ENV, "HOME": cwd}
        return run_process(
            argv,
            cwd=cwd,
            timeout=timeout,
            stdout=stdout,
            stdin=stdin,
            merge_stderr=merge_stderr,
            env=env,
            stderr=stderr,
        )

    def gate_calls(self) -> list[list[str]]:
        """The gate's own commands (not git, mktemp or the checkout's removal)."""
        return [c for c in self.calls if c[0] not in ("git", "env", "mktemp", "rm")]

    def gate_cwds(self) -> list[str]:
        return [d for c, d in zip(self.calls, self.cwds) if c in self.gate_calls()]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> MemoryStore:
    s = MemoryStore(clock=clock)
    s.put_variable(
        "fixer_protected_paths",
        [".github/workflows/**", "sonar-project.properties", "pyproject.toml"],
        updated_by="test",
    )
    return s


def ctx(**config) -> InvocationContext:
    return InvocationContext("run-1", "gate", "code", "spark", config={"builtin": "gate", **config})


def make_port(store, runner, tmp_path, clock) -> GatePort:
    return GatePort(store, run_as=runner, bundle_dir=tmp_path / "bundles", clock=clock)


def judge(store, runner, repo, tmp_path, clock, commit=None, **config) -> dict:
    port_ = make_port(store, runner, tmp_path, clock)
    result = port_.invoke(repo.inputs(commit), "k", T0 + timedelta(hours=1), context=ctx(**config))
    assert result.outcome == "completed", result.error
    return dict(result.output)


# ------------------------------------------------------------------------ the verdicts


def test_passing_gate_runs_setup_then_test_and_bundles_the_commit(store, tmp_path, clock):
    setup = [PY, "-c", "print('setup ran')"]
    repo = Repo(tmp_path, gate_yaml([PASSING], setup=[setup]))
    head = repo.commit("agent fix", {"src/app.py": "x = 3\n"})
    runner = LocalRunner()
    out = judge(store, runner, repo, tmp_path, clock)
    assert out["verdict"] == PASS
    assert out["instruction"] is None and out["rule"] is None
    assert runner.gate_calls() == [setup, PASSING]
    assert "all good" in out["output_tail"]
    assert out["gate"] == {"setup": [setup], "test": [PASSING]}
    bundle = Path(out["bundle"])
    assert bundle.is_file() and bundle.parent == tmp_path / "bundles"
    check = tmp_path / "check.git"
    git(tmp_path, "init", "-q", "--bare", str(check))
    git(check, "fetch", "-q", str(bundle), f"{head}:refs/x")
    assert git(check, "rev-parse", "refs/x") == head


def test_deleting_a_failing_test_is_guarded_by_name(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("drop the failing test", {"tests/test_x.py": "def test_ok():\n    assert True\n"})
    runner = LocalRunner()
    out = judge(store, runner, repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    assert out["rule"] == "test_removed"
    assert "test_broken" in out["violations"][0]["detail"]
    assert "test_removed" in out["instruction"]
    assert runner.gate_calls() == []  # no test command ran
    assert out["bundle"] is None  # nothing for github.push to push


def test_deleting_a_test_file_is_guarded(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("drop the tests", {"tests/test_x.py": None})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == GUARD
    rules = [v["rule"] for v in out["violations"]]
    assert out["rule"] == "test_deleted" and "test_removed" in rules


@pytest.mark.parametrize(
    ("files", "rule"),
    [
        ({".github/workflows/ci.yml": "on: push\n"}, "protected_path"),
        ({"sonar-project.properties": "sonar.exclusions=**\n"}, "protected_path"),
        ({"pyproject.toml": "[tool.coverage]\n"}, "protected_path"),
        ({"src/app.py": "import os  # noqa: F401\n"}, "suppression_marker"),
        ({"src/app.py": "x = 1  # NOSONAR\n"}, "suppression_marker"),
        ({"src/app.py": "x: int = 'a'  # type: ignore\n"}, "suppression_marker"),
        (
            {
                "tests/test_x.py": TESTS_PY.replace(
                    "def test_broken", "@pytest.mark.skip\ndef test_broken"
                )
            },
            "test_skipped",
        ),
        (
            {"tests/test_x.py": TESTS_PY + "\ndef test_more():\n    pytest.skip('later')\n"},
            "test_skipped",
        ),
    ],
)
def test_weakening_commits_are_guarded(store, tmp_path, clock, files, rule):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("weaken", files)
    runner = LocalRunner()
    out = judge(store, runner, repo, tmp_path, clock)
    assert (out["verdict"], out["rule"]) == (GUARD, rule), out["violations"]
    assert runner.gate_calls() == []


def test_workflow_edits_are_caught_even_when_the_variable_omits_them(tmp_path, clock):
    store = MemoryStore(clock=clock)
    store.put_variable("fixer_protected_paths", [], updated_by="test")
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("ci", {".github/workflows/tests.yml": "jobs: {}\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert (out["verdict"], out["rule"]) == (GUARD, "protected_path")


def test_unset_protected_paths_fail_closed(tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(MemoryStore(clock=clock), LocalRunner(), repo, tmp_path, clock)
    assert (out["verdict"], out["rule"]) == (GUARD, "protected_paths_unset")


def test_moving_an_existing_marker_or_a_test_is_not_flagged(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("marker", {"src/app.py": "import os  # noqa: F401\n"})
    repo.start = repo.commit("pr head 2", {})
    repo.commit(
        "reformat and move",
        {
            "src/app.py": "\nimport os  # noqa: F401\n",
            "tests/test_x.py": "def test_ok():\n    assert True\n",
            "tests/test_y.py": "def test_broken():\n    assert 1 == 1\n",
        },
    )
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS, out["violations"]


def test_rewritten_history_is_guarded(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    git(repo.wt, "reset", "-q", "--hard", repo.base)
    repo.commit("elsewhere", {"src/app.py": "x = 9\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert (out["verdict"], out["rule"]) == (GUARD, "history_rewritten")


def test_gate_runs_in_a_fresh_checkout_that_is_removed_afterwards(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    runner = LocalRunner()
    assert judge(store, runner, repo, tmp_path, clock)["verdict"] == PASS
    (cwd,) = runner.gate_cwds()
    assert cwd != str(repo.wt) and os.path.basename(cwd).startswith("culture-rules-gate-")
    assert not os.path.exists(cwd)


def test_checkout_is_removed_after_a_failure_too(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([[PY, "-c", "raise SystemExit(2)"]]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    runner = LocalRunner()
    assert judge(store, runner, repo, tmp_path, clock)["verdict"] == FAIL
    assert not os.path.exists(runner.gate_cwds()[0])


def test_skip_worktree_edits_in_the_agent_worktree_do_not_change_the_result(store, tmp_path, clock):
    check = [PY, "-c", "import sys; sys.exit('assert 1 == 2' in open('tests/test_x.py').read())"]
    repo = Repo(tmp_path, gate_yaml([check]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    git(repo.wt, "update-index", "--skip-worktree", "tests/test_x.py")
    (repo.wt / "tests/test_x.py").write_text("def test_broken():\n    assert True\n")
    assert git(repo.wt, "status", "--porcelain") == ""  # the worktree looks clean
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == FAIL  # the committed test file was judged


def test_untracked_files_in_the_agent_worktree_do_not_reach_the_gate(store, tmp_path, clock):
    check = [PY, "-c", "import os, sys; sys.exit(os.path.exists('conftest.py'))"]
    repo = Repo(tmp_path, gate_yaml([check]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    (repo.wt / "conftest.py").write_text("collect_ignore = ['tests']\n")
    assert judge(store, LocalRunner(), repo, tmp_path, clock)["verdict"] == PASS


def test_the_commit_is_judged_wherever_the_worktree_head_is(store, tmp_path, clock):
    check = [PY, "-c", "import sys; sys.exit(open('src/app.py').read() != 'x = 3\\n')"]
    repo = Repo(tmp_path, gate_yaml([check]))
    first = repo.commit("one", {"src/app.py": "x = 3\n"})
    repo.commit("two", {"src/app.py": "x = 4\n"})
    (repo.wt / "src/app.py").write_text("x = 'uncommitted'\n")
    assert judge(store, LocalRunner(), repo, tmp_path, clock, commit=first)["verdict"] == PASS


def test_checkout_ignores_a_hostile_fixer_gitconfig(store, tmp_path, clock):
    marker = tmp_path / "hook-ran"
    home = tmp_path / "fixer-home"
    hooks = home / "hooks"
    template = home / "template"
    for d in (hooks, template / "hooks"):
        d.mkdir(parents=True)
        script = d / "post-checkout"
        script.write_text(f"#!/bin/sh\ntouch {marker}\n")
        script.chmod(0o755)
    (home / ".gitconfig").write_text(
        f"[core]\n\thooksPath = {hooks}\n[init]\n\ttemplateDir = {template}\n"
        "[user]\n\tname = x\n\temail = x@example.invalid\n"
    )
    hostile = {"PATH": GIT_ENV["PATH"], "HOME": str(home), "LC_ALL": "C"}
    # control: plain git as this user does run the hook
    control = tmp_path / "control"
    subprocess.run(["git", "init", "-q", str(control)], env=hostile, check=True)
    subprocess.run(
        ["git", "-C", str(control), "commit", "-q", "--allow-empty", "-m", "c"],
        env=hostile,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(control), "checkout", "-q", "--detach", "HEAD"], env=hostile, check=True
    )
    assert marker.exists()
    marker.unlink()
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(env=hostile), repo, tmp_path, clock)
    assert out["verdict"] == PASS, out
    assert not marker.exists()


def test_no_gate_section_gives_no_gate_and_runs_nothing(store, tmp_path, clock):
    repo = Repo(tmp_path, "agents:\n- suffix: x\n  backend: claude\n")
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    runner = LocalRunner()
    out = judge(store, runner, repo, tmp_path, clock)
    assert out["verdict"] == NO_GATE
    assert out["bundle"] is None and out["instruction"] is None
    assert runner.gate_calls() == []


def test_no_gate_section_when_the_base_has_no_culture_yaml(store, tmp_path, clock):
    repo = Repo(tmp_path, None)
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    assert judge(store, LocalRunner(), repo, tmp_path, clock)["verdict"] == NO_GATE


def test_failing_test_run_returns_fail_with_the_output_tail(store, tmp_path, clock):
    noisy = [PY, "-c", "import sys\nfor i in range(5000): print('line', i)\nsys.exit(3)"]
    repo = Repo(tmp_path, gate_yaml([PASSING, noisy]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock, tail_bytes=200)
    assert out["verdict"] == FAIL
    assert (out["phase"], out["command"], out["exit_code"]) == ("test", noisy, 3)
    assert "line 4999" in out["output_tail"] and "line 0\n" not in out["output_tail"]
    assert len(out["output_tail"].encode()) <= 200
    assert out["output_tail"] in out["instruction"]
    assert "exited 3" in out["instruction"]
    assert out["bundle"] is None


def test_failing_setup_is_a_fail_verdict_and_skips_the_tests(store, tmp_path, clock):
    broken = [PY, "-c", "raise SystemExit('cannot sync')"]
    repo = Repo(tmp_path, gate_yaml([PASSING], setup=[broken]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    runner = LocalRunner()
    out = judge(store, runner, repo, tmp_path, clock)
    assert (out["verdict"], out["phase"]) == (FAIL, "setup")
    assert "cannot sync" in out["output_tail"]
    assert runner.gate_calls() == [broken]


def test_pr_editing_its_own_gate_is_judged_by_the_base_gate(store, tmp_path, clock):
    failing = [PY, "-c", "raise SystemExit(1)"]
    repo = Repo(tmp_path, gate_yaml([failing]))
    repo.commit("make my own gate pass", {"culture.yaml": gate_yaml([PASSING])})
    runner = LocalRunner()
    out = judge(store, runner, repo, tmp_path, clock)
    assert out["verdict"] == FAIL
    assert out["gate"] == {"setup": [], "test": [failing]}
    assert runner.gate_calls() == [failing]


def test_removing_the_gate_in_the_pr_does_not_skip_it(store, tmp_path, clock):
    failing = [PY, "-c", "raise SystemExit(1)"]
    repo = Repo(tmp_path, gate_yaml([failing]))
    repo.commit("no gate please", {"culture.yaml": "agents: []\n"})
    assert judge(store, LocalRunner(), repo, tmp_path, clock)["verdict"] == FAIL


# ------------------------------------------------------------------------ AC3: no shell


def test_gate_runs_exactly_the_declared_argv_and_never_a_shell(store, tmp_path, clock, monkeypatch):
    marker = tmp_path / "pwned"
    declared = [PY, "-c", "import sys; print(sys.argv[1:])", f"$(touch {marker})", ";", "|"]
    repo = Repo(tmp_path, gate_yaml([declared]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    inputs = repo.inputs()
    spawned: list[tuple[list[str], dict]] = []
    real = subprocess.Popen

    def spy(argv, **kw):
        spawned.append((list(argv), kw))
        return real(argv, **kw)

    monkeypatch.setattr(gate_mod.subprocess, "Popen", spy)
    # The production runner with a prefix that runs as the current user (env is a no-op).
    runner = RunAs(["env"])
    port_ = GatePort(store, run_as=runner, bundle_dir=tmp_path / "b", clock=clock)
    result = port_.invoke(inputs, "k", T0 + timedelta(hours=1), context=ctx())
    out = result.output
    assert out["verdict"] == PASS, (result.error, out)
    assert spawned and all(kw.get("shell") is False for _, kw in spawned), spawned
    shells = {"sh", "bash", "dash", "zsh"}
    assert not any(os.path.basename(argv[0]) in shells for argv, _ in spawned)
    gate_runs = [argv for argv, _ in spawned if declared[2] in argv]
    assert len(gate_runs) == 1
    prefix, checkout, rest = gate_runs[0][:3], gate_runs[0][3], gate_runs[0][4:]
    assert prefix == ["env", "env", "-C"] and rest == ["--", *declared]
    assert checkout != str(repo.wt) and "culture-rules-gate-" in checkout
    assert not marker.exists()
    assert f"$(touch {marker})" in out["output_tail"]


def test_run_as_refuses_shell_joining_prefixes_and_defaults_to_refusing():
    for prefix in (["ssh", "fixer@host"], ["/bin/sh", "-c"], ["su", "culture-fixer"], []):
        with pytest.raises(ValueError):
            RunAs(prefix)
    run_as, why = run_as_from_env({})
    assert run_as is None and "CULTURE_RULES_GATE_RUN_AS" in why
    run_as, _ = run_as_from_env({"CULTURE_RULES_GATE_RUN_AS": "sudo -n -u culture-fixer --"})
    assert run_as is not None and run_as.prefix == ("sudo", "-n", "-u", "culture-fixer", "--")


def test_unconfigured_runner_refuses_and_runs_nothing(store, tmp_path, clock, monkeypatch):
    inputs = Repo(tmp_path, gate_yaml([PASSING])).inputs()
    monkeypatch.setattr(gate_mod.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    port_ = GatePort.from_env(store, environ={})
    result = port_.invoke(inputs, "k", T0 + timedelta(hours=1), context=ctx())
    assert result.outcome == "failed" and not result.retryable
    assert result.error.startswith("gate_runner_unconfigured")


def test_run_as_prefix_wraps_the_argv(tmp_path):
    seen = []

    def fake(argv, **kw):
        seen.append(argv)
        return 0

    with open(tmp_path / "o", "wb") as out:
        RunAs(["sudo", "-n", "-u", "culture-fixer", "--"], run=fake)(
            ["uv", "run", "pytest"], cwd="/w", timeout=5, stdout=out
        )
    assert seen == [
        ["sudo", "-n", "-u", "culture-fixer", "--", "env", "-C", "/w", "--", "uv", "run", "pytest"]
    ]


# ------------------------------------------------------- run-as failures (t20 defects)

SUDO = "sudo -n -u culture-fixer -- /usr/bin/env PATH=/usr/bin:/bin"
NNP_STDERR = (
    b'sudo: The "no new privileges" flag is set, which prevents sudo from running as root.\n'
    b"sudo: If sudo is running in a container, you may need to adjust the container "
    b"configuration to disable the flag.\n"
)


def sudo_like(stderr_bytes: bytes, rc: int = 1):
    """A RunAs whose process step fails before running anything, the way sudo does."""
    calls = []

    def fake(argv, *, timeout, stdout, stdin=None, merge_stderr=False, stderr=None):
        calls.append(list(argv))
        target = stdout if merge_stderr else stderr
        if target is not None:
            target.write(stderr_bytes)
        return rc

    return RunAs(SUDO.split(), run=fake), calls


def test_sudo_under_no_new_privs_is_blocked_at_start_and_logged(store, tmp_path, clock, caplog):
    inputs = Repo(tmp_path, gate_yaml([PASSING])).inputs()
    probed = []

    def nnp():
        probed.append(1)
        return True

    with caplog.at_level("ERROR", logger="culture_rules.actors.gate"):
        port_ = GatePort.from_env(
            store, environ={"CULTURE_RULES_GATE_RUN_AS": SUDO}, no_new_privs=nnp
        )
    assert probed == [1]
    assert any(r.levelname == "ERROR" and "--gate-run-as" in r.getMessage() for r in caplog.records)
    assert "NoNewPrivileges=false" in caplog.text
    result = port_.invoke(inputs, "k", T0 + timedelta(hours=1), context=ctx())
    assert result.outcome == "failed" and not result.retryable
    assert result.error.startswith("run_as_blocked")
    assert "gate-sudo.conf" in result.error


def test_no_new_privs_is_checked_only_for_a_sudo_prefix(store):
    def boom():
        raise AssertionError("probed")

    port_ = GatePort.from_env(
        store,
        environ={"CULTURE_RULES_GATE_RUN_AS": "/usr/local/bin/as-fixer --"},
        no_new_privs=boom,
    )
    assert port_._blocked == ""
    port_ = GatePort.from_env(store, environ={}, no_new_privs=boom)
    assert port_._blocked == ""
    port_ = GatePort.from_env(
        store, environ={"CULTURE_RULES_GATE_RUN_AS": SUDO}, no_new_privs=lambda: False
    )
    assert port_._blocked == ""
    port_ = GatePort.from_env(
        store, environ={"CULTURE_RULES_GATE_RUN_AS": SUDO}, no_new_privs=lambda: None
    )
    assert port_._blocked == ""  # unknown (not Linux): let sudo speak for itself


def test_no_new_privs_reads_the_status_file(tmp_path):
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nNoNewPrivs:\t1\nSeccomp:\t0\n")
    assert gate_mod.no_new_privs(str(status)) is True
    status.write_text("Name:\tpython\nNoNewPrivs:\t0\n")
    assert gate_mod.no_new_privs(str(status)) is False
    status.write_text("Name:\tpython\n")
    assert gate_mod.no_new_privs(str(status)) is None
    assert gate_mod.no_new_privs(str(tmp_path / "missing")) is None


def test_a_sudo_no_new_privs_refusal_is_run_as_blocked_with_sudos_words(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    runner, calls = sudo_like(NNP_STDERR)
    result = make_port(store, runner, tmp_path, clock).invoke(
        repo.inputs(), "k", T0 + timedelta(hours=1), context=ctx()
    )
    assert result.outcome == "failed" and not result.retryable
    assert result.error.startswith('run_as_blocked: sudo: The "no new privileges" flag is set')
    assert "--gate-run-as" in result.error
    assert calls[-1][-1] == "true" and calls[-1][-3:-1] == ["/", "--"]  # the probe, in /


def test_any_other_run_as_failure_is_run_as_failed_with_the_stderr(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    runner, _ = sudo_like(b"sudo: a password is required\n")
    result = make_port(store, runner, tmp_path, clock).invoke(
        repo.inputs(), "k", T0 + timedelta(hours=1), context=ctx()
    )
    assert result.outcome == "failed" and not result.retryable
    assert result.error.startswith("run_as_failed: ")
    assert result.error.endswith("(exit 1): sudo: a password is required")


def test_a_missing_commit_is_source_unavailable_with_gits_stderr(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    inputs = {**repo.inputs(), "commit_sha": "0" * 40}
    result = make_port(store, LocalRunner(), tmp_path, clock).invoke(
        inputs, "k", T0 + timedelta(hours=1), context=ctx()
    )
    assert result.outcome == "failed" and not result.retryable
    assert result.error.startswith("source_unavailable: the worktree could not supply")
    assert "git pack-objects exited 128: fatal:" in result.error
    assert "\n" not in result.error


def test_the_stderr_tail_is_bounded_and_printable(tmp_path):
    with open(tmp_path / "err", "w+b") as err:
        err.write(b"x" * 2000 + b"\nfirst\x1b[31m red\x07\nsecond\x00\n\xff\n")
        tail = gate_mod._stderr_tail(err)
    assert len(tail.encode()) <= 520
    assert tail.endswith("first?[31m red? | second? | �")
    assert not any(ord(c) < 32 for c in tail)


# ------------------------------------------------------------------------ refusals


def test_bad_inputs_are_refused(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    port_ = make_port(store, LocalRunner(), tmp_path, clock)
    for over in ({"worktree": "relative"}, {"base_sha": "main"}, {"commit_sha": None}):
        result = port_.invoke({**repo.inputs(), **over}, "k", T0, context=ctx())
        assert result.outcome == "failed" and result.error.startswith("bad_input")


def test_missing_worktree_is_a_refusal_not_a_verdict(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    inputs = {**repo.inputs(), "worktree": str(tmp_path / "gone")}
    result = make_port(store, LocalRunner(), tmp_path, clock).invoke(
        inputs, "k", T0 + timedelta(hours=1), context=ctx()
    )
    assert result.outcome == "failed"
    assert result.error.split(":")[0] in ("source_unavailable", "gate_runner_unavailable")


def test_malformed_gate_section_is_refused_with_the_reason(store, tmp_path, clock):
    repo = Repo(tmp_path, "gate:\n  test: [[pytest, -n, 4]]\n")
    result = make_port(store, LocalRunner(), tmp_path, clock).invoke(
        repo.inputs(), "k", T0 + timedelta(hours=1), context=ctx()
    )
    assert result.outcome == "failed" and not result.retryable
    assert result.error.startswith("gate_invalid") and "quote it" in result.error


def test_past_the_deadline_is_a_retryable_refusal(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    result = make_port(store, LocalRunner(), tmp_path, clock).invoke(
        repo.inputs(), "k", T0, context=ctx()
    )
    assert result.outcome == "failed" and result.retryable
    assert result.error.startswith("deadline_exceeded")


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"test": []},
        {"test": "pytest"},
        {"test": [[]]},
        {"test": [["pytest", 4]]},
        {"test": [["A=1", "pytest"]]},
        {"setup": [["uv", "sync"]]},
        {"test": [["pytest"]], "shell": "make test"},
    ],
)
def test_gate_shape_is_strict(value):
    with pytest.raises(GateConfigError):
        gate_from_mapping(value)


def test_parse_gate_reads_the_template_shape():
    text = "agents: []\ngate:\n  setup:\n  - [uv, sync]\n  test:\n  - [uv, run, pytest, -n, auto]\n"
    spec = parse_gate(text)
    assert spec.setup == (("uv", "sync"),)
    assert spec.test == (("uv", "run", "pytest", "-n", "auto"),)
    assert parse_gate("agents: []\n") is None
    assert parse_gate("") is None


def test_this_repos_culture_yaml_declares_a_valid_gate():
    root = Path(__file__).resolve().parents[2]
    spec = parse_gate((root / "culture.yaml").read_text())
    assert spec is not None and spec.test


# ------------------------------------------------------------------------ pure guard


@pytest.mark.parametrize(
    ("path", "patterns", "hit"),
    [
        (".github/workflows/ci.yml", [".github/workflows/**"], True),
        (".github/workflows", [".github/workflows/**"], False),
        ("sub/sonar-project.properties", ["sonar-project.properties"], True),
        ("sonar-project.properties", ["/sonar-project.properties"], True),
        ("sub/sonar-project.properties", ["/sonar-project.properties"], False),
        ("a/b/.coveragerc", ["**/.coveragerc"], True),
        ("src/app.py", ["*.cfg", "setup.cfg"], False),
        ("web/eslint.config.js", ["web/*.config.js"], True),
        ("web/x/eslint.config.js", ["web/*.config.js"], False),
        ("config/lint/x.yml", ["config/lint"], True),
    ],
)
def test_path_matches(path, patterns, hit):
    assert (path_matches(path, patterns) is not None) == hit


@pytest.mark.parametrize(
    "path",
    [
        "tests with spaces/test_x.py",
        "tests/test_\ttab.py",
        "tests/test_\u00e9t\u00e9.py",
        "tests/test_[g]*.py",  # glob characters: a literal pathspec, never a pattern
    ],
)
def test_removing_a_test_from_an_unusually_named_file_is_guarded(store, tmp_path, clock, path):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("add tests", {path: TESTS_PY})
    repo.start = repo.commit("pr head 2", {})
    repo.commit("drop the failing test", {path: "def test_ok():\n    assert True\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert (out["verdict"], out["rule"]) == (GUARD, "test_removed"), out["violations"]
    assert out["violations"][0]["path"] == path


def test_a_marker_added_in_a_file_with_spaces_is_guarded(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("noqa", {"src/my app.py": "import os  # noqa: F401\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert (out["verdict"], out["rule"]) == (GUARD, "suppression_marker")
    assert out["violations"][0]["path"] == "src/my app.py"


def test_renames_with_spaces_are_tracked(store, tmp_path, clock):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    old = "tests with spaces/test_a b.py"
    repo.commit("add", {old: TESTS_PY})
    repo.start = repo.commit("pr head 2", {})
    git(repo.wt, "mv", old, "tests with spaces/test_c d.py")
    git(repo.wt, "commit", "-q", "-m", "rename within tests")
    assert judge(store, LocalRunner(), repo, tmp_path, clock)["verdict"] == PASS
    (repo.wt / "attic dir").mkdir()
    git(repo.wt, "mv", "tests with spaces/test_c d.py", "attic dir/a b.py")
    git(repo.wt, "commit", "-q", "-m", "move out of tests")
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert (out["verdict"], out["rule"]) == (GUARD, "test_deleted"), out["violations"]
    assert out["violations"][0]["path"] == old  # the old name, from start..commit


def test_diff_guard_on_raw_git_output():
    names = "M\x00src/a b.py\x00R100\x00tests/test_a.py\x00attic/a.py\x00"
    patches = {
        # headers are ignored: the path comes from the -z name list, never a +++ line
        "src/a b.py": "diff --git a/src/a b.py b/src/a b.py\n--- a/src/a b.py\t\n"
        "+++ b/src/a b.py\t\n@@ -1 +1,2 @@\n--- not a header: a removed line\n"
        "+x = 1  # noqa\n+y = 2\n",
    }
    found = diff_guard(names, patches, [])
    assert [(v.rule, v.path) for v in found] == [
        ("test_deleted", "tests/test_a.py"),
        ("suppression_marker", "src/a b.py"),
    ]


# ------------------------------------------------------------------------ end to end


class PushSpy(GitHubPushPort):
    """The real github.push port, its git and HTTP seams recording (and failing) calls."""

    def __init__(self, store):
        self.git_calls: list = []
        self.http_calls: list = []

        def no_git(argv, env, timeout):
            self.git_calls.append(argv)
            return 1, ""

        def no_http(*a, **k):
            self.http_calls.append(a)
            raise AssertionError("no network")

        super().__init__(store, git=no_git, transport=no_http)


def _fixer_workflow(until_verdicts: list[str]):
    wt_inputs = (port("worktree"), port("base_sha"), port("start_sha"))
    agent = step(
        "agent",
        "ai",
        inputs=(port("instruction", "string"),),
        outputs=(port("head_after", "string"),),
    )
    gate_step = step(
        "gate",
        "code",
        inputs=(*wt_inputs, port("commit_sha")),
        outputs=(
            port("verdict", "string"),
            port("instruction", required=False),
            port("bundle", required=False),
        ),
        config={"builtin": "gate"},
    )
    loop = step(
        "fix",
        "retry_until",
        inputs=(port("instruction", "string"), *wt_inputs),
        outputs=(
            port("verdict", "string"),
            port("bundle", required=False),
            port("instruction", required=False),
        ),
        max_iterations=2,
        config={
            "until": {
                "op": "in",
                "value": {"field": "verdict"},
                "items": {"literal": until_verdicts},
            },
            "carry": {"instruction": "instruction"},
        },
        body=(agent, gate_step),
    )
    edges = (
        edge("inputs", "instruction", "fix", "instruction"),
        edge("inputs", "worktree", "fix", "worktree"),
        edge("inputs", "base_sha", "fix", "base_sha"),
        edge("inputs", "start_sha", "fix", "start_sha"),
        edge("agent", "head_after", "gate", "commit_sha"),
    )
    return workflow(
        (loop,),
        edges,
        inputs=(
            port("instruction", "string"),
            port("worktree", "string"),
            port("base_sha", "string"),
            port("start_sha", "string"),
        ),
        outputs=(
            Output(name="verdict", type="string", source="steps.fix.outputs.verdict"),
            Output(name="bundle", source="steps.fix.outputs.bundle"),
        ),
    )


def _run_fixer(store, clock, tmp_path, repo, agent_commits, until_verdicts):
    """Run the fixer loop; the fake agent's iteration ``i`` leaves the worktree at commit i."""
    heads = [repo.commit(f"agent {i}", files) for i, files in enumerate(agent_commits)]

    class Agent(FakeActor):
        def invoke(self, input, key, deadline, *, context):
            i = int(context.step_id.split("[", 1)[1].split("]", 1)[0])
            git(repo.wt, "reset", "-q", "--hard", heads[i])
            self.on(context.step_id, ("complete", {"head_after": heads[i]}))
            return super().invoke(input, key, deadline, context=context)

    agent, runner, push = Agent(), LocalRunner(), PushSpy(store)
    gate_port = GatePort(store, run_as=runner, bundle_dir=tmp_path / "bundles", clock=clock)
    ports = {
        "ai": agent,
        "code": BuiltinCodePort({"gate": gate_port}),
        "action:github.push": push,
    }
    action = Action(
        kind="github.push",
        params={
            "actor": "gh",
            "repo": "o/r",
            "number": 1,
            "head_branch": "fix",
            "expected_head_sha": repo.start,
            "commit_sha": heads[-1],
            "source": "{{ workflow.outputs.bundle }}",
            "gate_verdict": "workflow.outputs.verdict",
        },
    )
    inputs = {
        "instruction": "fix the PR",
        "worktree": str(repo.wt),
        "base_sha": repo.base,
        "start_sha": repo.start,
    }
    ex = Executor(store, "spark", ports, clock=clock)
    run = ex.start(
        rule(action=action, workflow_inputs={k: f"trigger.data.{k}" for k in inputs}),
        _fixer_workflow(until_verdicts),
        trigger={"kind": "manual", "data": inputs},
    )
    ex.run_until_idle()
    return ex.run(run["id"]), agent, push, runner


def test_guard_verdict_pushes_nothing_end_to_end(store, clock, tmp_path):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    drop = {"tests/test_x.py": "def test_ok():\n    assert True\n"}
    doc, _agent, push, runner = _run_fixer(store, clock, tmp_path, repo, [drop, {}], ["pass"])
    assert doc["status"] == "failed"
    assert step_state(doc, "fix")["error"]["code"] == "loop_max_exceeded", doc["steps"]
    assert step_state(doc, "fix[0]/gate")["outputs"]["rule"] == "test_removed"
    assert (
        step_state(doc, ACTION_STEP) is None
        or step_state(doc, ACTION_STEP)["status"] != "succeeded"
    )
    assert push.git_calls == [] and push.http_calls == []
    assert runner.gate_calls() == []


def test_no_gate_verdict_pushes_nothing_end_to_end(store, clock, tmp_path):
    repo = Repo(tmp_path, "agents: []\n")
    doc, agent, push, runner = _run_fixer(
        store, clock, tmp_path, repo, [{"src/app.py": "x = 3\n"}], ["pass", "no_gate"]
    )
    assert step_state(doc, "fix")["outputs"]["verdict"] == NO_GATE
    assert len(agent.calls) == 1  # no_gate ends the loop at once
    action = step_state(doc, ACTION_STEP)
    assert action["status"] == "failed" and "gate_not_passed" in action["error"]["message"]
    assert push.git_calls == [] and push.http_calls == []
    assert runner.gate_calls() == []


def test_fail_instruction_becomes_the_agents_next_instruction(store, clock, tmp_path):
    check = [
        PY,
        "-c",
        "import sys; t = open('src/app.py').read(); print('FAILED: x is', t); "
        "sys.exit('3' not in t)",
    ]
    repo = Repo(tmp_path, gate_yaml([check]))
    doc, agent, push, _runner = _run_fixer(
        store,
        clock,
        tmp_path,
        repo,
        [{"src/app.py": "x = 2  # wrong\n"}, {"src/app.py": "x = 3\n"}],
        ["pass"],
    )
    first = step_state(doc, "fix[0]/gate")["outputs"]
    assert first["verdict"] == FAIL
    calls = [c[1]["instruction"] for c in agent.calls]
    assert calls[0] == "fix the PR"
    assert calls[1] == first["instruction"]
    assert "FAILED: x is" in calls[1] and first["output_tail"] in calls[1]
    assert step_state(doc, "fix")["outputs"]["verdict"] == PASS
    # the gate passed, so the push port got past the verdict check (and failed only on the
    # missing App actor in this store)
    action = step_state(doc, ACTION_STEP)
    assert "gate_not_passed" not in str(action["error"])


def test_default_ports_route_actorless_code_steps_to_builtins():
    ports = default_ports(MemoryStore(), "spark")
    code = ports["code"]
    assert isinstance(code, BuiltinCodePort)
    result = code.invoke({}, "k", T0, context=InvocationContext("r", "s", "code", "spark"))
    assert result.outcome == "failed" and result.error.startswith("no_builtin")
    assert not result.retryable
    result = code.invoke({}, "k", T0, context=ctx())
    assert isinstance(result, InvocationResult) and result.outcome == "failed"
