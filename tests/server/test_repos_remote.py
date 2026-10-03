"""``RepoTarget.is_remote``: which configured locations are git remotes, decided in linear time."""

from __future__ import annotations

import time

import pytest

from culture_rules.server.repos import RepoTarget, parse_repos


@pytest.mark.parametrize(
    ("location", "remote"),
    [
        ("https://github.com/agentculture/workflows.git", True),
        ("ssh://git@github.com/agentculture/workflows.git", True),
        ("git+ssh://host/repo", True),
        ("git@github.com:agentculture/workflows.git", True),
        ("u@h:p", True),
        ("a@b@c:d", True),
        ("/srv/defs", False),
        ("relative/dir", False),
        ("C:/repos/defs", False),
        ("@host:path", False),  # needs a user before the @
        ("user@:path", False),  # needs a host between @ and :
        ("dir/user@host:path", False),  # a / before the @ makes it a path
        ("user@host", False),  # no : after the host
        ("user@ho/st:path", False),  # a / between @ and : makes it a path
        ("Https://example.com/x", False),  # schemes are lower-case
        ("1ab://x", False),  # a scheme starts with a letter
        ("a b://x", False),
        ("user name@host:x", False),
        ("", False),
    ],
)
def test_is_remote_classifies_locations(location, remote):
    assert RepoTarget("t", location).is_remote is remote


def test_remote_names_derive_from_owner_and_repo():
    assert parse_repos("git@github.com:agentculture/workflows.git") == [
        RepoTarget("agentculture/workflows", "git@github.com:agentculture/workflows.git")
    ]


@pytest.mark.parametrize(
    ("location", "remote"),
    [
        ("@" * 50_000, False),  # many candidate @ splits, never a :
        ("a" + "@a" * 25_000, False),
        ("a" * 50_000 + "://", True),
        ("a" * 50_000 + ":/", False),
        ("a@" + "b" * 50_000 + "/", False),
    ],
    ids=["at-run", "at-a-run", "long-scheme", "almost-scheme", "slash-after-host"],
)
def test_is_remote_is_linear_on_adversarial_input(location, remote):
    start = time.perf_counter()
    assert RepoTarget("t", location).is_remote is remote
    assert time.perf_counter() - start < 0.25
