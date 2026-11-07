"""Boundary t8, acceptance 1 (c26, h21): core imports no app.

The extension side is one package per app under ``culture_rules/extensions/<app>/``. Every
other module of ``culture_rules`` is core, and no core module imports - statically, lazily
inside a function, through ``importlib.import_module`` or as a :class:`Lazy` target -
``culture_rules.apps`` (gone) or an app's package. The one core module allowed to name the app
packages is the built-in loader, ``culture_rules/extensions/builtin.py``.

Apps may lean on one another; those edges are pinned here so a new one is a conscious choice.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "culture_rules"
EXT = PKG / "extensions"

APPS = frozenset(p.name for p in EXT.iterdir() if (p / "__init__.py").is_file())
LOADER = "extensions/builtin.py"

#: (importing app, imported app): the cross-extension imports there are on disk.
CROSS_EXTENSION = {
    ("discord", "http"),  # attachment.fetch reuses http.call's vetted opener
    ("github", "discord"),  # github.merge reads the clicked button's one-time code
    ("library", "http"),  # library.push refuses a redirect the way http.call does
    ("repodocs", "library"),  # repodocs.sync pushes through library.push's transport
    ("stt", "library"),  # audio.transcribe uploads with library.push's multipart encoder
}


def _imported(source: str) -> list[str]:
    """Every module ``source`` imports or names as an import target (dotted names; for
    ``from M import a`` both ``M`` and ``M.a``)."""
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names += [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        elif isinstance(node, ast.Call) and node.args:
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            first = node.args[0]
            if (
                called in ("import_module", "__import__", "Lazy")
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                names.append(first.value)
    return names


def _app_of(module: str) -> str | None:
    """The app a dotted module belongs to (``"apps"`` for the old app package)."""
    parts = module.split(".")
    if parts[:2] == ["culture_rules", "apps"]:
        return "apps"
    if parts[:2] == ["culture_rules", "extensions"] and len(parts) > 2 and parts[2] in APPS:
        return parts[2]
    return None


def _owner(path: Path) -> str | None:
    """The app a source file belongs to, ``None`` for core."""
    rel = path.relative_to(PKG).parts
    return rel[1] if rel[0] == "extensions" and len(rel) > 2 and rel[1] in APPS else None


def _sources() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "web_dist" not in p.parts)


def app_imports_in(source: str) -> set[str]:
    """The apps ``source`` imports."""
    return {app for app in map(_app_of, _imported(source)) if app}


def test_the_extension_side_is_one_package_per_app():
    assert APPS == {
        "decider",
        "discord",
        "email",
        "github",
        "http",
        "jira",
        "library",
        "llm",
        "machine",
        "newspaper",
        "repodocs",
        "search",
        "stt",
        "transcript",
    }
    assert not (PKG / "apps").exists()
    assert not (PKG / "server" / "hooks").exists()


def test_no_core_module_imports_an_app():
    hits = {
        str(path.relative_to(PKG)): sorted(app_imports_in(path.read_text(encoding="utf-8")))
        for path in _sources()
        if _owner(path) is None and path.relative_to(PKG).as_posix() != LOADER
    }
    assert {rel: apps for rel, apps in hits.items() if apps} == {}


def test_the_loader_names_every_app():
    loader = (PKG / LOADER).read_text(encoding="utf-8")
    assert app_imports_in(loader) == APPS


def test_cross_extension_imports_are_the_pinned_ones():
    edges = set()
    for path in _sources():
        owner = _owner(path)
        if owner is not None:
            imported = app_imports_in(path.read_text(encoding="utf-8")) - {owner}
            edges |= {(owner, app) for app in imported}
    assert edges == CROSS_EXTENSION


@pytest.mark.parametrize(
    "source",
    [
        "from culture_rules.extensions.github.client import GitHubApp\n",
        "from culture_rules.extensions import discord\n",
        "def f():\n    import culture_rules.apps.github\n",
        "import importlib\nimportlib.import_module('culture_rules.extensions.jira.hooks')\n",
        "Lazy('culture_rules.extensions.http.port', 'HttpCallPort')\n",
    ],
)
def test_the_check_sees_every_import_form(source):
    assert app_imports_in(source)


def test_core_and_the_registry_are_not_apps():
    source = (
        "from culture_rules.extensions import Lazy, registry\n"
        "from culture_rules.extensions.registry import Extension\n"
        "from culture_rules.node.actions import transcript_file\n"
    )
    assert app_imports_in(source) == set()


def test_the_gateways_health_section_is_the_discord_extensions():
    from culture_rules.extensions import registry

    owners = {e.name: e.extension for e in registry().health_sections()}
    assert owners == {"gateways": "discord"}


def test_the_gateways_section_reads_the_state_collection_the_gateway_writes():
    from culture_rules.extensions.discord import gateway, health

    assert health.GATEWAY_STATE_COLLECTION is gateway.GATEWAY_STATE_COLLECTION
