"""The command registry: each verb is registered once; CLI, explain and learn all read it."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from culture_rules.cli import _build_parser, main
from culture_rules.cli.registry import ROLES, DuplicateVerb, Registry, Verb
from culture_rules.cli.verbs import REGISTRY
from culture_rules.explain import known_paths

NOUNS = {"rules", "workflows", "actors", "machines", "runs"}
ROOT = Path(__file__).resolve().parents[2] / "culture_rules"


def _noop(ctx, **params):
    return {}


def test_registry_covers_the_five_nouns_with_overview():
    assert {v.noun for v in REGISTRY.verbs()} == NOUNS
    for noun in NOUNS:
        assert REGISTRY.get(noun, "overview") is not None
        assert REGISTRY.get(noun, "list") is not None
        assert REGISTRY.get(noun, "show") is not None


def test_required_verbs_exist_per_noun():
    for noun in ("rules", "workflows", "actors", "machines"):
        names = {v.name for v in REGISTRY.verbs(noun)}
        assert {
            "create",
            "update",
            "enable",
            "disable",
            "delete",
            "restore",
        } <= names
        # the API's export/import bundle carries rules, workflows and actors, not machines
        assert ({"export", "import"} <= names) == (noun != "machines")
    assert "run" in {v.name for v in REGISTRY.verbs("rules")}
    assert {"drain", "undrain"} <= {v.name for v in REGISTRY.verbs("machines")}
    assert {"pause", "resume", "cancel"} <= {v.name for v in REGISTRY.verbs("runs")}


def test_every_verb_carries_name_schema_mutating_flag_and_role():
    for v in REGISTRY.verbs():
        assert v.noun
        assert v.name
        assert v.summary
        assert v.role in ROLES
        assert isinstance(v.mutating, bool)
        schema = v.params_schema()
        assert schema["type"] == "object"
        assert "properties" in schema
        assert ("apply" in schema["properties"]) == v.mutating
        if v.mutating:
            assert v.role != "viewer"
        else:
            assert v.role == "viewer"


def test_duplicate_registration_is_refused():
    reg = Registry()
    reg.add(Verb("rules", "list", "x", handler=_noop))
    with pytest.raises(DuplicateVerb):
        reg.add(Verb("rules", "list", "again", handler=_noop))


def test_roles_are_ordered_viewer_editor_admin():
    assert list(ROLES) == ["viewer", "editor", "admin"]


def test_cli_parser_enumerates_exactly_the_registry():
    parser = _build_parser()
    nouns = next(a for a in parser._actions if a.dest == "command").choices
    for noun in NOUNS:
        verbs = set(nouns[noun]._subparsers._group_actions[0].choices)
        assert verbs == {v.name for v in REGISTRY.verbs(noun)}


def test_every_verb_has_explain_entry_and_noun_entries():
    paths = set(known_paths())
    for noun in NOUNS:
        assert (noun,) in paths
    for v in REGISTRY.verbs():
        assert (v.noun, v.name) in paths, v.path
    assert ("serve",) in paths


def test_every_verb_appears_in_learn(capsys):
    assert main(["learn"]) == 0
    text = capsys.readouterr().out
    assert main(["learn", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    listed = {tuple(c["path"]) for c in payload["commands"]}
    for v in REGISTRY.verbs():
        assert f"culture-rules {v.noun} {v.name}" in text, v.path
        assert v.path in listed
    assert ("serve",) in listed


def test_cli_and_client_only_talk_to_the_http_api():
    """No data access through the store or engine: only the HTTP client."""
    forbidden = ("culture_rules.store", "culture_rules.engine", "culture_rules.server")
    offenders = []
    for sub in ("cli", "client"):
        for path in (ROOT / sub).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                    if node.module == "culture_rules":
                        mods += [f"culture_rules.{a.name}" for a in node.names]
                for mod in mods:
                    if mod.startswith(forbidden) and "serve" not in path.name:
                        offenders.append((path.name, mod))
    assert offenders == []


def test_registry_roles_match_the_server_policy():
    """o11/o12: the role a verb declares is the role the server enforces on its route."""
    from culture_rules.auth.policy import required_role

    routes = {
        ("rules", "list"): ("GET", "/rules"),
        ("rules", "create"): ("POST", "/rules"),
        ("rules", "purge"): ("POST", "/rules/r1/purge"),
        ("rules", "replay"): ("POST", "/replay"),
        ("rules", "run"): ("POST", "/runs"),
        ("machines", "drain"): ("POST", "/machines/m1/drain"),
        ("runs", "pause"): ("POST", "/controls/pause"),
    }
    by_key = {(v.noun, v.name): v for v in REGISTRY.verbs()}
    for key, (method, path) in routes.items():
        assert by_key[key].role == required_role(method, path), key
