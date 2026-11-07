"""The ``llm`` extension: ``llm.complete``, an actor's brief on an OpenAI-compatible model.

The port is :class:`culture_rules.extensions.llm.port.LlmCompletePort` (an agent actor's
endpoint, never with tools; briefs from the actor's configuration). Registering imports
nothing but the kind's typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import INT, REQ_STR, STR, ParamSpec, kind

__all__ = ["KINDS", "register"]

KINDS = (
    kind(
        "llm.complete",
        "Complete an actor's brief with an OpenAI-compatible model on an agent actor (no tools)",
        any_of=(("content", "content_path"),),
        actor=REQ_STR,
        content=ParamSpec("any"),  # or content_path (d2): one of the two is required
        content_path=STR,  # a schema-1 transcript file; needs content_root (checked at run time)
        content_root=STR,
        content_max_chars=INT,
        max_tokens=INT,
        temperature=ParamSpec("any"),
        not_after=STR,  # ISO-8601: past it the work fails llm_deadline instead of queueing
        brief=STR,  # a name in the actor's params.briefs; required at run time, no default
        answer=ParamSpec("dict"),  # {shape: <culture_rules.model.shape spec>}: a JSON answer
        cache=ParamSpec("bool"),  # answer + content_path: keep the first valid answer
        cache_name=STR,  # the cache file stem (answer.<name>.<sha16>.json), default "recording"
        cache_label=STR,  # the cache document's brief label, default "recording"
    ),
)


def register(ext: Extension) -> None:
    """``llm.complete`` (``port.LlmCompletePort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_port("llm.complete", Lazy(__name__ + ".port", "LlmCompletePort", store=True))
