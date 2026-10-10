"""Hatch build hook: ship the built web UI (``web/dist``) and the deploy data inside the wheel.

CI runs ``npm ci && npm run build`` in ``web/`` before ``uv build``; this hook then force-includes
``web/dist`` as ``culture_rules/web_dist`` (never committed; git-ignored). It applies to the sdist
too (``uv build`` builds the wheel from the sdist), so the build survives the round trip. With
``CULTURE_RULES_REQUIRE_WEB=1`` (release CI) a missing build fails the build instead of silently
shipping a wheel without the UI. Without it, a plain ``uv sync`` / editable install works with no
Node installed.

#45 t2: the node installer and the pr-fixer bundle with its two manifests ship the same way, as
``culture_rules/deploy_data`` (read through :mod:`culture_rules.deploy_data`). They are always
in the tree, so a missing source fails the build.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ImportError:  # pragma: no cover - only absent when imported outside a build (tests)
    BuildHookInterface = object  # type: ignore[assignment,misc]

WEB_DIST_TARGET = "culture_rules/web_dist"
DEPLOY_TARGET = "culture_rules/deploy_data"
# source (relative to the project root) -> target in the wheel
DEPLOY_SOURCES = {
    "deploy/node/install.sh": f"{DEPLOY_TARGET}/install.sh",
    "docs/rules/pr-fixer/workflows": f"{DEPLOY_TARGET}/pr-fixer/workflows",
    "docs/rules/pr-fixer/rules": f"{DEPLOY_TARGET}/pr-fixer/rules",
    "docs/rules/pr-fixer/actors": f"{DEPLOY_TARGET}/pr-fixer/actors",
    "docs/rules/pr-fixer/variables.json": f"{DEPLOY_TARGET}/pr-fixer/variables.json",
    "docs/rules/pr-fixer/release.json": f"{DEPLOY_TARGET}/pr-fixer/release.json",
}


def web_force_include(root: Path | str, *, require: bool) -> dict[str, str]:
    """``{source: target}`` for the built UI, empty when absent (an error if ``require``)."""
    dist = Path(root) / "web" / "dist"
    if (dist / "index.html").is_file():
        return {str(dist): WEB_DIST_TARGET}
    # a wheel built from the sdist: the UI is already in the package tree there (and no git
    # ignore applies), so it ships as ordinary package data; force-including it again would
    # add every file twice
    if (Path(root) / WEB_DIST_TARGET / "index.html").is_file():
        return {}
    if require:
        raise RuntimeError(
            "web/dist/index.html is missing: run `npm ci && npm run build` in web/ before "
            "building (CULTURE_RULES_REQUIRE_WEB is set)"
        )
    return {}


def deploy_force_include(root: Path | str) -> dict[str, str]:
    """``{source: target}`` for the deploy data; an error when a source is missing."""
    root = Path(root)
    out: dict[str, str] = {}
    for rel, target in DEPLOY_SOURCES.items():
        # a wheel built from the sdist: already in the package tree, ships as package data
        if (root / target).exists():
            continue
        source = root / rel
        if not source.exists():
            raise RuntimeError(f"{rel} is missing: the wheel must ship it as {target}")
        out[str(source)] = target
    return out


class WebDistHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        require = os.environ.get("CULTURE_RULES_REQUIRE_WEB", "").lower() in ("1", "true", "yes")
        build_data["force_include"].update(web_force_include(self.root, require=require))
        build_data["force_include"].update(deploy_force_include(self.root))
