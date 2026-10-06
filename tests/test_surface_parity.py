"""Surface parity: every registry verb exists on the CLI, as an MCP tool and in the OpenAPI paths.

The registry (``culture_rules.cli.verbs.REGISTRY``) is the single source of truth. This test is
data-driven: it enumerates the registry, so a new verb or route needs no edit here.

* CLI: the argparse tree built by ``_build_parser()`` has ``<noun> <verb>`` for every verb.
* MCP: ``tool_specs()`` lists ``<noun>_<verb>`` for every verb.
* OpenAPI: each verb is mapped to the HTTP operations its handler actually performs. They are
  recorded by invoking the handler (dry-run and apply, minimal and full arguments) against
  ``create_app(MemoryStore())`` with a real admin service token, through a recording transport.
  Each recorded ``(method, path template)`` must exist in ``api/openapi.json``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.cli import _build_parser  # noqa: E402
from culture_rules.cli.registry import Context, Param, Verb  # noqa: E402
from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.client.http import ApiClient  # noqa: E402
from culture_rules.engine.audit import AuditLog  # noqa: E402
from culture_rules.mcp import tools as mcp_tools  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

OPENAPI = Path(__file__).resolve().parents[1] / "api" / "openapi.json"

#: Registry verbs that deliberately make no HTTP call (still required on CLI and MCP).
#: Empty today: every noun ``overview`` reads its list route. ``serve`` and the top-level
#: ``overview`` are not registry verbs at all (they are local-only CLI commands).
LOCAL_ONLY_VERBS: frozenset[tuple[str, str]] = frozenset()

#: Local-only top-level CLI commands that are not registry verbs and have no API operation.
LOCAL_ONLY_COMMANDS = frozenset({"serve", "overview"})

HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options"}


# --------------------------------------------------------------------------- the three surfaces


def cli_commands(parser: argparse.ArgumentParser) -> set[tuple[str, str]]:
    """Every ``(noun, verb)`` the argparse tree accepts."""
    out: set[tuple[str, str]] = set()
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for noun, noun_parser in action.choices.items():
            for inner in noun_parser._actions:
                if isinstance(inner, argparse._SubParsersAction):
                    out |= {(noun, verb) for verb in inner.choices}
    return out


def mcp_tool_names(specs: list[dict]) -> set[str]:
    return {s["name"] for s in specs}


def openapi_operations(doc: dict) -> set[tuple[str, str]]:
    """``(METHOD, path template)`` for every operation in the OpenAPI document."""
    return {
        (method.upper(), path)
        for path, item in doc["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


def _template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", template)
    return re.compile("".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts) + "$")


def in_openapi(method: str, path: str, ops: set[tuple[str, str]]) -> bool:
    """Whether a concrete request path matches a documented ``(method, template)``."""
    return any(m == method and _template_regex(t).match(path) for m, t in ops)


# --------------------------------------------------------------------------- recording the verbs


class Recorder:
    """A transport onto an in-process app that records every request, whatever its outcome."""

    def __init__(self, store: MemoryStore):
        token = (
            ServiceTokens(store, AuditLog()).issue("bootstrap", name="alice", roles=["admin"]).token
        )
        self.client_http = TestClient(
            create_app(store),
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.token = token
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url.split("?", 1)[0]))
        r = self.client_http.request(method, url, headers=headers, content=body)
        return r.status_code, r.content

    def api(self) -> ApiClient:
        return ApiClient("http://127.0.0.1:8765", token=self.token, transport=self)


def dummy_args(verb: Verb, bundle: Path, *, full: bool) -> dict:
    """Arguments for a verb: required ones only, or (``full``) every declared parameter."""
    args: dict = {}
    for p in verb.params:
        if not (p.required or full):
            continue
        args[p.name] = _dummy(p, bundle)
    return args


def _dummy(p: Param, bundle: Path):
    if p.name == "path":
        return str(bundle)
    return {"string": "x", "integer": 1, "boolean": False, "object": {}, "array": [], "any": "x"}[
        p.type
    ]


def recorded_requests(verb: Verb, bundle: Path) -> set[tuple[str, str]]:
    """The HTTP operations ``verb`` performs, across dry-run/apply and minimal/full arguments."""
    seen: set[tuple[str, str]] = set()
    for apply in (False, True) if verb.mutating else (False,):
        for full in (False, True):
            rec = Recorder(MemoryStore())
            ctx = Context(client=rec.api(), apply=apply)
            try:
                verb.handler(ctx, **dummy_args(verb, bundle, full=full))
            except Exception:  # noqa: BLE001 - dummy args may be rejected; the request is recorded
                pass
            seen |= set(rec.calls)
    return seen


@pytest.fixture(scope="module")
def operations_by_verb(tmp_path_factory) -> dict[tuple[str, str], set[tuple[str, str]]]:
    bundle = tmp_path_factory.mktemp("bundle")
    for noun in ("rules", "workflows", "actors"):
        (bundle / noun).mkdir()
        (bundle / noun / "x.json").write_text(json.dumps({"id": "x"}))
    return {v.path: recorded_requests(v, bundle) for v in REGISTRY.verbs()}


# --------------------------------------------------------------------------- the parity check


def parity_failures(
    verbs: list[Verb],
    *,
    cli: set[tuple[str, str]],
    tool_names: set[str],
    api_ops: set[tuple[str, str]],
    ops_by_verb: dict[tuple[str, str], set[tuple[str, str]]],
) -> list[str]:
    """Every way a registry verb is missing from a surface (empty means parity holds)."""
    failures: list[str] = []
    for v in verbs:
        label = f"{v.noun} {v.name}"
        if v.path not in cli:
            failures.append(f"{label}: missing from the CLI")
        if v.tool_name not in tool_names:
            failures.append(f"{label}: missing from the MCP tool list ({v.tool_name})")
        ops = ops_by_verb.get(v.path, set())
        if not ops and v.path not in LOCAL_ONLY_VERBS:
            failures.append(f"{label}: made no HTTP call and is not listed as local-only")
        for method, path in sorted(ops):
            if not in_openapi(method, path, api_ops):
                failures.append(f"{label}: {method} {path} is not in the OpenAPI paths")
    return failures


@pytest.fixture(scope="module")
def surfaces():
    return {
        "cli": cli_commands(_build_parser()),
        "tool_names": mcp_tool_names(mcp_tools.tool_specs()),
        "api_ops": openapi_operations(json.loads(OPENAPI.read_text())),
    }


def check(surfaces, ops_by_verb, **override) -> list[str]:
    return parity_failures(REGISTRY.verbs(), ops_by_verb=ops_by_verb, **{**surfaces, **override})


# --------------------------------------------------------------------------- tests


def test_registry_is_not_empty():
    assert len(REGISTRY.verbs()) > 20


def test_every_registry_verb_is_on_the_cli(surfaces):
    missing = [v.path for v in REGISTRY.verbs() if v.path not in surfaces["cli"]]
    assert missing == []


def test_every_registry_verb_is_in_the_mcp_tool_list(surfaces):
    missing = [v.tool_name for v in REGISTRY.verbs() if v.tool_name not in surfaces["tool_names"]]
    assert missing == []
    assert len(surfaces["tool_names"]) == len(REGISTRY.verbs())  # and nothing extra


def test_every_registry_verb_maps_to_openapi_paths(surfaces, operations_by_verb):
    assert check(surfaces, operations_by_verb) == []


def test_local_only_exemptions_are_explicit_and_not_stale(operations_by_verb):
    for path in LOCAL_ONLY_VERBS:
        assert REGISTRY.get(*path) is not None, f"stale exemption {path}"
        assert operations_by_verb[path] == set(), f"{path} does make HTTP calls; drop exemption"
    top = {c for c in _top_level_commands() if c not in REGISTRY.nouns()}
    assert LOCAL_ONLY_COMMANDS <= top


def _top_level_commands() -> set[str]:
    for action in _build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def test_recording_sees_the_routes_a_verb_really_calls(operations_by_verb):
    assert ("POST", "/machines/x/drain") in operations_by_verb[("machines", "drain")]
    assert ("GET", "/rules") in operations_by_verb[("rules", "list")]


# ----- mutation checks: the parity test must fail when a surface loses something


def test_mutation_removing_one_mcp_tool_fails_parity(surfaces, operations_by_verb, monkeypatch):
    victim = REGISTRY.verbs()[0]
    real = mcp_tools.tool_specs
    monkeypatch.setattr(
        mcp_tools, "tool_specs", lambda: [s for s in real() if s["name"] != victim.tool_name]
    )
    mutated = mcp_tool_names(mcp_tools.tool_specs())
    failures = check(surfaces, operations_by_verb, tool_names=mutated)
    assert any(victim.tool_name in f for f in failures)


def test_mutation_removing_one_openapi_path_fails_parity(surfaces, operations_by_verb):
    victim = REGISTRY.get("machines", "drain")
    method, template = next(
        (m, t)
        for m, t in openapi_operations(json.loads(OPENAPI.read_text()))
        if any(
            r[0] == m and _template_regex(t).match(r[1]) for r in operations_by_verb[victim.path]
        )
    )
    doc = json.loads(OPENAPI.read_text())
    del doc["paths"][template][method.lower()]
    failures = check(surfaces, operations_by_verb, api_ops=openapi_operations(doc))
    assert any("machines drain" in f and "OpenAPI" in f for f in failures)


def test_mutation_removing_one_cli_command_fails_parity(surfaces, operations_by_verb):
    victim = REGISTRY.verbs()[-1]
    failures = check(surfaces, operations_by_verb, cli=surfaces["cli"] - {victim.path})
    assert any("missing from the CLI" in f for f in failures)


def test_a_verb_with_no_http_call_and_no_exemption_fails(surfaces, operations_by_verb):
    victim = REGISTRY.verbs()[0]
    failures = check(surfaces, {**operations_by_verb, victim.path: set()})
    assert any("not listed as local-only" in f for f in failures)
