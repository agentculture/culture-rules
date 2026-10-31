"""github.merge: squash-merge as the App at the prompted sha, fenced to a click carrying the
button's one-time code (d3), with an optional same-repo branch delete (fake transport, memory
store)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from culture_rules.actors import trusted  # noqa: E402
from culture_rules.apps import discord_buttons as db  # noqa: E402
from culture_rules.engine.actorport import InvocationContext  # noqa: E402
from culture_rules.model.discord_filters import BUTTON_CLICKED  # noqa: E402
from culture_rules.node.actions.github_merge import (  # noqa: E402
    MAX_RESENDS,
    MERGE_ONCE_COLLECTION,
    STALE_CLAIM_S,
    GitHubMergePort,
)
from culture_rules.node.firing import run_id_for  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

FAKE_BEARER = "ghs" + "_" + "FAKEINSTALLATIONTOKEN0123456789abcdefABCD"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
NOW = datetime.now(UTC)  # the port reads the code's expiry on real time
SHA = "a" * 40
MERGED_SHA = "b" * 40
REPO = "acme/widgets"
RULE = "merge-click"
EVENT = "discord:discord-bot:interaction:77"
WORKFLOW = {
    "id": "merge",
    "name": "merge",
    "steps": [
        {
            "id": "merge",
            "kind": "action",
            "action": {
                "kind": "github.merge",
                "params": {"actor": "gh-app", "repo": REPO, "number": 3, "sha": SHA},
            },
        }
    ],
}


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(autouse=True)
def pin_test_app(monkeypatch):
    """Pin the test App actor's digest, as a release pins ``github-app``'s."""
    pinned = {
        **trusted.TRUSTED_ACTOR_DIGESTS,
        "gh-app": frozenset({trusted.actor_digest(actor_doc())}),
    }
    monkeypatch.setattr(trusted, "TRUSTED_ACTOR_DIGESTS", pinned)


class Fake:
    """GitHub: the token exchange, the PR read, the merge and the ref delete."""

    def __init__(
        self,
        *,
        head_sha=SHA,
        state="open",
        merged=False,
        head_repo=REPO,
        merge_status=200,
        merge_body=None,
        delete_status=204,
        merge_raises=None,
    ):
        self.calls = []
        self.bodies = []
        self.head_sha, self.state, self.merged = head_sha, state, merged
        self.head_repo = head_repo
        self.merge_status, self.delete_status = merge_status, delete_status
        self.merge_body = merge_body or {"sha": MERGED_SHA, "merged": True, "message": "ok"}
        self.merge_raises = merge_raises

    def methods(self, method, suffix):
        return [u for (m, u) in self.calls if m == method and u.endswith(suffix)]

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        self.bodies.append(json.loads(body) if body else None)
        if url.endswith("/access_tokens"):
            exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return 201, json.dumps({"token": FAKE_BEARER, "expires_at": exp}).encode()
        if method == "PUT" and url.endswith("/merge"):
            if self.merge_raises is not None:
                raise self.merge_raises
            if self.merge_status == 200:
                self.merged, self.state = True, "closed"
            return self.merge_status, json.dumps(self.merge_body).encode()
        if method == "DELETE":
            return self.delete_status, b"" if self.delete_status == 204 else b"{}"
        if method == "GET" and "/pulls/" in url:
            pull = {
                "state": self.state,
                "merged": self.merged,
                "merge_commit_sha": MERGED_SHA if self.merged else None,
                "head": {
                    "sha": self.head_sha,
                    "ref": "fix/x",
                    "repo": {"full_name": self.head_repo},
                },
                "base": {"ref": "main", "repo": {"full_name": REPO}},
            }
            return 200, json.dumps(pull).encode()
        return 404, b"{}"


def actor_doc():
    return {
        "id": "gh-app",
        "name": "gh",
        "kind": "app",
        "params": {
            "surface": "github",
            "connection": {
                "app_id": "1",
                "installation_id": "2",
                "private_key": "grant:GH_KEY",
                "webhook_secret": "grant:GH_HOOK",
                "repos": [REPO],
            },
        },
        "schema_version": "1.0",
    }


CUSTOM_ID = "merge:tok"
VALUE = {"repo": REPO, "number": 3, "sha": SHA}


def issue_code(store, value=VALUE, custom_id=CUSTOM_ID):
    """The code the prompt's discord.message stores for its button (d3)."""
    db.remember_buttons(store, "discord-bot", "4242", [(custom_id, value, True)], now=NOW)
    return store.get(db.BUTTONS_COLLECTION, f"discord-bot:{custom_id}")["code"]


def click_envelope(event_type=BUTTON_CLICKED, code=None, custom_id=CUSTOM_ID):
    return {
        "id": EVENT,
        "type": event_type,
        "source": "discord",
        "data": {"custom_id": custom_id, "author_id": "42", "code": code},
    }


def world(pem, fake, *, envelope=None, stored_event=True, rule_enabled=True, run=None, code=True):
    """A store with the App actor, the click rule, the clicked button's code, the click event
    (carrying the code) and the run it started."""
    store = MemoryStore()
    store.put("actors", actor_doc())
    store.put("rules", {"id": RULE, "name": RULE, "enabled": rule_enabled})
    issued = issue_code(store) if code else None
    envelope = envelope or click_envelope(code=issued)
    if stored_event:
        store.put("events", {"id": envelope["id"], "envelope": envelope})
    doc = run or {
        "id": run_id_for(RULE, envelope["id"]),
        "rule_id": RULE,
        "workflow_id": "merge",
        "trigger": envelope,
        "workflow": {"id": "merge", "definition": WORKFLOW},
    }
    store.put("runs", doc)
    resolved = []

    def secrets(ref):
        resolved.append(ref)
        return pem

    port = GitHubMergePort(store, transport=fake, secrets=secrets)
    return port, store, resolved, doc["id"]


def merge(port, run_id, **extra):
    params = {"actor": "gh-app", "repo": REPO, "number": 3, "sha": SHA, **extra}
    ctx = InvocationContext(run_id=run_id, step_id="merge", kind="action", host="h", actor="gh-app")
    return port.invoke(params, "k", DEADLINE, context=ctx)


# --------------------------------------------------------------------------- the call


def test_the_port_takes_no_idempotency_key():
    assert GitHubMergePort(MemoryStore()).supports_idempotency_key is False


def test_the_merge_is_a_squash_put_at_the_prompted_sha(pem):
    fake = Fake()
    port, _, resolved, run_id = world(pem, fake)
    res = merge(port, run_id)
    assert res.outcome == "completed"
    puts = [i for i, (m, _) in enumerate(fake.calls) if m == "PUT"]
    assert len(puts) == 1
    assert fake.calls[puts[0]][1].endswith(f"/repos/{REPO}/pulls/3/merge")
    assert fake.bodies[puts[0]] == {"sha": SHA, "merge_method": "squash"}
    assert resolved == ["grant:GH_KEY"]


def test_a_merge_reports_merged_the_merge_commit_and_no_delete(pem):
    port, _, _, run_id = world(pem, Fake())
    out = dict(merge(port, run_id).output)
    assert out["merged"] is True
    assert out["sha"] == MERGED_SHA
    assert out["branch_deleted"] is False


def test_the_pr_is_read_before_the_merge(pem):
    fake = Fake()
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id)
    kinds = [m for (m, u) in fake.calls if not u.endswith("/access_tokens")]
    assert kinds == ["GET", "PUT"]


@pytest.mark.parametrize("status", [409, 405])
def test_a_moved_head_or_unmergeable_pr_fails_without_retry(pem, status):
    port, _, _, run_id = world(pem, Fake(merge_status=status))
    res = merge(port, run_id)
    assert res.outcome == "failed"
    assert res.error == f"http_{status}"
    assert res.retryable is False


@pytest.mark.parametrize("status", [409, 405, 422])
def test_a_refused_merge_releases_its_claim(pem, status):
    port, store, _, run_id = world(pem, Fake(merge_status=status))
    merge(port, run_id)
    assert list(store.find(MERGE_ONCE_COLLECTION)) == []


def test_a_head_that_moved_before_the_put_is_refused_without_a_merge(pem):
    fake = Fake(head_sha="c" * 40)
    port, store, _, run_id = world(pem, fake)
    res = merge(port, run_id)
    assert (res.error, res.retryable) == ("head_moved", False)
    assert fake.methods("PUT", "/merge") == []
    assert list(store.find(MERGE_ONCE_COLLECTION)) == []


def test_a_closed_pr_is_refused_without_a_merge(pem):
    fake = Fake(state="closed")
    port, _, _, run_id = world(pem, fake)
    res = merge(port, run_id)
    assert (res.error, res.retryable) == ("pr_not_open", False)
    assert fake.methods("PUT", "/merge") == []


# --------------------------------------------------------------------------- once claim


@pytest.mark.parametrize("status", [500, 502, 408])
def test_an_unknown_outcome_is_sent_again_only_after_a_read_shows_it_did_not_merge(pem, status):
    fake = Fake(merge_status=status)
    port, store, _, run_id = world(pem, fake)
    first = merge(port, run_id)
    assert first.outcome == "failed"
    [claim] = store.find(MERGE_ONCE_COLLECTION)
    assert claim["state"] == "unknown"
    fake.merge_status = 200  # the PR is still open at SHA: the merge did not happen
    second = merge(port, run_id)
    assert len(fake.methods("PUT", "/merge")) == 2
    assert second.outcome == "completed"
    assert second.output["merged"] is True
    [claim] = store.find(MERGE_ONCE_COLLECTION)
    assert (claim["state"], claim["resends"]) == ("merged", 1)


def test_re_sends_are_bounded(pem):
    fake = Fake(merge_status=502)
    port, _, _, run_id = world(pem, fake)
    results = [merge(port, run_id) for _ in range(MAX_RESENDS + 2)]
    assert len(fake.methods("PUT", "/merge")) == 1 + MAX_RESENDS
    assert (results[-1].error, results[-1].retryable) == ("merge_outcome_unknown", False)


def test_a_claim_still_in_flight_is_never_sent_again(pem):
    fake = Fake()
    port, store, _, run_id = world(pem, fake)
    store.insert(MERGE_ONCE_COLLECTION, _claim("claimed", datetime.now(UTC)))
    res = merge(port, run_id)
    assert fake.methods("PUT", "/merge") == []
    assert (res.error, res.retryable) == ("merge_outcome_unknown", False)


def test_a_stale_claim_is_sent_again(pem):
    fake = Fake()
    port, store, _, run_id = world(pem, fake)
    stale = datetime.now(UTC) - timedelta(seconds=STALE_CLAIM_S + 1)
    store.insert(MERGE_ONCE_COLLECTION, _claim("claimed", stale))
    res = merge(port, run_id)
    assert len(fake.methods("PUT", "/merge")) == 1
    assert res.outcome == "completed"


def test_an_unknown_claim_on_a_moved_head_is_released_without_a_merge(pem):
    fake = Fake(head_sha="c" * 40)
    port, store, _, run_id = world(pem, fake)
    store.insert(MERGE_ONCE_COLLECTION, _claim("unknown", datetime.now(UTC)))
    res = merge(port, run_id)
    assert fake.methods("PUT", "/merge") == []
    assert (res.error, res.retryable) == ("head_moved", False)
    assert store.find(MERGE_ONCE_COLLECTION) == []


def _claim(state, at):
    claim_id = hashlib.sha256(f"{REPO.lower()}#3#{SHA}".encode()).hexdigest()
    doc = {"id": claim_id, "repo": REPO, "number": 3, "sha": SHA, "state": state}
    return {**doc, "claimed_at": at.isoformat(), "resends": 0}


def test_a_network_error_on_the_put_keeps_the_claim(pem):
    fake = Fake(merge_raises=OSError("reset"))
    port, store, _, run_id = world(pem, fake)
    assert merge(port, run_id).error == "network_error"
    [claim] = store.find(MERGE_ONCE_COLLECTION)
    assert claim["state"] == "unknown"


def test_an_unknown_outcome_that_did_merge_is_reconciled_by_a_read(pem):
    fake = Fake(merge_status=502)
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id)
    fake.merged, fake.state = True, "closed"  # GitHub did merge before the 502
    res = merge(port, run_id)
    assert len(fake.methods("PUT", "/merge")) == 1
    assert res.outcome == "completed"
    assert res.output["merged"] is True
    assert res.output["sha"] == MERGED_SHA


def test_a_merged_claim_completes_again_without_a_call(pem):
    fake = Fake()
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id)
    before = len(fake.calls)
    res = merge(port, run_id)
    assert res.outcome == "completed"
    assert res.output["skipped"] == "merged_before"
    assert len(fake.calls) == before


def test_the_claim_is_keyed_on_repo_number_and_sha(pem):
    port, store, _, run_id = world(pem, Fake())
    merge(port, run_id)
    [claim] = store.find(MERGE_ONCE_COLLECTION)
    assert (claim["repo"], claim["number"], claim["sha"]) == (REPO, 3, SHA)


# --------------------------------------------------------------------------- branch delete


def test_delete_branch_deletes_the_same_repo_head_after_a_merge(pem):
    fake = Fake()
    port, _, _, run_id = world(pem, fake)
    res = merge(port, run_id, delete_branch=True)
    assert fake.methods("DELETE", f"/repos/{REPO}/git/refs/heads/fix/x")
    assert res.output["branch_deleted"] is True


def test_delete_branch_leaves_a_fork_head_alone(pem):
    fake = Fake(head_repo="someone/widgets")
    port, _, _, run_id = world(pem, fake)
    res = merge(port, run_id, delete_branch=True)
    assert res.output["merged"] is True
    assert [m for (m, _) in fake.calls if m == "DELETE"] == []
    assert res.output["branch_deleted"] is False


def test_no_delete_after_a_refused_merge(pem):
    fake = Fake(merge_status=409)
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id, delete_branch=True)
    assert [m for (m, _) in fake.calls if m == "DELETE"] == []


def test_no_delete_without_delete_branch(pem):
    fake = Fake()
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id)
    assert [m for (m, _) in fake.calls if m == "DELETE"] == []


@pytest.mark.parametrize("status", [404, 422])
def test_a_branch_already_gone_is_not_a_merge_failure(pem, status):
    port, _, _, run_id = world(pem, Fake(delete_status=status))
    res = merge(port, run_id, delete_branch=True)
    assert res.outcome == "completed"
    assert res.output["branch_deleted"] is False
    assert res.output["branch_delete_error"] == f"http_{status}"


# --------------------------------------------------------------------------- the fence


def _nothing_touched(fake, resolved):
    return fake.calls == [] and resolved == []


def test_a_run_not_started_by_a_click_is_refused_before_any_secret_or_call(pem):
    fake = Fake()
    envelope = click_envelope("github.pr.opened")
    port, _, resolved, run_id = world(pem, fake, envelope=envelope)
    res = merge(port, run_id)
    assert (res.error, res.retryable) == ("not_started_by_click", False)
    assert _nothing_touched(fake, resolved)


def test_a_click_trigger_without_its_stored_event_is_refused(pem):
    fake = Fake()
    port, _, resolved, run_id = world(pem, fake, stored_event=False)
    res = merge(port, run_id)
    assert res.error == "not_started_by_click"
    assert _nothing_touched(fake, resolved)


def test_a_stored_event_of_another_type_is_refused(pem):
    fake = Fake()
    port, store, resolved, run_id = world(pem, fake)
    store.put("events", {"id": EVENT, "envelope": click_envelope("github.pr.opened")})
    res = merge(port, run_id)
    assert res.error == "not_started_by_click"
    assert _nothing_touched(fake, resolved)


def test_a_run_not_started_by_its_rules_firing_is_refused(pem):
    fake = Fake()
    run = {
        "id": "run-by-hand",
        "rule_id": RULE,
        "workflow_id": "merge",
        "trigger": click_envelope(),
        "workflow": {"id": "merge", "definition": WORKFLOW},
    }
    port, _, resolved, run_id = world(pem, fake, run=run)
    res = merge(port, run_id)
    assert res.error == "not_started_by_click"
    assert _nothing_touched(fake, resolved)


def test_a_direct_workflow_run_is_refused(pem):
    fake = Fake()
    run = {
        "id": "run-direct",
        "rule_id": "adhoc:merge",
        "workflow_id": "merge",
        "trigger": {"kind": "manual"},
        "workflow": {"id": "merge", "definition": WORKFLOW},
    }
    port, store, resolved, run_id = world(pem, fake, run=run)
    store.put("workflows", {"id": "merge", "name": "merge", "enabled": True})
    res = merge(port, run_id)
    assert res.error == "not_started_by_click"
    assert _nothing_touched(fake, resolved)


def test_a_disabled_source_rule_is_refused_before_any_secret_or_call(pem):
    fake = Fake()
    port, _, resolved, run_id = world(pem, fake, rule_enabled=False)
    res = merge(port, run_id)
    assert (res.error, res.retryable) == ("rule_disabled", False)
    assert _nothing_touched(fake, resolved)


def test_an_unknown_run_is_refused(pem):
    fake = Fake()
    port, _, resolved, _ = world(pem, fake)
    res = merge(port, "run-missing")
    assert res.error == "run_not_found"
    assert _nothing_touched(fake, resolved)


def test_the_fence_codes_are_distinct():
    from culture_rules.node.actions import github_merge

    codes = {
        github_merge.NOT_CLICK,
        db.CODE_NOT_FOUND,
        db.CODE_MISMATCH,
        db.CODE_USED,
        "rule_disabled",
        github_merge.NOT_APP,
    }
    assert len(codes) == 6


# --------------------------------------------------------------------------- the code (d3)


def _stored_click(store, code, custom_id=CUSTOM_ID):
    envelope = click_envelope(code=code, custom_id=custom_id)
    store.put("events", {"id": envelope["id"], "envelope": envelope})


def _code_doc(store):
    (doc,) = store.find(db.CODES_COLLECTION)
    return doc


def test_a_click_with_its_buttons_code_merges(pem):
    port, _, _, run_id = world(pem, Fake())
    assert merge(port, run_id).outcome == "completed"


def test_the_merge_uses_up_the_code(pem):
    port, store, _, run_id = world(pem, Fake())
    merge(port, run_id)
    assert _code_doc(store)["used_by"] == run_id


def test_no_workflow_digest_is_needed(pem):
    port, _, _, run_id = world(pem, Fake())
    assert all("merge" not in role for role in trusted.TRUSTED_WORKFLOWS_V2)
    assert merge(port, run_id).outcome == "completed"


def test_a_click_without_a_code_is_refused_before_any_secret_or_call(pem):
    fake = Fake()
    port, _, resolved, run_id = world(pem, fake, code=False)
    res = merge(port, run_id)
    assert (res.error, res.retryable) == (db.CODE_NOT_FOUND, False)
    assert _nothing_touched(fake, resolved)


def test_a_forged_code_is_refused(pem):
    fake = Fake()
    port, store, resolved, run_id = world(pem, fake)
    _stored_click(store, "forged-code")
    assert merge(port, run_id).error == db.CODE_NOT_FOUND
    assert _nothing_touched(fake, resolved)


def test_the_code_is_read_from_the_stored_event_never_from_the_run(pem):
    fake = Fake()
    port, store, resolved, run_id = world(pem, fake)
    code = _code_doc(store)["id"]
    _stored_click(store, None)  # the run's own trigger copy still holds the real code
    assert store.get("runs", run_id)["trigger"]["data"]["code"] == code
    assert merge(port, run_id).error == db.CODE_NOT_FOUND
    assert _nothing_touched(fake, resolved)


def test_a_code_of_another_button_is_refused(pem):
    fake = Fake()
    port, store, resolved, run_id = world(pem, fake)
    _stored_click(store, _code_doc(store)["id"], custom_id="merge:other")
    assert merge(port, run_id).error == db.CODE_MISMATCH
    assert _nothing_touched(fake, resolved)


@pytest.mark.parametrize("extra", [{"number": 4}, {"sha": "c" * 40}])
def test_a_code_for_another_pr_or_commit_is_refused(pem, extra):
    fake = Fake()
    port, _, resolved, run_id = world(pem, fake)
    assert merge(port, run_id, **extra).error == db.CODE_MISMATCH
    assert _nothing_touched(fake, resolved)


def test_a_code_another_run_used_is_refused(pem):
    fake = Fake()
    port, store, resolved, run_id = world(pem, fake)
    code = _code_doc(store)["id"]
    store.put(db.CODES_COLLECTION, {**_code_doc(store), "used_at": NOW, "used_by": "run-other"})
    assert code
    assert merge(port, run_id).error == db.CODE_USED
    assert _nothing_touched(fake, resolved)


def test_a_refusal_before_the_code_leaves_it_unused(pem):
    port, store, _, run_id = world(pem, Fake(), rule_enabled=False)
    assert merge(port, run_id).error == "rule_disabled"
    assert _code_doc(store)["used_at"] is None


# --------------------------------------------------------------------------- input and actor


def test_an_unlisted_repo_is_refused_before_any_secret_or_call(pem):
    fake = Fake()
    port, _, resolved, run_id = world(pem, fake)
    res = merge(port, run_id, repo="evil/repo")
    assert res.error == "repo_not_allowed"
    assert _nothing_touched(fake, resolved)


@pytest.mark.parametrize(
    "extra",
    [
        {"sha": "abc"},
        {"sha": None},
        {"number": "x"},
        {"delete_branch": "yes"},
        {"number": 3.7},
        {"number": 3.0},
        {"number": True},
        {"number": "3"},
        {"number": " 3"},
        {"number": "3x"},
        {"number": 0},
        {"number": -3},
        {"number": None},
    ],
)
def test_bad_input_is_refused_before_any_secret_or_call(pem, extra):
    fake = Fake()
    port, _, resolved, run_id = world(pem, fake)
    res = merge(port, run_id, **extra)
    assert (res.error, res.retryable) == ("bad_input", False)
    assert _nothing_touched(fake, resolved)


def test_a_missing_actor_is_actor_not_found(pem):
    fake = Fake()
    port, store, resolved, run_id = world(pem, fake)
    store.delete("actors", "gh-app")
    assert merge(port, run_id).error == "actor_not_found"
    assert _nothing_touched(fake, resolved)


def test_a_secret_failure_is_secret_unavailable(pem):
    fake = Fake()
    port, store, _, run_id = world(pem, fake)

    def boom(ref):
        raise RuntimeError("no grant")

    port._secrets = boom
    assert merge(port, run_id).error == "secret_unavailable"
    assert fake.calls == []


def test_the_token_never_reaches_the_logs(pem, caplog):
    caplog.set_level(logging.DEBUG)
    port, _, _, run_id = world(pem, Fake())
    merge(port, run_id, delete_branch=True)
    assert FAKE_BEARER not in caplog.text


def test_a_repeat_after_a_merge_reports_the_merge_commit(pem):
    port, _, _, run_id = world(pem, Fake())
    merge(port, run_id, delete_branch=True)
    again = merge(port, run_id, delete_branch=True)
    assert again.output["sha"] == MERGED_SHA
    assert again.output["branch_deleted"] is True


# --------------------------------------------------------------------------- delete on a repeat


def _deletes(fake):
    return [u for (m, u) in fake.calls if m == "DELETE"]


def test_a_merged_before_repeat_deletes_a_branch_not_yet_deleted(pem):
    fake = Fake()
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id)  # merged, no delete asked
    again = merge(port, run_id, delete_branch=True)
    assert again.output["skipped"] == "merged_before"
    assert _deletes(fake) == [f"https://api.github.com/repos/{REPO}/git/refs/heads/fix/x"]


def test_a_merged_before_repeat_reports_the_delete(pem):
    port, _, _, run_id = world(pem, Fake())
    merge(port, run_id)
    again = merge(port, run_id, delete_branch=True)
    assert again.output["branch_deleted"] is True


def test_a_merged_before_repeat_records_the_delete_on_the_claim(pem):
    port, store, _, run_id = world(pem, Fake())
    merge(port, run_id)
    merge(port, run_id, delete_branch=True)
    [claim] = store.find(MERGE_ONCE_COLLECTION)
    assert claim["branch_deleted"] is True


def test_a_merged_before_repeat_never_deletes_twice(pem):
    fake = Fake()
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id, delete_branch=True)
    merge(port, run_id, delete_branch=True)
    assert len(_deletes(fake)) == 1


def test_a_merged_before_repeat_does_not_retry_a_recorded_delete_error(pem):
    fake = Fake(delete_status=422)
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id, delete_branch=True)
    again = merge(port, run_id, delete_branch=True)
    assert len(_deletes(fake)) == 1
    assert again.output["branch_delete_error"] == "http_422"


def test_a_merged_before_repeat_without_delete_branch_deletes_nothing(pem):
    fake = Fake()
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id)
    merge(port, run_id)
    assert _deletes(fake) == []


def test_a_merged_unconfirmed_outcome_deletes_the_branch(pem):
    fake = Fake(merge_status=502)
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id, delete_branch=True)
    fake.merged, fake.state = True, "closed"  # GitHub did merge before the 502
    res = merge(port, run_id, delete_branch=True)
    assert res.output["skipped"] == "merged_unconfirmed"
    assert len(_deletes(fake)) == 1


def test_a_merged_unconfirmed_outcome_reports_the_delete(pem):
    fake = Fake(merge_status=502)
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id, delete_branch=True)
    fake.merged, fake.state = True, "closed"
    res = merge(port, run_id, delete_branch=True)
    assert res.output["branch_deleted"] is True


def test_a_merged_unconfirmed_outcome_records_the_delete_on_the_claim(pem):
    fake = Fake(merge_status=502)
    port, store, _, run_id = world(pem, fake)
    merge(port, run_id, delete_branch=True)
    fake.merged, fake.state = True, "closed"
    merge(port, run_id, delete_branch=True)
    [claim] = store.find(MERGE_ONCE_COLLECTION)
    assert claim["branch_deleted"] is True


def test_a_merged_unconfirmed_outcome_leaves_a_fork_head_alone(pem):
    fake = Fake(merge_status=502, head_repo="someone/widgets")
    port, _, _, run_id = world(pem, fake)
    merge(port, run_id, delete_branch=True)
    fake.merged, fake.state = True, "closed"
    res = merge(port, run_id, delete_branch=True)
    assert _deletes(fake) == []
    assert res.output["branch_delete_error"] == "not_same_repo_pr"


# --------------------------------------------------------------------------- the pinned App


def test_an_app_actor_with_no_pinned_digest_is_refused_before_any_secret_or_call(pem, monkeypatch):
    unpinned = {k: v for k, v in trusted.TRUSTED_ACTOR_DIGESTS.items() if k != "gh-app"}
    monkeypatch.setattr(trusted, "TRUSTED_ACTOR_DIGESTS", unpinned)
    fake = Fake()
    port, _, resolved, run_id = world(pem, fake)
    res = merge(port, run_id)
    assert (res.error, res.retryable) == ("actor_not_trusted", False)
    assert _nothing_touched(fake, resolved)


def test_an_app_actor_whose_security_fields_changed_is_refused_before_any_secret_or_call(
    pem,
):
    fake = Fake()
    port, store, resolved, run_id = world(pem, fake)
    doc = actor_doc()
    doc["params"]["connection"]["private_key"] = "grant:OTHER_KEY"
    store.put("actors", doc)
    res = merge(port, run_id)
    assert (res.error, res.retryable) == ("actor_not_trusted", False)
    assert _nothing_touched(fake, resolved)


def test_an_actor_that_is_not_an_app_is_refused_before_any_secret_or_call(pem, monkeypatch):
    doc = actor_doc()
    doc["kind"] = "service"  # its digest leaves the connection out: it must never merge
    pinned = {**trusted.TRUSTED_ACTOR_DIGESTS, "gh-app": frozenset({trusted.actor_digest(doc)})}
    monkeypatch.setattr(trusted, "TRUSTED_ACTOR_DIGESTS", pinned)
    fake = Fake()
    port, store, resolved, run_id = world(pem, fake)
    store.put("actors", doc)
    res = merge(port, run_id)
    assert (res.error, res.retryable) == ("actor_not_app", False)
    assert _nothing_touched(fake, resolved)


def test_a_new_repo_for_the_pinned_app_keeps_it_trusted(pem):
    port, store, _, run_id = world(pem, Fake())
    doc = actor_doc()
    doc["params"]["connection"]["repos"].append("acme/other")
    store.put("actors", doc)
    assert merge(port, run_id).outcome == "completed"
