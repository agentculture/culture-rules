"""The ``newspaper`` extension: the daily newspaper's built-in code steps (#53).

``newspaper.sections``, ``newspaper.filter``, ``newspaper.render`` and ``newspaper.issue``
are served from :mod:`culture_rules.extensions.newspaper.port` (the render and the issue
store live in :mod:`~culture_rules.extensions.newspaper.render` and
:mod:`~culture_rules.extensions.newspaper.issue`). They are flow built-ins the no-flow-code
plan deletes later; this package only holds them on the extension side. Registering imports
nothing.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy

__all__ = ["register"]

_PORT = __name__ + ".port"


def register(ext: Extension) -> None:
    """The four ``newspaper.*`` built-ins."""
    ext.builtin("newspaper.sections", Lazy(_PORT, "NewspaperSectionsPort"))
    ext.builtin("newspaper.filter", Lazy(_PORT, "NewspaperFilterPort"))
    ext.builtin("newspaper.render", Lazy(_PORT, "NewspaperRenderPort"))
    ext.builtin("newspaper.issue", Lazy(_PORT, "NewspaperIssuePort", store=True))
