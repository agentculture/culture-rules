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

* **identity**: ``git remote get-url origin``; without one, or when origin is a path on this
  host (it starts with ``/``, ``./``, ``../`` or ``~``, or is a ``file:`` URL, which the
  library refuses), ``<host>:<abs path>`` (the node's host name), as the contract asks;
* **default branch**: the first that resolves to a commit of
  ``git symbolic-ref --quiet refs/remotes/origin/HEAD`` (the remote's default branch as last
  fetched; nothing is fetched here), ``refs/remotes/origin/main``,
  ``refs/remotes/origin/master``, ``refs/heads/main``, ``refs/heads/master``, and only then
  the checked-out branch (``git symbolic-ref --quiet HEAD``), else ``HEAD``, so a feature
  branch's commits are never pushed as the default branch while a main or master exists; its
  head sha and committer time come from ``git log -1 --format=%H%x00%cI <ref>``. A repo with
  no commit has only drafts;
* **project**: ``domains:`` and ``connected:`` from the working tree's ``culture.yaml``, read
  by hand (an inline ``[a, b]`` list, a block ``- a`` list, indented or at the key's own
  indentation, or one scalar; quotes and ``#`` comments dropped); empty lists when absent. A
  ``connected`` entry that is a bare ``org/repo`` (no scheme, no dot in ``org``) is sent as
  ``https://github.com/org/repo``; a bare single name is dropped with a warning naming it;
* **files in scope**: under ``docs/specs``, ``docs/plans``, ``docs/deliveries``,
  ``docs/design`` and ``.devague``: the default branch's tree
  (``git ls-tree -r -l -z --full-tree <head> -- <prefixes>``, blobs only, no symlinks) and
  every regular file on disk (tracked, untracked and gitignored alike; symlinks never
  followed).

Clones of one origin
====================
Before syncing, one pass reads every repo's ``git remote get-url origin`` and groups repos by
origin: the host lowercased, the scp form (``git@host:org/repo``) equal to the URL forms
(``https://[creds@]host[:port]/org/repo``), a trailing ``.git`` or ``/`` dropped, the path
compared case-insensitively. Local-path origins and repos without one are never grouped. In
a group of two or more full clones, the **owner** is the clone whose directory name is the
origin's repo name (case-insensitive, ``-`` and ``_`` equal); with several or no such names,
the first by directory name among them (or among all). Every clone keeps the origin as its
identity (one org-wide corpus); the owner syncs as above. Every other clone (a **copy**,
logged once per run by name) sends only default-branch versions, their spec links and their
tombstones: never a working draft and never a ``working`` tombstone. Its committed pushes
are normally no-ops (the owner sent the same blob at the same head). A copy whose head is
older or newer than the owner's still sends what it sees; the library's current version is
the newest by ``committed_at`` among versions pushed with ``commit == default_branch_head``,
so a stale copy never rolls it back.

Migration: the drafts a copy pushed before it was one (its state entries with a draft) are
handed to the owner's state in that pass, with a draft blob no file has. The owner then
re-sends its own draft of each such path once (its bytes on disk, or its committed bytes when
the file is only in its tree), replacing the copy's bytes in the library's one draft; a path
the owner has neither in its tree nor on disk gets a ``working`` tombstone, so a copy-only
draft leaves the owner's project. The copy keeps only its committed versions. A later run
sends nothing for these paths.

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
   longer ahead). Files are compared by git blob id, cached by size and mtime; a changed
   file is read once, and the bytes hashed are the bytes sent.
3. **tombstone**: a file the state records as pushed that is neither in the default branch's
   tree nor on disk gets ``tombstone: true`` at the head sha (or ``"working"`` if it was never
   committed). A file whose pushed committed version is gone from the default branch but is
   still on disk (an untracked leftover, an older branch checked out) gets the tombstone at
   the head sha first; the library drops the draft with it, so a draft follows only when the
   bytes on disk differ from that last committed blob. A ``404 not_found`` counts as done.

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
re-sent (a 200 no-op) with the new links. An answer with ``spec_links_applied: false`` (the
library kept links with a later or equal ``at_committed_at``) leaves the links' hash
unrecorded; the head they were sent at is still recorded, because the library would ignore
the same head again. An answer without the field records both.

Idempotency, limits and output
==============================
The state file (JSON, written atomically through a temp file and ``os.replace`` after each
repo and at the end) records per repo and path the committed version pushed (commit, time,
blob), the draft's blob and the spec links' hash, so an unchanged run sends nothing. A file
over :data:`MAX_BYTES` (16 MiB, the library's cap) is a counted error and is not sent; on disk
it is cached as oversize by size and mtime, so it is one error until it changes. A run that
stops early merges its stat cache into the stored one (files no longer on disk dropped), so
the files it did not reach stay cached.
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
_DEFAULT_REFS = (
    "refs/remotes/origin/main",
    "refs/remotes/origin/master",
    "refs/heads/main",
    "refs/heads/master",
)
"""Tried in order when ``refs/remotes/origin/HEAD`` is unset, before the checked-out branch."""
_LOCAL_ORIGINS = ("/", "./", "../", "~", "file:")
"""An origin URL starting with one of these is a path on this host, not a shareable identity."""
_HANDED_OVER = ""
"""The draft blob a copy's handed-over draft gets in its owner's state: no file hashes to it,
so the owner re-sends that path's draft (or tombstones it) once."""
_OVERSIZE = "oversize"
"""The stat cache's blob id for a file over :data:`MAX_BYTES` (skipped until it changes)."""

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


def _yaml_block_item(line: str) -> str | None:
    """A block-list item under a top-level key: an indented ``- item`` line, or a ``- item``
    line at the key's own (zero) indentation, which YAML also allows. ``---`` and a new
    ``key:`` line are not items, so they end the block."""
    item = _yaml_item(line)
    if item is None and line.startswith("-") and (len(line) == 1 or line[1].isspace()):
        item = line[1:].lstrip()
    return item


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
    project["connected"] = _connected(project["connected"], repo.name)
    return project


def _connected(entries: Sequence[str], repo_name: str) -> list[str]:
    """``connected`` as the library can resolve it: a bare ``org/repo`` (two segments, no
    scheme, no dot in the first) becomes ``https://github.com/org/repo``; a bare single name
    is dropped with a warning (by name); anything else is sent as written."""
    kept: list[str] = []
    for entry in entries:
        if "/" not in entry and ":" not in entry:
            log.warning(
                "repodocs.sync: %s: culture.yaml connected entry %r is a bare name; not sent",
                repo_name,
                entry,
            )
            continue
        org, _, name = entry.partition("/")
        if ":" not in entry and "." not in org and org and name and "/" not in name:
            entry = f"https://github.com/{entry}"
        kept.append(entry)
    return kept


def _yaml_block(lines: Sequence[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = _yaml_block_item(line)
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

    def origin(self) -> str:
        """``git remote get-url origin``, or ``""`` without one."""
        code, out = self.run("remote", "get-url", "origin", ok=(0, 1, 2, 128))
        return out.decode("utf-8", errors="replace").strip() if code == 0 else ""

    def inspect(self, host: str) -> None:
        self.fmt = self.text("rev-parse", "--show-object-format").strip() or "sha1"
        url = self.origin()
        shared = url and not url.lower().startswith(_LOCAL_ORIGINS)
        self.remote = url if shared else f"{host}:{self.path}"
        self.head = self._head()
        if self.head is not None:
            self.tree = self._tree(self.head.sha)

    def _symbolic(self, ref: str) -> list[str]:
        code, out = self.run("symbolic-ref", "--quiet", ref, ok=(0, 1, 128))
        target = out.decode("utf-8", errors="replace").strip()
        return [target] if code == 0 and target else []

    def _head(self) -> _Head | None:
        """The default branch's head: origin/HEAD, else origin's or the local main/master,
        and only then the checked-out branch (a feature branch is never taken while a
        main or master exists)."""
        candidates = [
            *self._symbolic("refs/remotes/origin/HEAD"),
            *_DEFAULT_REFS,
            *self._symbolic("HEAD"),
            "HEAD",
        ]
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


def _remote_key(url: str) -> str | None:
    """The origin URL as one comparable key, ``host/org/repo`` lowercased: the scp form
    (``git@host:org/repo``) and the URL forms (``https://[creds@]host[:port]/org/repo``)
    agree, and a trailing ``.git`` or ``/`` is dropped. ``None`` for a local-path origin or a
    URL with no host or path (never grouped)."""
    raw = url.strip()
    if not raw or raw.lower().startswith(_LOCAL_ORIGINS):
        return None
    if "://" in raw:
        try:
            parts = urlsplit(raw)
            host = parts.hostname or ""
        except ValueError:
            return None
        path = parts.path
    else:
        authority, sep, path = raw.partition(":")
        if not sep:
            return None
        host = authority.rsplit("@", 1)[-1]
    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4].rstrip("/")
    if not host or not path:
        return None
    return f"{host.lower()}/{path.lower()}"


def _fold(name: str) -> str:
    return name.lower().replace("_", "-")


def _owner(key: str, members: Sequence[tuple[Path, str]]) -> tuple[Path, str]:
    """The clone that owns a shared origin: the one whose directory name is the origin's repo
    name (case-insensitive, ``-`` = ``_``); among several or none, the first by name."""
    wanted = _fold(key.rsplit("/", 1)[-1])
    matching = [m for m in members if _fold(m[0].name) == wanted]
    return min(matching or members, key=lambda m: m[0].name)


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

    def _post(
        self, label: str, body: dict[str, Any], *, tombstone: bool = False
    ) -> dict[str, Any] | None:
        """POST one push: the parsed answer (``{}`` when it is not a JSON object) on success,
        ``None`` on a non-2xx answer (counted and logged)."""
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
            return _json_object(raw)
        self.counts.errors += 1
        log.warning("repodocs.sync: %s: http %d %s", label, status, _error_code(raw))
        return None

    def _error(self, label: str, why: str) -> None:
        self.counts.errors += 1
        log.warning("repodocs.sync: %s: %s", label, why)

    # --- the whole run

    def sync(self) -> _Counts:
        root = self.config.repos_root
        if not root.is_dir():
            raise _Refused("repos_root_missing", "repos_root is not a directory")
        try:
            paths = list(_repos(root))
            copies = self._clones(paths)
            for repo in paths:
                self._remaining()
                self._sync_repo(repo, copy=repo in copies)
        except _Stop:
            self.counts.stopped = True
        finally:
            _save_state(self.config.state_path, self.state)
        return self.counts

    def _clones(self, paths: Sequence[Path]) -> set[Path]:
        """The pre-pass: group the repos by origin (:func:`_remote_key`, one git call each)
        and return the copies, the clones of a shared origin other than its owner
        (:func:`_owner`). Each copy is logged once, and the drafts it pushed before are
        handed to its owner (:meth:`_hand_over_drafts`)."""
        groups: dict[str, list[tuple[Path, str]]] = {}
        for path in paths:
            try:
                url = _Repo(path, self.git).origin()
            except _GitFailed:
                continue  # the repo's own sync reports it
            key = _remote_key(url)
            if key is not None:
                groups.setdefault(key, []).append((path, url))
        copies: set[Path] = set()
        for key, members in groups.items():
            if len(members) < 2:
                continue
            owner, owner_url = _owner(key, members)
            for path, url in members:
                if path == owner:
                    continue
                copies.add(path)
                log.info(
                    "repodocs.sync: %s is a copy of %s (same origin); committed versions only",
                    path.name,
                    owner.name,
                )
                self._hand_over_drafts(path, url, owner, owner_url)
        return copies

    def _hand_over_drafts(self, copy: Path, url: str, owner: Path, owner_url: str) -> None:
        """Migration: a copy that pushed working drafts under the shared origin left them in
        the library. Its draft entries move to the owner's state with a blob no file has, so
        the owner re-sends its own draft of each path once (its bytes replace the copy's), or
        tombstones it as ``working`` when the owner has no such file. The copy keeps only its
        committed versions."""
        repos = self.state["repos"]
        cst = repos.get(str(copy))
        if not isinstance(cst, dict) or cst.get("remote") != url:
            return
        files = cst.get("files") or {}
        drafted = sorted(r for r, e in files.items() if isinstance(e, dict) and e.get("working"))
        if not drafted:
            return
        ost = repos.get(str(owner))
        if not isinstance(ost, dict) or ost.get("remote") != owner_url:
            ost = repos[str(owner)] = {"remote": owner_url, "files": {}, "stat": {}}
        owner_files = ost.setdefault("files", {})
        for rel in drafted:
            owner_files.setdefault(rel, {})["working"] = {"blob": _HANDED_OVER}
            entry = files[rel]
            entry.pop("working")
            if not entry:
                del files[rel]

    def _sync_repo(self, path: Path, *, copy: bool = False) -> None:
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
        sync = _RepoSync(self, repo, st, disk, read_project(path), copy=copy)
        try:
            sync.run()
        finally:
            # merge, so a run stopped early keeps the cache of the files it did not reach;
            # entries for files no longer on disk are dropped
            st["stat"] = {k: v for k, v in sync.old_stat.items() if k in disk} | sync.new_stat
            repos[str(path)] = st
            _save_state(self.config.state_path, self.state)


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


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
        *,
        copy: bool = False,
    ) -> None:
        self.run_ = run
        self.copy = copy
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
        # a copy sends no drafts, so files only on its disk are not its business
        disk = set() if self.copy else set(self.disk)
        for rel in sorted(set(self.repo.tree) | disk | set(self.files)):
            self._file(rel)
        if not self.copy and self.st.get("project") != self.project_key and not self.pushed_here:
            self._refresh_project()

    def _send(
        self, rel: str, body: dict[str, Any], *, tombstone: bool = False
    ) -> dict[str, Any] | None:
        """Push one request; the library's parsed answer, or ``None`` when it failed."""
        body = {"remote": self.repo.remote, "path": rel, **body}
        if not self.copy:  # only the owner's culture.yaml describes the project
            body["project"] = self.project
        answer = self.run_._post(self._label(rel), body, tombstone=tombstone)
        if answer is None:
            return None
        self.pushed_here = True
        self.st["project"] = self.project_key
        return answer

    def _record_links(
        self, entry: dict[str, Any], links: dict[str, Any] | None, answer: Mapping[str, Any]
    ) -> None:
        """Record the links as sent at this head. The library answers ``spec_links_applied:
        false`` when it kept newer (or equal) links; their hash is then not recorded. The head
        is, so the same head is not re-sent: the library would ignore it again."""
        assert self.repo.head is not None
        entry["links_at"] = self.repo.head.sha
        if answer.get("spec_links_applied") is False:
            entry.pop("links", None)
        else:
            entry["links"] = _hash(links)

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
        if in_tree is None and entry.get("committed"):
            # gone from the default branch, still on disk: the committed version must not
            # stay current, so tombstone it first; a draft follows only if the bytes differ
            if not self._tombstone_committed(rel, entry):
                return
            did = True
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
        answer = self._send(rel, body)
        if answer is None:
            return False
        entry["committed"] = {**version, "blob": oid}
        entry.pop("gone", None)
        if spec:
            self._record_links(entry, links, answer)
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
        if data is None:
            return False
        answer = self._send(rel, {**self._commit_body(pushed, data), "spec_links": links})
        if answer is None:
            return False
        self._record_links(entry, links, answer)
        self.run_.counts.pushed += 1
        return True

    def _working_blob(self, rel: str) -> tuple[str, bytes | None]:
        """The on-disk file's blob id, and its bytes when this call read them (``None`` on a
        stat-cache hit). A file over :data:`MAX_BYTES` is one counted error, cached as
        :data:`_OVERSIZE` by size and mtime, so later runs skip it silently until it
        changes."""
        info = self.disk[rel]
        stamp = [info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        cached = self.old_stat.get(rel)
        if isinstance(cached, list) and len(cached) == 4 and cached[:3] == stamp:
            self.new_stat[rel] = cached
            return cached[3], None
        if info.st_size > MAX_BYTES:
            self.run_._error(self._label(rel), f"too_large: {info.st_size} bytes")
            self.new_stat[rel] = [*stamp, _OVERSIZE]
            return _OVERSIZE, None
        data = _read_file(self.repo.path / rel)
        if len(data) > MAX_BYTES:  # grew after the stat: not cached, retried next run
            self.run_._error(self._label(rel), "too_large")
            return _OVERSIZE, None
        oid = _blob_id(len(data), [data], self.repo.fmt)
        self.new_stat[rel] = [*stamp, oid]
        return oid, data

    def _disk_bytes(self, rel: str) -> tuple[str, bytes] | None:
        """Read an on-disk file for sending (after a stat-cache hit), with its blob id."""
        data = _read_file(self.repo.path / rel)
        if len(data) > MAX_BYTES:
            self.run_._error(self._label(rel), "too_large")
            return None
        return _blob_id(len(data), [data], self.repo.fmt), data

    def _draft_bytes(
        self,
        rel: str,
        in_tree: tuple[str, int] | None,
        on_disk: bool,
        oid: str,
        data: bytes | None,
    ) -> tuple[str, bytes] | None:
        """The draft's blob id and bytes: the disk bytes already hashed, the disk read now
        (after a stat-cache hit, sending what was hashed), or the committed blob."""
        if not on_disk:
            assert in_tree is not None
            blob = self._blob_bytes(rel, *in_tree)
            return None if blob is None else (oid, blob)
        if data is not None:
            return oid, data
        return self._disk_bytes(rel)

    def _working(
        self, rel: str, entry: dict[str, Any], in_tree: tuple[str, int] | None, on_disk: bool
    ) -> bool:
        if self.copy:
            return False  # the owner clone sends the drafts
        committed_oid = in_tree[0] if in_tree else (entry.get("gone") or {}).get("blob")
        data: bytes | None = None
        oid = committed_oid
        if on_disk:
            oid, data = self._working_blob(rel)
            if oid == _OVERSIZE:
                return False
        last = (entry.get("working") or {}).get("blob")
        if oid is None or (last if last is not None else committed_oid) == oid:
            return False
        loaded = self._draft_bytes(rel, in_tree, on_disk, oid, data)
        if loaded is None:
            return False
        oid, data = loaded
        body = {"commit": WORKING, "content_b64": base64.b64encode(data).decode("ascii")}
        if self._send(rel, body) is None:
            return False
        entry["working"] = {"blob": oid}
        self.run_.counts.pushed += 1
        return True

    def _tombstone(self, rel: str, entry: dict[str, Any]) -> None:
        """A file gone from the tree and the disk. An entry left only by
        :meth:`_tombstone_committed` (no draft since) was already tombstoned: dropped."""
        head = self.repo.head
        uncommitted = not entry.get("committed")
        if uncommitted and (self.copy or (not entry.get("working") and entry.get("gone"))):
            self.files.pop(rel, None)  # a copy never sends a working tombstone
            return
        commit = head.sha if entry.get("committed") and head is not None else WORKING
        if self._send(rel, {"commit": commit, "tombstone": True}, tombstone=True) is not None:
            self.files.pop(rel, None)
            self.run_.counts.tombstoned += 1

    def _tombstone_committed(self, rel: str, entry: dict[str, Any]) -> bool:
        """Tombstone a committed version deleted on the default branch but left on disk. The
        library drops the doc's draft with it, so the entry keeps only the last committed
        blob (``gone``), which the leftover's bytes are compared against."""
        head = self.repo.head
        assert head is not None
        if self._send(rel, {"commit": head.sha, "tombstone": True}, tombstone=True) is None:
            return False
        entry["gone"] = {"blob": entry["committed"].get("blob"), "at": head.sha}
        for key in ("committed", "working", "links", "links_at"):
            entry.pop(key, None)
        self.files[rel] = entry
        self.run_.counts.tombstoned += 1
        return True

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
