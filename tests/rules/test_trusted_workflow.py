"""d20 round 2: only a workflow whose definition digest is pinned in code may push.

This test recomputes the digest from the shipped file. If you changed
docs/rules/pr-fixer/workflows/pr-fixer.json on purpose, add the digest it prints to
culture_rules/actors/trusted.py (see that module for the rollout order).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from culture_rules.actors.trusted import (
    TRUSTED_WORKFLOW_DIGESTS,
    workflow_digest,
    workflow_refusal,
)
from tests.rules.test_pr_fixer_bundle import workflow_doc


def test_the_shipped_pr_fixer_workflow_is_trusted():
    digest = workflow_digest(workflow_doc())
    assert digest in TRUSTED_WORKFLOW_DIGESTS, (
        f"docs/rules/pr-fixer/workflows/pr-fixer.json now hashes to {digest}: if the change "
        "is intended, add it to TRUSTED_WORKFLOW_DIGESTS in culture_rules/actors/trusted.py"
    )


def test_the_set_holds_only_well_formed_digests():
    assert TRUSTED_WORKFLOW_DIGESTS
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", d) for d in TRUSTED_WORKFLOW_DIGESTS)


def test_a_version_bump_keeps_trust_any_edit_loses_it():
    wf = workflow_doc()
    assert workflow_digest({**wf, "version": 9}) == workflow_digest(wf)
    edited = copy.deepcopy(wf)
    fix = next(s for s in edited["steps"] if s["id"] == "fix")
    next(b for b in fix["body"] if b["id"] == "gate")["placement"] = {
        "actor": "x",
        "machine": None,
        "requirement": None,
    }
    assert workflow_digest(edited) not in TRUSTED_WORKFLOW_DIGESTS
    for change in ({"description": "tweaked"}, {"steps": wf["steps"][:-1]}):
        assert workflow_digest({**wf, **change}) not in TRUSTED_WORKFLOW_DIGESTS


def test_workflow_refusal_reads_the_pinned_definition():
    wf = workflow_doc()
    assert workflow_refusal({"workflow": {"definition": wf, "digest": "sha256:forged"}}) is None
    edited = {**wf, "description": "x"}
    run = {
        "workflow": {"definition": edited, "digest": next(iter(TRUSTED_WORKFLOW_DIGESTS))}
    }  # a forged stored digest
    assert workflow_refusal(run) == "workflow_not_trusted"
    for run in (None, {}, {"workflow": None}, {"workflow": {"definition": "nope"}}):
        assert workflow_refusal(run) == "workflow_not_trusted"


# --------------------------------------------------------------------------- actors (round 3)


def _reviewer() -> dict:
    from tests.rules.test_pr_fixer_bundle import reviewer_actor

    return reviewer_actor()


def test_the_shipped_reviewer_actor_is_trusted():
    from culture_rules.actors.trusted import TRUSTED_ACTOR_DIGESTS, actor_digest

    digest = actor_digest(_reviewer())
    assert digest in TRUSTED_ACTOR_DIGESTS["codex-reviewer"], (
        f"docs/rules/pr-fixer/actors/codex-reviewer.json now hashes to {digest}: if intended, "
        "add it to TRUSTED_ACTOR_DIGESTS['codex-reviewer'] in culture_rules/actors/trusted.py"
    )


def test_cosmetic_actor_fields_keep_trust_security_fields_lose_it():
    from culture_rules.actors.trusted import actor_digest

    doc = _reviewer()
    base = actor_digest(doc)
    for cosmetic in ({"name": "x"}, {"description": "y"}):
        assert actor_digest({**doc, **cosmetic}) == base
    assert actor_digest({**doc, "params": {**doc["params"], "max_concurrency": 4}}) == base
    for key, value in (
        ("bridge_url", "http://127.0.0.1:9"),
        ("sandbox", "workspace-write"),
        ("locked_instruction", None),
        ("reviewer", False),
        ("max_bound_input_chars", None),
        ("model", "o3"),
    ):
        assert actor_digest({**doc, "params": {**doc["params"], key: value}}) != base, key
    assert actor_digest({**doc, "harness": "qwen"}) != base


def test_app_digests_cover_identity_key_and_author_but_not_the_repo_scope():
    from culture_rules.actors.trusted import actor_digest

    app = {
        "id": "github-app",
        "kind": "app",
        "machine": "spark2",
        "params": {
            "surface": "github",
            "commit_author": "bot",
            "connection": {
                "app_id": "1",
                "installation_id": "2",
                "private_key": "grant:K",
                "webhook_secret": "grant:W",
                "repos": ["o/a", "o/b"],
            },
        },
    }
    base = actor_digest(app)
    conn = app["params"]["connection"]
    # which repos the App reaches is scope (guildmaster adds repos at provisioning, d18):
    # adding one never needs a release
    for repos in (["o/a", "o/b", "o/c"], ["o/a"], []):
        edited = {**app, "params": {**app["params"], "connection": {**conn, "repos": repos}}}
        assert actor_digest(edited) == base, repos
    for change in ({"app_id": "9"}, {"installation_id": "8"}, {"private_key": "grant:X"}):
        edited = {**app, "params": {**app["params"], "connection": {**conn, **change}}}
        assert actor_digest(edited) != base, change
    assert actor_digest({**app, "params": {**app["params"], "commit_author": None}}) != base
    assert actor_digest({**app, "params": {**app["params"], "commit_author": "other"}}) != base


LIVE_APP = Path(__file__).parent / "fixtures" / "github-app.live.json"


def test_the_pinned_github_app_digest_is_the_live_actors():
    from culture_rules.actors.trusted import TRUSTED_ACTOR_DIGESTS, actor_digest

    live = json.loads(LIVE_APP.read_text())
    assert TRUSTED_ACTOR_DIGESTS["github-app"] == frozenset({actor_digest(live)})
    # a new repo for the App (guildmaster at provisioning) keeps it trusted
    more = copy.deepcopy(live)
    more["params"]["connection"]["repos"].append("agentculture/brand-new-repo")
    assert actor_digest(more) in TRUSTED_ACTOR_DIGESTS["github-app"]


def test_the_live_app_fixture_holds_references_not_secrets():
    live = json.loads(LIVE_APP.read_text())
    conn = live["params"]["connection"]
    for key in ("private_key", "webhook_secret"):
        assert conn[key].startswith("grant:"), key
    assert "commit_author" not in live["params"]  # see the ops doc: proposed, not set


def test_an_unknown_or_changed_app_actor_is_not_trusted():
    from culture_rules.actors.trusted import actor_refusal
    from culture_rules.store.memory import MemoryStore

    store = MemoryStore()
    live = json.loads(LIVE_APP.read_text())
    store.put("actors", live)
    assert actor_refusal(store, "github-app")[0] is None
    other = copy.deepcopy(live)
    other["params"]["connection"]["app_id"] = "1"
    store.put("actors", other)
    assert actor_refusal(store, "github-app")[0] == "actor_not_trusted"
    assert actor_refusal(store, "nobody")[0] == "actor_not_trusted"


def test_the_digest_helper_prints_what_the_checks_compute(tmp_path, capsys):
    import json

    from culture_rules.actors.trusted import actor_digest, main

    path = tmp_path / "a.json"
    path.write_text(json.dumps(_reviewer()))
    assert main(["actor", str(path)]) == 0
    assert capsys.readouterr().out.strip() == actor_digest(_reviewer())
    assert main(["nope"]) == 1
