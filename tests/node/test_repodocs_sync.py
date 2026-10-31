"""repodocs.sync action port: push every top-level repo's devague record into culture-library
(culture-rules#124, contract culture-library ``docs/api/repodocs.md``).

The repos are real throwaway git repos under ``tmp_path`` (git runs through the port's default
runner, wrapped so a test can count calls); the library is a fake transport. Nothing here
talks to a network or a grant store.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions import repodocs as repodocs_mod
from culture_rules.node.actions.repodocs import MAX_BYTES, RepodocsSyncPort, spec_slug
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
EPOCH = datetime(2026, 10, 1, tzinfo=UTC)
DEADLINE = NOW + timedelta(minutes=30)
BASE = "http://127.0.0.1:8796"
PUSH_URL = BASE + "/api/repodocs"
ACTOR_ID = "culture-library-repodocs"
KEY_REF = "grant:LIBRARY_REPODOCS_PUSH_API_KEY"
KEY_VALUE = "clk_fake_value_for_tests"
REMOTE = "https://github.com/agentculture/demo.git"
SPEC = "docs/specs/2026-10-01-foo.md"
PLAN = "docs/plans/2026-10-01-foo.md"
FRAME = ".devague/frames/foo.json"
WORKING = "working"
OUTPUT_KEYS = {"repos", "pushed", "skipped", "tombstoned", "errors", "duration_s", "stopped"}
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@localhost",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@localhost",
    "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": "/nonexistent",
}


# ------------------------------------------------------------------ git helpers


class Repo:
    """A throwaway git repo with a fixed identity and increasing commit dates."""

    _tick = 0

    def __init__(self, path: Path, *, remote: str | None = REMOTE, origin_head: bool = True):
        self.path = path
        self.origin_head = origin_head
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        if remote:
            self.git("remote", "add", "origin", remote)

    def git(self, *argv: str) -> str:
        Repo._tick += 1
        stamp = (EPOCH + timedelta(minutes=Repo._tick)).isoformat()
        env = {**os.environ, **GIT_ENV, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        out = subprocess.run(
            ["git", *argv], cwd=self.path, env=env, check=True, capture_output=True, text=True
        )
        return out.stdout.strip()

    def write(self, rel: str, data: str | bytes) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            target.write_text(data)

    def commit(self, message: str, *paths: str) -> str:
        self.git("add", "-A", *(paths or ["."]))
        self.git("commit", "-q", "-m", message)
        head = self.head()
        if self.origin_head and self.git("branch", "--show-current") == "main":
            self.publish()
        return head

    def publish(self) -> None:
        """Make origin/main the local main and origin/HEAD point at it (no network)."""
        self.git("update-ref", "refs/remotes/origin/main", "refs/heads/main")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    def head(self, ref: str = "HEAD") -> str:
        return self.git("rev-parse", ref)

    def time(self, ref: str = "HEAD") -> str:
        return self.git("log", "-1", "--format=%cI", ref)


# ------------------------------------------------------------------ the fake library


class FakeLibrary:
    """Records each push; answers 201, or a scripted status for a matching path."""

    def __init__(self, statuses: dict | None = None, *, raise_for: set | None = None):
        self.calls: list[dict] = []
        self.headers: list[dict] = []
        self.statuses = statuses or {}
        self.raise_for = raise_for or set()

    def __call__(self, method, url, headers, body, timeout):
        assert method == "POST"
        assert url == PUSH_URL
        payload = json.loads(body)
        if payload["path"] in self.raise_for:
            raise ConnectionRefusedError("down")
        self.calls.append(payload)
        self.headers.append(dict(headers))
        status = self.statuses.get((payload["path"], payload.get("tombstone", False)), 201)
        if status >= 400:
            return status, json.dumps({"detail": "nope", "code": "not_found"}).encode()
        return status, json.dumps({"created": status == 201}).encode()

    def by(self, path: str, commit: str | None = None) -> list[dict]:
        return [
            c
            for c in self.calls
            if c["path"] == path
            and (commit is None or (c["commit"] == WORKING) == (commit == WORKING))
        ]

    def content(self, call: dict) -> bytes:
        return base64.b64decode(call["content_b64"])


class StepClock:
    """Advances ``step`` seconds per reading (a run that takes time)."""

    def __init__(self, step: float = 0.0, start: datetime = NOW):
        self.now = start
        self.step = timedelta(seconds=step)

    def __call__(self) -> datetime:
        now = self.now
        self.now += self.step
        return now


# ------------------------------------------------------------------ the port


def _params(root: Path, **over):
    params = {
        "base_url": BASE,
        "api_key": KEY_REF,
        "repos_root": str(root),
        "state_path": str(root.parent / "state" / "repodocs.json"),
    }
    params.update(over)
    return {k: v for k, v in params.items() if v is not None}


def _store(params, *, kind="service"):
    store = MemoryStore()
    store.put(
        ACTORS_COLLECTION,
        {"id": ACTOR_ID, "name": ACTOR_ID, "kind": kind, "enabled": True, "params": params},
    )
    return store


def _ctx():
    return InvocationContext(run_id="run", step_id="sync", kind="action", host="spark")


def _sync(root: Path, library: FakeLibrary, *, clock=None, git=None, **over):
    port = RepodocsSyncPort(
        _store(_params(root, **over)),
        transport=library,
        secret_runner=lambda name: KEY_VALUE,
        clock=clock or StepClock(),
        git=git,
    )
    return port.invoke({"actor": ACTOR_ID}, "k", DEADLINE, context=_ctx())


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "git"
    base.mkdir()
    return base


def _demo(root: Path) -> Repo:
    repo = Repo(root / "demo")
    repo.write(SPEC, "# Foo spec\n")
    repo.write(PLAN, "# Foo plan\n")
    repo.write(FRAME, '{"slug": "foo"}\n')
    repo.write("src/app.py", "print('x')\n")
    repo.commit("first")
    return repo


# ------------------------------------------------------------------ commit pushes


def test_a_committed_file_is_pushed_at_the_default_branch_head(root):
    repo = _demo(root)
    library = FakeLibrary()
    result = _sync(root, library)
    assert result.outcome == COMPLETED, result.error
    (call,) = library.by(SPEC, "sha")
    assert call["remote"] == REMOTE
    assert call["commit"] == repo.head()
    assert call["default_branch_head"] == repo.head()
    assert call["committed_at"] == repo.time()
    assert library.content(call) == b"# Foo spec\n"
    assert "content" not in call
    assert "tombstone" not in call


def test_every_in_scope_prefix_is_pushed_and_nothing_else(root):
    repo = _demo(root)
    repo.write("docs/deliveries/2026-10-02-foo.md", "done\n")
    repo.write("docs/design/shot.png", b"\x89PNG\r\n\x1a\n\x00\xff")
    repo.write("docs/operations/runbook.md", "not in scope\n")
    repo.write("README.md", "not in scope\n")
    repo.commit("more")
    library = FakeLibrary()
    _sync(root, library)
    assert {c["path"] for c in library.calls} == {
        SPEC,
        PLAN,
        FRAME,
        "docs/deliveries/2026-10-02-foo.md",
        "docs/design/shot.png",
    }


def test_binary_content_round_trips_as_base64(root):
    repo = _demo(root)
    data = bytes(range(256)) * 3
    repo.write("docs/design/blob.bin", data)
    repo.commit("bin")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by("docs/design/blob.bin")
    assert library.content(call) == data


def test_the_push_carries_the_bearer_from_the_grant(root):
    _demo(root)
    library = FakeLibrary()
    _sync(root, library)
    assert library.headers
    for headers in library.headers:
        assert headers["Authorization"] == "Bearer " + KEY_VALUE
        assert headers["Content-Type"] == "application/json"


def test_a_committed_file_untouched_since_an_older_commit_still_names_the_head(root):
    repo = _demo(root)
    repo.write("other.txt", "unrelated\n")
    head = repo.commit("unrelated")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by(PLAN, "sha")
    assert call["commit"] == head == call["default_branch_head"]


def test_the_default_branch_is_origin_head_not_the_checked_out_branch(root):
    repo = _demo(root)
    main = repo.head()
    repo.git("checkout", "-q", "-b", "feature")
    repo.write(PLAN, "# Foo plan, on a branch\n")
    repo.commit("branch work")
    library = FakeLibrary()
    _sync(root, library)
    (committed,) = library.by(PLAN, "sha")
    assert committed["commit"] == main
    assert library.content(committed) == b"# Foo plan\n"
    (draft,) = library.by(PLAN, WORKING)  # the branch's version differs from main: a draft
    assert library.content(draft) == b"# Foo plan, on a branch\n"


def test_without_origin_head_the_current_branch_is_the_default(root):
    repo = Repo(root / "local", origin_head=False)
    repo.write(SPEC, "x\n")
    head = repo.commit("first")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by(SPEC, "sha")
    assert call["commit"] == head == call["default_branch_head"]


def test_a_repo_without_a_remote_sends_host_and_absolute_path(root):
    repo = Repo(root / "lonely", remote=None, origin_head=False)
    repo.write(PLAN, "x\n")
    repo.commit("first")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.calls
    assert call["remote"] == f"spark:{repo.path}"


@pytest.mark.parametrize("branch", ["main", "master"])
def test_without_origin_head_a_checked_out_feature_branch_is_not_the_default(root, branch):
    repo = Repo(root / "local", origin_head=False)
    if branch != "main":
        repo.git("checkout", "-q", "-b", branch)
    repo.write(PLAN, "# on the default branch\n")
    default = repo.commit("first")  # on an unborn repo, checkout -b renames main away
    repo.git("checkout", "-q", "-b", "feature")
    repo.write(PLAN, "# on a feature branch\n")
    repo.commit("branch work")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by(PLAN, "sha")
    assert call["commit"] == default == call["default_branch_head"]
    assert library.content(call) == b"# on the default branch\n"


@pytest.mark.parametrize("branch", ["main", "master"])
def test_without_origin_head_origin_main_wins_over_the_local_branch(root, branch):
    repo = Repo(root / "local", origin_head=False)
    repo.write(PLAN, "# pushed\n")
    pushed = repo.commit("first")
    repo.git("update-ref", f"refs/remotes/origin/{branch}", pushed)
    repo.write(PLAN, "# local only\n")
    repo.commit("not pushed yet")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by(PLAN, "sha")
    assert call["commit"] == pushed == call["default_branch_head"]


@pytest.mark.parametrize(
    "origin",
    ["/srv/git/demo.git", "./demo", "../demo.git", "~/repos/demo", "file:///srv/git/demo.git"],
)
def test_a_local_path_origin_is_identified_as_host_and_absolute_path(root, origin):
    repo = Repo(root / "mirror", remote=origin, origin_head=False)
    repo.write(PLAN, "x\n")
    repo.commit("first")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.calls
    assert call["remote"] == f"spark:{repo.path}"


def test_an_empty_repo_pushes_its_files_as_drafts(root):
    repo = Repo(root / "fresh")
    repo.write(FRAME, "{}\n")
    library = FakeLibrary()
    result = _sync(root, library)
    assert result.output["errors"] == 0
    (call,) = library.calls
    assert call["commit"] == WORKING


# ------------------------------------------------------------------ working drafts


def test_a_modified_tracked_file_is_pushed_as_the_working_draft(root):
    repo = _demo(root)
    repo.write(FRAME, '{"slug": "foo", "edited": true}\n')
    library = FakeLibrary()
    _sync(root, library)
    (draft,) = library.by(FRAME, WORKING)
    assert library.content(draft) == b'{"slug": "foo", "edited": true}\n'
    assert "committed_at" not in draft
    assert "default_branch_head" not in draft
    (committed,) = library.by(FRAME, "sha")
    assert library.content(committed) == b'{"slug": "foo"}\n'


def test_an_unmodified_file_has_no_draft(root):
    _demo(root)
    library = FakeLibrary()
    _sync(root, library)
    assert library.by(SPEC, WORKING) == []


def test_an_untracked_file_is_pushed_as_a_draft_only(root):
    repo = _demo(root)
    repo.write("docs/plans/2026-10-31-new.md", "new\n")
    library = FakeLibrary()
    _sync(root, library)
    (draft,) = library.by("docs/plans/2026-10-31-new.md")
    assert draft["commit"] == WORKING


def test_a_gitignored_devague_file_is_pushed_as_a_draft(root):
    repo = _demo(root)
    repo.write(".gitignore", ".devague/reviews/\n")
    repo.commit("ignore reviews")
    repo.write(".devague/reviews/r1.json", '{"review": 1}\n')
    library = FakeLibrary()
    _sync(root, library)
    (draft,) = library.by(".devague/reviews/r1.json")
    assert draft["commit"] == WORKING
    assert library.content(draft) == b'{"review": 1}\n'


def test_a_repo_that_ignores_devague_entirely_still_pushes_it(root):
    repo = Repo(root / "notion-agent")
    repo.write(".gitignore", ".devague\n")
    repo.write("README.md", "x\n")
    repo.commit("first")
    repo.write(FRAME, "{}\n")
    repo.write(".devague/questions/q.md", "why?\n")
    library = FakeLibrary()
    _sync(root, library)
    assert {(c["path"], c["commit"]) for c in library.calls} == {
        (FRAME, WORKING),
        (".devague/questions/q.md", WORKING),
    }


def test_a_discarded_draft_is_reset_to_the_committed_bytes(root):
    repo = _demo(root)
    repo.write(FRAME, "edited\n")
    _sync(root, FakeLibrary())
    repo.git("checkout", "--", FRAME)
    library = FakeLibrary()
    _sync(root, library)
    (draft,) = library.by(FRAME, WORKING)  # the library's draft is no longer ahead
    assert library.content(draft) == b'{"slug": "foo"}\n'


# ------------------------------------------------------------------ what is skipped


def test_worktree_dirs_symlinks_and_linked_worktrees_are_skipped(root, tmp_path):
    repo = _demo(root)
    for name in (".worktrees.demo", "demo-worktrees"):
        other = Repo(root / name / "x")
        other.write(SPEC, "nested\n")
        other.commit("nested")
        bare = Repo(root / name)
        bare.write(SPEC, "a worktree root\n")
        bare.commit("wt")
    outside = Repo(tmp_path / "elsewhere")
    outside.write(SPEC, "elsewhere\n")
    outside.commit("x")
    (root / "linked").symlink_to(outside.path)
    repo.git("worktree", "add", "-q", str(root / "demo-wt"), "-b", "wt")
    (root / "notes").mkdir()
    (root / "file.txt").write_text("x")
    library = FakeLibrary()
    result = _sync(root, library)
    assert result.output["repos"] == 1
    assert {c["remote"] for c in library.calls} == {REMOTE}
    assert all(library.content(c) != b"nested\n" for c in library.calls)


def test_symlinked_files_inside_a_repo_are_not_followed(root, tmp_path):
    _demo(root)
    secret = tmp_path / "secret.txt"
    secret.write_text("do not send\n")
    (root / "demo" / ".devague" / "link.txt").symlink_to(secret)
    library = FakeLibrary()
    _sync(root, library)
    assert library.by(".devague/link.txt") == []


# ------------------------------------------------------------------ project scope


def test_culture_yaml_domains_and_connected_are_sent_as_project(root):
    repo = _demo(root)
    repo.write(
        "culture.yaml",
        "suffix: demo\n"
        "domains: [library, 'media']  # inline\n"
        "connected:\n"
        "  - github.com/agentculture/culture-rules\n"
        '  - "github.com/agentculture/culture-library"\n'
        "backend: claude\n",
    )
    repo.commit("yaml")
    library = FakeLibrary()
    _sync(root, library)
    assert library.calls
    for call in library.calls:
        assert call["project"] == {
            "domains": ["library", "media"],
            "connected": [
                "github.com/agentculture/culture-rules",
                "github.com/agentculture/culture-library",
            ],
        }


def test_block_lists_at_the_keys_own_indent_are_read(root):
    repo = _demo(root)
    repo.write(
        "culture.yaml",
        "suffix: demo\n"
        "domains:\n"
        "- library\n"
        "# a comment\n"
        "- 'media'  # quoted\n"
        "connected:\n"
        "- github.com/agentculture/culture-rules\n"
        "backend: claude\n"
        "- not-a-domain\n",
    )
    repo.commit("yaml")
    library = FakeLibrary()
    _sync(root, library)
    assert library.calls
    for call in library.calls:
        assert call["project"] == {
            "domains": ["library", "media"],
            "connected": ["github.com/agentculture/culture-rules"],
        }


def test_connected_org_repo_becomes_a_github_url_and_bare_names_are_dropped(root, caplog):
    repo = _demo(root)
    repo.write(
        "culture.yaml",
        "connected:\n"
        "  - agentculture/culture-rules\n"
        "  - culture-library\n"
        "  - https://github.com/agentculture/steward\n"
        "  - github.com/agentculture/teken\n"
        "  - git@github.com:agentculture/devex.git\n",
    )
    repo.commit("yaml")
    library = FakeLibrary()
    with caplog.at_level("WARNING", logger=repodocs_mod.__name__):
        _sync(root, library)
    assert library.calls
    for call in library.calls:
        assert call["project"]["connected"] == [
            "https://github.com/agentculture/culture-rules",
            "https://github.com/agentculture/steward",
            "github.com/agentculture/teken",
            "git@github.com:agentculture/devex.git",
        ]
    assert any("culture-library" in r.getMessage() for r in caplog.records)


def test_a_repo_without_those_keys_sends_empty_lists(root):
    _demo(root)
    library = FakeLibrary()
    _sync(root, library)
    assert {json.dumps(c["project"]) for c in library.calls} == {
        json.dumps({"domains": [], "connected": []})
    }


def test_a_changed_project_is_resent_even_when_no_file_changed(root):
    repo = _demo(root)
    _sync(root, FakeLibrary())
    repo.write("culture.yaml", "domains:\n  - rules\n")
    library = FakeLibrary()
    result = _sync(root, library)
    assert len(library.calls) == 1, library.calls
    assert library.calls[0]["project"] == {"domains": ["rules"], "connected": []}
    assert result.output["errors"] == 0
    again = FakeLibrary()
    _sync(root, again)
    assert again.calls == []


# ------------------------------------------------------------------ idempotency


def test_an_unchanged_second_run_pushes_nothing(root):
    _demo(root)
    first = FakeLibrary()
    assert _sync(root, first).output["pushed"] == 3
    second = FakeLibrary()
    result = _sync(root, second)
    assert second.calls == []
    assert result.output["pushed"] == 0
    assert result.output["skipped"] == 3


def test_a_new_commit_pushes_only_the_changed_file(root):
    repo = _demo(root)
    _sync(root, FakeLibrary())
    repo.write(PLAN, "# Foo plan v2\n")
    head = repo.commit("v2")
    library = FakeLibrary()
    _sync(root, library)
    assert [(c["path"], c["commit"]) for c in library.calls] == [(PLAN, head)]


def test_a_failed_push_is_retried_next_run(root):
    _demo(root)
    first = FakeLibrary({(PLAN, False): 500})
    result = _sync(root, first)
    assert result.outcome == COMPLETED
    assert result.output["errors"] == 1
    second = FakeLibrary()
    _sync(root, second)
    assert [c["path"] for c in second.calls] == [PLAN]


def test_the_state_file_is_written_atomically_as_json(root):
    _demo(root)
    _sync(root, FakeLibrary())
    state_dir = root.parent / "state"
    assert [p.name for p in state_dir.iterdir()] == ["repodocs.json"]
    state = json.loads((state_dir / "repodocs.json").read_text())
    assert state["schema"] == 1
    assert str(root / "demo") in state["repos"]


def test_a_corrupt_state_file_starts_over(root):
    _demo(root)
    state = root.parent / "state" / "repodocs.json"
    state.parent.mkdir()
    state.write_text("{not json")
    library = FakeLibrary()
    result = _sync(root, library)
    assert result.outcome == COMPLETED
    assert len(library.calls) == 3


# ------------------------------------------------------------------ tombstones


def test_a_deleted_committed_file_is_tombstoned_at_the_new_head(root):
    repo = _demo(root)
    _sync(root, FakeLibrary())
    repo.git("rm", "-q", PLAN)
    head = repo.commit("drop plan")
    library = FakeLibrary()
    result = _sync(root, library)
    assert library.calls == [
        {
            "remote": REMOTE,
            "path": PLAN,
            "commit": head,
            "tombstone": True,
            "project": {"domains": [], "connected": []},
        }
    ]
    assert result.output["tombstoned"] == 1
    again = FakeLibrary()
    _sync(root, again)
    assert again.calls == []


def test_a_vanished_draft_is_tombstoned_as_working(root):
    repo = _demo(root)
    repo.write(".devague/questions/q.md", "?\n")
    _sync(root, FakeLibrary())
    (repo.path / ".devague/questions/q.md").unlink()
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.calls
    assert (call["path"], call["commit"], call["tombstone"]) == (
        ".devague/questions/q.md",
        WORKING,
        True,
    )


def test_a_tombstone_answered_404_counts_as_done(root):
    repo = _demo(root)
    _sync(root, FakeLibrary())
    repo.git("rm", "-q", PLAN)
    repo.commit("drop plan")
    library = FakeLibrary({(PLAN, True): 404})
    result = _sync(root, library)
    assert result.output["errors"] == 0
    assert result.output["tombstoned"] == 1
    again = FakeLibrary()
    _sync(root, again)
    assert again.calls == []


def _untrack_plan(repo: Repo) -> str:
    repo.git("rm", "-q", "--cached", PLAN)
    repo.git("commit", "-q", "-m", "untrack plan")
    repo.publish()
    return repo.head()


def test_a_file_deleted_on_main_but_still_on_disk_is_tombstoned_without_a_draft(root):
    repo = _demo(root)
    _sync(root, FakeLibrary())
    head = _untrack_plan(repo)
    library = FakeLibrary()
    result = _sync(root, library)
    assert [(c["path"], c["commit"], c.get("tombstone")) for c in library.calls] == [
        (PLAN, head, True)
    ]  # the leftover bytes equal the last committed version: no draft
    assert result.output["tombstoned"] == 1
    again = FakeLibrary()
    _sync(root, again)
    assert again.calls == []


def test_a_file_deleted_on_main_with_other_bytes_on_disk_is_tombstoned_then_drafted(root):
    repo = _demo(root)
    _sync(root, FakeLibrary())
    head = _untrack_plan(repo)
    repo.write(PLAN, "# Foo plan, left over and edited\n")
    library = FakeLibrary()
    _sync(root, library)
    assert [(c["path"], c["commit"], c.get("tombstone")) for c in library.calls] == [
        (PLAN, head, True),
        (PLAN, WORKING, None),
    ]
    assert library.content(library.calls[1]) == b"# Foo plan, left over and edited\n"
    again = FakeLibrary()
    _sync(root, again)
    assert again.calls == []
    (repo.path / PLAN).unlink()  # the leftover goes too: its draft is tombstoned
    gone = FakeLibrary()
    _sync(root, gone)
    assert [(c["path"], c["commit"], c.get("tombstone")) for c in gone.calls] == [
        (PLAN, WORKING, True)
    ]


def test_a_tombstoned_leftover_removed_from_disk_sends_nothing_more(root):
    repo = _demo(root)
    _sync(root, FakeLibrary())
    _untrack_plan(repo)
    _sync(root, FakeLibrary())
    (repo.path / PLAN).unlink()
    library = FakeLibrary()
    _sync(root, library)
    assert library.calls == []


# ------------------------------------------------------------------ limits and errors


def test_a_file_over_16_mib_is_counted_as_an_error_and_not_sent(root):
    repo = _demo(root)
    repo.write(".devague/huge.bin", b"\0" * (MAX_BYTES + 1))
    library = FakeLibrary()
    result = _sync(root, library)
    assert library.by(".devague/huge.bin") == []
    assert result.output["errors"] == 1
    assert result.output["pushed"] == 3


class ReadCounter:
    """Wraps ``_read_file`` and records the repo-relative path of every read."""

    def __init__(self, monkeypatch, repo_path: Path):
        self.reads: list[str] = []
        real = repodocs_mod._read_file

        def counted(path):
            self.reads.append(Path(path).relative_to(repo_path).as_posix())
            return real(path)

        monkeypatch.setattr(repodocs_mod, "_read_file", counted)


def test_a_changed_file_is_read_once_and_the_hashed_bytes_are_sent(root, monkeypatch):
    repo = _demo(root)
    repo.write(FRAME, '{"slug": "foo", "edited": true}\n')
    counter = ReadCounter(monkeypatch, repo.path)
    library = FakeLibrary()
    _sync(root, library)
    assert counter.reads.count(FRAME) == 1
    (draft,) = library.by(FRAME, WORKING)
    assert library.content(draft) == b'{"slug": "foo", "edited": true}\n'


def test_an_oversize_file_is_one_error_until_it_changes(root, monkeypatch):
    repo = _demo(root)
    huge = ".devague/huge.bin"
    repo.write(huge, b"\0" * (MAX_BYTES + 1))
    counter = ReadCounter(monkeypatch, repo.path)
    assert _sync(root, FakeLibrary()).output["errors"] == 1
    second = _sync(root, FakeLibrary())
    assert second.output["errors"] == 0
    assert huge not in counter.reads
    repo.write(huge, b"\0" * (MAX_BYTES + 2))
    assert _sync(root, FakeLibrary()).output["errors"] == 1


def test_a_stopped_run_keeps_the_stat_cache_of_the_files_it_did_not_reach(root, monkeypatch):
    repo = _demo(root)
    for n in range(6):
        repo.write(f".devague/frames/f{n}.json", f'{{"n": {n}}}\n')
    repo.commit("more frames")
    _sync(root, FakeLibrary())
    repo.write(".devague/frames/f0.json", '{"n": "zero, edited"}\n')
    repo.write(".devague/frames/f5.json", '{"n": "five, edited"}\n')
    stopped = _sync(root, FakeLibrary(), max_files=1)
    assert stopped.output["stopped"] is True
    counter = ReadCounter(monkeypatch, repo.path)
    library = FakeLibrary()
    _sync(root, library)
    assert [c["path"] for c in library.calls] == [".devague/frames/f5.json"]
    assert set(counter.reads) == {".devague/frames/f5.json"}


def test_a_stat_cache_entry_for_a_vanished_file_is_dropped(root):
    repo = _demo(root)
    repo.write(".devague/questions/q.md", "?\n")
    _sync(root, FakeLibrary())
    (repo.path / ".devague/questions/q.md").unlink()
    _sync(root, FakeLibrary())
    state = json.loads((root.parent / "state" / "repodocs.json").read_text())
    assert ".devague/questions/q.md" not in state["repos"][str(repo.path)]["stat"]


def test_the_run_deadline_stops_early_and_the_next_run_resumes(root):
    repo = _demo(root)
    for n in range(6):
        repo.write(f".devague/frames/f{n}.json", f'{{"n": {n}}}\n')
    repo.commit("more frames")
    first = FakeLibrary()
    result = _sync(root, first, clock=StepClock(step=1.0), max_seconds=5)
    assert result.outcome == COMPLETED
    assert result.output["stopped"] is True
    assert 0 < len(first.calls) < 9
    second = FakeLibrary()
    result2 = _sync(root, second)
    assert result2.output["stopped"] is False
    pushed = {c["path"] for c in first.calls} | {c["path"] for c in second.calls}
    assert len(pushed) == 9
    assert not {c["path"] for c in first.calls} & {c["path"] for c in second.calls}


def test_max_files_caps_the_pushes_per_run(root):
    _demo(root)
    library = FakeLibrary()
    result = _sync(root, library, max_files=2)
    assert len(library.calls) == 2
    assert result.output["stopped"] is True
    rest = FakeLibrary()
    _sync(root, rest)
    assert len(rest.calls) == 1


def test_an_unreachable_library_stops_the_run(root):
    _demo(root)
    library = FakeLibrary(raise_for={".devague/frames/foo.json"})
    result = _sync(root, library)
    assert result.outcome == COMPLETED
    assert result.output["errors"] == 1
    assert result.output["stopped"] is True
    assert library.calls == []


def test_a_broken_repo_is_an_error_and_the_others_still_sync(root):
    _demo(root)
    broken = root / "broken"
    (broken / ".git").mkdir(parents=True)
    library = FakeLibrary()
    result = _sync(root, library)
    assert result.output["errors"] >= 1
    assert len(library.calls) == 3


def test_git_runs_as_argv_lists_with_a_timeout(root):
    _demo(root)
    seen = []

    def runner(argv, cwd, timeout):
        assert isinstance(argv, list)
        assert argv[0] == "git"
        assert 0 < timeout <= repodocs_mod.GIT_TIMEOUT_S
        seen.append(argv)
        return repodocs_mod.subprocess_git(argv, cwd, timeout)

    _sync(root, FakeLibrary(), git=runner)
    assert seen


def test_the_output_carries_counts_only(root):
    repo = _demo(root)
    repo.write(".devague/frames/secret.json", '{"never": "in run state"}\n')
    result = _sync(root, FakeLibrary())
    assert set(result.output) == OUTPUT_KEYS
    for key in OUTPUT_KEYS - {"duration_s", "stopped"}:
        assert isinstance(result.output[key], int), key
    assert "never" not in json.dumps(dict(result.output))
    assert result.output["repos"] == 1


# ------------------------------------------------------------------ the actor


def test_a_literal_api_key_is_refused_and_never_echoed(root):
    _demo(root)
    library = FakeLibrary()
    result = _sync(root, library, api_key="clk_literal_secret")
    assert result.outcome == FAILED
    assert result.error.startswith("invalid_actor")
    assert "clk_literal_secret" not in result.error
    assert library.calls == []


def test_a_non_service_actor_is_refused(root):
    port = RepodocsSyncPort(
        _store(_params(root), kind="agent"),
        transport=FakeLibrary(),
        secret_runner=lambda name: KEY_VALUE,
    )
    result = port.invoke({"actor": ACTOR_ID}, "k", DEADLINE, context=_ctx())
    assert result.outcome == FAILED
    assert result.error.startswith("actor_kind_mismatch")


@pytest.mark.parametrize(
    "over",
    [
        {"base_url": "ftp://127.0.0.1"},
        {"repos_root": "relative/path"},
        {"state_path": "relative.json"},
        {"max_seconds": 0},
        {"max_files": -1},
    ],
)
def test_bad_actor_params_fail_before_any_push(root, over):
    _demo(root)
    library = FakeLibrary()
    result = _sync(root, library, **over)
    assert result.outcome == FAILED
    assert result.error.startswith("invalid_actor")
    assert library.calls == []


def test_base_url_defaults_to_the_spark_loopback(root):
    _demo(root)
    library = FakeLibrary()
    params = _params(root)
    del params["base_url"]
    port = RepodocsSyncPort(_store(params), transport=library, secret_runner=lambda name: KEY_VALUE)
    assert port.invoke({"actor": ACTOR_ID}, "k", DEADLINE, context=_ctx()).outcome == COMPLETED
    assert library.calls  # FakeLibrary asserts the url is PUSH_URL


def test_a_missing_repos_root_fails(root):
    library = FakeLibrary()
    result = _sync(root, library, repos_root=str(root / "nope"))
    assert result.outcome == FAILED
    assert result.error.startswith("repos_root_missing")


def test_nothing_to_push_never_reads_the_grant(root):
    _demo(root)
    _sync(root, FakeLibrary())
    asked = []
    port = RepodocsSyncPort(
        _store(_params(root)),
        transport=FakeLibrary(),
        secret_runner=lambda name: asked.append(name) or KEY_VALUE,
    )
    port.invoke({"actor": ACTOR_ID}, "k", DEADLINE, context=_ctx())
    assert asked == []


# ------------------------------------------------------------------ spec links


@pytest.mark.parametrize(
    ("path", "slug"),
    [
        ("docs/specs/2026-10-01-foo.md", "foo"),
        ("docs/specs/foo-bar.md", "foo-bar"),
        ("docs/specs/sub/2026-10-01-x-y.md", "x-y"),
    ],
)
def test_spec_slug(path, slug):
    assert spec_slug(path) == slug


def test_spec_links_from_spec_trailers(root):
    repo = _demo(root)
    repo.write("src/feature.py", "x = 1\n")
    repo.write("tests/test_feature.py", "def test(): ...\n")
    trailer = repo.commit("Build foo\n\nSpec: foo")
    repo.write("src/other.py", "unrelated\n")
    repo.commit("Unrelated\n\nSpec: something-else")
    head = repo.head()
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by(SPEC, "sha")
    assert call["spec_links"] == {
        "code_paths": ["src/feature.py", "tests/test_feature.py"],
        "trailer_commits": [trailer],
        "at_commit": head,
        "at_committed_at": repo.time(),
    }


def test_trailer_paths_follow_renames_to_the_head(root):
    repo = _demo(root)
    repo.write("src/feature.py", "x = 1\n" * 20)
    repo.commit("Build foo\n\nSpec: foo")
    repo.git("mv", "src/feature.py", "src/renamed.py")
    repo.commit("rename")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by(SPEC, "sha")
    assert call["spec_links"]["code_paths"] == ["src/renamed.py"]


def _merge_delivery(repo: Repo, subject: str) -> str:
    repo.git("checkout", "-q", "-b", "bar")
    repo.write("docs/specs/2026-10-01-bar.md", "# Bar\n")
    repo.write("src/bar.py", "def bar():\n    return 1\n" * 10)
    repo.write("docs/deliveries/2026-10-02-bar.md", "delivered\n")
    repo.commit("bar work")
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "bar", "-m", subject)
    repo.publish()
    return repo.head()


def test_spec_links_from_the_delivering_merge_with_a_rename(root):
    repo = _demo(root)
    merge = _merge_delivery(repo, "Bar feature (#12)")
    repo.git("mv", "src/bar.py", "src/bar_impl.py")
    repo.git("rm", "-q", "docs/deliveries/2026-10-02-bar.md")
    repo.write("docs/deliveries/2026-10-03-bar.md", "delivered\n")
    head = repo.commit("rename bar")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by("docs/specs/2026-10-01-bar.md", "sha")
    assert call["spec_links"] == {
        "pr": 12,
        "merge_commit": merge,
        "code_paths": [
            "docs/deliveries/2026-10-03-bar.md",
            "docs/specs/2026-10-01-bar.md",
            "src/bar_impl.py",
        ],
        "at_commit": head,
        "at_committed_at": repo.time(),
    }


def test_merge_pull_request_subjects_give_the_pr_number(root):
    repo = _demo(root)
    _merge_delivery(repo, "Merge pull request #7 from org/bar")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by("docs/specs/2026-10-01-bar.md", "sha")
    assert call["spec_links"]["pr"] == 7


def test_a_spec_with_no_trailer_and_no_delivery_has_no_spec_links(root):
    _demo(root)
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by(SPEC, "sha")
    assert "spec_links" not in call
    for other in library.calls:
        assert "spec_links" not in other


def test_changed_spec_links_are_resent_with_the_same_version(root):
    repo = _demo(root)
    first = FakeLibrary()
    _sync(root, first)
    (pushed,) = first.by(SPEC, "sha")
    repo.write("src/late.py", "x\n")
    repo.commit("Late work\n\nSpec: foo")
    library = FakeLibrary()
    _sync(root, library)
    (call,) = library.by(SPEC, "sha")
    assert call["commit"] == pushed["commit"]  # same version: a 200 no-op plus new links
    assert call["committed_at"] == pushed["committed_at"]
    assert call["default_branch_head"] == repo.head()
    assert call["spec_links"]["code_paths"] == ["src/late.py"]
    again = FakeLibrary()
    _sync(root, again)
    assert again.calls == []


class LinksIgnored(FakeLibrary):
    """Answers every spec_links push with ``spec_links_applied: false``."""

    def __call__(self, method, url, headers, body, timeout):
        status, raw = super().__call__(method, url, headers, body, timeout)
        if "spec_links" in json.loads(body):
            return status, json.dumps({"created": False, "spec_links_applied": False}).encode()
        return status, raw


def _spec_entry(root: Path, repo: Repo) -> dict:
    state = json.loads((root.parent / "state" / "repodocs.json").read_text())
    return state["repos"][str(repo.path)]["files"][SPEC]


def test_spec_links_the_library_did_not_apply_are_not_recorded_as_sent(root):
    repo = _demo(root)
    repo.write("src/feature.py", "x = 1\n")
    repo.commit("Build foo\n\nSpec: foo")
    library = LinksIgnored()
    _sync(root, library)
    (call,) = library.by(SPEC, "sha")
    assert "spec_links" in call
    entry = _spec_entry(root, repo)
    assert entry["committed"]["blob"]  # the version itself was stored
    assert "links" not in entry  # but not the links
    again = FakeLibrary()
    _sync(root, again)  # the same head: the library would ignore them again
    assert again.calls == []


def test_spec_links_applied_true_or_absent_records_the_links(root):
    repo = _demo(root)
    repo.write("src/feature.py", "x = 1\n")
    repo.commit("Build foo\n\nSpec: foo")
    _sync(root, FakeLibrary())
    entry = _spec_entry(root, repo)
    assert entry["links"]
    assert entry["links_at"] == repo.head()


def test_an_unwritable_state_file_fails_the_step(root):
    _demo(root)
    blocker = root.parent / "state"
    blocker.write_text("a file where the state dir should be")
    result = _sync(root, FakeLibrary())
    assert result.outcome == FAILED
    assert result.error.startswith("io_error")


def test_yaml_comment_and_item_scans_match_the_old_patterns():
    """The linear scans that replaced two backtracking regexes keep their behaviour."""
    import re

    from culture_rules.node.actions import repodocs as rd

    for text in ["lib", "lib # note", "lib\t# note", "a#b", "#only", "  x  ", "", "x #"]:
        assert rd._strip_comment(text) == re.split(r"\s+#", text, maxsplit=1)[0].strip()
    old = re.compile(r"^\s+-\s*(.*)$")
    for line in ["  - lib", "\t-lib", "- top", "  x", "   -   spaced  ", "  -"]:
        match = old.match(line)
        assert rd._yaml_item(line) == (match.group(1) if match else None)
