"""Placement resolver: machine / actor / requirement -> one eligible host, or a structured error."""

from __future__ import annotations

import pytest

from culture_rules.engine.placement import (
    PUBLIC_HOSTNAMES,
    MachineState,
    PlacementError,
    Resolved,
    is_dispatchable_address,
    resolve_placement,
    resolve_rule_placement,
    resolve_step_placement,
)
from culture_rules.model.actor import Actor
from culture_rules.model.machine import Machine
from culture_rules.model.placement import Placement
from culture_rules.model.workflow import Step


def m(name, caps=(), address=None, enabled=True):
    return Machine(name=name, address=address, capabilities=tuple(caps), enabled=enabled)


def a(id_, machine="spark", caps=(), enabled=True, kind="agent"):
    return Actor(
        id=id_, name=id_, kind=kind, capabilities=tuple(caps), machine=machine, enabled=enabled
    )


MACHINES = [m("spark", ["gpu"], "100.64.0.1"), m("nuc", [], "192.168.1.20"), m("edge", ["gpu"])]
STATES = [
    MachineState("spark", True, False),
    MachineState("nuc", True, False),
    MachineState("edge", True, False),
]
ACTORS = [a("culture-agent", "nuc"), a("gpu-agent", "spark", ["gpu"]), a("loose", None)]


def go(placement, machines=MACHINES, states=STATES, actors=ACTORS):
    return resolve_placement(placement, machines, states, actors)


def test_machine_form_resolves():
    r = go(Placement(machine="nuc"))
    assert isinstance(r, Resolved)
    assert (r.machine, r.address, r.actor) == ("nuc", "192.168.1.20", None)


def test_address_defaults_to_machine_name():
    r = go(Placement(machine="edge"))
    assert isinstance(r, Resolved)
    assert r.address == "edge"


def test_actor_form_resolves_to_actor_machine():
    r = go(Placement(actor="culture-agent"))
    assert isinstance(r, Resolved)
    assert (r.machine, r.actor) == ("nuc", "culture-agent")


@pytest.mark.parametrize(
    "placement,code",
    [
        (Placement(machine="ghost"), "placement.machine_unknown"),
        (Placement(actor="ghost"), "placement.actor_unknown"),
        (Placement(actor="loose"), "placement.actor_unplaced"),
        (Placement(), "placement.invalid_form"),
        (Placement(machine="nuc", actor="loose"), "placement.invalid_form"),
    ],
)
def test_structured_errors(placement, code):
    r = go(placement)
    assert isinstance(r, PlacementError)
    assert r.code == code
    assert r.to_dict()["code"] == code
    assert r.message


def test_offline_drained_disabled_unknown_state_refused():
    for states, code in [
        ([MachineState("nuc", False, False)], "placement.machine_offline"),
        ([MachineState("nuc", True, True)], "placement.machine_drained"),
        ([], "placement.machine_offline"),
    ]:
        r = go(Placement(machine="nuc"), states=states)
        assert isinstance(r, PlacementError)
        assert r.code == code
    r = go(Placement(machine="nuc"), machines=[m("nuc", enabled=False)])
    assert isinstance(r, PlacementError)
    assert r.code == "placement.machine_disabled"


def test_disabled_actor_refused():
    r = go(Placement(actor="x"), actors=[a("x", "nuc", enabled=False)])
    assert isinstance(r, PlacementError)
    assert r.code == "placement.actor_disabled"


def test_requirement_resolves_only_to_advertising_host():
    r = go(Placement(requirement=("gpu",)))
    assert isinstance(r, Resolved)
    assert r.machine in {"spark", "edge"}


def test_requirement_is_deterministic_and_skips_unhealthy():
    first = go(Placement(requirement=("gpu",)))
    assert first == go(Placement(requirement=("gpu",)))
    states = [
        MachineState("spark", True, True),
        MachineState("edge", True, False),
        MachineState("nuc", True, False),
    ]
    r = go(Placement(requirement=("gpu",)), states=states)
    assert isinstance(r, Resolved)
    assert r.machine == "edge"


def test_requirement_via_actor_capability():
    r = go(Placement(requirement=("vision",)), actors=[a("v", "nuc", ["vision"])])
    assert isinstance(r, Resolved)
    assert r.machine == "nuc"
    r = go(Placement(requirement=("vision",)), actors=[a("v", "nuc", ["vision"], enabled=False)])
    assert isinstance(r, PlacementError)


def test_requirement_none_enrolled_is_validation_error():
    r = go(Placement(requirement=("tpu",)))
    assert isinstance(r, PlacementError)
    assert r.code == "placement.requirement_unmet"
    assert "tpu" in r.message
    v = r.to_validation_error()
    assert v.code == "placement.requirement_unmet"
    assert v.path


def test_requirement_needs_all_capabilities():
    r = go(Placement(requirement=("gpu", "tpu")))
    assert isinstance(r, PlacementError)


@pytest.mark.parametrize(
    "addr",
    [
        "100.64.0.1",
        "100.100.5.5",
        "192.168.1.20",
        "10.0.0.3",
        "172.16.4.4",
        "127.0.0.1",
        "spark",
        "spark.tail1234.ts.net",
        "nuc.local",
        "fd7a:115c:a1e0::1",
    ],
)
def test_lan_and_tailnet_addresses_dispatchable(addr):
    assert is_dispatchable_address(addr)


@pytest.mark.parametrize(
    "addr",
    [
        "rules.culture.dev",
        "RULES.CULTURE.DEV.",
        "8.8.8.8",
        "example.com",
        "https://rules.culture.dev",
        "",
        "1.1.1.1:443",
    ],
)
def test_public_addresses_refused(addr):
    assert not is_dispatchable_address(addr)


def test_public_hostname_constant():
    assert "rules.culture.dev" in PUBLIC_HOSTNAMES


def test_machine_with_public_address_refused_as_dispatch():
    ms = [m("pub", ["gpu"], "rules.culture.dev")]
    st = [MachineState("pub", True, False)]
    r = go(Placement(machine="pub"), ms, st, [])
    assert isinstance(r, PlacementError)
    assert r.code == "placement.address_refused"
    r = go(Placement(requirement=("gpu",)), ms, st, [])
    assert isinstance(r, PlacementError)
    r = go(
        Placement(requirement=("gpu",)),
        ms + [m("lan", ["gpu"], "10.0.0.9")],
        st + [MachineState("lan", True, False)],
        [],
    )
    assert isinstance(r, Resolved)
    assert r.machine == "lan"


def test_step_placement_resolves_like_placement():
    p = Placement(machine="nuc")
    step = Step(id="s", kind="code", placement=p)
    assert resolve_step_placement(step, MACHINES, STATES, ACTORS) == go(p)


def test_rule_placement_helper_with_duck_object():
    class R:
        placement = Placement(requirement=("gpu",))

    assert resolve_rule_placement(R(), MACHINES, STATES, ACTORS) == go(R.placement)


def test_null_placement_means_any_eligible_host():
    r = go(None)
    assert isinstance(r, Resolved)
    assert isinstance(go(None, states=[]), PlacementError)
