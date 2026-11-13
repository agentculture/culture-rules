"""Boundary t8, acceptance 1 (c26, h21): core imports no app.

The extension side is one package per app under ``culture_rules/extensions/<app>/``. Every
other module of ``culture_rules`` is core, and no core module imports - statically, lazily
inside a function, through ``importlib.import_module`` or as a :class:`Lazy` target -
``culture_rules.apps`` (gone) or an app's package. The one core module allowed to name the app
packages is the built-in loader, ``culture_rules/extensions/builtin.py``.

Apps may lean on one another; those edges are pinned here so a new one is a conscious choice.

d9: core does not spell an app's names either. No string literal in a core module (docstrings
aside) holds a non-core extension's action kind, alias, built-in or store collection, or a type
in the namespace of the event types it registers; the few that remain are pinned in
:data:`APP_LITERALS_ALLOWED`, each with its reason, so a new one fails.
"""

from __future__ import annotations

import ast
import re
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


# --------------------------------------------------------------------------- d9: no app names


def _app_names() -> tuple[frozenset[str], frozenset[str]]:
    """What only an app may spell: every non-core extension's action kinds (and aliases),
    built-ins and store collections, and the namespaces of the event types they register
    (``discord``, ``github``, ``jira``). Read from the registry, so a new extension is guarded
    with no edit here."""
    from culture_rules.extensions import registry

    reg = registry()
    names = {b.name for b in reg.builtins() if b.extension != "core"}
    for name, kind in reg.action_kinds().items():
        if reg._tables["kinds"][name].extension != "core":
            names |= {name, *reg.aliases(name)}
    spaces = {t.split(".")[0] for t, e in reg.event_types().items() if e.extension != "core"}
    for entry in reg._tables["collections"].values():
        if entry.extension != "core":
            names |= set(entry.provider())  # an app's store collections (gateway_state, ...)
    return frozenset(names), frozenset(spaces)


def _pattern(names: frozenset[str], spaces: frozenset[str]) -> re.Pattern[str]:
    alternatives = [re.escape(n) + r"(?![\w])" for n in sorted(names, key=len, reverse=True)]
    alternatives += [re.escape(s) + r"\.(?:[a-z_*]|$)" for s in sorted(spaces)]
    return re.compile(r"(?<![\w./-])(" + "|".join(alternatives) + ")")


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Module, class and function docstrings and bare string statements (attribute docs):
    prose, which may name an app; code may not."""
    found = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found.add(id(node.value))
    return found


def app_literals_in(source: str, pattern: re.Pattern[str]) -> set[str]:
    """The string literals (docstrings aside) in ``source`` that spell an app name."""
    tree = ast.parse(source)
    prose = _docstring_ids(tree)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
        and pattern.search(node.value)
    }


#: (core module, literal) pairs that still spell an app name, each with its reason. A new one
#: fails the test: give the knowledge to the app through a registry seam instead.
APP_LITERALS_ALLOWED = {
    # the loader: core's ``message`` kind is shown in the editor as the Discord kind that took
    # over its legacy actor form (shown_as); builtin.py is the one module that names apps
    ("extensions/builtin.py", "discord.message"),
    # help text served in the JSON schemas and the OpenAPI contract (field docs): changing it
    # changes the pinned contract, so it waits for a contract release
    ("model/action.py", "Action type, e.g. github.comment, mesh.message, http.call"),
    ("model/rule.py", "__RULE_MAX_ATTEMPTS_DOC__"),
    # the CLI's explain catalogue: user-facing help naming the kinds a model actor serves
    ("explain/catalog.py", "__CATALOG_MODEL_ACTORS__"),
    # left in core after d9 (outside its list; reported as later seams):
    # - the model rule's endpoint kinds (one enabled endpoint actor per model)
    ("actors/model_rule.py", "llm.complete"),
    ("actors/model_rule.py", "audio.transcribe"),
    ("actors/model_rule.py", "decider.assess"),
    # - the wait step's head_unchanged guard reads the PR head through github.pr_head
    ("engine/runs.py", "action:github.pr_head"),
    ("engine/runs.py", "github.pr_head"),
    # - the hook sink's settle-on-check types
    ("events/hook_sink.py", "github.checks.suite_completed"),
    ("events/hook_sink.py", "github.checks.workflow_completed"),
    # - the backup's run-history collections name github.comment's once claims
    ("ops/backup.py", "github_comment_once"),
    # - the attempt budget's reset signals (a human push, green checks)
    ("node/firing.py", "github."),  # BUDGET_RESET_FAMILIES: the rules a reset concerns
    ("node/firing.py", "github.pr.synchronize"),
    ("node/firing.py", "github.pr.checks_settled"),
}
#: Long literals are pinned by a stable prefix (the placeholder stands for the literal that
#: starts with it).
_LONG_LITERALS = {
    "__RULE_MAX_ATTEMPTS_DOC__": "Attempt budget (>= 1): after ``max_attempts`` admitted runs",
    "__CATALOG_MODEL_ACTORS__": "## Model actors (needs culture-rules >= 0.22.0)",
}


def _pinned(rel: str, literal: str) -> bool:
    if (rel, literal) in APP_LITERALS_ALLOWED:
        return True
    return any(
        (rel, key) in APP_LITERALS_ALLOWED and literal.startswith(prefix)
        for key, prefix in _LONG_LITERALS.items()
    )


def _core_app_literals() -> dict[str, set[str]]:
    pattern = _pattern(*_app_names())
    found: dict[str, set[str]] = {}
    for path in _sources():
        if _owner(path) is not None:
            continue
        rel = path.relative_to(PKG).as_posix()
        literals = app_literals_in(path.read_text(encoding="utf-8"), pattern)
        if literals:
            found[rel] = literals
    return found


def test_no_core_module_spells_an_app_name_outside_the_allowlist():
    found = _core_app_literals()
    unexpected = {
        rel: sorted(lit for lit in lits if not _pinned(rel, lit)) for rel, lits in found.items()
    }
    assert {rel: lits for rel, lits in unexpected.items() if lits} == {}


def test_every_allowlisted_app_literal_is_still_there():
    found = _core_app_literals()
    stale = {
        (rel, lit)
        for rel, lit in APP_LITERALS_ALLOWED
        if not any(_pinned(rel, value) for value in found.get(rel, ()))
        or (lit not in _LONG_LITERALS and lit not in found.get(rel, ()))
    }
    assert stale == set()


@pytest.mark.parametrize(
    "source",
    [
        "KIND = 'github.comment'\n",
        "def f(t):\n    return t.startswith('discord.')\n",
        "X = f'type {name} on jira.issue'\n",
        "B = 'attachment.fetch'\n",
        "A = ('llm.complete',)\n",
        "C = 'gateway_state'\n",
    ],
)
def test_the_app_name_check_sees_every_literal_form(source):
    assert app_literals_in(source, _pattern(*_app_names()))


@pytest.mark.parametrize(
    "source",
    [
        '"""A docstring may name github.comment."""\n',
        "def f():\n    '''Prose: discord.* events.'''\n",
        "X = 'machine.drain'\n",
        "X = 'http.response.start'\n",
        "X = 'https://github.com/agentculture'\n",
    ],
)
def test_the_app_name_check_ignores_prose_and_core_names(source):
    assert app_literals_in(source, _pattern(*_app_names())) == set()


# --- no spec-tool name in code, tests or the editor ---------------------------------------------
# The needle is built from two halves so this file never spells it out itself.
_SPEC_TOOL_NEEDLE = "de" + "vague"
_SPEC_TOOL_ROOTS = ("culture_rules", "tests", "web/src")
_SPEC_TOOL_SKIP_DIRS = {"node_modules", "__pycache__", ".pytest_cache", "dist"}

#: Files (relative to the repo root) that mention the needle today, each with its reason. A new
#: file that mentions it fails; a pinned file that stops mentioning it fails too, so the list
#: cannot rot.
SPEC_TOOL_MENTIONS_ALLOWED = {
    "culture_rules/extensions/repodocs/__init__.py": "repodocs syncs the tool's record paths",
    "culture_rules/extensions/repodocs/port.py": "repodocs syncs the tool's record paths",
    "culture_rules/model/action_kinds.py": "the repodocs kind's help text names those paths",
    "tests/fixtures/workflow_descriptions.json": "a fixture describing the repodocs kind",
    "tests/node/test_repodocs_sync.py": "tests repodocs syncing the tool's record paths",
    "tests/rules/fixtures/github-app.live.json": "a fixture whose text names those paths",
    "tests/rules/fixtures/github-app.with-author.json": "a fixture whose text names those paths",
    "web/src/fixtures/action-kinds.data.ts": "snapshot of the action catalogue (repodocs kind)",
    "web/src/rules/ActionPicker.repodocs.test.tsx": "editor test for the repodocs kind",
}


def _files_mentioning_spec_tool() -> set[str]:
    found: set[str] = set()
    for root in _SPEC_TOOL_ROOTS:
        for path in (ROOT / root).rglob("*"):
            if not path.is_file() or _SPEC_TOOL_SKIP_DIRS & set(path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable
            if _SPEC_TOOL_NEEDLE in text.lower():
                found.add(path.relative_to(ROOT).as_posix())
    return found


def test_no_new_file_names_the_spec_tool():
    found = _files_mentioning_spec_tool()
    new = sorted(found - set(SPEC_TOOL_MENTIONS_ALLOWED))
    assert not new, f"files naming the spec tool, not pinned in the allowlist: {new}"
    stale = sorted(set(SPEC_TOOL_MENTIONS_ALLOWED) - found)
    assert not stale, f"pinned files that no longer name it, drop them: {stale}"
