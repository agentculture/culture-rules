"""A two-node world running the shipped d21 PR fixer chain end to end (test harness).

spark (the App actor, the reviewer) and spark2 (the fixer machine: agent, gate, push) share
one store. The shipped bundle (docs/rules/pr-fixer) is imported with its rules enabled; a
red checks settle starts the chain: ``pr-fix`` (quiet, threads, Sonar, agent, gate) ->
``review-commit`` (Codex, the verdict) -> ``publish-fix`` (push, replies) or ``pr-fix``
again with the findings. Real gate, real review builtin, real run events and holds; the
qwen and codex bridges are transport doubles behind the real bridge adapter, so callbacks,
``require_commit`` and the locked brief run for real. The push is a recorder by default, or
the real ``github.push`` port against a local bare remote and a fake GitHub API.
"""

from __future__ import annotations

import copy
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from culture_rules.actors.agent import BridgeAgentActor, record_bridge_event
from culture_rules.actors.gate import GatePort
from culture_rules.actors.review import ReviewVerdictPort
from culture_rules.apps.sonarcloud import SonarCloud
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.io.exchange import read_bundle
from culture_rules.model.actor import Actor
from culture_rules.node.actions.github_pr import (
    AddressedThreadsPort,
    GitHubPushPort,
    GitHubThreadsPort,
)
from culture_rules.node.actions.sonar import SonarGateIssuesPort
from culture_rules.node.runner import BuiltinCodePort
from tests.actors.test_gate import PASSING, LocalRunner, Repo, gate_yaml, git
from tests.engine.run_helpers import FakeActor, enrol_online, machine
from tests.events.fakes import envelope
from tests.node.test_node import Cluster
from tests.rules.test_pr_fixer_single import (
    AGENT_ACTOR,
    APP_ACTOR,
    REPO,
    TRUSTED,
    VARIABLES,
    ReviewerBridge,
    ThreadsApp,
    pr_facts,
    reviewer_adapter,
    seed,
)

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "rules" / "pr-fixer"
TRIGGER_RULES = (
    "pr-fixer-checks",
    "pr-fixer-comment",
    "pr-fixer-review",
    "pr-fixer-review-comment",
)
STAGE_RULES = ("pr-fixer-review-commit", "pr-fixer-refix", "pr-fixer-publish")
CHAIN_VARIABLES = {**VARIABLES, "fixer_comment_triggers": ["/fix", "@rules-culture-dev"]}
KEY = f"pr-fixer:{REPO}#7"


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


def workflow_docs() -> dict[str, dict]:
    return {w.id: w.to_dict() for w in bundle().workflows}


class QwenBridge:
    """The qwen bridge behind the real :class:`BridgeAgentActor`: a transport double.

    Each request runs one scripted turn in the repo's worktree - ``"commit"`` (default: makes
    x 3), ``"none"`` (no commit) or a callable ``(repo) -> head`` - and records the terminal
    callback at once with the per-invocation token, as the bridge would."""

    def __init__(self, base, repo: Repo, script=None) -> None:
        self.store = base
        self.repo = repo
        self.script = list(script or [])
        self.inputs: list[dict] = []
        self.seq = 0
        self.on_request = None

    def __call__(self, method, url, body, headers, timeout):
        if url.endswith("/cancel"):
            return 202, b"{}"
        if self.on_request is not None:
            self.on_request()
        doc = json.loads(body)
        given = doc["input"]
        self.inputs.append(given)
        turn = self.script.pop(0) if self.script else "commit"
        git(self.repo.wt, "reset", "-q", "--hard", self.repo.start)
        if turn == "none":
            head, status = self.repo.start, "no_changes"
        elif callable(turn):
            head, status = turn(self.repo), "completed"
        else:
            head, status = self.repo.commit("fix x", {"src/app.py": "x = 3\n"}), "completed"
        result = {
            "schema": "cultureagent.bridge.result/v1",
            "backend": "qwen",
            "status": status,
            "summary": "made x 3" if status == "completed" else "nothing to change",
            "head_before": self.repo.start,
            "head_after": head,
            "commits": [] if head == self.repo.start else [{"sha": head}],
            "dirty": False,
            "worktree": str(self.repo.wt),
            "threads_addressed": [
                {"thread_id": "PRRT_1", "commit": head, "reply": "Done"},
                {"thread_id": "PRRT_2", "commit": head, "reply": "untrusted"},
            ],
        }
        self.seq += 1
        event = {"kind": "completed", "sequence": self.seq, "payload": {"result": result}}
        inv = doc["callback"]["url"].rsplit("/", 2)[-2]
        assert record_bridge_event(self.store, inv, doc["callback"]["token"], event) == "recorded"
        return 202, json.dumps({"invocation_id": f"qinv-{self.seq}"}).encode()


def qwen_adapter(base, transport, clock) -> BridgeAgentActor:
    actor = Actor.from_dict(copy.deepcopy(AGENT_ACTOR))
    return BridgeAgentActor(
        base,
        bridge_url=actor.params["bridge_url"],
        callback_url=actor.params["callback_url"],
        token="t",
        resolve_secret=lambda ref: ref,
        defaults={"mode": "yolo"},
        actor_id=actor.id,
        transport=transport,
        clock=clock,
        actor_doc=copy.deepcopy(AGENT_ACTOR),
    )


class SonarAPI:
    """SonarCloud's Web API as a transport double: a passing gate unless told otherwise."""

    def __init__(self) -> None:
        self.gate = {"projectStatus": {"status": "OK", "conditions": []}}
        self.issues: list[dict] = []
        self.requests: list[str] = []

    def __call__(self, method, url, headers, timeout):
        self.requests.append(url)
        if "/api/qualitygates/" in url:
            return 200, json.dumps(self.gate).encode()
        if "/api/issues/search" in url:
            return 200, json.dumps({"issues": self.issues, "paging": {"total": 0}}).encode()
        return 404, b"{}"


class PushRecorder(FakeActor):
    def __init__(self) -> None:
        super().__init__(default=lambda inp, ctx: {"head_after": inp["commit_sha"], "pushed": True})


class GitHubDouble:
    """The GitHub REST API the real push port talks to, for PR o/r#7 on a local remote."""

    def __init__(self, world: ChainWorld) -> None:
        self.world = world
        self.calls: list[tuple[str, str]] = []
        self.head_override: str | None = None

    def __call__(self, method, url, headers, body, timeout):
        path = url.removeprefix("https://api.github.com")
        self.calls.append((method, path))
        exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if path.endswith("/access_tokens"):
            return 201, json.dumps({"token": "ghs" + "_" + "x" * 36, "expires_at": exp}).encode()
        if path == f"/repos/{REPO}/pulls/7":
            doc = {
                "state": "open",
                "head": {
                    "ref": "fix",
                    "sha": self.head_override or self.world.remote_head(),
                    "repo": {"full_name": REPO},
                },
                "base": {"ref": "main", "sha": self.world.repo.base, "repo": {"full_name": REPO}},
            }
            return 200, json.dumps(doc).encode()
        return 404, b"{}"


class ChainWorld:
    """Two nodes on one store: spark (App, reviewer) and spark2 (agent, gate, push)."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        reviews=None,
        turns=None,
        real_push_pem: str | None = None,
        disabled: tuple[str, ...] = (),
        verdict_port: Any = None,
    ) -> None:
        self.tmp = tmp_path
        self.repo = Repo(tmp_path, gate_yaml([PASSING]))
        self.c = Cluster("spark", "spark2")
        base = self.c.base
        enrol_online(base, self.c.clock, machine("spark"), machine("spark2"))
        base.put("actors", copy.deepcopy(APP_ACTOR))
        base.put("actors", copy.deepcopy(AGENT_ACTOR))
        for actor in bundle().actors:
            base.put("actors", actor.to_dict())
        for rid, doc in rule_docs().items():
            base.put("rules", {**doc, "enabled": rid not in disabled})
        for doc in workflow_docs().values():
            base.put("workflows", doc)
        seed(base, CHAIN_VARIABLES)
        self.qwen = QwenBridge(base, self.repo, turns)
        self.reviewer = ReviewerBridge(base, reviews)
        self.sonar = SonarAPI()
        self.runner = LocalRunner()

        def head(inp, ctx):
            return {"head_sha": self.moved_head or self.repo.start, "base_sha": self.repo.base}

        self.moved_head: str | None = None
        gate = GatePort(
            base,
            run_as=self.runner,
            bundle_dir=tmp_path / "bundles",
            clock=self.c.clock,
            pr_lookup=FakeActor(default=head),
        )
        self.github = None
        if real_push_pem is not None:
            self._remote()
            self.github = GitHubDouble(self)
            self.push = GitHubPushPort(
                base,
                transport=self.github,
                secrets=lambda ref: real_push_pem,
                git_base=f"file://{self.tmp / 'remotes'}",
                clock=self.c.clock,
            )
        else:
            self.push = PushRecorder()
        self.reply = FakeActor(default=lambda inp, ctx: {"comment_id": 1, "resolved": True})
        self.comment = FakeActor(default=lambda inp, ctx: {"comment_id": 2})
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
        threads._app = lambda actor_id, conn, allowed: self.app
        ports = {
            "action:github.pr_head": FakeActor(default=head),
            "action:github.push": self.push,
            "action:github.review_reply": self.reply,
            "action:github.comment": self.comment,
            "action:noop": FakeActor(),
            "code": BuiltinCodePort(
                {
                    "gate": gate,
                    "review": verdict_port or ReviewVerdictPort(base, clock=self.c.clock),
                    "github.threads": threads,
                    "github.threads_addressed": AddressedThreadsPort(),
                    "sonar.gate_issues": SonarGateIssuesPort(
                        client=lambda: SonarCloud(transport=self.sonar)
                    ),
                }
            ),
        }

        def agent_for(actor):
            if actor.id == "codex-reviewer":
                return reviewer_adapter(base, actor, self.reviewer, self.c.clock)
            return qwen_adapter(base, self.qwen, self.c.clock)

        for host in ("spark", "spark2"):
            self.c.nodes[host] = self.c.node(host, actors=ports, adapters={"agent": agent_for})
        self.c.start()

    # ------------------------------------------------------------------ the remote

    def _remote(self) -> None:
        remote = self.tmp / "remotes" / "o" / "r.git"
        remote.parent.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        git(self.repo.wt, "push", "-q", str(remote), f"{self.repo.start}:refs/heads/fix")
        self.remote = remote

    def remote_head(self) -> str:
        return git(self.remote, "rev-parse", "refs/heads/fix")

    # ------------------------------------------------------------------ driving

    def fire(self, **data) -> None:
        """Settle red checks on PR o/r#7 at the PR head; run the chain to its end."""
        facts = pr_facts(
            head_sha=self.repo.start,
            base_sha=self.repo.base,
            conclusion="failure",
            state="open",
            **data,
        )
        self.c.publish(envelope(1, type="github.pr.checks_settled", data=facts))
        self.run_chain()

    def run_chain(self, rounds: int = 40) -> None:
        for _ in range(rounds):
            self.cycle(1)
            self.c.clock.advance(301)  # past any quiet period

    def cycle(self, rounds: int = 1) -> None:
        for _ in range(rounds):
            reports = self.c.cycle()
            assert all(not r.errors for r in reports.values()), reports

    # ------------------------------------------------------------------ reading

    def runs(self, rule_id: str) -> list[dict]:
        docs = self.c.base.find(RUNS_COLLECTION, {"rule_id": rule_id})
        return sorted(docs, key=lambda d: d["created_at"])

    def run_of(self, workflow_id: str) -> list[dict]:
        docs = self.c.base.find(RUNS_COLLECTION, {"workflow_id": workflow_id})
        return sorted(docs, key=lambda d: d["created_at"])

    def comments(self) -> list[str]:
        return [c[1]["body"] for c in self.comment.calls]
