"""d37: the PR read also reports the base branch's live tip, and checks that a given base
commit is on the base branch (no older than the PR's recorded base)."""

from __future__ import annotations

import json

from culture_rules.store.memory import MemoryStore
from tests.node.test_github_action import (  # noqa: F401 - fixtures
    DEADLINE,
    Fake,
    actor_doc,
    ctx,
    pem,
)

PULL_BASE = "a" * 40
TIP = "b" * 40
MID = "d" * 40


class BranchFake(Fake):
    """``/pulls/3`` (base ``main`` at :data:`PULL_BASE`), ``/branches/main`` (tip
    :data:`TIP`) and ``/compare/X...Y`` answered from ``statuses``."""

    def __init__(self, statuses=None, branch_status=200):
        super().__init__(200)
        self.statuses = statuses or {}
        self.branch_status = branch_status

    def __call__(self, method, url, headers, body, timeout):
        if url.endswith("/access_tokens"):
            return super().__call__(method, url, headers, body, timeout)
        self.calls.append(url)
        if "/pulls/" in url:
            pull = {"head": {"sha": "c" * 40}, "base": {"sha": PULL_BASE, "ref": "main"}}
            return 200, json.dumps({**pull, "state": "open", "merged": False}).encode()
        if "/branches/" in url:
            if self.branch_status != 200:
                return self.branch_status, b"{}"
            return 200, json.dumps({"name": "main", "commit": {"sha": TIP}}).encode()
        if "/compare/" in url:
            pair = url.rsplit("/compare/", 1)[1].split("?", 1)[0]
            status = self.statuses.get(pair)
            if status is None:
                return 404, b"{}"
            return 200, json.dumps({"status": status}).encode()
        return 404, b"{}"


def port_with(pem, fake):  # noqa: F811
    from culture_rules.node.actions.github import GitHubPrHeadPort

    store = MemoryStore()
    store.put("actors", actor_doc())
    return GitHubPrHeadPort(store, transport=fake, secrets=lambda ref: pem)


def test_the_base_tip_is_read_from_the_base_branch_when_asked(pem):  # noqa: F811
    fake = BranchFake()
    port = port_with(pem, fake)
    res = port.invoke(
        {"repo": "acme/widgets", "number": 3, "with_base_tip": True}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["base_sha"] == PULL_BASE
    assert res.output["base_tip_sha"] == TIP
    assert any(c.endswith("/repos/acme/widgets/branches/main") for c in fake.calls)


def test_no_base_tip_is_read_unless_asked(pem):  # noqa: F811
    fake = BranchFake()
    res = port_with(pem, fake).invoke(
        {"repo": "acme/widgets", "number": 3}, "k", DEADLINE, context=ctx()
    )
    assert "base_tip_sha" not in res.output
    assert not any("/branches/" in c for c in fake.calls)


def test_an_unreadable_base_tip_is_left_out_not_a_failure(pem):  # noqa: F811
    port = port_with(pem, BranchFake(branch_status=502))
    res = port.invoke(
        {"repo": "acme/widgets", "number": 3, "with_base_tip": True}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["base_tip_sha"] is None


def test_a_base_commit_between_the_prs_base_and_the_tip_is_on_the_branch(pem):  # noqa: F811
    statuses = {f"{PULL_BASE}...{MID}": "ahead", f"{MID}...main": "ahead"}
    port = port_with(pem, BranchFake(statuses))
    res = port.invoke(
        {"repo": "acme/widgets", "number": 3, "base_sha": MID}, "k", DEADLINE, context=ctx()
    )
    assert res.output["base_on_branch"] is True


def test_the_tip_itself_is_on_the_branch(pem):  # noqa: F811
    statuses = {f"{PULL_BASE}...{TIP}": "ahead", f"{TIP}...main": "identical"}
    res = port_with(pem, BranchFake(statuses)).invoke(
        {"repo": "acme/widgets", "number": 3, "base_sha": TIP}, "k", DEADLINE, context=ctx()
    )
    assert res.output["base_on_branch"] is True


def test_the_prs_own_base_needs_no_compare(pem):  # noqa: F811
    fake = BranchFake()
    res = port_with(pem, fake).invoke(
        {"repo": "acme/widgets", "number": 3, "base_sha": PULL_BASE}, "k", DEADLINE, context=ctx()
    )
    assert res.output["base_on_branch"] is True
    assert not any("/compare/" in c for c in fake.calls)


def test_a_commit_older_than_the_prs_base_is_not_on_the_branch(pem):  # noqa: F811
    old = "e" * 40
    statuses = {f"{PULL_BASE}...{old}": "behind", f"{old}...main": "ahead"}
    res = port_with(pem, BranchFake(statuses)).invoke(
        {"repo": "acme/widgets", "number": 3, "base_sha": old}, "k", DEADLINE, context=ctx()
    )
    assert res.output["base_on_branch"] is False


def test_a_commit_off_the_branch_is_not_on_the_branch(pem):  # noqa: F811
    side = "f" * 40
    statuses = {f"{PULL_BASE}...{side}": "ahead", f"{side}...main": "diverged"}
    res = port_with(pem, BranchFake(statuses)).invoke(
        {"repo": "acme/widgets", "number": 3, "base_sha": side}, "k", DEADLINE, context=ctx()
    )
    assert res.output["base_on_branch"] is False


def test_a_compare_that_fails_leaves_the_answer_unknown(pem):  # noqa: F811
    res = port_with(pem, BranchFake({})).invoke(
        {"repo": "acme/widgets", "number": 3, "base_sha": MID}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["base_on_branch"] is None


def test_a_malformed_base_to_check_is_bad_input(pem):  # noqa: F811
    res = port_with(pem, BranchFake()).invoke(
        {"repo": "acme/widgets", "number": 3, "base_sha": "main"}, "k", DEADLINE, context=ctx()
    )
    assert (res.outcome, res.error) == ("failed", "bad_input")
