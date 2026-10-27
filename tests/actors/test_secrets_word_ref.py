"""``<word> grant:<NAME>`` header values pass the save-time secret check (#115 review)."""

import pytest

from culture_rules.actors.secrets import SecretError, assert_refs_only, split_word_ref


def _policy(name, value):
    return {"http": {"headers": {name: value}}}


@pytest.mark.parametrize("name", ["Authorization", "X-Api-Key", "X-Auth-Token"])
def test_a_word_then_a_reference_is_accepted_in_a_header(name):
    assert assert_refs_only(_policy(name, "Bearer grant:KEY")) is None


def test_a_bare_reference_is_still_accepted_in_a_header():
    assert assert_refs_only(_policy("X-Api-Key", "grant:KEY")) is None


@pytest.mark.parametrize(
    "value", ["Bearer sk-literal", "Bearer grant:", "two words grant:KEY", "grant:KEY extra"]
)
def test_any_other_value_in_a_secret_header_is_refused(value):
    policy = _policy("X-Api-Key", value)
    with pytest.raises(SecretError):
        assert_refs_only(policy)


def test_a_word_then_a_reference_outside_headers_is_refused():
    params = {"api_key": "Bearer grant:KEY"}
    with pytest.raises(SecretError):
        assert_refs_only(params)


def test_split_word_ref_returns_the_word_and_the_reference():
    assert split_word_ref("Bearer grant:KEY") == ("Bearer", "grant:KEY")


@pytest.mark.parametrize("value", ["grant:KEY", "a b grant:KEY", "Bearer  grant:KEY", 7])
def test_split_word_ref_is_none_for_other_shapes(value):
    assert split_word_ref(value) is None
