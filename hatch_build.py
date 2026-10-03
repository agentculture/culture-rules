"""Hatch build hook: ship the built web UI (``web/dist``) inside the wheel.

CI runs ``npm ci && npm run build`` in ``web/`` before ``uv build``; this hook then force-includes
``web/dist`` as ``culture_rules/web_dist`` (never committed; git-ignored). It applies to the sdist
too (``uv build`` builds the wheel from the sdist), so the build survives the round trip. With
``CULTURE_RULES_REQUIRE_WEB=1`` (release CI) a missing build fails the build instead of silently
shipping a wheel without the UI. Without it, a plain ``uv sync`` / editable install works with no
Node installed.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ImportError:  # pragma: no cover - only absent when imported outside a build (tests)
    BuildHookInterface = object  # type: ignore[assignment,misc]

WEB_DIST_TARGET = "culture_rules/web_dist"


def web_force_include(root: Path | str, *, require: bool) -> dict[str, str]:
    """``{source: target}`` for the built UI, empty when absent (an error if ``require``)."""
    dist = Path(root) / "web" / "dist"
    if (dist / "index.html").is_file():
        return {str(dist): WEB_DIST_TARGET}
    if require:
        raise RuntimeError(
            "web/dist/index.html is missing: run `npm ci && npm run build` in web/ before "
            "building (CULTURE_RULES_REQUIRE_WEB is set)"
        )
    return {}


class WebDistHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        require = os.environ.get("CULTURE_RULES_REQUIRE_WEB", "").lower() in ("1", "true", "yes")
        build_data["force_include"].update(web_force_include(self.root, require=require))
