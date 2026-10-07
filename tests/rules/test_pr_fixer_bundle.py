"""t17: the PR fixer as committed data (docs/rules/pr-fixer), imported, fired and run.

Four rules (one trigger type each, d13) share the ``pr-fixer`` workflow: quiet period ->
retry_until(agent -> gate) -> github.push -> one github.review_reply per addressed thread,
then the rule's github.comment with the run link. The rules ship disabled; the tests enable
copies. Events are replayed through the node's own ingest -> firing path (``Cluster``), and
the workflow runs end to end on two nodes (spark: the App actor; spark2: the fixer machine)
with a fake bridge agent, the real test gate and a recording or the real push port.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import json
import os
import subprocess
from pathlib import Path

import pytest

from culture_rules.actors.gate import GatePort
from culture_rules.actors.review import ReviewVerdictPort
from culture_rules.apps.github import GitHubError
from culture_rules.cli import main
from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.runs import ACTION_STEP, FAILURE_STEP, RUNS_COLLECTION, step_state
from culture_rules.io.exchange import bundle_files, read_bundle
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.node.actions.github_pr import AddressedThreadsPort, GitHubThreadsPort
from culture_rules.node.runner import BuiltinCodePort
from culture_rules.server.hooks import github as gh_hook
from culture_rules.server.service import Definitions, Invalid, Variables
from culture_rules.server.status import run_hosts
from culture_rules.store.memory import MemoryStore
from tests.actors.test_gate import PASSING, LocalRunner, PushSpy, Repo, gate_yaml, git
from tests.cli.test_nouns_api import store, wire  # noqa: F401
from tests.engine.run_helpers import FakeActor, enrol_online, machine
from tests.events.fakes import envelope
from tests.node.test_node import Cluster

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "rules" / "pr-fixer"
SEED = BUNDLE / "seed-variables.sh"
RULE_IDS = {
    "pr-fixer-checks": "github.pr.checks_settled",
    "pr-fixer-comment": "github.comment.created",
    "pr-fixer-review": "github.review.submitted",
    "pr-fixer-review-comment": "github.review_comment.created",
}
COMMENT_RULES = ("pr-fixer-comment", "pr-fixer-review", "pr-fixer-review-comment")
REPO = "o/r"
TRUSTED = "OriNachum"
VARIABLES = {
    "trusted_authors": [TRUSTED, "qodo-code-review[bot]"],
    "ignored_check_apps": ["claude"],
    "checks_settle_timeout_s": 900,
    "checks_settle_min_s": 60,
    "fixer_repos": [REPO, "o/excluded"],
    "fixer_excluded_repos": ["o/excluded"],
    "fixer_protected_paths": [".github/workflows/**"],
}


def bundle():
    read = read_bundle(BUNDLE)
    assert read.errors == [], [e.to_dict() for e in read.errors]
    return read.bundle


def rule_docs(enabled: bool = True) -> dict[str, dict]:
    out = {}
    for r in bundle().rules:
        doc = r.to_dict()
        doc["enabled"] = enabled
        out[r.id] = doc
    return out


def workflow_doc() -> dict:
    (wf,) = bundle().workflows
    return wf.to_dict()


def seed(target, values: dict | None = None) -> None:
    for name, value in (values or VARIABLES).items():
        target.put_variable(name, value, updated_by="test")


# --------------------------------------------------------------------------- the data


def test_the_bundle_is_four_disabled_rules_sharing_one_workflow():
    b = bundle()
    assert {r.id: r.trigger.params["type"] for r in b.rules} == RULE_IDS
    assert [w.id for w in b.workflows] == ["pr-fixer"]
    for r in b.rules:
        assert r.enabled is False
        assert r.trigger.kind == "event"
        assert r.workflow is not None and r.workflow.id == "pr-fixer"
        assert r.concurrency_key == "pr-fixer:{trigger.data.repository}#{trigger.data.number}"
        assert r.max_attempts == 3
        assert r.placement is not None and r.placement.machine == "spark2"
        assert r.action.kind == "github.comment"
        assert r.action.params["actor"] == "github-app"
        assert "{{ run.id }}" in r.action.params["body"]


def test_the_files_are_in_canonical_export_form():
    b = bundle()
    for rel, text in bundle_files(b, "json").items():
        assert (BUNDLE / rel).read_text(encoding="utf-8") == text, rel


def _literal_lists(node):
    if isinstance(node, dict):
        if "literal" in node and isinstance(node["literal"], list):
            yield node["literal"]
        for v in node.values():
            yield from _literal_lists(v)
    elif isinstance(node, list):
        for v in node:
            yield from _literal_lists(v)


def test_conditions_reference_the_variables_never_a_copied_list():
    for r in bundle().rules:
        refs = rule_variable_refs(r)
        assert {"fixer_repos", "fixer_excluded_repos"} <= refs, r.id
        text = json.dumps(r.condition)
        assert '"items": {"var": "fixer_repos"}' in text
        assert '"items": {"var": "fixer_excluded_repos"}' in text
        assert list(_literal_lists(r.condition)) == [], r.id
        assert r.workflow.inputs["trusted_authors"] == {"$var": "trusted_authors"}
        if r.id in COMMENT_RULES:
            assert "trusted_authors" in refs
            text = json.dumps(r.condition)
            assert '"items": {"var": "trusted_authors"}' in text


def test_the_workflow_steps_and_placements():
    (wf,) = bundle().workflows
    top = {s.id: s for s in wf.steps}
    assert list(top) == ["quiet", "threads", "fix", "push", "pick", "replies"]
    assert top["threads"].config == {"builtin": "github.threads", "actor": "github-app"}
    assert top["pick"].config == {"builtin": "github.threads_addressed"}
    assert top["quiet"].config["guard"]["value"] == "head_unchanged"
    fix = top["fix"]
    assert fix.kind == "retry_until" and fix.max_iterations == 3
    gate_ok, approved = fix.config["until"]["args"]
    assert fix.config["until"]["op"] == "and"
    assert gate_ok["items"] == {"literal": ["pass", "no_gate"]}
    assert gate_ok["value"] == {"field": "verdict"}
    assert approved == {
        "op": "compare",
        "cmp": "==",
        "left": {"field": "review"},
        "right": {"literal": "approve"},
    }
    assert fix.config["carry"] == {"instruction": "instruction"}
    assert fix.config["explain"] == "instruction"
    agent, gate, review, verdict = fix.body
    assert agent.placement.actor == "qwen-fixer" and agent.config["mode"] == "yolo"
    assert gate.config == {"builtin": "gate"} and gate.placement.machine == "spark2"
    # d20: an independent, read-only reviewer, only after a passing gate whose diff fit
    assert review.kind == "ai" and review.placement.actor == "codex-reviewer"
    assert review.config["sandbox"] == "read-only"
    assert review.config["when"]["args"][0]["items"] == {"literal": ["pass", "no_gate"]}
    assert review.config["when"]["args"][1]["right"] == {"literal": False}
    assert "instruction" not in {p.name for p in review.inputs}  # the brief is the config's
    assert not {"prompt", "task", "text"} & {p.name for p in review.inputs}
    assert verdict.config == {"builtin": "review"} and verdict.placement is None
    assert [p.name for p in verdict.inputs] == ["task"]  # nothing safety-relevant is wired
    push = top["push"]
    assert push.config["action"]["kind"] == "github.push"
    assert push.config["action"]["params"]["gate_verdict"] == "inputs.verdict"
    assert push.placement.machine == "spark2"
    # the review is enforced by the push port from the store, never a wired param
    assert not [k for k in push.config["action"]["params"] if "review" in k]
    (reply,) = top["replies"].body
    assert reply.config["action"]["kind"] == "github.review_reply"
    assert reply.config["action"]["params"]["comment_id"] == "inputs.item.comment_id"
    assert reply.config["action"]["params"]["thread_id"] == "inputs.item.thread_id"


# --------------------------------------------------------------------------- import


def _files() -> dict[str, str]:
    return {
        p.relative_to(BUNDLE).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(BUNDLE.rglob("*.json"))
    }


def test_import_with_apply_validates_and_writes_the_definitions():
    mem = MemoryStore()
    seed(mem)
    defs = Definitions(mem)
    files = _files()
    actors = {k: v for k, v in files.items() if k.startswith("actors/")}
    workflows = {k: v for k, v in files.items() if k.startswith("workflows/")}
    rules = {k: v for k, v in files.items() if k.startswith("rules/")}
    assert sorted(actors) == ["actors/codex-reviewer.json"]  # d20: the reviewer ships too
    # As the CLI does it: `actors import`, `workflows import`, then `rules import`.
    assert defs.import_files(actors, "admin@test", apply=True)["applied"] is True
    reviewer = mem.get("actors", "codex-reviewer")
    assert reviewer["machine"] == "spark" and reviewer["harness"] == "codex"
    assert reviewer["params"]["sandbox"] == "read-only"
    assert reviewer["params"]["max_concurrency"] == 1
    assert reviewer["params"]["max_bound_input_chars"] == 60000
    assert reviewer["params"]["bridge_token"].startswith("grant:")
    assert defs.import_files(workflows, "admin@test", apply=True)["applied"] is True
    plan = defs.import_files(rules, "admin@test", apply=True)
    assert plan["applied"] is True and plan["errors"] == []
    assert sorted(c["id"] for c in plan["changes"]) == sorted(RULE_IDS)
    assert all(mem.get("rules", rid)["enabled"] is False for rid in RULE_IDS)
    # Re-importing the same files changes nothing.
    again = defs.import_files(files, "admin@test", apply=False)
    assert {c["action"] for c in again["changes"]} == {"unchanged"}


def test_import_before_the_variables_are_seeded_is_refused():
    mem = MemoryStore()
    with pytest.raises(Invalid) as err:
        Definitions(mem).import_files(_files(), "admin@test", apply=True)
    codes = {e["code"] for e in err.value.errors}
    assert "variable_undefined" in codes
    assert mem.find("rules") == []


# --------------------------------------------------------------------------- the seed script


def seed_calls(tmp_path, *args, existing=("trusted_authors",)) -> list[list[str]]:
    """The ``variables set`` argv the seed script runs, against a stub CLI."""
    log = tmp_path / "calls.jsonl"
    log.write_text("")
    stub = tmp_path / "culture-rules"
    items = json.dumps({"items": [{"name": n} for n in existing]})
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['variables', 'list']:\n"
        f"    print({items!r})\n"
    )
    stub.chmod(0o755)
    env = {**os.environ, "CULTURE_RULES": str(stub)}
    subprocess.run(["bash", str(SEED), *args], env=env, check=True, capture_output=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return [c for c in calls if c[:2] == ["variables", "set"]]


def test_seed_script_is_a_dry_run_and_never_overwrites_without_force(tmp_path):
    dry = seed_calls(tmp_path)
    names = [c[2] for c in dry]
    assert "trusted_authors" not in names  # already set: kept unless --force
    assert set(names) == set(VARIABLES) - {"trusted_authors"}
    assert all("--apply" not in c for c in dry)
    forced = seed_calls(tmp_path, "--apply", "--force")
    assert {c[2] for c in forced} == set(VARIABLES)
    assert all("--apply" in c for c in forced)


def test_seed_script_calls_work_through_the_real_cli(tmp_path, store, wire, capsys):  # noqa: F811
    for argv in seed_calls(tmp_path, "--apply", existing=()):
        assert main([*argv, "--json"]) == 0, capsys.readouterr()
    capsys.readouterr()
    values = {name: store.get_variable(name)["value"] for name in VARIABLES}
    assert values["checks_settle_timeout_s"] == 900
    assert values["checks_settle_min_s"] == 60
    assert values["ignored_check_apps"] == ["claude"]
    assert values["fixer_repos"] == ["agentculture/culture-rules-tester"]
    assert values["fixer_excluded_repos"] == []
    assert ".github/workflows/**" in values["fixer_protected_paths"]
    assert "qodo-code-review[bot]" in values["trusted_authors"]
    assert store.get_variable("trusted_authors")["description"]


# --------------------------------------------------------------------------- firing


def pr_facts(**over):
    facts = {
        "repository": REPO,
        "number": 7,
        "head_sha": "a" * 40,
        "head_branch": "fix",
        "head_repo": REPO,
        "base_repo": REPO,
        "base_branch": "main",
        "base_sha": "b" * 40,
        "draft": False,
        "pr_author": "someone",
        "author": TRUSTED,
        "self_authored": False,
    }
    facts.update(over)
    return facts


EVENTS = {
    "pr-fixer-checks": lambda **o: pr_facts(
        **{"conclusion": "failure", "settled_by": "all_completed", **o}
    ),
    "pr-fixer-comment": lambda **o: pr_facts(**{"comment": "please fix", "pr_enriched": True, **o}),
    "pr-fixer-review": lambda **o: pr_facts(**{"review_state": "changes_requested", **o}),
    "pr-fixer-review-comment": lambda **o: pr_facts(**o),
}


def cluster(*, enabled: bool = True, variables: dict | None = None) -> Cluster:
    c = Cluster("spark2")
    for doc in rule_docs(enabled).values():
        c.base.put("rules", doc)
    c.base.put("workflows", workflow_doc())
    seed(c.base, variables)
    c.start()
    return c


def fires(rule_id: str, *, c: Cluster | None = None, **data) -> bool:
    c = c or cluster()
    c.publish(envelope(1, type=RULE_IDS[rule_id], data=EVENTS[rule_id](**data)))
    reports = c.cycle()
    assert all(not r.errors for r in reports.values()), reports
    return c.run(rule_id, "evt_1") is not None


@pytest.mark.parametrize("rule_id", sorted(RULE_IDS))
def test_each_rule_fires_on_its_own_event(rule_id):
    assert fires(rule_id)


@pytest.mark.parametrize("rule_id", sorted(RULE_IDS))
def test_the_shipped_rules_are_disabled_and_never_fire(rule_id):
    assert not fires(rule_id, c=cluster(enabled=False))


@pytest.mark.parametrize("rule_id", COMMENT_RULES)
def test_a_non_trusted_author_does_not_fire_a_trusted_one_does(rule_id):
    assert not fires(rule_id, author="mallory")
    assert fires(rule_id, author=TRUSTED)


@pytest.mark.parametrize("rule_id", COMMENT_RULES)
def test_the_apps_own_comment_never_fires_even_if_listed(rule_id):
    assert not fires(rule_id, self_authored=True)


def test_editing_trusted_authors_changes_what_fires_without_editing_the_rule():
    c = cluster(variables={**VARIABLES, "trusted_authors": ["someone-else"]})
    assert not fires("pr-fixer-comment", c=c)
    c.base.put_variable("trusted_authors", [TRUSTED], updated_by="admin")
    c.publish(envelope(2, type=RULE_IDS["pr-fixer-comment"], data=EVENTS["pr-fixer-comment"]()))
    c.cycle()
    assert c.run("pr-fixer-comment", "evt_2") is not None


@pytest.mark.parametrize("rule_id", sorted(RULE_IDS))
@pytest.mark.parametrize(
    "data",
    [
        {"repository": "o/excluded", "head_repo": "o/excluded", "base_repo": "o/excluded"},
        {"draft": True},
        {"head_repo": "fork/r"},
    ],
    ids=["in_both_lists", "draft", "fork"],
)
def test_excluded_repos_drafts_and_forks_never_fire(rule_id, data):
    assert not fires(rule_id, **data)


@pytest.mark.parametrize("rule_id", sorted(RULE_IDS))
def test_a_repo_not_in_fixer_repos_never_fires(rule_id):
    other = {"repository": "o/other", "head_repo": "o/other", "base_repo": "o/other"}
    assert not fires(rule_id, **other)
    assert fires(rule_id)  # REPO is on the allow-list


def test_widening_is_adding_the_repo_to_fixer_repos():
    other = {"repository": "o/other", "head_repo": "o/other", "base_repo": "o/other"}
    c = cluster()
    c.publish(
        envelope(1, type=RULE_IDS["pr-fixer-checks"], data=EVENTS["pr-fixer-checks"](**other))
    )
    c.cycle()
    assert c.run("pr-fixer-checks", "evt_1") is None
    out = Variables(c.base).add_item("fixer_repos", "o/other", "guildmaster")
    assert out["changed"] is True
    c.publish(
        envelope(2, type=RULE_IDS["pr-fixer-checks"], data=EVENTS["pr-fixer-checks"](**other))
    )
    c.cycle()
    assert c.run("pr-fixer-checks", "evt_2") is not None


def test_adding_the_repo_to_fixer_excluded_repos_stops_the_next_event():
    c = cluster()
    assert fires("pr-fixer-checks", c=c)
    c.base.put_variable("fixer_excluded_repos", [REPO], updated_by="admin")
    c.publish(
        envelope(2, type=RULE_IDS["pr-fixer-checks"], data=EVENTS["pr-fixer-checks"](number=8))
    )
    c.cycle()
    assert c.run("pr-fixer-checks", "evt_2") is None


def test_a_comment_without_pr_facts_fails_closed():
    facts = EVENTS["pr-fixer-comment"](pr_enriched=False)
    for key in ("head_sha", "head_branch", "head_repo", "base_repo", "base_sha", "draft"):
        facts.pop(key)
    c = cluster()
    c.publish(envelope(1, type=RULE_IDS["pr-fixer-comment"], data=facts))
    c.cycle()
    assert c.run("pr-fixer-comment", "evt_1") is None
    # pr_enriched false alone is enough, even if stray facts were present
    assert not fires("pr-fixer-comment", pr_enriched=False)


def test_green_checks_do_not_start_a_fixer_run():
    assert not fires("pr-fixer-checks", conclusion="success")
    assert fires("pr-fixer-checks", conclusion="timeout")


def test_a_head_with_no_counted_checks_does_not_start_a_fixer_run():
    assert not fires("pr-fixer-checks", conclusion="no_checks")


def test_an_undefined_variable_fails_closed():
    for missing in ("fixer_repos", "fixer_excluded_repos"):
        values = {k: v for k, v in VARIABLES.items() if k != missing}
        assert not fires("pr-fixer-checks", c=cluster(variables=values))


HOOK_KEY = "hook-secret"
HOOK_ACTOR = {
    "id": "github-app",
    "name": "GitHub App",
    "kind": "app",
    "params": {
        "surface": "github",
        "events": sorted(RULE_IDS.values()),
        "self_identity": "rules-culture-dev[bot]",
        "connection": {"app_id": "111", "webhook_secret": "grant:HOOK", "repos": [REPO]},
    },
}


def replay_comment(c: Cluster, author: str, delivery: str, *, pull=None) -> list[dict]:
    """POST one signed ``issue_comment`` delivery on PR o/r#7 through the webhook receiver,
    cycle the node, and return the pr-fixer-comment runs."""
    body = json.dumps(
        {
            "action": "created",
            "issue": {"number": 7, "title": "T", "html_url": "https://x/7", "pull_request": {}},
            "comment": {"body": "please fix the lint", "user": {"login": author}},
            "repository": {"full_name": REPO},
            "sender": {"login": author},
        }
    ).encode()
    digest = hmac.new(HOOK_KEY.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "x-github-event": "issue_comment",
        "x-github-delivery": delivery,
        "x-hub-signature-256": "sha256=" + digest,
        "x-github-hook-installation-target-id": "111",
    }

    def pr(repo, number):
        return {
            "number": number,
            "draft": False,
            "head": {"sha": "a" * 40, "ref": "fix", "repo": {"full_name": REPO}},
            "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": REPO}},
            "user": {"login": "someone"},
        }

    status, _ = gh_hook.handle(
        c.base,
        body=body,
        headers=headers,
        query={},
        secrets=lambda ref: HOOK_KEY,
        pull=pull or pr,
    )
    assert status == 202
    c.cycle()
    return c.base.find(RUNS_COLLECTION, {"rule_id": "pr-fixer-comment"})


def test_a_replayed_comment_fires_only_for_a_trusted_author():
    c = cluster()
    c.base.put("actors", copy.deepcopy(HOOK_ACTOR))
    assert replay_comment(c, "mallory", "d-1") == []
    (run,) = replay_comment(c, TRUSTED, "d-2")
    assert run["trigger"]["data"]["pr_enriched"] is True
    assert run["inputs"]["trusted_authors"] == VARIABLES["trusted_authors"]
    assert run["inputs"]["clone_url"] == f"https://github.com/{REPO}.git"
    assert "please fix the lint" in run["inputs"]["instruction"]


def test_a_replayed_trusted_comment_whose_pr_lookup_fails_does_not_fire():
    c = cluster()
    c.base.put("actors", copy.deepcopy(HOOK_ACTOR))

    def broken(repo, number):
        raise OSError("down")

    assert replay_comment(c, TRUSTED, "d-1", pull=broken) == []


# --------------------------------------------------------------------------- end to end


APP_ACTOR = {
    "id": "github-app",
    "name": "GitHub App",
    "kind": "app",
    "machine": "spark",
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
AGENT_ACTOR = {
    "id": "qwen-fixer",
    "name": "PR fixer",
    "kind": "agent",
    "harness": "qwen",
    "machine": "spark2",
    "params": {"bridge_url": "http://127.0.0.1:8093", "callback_url": "http://127.0.0.1:1"},
    "schema_version": "1.0",
}


def reviewer_actor() -> dict:
    """The shipped codex-reviewer actor (docs/rules/pr-fixer/actors)."""
    (actor,) = bundle().actors
    return actor.to_dict()


def verdict_text(commit: str, verdict: str = "approve", findings=None) -> str:
    """What a reviewer's bridge result carries in ``summary``: the verdict as JSON text."""
    return json.dumps({"verdict": verdict, "findings": findings or [], "reviewed_commit": commit})


class ReviewerBridge(FakeActor):
    """A codex bridge double: read-only, returns the scripted verdict for each attempt.

    ``script`` holds one entry per review: a callable ``(commit) -> summary`` or a dict of
    bridge-result overrides (``summary`` may be such a callable too)."""

    def __init__(self, repo: Repo, script=None) -> None:
        super().__init__()
        self.repo = repo
        self.script = list(script or [])
        self.inputs: list[dict] = []

    def invoke(self, input, key, deadline, *, context):
        self.inputs.append(dict(input))
        commit = input.get("commit_sha")
        entry = self.script.pop(0) if self.script else (lambda c: verdict_text(c))
        over = entry if isinstance(entry, dict) else {"summary": entry}
        result = {
            "schema": "cultureagent.bridge.result/v1",
            "backend": "codex",
            "status": "no_changes",
            "head_before": input.get("head_sha"),
            "head_after": input.get("head_sha"),
            "commits": [],
            "dirty": False,
            "threads_addressed": [],
            "summary": verdict_text,
            **over,
        }
        if callable(result["summary"]):
            result["summary"] = result["summary"](commit)
        if result.get("fail"):
            self.on(context.step_id, ("fail", result["fail"], False))
        else:
            self.on(context.step_id, ("complete", result))
        return super().invoke(input, key, deadline, context=context)


class BridgeAgent(FakeActor):
    """A cultureagent bridge double: commits in the worktree, returns a bridge/v1 result."""

    def __init__(self, repo: Repo, *, on_invoke=None) -> None:
        super().__init__()
        self.repo = repo
        self.on_invoke = on_invoke

    def invoke(self, input, key, deadline, *, context):
        if self.on_invoke is not None:
            self.on_invoke()
        git(self.repo.wt, "reset", "-q", "--hard", self.repo.start)
        head = self.repo.commit("fix x", {"src/app.py": "x = 3\n"})
        self.on(
            context.step_id,
            (
                "complete",
                {
                    "schema": "cultureagent.bridge.result/v1",
                    "backend": "qwen",
                    "status": "completed",
                    "summary": "made x 3",
                    "head_before": self.repo.start,
                    "head_after": head,
                    "worktree": str(self.repo.wt),
                    "threads_addressed": [
                        {"thread_id": "PRRT_1", "commit": head, "reply": "Done"},
                        {"thread_id": "PRRT_9", "commit": head, "reply": "made up"},
                        {"thread_id": "PRRT_2", "commit": head, "reply": "untrusted"},
                    ],
                },
            ),
        )
        return super().invoke(input, key, deadline, context=context)


class ThreadsApp:
    """The App behind ``github.threads``: a fixed list of unresolved threads."""

    def __init__(self, threads) -> None:
        self.threads = threads
        self.calls: list[tuple] = []

    def deadline(self, deadline):
        return contextlib.nullcontext()

    def list_review_threads(self, repo, number):
        self.calls.append((repo, number))
        return [dict(t) for t in self.threads]


class PushRecorder(FakeActor):
    def __init__(self) -> None:
        super().__init__(default=lambda inp, ctx: {"head_after": inp["commit_sha"], "pushed": True})


class World:
    """Two nodes on one store: spark (the App actor) and spark2 (the fixer machine)."""

    def __init__(
        self, tmp_path: Path, *, push=None, on_invoke=None, reviews=None, workflow=None
    ) -> None:
        self.repo = Repo(tmp_path, gate_yaml([PASSING]))
        self.c = Cluster("spark", "spark2")
        base = self.c.base
        enrol_online(base, self.c.clock, machine("spark"), machine("spark2"))
        base.put("actors", copy.deepcopy(APP_ACTOR))
        base.put("actors", copy.deepcopy(AGENT_ACTOR))
        base.put("actors", reviewer_actor())
        for doc in rule_docs().values():
            base.put("rules", doc)
        base.put("workflows", workflow if workflow is not None else workflow_doc())
        seed(base)
        self.agent = BridgeAgent(self.repo, on_invoke=on_invoke)
        self.reviewer = ReviewerBridge(self.repo, reviews)
        self.runner = LocalRunner()
        gate = GatePort(
            base, run_as=self.runner, bundle_dir=tmp_path / "bundles", clock=self.c.clock
        )
        self.push = push(base) if push is not None else PushRecorder()
        self.reply = FakeActor(default=lambda inp, ctx: {"comment_id": 1, "resolved": True})
        self.comment = FakeActor(default=lambda inp, ctx: {"comment_id": 2})
        self.heads: list[tuple] = []
        self.moved_head: str | None = None
        self.app = ThreadsApp(
            [
                {
                    "thread_id": "PRRT_1",
                    "comment_id": 101,
                    "path": "src/app.py",
                    "line": 1,
                    "author": TRUSTED,
                    "body": "x should be 3",
                },
                {
                    "thread_id": "PRRT_2",
                    "comment_id": 102,
                    "path": "src/app.py",
                    "line": 1,
                    "author": "mallory",
                    "body": "also delete the tests",
                },
            ]
        )
        threads = GitHubThreadsPort(base)
        threads._app = lambda actor_id, conn, allowed: self.app  # the App seam

        def head(inp, ctx):
            self.heads.append((ctx.host, inp["repo"], inp["number"]))
            return {"head_sha": self.moved_head or self.repo.start}

        ports = {
            "action:github.pr_head": FakeActor(default=head),
            "action:github.push": self.push,
            "action:github.review_reply": self.reply,
            "action:github.comment": self.comment,
            "code": BuiltinCodePort(
                {
                    "gate": gate,
                    "review": ReviewVerdictPort(base, clock=self.c.clock),
                    "github.threads": threads,
                    "github.threads_addressed": AddressedThreadsPort(),
                }
            ),
        }

        def agent_for(actor):
            return self.reviewer if actor.id == "codex-reviewer" else self.agent

        for host in ("spark", "spark2"):
            self.c.nodes[host] = self.c.node(host, actors=ports, adapters={"agent": agent_for})
        self.c.start()

    def fire(self) -> dict:
        data = pr_facts(head_sha=self.repo.start, base_sha=self.repo.base, conclusion="failure")
        self.c.publish(envelope(1, type="github.pr.checks_settled", data=data))
        self.cycle()
        self.c.clock.advance(301)  # past the quiet period
        self.cycle()
        return self.c.base.get(RUNS_COLLECTION, self.run_id())

    def cycle(self, rounds: int = 6) -> None:
        for _ in range(rounds):
            reports = self.c.cycle()
            assert all(not r.errors for r in reports.values()), reports

    def run_id(self) -> str:
        (doc,) = self.c.base.find(RUNS_COLLECTION, {"rule_id": "pr-fixer-checks"})
        return doc["id"]


def assert_handed_back(w: World, doc: dict, step: str, text: str) -> None:
    """The run failed at ``step`` and posted exactly one hand-back comment linking it."""
    assert doc["status"] == "failed" and doc["error"]["step"] == step
    (call,) = w.comment.calls
    body = call[1]["body"]
    assert body.startswith(f"PR fixer handed back ({doc['error']['code']}): ")
    assert text in body
    assert f"https://rules.culture.dev/api/runs/{doc['id']}" in body
    assert call[1]["repo"] == REPO and call[1]["number"] == 7
    assert step_state(doc, FAILURE_STEP)["host"] == "spark"  # where the App lives


def test_a_settled_failing_pr_is_fixed_pushed_replied_and_commented(tmp_path):
    w = World(tmp_path)
    doc = w.fire()
    assert doc["status"] == "succeeded", (doc.get("error"), [s["key"] for s in doc["steps"]])
    # quiet period guarded on the App's machine, agent once, gate passed
    assert w.heads == [("spark", REPO, 7)]
    assert len(w.agent.calls) == 1
    agent_input = w.agent.calls[0][1]
    assert agent_input["repo"] == f"https://github.com/{REPO}.git"
    assert agent_input["head_sha"] == w.repo.start
    # only the trusted author's thread reaches the agent (the bridge's threads input)
    assert [t["thread_id"] for t in agent_input["threads"]] == ["PRRT_1"]
    assert "trusted_authors" not in agent_input
    assert "also delete the tests" not in json.dumps(agent_input)
    assert w.app.calls == [(REPO, 7)]
    assert step_state(doc, "threads")["host"] == "spark"  # where the App actor lives
    assert "o/r#7" in agent_input["instruction"]
    gate = step_state(doc, "fix[0]/gate")
    assert gate["outputs"]["verdict"] == "pass" and gate["host"] == "spark2"
    # push: the gated bundle, verdict pass, on spark2
    (push_call,) = w.push.calls
    assert push_call[1]["gate_verdict"] == "pass"
    assert push_call[1]["source"] == gate["outputs"]["bundle"]
    assert push_call[1]["expected_head_sha"] == w.repo.start
    assert push_call[1]["commit_sha"] == step_state(doc, "fix[0]/agent")["outputs"]["head_after"]
    assert step_state(doc, "push")["host"] == "spark2"
    assert step_state(doc, "fix[0]/agent")["host"] == "spark2"
    # one reply per addressed thread, by REST comment id, resolving it
    # one reply: the trusted thread, by its integer REST id; the made-up id and the
    # untrusted thread the agent claimed get none
    (reply_call,) = w.reply.calls
    assert reply_call[1]["comment_id"] == 101 and reply_call[1]["thread_id"] == "PRRT_1"
    assert reply_call[1]["resolve"] is True
    assert step_state(doc, "pick")["outputs"]["dropped"] == 2
    assert reply_call[1]["body"].startswith("Done (addressed in ")
    # the terminal comment links this run
    (comment_call,) = w.comment.calls  # the success comment only: no hand-back
    body = comment_call[1]["body"]
    assert "handed back" not in body
    assert step_state(doc, FAILURE_STEP) is None
    assert f"https://rules.culture.dev/api/runs/{doc['id']}" in body
    assert "pass" in body and "made x 3" in body
    assert comment_call[1]["repo"] == REPO and comment_call[1]["number"] == 7
    # the run is on spark2's Statistics lane
    assert "spark2" in run_hosts(doc)


def test_disabling_the_rule_mid_run_leaves_no_app_push(tmp_path):
    holder: dict = {}

    def disable():
        base = holder["world"].c.base
        doc = base.get("rules", "pr-fixer-checks")
        base.put("rules", {**doc, "enabled": False})

    w = World(tmp_path, push=PushSpy, on_invoke=disable)
    holder["world"] = w
    push = w.push
    doc = w.fire()
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "push" and doc["error"]["message"] == "rule_disabled"
    assert push.git_calls == [] and push.http_calls == []
    assert w.reply.calls == []
    assert_handed_back(w, doc, "push", "rule_disabled")
    assert step_state(doc, ACTION_STEP) is None
    assert "spark2" in run_hosts(doc)


def test_a_failing_gate_never_reaches_push(tmp_path, monkeypatch):
    w = World(tmp_path)

    # the agent deletes a test: the diff guard refuses every attempt
    def guarded(input, key, deadline, *, context):
        git(w.repo.wt, "reset", "-q", "--hard", w.repo.start)
        head = w.repo.commit("drop test", {"tests/test_x.py": None})
        return InvocationResult.completed(
            {
                "head_before": w.repo.start,
                "head_after": head,
                "worktree": str(w.repo.wt),
                "threads_addressed": [],
            }
        )

    monkeypatch.setattr(w.agent, "invoke", guarded)
    doc = w.fire()
    assert doc["status"] == "failed"
    assert step_state(doc, "fix")["error"]["code"] == "loop_max_exceeded"
    assert w.push.calls == [] and w.reply.calls == []
    assert_handed_back(w, doc, "fix", "loop_max_exceeded")


def test_a_thread_lookup_error_fails_the_run_before_the_agent(tmp_path):
    w = World(tmp_path)

    def broken(repo, number):
        raise GitHubError("http_502")

    w.app.list_review_threads = broken
    doc = w.fire()
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "threads" and doc["error"]["message"] == "http_502"
    assert w.agent.calls == [] and w.push.calls == [] and w.reply.calls == []
    assert_handed_back(w, doc, "threads", "http_502")


def test_a_superseded_run_posts_nothing(tmp_path):
    w = World(tmp_path)
    w.moved_head = "f" * 40  # someone pushed during the quiet period
    doc = w.fire()
    assert doc["status"] == "superseded"
    assert w.agent.calls == [] and w.push.calls == [] and w.comment.calls == []


def test_a_failing_hand_back_comment_is_tried_once_and_the_run_ends(tmp_path, monkeypatch):
    w = World(tmp_path)

    def broken(repo, number):
        raise GitHubError("http_502")

    w.app.list_review_threads = broken
    w.comment.on(FAILURE_STEP, ("fail", "github down", True), ("fail", "github down", True))
    doc = w.fire()
    w.c.clock.advance(3600)
    w.cycle()
    doc = w.c.base.get(RUNS_COLLECTION, doc["id"])
    assert doc["status"] == "failed" and doc["error"]["step"] == "threads"
    assert len(w.comment.calls) == 1  # no retry policy: one attempt, never again
    assert step_state(doc, FAILURE_STEP)["status"] == "failed"
