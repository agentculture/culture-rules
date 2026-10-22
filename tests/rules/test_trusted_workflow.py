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
    ROLE_SINGLE,
    TRUSTED_WORKFLOWS,
    TRUSTED_WORKFLOWS_V2,
    pinned_digest,
    trusted_workflow_digests,
    workflow_digest,
    workflow_digest_v2,
    workflow_refusal,
    workflow_role,
)
from tests.rules.test_pr_fixer_single import workflow_doc

TRUSTED_WORKFLOW_DIGESTS = trusted_workflow_digests()


def test_the_single_pr_fixer_workflow_stays_trusted_in_its_role():
    """The 0.13.0 single workflow (a copy in tests/rules/fixtures/pr-fixer-single) stays
    trusted while runs pinned to it may still push (d21 rollout)."""
    digest = workflow_digest(workflow_doc())
    assert digest in TRUSTED_WORKFLOWS[ROLE_SINGLE]
    assert workflow_role({"workflow": {"definition": workflow_doc()}}) == ROLE_SINGLE


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
    from tests.rules.test_pr_fixer_single import reviewer_actor

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
AUTHORED_APP = Path(__file__).parent / "fixtures" / "github-app.with-author.json"


def test_the_pinned_github_app_digest_is_the_live_actors():
    from culture_rules.actors.trusted import TRUSTED_ACTOR_DIGESTS, actor_digest

    live = json.loads(LIVE_APP.read_text())
    authored = json.loads(AUTHORED_APP.read_text())
    assert authored["params"]["commit_author"] == "rules-culture-dev[bot]"
    assert {k: v for k, v in authored["params"].items() if k != "commit_author"} == live["params"]
    # both the live actor and its commit_author successor are trusted during the move
    assert TRUSTED_ACTOR_DIGESTS["github-app"] == frozenset(
        {actor_digest(live), actor_digest(authored)}
    )
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


def test_the_digest_helper_prints_what_the_checks_compute(capsys):
    import io
    import json

    from culture_rules.actors.trusted import actor_digest, main

    assert main(["actor"], stdin=io.StringIO(json.dumps(_reviewer()))) == 0
    assert capsys.readouterr().out.strip() == actor_digest(_reviewer())
    assert main(["nope"]) == 1
    # a path argument is refused: the helper never opens a file it is told to
    assert main(["actor", "/etc/passwd"], stdin=io.StringIO("{}")) == 1
    assert main(["actor"], stdin=io.StringIO("not json")) == 1


def test_the_digest_helper_prints_the_v2_digest_new_pins_use(capsys):
    """New pins go in TRUSTED_WORKFLOWS_V2, so the helper prints that digest too."""
    import io
    import json

    from culture_rules.actors.trusted import main, workflow_digest, workflow_digest_v2

    wf = workflow_doc()
    assert main(["workflow-v2"], stdin=io.StringIO(json.dumps(wf))) == 0
    assert capsys.readouterr().out.strip() == workflow_digest_v2(wf)
    assert main(["workflow"], stdin=io.StringIO(json.dumps(wf))) == 0
    assert capsys.readouterr().out.strip() == workflow_digest(wf)


def test_every_committed_pr_fixer_workflow_hashes_to_a_trusted_digest():
    """t12: a committed definition is trusted in its role through v2 (prose left out), and
    its pre-migration form still hashes to the historical v1 pin."""
    from culture_rules.actors.trusted import (
        ROLE_FIX,
        ROLE_PUBLISH,
        ROLE_REVIEW,
        TRUSTED_WORKFLOWS_V2,
        workflow_digest_v2,
    )
    from tests.rules.pre_migration import pre_migration

    root = Path(__file__).resolve().parents[2]
    # The queue and report-secrets workflows never push, so they are not in the trusted set.
    for name, role in (
        ("pr-fix", ROLE_FIX),
        ("review-commit", ROLE_REVIEW),
        ("publish-fix", ROLE_PUBLISH),
    ):
        path = root / "docs" / "rules" / "pr-fixer" / "workflows" / f"{name}.json"
        doc = json.loads(path.read_text())
        assert workflow_digest_v2(doc) in TRUSTED_WORKFLOWS_V2[role], name
        assert workflow_role({"workflow": {"definition": doc}}) == role, name
        if name == "pr-fix":
            # d12: the step rename moved pr-fix's v1 digest too; the v1 pin is the
            # pre-rename definition's and gets no new v1 pin (new pins are v2 only)
            continue
        assert workflow_digest(pre_migration(doc)) in TRUSTED_WORKFLOW_DIGESTS, name
    assert workflow_digest(workflow_doc()) in TRUSTED_WORKFLOW_DIGESTS


def test_notes_do_not_change_the_v1_digest():
    wf = workflow_doc()
    noted = copy.deepcopy(wf)
    noted["notes"] = "workflow level"
    fix = next(s for s in noted["steps"] if s["id"] == "fix")
    fix["notes"] = "step level"
    next(b for b in fix["body"] if b["id"] == "gate")["notes"] = "loop body step"
    assert workflow_digest(noted) == workflow_digest(wf)


# --------------------------------------------------------------------------- v2 (spec c16)

ROOT = Path(__file__).resolve().parents[2]
SINGLE_SOURCE = ROOT / "tests/rules/fixtures/pr-fixer-single/workflows/pr-fixer.json"
V2_SOURCES = {
    "pr-fix": ROOT / "docs/rules/pr-fixer/workflows/pr-fix.json",
    "review-commit": ROOT / "docs/rules/pr-fixer/workflows/review-commit.json",
    "publish-fix": ROOT / "docs/rules/pr-fixer/workflows/publish-fix.json",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_steps(node: dict):
    for step in node.get("steps") or node.get("body") or ():
        yield step
        yield from _walk_steps(step)


def _all_nodes(doc: dict):
    yield doc
    yield from _walk_steps(doc)


def _role(doc: dict) -> str | None:
    return workflow_role({"workflow": {"definition": doc}})


def test_every_v2_source_is_pinned_in_its_role():
    from culture_rules.actors.trusted import TRUSTED_WORKFLOWS_V2, workflow_digest_v2

    assert set(TRUSTED_WORKFLOWS_V2) == set(TRUSTED_WORKFLOWS)
    # the single d20 fixture is historical: trusted through its v1 pin only (h33, d4)
    single = _load(SINGLE_SOURCE)
    assert TRUSTED_WORKFLOWS_V2[ROLE_SINGLE] == frozenset()
    assert workflow_digest_v2(single) not in TRUSTED_WORKFLOWS_V2[ROLE_SINGLE]
    assert workflow_digest(single) in TRUSTED_WORKFLOWS[ROLE_SINGLE]
    assert _role(single) == ROLE_SINGLE
    for role, path in V2_SOURCES.items():
        digest = workflow_digest_v2(_load(path))
        assert digest in TRUSTED_WORKFLOWS_V2[role], (
            f"{path} now hashes (v2) to {digest}: if intended, add it to "
            f"TRUSTED_WORKFLOWS_V2[{role!r}] in culture_rules/actors/trusted.py"
        )
        assert _role(_load(path)) == role
    every = [d for digests in TRUSTED_WORKFLOWS_V2.values() for d in digests]
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", d) for d in every)
    assert len(every) == len(set(every))  # a v2 digest belongs to one role


def test_v2_ignores_description_and_notes_at_every_level():
    from culture_rules.actors.trusted import workflow_digest_v2

    for path in (*V2_SOURCES.values(), SINGLE_SOURCE):
        wf = _load(path)
        base = workflow_digest_v2(wf)
        edited = copy.deepcopy(wf)
        for node in _all_nodes(edited):
            node["description"] = "prose changed"
            node["notes"] = "notes changed"
        assert workflow_digest_v2(edited) == base, path
        stripped = copy.deepcopy(wf)
        for node in _all_nodes(stripped):
            node.pop("description", None)
            node.pop("notes", None)
        assert workflow_digest_v2(stripped) == base, path
        assert workflow_digest_v2({**wf, "version": 99}) == base


def test_v2_changes_on_any_other_field():
    from culture_rules.actors.trusted import workflow_digest_v2

    wf = _load(V2_SOURCES["pr-fix"])
    base = workflow_digest_v2(wf)
    for change in (
        {"name": "renamed"},
        {"id": "other"},
        {"enabled": False},
        {"steps": wf["steps"][:-1]},
        {"edges": wf.get("edges", [])[:-1]},
        {"outputs": []},
        {"inputs": []},
    ):
        assert workflow_digest_v2({**wf, **change}) != base, change
    # a port description is not prose the v2 digest drops: only workflow and step level
    ported = copy.deepcopy(wf)
    ported["inputs"][0]["description"] = "changed"
    assert workflow_digest_v2(ported) != base
    # a field deep in a loop body
    looped = 0
    for path in (*V2_SOURCES.values(), SINGLE_SOURCE):
        doc = _load(path)
        loop = next((s for s in _walk_steps(doc) if s.get("body")), None)
        if loop is None:
            continue
        edited = copy.deepcopy(doc)
        body_step = next(s for s in _walk_steps(edited) if s["id"] == loop["id"])["body"][0]
        body_step["name"] = (body_step.get("name") or "") + " edited"
        assert workflow_digest_v2(edited) != workflow_digest_v2(doc), path
        edited2 = copy.deepcopy(doc)
        next(s for s in _walk_steps(edited2) if s["id"] == loop["id"])["body"][0]["config"] = {
            "x": 1
        }
        assert workflow_digest_v2(edited2) != workflow_digest_v2(doc), path
        looped += 1
    assert looped >= 2  # pr-fix and publish-fix (and the single fixture) carry loops


def test_moving_description_into_notes_keeps_v2_and_the_role():
    """t12 migrates the committed files (description -> notes): v2 and the role survive."""
    from culture_rules.actors.trusted import workflow_digest_v2
    from tests.rules.pre_migration import pre_migration

    for role, path in V2_SOURCES.items():
        wf = pre_migration(_load(path))  # the description-bearing form t12 migrated away
        migrated = copy.deepcopy(wf)
        moved = 0
        for node in _all_nodes(migrated):
            text = node.pop("description", "")
            if text:
                moved += 1
                node["notes"] = "\n\n".join(t for t in (node.get("notes", ""), text) if t)
            node["description"] = ""
        assert moved, path
        assert workflow_digest_v2(migrated) == workflow_digest_v2(wf), path
        assert workflow_digest(migrated) != workflow_digest(wf), path  # v1 alone would break
        assert _role(migrated) == role, path


def test_a_run_matches_by_v1_or_v2_and_unknown_under_both_is_refused():
    from culture_rules.actors import trusted
    from tests.rules.pre_migration import pre_migration

    wf = _load(V2_SOURCES["publish-fix"])
    historical = pre_migration(wf)  # the v1 path needs the pre-t12 description
    v1 = trusted.workflow_digest(historical)
    v2 = trusted.workflow_digest_v2(wf)
    assert _role(wf) == trusted.ROLE_PUBLISH
    only_v1 = {**trusted.TRUSTED_WORKFLOWS_V2, trusted.ROLE_PUBLISH: frozenset()}
    only_v2 = {**trusted.TRUSTED_WORKFLOWS, trusted.ROLE_PUBLISH: frozenset()}
    import pytest

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trusted, "TRUSTED_WORKFLOWS_V2", only_v1)
        assert v1 in trusted.TRUSTED_WORKFLOWS[trusted.ROLE_PUBLISH]
        assert _role(historical) == trusted.ROLE_PUBLISH
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trusted, "TRUSTED_WORKFLOWS", only_v2)
        assert v2 in trusted.TRUSTED_WORKFLOWS_V2[trusted.ROLE_PUBLISH]
        assert _role(wf) == trusted.ROLE_PUBLISH
    # an edit outside prose: unknown under both versions, refused
    edited = {**wf, "name": "edited"}
    assert _role(edited) is None
    assert workflow_refusal({"workflow": {"definition": edited}}) == "workflow_not_trusted"
    # a forged stored digest is never read, whichever version it claims
    forged = {"workflow": {"definition": edited, "digest": v2, "digest_v2": v2}}
    assert workflow_refusal(forged) == "workflow_not_trusted"


def test_v1_and_v2_matching_different_roles_fails_closed():
    from culture_rules.actors import trusted
    from tests.rules.pre_migration import pre_migration

    wf = pre_migration(_load(V2_SOURCES["publish-fix"]))
    v2 = trusted.workflow_digest_v2(wf)
    crossed = {
        **trusted.TRUSTED_WORKFLOWS_V2,
        trusted.ROLE_PUBLISH: frozenset(),
        trusted.ROLE_REVIEW: frozenset({v2}),
    }
    import pytest

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trusted, "TRUSTED_WORKFLOWS_V2", crossed)
        assert trusted.workflow_digest(wf) in trusted.TRUSTED_WORKFLOWS[trusted.ROLE_PUBLISH]
        assert _role(wf) is None
        assert workflow_refusal({"workflow": {"definition": wf}}) == "workflow_not_trusted"


def test_a_digest_error_grants_no_role(monkeypatch):
    from culture_rules.actors import trusted

    wf = _load(V2_SOURCES["publish-fix"])

    def boom(_definition):
        raise RuntimeError("digest failed")

    monkeypatch.setattr(trusted, "workflow_digest_v2", boom)
    assert _role(wf) is None
    monkeypatch.undo()
    monkeypatch.setattr(trusted, "workflow_digest", boom)
    assert _role(wf) is None
    for bad in ({"id": 1}, {"steps": "nope"}, {}):
        assert _role(bad) is None


def test_v1_is_unchanged_by_the_v2_function():
    """v1 still hashes description (only version and notes are left out, d2)."""
    from culture_rules.actors.trusted import workflow_digest_v2

    wf = workflow_doc()
    assert workflow_digest({**wf, "description": "x"}) != workflow_digest(wf)
    assert workflow_digest(wf) != workflow_digest_v2(wf)
    assert workflow_digest(wf) in TRUSTED_WORKFLOWS[ROLE_SINGLE]


def test_the_pre_rename_pr_fix_stays_trusted_in_its_role_beside_the_renamed_one():
    """d12: the step rename gave pr-fix a new v2 digest; the old pin stays while runs pinned
    to the pre-rename definition may still push."""
    from culture_rules.actors.trusted import ROLE_FIX, TRUSTED_WORKFLOWS_V2, workflow_digest_v2

    old = "sha256:4a92df7e0b127246fe668c8ea2233169088f1d9ee06df0bad33ebe2036dad769"
    assert old in TRUSTED_WORKFLOWS_V2[ROLE_FIX]
    assert workflow_digest_v2(_load(V2_SOURCES[ROLE_FIX])) in TRUSTED_WORKFLOWS_V2[ROLE_FIX]
    assert workflow_digest_v2(_load(V2_SOURCES[ROLE_FIX])) != old


def test_pinned_digest_names_the_v1_or_v2_digest_a_definition_matches(monkeypatch):
    doc = {**workflow_doc(), "id": "probe-not-trusted", "name": "probe-not-trusted"}
    assert pinned_digest(doc) is None
    v1, v2 = workflow_digest(doc), workflow_digest_v2(doc)
    monkeypatch.setitem(TRUSTED_WORKFLOWS_V2, "probe", frozenset({v2}))
    assert pinned_digest(doc) == v2
    monkeypatch.setitem(TRUSTED_WORKFLOWS, "probe", frozenset({v1}))
    assert pinned_digest(doc) == v1
