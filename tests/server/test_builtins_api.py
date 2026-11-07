"""``GET /steps/builtins``: the registry's built-in code steps as data (R2a, t18)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.extensions import registry  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402


def _tally(store):
    return store


def _acme(ext):
    ext.builtin(
        "acme.tally",
        _tally,
        phrase="acme.tally: count the things",
        inputs=(("things", "array"),),
        outputs=(("count", "integer"), ("note", "string", False)),
    )


@pytest.fixture
def fake():
    registry().install("acme", _acme)
    yield "acme.tally"
    registry().uninstall("acme")


@pytest.fixture
def world():
    store = MemoryStore()
    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    token = ServiceTokens(store).issue("viewer", name="viewer", roles=["viewer"]).token
    return client, {"Authorization": f"Bearer {token}"}


def _items(world):
    client, headers = world
    return client.get("/steps/builtins", headers=headers).json()["items"]


def _item(world, name):
    return next(i for i in _items(world) if i["name"] == name)


def test_a_viewer_may_read_the_catalogue(world):
    client, headers = world
    assert client.get("/steps/builtins", headers=headers).status_code == 200


def test_the_route_needs_a_principal(world):
    client, _ = world
    assert client.get("/steps/builtins").status_code == 401


def test_the_route_lists_exactly_the_registered_builtins_in_order(world):
    assert [i["name"] for i in _items(world)] == [b.name for b in registry().builtins()]


def test_a_builtin_installed_by_an_extension_is_served_with_its_ports(world, fake):
    item = _items(world)[-1]
    assert item == {
        "name": fake,
        "label": "acme.tally: count the things",
        "inputs": [{"name": "things", "type": "array", "required": True}],
        "outputs": [
            {"name": "count", "type": "integer", "required": True},
            {"name": "note", "type": "string", "required": False},
        ],
    }


def test_a_builtin_gone_after_uninstall_is_not_served(world, fake):
    registry().uninstall("acme")
    registry().install("acme", lambda ext: None)
    assert fake not in [i["name"] for i in _items(world)]


@pytest.mark.parametrize(
    "name", ["json.parse", "text.join", "time.add", "date.local", "file.read", "list.filter"]
)
def test_each_building_block_is_served_with_ports(world, name):
    item = _item(world, name)
    assert item["inputs"]
    assert item["outputs"]


def test_markdown_render_keeps_the_editors_short_label(world):
    assert (
        _item(world, "markdown.render")["label"] == "markdown.render: fields as one Markdown page"
    )


def test_every_builtin_is_served_with_a_label(world):
    assert all(i["label"] for i in _items(world))


def test_the_web_tests_copy_of_the_builtin_catalogue_is_current():
    script = Path(__file__).resolve().parents[2] / "scripts" / "export-builtins.py"
    done = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
