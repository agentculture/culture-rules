"""The extension registry (Boundary t7): registration rules, on fresh registries.

Every test here builds its own :class:`Registry`, so nothing leaks into the default one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from culture_rules.extensions import ExtensionError, Lazy, Registry
from culture_rules.model.action_kinds import ActionKind, ParamSpec

ROOT = Path(__file__).resolve().parent.parent

KIND = ActionKind("acme.ping", {"actor": ParamSpec("str", True)}, "Ping")


def router_factory(store):
    return store


def other_factory(store):
    return (store,)


def listener_factory(ctx):
    return ctx


def acme(ext):
    ext.action_kind(KIND, aliases=("acme.legacy_ping",))
    ext.action_port("acme.ping", router_factory)
    ext.builtin("acme.count", router_factory)
    ext.event_type("acme.thing.happened", summary="a thing")
    ext.webhook_route("/hooks/acme", router_factory)
    ext.node_listener("acme.poller", listener_factory)


def acme_again(ext):
    acme(ext)


def test_install_registers_every_kind_of_entry():
    reg = Registry()
    reg.install("acme", acme)
    assert reg.resolve_kind("acme.ping") is KIND
    assert reg.resolve_kind("acme.legacy_ping") is KIND
    assert [p.kind for p in reg.action_ports()] == ["acme.ping"]
    assert [b.name for b in reg.builtins()] == ["acme.count"]
    assert "acme.thing.happened" in reg.event_types()
    assert [r.path for r in reg.webhook_routes()] == ["/hooks/acme"]
    assert [n.name for n in reg.node_listeners()] == ["acme.poller"]


def test_installing_the_same_extension_twice_is_a_no_op():
    reg = Registry()
    assert reg.install("acme", acme) is True
    assert reg.install("acme", acme) is False
    assert reg.installed() == ("acme",)


def test_installing_another_register_function_under_a_taken_name_is_refused():
    reg = Registry()
    reg.install("acme", acme)
    with pytest.raises(ExtensionError, match="acme"):
        reg.install("acme", acme_again)


def test_an_equal_registration_is_a_no_op():
    reg = Registry()

    def twice(ext):
        ext.builtin("acme.count", router_factory)
        ext.builtin("acme.count", router_factory)

    reg.install("acme", twice)
    assert [b.name for b in reg.builtins()] == ["acme.count"]


def test_a_conflict_names_the_extension_holding_the_name():
    reg = Registry()
    reg.install("acme", acme)
    with pytest.raises(ExtensionError, match="already registered by extension 'acme'"):
        reg.install("other", lambda ext: ext.builtin("acme.count", other_factory))


@pytest.mark.parametrize(
    "register",
    [
        lambda ext: ext.action_kind(ActionKind("acme.ping", {}, "another")),
        lambda ext: ext.action_kind(ActionKind("acme.legacy_ping", {}, "an alias's name")),
        lambda ext: ext.action_kind(ActionKind("x.y", {}, ""), aliases=("acme.ping",)),
        lambda ext: ext.action_port("acme.ping", other_factory),
        lambda ext: ext.builtin("acme.count", other_factory),
        lambda ext: ext.event_type("acme.thing.happened", summary="another"),
        lambda ext: ext.webhook_route("/hooks/acme", other_factory),
        lambda ext: ext.node_listener("acme.poller", other_factory),
    ],
    ids=[
        "kind",
        "kind-named-like-alias",
        "alias-named-like-kind",
        "port",
        "builtin",
        "event",
        "route",
        "listener",
    ],
)
def test_a_conflicting_duplicate_is_refused(register):
    reg = Registry()
    reg.install("acme", acme)
    with pytest.raises(ExtensionError):
        reg.install("rival", register)


def test_a_refused_install_leaves_nothing_behind():
    reg = Registry()
    reg.install("acme", acme)

    def half(ext):
        ext.builtin("rival.one", router_factory)
        ext.builtin("acme.count", other_factory)  # conflicts: the whole install rolls back

    with pytest.raises(ExtensionError):
        reg.install("rival", half)
    assert [b.name for b in reg.builtins()] == ["acme.count"]
    assert reg.installed() == ("acme",)


def test_uninstall_removes_everything_the_extension_registered():
    reg = Registry()
    reg.install("acme", acme)
    reg.uninstall("acme")
    assert reg.resolve_kind("acme.legacy_ping") is None
    assert (reg.action_ports(), reg.builtins(), reg.webhook_routes()) == ((), (), ())
    assert (dict(reg.event_types()), reg.node_listeners(), reg.installed()) == ({}, (), ())


@pytest.mark.parametrize(
    "register",
    [
        lambda ext: ext.builtin("action", router_factory),
        lambda ext: ext.builtin("", router_factory),
        lambda ext: ext.action_kind(ActionKind("", {}, "")),
        lambda ext: ext.event_type("Not.Dotted Type"),
        lambda ext: ext.event_type("single"),
        lambda ext: ext.webhook_route("/acme", router_factory),
        lambda ext: ext.webhook_route("/hooks/", router_factory),
        lambda ext: ext.webhook_route("/hooks/{id}", router_factory),
        lambda ext: ext.node_listener("", listener_factory),
    ],
    ids=[
        "reserved-builtin",
        "empty-builtin",
        "empty-kind",
        "bad-event",
        "one-segment-event",
        "route-outside-hooks",
        "bare-hooks",
        "templated-route",
        "empty-listener",
    ],
)
def test_a_malformed_registration_is_refused(register):
    with pytest.raises(ExtensionError):
        Registry().install("bad", register)


def test_lenient_is_per_kind_and_follows_aliases():
    reg = Registry()
    reg.install(
        "acme", lambda ext: ext.action_kind(KIND, aliases=("acme.legacy_ping",), lenient=True)
    )
    assert reg.is_lenient("acme.legacy_ping") is True
    assert reg.is_lenient("other") is False


def test_node_collections_come_from_providers_in_order():
    reg = Registry()
    reg.install("acme", lambda ext: ext.node_collections(lambda: ("acme_a", "acme_b")))
    assert reg.node_collections() == ("acme_a", "acme_b")


def test_lazy_imports_its_module_only_when_called():
    factory = Lazy("collections", "OrderedDict")
    assert factory(None) == {}
    assert Lazy("collections", "OrderedDict") == factory  # equal by value: re-registration


def test_lazy_with_store_passes_the_store():
    assert Lazy("builtins", "list", store=True)("ab") == ["a", "b"]


def test_the_event_type_rule_matches_app_actors():
    from culture_rules.extensions.registry import EVENT_TYPE_RE
    from culture_rules.model.app_actor import EVENT_TYPE_RE as APP_RE

    assert EVENT_TYPE_RE.pattern == APP_RE.pattern


IMPORT_CHECK = """
import sys
import culture_rules.extensions as ext
ext.registry()
third = sorted(
    name for name in sys.modules
    if "." not in name and not name.startswith("_")
    and name not in sys.stdlib_module_names and name != "culture_rules"
)
assert not third, third
"""


def test_the_registry_and_its_built_ins_load_with_no_third_party_module():
    proc = subprocess.run(
        [sys.executable, "-c", IMPORT_CHECK],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
