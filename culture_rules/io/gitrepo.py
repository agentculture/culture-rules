"""Save definitions to, and load them from, a (second) git repository via the git CLI.

All git calls are ``subprocess`` argv lists (no shell). Saving is a write: it plans first and
only commits (and optionally pushes) with ``apply=True``. Loading clones into a throwaway
temp directory, so the source repo is never modified.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from culture_rules.io.bundle import Bundle
from culture_rules.io.exchange import (
    ExportPlan,
    IssueRecord,
    ReadResult,
    export_bundle,
    read_bundle,
)

__all__ = ["GitError", "SaveResult", "load_from_repo", "save_to_repo"]

DEFAULT_MESSAGE = "culture-rules: export rules and workflows"
_IDENTITY = ("-c", "user.name=culture-rules", "-c", "user.email=culture-rules@localhost")


class GitError(RuntimeError):
    """A git invocation failed."""


@dataclass
class SaveResult:
    plan: ExportPlan
    committed: bool = False
    pushed: bool = False
    commit: str | None = None
    extra: list[str] = field(default_factory=list)


def _git(cwd: Path | str, *argv: str, identity: bool = False) -> str:
    cmd = ["git", *(_IDENTITY if identity else ()), *argv]
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, check=False
        )  # noqa: S603
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from exc
    if proc.returncode != 0:
        raise GitError(f"git {argv[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _subdir(repo: Path, directory: str) -> Path:
    target = (repo / directory).resolve()
    if not target.is_relative_to(repo.resolve()):
        raise ValueError(f"directory {directory!r} escapes the repository")
    return target


def save_to_repo(
    bundle: Bundle,
    repo: str | Path,
    *,
    directory: str = "",
    fmt: str = "yaml",
    message: str = DEFAULT_MESSAGE,
    apply: bool = False,
    push: bool = False,
) -> SaveResult:
    """Write the bundle into a checkout of the target repo and commit it.

    ``repo`` is a local working tree. Dry-run (the default) only reports the file plan.
    A commit is made only if something changed; ``push=True`` then runs ``git push``.
    """
    root = Path(repo)
    _git(root, "rev-parse", "--git-dir")  # fail early if not a repo
    target = _subdir(root, directory)
    plan = export_bundle(bundle, target, fmt=fmt, apply=apply)
    result = SaveResult(plan)
    if not apply:
        return result
    if any(c.action != "unchanged" for c in plan.changes):
        rel = target.relative_to(root.resolve()).as_posix() or "."
        _git(root, "add", "--all", "--", rel)
        if _git(root, "status", "--porcelain", "--", rel).strip():
            _git(root, "commit", "-m", message, identity=True)
            result.committed = True
            result.commit = _git(root, "rev-parse", "HEAD").strip()
    if push:
        _git(root, "push", "origin", "HEAD")
        result.pushed = True
    return result


def load_from_repo(
    source: str | Path, *, directory: str = "", ref: str | None = None
) -> ReadResult:
    """Clone ``source`` (path or URL) to a temp dir and read definitions from ``directory``."""
    with tempfile.TemporaryDirectory(prefix="culture-rules-load-") as tmp:
        dest = Path(tmp) / "repo"
        _git(
            tmp,
            "clone",
            "--quiet",
            *(["--branch", ref] if ref else []),
            "--",
            str(source),
            str(dest),
        )
        base = _subdir(dest, directory)
        if not base.is_dir():
            return ReadResult(
                Bundle(),
                [IssueRecord(directory or ".", "missing_directory", "no such directory in repo")],
            )
        return read_bundle(base)
