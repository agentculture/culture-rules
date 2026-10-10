"""d36: the merge-from-base paragraph every fixer try's instruction ends with.

``pr-fixer-dispatch`` appends it (rule text, after a blank line) to the request's
instruction, naming the run's base commit. A try's instruction later becomes the next
try's ``task`` (a retry's ``prior_instruction`` in ``queue.add``, the original task in the
review's request for changes), and the next dispatch appends the paragraph again; so the
paragraph is cut from the text before it is carried on, and every try reads it once.
Standard-library only.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MERGE_PARAGRAPH_HEAD", "without_merge_paragraph"]

MERGE_PARAGRAPH_HEAD = "Merging the base branch (d36):"
"""How the paragraph begins; ``pr-fixer-dispatch``'s instruction template starts it with
this, after a blank line, as its last paragraph."""


def without_merge_paragraph(text: Any) -> Any:
    """``text`` without its trailing d36 paragraph (anything else is returned as is)."""
    if not isinstance(text, str):
        return text
    at = text.rfind("\n\n" + MERGE_PARAGRAPH_HEAD)
    return text[:at] if at >= 0 else text
