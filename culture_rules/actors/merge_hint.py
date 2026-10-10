"""d36: the one merge from base, told to the fixer's agent and named in the review.

Two texts live here so their producers and consumers share them:

- :func:`with_merge_paragraph` appends the merge-from-base paragraph to the instruction
  of a fixer try at the bridge call (the way the d26 status-notes hint is appended), naming
  the run's base commit. Nothing is stored: a try's instruction and task never hold the
  paragraph, so it cannot pile up across retries. An instruction that already names the
  base commit (the conflict rule's, or a /fix that orders that merge) gets none.
- :data:`COPIED_BASE_LEAD` starts the gate's finding for a base copied in as a plain
  commit; the review recognises it and asks for one real merge instead of a smaller fix
  (:func:`copied_base_ask`).

Standard-library only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

__all__ = [
    "COPIED_BASE_LEAD",
    "MERGE_PARAGRAPH_HEAD",
    "copied_base_ask",
    "copied_base_in",
    "merge_paragraph",
    "with_merge_paragraph",
]

MERGE_PARAGRAPH_HEAD = "Merging the base branch (d36):"
"""How the paragraph begins (after a blank line)."""

_SHA = re.compile(r"[0-9a-f]{40}")


def merge_paragraph(base: str) -> str:
    """The paragraph for base commit ``base``, with its leading blank line."""
    return (
        f"\n\n{MERGE_PARAGRAPH_HEAD} if this PR needs its base branch - it conflicts with "
        "it, or the request asks to merge, sync or update from it - make exactly one real "
        f"two-parent merge of the base commit {base} (git merge {base}) and resolve every "
        "conflict in that merge commit; the gate allows that one merge from base and the "
        "review then judges only its resolution. Never squash, flatten, cherry-pick or "
        "rebase the base in, never copy its changes as a plain commit, and never merge a "
        "later base commit. If the PR does not need its base branch, do not merge it."
    )


def with_merge_paragraph(instruction: Any, base: Any) -> Any:
    """``instruction`` followed by :func:`merge_paragraph` for ``base``; unchanged when
    ``base`` is not a full commit SHA, ``instruction`` is not text, or it already names
    ``base`` (it orders that merge itself)."""
    if not isinstance(instruction, str) or not isinstance(base, str) or not _SHA.fullmatch(base):
        return instruction
    if base in instruction:
        return instruction
    return instruction + merge_paragraph(base)


COPIED_BASE_LEAD = "the base branch looks copied in as a plain commit"
"""How the gate's copied-base finding begins."""

_MERGE_OF = re.compile(r"git merge ([0-9a-f]{40})")


def copied_base_in(problems: Iterable[Any]) -> str | None:
    """The base commit a copied-base finding in ``problems`` names (``""`` when it names
    none), or ``None`` when there is no such finding."""
    for problem in problems:
        if isinstance(problem, str) and problem.startswith(COPIED_BASE_LEAD):
            found = _MERGE_OF.search(problem)
            return found.group(1) if found else ""
    return None


def copied_base_ask(base: str) -> str:
    """What a finding asks for when the base was copied in: one real merge, not a smaller
    diff (the reviewer judges only a merge's resolution)."""
    merge = f"git merge {base}" if base else "git merge <the base commit>"
    return (
        "The reviewer cannot be shown this change in full because the base branch's own "
        f"changes are in it. Undo the copy and make one real two-parent merge ({merge}), "
        "resolving any conflict in that merge commit; the reviewer then judges only the "
        "merge's resolution, so the diff need not be smaller."
    )
