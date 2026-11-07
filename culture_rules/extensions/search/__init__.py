"""The ``search`` extension: ``search.web`` through a search app actor (Brave).

The port is :class:`culture_rules.extensions.search.port.SearchWebPort` and the client
:class:`culture_rules.extensions.search.brave.BraveSearch`. Registering imports nothing but
the kind's typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import INT, REQ_STR, STR, ParamSpec, kind

__all__ = ["KINDS", "register"]

KINDS = (
    kind(
        "search.web",
        "Search the web through a search app actor (Brave)",
        label="Search the web",
        declarable_by_apps=True,
        actors=({"kinds": ["app"], "surface": "search"},),
        actor=REQ_STR,
        query=REQ_STR,
        count=INT,
        freshness=ParamSpec(
            "str",
            choices=(
                ("pd", "Past day"),
                ("pw", "Past week"),
                ("pm", "Past month"),
                ("py", "Past year"),
            ),
            choose="Any time",
        ),
        country=STR,
        lang=STR,
    ),
)


def register(ext: Extension) -> None:
    """``search.web`` (``port.SearchWebPort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_phrases("search.web", words=(("query", '"{}"'),))
    ext.action_port("search.web", Lazy(__name__ + ".port", "SearchWebPort", store=True))
