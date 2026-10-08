"""Characterization tests (Sonar S3776 refactor of model.variable._check_scalar): which values
a shared variable accepts, and the exact error for each one it refuses."""

from __future__ import annotations

import math

import pytest

from culture_rules.model.variable import validate_variable_value


@pytest.mark.parametrize(
    "value",
    [None, True, False, 0, -3, 1.5, "", "text", [], [1, "a", 2.5, True, None], [False]],
)
def test_json_scalars_and_flat_lists_are_accepted(value):
    assert validate_variable_value(value) is None


@pytest.mark.parametrize(
    "value, message",
    [
        (math.nan, "invalid variable value nan: non-finite floats are not valid JSON"),
        (math.inf, "invalid variable value inf: non-finite floats are not valid JSON"),
        (-math.inf, "invalid variable value -inf: non-finite floats are not valid JSON"),
        ({"a": 1}, "invalid variable value dict: must be a JSON scalar or list"),
        ((1, 2), "invalid variable value tuple: must be a JSON scalar or list"),
        (b"x", "invalid variable value bytes: must be a JSON scalar or list"),
        ([1, [2]], "invalid variable value list: must be a JSON scalar or list"),
        ([{"a": 1}], "invalid variable value dict: must be a JSON scalar or list"),
        ([1, math.nan], "invalid variable value nan: non-finite floats are not valid JSON"),
        ([math.inf, {}], "invalid variable value inf: non-finite floats are not valid JSON"),
        ([{}, math.inf], "invalid variable value dict: must be a JSON scalar or list"),
    ],
)
def test_everything_else_is_refused_with_its_reason(value, message):
    with pytest.raises(ValueError) as exc:
        validate_variable_value(value)
    assert str(exc.value) == message
