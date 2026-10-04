"""Criterion 4: schemas/ holds one JSON Schema per model, generated from the dataclasses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from culture_rules.model.schema import MODELS, generate_all, json_schema, main, schema_filename
from culture_rules.model.workflow import Workflow

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"
EXPECTED = {"rule", "workflow", "step", "action", "actor", "machine", "placement"}


def test_one_schema_per_model() -> None:
    assert {schema_filename(cls).split(".")[0] for cls in MODELS} == EXPECTED
    committed = {p.name for p in SCHEMAS_DIR.glob("*.schema.json")}
    assert committed == {f"{name}.schema.json" for name in EXPECTED}


@pytest.mark.parametrize("cls", MODELS, ids=lambda c: c.__name__)
def test_committed_schema_matches_generated(cls) -> None:
    committed = (SCHEMAS_DIR / schema_filename(cls)).read_text(encoding="utf-8")
    assert (
        committed == generate_all()[schema_filename(cls)]
    ), "schemas/ is stale: run `uv run python -m culture_rules.model.schema --write schemas`"


def test_check_mode(tmp_path, capsys) -> None:
    assert main(["--check", str(SCHEMAS_DIR)]) == 0
    assert main(["--check"]) == 0  # DIR defaults to the repo's schemas/
    # The directory is fixed to schemas/; tests point the generator elsewhere in code only.
    assert main(["--write"], schemas_dir=tmp_path) == 0
    assert main(["--check"], schemas_dir=tmp_path) == 0
    (tmp_path / "rule.schema.json").write_text("{}", encoding="utf-8")
    assert main(["--check"], schemas_dir=tmp_path) == 1
    assert "rule.schema.json" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["--write", "--check"])
@pytest.mark.parametrize(
    "bad",
    [
        str(SCHEMAS_DIR / ".." / ".."),
        str(SCHEMAS_DIR / ".." / "culture_rules"),
        "../../etc",
        "/tmp",
    ],
)
def test_dir_outside_schemas_is_refused(mode, bad, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main([mode, bad])
    assert exc.value.code == 2
    assert "must be the repository's schemas/ directory" in capsys.readouterr().err


def test_write_refusal_touches_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    with pytest.raises(SystemExit):
        main(["--write", "out/../out"])
    assert list((tmp_path / "out").iterdir()) == []


def _all_property_names(schema: dict) -> set[str]:
    names: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, val in node.get("properties", {}).items():
                names.add(key)
                walk(val)
            for key, val in node.items():
                if key != "properties":
                    walk(val)
        elif isinstance(node, list):
            for val in node:
                walk(val)

    walk(schema)
    return names


def test_workflow_schema_has_no_trigger_field() -> None:
    names = _all_property_names(json_schema(Workflow))
    assert not any("trigger" in n for n in names)


def test_rule_stage_schemas_have_no_actor_slot() -> None:
    # h7, scoped by deviation d1: no *stage* of the rule chain (trigger, condition, workflow,
    # action) has an actor slot. The rule's placement may name an actor — that says where the
    # rule evaluates, it is not a stage.
    gen = generate_all()
    rule = json.loads(gen["rule.schema.json"])
    rule["properties"].pop("placement")
    rule.get("$defs", {}).pop("Placement", None)
    for name, schema in (("rule", rule), ("action", json.loads(gen["action.schema.json"]))):
        names = _all_property_names(schema)
        assert not any("actor" in n for n in names), name


def test_rule_schema_requires_action_and_trigger() -> None:
    schema = json.loads(generate_all()["rule.schema.json"])
    assert {"action", "trigger", "id", "name"} <= set(schema["required"])
    assert "condition" not in schema["required"]
    assert "workflow" not in schema["required"]
    # the action slot is not nullable
    assert schema["properties"]["action"]["$ref"] == "#/$defs/Action"
    assert "anyOf" not in schema["properties"]["action"]


def test_actor_schema_kind_enum() -> None:
    schema = json.loads(generate_all()["actor.schema.json"])
    assert set(schema["properties"]["kind"]["enum"]) == {
        "agent",
        "human",
        "service",
        "daemon",
        "runner",
        "robot",
        "app",
    }


def test_placement_schema_is_exactly_one_form() -> None:
    schema = json.loads(generate_all()["placement.schema.json"])
    assert len(schema["oneOf"]) == 3


def test_step_schema_requires_max_for_loops() -> None:
    schema = json.loads(generate_all()["step.schema.json"])
    rule = schema["allOf"][0]
    assert set(rule["if"]["properties"]["kind"]["enum"]) == {"for_each", "retry_until"}
    assert "max_iterations" in rule["then"]["required"]


def test_schemas_are_closed_and_draft_2020_12() -> None:
    for text in generate_all().values():
        schema = json.loads(text)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False
        for definition in schema.get("$defs", {}).values():
            assert definition["additionalProperties"] is False


def test_schema_accepts_fixture_documents_when_jsonschema_available() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    from tests.model.factories import (
        make_action,
        make_actor,
        make_machine,
        make_rule,
        make_step,
        make_workflow,
    )

    gen = generate_all()
    for name, obj in [
        ("rule", make_rule()),
        ("workflow", make_workflow()),
        ("step", make_step()),
        ("action", make_action()),
        ("actor", make_actor()),
        ("machine", make_machine()),
    ]:
        jsonschema.validate(obj.to_dict(), json.loads(gen[f"{name}.schema.json"]))
