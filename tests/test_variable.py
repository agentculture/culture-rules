"""Tests for the Variable model."""

from __future__ import annotations

from culture_rules.model.variable import Variable, VariableVersion


def test_variable_construction():
    v = Variable(
        name="foo",
        value=42,
        version=1,
        updated_by="me",
        updated_at="2026-01-01T00:00:00+00:00",
        description="a variable",
    )
    assert v.name == "foo"
    assert v.value == 42
    assert v.version == 1
    assert v.updated_by == "me"
    assert v.description == "a variable"


def test_variable_to_dict_round_trip():
    v = Variable(
        name="bar",
        value=[1, 2, 3],
        version=2,
        updated_by="svc",
        updated_at="2026-06-01T12:00:00+00:00",
        description=None,
    )
    d = v.to_dict()
    assert d["name"] == "bar"
    assert d["value"] == [1, 2, 3]
    assert d["version"] == 2
    assert d["updated_by"] == "svc"
    assert d["description"] is None
    v2 = Variable.from_dict(d)
    assert v2.name == v.name
    assert v2.value == v.value
    assert v2.version == v.version
    assert v2.updated_by == v.updated_by
    assert v2.updated_at == v.updated_at


def test_variable_to_json_round_trip():
    v = Variable(
        name="x",
        value="hello",
        version=1,
        updated_by="u",
        updated_at="2026-01-01T00:00:00+00:00",
        description="desc",
    )
    j = v.to_json()
    v2 = Variable.from_json(j)
    assert v2.name == v.name
    assert v2.value == v.value


def test_variable_version_construction():
    vv = VariableVersion(
        version=1,
        value=10,
        updated_by="me",
        updated_at="2026-01-01T00:00:00+00:00",
        description="first",
    )
    assert vv.version == 1
    assert vv.value == 10
    d = vv.to_dict()
    assert d["description"] is not None


def test_variable_description_defaults_none():
    v = Variable(
        name="nope",
        value=0,
        version=1,
        updated_by="me",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert v.description is None
