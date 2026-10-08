"""Codex review #5 (P1): a change the text diff cannot show is never "reviewed".

``git diff`` prints ``Binary files ... differ`` for a binary change, and a mode change, a
symlink or a submodule pointer is a one-line header the reviewer may not weigh. Any such
change marks the review material incomplete: ``diff_truncated`` is true (the text does
not show the whole change) and ``diff_problems`` says which paths and why. The fixer's
``review`` step then never runs Codex and asks for a text-only fix.
"""

from __future__ import annotations

import os

import pytest

from culture_rules.actors.gate import PASS
from tests.actors.test_gate import (  # noqa: F401 - fixtures
    PASSING,
    LocalRunner,
    Repo,
    clock,
    gate_yaml,
    git,
    judge,
    store,
)


def _binary(repo):
    (repo.wt / "src").mkdir(exist_ok=True)
    (repo.wt / "src/blob.bin").write_bytes(b"\x00\x01payload\x00" * 10)
    git(repo.wt, "add", "src/blob.bin")


def _exec_mode(repo):
    os.chmod(repo.wt / "src/app.py", 0o755)
    git(repo.wt, "add", "src/app.py")


def _new_executable(repo):
    path = repo.wt / "run.sh"
    path.write_text("#!/bin/sh\necho hi\n")
    os.chmod(path, 0o755)
    git(repo.wt, "add", "run.sh")


def _symlink(repo):
    os.symlink("/etc/passwd", repo.wt / "src/link")
    git(repo.wt, "add", "src/link")


def _submodule(repo):
    git(repo.wt, "update-index", "--add", "--cacheinfo", f"160000,{repo.base},vendor/sub")


@pytest.mark.parametrize(
    "change, path, why",
    [
        (_binary, "src/blob.bin", "binary"),
        (_exec_mode, "src/app.py", "mode"),
        (_new_executable, "run.sh", "mode"),
        (_symlink, "src/link", "symlink"),
        (_submodule, "vendor/sub", "submodule"),
    ],
)
def test_a_non_text_change_marks_the_review_material_incomplete(
    store, tmp_path, clock, change, path, why  # noqa: F811
):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    change(repo)
    git(repo.wt, "commit", "-q", "-m", "agent change")
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS
    assert out["diff_truncated"] is True
    assert any(path in p and why in p for p in out["diff_problems"]), out["diff_problems"]


def test_a_text_only_change_has_no_problems(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n", "src/new.py": "y = 1\n"})
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["diff_truncated"] is False
    assert out["diff_problems"] == []


def test_non_utf8_text_is_incomplete_not_replaced(store, tmp_path, clock):  # noqa: F811
    # git calls latin-1 text "text", but decoding it with replacement would hide bytes
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    (repo.wt / "src/app.py").write_bytes(b"x = 3  # caf\xe9 \xff\xfe hidden\n")
    git(repo.wt, "add", "src/app.py")
    git(repo.wt, "commit", "-q", "-m", "fix")
    out = judge(store, LocalRunner(), repo, tmp_path, clock)
    assert out["verdict"] == PASS
    assert out["diff_truncated"] is True
    assert any("UTF-8" in p for p in out["diff_problems"]), out["diff_problems"]
