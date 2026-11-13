"""The runner's ``segment`` argument type: one safe path segment."""

from __future__ import annotations

import pytest

from culture_rules.actors.code import CodeRunnerError, bind_argv

SPEC = {"argv": ["cat", "/data/{key}"], "params": {"key": "segment"}}


def test_segment_accepts_a_key():
    assert bind_argv(SPEC, {"key": "SCRUM-12"}) == ["cat", "/data/SCRUM-12"]


def test_segment_accepts_the_length_limit():
    assert bind_argv(SPEC, {"key": "a" * 128}) == ["cat", "/data/" + "a" * 128]


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "a/b",
        "/abs",
        "a\\b",
        "a\x00b",
        "a" * 129,
        12,
        None,
        True,
        "-",
        "--help",
        "-rf",
        "a\nb",
        "a\tb",
        "a\x1bb",
        "a\x7fb",
        "a\x1fb",
        "a,b",
        "a(b",
        "a)b",
        "a*",
        "a?",
        "a[1]",
        "a]",
        "a{b",
        "a}",
    ],
)
def test_segment_refuses_unsafe_values(value):
    with pytest.raises(CodeRunnerError):
        bind_argv(SPEC, {"key": value})


def test_existing_types_unchanged():
    spec = {
        "argv": ["x", "{a}", "{b}", "{c}", "{d}"],
        "params": {"a": "string", "b": "int", "c": "number", "d": "bool"},
    }
    assert bind_argv(spec, {"a": "../x/", "b": 3, "c": 1.5, "d": True}) == [
        "x",
        "../x/",
        "3",
        "1.5",
        "true",
    ]
