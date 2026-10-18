"""``workflows update`` (CLI and MCP) sends the revision it read as ``expected_revision``
(spec c34, deviation d7), against a real ``create_app(MemoryStore())``."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from culture_rules.cli import _api  # noqa: E402
from culture_rules.client.http import ApiClient  # noqa: E402
from culture_rules.mcp.tools import ToolError, call_tool  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.cli.test_nouns_api import Wire, jrun, run, snapshot, write_body  # noqa: E402
from tests.server.conftest import workflow_body  # noqa: E402

ALICE = {"X-Culture-Identity": "alice"}


class BodyWire(Wire):
    """A :class:`Wire` that also records each request's JSON body."""

    def __init__(self, store):
        super().__init__(store)
        self.bodies: list[tuple[str, str, object]] = []

    def __call__(self, method, url, headers, body):
        self.bodies.append((method, url, json.loads(body) if body else None))
        return super().__call__(method, url, headers, body)

    def puts(self):
        return [b for m, _, b in self.bodies if m == "PUT"]


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def wire(store, monkeypatch):
    w = BodyWire(store)
    monkeypatch.setattr(
        _api,
        "make_client",
        lambda api_url=None: ApiClient("http://127.0.0.1:8765", token=w.token, transport=w),
    )
    return w


def _client(wire):
    return ApiClient("http://127.0.0.1:8765", token=wire.token, transport=wire)


def _seed(wire, store):
    wire.tc.post("/workflows", json=workflow_body("wf"), headers=ALICE)
    assert store.get("workflows", "wf")["revision"] == 1


def _named(name):
    return {**workflow_body("wf"), "name": name}


def test_update_reads_the_revision_and_sends_it(wire, store, capsys, tmp_path):
    _seed(wire, store)
    body = write_body(tmp_path, _named("cli"))
    out = jrun(capsys, "workflows", "update", "wf", "--body", f"@{body}", "--apply")
    assert out["result"]["revision"] == 2
    assert wire.puts() == [{**_named("cli"), "expected_revision": 1}]


def test_a_body_from_show_carries_its_revision_and_never_sends_it(wire, store, capsys, tmp_path):
    _seed(wire, store)
    body = write_body(tmp_path, {**_named("shown"), "revision": 1})
    jrun(capsys, "workflows", "update", "wf", "--body", f"@{body}", "--apply")
    (sent,) = wire.puts()
    assert sent["expected_revision"] == 1
    assert "revision" not in sent
    # a stale shown body is refused, and nothing is written
    wire.tc.put("/workflows/wf", json={**_named("other"), "expected_revision": 2}, headers=ALICE)
    before = snapshot(store)
    rc, _, err = run(capsys, "workflows", "update", "wf", "--body", f"@{body}", "--apply", "--json")
    assert rc == 1
    assert "revision 3" in err
    assert snapshot(store) == before


def test_expected_revision_flag_wins_and_stale_is_a_user_error(wire, store, capsys, tmp_path):
    _seed(wire, store)
    body = write_body(tmp_path, {**_named("flag"), "revision": 9})
    jrun(
        capsys,
        "workflows",
        "update",
        "wf",
        "--body",
        f"@{body}",
        "--expected-revision",
        "1",
        "--apply",
    )
    assert wire.puts()[-1]["expected_revision"] == 1
    rc, _, err = run(
        capsys, "workflows", "update", "wf", "--body", f"@{body}", "--expected-revision", "1",
        "--apply", "--json",
    )  # fmt: skip
    assert rc == 1
    assert "revision 2" in err


def test_dry_run_shows_the_expected_revision_and_writes_nothing(wire, store, capsys, tmp_path):
    _seed(wire, store)
    before = snapshot(store)
    body = write_body(tmp_path, _named("dry"))
    out = jrun(capsys, "workflows", "update", "wf", "--body", f"@{body}")
    assert out["dry_run"] is True
    assert out["would"]["body"]["expected_revision"] == 1
    assert wire.mutating() == []
    assert snapshot(store) == before


def test_rules_update_takes_no_expected_revision(wire, store, capsys):
    from culture_rules.cli.verbs import REGISTRY

    params = {v.name: [p.name for p in v.params] for v in REGISTRY.verbs("rules")}
    assert "expected_revision" not in params["update"]
    wf = {v.name: [p.name for p in v.params] for v in REGISTRY.verbs("workflows")}
    assert "expected_revision" in wf["update"]


def test_mcp_workflows_update_sends_the_revision(wire, store):
    _seed(wire, store)
    client = _client(wire)
    out = call_tool("workflows_update", {"id": "wf", "body": _named("mcp"), "apply": True}, client)
    assert out["result"]["revision"] == 2
    assert wire.puts()[-1]["expected_revision"] == 1
    with pytest.raises(ToolError, match="revision 2"):
        call_tool(
            "workflows_update",
            {"id": "wf", "body": _named("stale"), "expected_revision": 1, "apply": True},
            client,
        )
    assert store.get("workflows", "wf")["name"] == "mcp"
