"""File-in transcription t6: the shipped pocket-transcribe data (docs/rules/pocket-transcribe).

Two STT agent actors (``stt-en``: whisper for every language, ``stt-he``: zipformer for
Hebrew), the generic ``transcribe-file`` workflow and its Hebrew twin ``transcribe-file-he``,
and two event rules (the Hebrew one skips recordings marked ``en``), shipped disabled, plus
``variables.json`` and its seed script. Nothing personal or secret is
committed: the key is a grant reference, the base URL is a loopback placeholder and the
actor's two roots are ``/home/OPERATOR`` placeholders the operator replaces. The workflow
names nothing from Pocket; only the rule's trigger and input mapping do. The flow itself is
driven in tests/node/test_transcribe_file_flow.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from culture_rules.cli import main
from culture_rules.io.exchange import import_bundle, read_bundle
from culture_rules.model.action_step import action_spec
from culture_rules.model.variable import validate_variable_value
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.node.actions.stt import check_stt_actor
from culture_rules.store.memory import MemoryStore
from tests.cli.test_nouns_api import store, wire  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "rules" / "pocket-transcribe"
WORKFLOW_FILE = BUNDLE / "workflows" / "transcribe-file.json"
VARIABLES = BUNDLE / "variables.json"
DESCRIPTIONS = ROOT / "tests" / "fixtures" / "workflow_descriptions.json"
SEED = BUNDLE / "seed-variables.sh"
RULE = "pocket-transcribe"
WORKFLOW = "transcribe-file"
STT = "stt-en"
STT_HE = "stt-he"
RULE_HE = "pocket-transcribe-he"
WORKFLOW_HE = "transcribe-file-he"
WORKFLOW_HE_FILE = BUNDLE / "workflows" / "transcribe-file-he.json"
MODEL_HE = "ivrit-ai/zipformer2-66m-streaming-he"
STEP = "transcribe"
EVENT = "pocketsync.recording.landed"
MODEL = "ivrit-ai/whisper-large-v3-turbo"
DIR_VAR = "transcripts_dir"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
APPLY = "--apply"
VALUE = "value"
INPUTS = ("audio_path", "audio_sha256", "language", "model", "output_dir", "recorded_at")
OUTPUTS = (
    "result_path",
    "result_sha256",
    "audio_sha256",
    "model",
    "language",
    "segment_count",
    "window_count",
    "empty_window_count",
)
# transcripts on the reader (t4): passed through from the inputs for pocket-to-reader
PASS_THROUGH = ("audio_path", "output_dir", "recorded_at")
READER_RULE = "pocket-to-reader"
READER_WORKFLOW = "transcript-to-reader"


def bundle():
    read = read_bundle(BUNDLE)
    assert read.errors == [], [e.to_dict() for e in read.errors]
    return read.bundle


def actor(actor_id=STT):
    return {a.id: a for a in bundle().actors}[actor_id]


def workflow(workflow_id=WORKFLOW):
    return {w.id: w for w in bundle().workflows}[workflow_id]


def rule(rule_id=RULE):
    return {r.id: r for r in bundle().rules}[rule_id]


def step(workflow_id=WORKFLOW):
    (s,) = workflow(workflow_id).steps
    return s


def bundle_files() -> list[Path]:
    return sorted(p for p in BUNDLE.rglob("*") if p.is_file())


# --------------------------------------------------------------------------- the data


def test_the_bundle_reads_without_errors():
    b = bundle()
    assert sorted(a.id for a in b.actors) == [STT, STT_HE]
    assert sorted(w.id for w in b.workflows) == [WORKFLOW, WORKFLOW_HE, READER_WORKFLOW]
    assert sorted(r.id for r in b.rules) == [READER_RULE, RULE, RULE_HE]


def test_it_imports_into_a_store_with_the_rule_disabled():
    base = MemoryStore()
    plan = import_bundle(BUNDLE, base, apply=True)
    assert plan.errors == []
    assert plan.applied is True
    for rule_id in (RULE, RULE_HE):
        assert base.get("rules", rule_id)["enabled"] is False
    for workflow_id in (WORKFLOW, WORKFLOW_HE):
        assert base.get("workflows", workflow_id) is not None
    for actor_id in (STT, STT_HE):
        assert base.get("actors", actor_id) is not None


def test_scan_secrets_finds_nothing_in_the_bundle():
    script = ROOT / "scripts" / "scan-secrets.py"
    paths = [str(p.relative_to(ROOT)) for p in bundle_files()]
    done = subprocess.run(
        [sys.executable, str(script), *paths], cwd=ROOT, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------------------- the actor


def test_the_stt_actor_is_a_valid_endpoint_actor_on_spark():
    a = actor()
    assert a.kind == "agent"
    assert a.machine == "spark"
    assert check_stt_actor(a) is None
    assert a.params["model"] == MODEL
    assert a.params["api_key"] == "grant:LOBES_GATEWAY_API_KEY"
    assert a.params["max_concurrency"] == 1
    assert a.params["concurrency_pool"]


def test_the_hebrew_actor_is_a_valid_keyless_endpoint_actor_on_its_own_pool():
    a = actor(STT_HE)
    assert a.kind == "agent"
    assert a.machine == "spark"
    assert check_stt_actor(a) is None
    assert a.params["model"] == MODEL_HE
    assert "api_key" not in a.params
    assert a.params["max_concurrency"] == 1
    assert a.params["concurrency_pool"] != actor().params["concurrency_pool"]


def test_the_base_urls_are_loopback_placeholders():
    for actor_id in (STT, STT_HE):
        assert urlsplit(actor(actor_id).params["base_url"]).hostname in LOOPBACK


def test_the_roots_are_absolute_placeholders_naming_no_real_user():
    for actor_id in (STT, STT_HE):
        for name in ("audio_root", "output_root"):
            value = actor(actor_id).params[name]
            assert os.path.isabs(value), name
            assert value.startswith("/home/OPERATOR/"), value


# --------------------------------------------------------------------------- the workflow


def test_the_workflow_has_the_file_in_inputs_and_eight_scalar_outputs():
    wf = workflow()
    assert tuple(p.name for p in wf.inputs) == INPUTS
    required = {p.name for p in wf.inputs if p.required}
    assert required == {"audio_path", "audio_sha256", "output_dir"}
    assert tuple(o.name for o in wf.outputs) == OUTPUTS + PASS_THROUGH
    sources = {o.name: o.source for o in wf.outputs}
    assert {sources[n] for n in OUTPUTS} == {f"steps.{STEP}.outputs.{n}" for n in OUTPUTS}
    assert {sources[n] for n in PASS_THROUGH} == {f"inputs.{n}" for n in PASS_THROUGH}


def test_the_workflow_is_one_audio_transcribe_step_on_the_stt_actor():
    spec = action_spec(step())
    assert spec["kind"] == "audio.transcribe"
    assert spec["params"]["actor"] == STT
    for name in INPUTS:
        if name != "recorded_at":  # passed through to the outputs, not used by the step
            assert spec["params"][name] == f"inputs.{name}"
    assert "recorded_at" not in spec["params"]
    assert step().config.get("idempotent") is True


def test_the_transcribe_step_outlives_the_longest_recording():  # c30, h22
    assert step().timeout_s >= 14400


def test_the_workflow_names_nothing_from_pocket():  # honesty: generic file-in
    for path in (WORKFLOW_FILE, WORKFLOW_HE_FILE):
        assert "pocket" not in path.read_text(encoding="utf-8").lower()


def test_the_hebrew_workflow_is_the_same_file_in_contract_on_the_hebrew_actor():
    wf, he = workflow(), workflow(WORKFLOW_HE)
    assert [p.to_dict() for p in he.inputs] == [p.to_dict() for p in wf.inputs]
    assert [o.to_dict() for o in he.outputs] == [o.to_dict() for o in wf.outputs]
    spec = action_spec(step(WORKFLOW_HE))
    assert spec["params"] == {**action_spec(step())["params"], "actor": STT_HE}
    assert step(WORKFLOW_HE).timeout_s == step().timeout_s
    assert step(WORKFLOW_HE).config.get("idempotent") is True
    assert he.description == ""
    assert step(WORKFLOW_HE).description == ""


def test_notes_are_committed_and_plain_descriptions_live_in_the_store():
    """Committed files carry technical ``notes``; the plain ``description`` is written in the
    editor and kept in the store (commit-is-code-and-logic), so an import never overwrites
    it. The plain text the corpus tests read is in tests/fixtures/workflow_descriptions.json."""
    for node in (workflow(), step()):
        assert node.description == "", node
        assert node.notes, node
    assert "audio.transcribe" in step().notes
    written = json.loads(DESCRIPTIONS.read_text())[WORKFLOW]
    assert written["description"]
    assert set(written["steps"]) == {STEP}
    for text in (written["description"], *written["steps"].values()):
        assert "audio.transcribe" not in text
        assert "pocket" not in text.lower()


# --------------------------------------------------------------------------- the rule


def test_the_rule_ships_disabled_on_the_landed_event():
    r = rule()
    assert r.enabled is False
    assert r.trigger.kind == "event"
    assert r.trigger.params == {"type": EVENT}
    assert r.workflow.id == WORKFLOW
    assert r.action.kind == "noop"
    assert r.condition is None


def test_the_hebrew_rule_ships_disabled_and_skips_only_recordings_marked_en():
    r = rule(RULE_HE)
    assert r.enabled is False
    assert r.trigger.params == {"type": EVENT}
    assert r.workflow.id == WORKFLOW_HE
    assert r.workflow.inputs == rule().workflow.inputs
    # not (== "en"), not != "en": a comparison with a missing field is false, so a landed
    # event with no language field at all still runs the Hebrew lane
    assert r.condition == {
        "arg": {
            "cmp": "==",
            "left": {"field": "data.language"},
            "op": "compare",
            "right": {"literal": "en"},
        },
        "op": "not",
    }


def test_each_lane_runs_one_transcription_per_audio_at_a_time():
    assert rule().concurrency_key == "stt-en:{trigger.data.audio_sha256}"
    assert rule(RULE_HE).concurrency_key == "stt-he:{trigger.data.audio_sha256}"


def test_the_rule_maps_the_event_and_the_variable_and_leaves_the_model_to_the_actor():
    inputs = rule().workflow.inputs
    assert inputs["audio_path"] == "trigger.data.audio_path"
    assert inputs["audio_sha256"] == "trigger.data.audio_sha256"
    assert inputs["language"] == "trigger.data.language"
    assert inputs["output_dir"] == {"$var": DIR_VAR}
    assert "model" not in inputs
    assert rule_variable_refs(rule()) == {DIR_VAR}


# --------------------------------------------------------------------------- the variables


def variables() -> dict:
    return json.loads(VARIABLES.read_text())


def test_the_variable_is_valid_and_home_relative():
    values = variables()
    assert set(values) == {DIR_VAR}
    item = values[DIR_VAR]
    validate_variable_value(item[VALUE])
    assert item["description"]
    assert item[VALUE].startswith("$HOME/")


def seed_calls(tmp_path, *args, existing=(), home=None) -> list[list[str]]:
    """The ``variables set`` argv the seed script runs, against a stub CLI."""
    log = tmp_path / "calls.jsonl"
    log.write_text("")
    stub = tmp_path / "culture-rules"
    items = json.dumps({"items": [{"name": n} for n in existing]})
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['variables', 'list']:\n"
        f"    print({items!r})\n"
    )
    stub.chmod(0o755)
    env = {**os.environ, "CULTURE_RULES": str(stub), "HOME": str(home or tmp_path)}
    subprocess.run(["bash", str(SEED), *args], env=env, check=True, capture_output=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return [c for c in calls if c[:2] == ["variables", "set"]]


def test_the_seed_script_is_a_dry_run_and_expands_the_home(tmp_path):
    (call,) = seed_calls(tmp_path, home="/srv/someone")
    assert call[2] == DIR_VAR
    assert APPLY not in call
    value = json.loads(call[call.index("--value") + 1])
    assert value == "/srv/someone/.local/share/culture-rules/transcripts"


def test_the_seed_script_keeps_an_existing_value_unless_forced(tmp_path):
    assert seed_calls(tmp_path, APPLY, existing=(DIR_VAR,)) == []
    (forced,) = seed_calls(tmp_path, APPLY, "--force", existing=(DIR_VAR,))
    assert APPLY in forced


def test_the_seed_script_calls_work_through_the_real_cli(
    tmp_path, store, wire, capsys  # noqa: F811
):
    for argv in seed_calls(tmp_path, APPLY):
        assert main([*argv, "--json"]) == 0, capsys.readouterr()
    capsys.readouterr()
    expected = os.path.join(str(tmp_path), ".local/share/culture-rules/transcripts")
    assert store.get_variable(DIR_VAR)[VALUE] == expected
