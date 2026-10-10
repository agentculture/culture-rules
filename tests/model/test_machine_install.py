"""#45 t1: a Machine record carries an optional, typed install section.

The section mirrors ``deploy/node/install.sh``'s flags so ``rollout upgrade`` can re-run an
install from the record: secrets are grant secret NAMES (never values), and the gate's run-as
prefix must be a ``sudo -n -u <user>`` prefix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from culture_rules.machines.enrol import MACHINES_COLLECTION, enrol, enrolled_machines
from culture_rules.model.machine import INSTALL_MODES, Machine, MachineInstall
from culture_rules.model.serde import ModelParseError
from culture_rules.model.validate import validate, validate_data
from culture_rules.store.memory import MemoryStore

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"

FULL_INSTALL = {
    "mode": "install_sh",
    "node_name": "thor",
    "secrets": ["RULES_GITHUB_APP_KEY", "DISCORD_TOKEN_2"],
    "gate_run_as": "sudo -n -u culture-fixer -- /usr/bin/env PATH=/usr/bin:/bin",
    "extras": ["store", "events", "github"],
    "wheelhouse": "/opt/wheelhouse",
    "python": "3.12",
    "events_host": "spark.local",
    "events_port": 1883,
}


def _codes(obj) -> set[tuple[str, str]]:
    return {(e.path, e.code) for e in validate(obj)}


# --- criterion 1: the typed section, strict parse, schema -------------------------------


def test_install_section_parses_into_a_typed_dataclass() -> None:
    machine = Machine.from_dict({"name": "thor", "install": FULL_INSTALL})
    assert isinstance(machine.install, MachineInstall)
    assert machine.install.mode == "install_sh"
    assert machine.install.node_name == "thor"
    assert machine.install.secrets == ("RULES_GITHUB_APP_KEY", "DISCORD_TOKEN_2")
    assert machine.install.extras == ("store", "events", "github")
    assert machine.install.events_port == 1883
    assert validate(machine) == []
    assert Machine.from_dict(machine.to_dict()) == machine


def test_shared_venv_mode_needs_nothing_else() -> None:
    machine = Machine.from_dict({"name": "spark", "install": {"mode": "shared_venv"}})
    assert machine.install == MachineInstall(mode="shared_venv")
    assert validate(machine) == []
    assert set(INSTALL_MODES) == {"install_sh", "shared_venv"}


def test_unknown_install_key_is_refused_by_strict_parse() -> None:
    with pytest.raises(ModelParseError) as exc:
        Machine.from_dict({"name": "thor", "install": {"mode": "install_sh", "wheel": "x"}})
    assert exc.value.path == "install.wheel"
    assert exc.value.code == "unknown_field"


def test_unknown_mode_is_refused() -> None:
    _, errors = validate_data(Machine, {"name": "thor", "install": {"mode": "docker"}})
    assert ("install.mode", "invalid_value") in {(e.path, e.code) for e in errors}


def test_mode_is_required_in_an_install_section() -> None:
    _, errors = validate_data(Machine, {"name": "thor", "install": {"node_name": "thor"}})
    assert ("install.mode", "required") in {(e.path, e.code) for e in errors}


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_events_port_out_of_range_is_refused(port) -> None:
    machine = Machine(name="thor", install=MachineInstall(mode="install_sh", events_port=port))
    assert ("install.events_port", "range") in _codes(machine)


def test_committed_machine_schema_describes_install() -> None:
    schema = json.loads((SCHEMAS_DIR / "machine.schema.json").read_text(encoding="utf-8"))
    assert "install" in schema["properties"]
    assert "install" not in schema["required"]
    section = schema["$defs"]["MachineInstall"]
    assert section["additionalProperties"] is False
    assert section["required"] == ["mode"]
    assert set(section["properties"]) == set(FULL_INSTALL)
    assert set(section["properties"]["mode"]["enum"]) == {"install_sh", "shared_venv"}


# --- criterion 2: secrets are grant names, gate_run_as is a sudo -n -u prefix -----------


@pytest.mark.parametrize(
    "bad",
    ["", "rules_key", "RULES-KEY", "RULES KEY", "ghp_abcdef0123456789", "KEY=value", "KEY\n"],
)
def test_secret_that_is_not_a_grant_name_is_refused(bad) -> None:
    machine = Machine(
        name="thor", install=MachineInstall(mode="install_sh", secrets=("GOOD_NAME", bad))
    )
    assert ("install.secrets[1]", "invalid_secret_name") in _codes(machine)


@pytest.mark.parametrize(
    "good",
    [
        "sudo -n -u culture-fixer",
        "sudo -n -u culture-fixer --",
        "sudo -n -u culture-fixer -- /usr/bin/env PATH=/home/culture-fixer/.local/bin:/usr/bin",
        "/usr/bin/sudo -n -u fixer_1 --",
    ],
)
def test_sudo_n_u_prefix_is_accepted(good) -> None:
    machine = Machine(name="thor", install=MachineInstall(mode="install_sh", gate_run_as=good))
    assert validate(machine) == []


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "culture-fixer",
        "sudo -u culture-fixer",
        "sudo -u culture-fixer -n",
        "sudo -n culture-fixer",
        "sudo -n -u",
        "sudo -n -u -- /bin/true",
        "sudo -n -u culture-fixer\nrm -rf /",
        "sudo -n -u 'culture fixer'",
        "sudo -n -u culture-fixer -- sh -c 'x'\\",
        "doas -n -u culture-fixer",
        "env sudo -n -u culture-fixer",
        "sudo -n -u 'unterminated",
    ],
)
def test_gate_run_as_that_is_not_a_sudo_n_u_prefix_is_refused(bad) -> None:
    machine = Machine(name="thor", install=MachineInstall(mode="install_sh", gate_run_as=bad))
    assert ("install.gate_run_as", "invalid_run_as") in _codes(machine)


# --- criterion 3: documents without install load and save unchanged ---------------------


LEGACY = {
    "name": "orin",
    "address": "orin.tailnet.internal",
    "platform": "linux-aarch64",
    "capabilities": ["gpu"],
    "roles": ["engine_node", "runner"],
    "description": "",
    "enabled": True,
    "schema_version": "1.0",
}


def test_machine_without_install_round_trips_byte_for_byte() -> None:
    machine = Machine.from_dict(LEGACY)
    assert machine.install is None
    assert validate(machine) == []
    assert machine.to_dict() == LEGACY
    assert "install" not in machine.to_json()


def test_stored_machine_without_install_loads_and_resaves_unchanged() -> None:
    store = MemoryStore()
    store.put(MACHINES_COLLECTION, {"id": "orin", **LEGACY})
    before = store.get(MACHINES_COLLECTION, "orin")
    (loaded,) = enrolled_machines(store)
    assert loaded.install is None
    result = enrol(store, loaded, apply=True)
    assert result.action == "unchanged"
    assert store.get(MACHINES_COLLECTION, "orin") == before


def test_install_survives_a_store_round_trip() -> None:
    store = MemoryStore()
    machine = Machine.from_dict({**LEGACY, "install": FULL_INSTALL})
    assert enrol(store, machine, apply=True).action == "create"
    (loaded,) = enrolled_machines(store)
    assert loaded == machine
    assert store.get(MACHINES_COLLECTION, "orin")["install"]["secrets"] == list(
        FULL_INSTALL["secrets"]
    )
