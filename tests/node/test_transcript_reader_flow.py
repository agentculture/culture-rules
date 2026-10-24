"""Transcripts on the reader t4: landed recording -> transcription -> one book on the reader.

One node (spark) runs the shipped pocket-transcribe bundle with every rule enabled, including
``pocket-to-reader``, which fires ``transcript-to-reader`` on a transcription run's
``rules.run.succeeded``. ``audio.transcribe`` is a stub that writes a synthetic schema-1
transcript per lane (no ffmpeg, no audio), ``library.push`` is a stub that records each upload
and deduplicates by the bytes' sha256 the way xteink does, and ``llm.complete`` is the real port
on a fake transport. ``transcript.render`` and ``select`` are the real built-ins. All text here
is synthetic.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.io.exchange import read_bundle
from culture_rules.node.actions.llm import TRANSCRIPT_BRIEF, LlmCompletePort
from culture_rules.node.actions.select import SELECT_BUILTIN, SelectPort
from culture_rules.node.actions.transcript_file import result_path, write_atomic
from culture_rules.node.actions.transcript_render import TranscriptRenderPort
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.node.daemon import Node
from culture_rules.node.runner import BuiltinCodePort, NoopAction
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.events.fakes import FakeEventSource, envelope
from tests.node.test_node import BEATS
from tests.rules.test_pocket_transcribe_bundle import BUNDLE, DIR_VAR, EVENT, MODEL, MODEL_HE

NEWSPAPER = BUNDLE.parent / "newspaper"
READER_RULE = "pocket-to-reader"
READER_WORKFLOW = "transcript-to-reader"
SHA = "ab" * 32
RECORDED_AT = "2026-10-23T22:30:00Z"  # 01:30 on 2026-10-24 in Israel
DAY = "2026-10-24"
PRIVATE_TEXT = "sentinel-transcript-words"
PRIVATE_SUMMARY = "sentinel-summary-line"
SUCCEEDED = "rules.run.succeeded"


def _reply(summary=PRIVATE_SUMMARY, word="meeting"):
    content = json.dumps({"summary": summary, "word": word})
    body = {"model": "cortex-spark2", "choices": [{"message": {"content": content}}]}
    body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return json.dumps(body).encode()


class FakeLlm:
    def __init__(self) -> None:
        self.status = 200
        self.body = _reply()
        self.calls: list[dict] = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append(json.loads(data))
        return self.status, self.body


class StubTranscribe:
    """``audio.transcribe`` without audio: writes each lane's synthetic transcript."""

    supports_idempotency_key = True

    def __init__(self) -> None:
        self.texts = {"stt-en": [PRIVATE_TEXT, "english words"], "stt-he": [PRIVATE_TEXT, "מילים"]}
        self.fail: set[str] = set()
        self.calls: list[dict] = []

    def invoke(self, input, _key, _deadline, *, context):
        actor = input["actor"]
        self.calls.append(dict(input))
        if actor in self.fail:
            return InvocationResult.failed("stt_unavailable: down", retryable=False)
        model = MODEL if actor == "stt-en" else MODEL_HE
        texts = self.texts[actor]
        path = result_path(input["output_dir"], input["audio_sha256"], model)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        segments = [{"start": i, "end": i + 1, "text": t} for i, t in enumerate(texts)]
        doc = {
            "schema": 1,
            "audio_sha256": input["audio_sha256"],
            "model": model,
            "language": input.get("language"),
            "segments": segments,
        }
        write_atomic(path, doc)
        out = {
            "result_path": path,
            "result_sha256": "0" * 64,
            "audio_sha256": input["audio_sha256"],
            "model": model,
            "language": input.get("language") or "",
            "segment_count": len(texts),
            "window_count": 1,
            "empty_window_count": 0 if texts else 1,
        }
        return InvocationResult.completed(out)


class StubLibrary:
    """``library.push``: records uploads; the same bytes again return the same item."""

    supports_idempotency_key = True

    def __init__(self) -> None:
        self.pushes: list[dict] = []
        self.items: dict[str, int] = {}

    def invoke(self, input, _key, _deadline, *, context):
        self.pushes.append(dict(input))
        digest = hashlib.sha256(base64.b64decode(input["content_base64"])).hexdigest()
        created = digest not in self.items
        self.items.setdefault(digest, len(self.items) + 1)
        item = self.items[digest]
        return InvocationResult.completed({"item_id": item, "created": created, "queue_id": item})


def _actors():
    docs = {a.id: copy.deepcopy(a.to_dict()) for a in read_bundle(BUNDLE).bundle.actors}
    for a in read_bundle(NEWSPAPER).bundle.actors:
        if a.id in ("cortex-spark2", "newspaper-library"):
            docs[a.id] = copy.deepcopy(a.to_dict())
    return docs


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.clock = Clock()
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        self.audio = tmp_path / "files" / "rec-1" / "audio.mp3"
        self.audio.parent.mkdir(parents=True)
        self.audio.write_bytes(b"not really audio")
        self.out_dir = str((tmp_path / "transcripts").resolve())
        Path(self.out_dir).mkdir()
        for doc in _actors().values():
            self.base.put(ACTORS_COLLECTION, doc)
        bundle = read_bundle(BUNDLE)
        assert bundle.errors == [], [e.to_dict() for e in bundle.errors]
        for wf in bundle.bundle.workflows:
            self.base.put("workflows", wf.to_dict())
        for r in bundle.bundle.rules:
            self.base.put("rules", {**r.to_dict(), "enabled": True})
        self.base.put_variable(DIR_VAR, self.out_dir, updated_by="test")
        self.base.put_variable("newspaper_device", 3, updated_by="test")  # the operator's setup
        self.stt = StubTranscribe()
        self.library = StubLibrary()
        self.llm = FakeLlm()
        llm = LlmCompletePort(
            self.base, transport=self.llm, secret_runner=lambda _r: "k", clock=self.clock
        )
        self.source = FakeEventSource(name="sub@spark")
        self.node = Node(
            self.base.peer(),
            "spark",
            actors={
                "action:audio.transcribe": self.stt,
                "action:llm.complete": llm,
                "action:transcript.render": TranscriptRenderPort(self.base),
                "action:library.push": self.library,
                "action:noop": NoopAction(),
                "code": BuiltinCodePort({SELECT_BUILTIN: SelectPort()}),
            },
            event_source=self.source,
            clock=self.clock,
            heartbeat_options=BEATS,
        )
        self.node.start()
        self._n = 0

    def land(self, **over) -> None:
        self._n += 1
        data = {
            "recording_id": "rec-1",
            "audio_path": str(self.audio),
            "audio_sha256": SHA,
            "language": "he",
            "recording_at": RECORDED_AT,
            **over,
        }
        data = {k: v for k, v in data.items() if v is not None}
        self.source.publish(envelope(self._n, type=EVENT, source="pocketsync://landing", data=data))
        self.cycles()

    def cycles(self, n: int = 14) -> None:
        for _ in range(n):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors

    def reader_runs(self) -> list[dict]:
        return [r for r in self.base.find(RUNS_COLLECTION) if r.get("rule_id") == READER_RULE]

    def run_events(self) -> list[dict]:
        return [
            e["envelope"]
            for e in self.base.find(EVENTS_COLLECTION)
            if e["envelope"]["type"] == SUCCEEDED
        ]

    def book(self, n: int = -1) -> zipfile.ZipFile:
        raw = base64.b64decode(self.library.pushes[n]["content_base64"])
        return zipfile.ZipFile(io.BytesIO(raw))


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)


def _opf(book: zipfile.ZipFile) -> str:
    name = next(n for n in book.namelist() if n.endswith(".opf"))
    return book.read(name).decode()


def test_a_hebrew_recording_gets_one_book_from_the_zipformer_run(world):
    world.land(language="he")
    (run,) = world.reader_runs()
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])
    assert run["concurrency_key"] == f"reader:{SHA}"
    (push,) = world.library.pushes
    assert push["title"] == f"{DAY} meeting"
    assert push["kind"] == "article"
    assert push["actor"] == "newspaper-library"
    assert 'page-progression-direction="rtl"' in _opf(world.book())
    assert run["outputs"]["source"] == "given"
    (event,) = [e for e in world.run_events() if e["data"]["rule_id"] == READER_RULE]
    assert event["data"]["outputs"]["title"] == f"{DAY} meeting"


def test_an_unlabelled_recording_gets_one_book_and_the_whisper_success_starts_nothing(world):
    world.land(language=None)
    (run,) = world.reader_runs()
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])
    (push,) = world.library.pushes
    assert push["title"] == f"{DAY} meeting"
    starts = [e for e in world.run_events() if e["data"]["rule_id"] != READER_RULE]
    assert {e["data"]["workflow_id"] for e in starts} == {"transcribe-file", "transcribe-file-he"}


def test_an_english_recording_gets_its_book_from_whisper(world):
    world.land(language="en")
    (run,) = world.reader_runs()
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])
    assert [c["actor"] for c in world.stt.calls] == ["stt-en"]
    (push,) = world.library.pushes
    assert 'page-progression-direction="ltr"' in _opf(world.book())
    assert run["outputs"]["source"] == "given"


def test_an_english_recording_with_empty_whisper_is_transcribed_again_on_stt_he(world):
    world.stt.texts["stt-en"] = []
    world.land(language="en")
    (run,) = world.reader_runs()
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])
    assert [c["actor"] for c in world.stt.calls] == ["stt-en", "stt-he"]
    assert run["outputs"]["source"] == "stt-he"
    (push,) = world.library.pushes
    assert push["title"] == f"{DAY} meeting"


def test_a_failed_zipformer_run_starts_no_book(world):
    world.stt.fail.add("stt-he")
    world.land(language="he")
    assert world.reader_runs() == []
    assert world.library.pushes == []


def test_a_summary_failure_still_ships_the_book_as_a_recording(world):
    world.llm.status = 400  # llm_rejected: not retryable, so the skip is immediate
    world.land(language="he")
    (run,) = world.reader_runs()
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])
    (push,) = world.library.pushes
    assert push["title"] == f"{DAY} recording"
    assert "Summary unavailable." in world.book().read("OEBPS/c1.xhtml").decode()
    assert run["outputs"]["summary_used"] is False


def test_the_summary_reads_the_transcript_itself_with_the_transcript_brief(world):
    world.land(language="he")
    (call,) = world.llm.calls
    assert call["messages"][0]["content"] == TRANSCRIPT_BRIEF
    assert PRIVATE_TEXT in call["messages"][1]["content"]


def test_the_same_recording_again_pushes_the_same_bytes(world):
    world.land(language="he")
    world.land(language="he")
    pushes = world.library.pushes
    assert len(pushes) == 2
    assert len({p["content_base64"] for p in pushes}) == 1
    assert len(world.library.items) == 1


def test_no_transcript_text_enters_the_run_and_no_summary_text_its_outputs(world):
    world.land(language="he")
    (run,) = world.reader_runs()
    assert PRIVATE_TEXT not in json.dumps(run, default=str)
    assert PRIVATE_SUMMARY not in json.dumps(run["outputs"])
    for event in world.run_events():
        assert PRIVATE_TEXT not in json.dumps(event)
        assert PRIVATE_SUMMARY not in json.dumps(event)


def test_a_rerun_whose_summary_answers_differently_still_adds_no_second_book(world):
    world.land(language="he")
    world.llm.body = _reply(summary="a different line", word="budget")
    world.land(language="he")
    pushes = world.library.pushes
    assert len(pushes) == 2
    assert {p["title"] for p in pushes} == {f"{DAY} meeting"}
    assert len({p["content_base64"] for p in pushes}) == 1
    assert len(world.library.items) == 1


def test_the_book_is_queued_on_the_newspaper_device(world):
    world.land(language="he")
    (push,) = world.library.pushes
    assert push["device_id"] == 3
