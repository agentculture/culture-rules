"""``repodocs.sync`` action port: push every top-level repo's devague record into the library
(culture-rules#124; the wire contract is culture-library's ``docs/api/repodocs.md``).

Params (``culture_rules.model.action_kinds``): ``actor`` only (falls back to
``context.actor``). Everything else is the bound ``service`` actor's params::

    {"base_url": "http://127.0.0.1:8796",
     "api_key": "grant:LIBRARY_REPODOCS_PUSH_API_KEY",
     "repos_root": "~/git",
     "state_path": "~/.local/state/culture-rules/repodocs-sync.json",
     "max_seconds": 300,
     "max_files": 5000}

``base_url`` (default :data:`DEFAULT_BASE_URL`) is http or https with a host and no
credentials, query or fragment. ``api_key`` must be a ``grant:<NAME>`` reference (a literal is
refused ``invalid_actor`` and never echoed); it is resolved on the executing host only when
there is something to send. ``repos_root`` and ``state_path`` are absolute (``~`` expands).
``max_seconds`` (default :data:`DEFAULT_MAX_SECONDS`) bounds one run; ``max_files`` (default
none) caps the requests one run sends. Both are positive integers.

One run
=======
Each directory directly under ``repos_root``, in name order, is a repo when its ``.git`` is a
real directory. Skipped: names starting ``.worktrees.`` or ending ``-worktrees``, symlinks,
and linked worktrees (``.git`` is a file). Per repo (git runs with explicit ``--git-dir`` and
``--work-tree``, so a broken repo never falls back to a parent):

* **identity**: ``git remote get-url origin``; without one, ``<host>:<abs path>`` (the node's
  host name), as the contract asks;
* **default branch**: ``git symbolic-ref --quiet refs/remotes/origin/HEAD`` (the remote's
  default branch as last fetched; nothing is fetched here), else the checked-out branch
  (``git symbolic-ref --quiet HEAD``), else ``HEAD``; its head sha and committer time come
  from ``git log -1 --format=%H%x00%cI <ref>``. A repo with no commit has only drafts;
* **project**: ``domains:`` and ``connected:`` from the working tree's ``culture.yaml``, read
  by hand (an inline ``[a, b]`` list, a block ``- a`` list or one scalar; quotes and ``#``
  comments dropped); empty lists when absent;
* **files in scope**: under ``docs/specs``, ``docs/plans``, ``docs/deliveries``,
  ``docs/design`` and ``.devague``: the default branch's tree
  (``git ls-tree -r -l -z --full-tree <head> -- <prefixes>``, blobs only, no symlinks) and
  every regular file on disk (tracked, untracked and gitignored alike; symlinks never
  followed).

Per file, against the state file (:func:`load_state`), in path order:

1. **committed version**: when the default branch holds the file and its blob differs from the
   one last pushed, its bytes (``git cat-file blob``) go out with ``commit`` =
   ``default_branch_head`` = the head sha and ``committed_at`` = the head's committer time.
   The library makes a version current only when ``commit == default_branch_head``, so the
   version is pinned to the head the run saw, not to the commit that last touched the file;
   history accrues from the first sync on (no backfill). An unchanged blob is not resent when
   the head moves, so the library's current version stays the one pushed.
2. **working draft**: the bytes on disk (the default-branch bytes when the file is not on
   disk) go out with ``commit: "working"`` when they differ from the last draft pushed, or,
   with no draft pushed yet, from the default-branch version (or there is none). That covers
   uncommitted edits, files committed only on the checked-out branch, untracked and gitignored
   files, and a discarded draft (reset to the committed bytes so the library's draft is no
   longer ahead). Files are compared by git blob id, cached by size and mtime.
3. **tombstone**: a file the state records as pushed that is neither in the default branch's
   tree nor on disk gets ``tombstone: true`` at the head sha (or ``"working"`` if it was never
   committed). A ``404 not_found`` counts as done.

Every request carries ``project``. A changed project with no other push for that repo
re-sends one already-pushed file (a 200 no-op) to carry it.

Spec links (``docs/specs/**.md``)
=================================
The slug is the file name without a leading ``YYYY-MM-DD-`` and ``.md`` (:func:`spec_slug`).
At the head ``H``:

* ``trailer_commits``: commits reachable from ``H`` whose ``Spec:`` trailer names the slug
  (``git log --format=%H%x00%(trailers:key=Spec,valueonly,separator=%x1f)%x1e H``), newest
  first; a value is normalised like a file name, so ``foo``,
  ``2026-10-01-foo`` and ``docs/specs/2026-10-01-foo.md`` all name ``foo``;
* the delivering merge: the oldest first-parent commit of ``H`` that added a
  ``docs/deliveries/*<slug>.md`` (``git log --first-parent --diff-merges=first-parent
  --no-renames --diff-filter=A --name-only -z H -- docs/deliveries``), i.e. the merge or
  squash commit that landed the delivery summary on the default branch. ``merge_commit`` is its
  sha and ``pr`` the number in its subject (``... (#12)`` or ``Merge pull request #12``);
* ``code_paths``: the files the trailer commits touched or, without trailers, the files the
  delivering merge changed against its first parent
  (``git diff-tree -r -z -M --name-status <parent> <commit>``; deletions dropped), then
  followed forward to ``H`` (``git diff-tree -r -z -M --name-status <commit> H``: a rename
  maps to its new path, a deletion drops the path);
* ``at_commit`` = ``H`` and ``at_committed_at`` = its committer time.

With neither trailers nor a delivering merge, ``spec_links`` is omitted. Links are sent with a
spec's committed version; when the head moves and a spec's links change, the same version is
re-sent (a 200 no-op) with the new links.

Idempotency, limits and output
==============================
The state file (JSON, written atomically through a temp file and ``os.replace`` after each
repo and at the end) records per repo and path the committed version pushed (commit, time,
blob), the draft's blob and the spec links' hash, so an unchanged run sends nothing. A file
over :data:`MAX_BYTES` (16 MiB, the library's cap) is a counted error and is not sent.
Per-file failures (git, a non-2xx answer) are counted and logged by path and status, never
by content, and never stop the run; the next run retries them. A network error stops the run
(the library is unreachable); so do ``max_seconds``, ``max_files`` and the attempt deadline:
the next run resumes from the state file. Each git call is bounded by :data:`GIT_TIMEOUT_S`.

Outputs counts only, never file bytes or paths: ``{repos, pushed, skipped, tombstoned,
errors, duration_s, stopped}``. Failures of the step itself (a bad actor, an unresolvable
grant, a missing ``repos_root``) are not retryable. ``supports_idempotency_key`` is True: a
retry re-runs the sync, which the state file and the library make idempotent. git runs as an
argv list with ``shell=False``; HTTP uses :func:`~culture_rules.node.actions.library.
urllib_transport` (no proxies, no redirects). Standard-library only.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import logging
import os
import re
import stat
import subprocess  # argv lists only, shell=False (B404/B603 skipped in pyproject)
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from culture_rules.actors.secrets import SecretError, is_secret_ref, resolve
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.library import Transport, urllib_transport
from culture_rules.node.actions.machine import load_bound_actor

__all__ = [
    "DEFAULT_BASE_URL",
    "GIT_TIMEOUT_S",
    "MAX_BYTES",
    "PREFIXES",
    "RepodocsSyncPort",
    "load_state",
    "read_project",
    "spec_slug",
    "subprocess_git",
]

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8796"
PUSH_PATH = "/api/repodocs"
MAX_BYTES = 16 * 1024 * 1024
"""The library's per-file cap (``CULTURE_LIBRARY_REPODOC_MAX_BYTES``'s default)."""
DEFAULT_MAX_SECONDS = 300
GIT_TIMEOUT_S = 60.0
HTTP_TIMEOUT_S = 60.0
DEADLINE_MARGIN_S = 30.0
PREFIXES = ("docs/specs", "docs/plans", "docs/deliveries", "docs/design", ".devague")
WORKING = "working"
ACTOR_KIND = "service"
STATE_SCHEMA = 1
GIT_TIMED_OUT = -1000
GIT_UNAVAILABLE = -1001
_MAX_YAML = 1024 * 1024
_INVALID_ACTOR = "invalid_actor"
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_PR_SUFFIX = re.compile(r"\(#(\d+)\)\s*$")
_PR_MERGE = re.compile(r"^Merge pull request #(\d+)\b")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_YAML_KEY = re.compile(r"^(domains|connected)\s*:(.*)$")

GitRunner = Callable[[Sequence[str], str, float], tuple[int, bytes]]
"""``runner(argv, cwd, timeout) -> (returncode, stdout bytes)``; stderr is discarded. A
runner returns :data:`GIT_TIMED_OUT` / :data:`GIT_UNAVAILABLE` for a timeout / no git."""


def subprocess_git(argv: Sequence[str], cwd: str, timeout: float) -> tuple[int, bytes]:
    """Default runner: one ``git`` process, argv list, no shell, stdin closed, no prompts."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        proc = subprocess.run(  # nosec B603
            list(argv),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return GIT_TIMED_OUT, b""
    except OSError:
        return GIT_UNAVAILABLE, b""
    return proc.returncode, proc.stdout or b""


# ------------------------------------------------------------------ pure helpers


def spec_slug(path: str) -> str:
    """A spec's slug: its file name without a ``YYYY-MM-DD-`` prefix and ``.md``."""
    name = path.strip().rsplit("/", 1)[-1]
    if name.endswith(".md"):
        name = name[:-3]
    return _DATE_PREFIX.sub("", name)


def _is_spec(path: str) -> bool:
    return path.startswith("docs/specs/") and path.endswith(".md")


def _pr_number(subject: str) -> int | None:
    match = _PR_SUFFIX.search(subject) or _PR_MERGE.match(subject)
    return int(match.group(1)) if match else None


def _yaml_scalar(text: str) -> str:
    text = text.strip()
    if text[:1] in ("'", '"'):
        end = text.find(text[0], 1)
        return text[1:end] if end > 0 else text[1:]
    return _strip_comment(text)


def _strip_comment(text: str) -> str:
    """Drop a trailing ``#`` comment: a ``#`` preceded by whitespace (a linear scan)."""
    for index in range(1, len(text)):
        if text[index] == "#" and text[index - 1].isspace():
            return text[:index].strip()
    return text.strip()


def _yaml_item(line: str) -> str | None:
    """The value of an indented ``- item`` line, or None when the line is not one."""
    if not line[:1].isspace():
        return None
    body = line.lstrip()
    return body[1:].lstrip() if body.startswith("-") else None


def _yaml_inline(text: str) -> list[str]:
    inner = text.strip()[1:]
    inner = inner.split("]", 1)[0]
    return [v for v in (_yaml_scalar(part) for part in inner.split(",")) if v]


def read_project(repo: Path) -> dict[str, list[str]]:
    """``{"domains", "connected"}`` from ``repo/culture.yaml`` (top-level keys, parsed by
    hand); empty lists when the file or a key is absent."""
    project: dict[str, list[str]] = {"domains": [], "connected": []}
    try:
        with open(repo / "culture.yaml", "rb") as handle:
            text = handle.read(_MAX_YAML).decode("utf-8", errors="replace")
    except OSError:
        return project
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _YAML_KEY.match(line)
        if not match:
            continue
        rest = match.group(2).strip()
        if rest.startswith("["):
            project[match.group(1)] = _yaml_inline(rest)
        elif rest and not rest.startswith("#"):
            value = _yaml_scalar(rest)
            project[match.group(1)] = [value] if value else []
        else:
            project[match.group(1)] = _yaml_block(lines[index + 1 :])
    return project


def _yaml_block(lines: Sequence[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = _yaml_item(line)
        if item is None:
            break
        value = _yaml_scalar(item)
        if value:
            items.append(value)
    return items


def _name_status(raw: bytes) -> Iterator[tuple[str, str, str | None]]:
    """``(status, path, new path)`` from ``--name-status -z`` output."""
    tokens = raw.decode("utf-8", errors="surrogateescape").split("\0")
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index][:1]
        if status in ("R", "C"):
            yield status, tokens[index + 1], tokens[index + 2]
            index += 3
        else:
            yield status, tokens[index + 1], None
            index += 2


def _blob_id(data_len: int, chunks: Sequence[bytes], fmt: str) -> str:
    digest = hashlib.new(fmt, usedforsecurity=False)  # git object ids, not security
    digest.update(b"blob %d\0" % data_len)
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ the state file


def load_state(path: Path) -> dict[str, Any]:
    """The state file, or a fresh state when it is missing, unreadable or another schema."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = None
    except (OSError, ValueError):
        log.warning("repodocs.sync: state file %s is unreadable; starting over", path)
        data = None
    if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
        return {"schema": STATE_SCHEMA, "repos": {}}
    if not isinstance(data.get("repos"), dict):
        data["repos"] = {}
    return data


def _save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ errors and config


class _Refused(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")

    def result(self) -> InvocationResult:
        return InvocationResult.failed(str(self), retryable=False)


class _Stop(Exception):
    """Stop the run early (deadline, max_files, an unreachable library)."""


class _GitFailed(Exception):
    pass


def _base_url(value: Any) -> str:
    if value is None:
        return DEFAULT_BASE_URL
    if not isinstance(value, str) or not value:
        raise _Refused(_INVALID_ACTOR, "base_url must be a non-empty string")
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError as exc:
        raise _Refused(_INVALID_ACTOR, "base_url is malformed") from exc
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise _Refused(_INVALID_ACTOR, "base_url must be http(s) with a host")
    if parts.username is not None or parts.password is not None:
        raise _Refused(_INVALID_ACTOR, "base_url must not carry credentials")
    if parts.query or parts.fragment:
        raise _Refused(_INVALID_ACTOR, "base_url must not carry a query or fragment")
    return value.rstrip("/")


def _abs_path(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise _Refused(_INVALID_ACTOR, f"{name} must be a path")
    path = os.path.expanduser(value)
    if not os.path.isabs(path):
        raise _Refused(_INVALID_ACTOR, f"{name} must be absolute (or start with ~)")
    return Path(path)


def _positive(value: Any, name: str, default: int | None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _Refused(_INVALID_ACTOR, f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class _Config:
    base_url: str
    api_key: str
    repos_root: Path
    state_path: Path
    max_seconds: int
    max_files: int | None

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> _Config:
        base = _base_url(params.get("base_url"))
        ref = params.get("api_key")
        if not is_secret_ref(ref):
            raise _Refused(_INVALID_ACTOR, "api_key must be a grant:<NAME> reference")
        return cls(
            base_url=base,
            api_key=ref,
            repos_root=_abs_path(params.get("repos_root"), "repos_root"),
            state_path=_abs_path(params.get("state_path"), "state_path"),
            max_seconds=_positive(params.get("max_seconds"), "max_seconds", DEFAULT_MAX_SECONDS)
            or DEFAULT_MAX_SECONDS,
            max_files=_positive(params.get("max_files"), "max_files", None),
        )


@dataclass
class _Counts:
    repos: int = 0
    pushed: int = 0
    skipped: int = 0
    tombstoned: int = 0
    errors: int = 0
    stopped: bool = False


# ------------------------------------------------------------------ one repo


@dataclass
class _Head:
    sha: str
    time: str


@dataclass
class _Merge:
    sha: str
    parents: tuple[str, ...]
    subject: str


@dataclass
class _Repo:
    """One repo as git sees it (read-only)."""

    path: Path
    git: GitRunner
    remote: str = ""
    fmt: str = "sha1"
    head: _Head | None = None
    tree: dict[str, tuple[str, int]] = field(default_factory=dict)
    _links_index: tuple[dict[str, list[str]], dict[str, _Merge]] | None = None

    def run(self, *args: str, ok: tuple[int, ...] = (0,)) -> tuple[int, bytes]:
        argv = ["git", f"--git-dir={self.path / '.git'}", f"--work-tree={self.path}", *args]
        code, out = self.git(argv, str(self.path), GIT_TIMEOUT_S)
        if code not in ok:
            raise _GitFailed(f"git {args[0]} exited {code}")
        return code, out

    def text(self, *args: str) -> str:
        return self.run(*args)[1].decode("utf-8", errors="surrogateescape")

    def inspect(self, host: str) -> None:
        self.fmt = self.text("rev-parse", "--show-object-format").strip() or "sha1"
        code, out = self.run("remote", "get-url", "origin", ok=(0, 1, 2, 128))
        url = out.decode("utf-8", errors="replace").strip()
        self.remote = url if code == 0 and url else f"{host}:{self.path}"
        self.head = self._head()
        if self.head is not None:
            self.tree = self._tree(self.head.sha)

    def _head(self) -> _Head | None:
        candidates = []
        for ref in ("refs/remotes/origin/HEAD", "HEAD"):
            code, out = self.run("symbolic-ref", "--quiet", ref, ok=(0, 1, 128))
            if code == 0 and out.strip():
                candidates.append(out.decode("utf-8", errors="replace").strip())
        candidates.append("HEAD")
        for ref in candidates:
            code, _ = self.run(
                "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", ok=(0, 1, 128)
            )
            if code == 0:
                sha, _, when = self.text("log", "-1", "--format=%H%x00%cI", ref, "--").partition(
                    "\0"
                )
                return _Head(sha.strip(), when.strip())
        return None

    def _tree(self, head: str) -> dict[str, tuple[str, int]]:
        raw = self.text("ls-tree", "-r", "-l", "-z", "--full-tree", head, "--", *PREFIXES)
        tree: dict[str, tuple[str, int]] = {}
        for record in raw.split("\0"):
            meta, _, path = record.partition("\t")
            parts = meta.split()
            if len(parts) != 4 or parts[1] != "blob" or parts[0] == "120000":
                continue
            tree[path] = (parts[2], int(parts[3]))
        return tree

    def blob(self, oid: str) -> bytes:
        return self.run("cat-file", "blob", oid)[1]

    # --- spec links

    def _index(self) -> tuple[dict[str, list[str]], dict[str, _Merge]]:
        if self._links_index is None:
            self._links_index = (self._trailers(), self._deliveries())
        return self._links_index

    def _trailers(self) -> dict[str, list[str]]:
        assert self.head is not None
        raw = self.text(
            "log",
            "--format=%H%x00%(trailers:key=Spec,valueonly,separator=%x1f)%x1e",
            self.head.sha,
            "--",
        )
        found: dict[str, list[str]] = {}
        for record in raw.split("\x1e"):
            sha, _, values = record.strip().partition("\0")
            for value in values.split("\x1f"):
                slug = spec_slug(value) if value.strip() else ""
                if sha and slug and sha not in found.setdefault(slug, []):
                    found[slug].append(sha)
        return found

    def _deliveries(self) -> dict[str, _Merge]:
        assert self.head is not None
        raw = self.text(
            "log",
            "--first-parent",
            "--diff-merges=first-parent",
            "--no-renames",
            "--diff-filter=A",
            "--name-only",
            "-z",
            "--format=%x1e%H%x00%P%x00%s",
            self.head.sha,
            "--",
            "docs/deliveries",
        )
        found: dict[str, _Merge] = {}
        for record in raw.split("\x1e"):  # newest first: an older record overrides
            fields = record.split("\0")
            if len(fields) < 3:
                continue
            merge = _Merge(fields[0], tuple(fields[1].split()), fields[2])
            for path in (f.strip("\n") for f in fields[3:]):
                if path.endswith(".md"):
                    found[spec_slug(path)] = merge
        return found

    def _touched(self, sha: str, parents: Sequence[str] | None = None) -> set[str]:
        if parents is None:
            parents = self.text("log", "-1", "--format=%P", sha, "--").split()
        if parents:
            raw = self.run("diff-tree", "-r", "-z", "-M", "--name-status", parents[0], sha)[1]
        else:
            raw = self.run(
                "diff-tree", "-r", "-z", "-M", "--name-status", "--root", "--no-commit-id", sha
            )[1]
        return {new or old for status, old, new in _name_status(raw) if status != "D"}

    def _forward(self, paths: set[str], sha: str) -> set[str]:
        assert self.head is not None
        if sha == self.head.sha:
            return paths
        raw = self.run("diff-tree", "-r", "-z", "-M", "--name-status", sha, self.head.sha)[1]
        renamed: dict[str, str] = {}
        deleted: set[str] = set()
        for status, old, new in _name_status(raw):
            if status == "R" and new:
                renamed[old] = new
            elif status == "D":
                deleted.add(old)
        return {renamed.get(p, p) for p in paths if p in renamed or p not in deleted}

    def spec_links(self, path: str) -> dict[str, Any] | None:
        """The spec's links at the head (module docstring), ``None`` with nothing found."""
        if self.head is None:
            return None
        trailers, deliveries = self._index()
        slug = spec_slug(path)
        commits = trailers.get(slug, [])
        merge = deliveries.get(slug)
        if not commits and merge is None:
            return None
        paths: set[str] = set()
        if commits:
            for sha in commits:
                paths |= self._forward(self._touched(sha), sha)
        elif merge is not None:
            paths = self._forward(self._touched(merge.sha, merge.parents), merge.sha)
        links: dict[str, Any] = {}
        if merge is not None:
            links["merge_commit"] = merge.sha
            pr = _pr_number(merge.subject)
            if pr is not None:
                links["pr"] = pr
        if commits:
            links["trailer_commits"] = list(commits)
        links["code_paths"] = sorted(paths)
        links["at_commit"] = self.head.sha
        links["at_committed_at"] = self.head.time
        return links


def _repos(root: Path) -> Iterator[Path]:
    """Top-level repos under ``root`` in name order (module docstring: what is skipped)."""
    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        name = entry.name
        if name.startswith(".worktrees.") or name.endswith("-worktrees"):
            continue
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        dot_git = Path(entry.path) / ".git"
        if dot_git.is_dir() and not dot_git.is_symlink():
            yield Path(entry.path)


def _disk(repo: Path) -> dict[str, os.stat_result]:
    """Regular files under the in-scope prefixes, by repo-relative path; no symlinks."""
    found: dict[str, os.stat_result] = {}
    for prefix in PREFIXES:
        top = repo / prefix
        if top.is_symlink() or not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
            )
            for name in filenames:
                full = os.path.join(dirpath, name)
                info = os.lstat(full)
                if stat.S_ISREG(info.st_mode):
                    found[os.path.relpath(full, repo).replace(os.sep, "/")] = info
    return found


def _read_file(path: Path) -> bytes:
    """A regular file's bytes, never through a symlink; :data:`MAX_BYTES` + 1 at most."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError("not a regular file")
        return handle.read(MAX_BYTES + 1)


# ------------------------------------------------------------------ the run


class _Run:
    def __init__(
        self,
        config: _Config,
        *,
        git: GitRunner,
        transport: Transport,
        key: Callable[[], str],
        clock: Callable[[], datetime],
        stop_at: datetime,
        host: str,
    ) -> None:
        self.config = config
        self.git = git
        self.transport = transport
        self.key = key
        self.clock = clock
        self.stop_at = stop_at
        self.host = host
        self.counts = _Counts()
        self.sent = 0
        self.state = load_state(config.state_path)

    # --- budget and HTTP

    def _remaining(self) -> float:
        left = (self.stop_at - self.clock()).total_seconds()
        if left <= 0:
            raise _Stop("deadline")
        return left

    def _post(self, label: str, body: dict[str, Any], *, tombstone: bool = False) -> bool:
        if self.config.max_files is not None and self.sent >= self.config.max_files:
            raise _Stop("max_files")
        timeout = min(HTTP_TIMEOUT_S, self._remaining())
        headers = {
            "Authorization": "Bearer " + self.key(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = json.dumps(body).encode("utf-8")
        self.sent += 1
        try:
            status, raw = self.transport(
                "POST", self.config.base_url + PUSH_PATH, headers, payload, timeout
            )
        except (http.client.HTTPException, OSError) as exc:
            self.counts.errors += 1
            log.warning("repodocs.sync: %s: library unreachable (%s)", label, type(exc).__name__)
            raise _Stop("network") from exc
        if 200 <= status < 300 or (tombstone and status == 404):
            return True
        self.counts.errors += 1
        log.warning("repodocs.sync: %s: http %d %s", label, status, _error_code(raw))
        return False

    def _error(self, label: str, why: str) -> None:
        self.counts.errors += 1
        log.warning("repodocs.sync: %s: %s", label, why)

    # --- the whole run

    def sync(self) -> _Counts:
        root = self.config.repos_root
        if not root.is_dir():
            raise _Refused("repos_root_missing", "repos_root is not a directory")
        try:
            for repo in _repos(root):
                self._remaining()
                self._sync_repo(repo)
        except _Stop:
            self.counts.stopped = True
        finally:
            _save_state(self.config.state_path, self.state)
        return self.counts

    def _sync_repo(self, path: Path) -> None:
        self.counts.repos += 1
        repo = _Repo(path, self.git)
        try:
            repo.inspect(self.host)
            disk = _disk(path)
        except (_GitFailed, OSError) as exc:
            self._error(path.name, f"repo skipped: {exc}")
            return
        repos = self.state["repos"]
        st = repos.get(str(path))
        if not isinstance(st, dict) or st.get("remote") != repo.remote:
            st = {"remote": repo.remote, "files": {}, "stat": {}}
        sync = _RepoSync(self, repo, st, disk, read_project(path))
        try:
            sync.run()
        finally:
            st["stat"] = sync.new_stat
            repos[str(path)] = st
            _save_state(self.config.state_path, self.state)


def _error_code(raw: bytes) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    if isinstance(data, dict) and isinstance(data.get("detail"), dict):
        data = data["detail"]
    code = data.get("code") if isinstance(data, dict) else None
    return code if isinstance(code, str) and _CODE_RE.match(code) else ""


class _RepoSync:
    """One repo's files against its state entry."""

    def __init__(
        self,
        run: _Run,
        repo: _Repo,
        st: dict[str, Any],
        disk: dict[str, os.stat_result],
        project: dict[str, list[str]],
    ) -> None:
        self.run_ = run
        self.repo = repo
        self.st = st
        self.files: dict[str, Any] = st.setdefault("files", {})
        self.old_stat: dict[str, Any] = st.get("stat") or {}
        self.new_stat: dict[str, Any] = {}
        self.disk = disk
        self.project = project
        self.project_key = _hash(project)
        self.pushed_here = False

    def _label(self, rel: str) -> str:
        return f"{self.repo.path.name}/{rel}"

    def run(self) -> None:
        for rel in sorted(set(self.repo.tree) | set(self.disk) | set(self.files)):
            self._file(rel)
        if self.st.get("project") != self.project_key and not self.pushed_here:
            self._refresh_project()

    def _send(self, rel: str, body: dict[str, Any], *, tombstone: bool = False) -> bool:
        body = {"remote": self.repo.remote, "path": rel, **body, "project": self.project}
        if not self.run_._post(self._label(rel), body, tombstone=tombstone):
            return False
        self.pushed_here = True
        self.st["project"] = self.project_key
        return True

    # --- one file

    def _file(self, rel: str) -> None:
        entry: dict[str, Any] = self.files.get(rel) or {}
        in_tree = self.repo.tree.get(rel)
        on_disk = rel in self.disk
        if in_tree is None and not on_disk:
            if entry:
                self._tombstone(rel, entry)
            return
        did = False
        try:
            if in_tree is not None:
                did = self._committed(rel, entry, *in_tree)
            did = self._working(rel, entry, in_tree, on_disk) or did
        except (_GitFailed, OSError) as exc:
            self.run_._error(self._label(rel), f"skipped: {exc}")
        finally:
            if entry:
                self.files[rel] = entry
        if not did:
            self.run_.counts.skipped += 1

    def _commit_body(self, commit: Mapping[str, Any], data: bytes) -> dict[str, Any]:
        assert self.repo.head is not None
        return {
            "commit": commit["commit"],
            "committed_at": commit["committed_at"],
            "default_branch_head": self.repo.head.sha,
            "content_b64": base64.b64encode(data).decode("ascii"),
        }

    def _blob_bytes(self, rel: str, oid: str, size: int) -> bytes | None:
        if size > MAX_BYTES:
            self.run_._error(self._label(rel), f"too_large: {size} bytes")
            return None
        return self.repo.blob(oid)

    def _committed(self, rel: str, entry: dict[str, Any], oid: str, size: int) -> bool:
        head = self.repo.head
        assert head is not None
        pushed = entry.get("committed")
        if pushed is None or pushed.get("blob") != oid:
            return self._push_changed(rel, entry, oid, size)
        return self._refresh_links(rel, entry, pushed, oid, size)

    def _push_changed(self, rel: str, entry: dict[str, Any], oid: str, size: int) -> bool:
        """Push a committed version whose blob changed since the last push."""
        head = self.repo.head
        assert head is not None
        spec = _is_spec(rel)
        data = self._blob_bytes(rel, oid, size)
        if data is None:
            return False
        version = {"commit": head.sha, "committed_at": head.time}
        body = self._commit_body(version, data)
        links = self.repo.spec_links(rel) if spec else None
        if links is not None:
            body["spec_links"] = links
        if not self._send(rel, body):
            return False
        entry["committed"] = {**version, "blob": oid}
        if spec:
            entry.update(links=_hash(links), links_at=head.sha)
        self.run_.counts.pushed += 1
        return True

    def _refresh_links(
        self, rel: str, entry: dict[str, Any], pushed: dict[str, Any], oid: str, size: int
    ) -> bool:
        """Re-send an unchanged spec version when its spec links moved with the head."""
        head = self.repo.head
        assert head is not None
        if not _is_spec(rel) or entry.get("links_at") == head.sha:
            return False
        links = self.repo.spec_links(rel)
        if links is None or _hash(links) == entry.get("links"):
            entry["links_at"] = head.sha
            return False
        data = self._blob_bytes(rel, oid, size)
        if data is None or not self._send(
            rel, {**self._commit_body(pushed, data), "spec_links": links}
        ):
            return False
        entry.update(links=_hash(links), links_at=head.sha)
        self.run_.counts.pushed += 1
        return True

    def _working_blob(self, rel: str) -> str:
        info = self.disk[rel]
        stamp = [info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        cached = self.old_stat.get(rel)
        if isinstance(cached, list) and len(cached) == 4 and cached[:3] == stamp:
            self.new_stat[rel] = cached
            return cached[3]
        if info.st_size > MAX_BYTES:
            return f"oversize:{info.st_size}"
        data = _read_file(self.repo.path / rel)
        oid = _blob_id(len(data), [data], self.repo.fmt)
        self.new_stat[rel] = [*stamp, oid]
        return oid

    def _working(
        self, rel: str, entry: dict[str, Any], in_tree: tuple[str, int] | None, on_disk: bool
    ) -> bool:
        committed_oid = in_tree[0] if in_tree else None
        oid = self._working_blob(rel) if on_disk else committed_oid
        last = (entry.get("working") or {}).get("blob")
        if oid is None or (last if last is not None else committed_oid) == oid:
            return False
        if on_disk:
            data = _read_file(self.repo.path / rel)
            if len(data) > MAX_BYTES:
                self.run_._error(self._label(rel), "too_large")
                return False
        else:
            assert in_tree is not None
            data = self._blob_bytes(rel, *in_tree)
            if data is None:
                return False
        body = {"commit": WORKING, "content_b64": base64.b64encode(data).decode("ascii")}
        if not self._send(rel, body):
            return False
        entry["working"] = {"blob": oid}
        self.run_.counts.pushed += 1
        return True

    def _tombstone(self, rel: str, entry: dict[str, Any]) -> None:
        head = self.repo.head
        commit = head.sha if entry.get("committed") and head is not None else WORKING
        if self._send(rel, {"commit": commit, "tombstone": True}, tombstone=True):
            self.files.pop(rel, None)
            self.run_.counts.tombstoned += 1

    def _refresh_project(self) -> None:
        """Carry a changed project on a re-send of one already-pushed file (a 200 no-op)."""
        for rel in sorted(self.files):
            entry = self.files[rel]
            in_tree = self.repo.tree.get(rel)
            pushed = entry.get("committed")
            try:
                if in_tree and pushed and pushed.get("blob") == in_tree[0]:
                    data = self._blob_bytes(rel, *in_tree)
                    if data is not None:
                        self._send(rel, self._commit_body(pushed, data))
                    return
                if rel in self.disk and entry.get("working"):
                    data = _read_file(self.repo.path / rel)
                    if len(data) <= MAX_BYTES:
                        body = {"commit": WORKING, "content_b64": base64.b64encode(data).decode()}
                        self._send(rel, body)
                    return
            except (_GitFailed, OSError) as exc:
                self.run_._error(self._label(rel), f"skipped: {exc}")
                return


# ------------------------------------------------------------------ the port


class RepodocsSyncPort:
    """ActorPort for ``repodocs.sync`` (see the module docstring)."""

    supports_idempotency_key = True  # the state file and the library make a re-run a no-op

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        git: GitRunner | None = None,
        secret_runner: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport or urllib_transport
        self._git = git or subprocess_git
        self._secret_runner = secret_runner
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del idempotency_key
        actor, _doc, failure = load_bound_actor(self._store, input, context)
        if failure is not None or actor is None:
            return failure or InvocationResult.failed("actor_not_found", retryable=False)
        if actor.kind != ACTOR_KIND:
            return InvocationResult.failed(
                f"actor_kind_mismatch: repodocs.sync needs a {ACTOR_KIND} actor, "
                f"{actor.id!r} is {actor.kind!r}",
                retryable=False,
            )
        try:
            config = _Config.from_params(actor.params)
        except _Refused as exc:
            return exc.result()
        start = self._clock()
        stop_at = start + timedelta(seconds=config.max_seconds)
        if deadline is not None:
            stop_at = min(stop_at, deadline - timedelta(seconds=DEADLINE_MARGIN_S))
        resolved: list[str] = []

        def key() -> str:
            if not resolved:
                try:
                    resolved.append(resolve(config.api_key, self._secret_runner))
                except SecretError as exc:
                    raise _Refused("secret_unresolved", "api_key") from exc
            return resolved[0]

        run = _Run(
            config,
            git=self._git,
            transport=self._transport,
            key=key,
            clock=self._clock,
            stop_at=stop_at,
            host=context.host,
        )
        try:
            counts = run.sync()
        except _Refused as exc:
            return exc.result()
        except OSError as exc:  # the state file or repos_root could not be read or written
            return InvocationResult.failed(f"io_error: {type(exc).__name__}", retryable=True)
        duration = (self._clock() - start).total_seconds()
        return InvocationResult.completed(
            {
                "repos": counts.repos,
                "pushed": counts.pushed,
                "skipped": counts.skipped,
                "tombstoned": counts.tombstoned,
                "errors": counts.errors,
                "duration_s": round(duration, 3),
                "stopped": counts.stopped,
            }
        )
