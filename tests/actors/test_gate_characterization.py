"""Characterization tests (Sonar S3776 refactor of gate.py): the exact gate-config errors,
the -U0 hunk walker, and every diff-guard rule, pinned as they behave today."""

from __future__ import annotations

import pytest

from culture_rules.actors.gate import (
    GateConfigError,
    Violation,
    _hunk_lines,
    diff_guard,
    gate_from_mapping,
)


@pytest.mark.parametrize(
    "value, message",
    [
        ({"test": "pytest"}, "gate.test must be a list of argv lists"),
        ({"test": []}, "gate.test must declare at least one command"),
        ({"test": [["pytest"]], "setup": "x"}, "gate.setup must be a list of argv lists"),
        ({"test": [[]]}, "gate.test[0] must be a non-empty list of strings"),
        ({"test": [["a"], "b"]}, "gate.test[1] must be a non-empty list of strings"),
        ({"test": [["pytest", 4]]}, "gate.test[0][1] must be a string (quote it), not 4"),
        ({"test": [["py\x00test"]]}, "gate.test[0][0] contains a NUL byte"),
        ({"test": [[""]]}, "gate.test[0][0] must name a program (non-empty, no '=')"),
        ({"test": [["A=1", "x"]]}, "gate.test[0][0] must name a program (non-empty, no '=')"),
        (
            {"test": [["ok"]], "setup": [["uv"], ["B=2"]]},
            "gate.setup[1][0] must name a program (non-empty, no '=')",
        ),
    ],
)
def test_gate_config_errors_name_the_exact_place(value, message):
    with pytest.raises(GateConfigError) as exc:
        gate_from_mapping(value)
    assert str(exc.value) == message


def test_a_valid_gate_keeps_setup_and_test_as_argv_tuples():
    spec = gate_from_mapping({"setup": [], "test": [["uv", "run", "pytest"], ["x", "a=b"]]})
    assert spec.setup == ()
    assert spec.test == (("uv", "run", "pytest"), ("x", "a=b"))


def test_hunk_lines_walks_counted_hunks_only():
    patch = "\n".join(
        [
            "diff --git a/f b/f",
            "--- a/f",
            "+++ b/f",
            "@@ -1,2 +1,3 @@ ctx",
            " kept",  # a context line counts on both sides
            "-old",
            "+new",
            "+more",
            "+beyond the count",  # past the hunk: not a hunk line
            "@@ -5 +6 @@",  # no counts: one line each
            "-five",
            "\\ No newline at end of file",
            "+six",
            "@@ -9,0 +10,2 @@",
            "+a",
        ]
    )
    assert list(_hunk_lines(patch)) == [
        ("-", "old"),
        ("+", "new"),
        ("+", "more"),
        ("-", "five"),
        ("+", "six"),
        ("+", "a"),  # a truncated hunk ends with the patch
    ]


def test_hunk_lines_of_nothing_is_nothing():
    assert list(_hunk_lines("")) == []
    assert list(_hunk_lines("no hunk here\n+not counted")) == []


def _ns(*entries):
    return "".join(f"{s}\x00" + "\x00".join(paths) + "\x00" for s, *paths in entries)


def test_diff_guard_reports_every_rule_in_order():
    name_status = _ns(
        ("M", ".github/workflows/ci.yml"),
        ("D", "tests/test_gone.py"),
        ("R100", "tests/test_moved.py", "src/moved.py"),
        ("R100", "tests/test_a.py", "tests/test_b.py"),
        ("M", "tests/test_edit.py"),
        ("M", "src/app.py"),
    )
    patches = {
        "tests/test_edit.py": "@@ -1,2 +1,2 @@\n-def test_one():\n"
        "-def test_two():\n+def test_two():\n+@pytest.mark.skip\n",
        "src/app.py": "@@ -1 +1,2 @@\n-x = 1\n+x = 1  # noqa\n+y = 2  # type: ignore\n",
    }
    assert diff_guard(name_status, patches, ["src/secret/*"]) == [
        Violation("protected_path", ".github/workflows/ci.yml", "matches '.github/workflows/**'"),
        Violation("test_deleted", "tests/test_gone.py", "test file deleted"),
        Violation("test_deleted", "tests/test_moved.py", "moved to src/moved.py"),
        Violation("test_removed", "tests/test_edit.py", "test 'test_one' removed"),
        Violation("test_skipped", "tests/test_edit.py", "pytest.mark.skip: @pytest.mark.skip"),
        Violation("suppression_marker", "src/app.py", "noqa: x = 1  # noqa"),
        Violation("suppression_marker", "src/app.py", "type: ignore: y = 2  # type: ignore"),
    ]


def test_diff_guard_matches_user_patterns_on_both_sides_of_a_rename():
    found = diff_guard(_ns(("R090", "src/a.py", "src/secret/a.py")), {}, ["src/secret/*"])
    assert found == [Violation("protected_path", "src/secret/a.py", "matches 'src/secret/*'")]


def test_diff_guard_of_a_clean_diff_is_empty():
    patches = {"src/app.py": "@@ -1 +1 @@\n-x = 1\n+x = 2\n"}
    assert diff_guard(_ns(("M", "src/app.py")), patches, []) == []
