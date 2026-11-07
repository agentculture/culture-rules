"""The extension registry (Boundary t7): registration rules, on fresh registries.

Every test here builds its own :class:`Registry`, so nothing leaks into the default one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from culture_rules.extensions import ApiContext, BuiltinPortSpec, ExtensionError, Lazy, Registry
from culture_rules.model.action_kinds import ActionKind, ParamSpec, kind

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
    reg = Registry()
    with pytest.raises(ExtensionError):
        reg.install("bad", register)


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


def mount_routes(app, ctx):
    app.append((ctx.store, ctx.options.get("transport")))


def mount_other(app, ctx):
    app.append(ctx)


def test_api_routes_mount_in_registration_order_with_their_context():
    reg = Registry()
    reg.install("acme", lambda ext: ext.api_routes("acme.targets", mount_routes))
    reg.install("beta", lambda ext: ext.api_routes("beta.targets", mount_other))
    assert [(e.name, e.extension) for e in reg.api_routes()] == [
        ("acme.targets", "acme"),
        ("beta.targets", "beta"),
    ]
    mounted: list = []
    ctx = ApiContext("store", options={"transport": "fake"})
    for entry in reg.api_routes():
        entry.mount(mounted, ctx)
    assert mounted == [("store", "fake"), ctx]


def test_api_routes_follow_the_registration_rules():
    reg = Registry()
    reg.install("acme", lambda ext: ext.api_routes("acme.targets", mount_routes))
    with pytest.raises(ExtensionError, match="already registered by extension 'acme'"):
        reg.install("beta", lambda ext: ext.api_routes("acme.targets", mount_other))
    with pytest.raises(ExtensionError, match="non-empty string"):
        reg.install("gamma", lambda ext: ext.api_routes("", mount_other))
    reg.uninstall("acme")
    assert reg.api_routes() == ()


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


def test_a_catalogue_view_stays_live_across_uninstall_and_install():
    """action_kinds() is a live view: it drops an uninstalled extension's kind and sees a
    kind installed after the view was taken (the dict is refilled, never replaced)."""
    reg = Registry()
    view = reg.action_kinds()
    reg.install("one", lambda ext: ext.action_kind(kind("one.ping", "Ping one")))
    reg.uninstall("one")
    reg.install("two", lambda ext: ext.action_kind(kind("two.ping", "Ping two")))
    assert list(view) == ["two.ping"]


# --------------------------------------------------------------------------- d9 seams


def check_status(params, path):
    return [(f"{path}.status", "acme_status", "no status")] if params.get("status") else []


def admits_all(params, data, own):
    del params, data, own
    return True


def admits_none(params, data, own):
    del params, data, own
    return False


def no_problems(params, path):
    del params, path
    return []


def filter_words(params):
    return [f"({params.get('room')})"] if params.get("room") else []


def guard_words(params):
    del params
    return ["(guarded)"]


def health_rooms(store, now):
    return [store, now]


def reserve_acme(envelope):
    return "acme is reserved" if envelope.get("type") == "acme.secret" else None


ROLES = (("GET", "/actors/{actor_id}/acme/targets", "viewer"),)


def acme_d9(ext):
    ext.params_validator("acme.ping", check_status)
    ext.trigger_filter("acme", match=admits_all, validate=no_problems, describe=filter_words)
    ext.action_phrases("acme.ping", words=(("room", "in {}"),), after=guard_words)
    ext.builtin("acme.tally", router_factory, phrase="acme.tally: count the things")
    ext.health_section("rooms", health_rooms, empty=list)
    ext.event_reservation("acme.secret", reserve_acme)
    ext.api_routes("acme.targets", mount_routes, roles=ROLES)


def test_the_d9_seams_register_and_read_back():
    reg = Registry()
    reg.install("acme", acme_d9)
    assert reg.params_validator("acme.ping") is check_status
    assert reg.trigger_filter("acme.room.joined").describe is filter_words
    assert reg.action_phrases("acme.ping").words == (("room", "in {}"),)
    assert reg.builtin_phrases() == {"acme.tally": "acme.tally: count the things"}
    assert [(s.name, s.empty()) for s in reg.health_sections()] == [("rooms", [])]
    assert [r.check for r in reg.event_reservations()] == [reserve_acme]
    assert reg.route_role("GET", "/actors/a1/acme/targets") == "viewer"


def test_a_params_validator_follows_the_kinds_aliases():
    reg = Registry()
    reg.install("acme", acme)
    reg.install("checks", lambda ext: ext.params_validator("acme.ping", check_status))
    assert reg.params_validator("acme.legacy_ping") is check_status


@pytest.mark.parametrize(
    "event_type",
    ["acme", "acmex.room.joined", "other.acme.joined", "", "Acme.room.joined"],
)
def test_a_trigger_filter_covers_only_its_namespace(event_type):
    reg = Registry()
    reg.install("acme", acme_d9)
    assert reg.trigger_filter(event_type) is None


def test_a_trigger_filter_covers_every_type_in_its_namespace():
    reg = Registry()
    reg.install("acme", acme_d9)
    assert reg.trigger_filter("acme.x").match is admits_all


def test_trigger_filters_are_listed_in_registration_order():
    reg = Registry()
    reg.install("acme", acme_d9)
    reg.install("beta", lambda ext: ext.trigger_filter("beta", match=admits_none))
    assert [f.namespace for f in reg.trigger_filters()] == ["acme", "beta"]


def test_a_route_role_matches_only_its_method_and_path():
    reg = Registry()
    reg.install("acme", acme_d9)
    assert reg.route_role("POST", "/actors/a1/acme/targets") is None


def test_a_route_role_matches_one_segment_per_placeholder():
    reg = Registry()
    reg.install("acme", acme_d9)
    assert reg.route_role("GET", "/actors/a/b/acme/targets") is None


def test_health_sections_and_reservations_keep_registration_order():
    reg = Registry()
    reg.install("acme", acme_d9)
    reg.install(
        "beta",
        lambda ext: (
            ext.health_section("beta", health_rooms),
            ext.event_reservation("beta.secret", reserve_acme),
        ),
    )
    assert [s.name for s in reg.health_sections()] == ["rooms", "beta"]
    assert [r.name for r in reg.event_reservations()] == ["acme.secret", "beta.secret"]


def test_a_health_sections_empty_value_defaults_to_none():
    reg = Registry()
    reg.install("beta", lambda ext: ext.health_section("beta", health_rooms))
    assert reg.health_sections()[0].empty() is None


def test_installing_the_d9_seams_twice_is_a_no_op():
    reg = Registry()
    reg.install("acme", lambda ext: (acme_d9(ext), acme_d9(ext)))
    assert len(reg.health_sections()) == 1


@pytest.mark.parametrize(
    "register",
    [
        lambda ext: ext.params_validator("acme.ping", no_problems),
        lambda ext: ext.trigger_filter("acme", match=admits_none),
        lambda ext: ext.action_phrases("acme.ping", words=()),
        lambda ext: ext.builtin("acme.tally", router_factory, phrase="another"),
        lambda ext: ext.health_section("rooms", health_rooms, empty=dict),
        lambda ext: ext.event_reservation("acme.secret", lambda envelope: None),
    ],
    ids=["validator", "filter", "phrases", "builtin-phrase", "health", "reservation"],
)
def test_a_conflicting_d9_entry_is_refused(register):
    reg = Registry()
    reg.install("acme", acme_d9)
    with pytest.raises(ExtensionError, match="already registered by extension 'acme'"):
        reg.install("rival", register)


@pytest.mark.parametrize(
    "register",
    [
        lambda ext: ext.params_validator("", check_status),
        lambda ext: ext.params_validator("acme.ping", "not callable"),
        lambda ext: ext.trigger_filter("acme.room", match=admits_all),
        lambda ext: ext.trigger_filter("Acme", match=admits_all),
        lambda ext: ext.trigger_filter("acme", match=None),
        lambda ext: ext.action_phrases("acme.ping", words=(("room",),)),
        lambda ext: ext.action_phrases("acme.ping", words=(("room", 3),)),
        lambda ext: ext.health_section("status", health_rooms),
        lambda ext: ext.health_section("rooms", "not callable"),
        lambda ext: ext.event_reservation("", reserve_acme),
        lambda ext: ext.api_routes("a", mount_routes, roles=(("GET", "/x", "owner"),)),
        lambda ext: ext.api_routes("a", mount_routes, roles=(("FETCH", "/x", "viewer"),)),
        lambda ext: ext.api_routes("a", mount_routes, roles=(("GET", "x", "viewer"),)),
    ],
    ids=[
        "validator-empty-kind",
        "validator-not-callable",
        "filter-dotted-namespace",
        "filter-uppercase-namespace",
        "filter-no-match",
        "phrases-no-template",
        "phrases-template-not-text",
        "health-core-section",
        "health-not-callable",
        "reservation-empty-name",
        "role-unknown",
        "role-bad-method",
        "role-relative-path",
    ],
)
def test_a_malformed_d9_registration_is_refused(register):
    reg = Registry()
    with pytest.raises(ExtensionError):
        reg.install("bad", register)


def test_uninstall_removes_the_d9_entries():
    reg = Registry()
    reg.install("acme", acme_d9)
    reg.uninstall("acme")
    assert reg.params_validator("acme.ping") is None
    assert reg.trigger_filter("acme.room.joined") is None
    assert reg.action_phrases("acme.ping") is None
    assert reg.builtin_phrases() == {}
    assert reg.health_sections() == ()
    assert reg.event_reservations() == ()
    assert reg.route_role("GET", "/actors/a1/acme/targets") is None


def test_a_refused_install_rolls_the_d9_entries_back():
    reg = Registry()
    reg.install("acme", acme_d9)

    def half(ext):
        ext.health_section("beta", health_rooms)
        ext.params_validator("acme.ping", no_problems)  # conflicts: the install rolls back

    with pytest.raises(ExtensionError):
        reg.install("rival", half)
    assert [s.name for s in reg.health_sections()] == ["rooms"]


# ------------------------------------------------------------------ builtin catalogue data


def acme_ports(ext):
    ext.builtin(
        "acme.count",
        router_factory,
        phrase="acme.count: count the things",
        inputs=(("things", "array"), BuiltinPortSpec("limit", "integer", False)),
        outputs=[("count", "integer")],
    )


def test_a_builtins_ports_read_back_as_specs():
    reg = Registry()
    reg.install("acme", acme_ports)
    (entry,) = reg.builtins()
    assert entry.inputs == (
        BuiltinPortSpec("things", "array", True),
        BuiltinPortSpec("limit", "integer", False),
    )


def test_a_port_given_by_name_alone_is_a_required_string():
    reg = Registry()
    reg.install("acme", lambda ext: ext.builtin("acme.x", router_factory, outputs=["text"]))
    assert reg.builtins()[0].outputs == (BuiltinPortSpec("text", "string", True),)


def test_a_builtins_label_falls_back_to_its_phrase():
    reg = Registry()
    reg.install("acme", acme_ports)
    assert reg.builtins()[0].shown_label == "acme.count: count the things"


def test_a_builtin_without_phrase_or_label_is_shown_by_name():
    reg = Registry()
    reg.install("acme", lambda ext: ext.builtin("acme.x", router_factory))
    assert reg.builtins()[0].shown_label == "acme.x"


def test_a_registered_label_wins_over_the_phrase():
    reg = Registry()
    reg.install(
        "acme", lambda ext: ext.builtin("acme.x", router_factory, phrase="long", label="short")
    )
    assert reg.builtins()[0].shown_label == "short"


@pytest.mark.parametrize(
    "inputs",
    [
        "things",
        [("things", "list")],
        [("things", "array", "yes")],
        [("a",), ("a",)],
        [("",)],
        [("a", "string", True, "extra")],
    ],
    ids=["a-string", "unknown-type", "required-not-bool", "duplicate", "empty-name", "four"],
)
def test_a_malformed_builtin_port_is_refused(inputs):
    reg = Registry()
    with pytest.raises(ExtensionError):
        reg.install("bad", lambda ext: ext.builtin("bad.x", router_factory, inputs=inputs))


def test_a_non_string_builtin_label_is_refused():
    reg = Registry()
    with pytest.raises(ExtensionError):
        reg.install("bad", lambda ext: ext.builtin("bad.x", router_factory, label=3))


@pytest.mark.parametrize("template", ["/actors/{é}/acme", "/actors/ünï/acme"])
def test_a_route_template_is_ascii_only(template):
    reg = Registry()

    def register(ext):
        ext.api_routes("acme.routes", lambda app, ctx: None, roles=(("GET", template, "viewer"),))

    with pytest.raises(ExtensionError):
        reg.install("acme", register)
