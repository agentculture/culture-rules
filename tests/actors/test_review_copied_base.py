"""d36 /code-review #1 on #46: a base copied in as a plain commit is answered with one real
merge, never with "make a smaller fix".

When the gate finds the base branch copied in (its finding starts with
:data:`~culture_rules.actors.merge_hint.COPIED_BASE_LEAD`), the review's findings for the
oversized diff ask for one real merge of the base commit: the reviewer then judges only the
merge's resolution, so the diff need not shrink. Without that finding the ask is unchanged.
"""

from __future__ import annotations

from culture_rules.actors.merge_hint import COPIED_BASE_LEAD
from culture_rules.actors.review import _unreviewable

BASE = "89abcdef0123456789abcdef0123456789abcdef"
SIZE = "the diff is 513944 characters, over the 30000 the reviewer reads"
HINT = (
    f"{COPIED_BASE_LEAD}: the commit takes the base branch's own version of 1 file(s) ... "
    f"(git merge {BASE} would bring them in as a merge the reviewer does not judge). If "
    "those files are the PR's own change, ignore this"
)


def test_a_copied_base_asks_for_one_real_merge_not_a_smaller_fix():
    findings = _unreviewable(
        {"diff_problems": [SIZE, HINT, "a.sh: mode change (mode 100644 -> 100755)"]}
    )
    assert len(findings) == 3
    for f in findings:
        assert "Make a smaller" not in f["detail"]
        assert f"one real two-parent merge (git merge {BASE})" in f["detail"]
        assert "the diff need not be smaller" in f["detail"]
    assert findings[0]["detail"].startswith(SIZE)  # the size finding stays, reworded ask


def test_without_a_copied_base_the_ask_is_a_smaller_text_only_fix():
    (finding,) = _unreviewable({"diff_problems": [SIZE]})
    assert "Make a smaller, text-only fix" in finding["detail"]
    assert "git merge" not in finding["detail"]


def test_a_copied_base_finding_without_a_commit_still_asks_for_a_merge():
    _, finding = _unreviewable({"diff_problems": [SIZE, f"{COPIED_BASE_LEAD}: no sha here"]})
    assert "git merge <the base commit>" in finding["detail"]
    assert "Make a smaller" not in finding["detail"]
