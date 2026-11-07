"""``GET /actions/kinds``: the registry's kinds as data, in catalogue order (Boundary t9)."""

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
from culture_rules.model.action_kinds import ActionKind, ParamSpec  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

FAKE = ActionKind(
    "acme.ping",
    {
        "actor": ParamSpec("str", True),
        "text": ParamSpec("str", True, widget="multiline", label="Words"),
        "mode": ParamSpec("str", choices=(("fast", "Fast"), ("slow", "Slow")), choose="Pick"),
    },
    "Ping Acme",
    (("text", "mode"),),
    "Ping",
    ({"kinds": ["service"], "has": ["base_url"]},),
)


@pytest.fixture
def fake():
    registry().install("acme", lambda ext: ext.action_kind(FAKE, aliases=("acme.legacy",)))
    yield FAKE
    registry().uninstall("acme")


@pytest.fixture
def world():
    store = MemoryStore()
    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    token = ServiceTokens(store).issue("viewer", name="viewer", roles=["viewer"]).token
    return client, {"Authorization": f"Bearer {token}"}


def _items(world):
    client, headers = world
    return client.get("/actions/kinds", headers=headers).json()["items"]


def test_a_viewer_may_read_the_catalogue(world):
    client, headers = world
    assert client.get("/actions/kinds", headers=headers).status_code == 200


def test_the_route_needs_a_principal(world):
    client, _ = world
    assert client.get("/actions/kinds").status_code == 401


def test_the_route_lists_exactly_the_registered_kinds_in_order(world):
    assert [i["kind"] for i in _items(world)] == list(registry().action_kinds())


def test_a_kind_installed_by_an_extension_is_served(world, fake):
    assert [i["kind"] for i in _items(world)][-1] == FAKE.name


def test_a_kind_gone_after_uninstall_is_not_served(world, fake):
    registry().uninstall("acme")
    registry().install("acme", lambda ext: None)
    assert FAKE.name not in [i["kind"] for i in _items(world)]


def test_an_extension_kinds_item_carries_its_params_and_hints(world, fake):
    item = _items(world)[-1]
    assert [p["name"] for p in item["params"]] == ["actor", "text", "mode"]
    assert (item["label"], item["any_of"], item["aliases"]) == (
        "Ping",
        [["text", "mode"]],
        ["acme.legacy"],
    )


def test_a_param_carries_its_widget_label_and_choices(world, fake):
    text, mode = _items(world)[-1]["params"][1:]
    assert (text["widget"], text["label"], text["required"]) == ("multiline", "Words", True)
    assert (mode["choices"], mode["choose"]) == (
        [{"value": "fast", "label": "Fast"}, {"value": "slow", "label": "Slow"}],
        "Pick",
    )


def test_a_kind_without_actor_clauses_means_an_app_declaring_it(world):
    noop, message = _items(world)[:2]
    assert (noop["actors"], message["aliases"]) == ([{"declares_action": True}], ["mesh.message"])


def test_the_label_falls_back_to_the_summary(world):
    registry().install("plain", lambda ext: ext.action_kind(ActionKind("plain.do", {}, "Do it")))
    try:
        assert _items(world)[-1]["label"] == "Do it"
    finally:
        registry().uninstall("plain")


def test_every_builtin_kind_is_served_with_a_label(world):
    assert all(i["label"] for i in _items(world))


def test_the_web_tests_copy_of_the_catalogue_is_current():
    script = Path(__file__).resolve().parents[2] / "scripts" / "export-action-kinds.py"
    done = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_only_the_params_the_old_editor_hid_are_advanced(world):
    advanced = {(i["kind"], p["name"]) for i in _items(world) for p in i["params"] if p["advanced"]}
    assert advanced == {
        ("github.comment", "once_key"),
        ("email.send", "idempotency_key"),
        ("email.send", "sender"),
        ("email.send", "not_after"),
    }


# ------------------------------------------------------------------ d11: generic editor hints

APP_DECLARABLE = {
    "discord.message",
    "discord.read",
    "discord.react",
    "discord.reply",
    "discord.sweep",
    "github.comment",
    "github.merge",
    "github.pr",
    "jira.comment",
    "http.call",
    "email.send",
    "search.web",
}


def test_the_catalogue_says_which_kinds_an_app_actor_may_declare(world):
    declarable = {i["kind"] for i in _items(world) if i["declarable_by_apps"]}
    assert declarable == APP_DECLARABLE


def test_a_kind_is_not_declarable_unless_its_extension_says_so(world, fake):
    assert _items(world)[-1]["declarable_by_apps"] is False


def test_an_extension_kind_may_be_declarable(world):
    spec = ActionKind("acme.notify", {}, "Notify", declarable_by_apps=True)
    registry().install("notify", lambda ext: ext.action_kind(spec))
    try:
        assert _items(world)[-1]["declarable_by_apps"] is True
    finally:
        registry().uninstall("notify")


def test_a_legacy_message_naming_an_actor_is_shown_as_a_discord_message(world):
    shown = {i["kind"]: i["shown_as"] for i in _items(world) if i["shown_as"]}
    assert shown == {"message": {"when_param": "actor", "kind": "discord.message"}}


def test_the_command_args_widget_names_its_command_param(world):
    by_kind = {i["kind"]: i for i in _items(world)}
    args_for = {
        (k, p["name"]): p["args_for"]
        for k, i in by_kind.items()
        for p in i["params"]
        if p["args_for"]
    }
    assert args_for == {("machine.command", "args"): "command"}
