"""The ``llm`` extension: ``llm.complete``, an actor's brief on an OpenAI-compatible model.

The port is :class:`culture_rules.extensions.llm.port.LlmCompletePort` (an agent actor's
endpoint, never with tools; briefs from the actor's configuration). Registering imports
nothing but the kind's typed params.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import INT, REQ_STR, STR, ParamSpec, kind

__all__ = ["KINDS", "register"]

LLM_COMPLETE = "llm.complete"

KINDS = (
    kind(
        LLM_COMPLETE,
        "Complete an actor's brief with an OpenAI-compatible model on an agent actor (no tools)",
        label="Summarise with a language model",
        any_of=(("content", "content_path"),),
        actors=({"kinds": ["agent"], "has": ["base_url"]},),
        actor=REQ_STR,
        brief=ParamSpec(
            "str",
            needed=True,
            options_from="briefs",
            choose="Choose one of the actor's briefs…",
        ),  # a name in the actor's params.briefs; required at run time, no default
        answer=ParamSpec("dict", widget="json"),  # {shape: <culture_rules.model.shape spec>}
        content=ParamSpec("any", widget="multiline"),  # or content_path (d2): one of the two
        content_path=STR,  # a schema-1 transcript file; needs content_root (checked at run time)
        content_root=STR,
        content_max_chars=INT,
        max_tokens=INT,
        temperature=ParamSpec("any", widget="number"),
        not_after=STR,  # ISO-8601: past it the work fails llm_deadline instead of queueing
        cache=ParamSpec(
            "bool", label="Keep the first answer for this file"
        ),  # answer + content_path: keep the first valid answer
        cache_name=STR,  # the cache file stem (answer.<name>.<sha16>.json), default "recording"
        cache_label=STR,  # the cache document's brief label, default "recording"
    ),
)


def _has_shape(value: Any) -> bool:
    """``answer`` reads its word only when it holds a ``shape`` (an object, never a literal)."""
    return isinstance(value, Mapping) and "shape" in value


#: What describe says for ``llm.complete`` after its name and actor.
PHRASES = (
    ("brief", "brief {}"),
    ("answer", "checking the answer against its configured shape", _has_shape),
    ("cache", "keeping the first valid answer"),
    ("cache_name", "cache name {}"),
    ("cache_label", "cache label {}"),
)


def register(ext: Extension) -> None:
    """``llm.complete`` (``port.LlmCompletePort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_phrases(LLM_COMPLETE, words=PHRASES)
    ext.action_port(LLM_COMPLETE, Lazy(__name__ + ".port", "LlmCompletePort", store=True))
