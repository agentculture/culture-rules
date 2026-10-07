"""Workflows trusted to push (d20 round 2): a set of definition digests pinned in code.

The PR fixer's safety rests on the exact shape of its workflow: the gate is the actor-less
built-in, the agent is an ``ai`` step, the reviewer runs through the locked bridge path,
one verdict step per try. Editing the workflow (an actor-routed "gate" that supplies a fake
diff, a decoy step standing in for the real writer, two verdict steps per try ...) could
undo that in ways per-step checks keep chasing. So the class is closed here: ``github.push``
and the built-in ``review`` step refuse any run whose **pinned** workflow definition does
not hash to a digest in :data:`TRUSTED_WORKFLOW_DIGESTS` (``workflow_not_trusted``). An
edited workflow still runs; it can never push.

The digest (:func:`workflow_digest`) is sha256 over the definition the run pinned, put
through the workflow model (so field order and defaults do not matter) without
``version`` (which every save bumps), as compact sorted JSON. It is recomputed from the
run's pinned definition, never read from a stored digest.

Rolling out a legitimate change: edit ``docs/rules/pr-fixer/workflows/pr-fixer.json``;
``tests/rules/test_trusted_workflow.py`` then fails and prints the new digest; add it to
the set in the same PR (keep the old one while runs pinned to it may still push, remove it
in a later release); ship the wheel, upgrade every node, then import the workflow. It is a
set so the split workflows planned for d21 can be listed beside it. Standard-library only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

__all__ = ["TRUSTED_WORKFLOW_DIGESTS", "workflow_digest", "workflow_refusal"]

TRUSTED_WORKFLOW_DIGESTS: frozenset[str] = frozenset(
    {
        # pr-fixer, docs/rules/pr-fixer/workflows/pr-fixer.json (d20 round 2)
        "sha256:01ece1cd69f995aeb0e931553546aa905bdfa20bc7fc530dfbaea8c75f4fc5d6",
    }
)


def workflow_digest(definition: Any) -> str | None:
    """The trust digest of a workflow definition (dict or model), or ``None`` if it does
    not parse as a workflow."""
    from culture_rules.model.workflow import Workflow  # noqa: PLC0415 - model import is cheap

    try:
        model = (
            definition
            if isinstance(definition, Workflow)
            else Workflow.from_dict(dict(definition), strict=False)
        )
        doc = model.to_dict()
    except Exception:  # noqa: BLE001 - anything unparseable is simply not trusted
        return None
    doc.pop("version", None)
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def workflow_refusal(run: Mapping[str, Any] | None) -> str | None:
    """``workflow_not_trusted`` unless the run's pinned workflow is a trusted one."""
    pin = (run or {}).get("workflow") if isinstance(run, Mapping) else None
    definition = pin.get("definition") if isinstance(pin, Mapping) else None
    if not isinstance(definition, Mapping):
        return "workflow_not_trusted"
    return (
        None if workflow_digest(definition) in TRUSTED_WORKFLOW_DIGESTS else "workflow_not_trusted"
    )
