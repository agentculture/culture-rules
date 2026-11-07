"""The ``decider`` extension: ``decider.assess``, probabilities from the mesh's decider.

The port is :class:`culture_rules.extensions.decider.port.DeciderAssessPort`
(``POST <base_url>/v1/systemone``; it never writes text). Registering imports nothing but
the kind's typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import REQ_STR, REQ_TEXT, STR, ParamSpec, kind

__all__ = ["KINDS", "register"]

DECIDER_ASSESS = "decider.assess"

KINDS = (
    kind(
        DECIDER_ASSESS,
        "Ask the mesh's decider for probabilities over the options a step defines (no text)",
        label="Ask the decider",
        actors=({"kinds": ["agent"], "has": ["base_url"]},),
        actor=REQ_STR,
        state=REQ_TEXT,
        questions=ParamSpec(
            "dict", True, widget="json"
        ),  # {key: {type: choice|noul|score, instructions, criteria}}
        not_after=STR,  # ISO-8601: past it the work fails decider_deadline instead of queueing
    ),
)


def register(ext: Extension) -> None:
    """``decider.assess`` (``port.DeciderAssessPort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_phrases(DECIDER_ASSESS)
    ext.action_port(DECIDER_ASSESS, Lazy(__name__ + ".port", "DeciderAssessPort", store=True))
