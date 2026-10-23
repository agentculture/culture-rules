"""File-in transcription t6: the shipped transcribe-file flow, end to end on a fake STT server.

One node (spark) runs the shipped bundle (docs/rules/pocket-transcribe) with its rule enabled,
the actor's two roots pointed at ``tmp_path`` (the operator's setup step) and the
``transcripts_dir`` variable seeded. The real :class:`AudioTranscribePort` runs on a fake
transport that behaves like the lobes Whisper sidecar. A synthetic
``pocketsync.recording.landed`` event goes in through the node's event source; the run must
end in a ``rules.run.succeeded`` event whose ``data.outputs`` carry the eight outputs, with
the transcript on disk. A direct ``start_workflow`` with another model then writes a second
file and leaves the first byte for byte. Audio is generated with ffmpeg; nothing is committed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.node.actions.stt import AudioTranscribePort
from culture_rules.node.actions.transcript_file import result_path
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.node.daemon import Node
from culture_rules.node.firing import run_id_for
from culture_rules.node.runner import NoopAction
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.events.fakes import FakeEventSource, envelope
from tests.node.test_audio_transcribe import FakeStt, _gen, _sha
from tests.node.test_node import BEATS
from tests.rules.test_pocket_transcribe_bundle import (
    DIR_VAR,
    EVENT,
    MODEL,
    OUTPUTS,
    RULE,
    WORKFLOW,
    bundle,
)

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)

SUCCEEDED = "rules.run.succeeded"
OTHER_MODEL = "openai/whisper-large-v3"
STATUS = "status"
DATA = "data"


class World:
    """spark running the shipped transcribe-file bundle on a fake STT server."""

    def __init__(self, tmp_path: Path) -> None:
        self.clock = Clock()
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        self.audio_root = tmp_path / "pocketsync" / "files"
        self.output_root = tmp_path / "culture-rules" / "transcripts"
        self.audio_root.mkdir(parents=True)
        self.output_root.mkdir(parents=True)
        self.out_dir = str(self.output_root / "files")
        for a in bundle().actors:
            doc = copy.deepcopy(a.to_dict())
            doc["params"]["audio_root"] = str(self.audio_root)  # the operator's setup step
            doc["params"]["output_root"] = str(self.output_root)
            self.base.put(ACTORS_COLLECTION, doc)
        for wf in bundle().workflows:
            self.base.put("workflows", wf.to_dict())
        for r in bundle().rules:
            self.base.put("rules", {**r.to_dict(), "enabled": True})
        self.base.put_variable(DIR_VAR, self.out_dir, updated_by="test")
        self.stt = FakeStt()
        self.source = FakeEventSource(name="sub@spark")
        port = AudioTranscribePort(
            self.base,
            transport=self.stt,
            secret_runner=lambda _ref: "fake-key-value",
            clock=self.clock,
        )
        self.node = Node(
            self.base.peer(),
            "spark",
            actors={"action:audio.transcribe": port, "action:noop": NoopAction()},
            event_source=self.source,
            clock=self.clock,
            heartbeat_options=BEATS,
        )
        self.node.start()

    def cycles(self, n: int = 6) -> None:
        for _ in range(n):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors

    def run_events(self, type_: str) -> list[dict]:
        return [
            e["envelope"]
            for e in self.base.find(EVENTS_COLLECTION)
            if e["envelope"]["type"] == type_
        ]


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)


@pytest.fixture
def recording(world) -> Path:
    path = world.audio_root / "rec-1" / "audio.wav"
    path.parent.mkdir()
    return _gen(path, 20)


def landed(audio: Path, n: int = 1, **over) -> dict:
    data = {
        "recording_id": "rec-1",
        "content_hash": "c" * 64,
        "updated_at": "2026-10-23T08:00:00Z",
        "audio_path": str(audio),
        "audio_sha256": _sha(audio),
        "language": "he",
        **over,
    }
    return envelope(n, type=EVENT, source="pocketsync://landing", data=data)


def test_a_landed_event_ends_in_run_succeeded_carrying_the_eight_outputs(world, recording):
    world.source.publish(landed(recording))
    world.cycles()
    run = world.base.get(RUNS_COLLECTION, run_id_for(RULE, "evt_1"))
    assert run is not None
    assert run[STATUS] == "succeeded", (run.get("error"), run["steps"])
    assert run["concurrency_key"] == f"stt:{_sha(recording)}"

    (event,) = world.run_events(SUCCEEDED)
    assert event["runId"] == run["id"]
    assert event[DATA]["rule_id"] == RULE
    outputs = event[DATA]["outputs"]
    assert set(outputs) == set(OUTPUTS)
    target = result_path(str(Path(world.out_dir).resolve()), _sha(recording), MODEL)
    assert outputs["result_path"] == target
    assert Path(target).is_file()
    assert outputs["result_sha256"] == hashlib.sha256(Path(target).read_bytes()).hexdigest()
    assert outputs["audio_sha256"] == _sha(recording)
    assert outputs["model"] == MODEL  # the actor's: the rule maps no model
    assert outputs["language"] == "he"
    assert outputs["window_count"] == 1
    assert outputs["segment_count"] == 2  # the fake's verbose reply: two segments per window
    assert outputs["empty_window_count"] == 0
    doc = json.loads(Path(target).read_bytes())
    assert doc["schema"] == 1 and doc["model"] == MODEL and len(doc["segments"]) == 2
    (call,) = world.stt.sent
    assert call["language"] == "he"


def test_a_direct_run_with_another_model_writes_a_second_file_and_keeps_the_first(world, recording):
    world.source.publish(landed(recording))
    world.cycles()
    first = result_path(str(Path(world.out_dir).resolve()), _sha(recording), MODEL)
    before = Path(first).read_bytes()

    inputs = {
        "audio_path": str(recording),
        "audio_sha256": _sha(recording),
        "output_dir": world.out_dir,
        "model": OTHER_MODEL,
    }
    started = world.node.executor.start_workflow(WORKFLOW, inputs, "operator")
    world.cycles()
    run = world.base.get(RUNS_COLLECTION, started["id"])
    assert run[STATUS] == "succeeded", (run.get("error"), run["steps"])
    second = result_path(str(Path(world.out_dir).resolve()), _sha(recording), OTHER_MODEL)
    assert run["outputs"]["result_path"] == second != first
    assert run["outputs"]["model"] == OTHER_MODEL
    assert run["outputs"]["language"] is None  # no language given: none is sent
    assert Path(second).is_file()
    assert Path(first).read_bytes() == before
    assert len(world.stt.sent) == 2
    assert world.stt.sent[1]["language"] is None


def test_the_same_audio_landing_again_is_a_no_op(world, recording):
    world.source.publish(landed(recording, 1))
    world.cycles()
    world.source.publish(landed(recording, 2, content_hash="d" * 64))
    world.cycles()
    assert [e[DATA]["rule_id"] for e in world.run_events(SUCCEEDED)] == [RULE, RULE]
    assert len(world.stt.sent) == 1  # the second run found the file: no request


FAILED = "rules.run.failed"


def test_a_refused_path_ends_in_run_failed_with_its_reason(world, recording, tmp_path):
    outside = tmp_path / "elsewhere" / "audio.wav"
    outside.parent.mkdir()
    _gen(outside, 5)
    world.source.publish(landed(outside))
    world.cycles()
    (event,) = world.run_events(FAILED)
    assert event[DATA]["error_reason"] == "path_refused"
    assert event[DATA]["error_message"].startswith("path_refused: ")
    assert world.stt.sent == []


def test_a_changed_file_ends_in_run_failed_with_audio_mismatch(world, recording):
    world.source.publish(landed(recording, audio_sha256="e" * 64))
    world.cycles()
    (event,) = world.run_events(FAILED)
    assert event[DATA]["error_reason"] == "audio_mismatch"
    assert world.stt.sent == []
