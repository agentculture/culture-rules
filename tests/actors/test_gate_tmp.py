"""The gate's temp space: no run-as account name in a repo's temp paths (lobes-cli#302).

pytest's default basetemp is ``<tmp>/pytest-of-<user>``, so under the run-as account
every ``tmp_path`` contained ``culture-fixer`` (which contains ``-f``), and a repo test that
asserts ``-f`` is absent from a dry-run output echoing a tmp path failed only in the gate.
The gate now runs setup and test commands with ``TMPDIR`` and pytest's ``--basetemp``
pointed into its own fresh workspace, which is removed with the checkout.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from datetime import timedelta

from culture_rules.actors.gate import FAIL, PASS, GatePort, RunAs, gate_env, run_process
from tests.actors.test_gate import (  # noqa: F401 - fixtures
    GIT_ENV,
    PY,
    LocalRunner,
    Repo,
    clock,
    ctx,
    gate_yaml,
    judge,
    store,
)
from tests.engine.run_helpers import T0

FIXER = "culture-fixer"
TMP_TEST = (
    "import os\n\n\n"
    "def test_tmp_path_names_no_account(tmp_path):\n"
    f"    assert {FIXER!r} not in str(tmp_path), tmp_path\n"
    "    assert str(tmp_path).startswith(os.environ['TMPDIR'] + os.sep), tmp_path\n"
)
PYTEST = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_tmp.py"]


def fixer_env(tmp_path) -> dict[str, str]:
    """The environment a run-as account named culture-fixer gets (getpass reads LOGNAME)."""
    system_tmp = tmp_path / "systmp"
    system_tmp.mkdir(exist_ok=True)
    return {
        **GIT_ENV,
        "HOME": str(tmp_path),
        "LOGNAME": FIXER,
        "USER": FIXER,
        "TMPDIR": str(system_tmp),
    }


def fixer_runner(tmp_path) -> LocalRunner:
    return LocalRunner(env=fixer_env(tmp_path))


# ---------------------------------------------------------------- the env the gate builds


def test_gate_env_points_tmpdir_and_basetemp_into_the_workspace():
    assert gate_env("/tmp/ws/tmp") == [
        "env",
        "TMPDIR=/tmp/ws/tmp",
        "PYTEST_ADDOPTS=--basetemp=/tmp/ws/tmp/pytest",
    ]


def test_gate_env_preserves_an_existing_pytest_addopts():
    words = gate_env("/tmp/ws/tmp", addopts="-x --strict-markers")
    assert words[-1] == "PYTEST_ADDOPTS=-x --strict-markers --basetemp=/tmp/ws/tmp/pytest"


def test_gate_env_quotes_a_basetemp_with_spaces():
    words = gate_env("/tmp/a b/tmp")
    value = words[-1].split("=", 1)[1]
    assert shlex.split(value) == ["--basetemp=/tmp/a b/tmp/pytest"]


def test_gate_commands_get_workspace_tmp_and_run_as_declared(store, tmp_path, clock):  # noqa: F811
    probe = [PY, "-c", "import os, sys; print(os.environ['TMPDIR'], sys.argv[1:])", "a", "-f"]
    setup = [PY, "-c", "print('setup')"]
    repo = Repo(tmp_path, gate_yaml([probe], setup=[setup]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    runner = fixer_runner(tmp_path)
    out = judge(store, runner, repo, tmp_path, clock)
    assert out["verdict"] == PASS, out
    assert out["command"] is None
    assert out["gate"]["test"] == [probe]
    raw = [c for c in runner.calls if PY in c]
    assert len(raw) == 2
    for argv, declared in zip(raw, (setup, probe)):
        env_words, rest = argv[: argv.index(PY)], argv[argv.index(PY) :]
        assert rest == declared  # a non-pytest command runs exactly as declared
        (cwd,) = {d for c, d in zip(runner.calls, runner.cwds) if c == argv}
        workspace = os.path.dirname(cwd)
        assert env_words == gate_env(os.path.join(workspace, "tmp"))
        assert FIXER not in workspace
        assert "-" not in workspace.removeprefix(str(tmp_path))
    printed_tmp = out["output_tail"].split()[0]
    assert printed_tmp == os.path.join(workspace, "tmp")
    assert "['a', '-f']" in out["output_tail"]
    assert not os.path.exists(workspace)  # removed with the checkout


def test_the_production_runner_passes_the_env_through(store, tmp_path, clock):  # noqa: F811
    probe = [PY, "-c", "import os; print('T=' + os.environ['TMPDIR'])"]
    repo = Repo(tmp_path, gate_yaml([probe]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    seen: list[list[str]] = []

    def spy(argv, **kw):
        seen.append(list(argv))
        return run_process(argv, env=GIT_ENV, **kw)

    port_ = GatePort(store, run_as=RunAs(["env"], run=spy), bundle_dir=tmp_path / "b", clock=clock)
    result = port_.invoke(repo.inputs(), "k", T0 + timedelta(hours=1), context=ctx())
    assert result.output["verdict"] == PASS, (result.error, result.output)
    (argv,) = [a for a in seen if probe[2] in a]
    assert argv[:3] == ["env", "env", "-C"]
    assert argv[4] == "--"
    workspace = os.path.dirname(argv[3])
    assert argv[5:] == [*gate_env(os.path.join(workspace, "tmp")), *probe]
    assert f"T={workspace}/tmp" in result.output["output_tail"]


# ---------------------------------------------------------------- a real pytest repo


def test_control_pytest_names_the_account_in_tmp_path_without_the_gate(tmp_path):
    """Why the gate needs this: the same test fails under the account's own defaults."""
    repo = tmp_path / "control"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests/test_tmp.py").write_text(TMP_TEST)
    proc = subprocess.run(  # nosec B603 - fixed argv
        PYTEST, cwd=repo, env=fixer_env(tmp_path), capture_output=True, text=True
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"pytest-of-{FIXER}" in proc.stdout


def test_a_pytest_repo_sees_no_account_name_in_tmp_path(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PYTEST]))
    repo.commit("tmp test", {"tests/test_tmp.py": TMP_TEST})
    out = judge(store, fixer_runner(tmp_path), repo, tmp_path, clock)
    assert out["verdict"] == PASS, out["output_tail"]
    assert "1 passed" in out["output_tail"]


def test_a_repos_own_basetemp_on_the_command_line_wins(store, tmp_path, clock):  # noqa: F811
    own = tmp_path / "own-basetemp"
    check = (
        "import os\n\n\n"
        "def test_own(tmp_path):\n"
        f"    assert str(tmp_path).startswith({str(own) + os.sep!r}), tmp_path\n"
    )
    command = [*PYTEST[:-1], f"--basetemp={own}", "tests/test_own.py"]
    repo = Repo(tmp_path, gate_yaml([command]))
    repo.commit("own basetemp", {"tests/test_own.py": check})
    out = judge(store, fixer_runner(tmp_path), repo, tmp_path, clock)
    assert out["verdict"] == PASS, out["output_tail"]


def test_a_failing_tmp_path_assertion_is_still_a_fail(store, tmp_path, clock):  # noqa: F811
    bad = "def test_bad(tmp_path):\n    assert 'tmp' not in str(tmp_path)\n"
    repo = Repo(tmp_path, gate_yaml([[*PYTEST[:-1], "tests/test_bad.py"]]))
    repo.commit("bad", {"tests/test_bad.py": bad})
    assert judge(store, fixer_runner(tmp_path), repo, tmp_path, clock)["verdict"] == FAIL
