"""Green PR merge prompt end to end (green-pr-merge-prompt t8): surfaces only, rules in the test.

A ``github.pr.checks_settled`` success starts the prompt workflow (id ``merge_prompt``):
``github.pr`` reads the PR, then ``discord.message`` posts it with one success button whose
value (repo, number, sha) is kept in ``discord_buttons`` (d1) with the run that posted it (d2).
The operator's click is the ``discord.button.clicked`` event the gateway records
(``button_data`` plus the stored value and poster); it starts the ``merge`` workflow. For the
test the release step pins both digests (the prompt in ``merge_prompt``, the merge workflow in
``merge``): ``github.merge`` squash-merges at the prompted sha and deletes the same-repo
branch, then ``discord.reply`` answers the clicker privately. One node
runs it on doubles of GitHub and Discord. Nothing here is committed configuration (c12, c24).
"""

from __future__ import annotations

import copy
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from culture_rules.actors import trusted  # noqa: E402
from culture_rules.apps.discord_buttons import (  # noqa: E402
    BUTTONS_COLLECTION,
    button_value,
    stored_button,
)
from culture_rules.apps.discord_commands import (  # noqa: E402
    BUTTON_INTERACTION,
    remember_interaction,
)
from culture_rules.apps.discord_gateway import button_data  # noqa: E402
from culture_rules.engine.runs import RUNS_COLLECTION  # noqa: E402
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document  # noqa: E402
from culture_rules.model.discord_filters import BUTTON_CLICKED  # noqa: E402
from culture_rules.node.actions.discord_reply import DiscordReplyAction  # noqa: E402
from culture_rules.node.actions.github import GitHubPrPort  # noqa: E402
from culture_rules.node.actions.github_merge import GitHubMergePort  # noqa: E402
from culture_rules.node.actions.message import DiscordMessageAction  # noqa: E402
from culture_rules.node.checks_settle import SOURCE as SETTLE_SOURCE  # noqa: E402
from culture_rules.node.daemon import Node, NodeOptions  # noqa: E402
from culture_rules.node.runner import NoopAction  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.apps.test_discord_gateway import discord_actor  # noqa: E402
from tests.engine.run_helpers import Clock, enrol_online, machine  # noqa: E402
from tests.events.fakes import FakeEventSource, envelope  # noqa: E402
from tests.node.test_node import BEATS  # noqa: E402
from tests.rules.test_discord_library_bundle import rule, workflow  # noqa: E402
from tests.rules.test_discord_reaction_flow import action, edge, port, step  # noqa: E402

SETTLED = "github.pr.checks_settled"
START = datetime.now(UTC).replace(microsecond=0)  # the App client checks deadlines on real time
REPO, NUMBER, SHA, MERGED_SHA = "acme/widgets", 7, "a" * 40, "b" * 40
GH, BOT, APP = "gh-app", "discord-bot", "555"
CHANNEL, GUILD, MESSAGE = "4242", "1", "9001"
OPERATOR, OTHER = "111", "222"
TOKEN = "interaction-token-" + "x" * 40
FAKE_BEARER = "ghs" + "_" + "FAKEINSTALLATIONTOKEN0123456789abcdefABCD"


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


# --------------------------------------------------------------------------- the configuration


def _workflow(id_: str, inputs: list[dict], steps: list[dict], edges: list[dict]) -> dict:
    wf = copy.deepcopy(workflow().to_dict())
    wf.update(id=id_, name=id_, inputs=inputs, steps=steps, edges=edges, outputs=[])
    return wf


def prompt_workflow(id_: str = trusted.ROLE_MERGE_PROMPT) -> dict:
    """Read the PR, then post it with a Merge button whose value is the PR's identity. Its id
    is its trust role's name (``merge_prompt``), as every trusted workflow's is."""
    read = step(
        "pr",
        action("github.pr", {"actor": GH, "repo": "inputs.repo", "number": "inputs.number"}),
        inputs=[port("repo"), port("number", "integer")],
        outputs=[port("title"), port("url"), port("author")],
    )
    button = {
        "label": "Merge",
        "style": "success",
        "custom_id": "merge",
        "value": {"repo": "inputs.repo", "number": "inputs.number", "sha": "inputs.sha"},
    }
    post = step(
        "post",
        action(
            "discord.message",
            {
                "actor": BOT,
                "channel": CHANNEL,
                "text": "{{inputs.repo}}#{{inputs.number}} is green: {{inputs.title}} "
                "by {{inputs.author}} {{inputs.url}}",
                "buttons": [button],
            },
        ),
        inputs=[
            port("repo"),
            port("number", "integer"),
            port("sha"),
            port("title"),
            port("url"),
            port("author"),
        ],
        outputs=[port("message_id"), port("custom_ids", "array")],
    )
    edges = [
        edge("inputs", "repo", "pr", "repo"),
        edge("inputs", "number", "pr", "number"),
        *(edge("inputs", p, "post", p) for p in ("repo", "number", "sha")),
        *(edge("pr", p, "post", p) for p in ("title", "url", "author")),
    ]
    inputs = [port("repo"), port("number", "integer"), port("sha")]
    return _workflow(id_, inputs, [read, post], edges)


def merge_workflow() -> dict:
    """The click's workflow, id ``merge``: merge at the stored sha, then answer privately."""
    merge = step(
        "merge",
        action(
            "github.merge",
            {
                "actor": GH,
                "repo": "inputs.repo",
                "number": "inputs.number",
                "sha": "inputs.sha",
                "delete_branch": True,
            },
        ),
        inputs=[port("repo"), port("number", "integer"), port("sha")],
        outputs=[port("merged", "boolean"), port("sha"), port("branch_deleted", "boolean")],
    )
    reply = step(
        "reply",
        action(
            "discord.reply",
            {"actor": BOT, "event_id": "inputs.event_id", "text": "Merged as {{inputs.sha}}"},
        ),
        inputs=[port("event_id"), port("sha")],
        outputs=[port("replied", "boolean")],
    )
    edges = [
        *(edge("inputs", p, "merge", p) for p in ("repo", "number", "sha")),
        edge("inputs", "event_id", "reply", "event_id"),
        edge("merge", "sha", "reply", "sha"),
    ]
    inputs = [port("repo"), port("number", "integer"), port("sha"), port("event_id")]
    return _workflow("merge", inputs, [merge, reply], edges)


def _rule(id_: str, trigger: dict, workflow_id: str, inputs: dict, condition=None) -> dict:
    doc = rule().to_dict()
    doc.update(id=id_, name=id_, enabled=True, trigger=trigger, condition=condition)
    doc["concurrency_key"] = None
    doc["workflow"] = {"id": workflow_id, "inputs": inputs, "version": None}
    return doc


def _eq(field: str, literal) -> dict:
    return {"op": "compare", "cmp": "==", "left": {"field": field}, "right": {"literal": literal}}


def prompt_rule(id_: str = "green-pr-prompt", workflow_id: str = trusted.ROLE_MERGE_PROMPT) -> dict:
    """Green, not a draft, not a fork (the data facts the settler carries)."""
    condition = {
        "op": "and",
        "args": [
            _eq("data.conclusion", "success"),
            _eq("data.draft", False),
            {
                "op": "compare",
                "cmp": "==",
                "left": {"field": "data.head_repo"},
                "right": {"field": "data.base_repo"},
            },
        ],
    }
    inputs = {
        "repo": "trigger.data.repository",
        "number": "trigger.data.number",
        "sha": "trigger.data.head_sha",
    }
    trigger = {"kind": "event", "params": {"type": SETTLED}}
    return _rule(id_, trigger, workflow_id, inputs, condition)


def click_rule() -> dict:
    trigger = {
        "kind": "event",
        "params": {
            "type": BUTTON_CLICKED,
            "channels": [CHANNEL],
            "users": [OPERATOR],
            "custom_id": "merge:",
        },
    }
    inputs = {
        "repo": "trigger.data.value.repo",
        "number": "trigger.data.value.number",
        "sha": "trigger.data.value.sha",
        "event_id": "trigger.id",
    }
    return _rule("merge-on-click", trigger, "merge", inputs)


# --------------------------------------------------------------------------- the doubles


class GitHub:
    """The token exchange, the PR read, the squash merge and the ref delete."""

    def __init__(self, *, head_sha: str = SHA, head_repo: str = REPO) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.head_sha, self.head_repo = head_sha, head_repo
        self.merged = False

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, urlsplit(url).path, json.loads(body) if body else None))
        if url.endswith("/access_tokens"):
            exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return 201, json.dumps({"token": FAKE_BEARER, "expires_at": exp}).encode()
        if method == "PUT" and url.endswith("/merge"):
            if json.loads(body)["sha"] != self.head_sha:
                return 409, json.dumps({"message": "Head branch was modified"}).encode()
            self.merged = True
            return 200, json.dumps({"sha": MERGED_SHA, "merged": True}).encode()
        if method == "DELETE":
            return 204, b""
        if method == "GET" and "/pulls/" in url:
            pull = {
                "number": NUMBER,
                "title": "Fix the widget",
                "html_url": f"https://github.com/{REPO}/pull/{NUMBER}",
                "user": {"login": "ori"},
                "draft": False,
                "state": "closed" if self.merged else "open",
                "merged": self.merged,
                "merge_commit_sha": MERGED_SHA if self.merged else None,
                "head": {
                    "sha": self.head_sha,
                    "ref": "fix/x",
                    "repo": {"full_name": self.head_repo},
                },
                "base": {"ref": "main", "sha": "c" * 40, "repo": {"full_name": REPO}},
            }
            return 200, json.dumps(pull).encode()
        return 404, b"{}"

    def puts(self) -> list[dict]:
        return [b for m, _, b in self.calls if m == "PUT"]

    def deletes(self) -> list[str]:
        return [p for m, p, _ in self.calls if m == "DELETE"]


class Discord:
    """Discord's REST API: the post into CHANNEL and the interaction follow-up."""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.followups: list[tuple[str, dict]] = []

    def __call__(self, url, data, headers, timeout, method=None):
        path = urlsplit(url).path
        body = json.loads(data) if data else None
        if path.endswith(f"/channels/{CHANNEL}/messages") and body is not None:
            self.posts.append(body)
            return 200, {"id": MESSAGE, "channel_id": CHANNEL}, {}
        if "/webhooks/" in path:
            self.followups.append((path, body))
            return 200, {"id": "f1"}, {}
        if path.endswith(f"/channels/{CHANNEL}"):
            return 200, {"id": CHANNEL, "guild_id": GUILD}, {}
        return 404, {"message": "Unknown"}, {}


def github_actor() -> dict:
    return {
        "id": GH,
        "name": GH,
        "kind": "app",
        "enabled": True,
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


class World:
    def __init__(self, pem: str, github: GitHub | None = None) -> None:
        self.clock = Clock(START)
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        bot = discord_actor(BOT, guild=GUILD, events=(BUTTON_CLICKED,), channels=(CHANNEL,))
        bot["params"]["actions"] = ["discord.message", "discord.reply"]
        self.base.put("actors", bot)
        self.base.put("actors", github_actor())
        for doc in (prompt_workflow(), merge_workflow()):
            self.base.put("workflows", doc)
        for doc in (prompt_rule(), click_rule()):
            self.base.put("rules", doc)
        self.github, self.discord = github or GitHub(), Discord()
        self.source = FakeEventSource(name="sub@spark")
        secret = lambda ref: f"secret-for-{ref}"  # noqa: E731
        gh_secret = lambda ref: pem  # noqa: E731
        ports = {
            "action:noop": NoopAction(),
            "action:github.pr": GitHubPrPort(
                self.base, transport=self.github, secrets=gh_secret, clock=self.clock
            ),
            "action:github.merge": GitHubMergePort(
                self.base, transport=self.github, secrets=gh_secret, clock=self.clock
            ),
            "action:discord.message": DiscordMessageAction(
                self.base, resolve_secret=secret, transport=self.discord
            ),
            "action:discord.reply": DiscordReplyAction(
                self.base, transport=self.discord, clock=self.clock
            ),
        }
        self.node = Node(
            self.base.peer(),
            "spark",
            actors=ports,
            event_source=self.source,
            clock=self.clock,
            heartbeat_options=BEATS,
            options=NodeOptions(listen_gateways=False),  # events come from the fake source
        )
        self.node.start()
        self.sent = 0

    def _publish(self, type_: str, data: dict) -> str:
        self.sent += 1
        env = envelope(self.sent, type=type_, source="test://", data=data)
        self.source.publish(env)
        return env["id"]

    def _settle(self) -> None:
        for _ in range(30):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors

    def green(self, **data) -> None:
        facts = {
            "repository": REPO,
            "head_sha": SHA,
            "number": NUMBER,
            "pr_numbers": [NUMBER],
            "conclusion": "success",
            "settled_by": "all_completed",
            "draft": False,
            "head_repo": REPO,
            "base_repo": REPO,
            **data,
        }
        # d25: only the checks settler writes this type, straight into the store from its
        # internal source (the bus quarantines it); write it as the settler would
        self.sent += 1
        env = envelope(self.sent, type=SETTLED, source=SETTLE_SOURCE, data=facts)
        self.base.insert(EVENTS_COLLECTION, event_document(env, host="checks-settle"))
        self._settle()

    def posted_id(self) -> str:
        (doc,) = self.base.find(BUTTONS_COLLECTION, {})
        return doc["custom_id"]

    def click(self, user: str = OPERATOR, custom_id: str | None = None) -> str:
        """What the gateway records for a click: button_data plus the stored value and poster,
        and the interaction token kept apart under the event id."""
        custom_id = custom_id or self.posted_id()
        raw = {
            "custom_id": custom_id,
            "message_id": MESSAGE,
            "channel_id": CHANNEL,
            "guild_id": GUILD,
            "interaction_id": f"i{self.sent + 1}",
            "user_id": user,
            "username": "ori" if user == OPERATOR else "dana",
        }
        stored = stored_button(self.base, BOT, custom_id, now=self.clock())
        data = {
            **button_data(raw),
            "actor": BOT,
            "value": stored["value"] if stored else None,
            "posted_by": stored["posted_by"] if stored else None,
        }
        event_id = f"evt_{self.sent + 1}"
        remember_interaction(
            self.base, event_id, TOKEN, APP, BOT, now=self.clock(), kind=BUTTON_INTERACTION
        )
        assert self._publish(BUTTON_CLICKED, data) == event_id
        self._settle()
        return event_id

    def runs(self, rule_id: str) -> list[dict]:
        docs = self.base.find(RUNS_COLLECTION, {"rule_id": rule_id})
        return sorted(docs, key=lambda d: d["created_at"])


@pytest.fixture(autouse=True)
def pin_test_app(monkeypatch):
    """Pin the test App actor's digest, as a release pins ``github-app``'s."""
    pinned = {
        **trusted.TRUSTED_ACTOR_DIGESTS,
        GH: frozenset({trusted.actor_digest(github_actor())}),
    }
    monkeypatch.setattr(trusted, "TRUSTED_ACTOR_DIGESTS", pinned)


@pytest.fixture
def trust_merge(monkeypatch):
    """The release step: pin the live prompt workflow's v2 digest in the merge_prompt role
    and the live merge workflow's in the merge role (d2: two digests)."""
    roles = dict(trusted.TRUSTED_WORKFLOWS_V2)
    roles[trusted.ROLE_MERGE] = frozenset({trusted.workflow_digest_v2(merge_workflow())})
    roles[trusted.ROLE_MERGE_PROMPT] = frozenset({trusted.workflow_digest_v2(prompt_workflow())})
    monkeypatch.setattr(trusted, "TRUSTED_WORKFLOWS_V2", roles)


def _ok(run: dict) -> None:
    assert run["status"] == "succeeded", run.get("error") or run["steps"]


# --------------------------------------------------------------------------- the flow


def test_the_configured_workflows_validate():
    from culture_rules.model.rule import Rule
    from culture_rules.model.validate import validate
    from culture_rules.model.workflow import Workflow

    assert validate(Workflow.from_dict(prompt_workflow())) == []
    assert validate(Workflow.from_dict(merge_workflow())) == []
    assert validate(Rule.from_dict(prompt_rule())) == []
    assert validate(Rule.from_dict(click_rule())) == []


def test_a_green_pr_is_prompted_with_a_button_that_keeps_its_meaning(pem):
    world = World(pem)
    world.green()
    (run,) = world.runs("green-pr-prompt")
    _ok(run)
    (post,) = world.discord.posts
    assert "Fix the widget" in post["content"] and f"{REPO}#{NUMBER}" in post["content"]
    (row,) = post["components"]
    (button,) = row["components"]
    assert button["style"] == 3 and button["label"] == "Merge"
    assert button["custom_id"].startswith("merge:")
    # the meaning is in the db, not in the id (d1)
    assert SHA not in button["custom_id"] and REPO not in button["custom_id"]
    stored = button_value(world.base, BOT, button["custom_id"], now=world.clock())
    assert stored == {"repo": REPO, "number": NUMBER, "sha": SHA}
    # and who posted it (d2): the prompt run and its workflow
    poster = stored_button(world.base, BOT, button["custom_id"], now=world.clock())["posted_by"]
    assert poster == {"run_id": run["id"], "workflow_id": trusted.ROLE_MERGE_PROMPT, "step": "post"}


def test_the_operator_s_click_squash_merges_the_prompted_sha_and_answers_privately(
    pem, trust_merge
):
    world = World(pem)
    world.green()
    world.click()
    (run,) = world.runs("merge-on-click")
    _ok(run)
    assert world.github.puts() == [{"sha": SHA, "merge_method": "squash"}]
    assert world.github.deletes() == [f"/repos/{REPO}/git/refs/heads/fix/x"]
    ((path, body),) = world.discord.followups
    assert path == f"/api/v10/webhooks/{APP}/{TOKEN}" or path.endswith(f"/webhooks/{APP}/{TOKEN}")
    assert body["flags"] == 64 and MERGED_SHA in body["content"]


# --------------------------------------------------------------------------- nothing merges


def test_a_click_by_anyone_else_starts_nothing(pem, trust_merge):
    world = World(pem)
    world.green()
    world.click(user=OTHER)
    assert world.runs("merge-on-click") == []
    assert world.github.puts() == []


def test_a_head_that_moved_since_the_prompt_merges_nothing(pem, trust_merge):
    github = GitHub()
    world = World(pem, github)
    world.green()
    github.head_sha = "d" * 40  # a new commit landed after the prompt
    world.click()
    (run,) = world.runs("merge-on-click")
    assert run["status"] == "failed"
    assert github.puts() == [] and github.deletes() == []
    assert world.discord.followups == []


def test_an_untrusted_merge_workflow_merges_nothing(pem):
    world = World(pem)  # no digest pinned in the merge role: the shipped state
    world.green()
    world.click()
    (run,) = world.runs("merge-on-click")
    assert run["status"] == "failed"
    assert "workflow_not_trusted" in json.dumps(run.get("error") or run["steps"])
    assert world.github.puts() == []


def test_an_untrusted_prompt_workflow_merges_nothing(pem, trust_merge, monkeypatch):
    roles = dict(trusted.TRUSTED_WORKFLOWS_V2)
    roles[trusted.ROLE_MERGE_PROMPT] = frozenset()  # the merge role pinned, the prompt not
    monkeypatch.setattr(trusted, "TRUSTED_WORKFLOWS_V2", roles)
    world = World(pem)
    world.green()
    world.click()
    (run,) = world.runs("merge-on-click")
    assert run["status"] == "failed"
    assert "prompt_not_trusted" in json.dumps(run.get("error") or run["steps"])
    assert world.github.puts() == []


def test_a_button_posted_by_an_untrusted_workflow_merges_nothing(pem, trust_merge):
    """A second workflow posts a button with the same ``merge`` prefix; the operator's click
    on it is admitted by the click rule and starts the trusted merge workflow, but the
    button's origin is not a trusted prompt, so nothing merges."""
    world = World(pem)
    world.base.put("workflows", prompt_workflow("rogue-prompt"))
    world.base.put("rules", prompt_rule("rogue-rule", "rogue-prompt"))
    real = world.base.get("rules", "green-pr-prompt")
    world.base.put("rules", {**real, "enabled": False})  # only the rogue posts
    world.green()
    assert world.runs("green-pr-prompt") == []
    (posting,) = world.runs("rogue-rule")
    _ok(posting)
    (row,) = world.discord.posts[0]["components"]
    assert row["components"][0]["custom_id"].startswith("merge:")
    world.click()
    (run,) = world.runs("merge-on-click")
    assert run["status"] == "failed"
    assert "prompt_not_trusted" in json.dumps(run.get("error") or run["steps"])
    assert world.github.puts() == [] and world.github.deletes() == []
    assert world.discord.followups == []


def test_an_unpinned_app_actor_merges_nothing(pem, trust_merge, monkeypatch):
    unpinned = {k: v for k, v in trusted.TRUSTED_ACTOR_DIGESTS.items() if k != GH}
    monkeypatch.setattr(trusted, "TRUSTED_ACTOR_DIGESTS", unpinned)
    world = World(pem)
    world.green()
    world.click()
    (run,) = world.runs("merge-on-click")
    assert run["status"] == "failed"
    assert "actor_not_trusted" in json.dumps(run.get("error") or run["steps"])
    assert world.github.puts() == []


def test_a_draft_or_a_fork_or_a_red_pr_is_not_prompted(pem):
    for facts in ({"draft": True}, {"head_repo": "someone/widgets"}, {"conclusion": "failure"}):
        world = World(pem)
        world.green(**facts)
        assert world.runs("green-pr-prompt") == [], facts
        assert world.discord.posts == []


# --------------------------------------------------------------------------- surfaces only


REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 B607 - fixed arguments, no shell
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def _changed_files() -> list[str]:
    inside = _git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("not inside a git checkout")
    if _git("rev-parse", "--verify", "--quiet", "origin/main").returncode != 0:
        pytest.skip("origin/main is unknown here")
    diff = _git("diff", "origin/main", "--name-only")
    if diff.returncode != 0:
        pytest.skip(f"git diff failed: {diff.stderr.strip()}")
    return diff.stdout.split()


def test_the_delivery_commits_no_rule_or_workflow():  # c12, h7
    bundles = [f for f in _changed_files() if f.startswith("docs/rules/")]
    assert [f for f in bundles if "/rules/" in f[len("docs/rules/") :] or "/workflows/" in f] == []


def test_no_flow_specific_value_is_coded():  # c24, h16
    words = ("green-pr-prompt", "merge-on-click", "#spark")
    hits = []
    for top in ("culture_rules", "web/src"):
        for path in (REPO_ROOT / top).rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits += [f"{path.relative_to(REPO_ROOT)}: {w}" for w in words if w in text]
    assert hits == []
