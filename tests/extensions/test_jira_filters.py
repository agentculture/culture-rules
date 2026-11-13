"""The jira.* trigger filter ``users``: matched, validated and described through the seams."""

from __future__ import annotations

import pytest

from culture_rules.engine.matching import trigger_matches
from culture_rules.model.describe import trigger_text
from culture_rules.model.rule import Trigger
from culture_rules.model.validate import validate

COMMENT = "jira.comment.created"
CREATED = "jira.issue.created"
A = "5b10ac8d82e05b22cc7d4ef5"
B = "712020:9a1b"


def trig(type_: str = COMMENT, **params) -> Trigger:
    return Trigger(kind="event", params={"type": type_, **params})


def ev(type_: str = COMMENT, **data) -> dict:
    base = {"key": "ENG-1", "author": A}
    base.update(data)
    return {"kind": "event", "type": type_, "data": base}


def codes(params: dict, type_: str = COMMENT) -> set[str]:
    return {e.code for e in validate(trig(type_, **params))}


# --------------------------------------------------------------------------- match


@pytest.mark.parametrize("type_", [COMMENT, CREATED])
def test_users_fires_for_a_listed_author_only(type_):
    assert trigger_matches(trig(type_, users=[A]), ev(type_))
    assert not trigger_matches(trig(type_, users=[A]), ev(type_, author=B))
    assert trigger_matches(trig(type_, users=[B, A]), ev(type_))


def test_users_is_case_sensitive_accountids_are_opaque():
    assert not trigger_matches(trig(users=[A.upper()]), ev())


def test_author_none_never_matches_a_users_filter():
    assert not trigger_matches(trig(users=[A]), ev(author=None))
    assert not trigger_matches(trig(users=[A]), {"kind": "event", "type": COMMENT, "data": {}})


@pytest.mark.parametrize("type_", [COMMENT, CREATED])
def test_self_authored_never_fires_a_users_filter_even_with_include_self(type_):
    own = ev(type_, self_authored=True)
    assert not trigger_matches(trig(type_, users=[A]), own)
    assert not trigger_matches(trig(type_, users=[A], include_self=True), own)


@pytest.mark.parametrize("type_", [COMMENT, CREATED])
def test_no_users_param_keeps_todays_behaviour(type_):
    assert trigger_matches(trig(type_), ev(type_))
    assert trigger_matches(trig(type_), ev(type_, author=None))
    assert not trigger_matches(trig(type_), ev(type_, self_authored=True))
    assert trigger_matches(trig(type_, include_self=True), ev(type_, self_authored=True))


def test_a_malformed_users_param_fails_closed():
    assert not trigger_matches(trig(users=A), ev())
    assert not trigger_matches(trig(users=[]), ev())
    assert not trigger_matches(trig(users=None), ev())


def test_other_namespaces_are_untouched():
    other = Trigger(kind="event", params={"type": "x.y", "users": [B]})
    assert trigger_matches(other, {"kind": "event", "type": "x.y", "data": {"author": A}})


# --------------------------------------------------------------------------- validate


@pytest.mark.parametrize("type_", [COMMENT, CREATED])
def test_a_list_of_strings_is_accepted(type_):
    assert codes({}, type_) == set()
    assert codes({"users": [A]}, type_) == set()
    assert codes({"users": [A, B]}, type_) == set()


@pytest.mark.parametrize(
    "users", [A, [], [""], ["  "], [A, 7], [None], {"id": A}, None, 7, [[A]], [True]]
)
def test_users_that_is_not_a_non_empty_list_of_strings_is_refused(users):
    trigger = trig(users=users)
    errors = validate(trigger)
    assert {e.code for e in errors} == {"trigger_param_invalid"}
    assert [e.path for e in errors] == ["params.users"]
    assert "params.users" in errors[0].message


# --------------------------------------------------------------------------- describe


def test_describe_says_the_filter_in_words():
    assert trigger_text(trig(users=[A])) == f"{COMMENT} (by {A})"
    assert trigger_text(trig(CREATED, users=["A", "B"])) == f"{CREATED} (by A or B)"


def test_describe_without_users_is_the_type_alone():
    assert trigger_text(trig()) == COMMENT
