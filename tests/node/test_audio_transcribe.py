"""audio.transcribe action port (file-in transcription t5): checks, split, upload, resume, codes.

The port is called directly with a fake STT transport that behaves like the lobes Whisper
sidecar's caps: it answers 413 for a body over 16 MiB or a WAV longer than 600 s, so an
unsplit upload of the 1500 s file would fail. Audio is generated with ffmpeg in ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import subprocess
import threading
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions import stt
from culture_rules.node.actions.stt import AudioTranscribePort, check_stt_actor, urllib_transport
from culture_rules.node.actions.transcript_file import WorkDir, read_existing, result_path
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)

LOCAL = "http://127.0.0.1:8001"
MODEL = "ivrit-ai/whisper-large-v3-turbo"
FAKE_BEARER = "sk-test-bearer-value-123"
KEY_REF = "grant:STT_KEY"
MAX_BODY = 16 * 1024 * 1024
MAX_WAV_S = 600.0


# ---- audio fixtures -------------------------------------------------------------------------


def _gen(path: Path, seconds: float, *, period: int = 70, rate: int = 8000, codec=None) -> Path:
    """A sine tone that drops to 3 s of silence at the start of every ``period`` seconds."""
    expr = f"sin(2*PI*440*t)*gt(mod(t\\,{period})\\,3)"
    argv = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"aevalsrc={expr}:s={rate}:d={seconds}",
        "-ac",
        "1",
    ]
    argv += codec or []
    subprocess.run([*argv, str(path)], check=True, timeout=120)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def long_audio(tmp_path_factory) -> Path:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not installed")
    return _gen(tmp_path_factory.mktemp("long") / "long.wav", 1500)


@pytest.fixture(scope="module")
def short_audio(tmp_path_factory) -> Path:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not installed")
    return _gen(tmp_path_factory.mktemp("short") / "short.wav", 20)


# ---- the fake STT server --------------------------------------------------------------------


def _multipart(content_type: str, body: bytes) -> dict[str, tuple[dict[str, str], bytes]]:
    boundary = content_type.split("boundary=", 1)[1].encode()
    parts: dict[str, tuple[dict[str, str], bytes]] = {}
    for chunk in body.split(b"--" + boundary)[1:]:
        if chunk.startswith(b"--"):
            break
        head, _, value = chunk[2:].partition(b"\r\n\r\n")
        value = value[:-2]  # the trailing CRLF before the next delimiter
        headers: dict[str, str] = {}
        for line in head.decode().split("\r\n"):
            key, _, val = line.partition(": ")
            headers[key.lower()] = val
        name = headers["content-disposition"].split('name="', 1)[1].split('"', 1)[0]
        parts[name] = (headers, value)
    return parts


def _wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        return w.getnframes() / w.getframerate()


class FakeStt:
    """Records each request; enforces the lobes caps; answers with ``segments`` replies.

    ``fail`` maps a 1-based call number to ``(status, body)`` or an exception to raise."""

    def __init__(self, fail=None, *, status=200, body=None, exc=None):
        self.fail = dict(fail or {})
        self.status, self.body, self.exc = status, body, exc
        self.calls: list[dict] = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        n = len(self.calls)
        if self.exc is not None:
            raise self.exc
        if n in self.fail:
            outcome = self.fail.pop(n)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        if self.body is not None:
            return self.status, self.body
        if len(data) > MAX_BODY:
            return 413, b'{"error":"upload_too_large"}'
        parts = _multipart(headers["Content-Type"], data)
        wav_headers, wav = parts["file"]
        seconds = _wav_seconds(wav)
        self.calls[-1].update(
            seconds=seconds,
            size=len(data),
            filename=wav_headers["content-disposition"].split('filename="')[1].split('"')[0],
            file_type=wav_headers.get("content-type"),
            fields=sorted(parts),
            language=parts["language"][1].decode() if "language" in parts else None,
        )
        if seconds > MAX_WAV_S:
            return 413, b'{"error":"clip_too_long"}'
        reply = {
            "text": f"window {n}",
            "segments": [
                {"start": 0.0, "end": seconds / 2, "text": f"w{n}a"},
                {"start": seconds / 2, "end": seconds, "text": f"w{n}b"},
            ],
        }
        return 200, json.dumps(reply).encode()

    @property
    def sent(self) -> list[dict]:
        return [c for c in self.calls if "seconds" in c]


# ---- port helpers ---------------------------------------------------------------------------


def _actor(aroot, oroot, actor_id="stt", kind="agent", **params):
    base = {
        "base_url": LOCAL,
        "model": MODEL,
        "api_key": KEY_REF,
        "audio_root": str(aroot),
        "output_root": str(oroot),
    }
    base.update(params)
    base = {k: v for k, v in base.items() if v is not None}
    return {"id": actor_id, "name": actor_id, "kind": kind, "enabled": True, "params": base}


def _ctx(actor="stt"):
    return InvocationContext(
        run_id="run1",
        step_id="transcribe",
        kind="action",
        host="spark",
        actor=actor,
        config={"kind": "audio.transcribe", "params": {"actor": actor}},
    )


class Env:
    def __init__(self, tmp_path: Path, audio: Path | None = None, transport=None, **actor_params):
        self.audio_root = tmp_path / "audio"
        self.output_root = tmp_path / "out"
        self.audio_root.mkdir(exist_ok=True)
        self.output_root.mkdir(exist_ok=True)
        self.audio = None
        if audio is not None:
            self.audio = self.audio_root / audio.name
            if not self.audio.exists():
                shutil.copy(audio, self.audio)
        self.store = MemoryStore()
        self.store.put(ACTORS_COLLECTION, _actor(self.audio_root, self.output_root, **actor_params))
        self.transport = transport or FakeStt()
        self.secret_lookups: list[str] = []
        self.port = self.new_port()

    def _secret(self, name: str) -> str:
        self.secret_lookups.append(name)
        return FAKE_BEARER

    def new_port(self) -> AudioTranscribePort:
        return AudioTranscribePort(self.store, transport=self.transport, secret_runner=self._secret)

    def params(self, **over):
        params = {
            "actor": "stt",
            "audio_path": str(self.audio or self.audio_root / "none.wav"),
            "audio_sha256": _sha(self.audio) if self.audio else "0" * 64,
            "output_dir": str(self.output_root / "transcripts"),
        }
        params.update(over)
        return {k: v for k, v in params.items() if v is not None}

    def invoke(self, key="k1", seconds=3600, port=None, **over):
        deadline = datetime.now(UTC) + timedelta(seconds=seconds)
        return (port or self.port).invoke(self.params(**over), key, deadline, context=_ctx())


def _failed(res, code, *, retryable=False):
    assert res.outcome == FAILED, res
    assert res.error.split(":", 1)[0] == code, res.error
    assert res.retryable is retryable, res
    assert FAKE_BEARER not in res.error
    return res


# ---- the long file: split, offsets, caps ----------------------------------------------------


@needs_ffmpeg
def test_a_1500s_file_is_sent_as_capped_windows_with_offset_segments(tmp_path, long_audio):
    env = Env(tmp_path, long_audio, language="he")
    res = env.invoke(language="he")
    assert res.outcome == COMPLETED, res
    sent = env.transport.sent
    assert len(sent) >= 4
    assert len(env.transport.calls) == len(sent)  # no 413 was ever answered
    for call in sent:
        assert call["seconds"] <= 480 + 0.05
        assert call["size"] <= MAX_BODY
        assert call["url"] == LOCAL + "/v1/audio/transcriptions"
        assert call["headers"]["Authorization"] == "Bearer " + FAKE_BEARER
        assert call["fields"] == ["file", "language"]
        assert call["language"] == "he"
        assert call["file_type"] == "audio/wav"
        assert 0 < call["timeout"] <= 900
    assert [c["filename"] for c in sent] == [f"window-{i:04d}.wav" for i in range(1, len(sent) + 1)]
    assert abs(sum(c["seconds"] for c in sent) - 1500) < 0.1

    out = res.output
    doc = json.loads(Path(out["result_path"]).read_bytes())
    assert out["result_path"] == result_path(
        str((env.output_root / "transcripts").resolve()), _sha(env.audio), MODEL
    )
    assert out["result_sha256"] == hashlib.sha256(Path(out["result_path"]).read_bytes()).hexdigest()
    assert out["audio_sha256"] == _sha(env.audio)
    assert out["model"] == MODEL
    assert out["language"] == "he"
    assert out["window_count"] == len(sent)
    assert out["empty_window_count"] == 0
    assert out["segment_count"] == 2 * len(sent) == len(doc["segments"])
    assert "text" not in out
    assert "segments" not in out

    window_start = 0.0
    for i, call in enumerate(sent):
        first = doc["segments"][2 * i]
        assert abs(first["start"] - window_start) < 0.05, (i, first, window_start)
        assert first["text"] == f"w{i + 1}a"
        window_start += call["seconds"]
    # the first cut falls in the silence that begins at 420 s, not at the 480 s hard limit
    assert sent[0]["seconds"] < 470
    assert not WorkDir(
        str((env.output_root / "transcripts").resolve()), _sha(env.audio), MODEL
    ).get(1)


@needs_ffmpeg
def test_an_unsplit_upload_would_be_refused_by_the_fake(tmp_path, long_audio):
    """The fake really enforces the caps: one 1500 s WAV answers 413 (stt_rejected)."""
    big = tmp_path / "big.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(long_audio), "-ac", "1", "-ar"]
        + ["16000", "-c:a", "pcm_s16le", str(big)],
        check=True,
    )
    fake = FakeStt()
    status, _ = fake(LOCAL, *_upload(big))
    assert status == 413
    assert _failed(stt._status_failure(413, b"").result(), "stt_rejected")


def _upload(path: Path):
    from culture_rules.node.actions.library import encode_multipart

    ctype, body = encode_multipart([], [("file", "x.wav", "audio/wav", path.read_bytes())])
    return body, {"Content-Type": ctype}, 10


@needs_ffmpeg
def test_a_second_run_is_a_no_op_with_the_same_outputs(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    first = env.invoke(key="k1")
    assert first.outcome == COMPLETED, first
    calls = len(env.transport.calls)
    assert calls == 1
    port = env.new_port()  # a fresh process: no ledger to replay from
    second = env.invoke(key="k2", port=port)
    assert second.outcome == COMPLETED, second
    assert len(env.transport.calls) == calls
    assert second.output == first.output


@needs_ffmpeg
def test_the_no_op_runs_no_ffmpeg(tmp_path, short_audio, monkeypatch):
    env = Env(tmp_path, short_audio)
    assert env.invoke(key="k1").outcome == COMPLETED

    def boom(*_a, **_k):
        raise AssertionError("ffmpeg ran on the no-op path")

    for name in ("probe_duration", "detect_silences", "extract_window"):
        monkeypatch.setattr(stt.audio_split, name, boom)
    assert env.invoke(key="k2", port=env.new_port()).outcome == COMPLETED


@needs_ffmpeg
def test_a_completed_key_replays_from_the_ledger(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    first = env.invoke(key="k1")
    assert env.port.replay("k1") is first
    assert env.invoke(key="k1") is first
    assert env.port.supports_idempotency_key is True


@needs_ffmpeg
def test_a_different_model_writes_a_second_file(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    a = env.invoke(key="k1")
    b = env.invoke(key="k2", model="other/model")
    assert a.outcome == b.outcome == COMPLETED
    assert a.output["result_path"] != b.output["result_path"]
    assert Path(a.output["result_path"]).exists()
    assert Path(b.output["result_path"]).exists()
    assert b.output["model"] == "other/model"
    assert len(env.transport.calls) == 2


@needs_ffmpeg
def test_a_stale_result_for_another_model_field_is_redone(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    first = env.invoke(key="k1")
    path = Path(first.output["result_path"])
    doc = json.loads(path.read_bytes())
    doc["model"] = "someone-else"
    path.write_text(json.dumps(doc))
    again = env.invoke(key="k2", port=env.new_port())
    assert again.outcome == COMPLETED
    assert len(env.transport.calls) == 2
    assert read_existing(str(path))["model"] == MODEL


# ---- resume ---------------------------------------------------------------------------------


@needs_ffmpeg
def test_a_503_on_window_3_fails_retryable_and_the_retry_sends_only_3_and_4(tmp_path, long_audio):
    env = Env(tmp_path, long_audio, transport=FakeStt(fail={3: (503, b"busy")}))
    res = env.invoke(key="k1")
    _failed(res, "stt_unavailable", retryable=True)
    assert len(env.transport.calls) == 3
    first_two = [c["seconds"] for c in env.transport.sent]
    assert len(first_two) == 2

    retry = env.invoke(key="k1", port=env.new_port())
    assert retry.outcome == COMPLETED, retry
    resent = env.transport.calls[3:]
    assert len(resent) == 2  # windows 3 and 4 only
    assert [c["filename"] for c in resent] == ["window-0003.wav", "window-0004.wav"]
    assert retry.output["window_count"] == 4
    doc = json.loads(Path(retry.output["result_path"]).read_bytes())
    assert abs(doc["segments"][4]["start"] - sum(first_two)) < 0.05
    workdir = Path(retry.output["result_path"]).parent / f".work.{MODEL.replace('/', '-')}"
    assert not workdir.exists()


# ---- refusals before any request ------------------------------------------------------------


@needs_ffmpeg
def test_a_path_outside_audio_root_is_refused(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    outside = tmp_path / "elsewhere.wav"
    shutil.copy(short_audio, outside)
    _failed(env.invoke(audio_path=str(outside), audio_sha256=_sha(outside)), "path_refused")
    dotdot = str(env.audio_root / ".." / "elsewhere.wav")
    _failed(env.invoke(audio_path=dotdot, audio_sha256=_sha(outside)), "path_refused")
    assert env.transport.calls == []


@needs_ffmpeg
def test_a_symlink_escaping_audio_root_is_refused(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    outside = tmp_path / "secret.wav"
    shutil.copy(short_audio, outside)
    link = env.audio_root / "link.wav"
    link.symlink_to(outside)
    _failed(env.invoke(audio_path=str(link), audio_sha256=_sha(outside)), "path_refused")
    assert env.transport.calls == []


@needs_ffmpeg
def test_an_output_dir_outside_output_root_is_refused(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    _failed(env.invoke(output_dir=str(tmp_path / "nope")), "path_refused")
    _failed(env.invoke(output_dir=str(env.output_root / ".." / "nope")), "path_refused")
    escape = env.output_root / "escape"
    escape.symlink_to(tmp_path)
    _failed(env.invoke(output_dir=str(escape / "x")), "path_refused")
    assert env.transport.calls == []
    assert not (tmp_path / "nope").exists()


@needs_ffmpeg
def test_an_actor_without_roots_refuses_every_path(tmp_path, short_audio):
    no_audio_root = Env(tmp_path, short_audio, audio_root=None)
    _failed(no_audio_root.invoke(), "path_refused")
    no_output_root = Env(tmp_path, short_audio, output_root=None)
    _failed(no_output_root.invoke(), "path_refused")
    assert no_audio_root.transport.calls == []
    assert no_output_root.transport.calls == []


@pytest.mark.parametrize("bad", ["../../etc", "A" * 64, "0" * 63, "", 12])
def test_a_bad_audio_sha256_is_invalid_request(tmp_path, bad, monkeypatch):
    env = Env(tmp_path)
    touched: list[str] = []
    monkeypatch.setattr(stt, "_hash_file", lambda p: touched.append(p) or "x")
    params = env.params()
    params["audio_sha256"] = bad
    res = env.port.invoke(params, "k", datetime.now(UTC) + timedelta(hours=1), context=_ctx())
    _failed(res, "invalid_request")
    assert env.port.refuse_before_admit(params) is not None
    assert touched == []
    assert env.transport.calls == []
    assert not any(p.name != "transcripts" for p in env.output_root.iterdir())


@pytest.mark.parametrize("missing", ["audio_path", "output_dir"])
def test_missing_params_are_invalid_request(tmp_path, missing):
    env = Env(tmp_path)
    params = env.params(audio_path=str(env.audio_root / "a.wav"))
    params.pop(missing)
    res = env.port.invoke(params, "k", datetime.now(UTC) + timedelta(hours=1), context=_ctx())
    _failed(res, "invalid_request")


@needs_ffmpeg
def test_changed_bytes_are_audio_mismatch(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    sha = _sha(env.audio)
    with open(env.audio, "ab") as f:
        f.write(b"\0" * 10)
    _failed(env.invoke(audio_sha256=sha), "audio_mismatch")
    assert env.transport.calls == []


@needs_ffmpeg
def test_changed_bytes_after_a_result_are_audio_mismatch_not_a_no_op(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    sha = _sha(env.audio)
    assert env.invoke(key="k1").outcome == COMPLETED
    with open(env.audio, "ab") as f:
        f.write(b"\0" * 10)
    _failed(env.invoke(key="k2", port=env.new_port(), audio_sha256=sha), "audio_mismatch")


@needs_ffmpeg
def test_ffmpeg_missing_from_path(tmp_path, short_audio, monkeypatch):
    env = Env(tmp_path, short_audio)
    empty = tmp_path / "bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    res = _failed(env.invoke(), "ffmpeg_missing")
    assert "ffmpeg" in res.error
    assert env.transport.calls == []


def test_garbage_audio_is_audio_undecodable(tmp_path):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not installed")
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(os.urandom(4096))
    env = Env(tmp_path, junk)
    _failed(env.invoke(), "audio_undecodable")
    assert env.transport.calls == []


@needs_ffmpeg
def test_a_400_is_stt_rejected(tmp_path, short_audio):
    env = Env(tmp_path, short_audio, transport=FakeStt(status=400, body=b'{"error":"bad"}'))
    res = _failed(env.invoke(), "stt_rejected")
    assert "bad" not in res.error.split(":", 1)[1]  # no body in the error
    assert len(env.transport.calls) == 1


@needs_ffmpeg
def test_a_400_invalid_audio_is_audio_undecodable(tmp_path, short_audio):
    body = b'{"error":{"code":"invalid_audio","message":"cannot decode"}}'
    env = Env(tmp_path, short_audio, transport=FakeStt(status=400, body=body))
    _failed(env.invoke(), "audio_undecodable")


@needs_ffmpeg
def test_connection_refused_is_retryable_stt_unavailable(tmp_path, short_audio):
    env = Env(tmp_path, short_audio, transport=FakeStt(exc=ConnectionRefusedError()))
    _failed(env.invoke(), "stt_unavailable", retryable=True)
    assert len(env.transport.calls) == 1


@needs_ffmpeg
@pytest.mark.parametrize("status", [500, 502, 408, 429])
def test_retryable_statuses(tmp_path, short_audio, status):
    env = Env(tmp_path, short_audio, transport=FakeStt(status=status, body=b""))
    _failed(env.invoke(), "stt_unavailable", retryable=True)


@needs_ffmpeg
@pytest.mark.parametrize("status", [401, 403, 404, 413, 422])
def test_other_4xx_are_stt_rejected(tmp_path, short_audio, status):
    env = Env(tmp_path, short_audio, transport=FakeStt(status=status, body=b""))
    _failed(env.invoke(), "stt_rejected")


@needs_ffmpeg
@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"txt": "x"}', b'{"text": 3}', b'{"text": "x", "segments": "no"}']
    + [b'{"text": "x", "segments": [{"start": "0", "end": 1, "text": "x"}]}', b"{" * 10],
)
def test_bad_replies_are_stt_bad_response(tmp_path, short_audio, body):
    env = Env(tmp_path, short_audio, transport=FakeStt(body=body))
    _failed(env.invoke(), "stt_bad_response")
    assert not WorkDir(
        str((env.output_root / "transcripts").resolve()), _sha(env.audio), MODEL
    ).get(1)


@needs_ffmpeg
def test_an_oversized_reply_is_stt_bad_response(tmp_path, short_audio):
    body = json.dumps({"text": "x" * (stt.MAX_RESPONSE_BYTES + 10)}).encode()
    env = Env(tmp_path, short_audio, transport=FakeStt(body=body))
    _failed(env.invoke(), "stt_bad_response")


@needs_ffmpeg
def test_a_plain_text_reply_is_one_segment_over_the_window(tmp_path, short_audio):
    env = Env(tmp_path, short_audio, transport=FakeStt(body=b'{"text": "shalom"}'))
    res = env.invoke()
    assert res.outcome == COMPLETED, res
    doc = json.loads(Path(res.output["result_path"]).read_bytes())
    assert len(doc["segments"]) == 1
    seg = doc["segments"][0]
    assert seg["start"] == 0
    assert abs(seg["end"] - 20) < 0.1
    assert seg["text"] == "shalom"
    assert res.output["language"] is None
    assert env.transport.calls[0].get("fields") is None  # the fixed body skipped parsing


@needs_ffmpeg
def test_language_is_omitted_when_not_given(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    assert env.invoke().outcome == COMPLETED
    assert env.transport.sent[0]["fields"] == ["file"]


@needs_ffmpeg
def test_an_empty_window_is_kept_and_counted(tmp_path, short_audio):
    env = Env(tmp_path, short_audio, transport=FakeStt(body=b'{"text": ""}'))
    res = env.invoke()
    assert res.output["empty_window_count"] == 1
    assert res.output["segment_count"] == 1


@needs_ffmpeg
def test_mp3_input(tmp_path):
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    if "libmp3lame" not in probe.stdout:
        pytest.skip("ffmpeg has no libmp3lame encoder")
    mp3 = _gen(tmp_path / "rec.mp3", 15, codec=["-c:a", "libmp3lame"])
    env = Env(tmp_path, mp3)
    res = env.invoke()
    assert res.outcome == COMPLETED, res
    assert len(env.transport.sent) == 1


# ---- the actor ------------------------------------------------------------------------------


def test_a_non_agent_actor_is_actor_kind_mismatch(tmp_path):
    env = Env(tmp_path, kind="runner")
    _failed(env.invoke(), "actor_kind_mismatch")


@pytest.mark.parametrize(
    "over",
    [
        {"base_url": "ftp://x"},
        {"base_url": "http://u:p@h"},
        {"model": ""},
        {"api_key": "sk-literal"},
        {"audio_root": 5},
        {"output_root": "relative/dir"},
    ],
)
def test_invalid_actor_params_are_stt_actor_invalid(tmp_path, over):
    env = Env(tmp_path, **over)
    res = _failed(env.invoke(), "stt_actor_invalid")
    assert "sk-literal" not in res.error
    actor_doc = env.store.get(ACTORS_COLLECTION, "stt")
    from culture_rules.model.actor import Actor

    assert check_stt_actor(Actor.from_dict(actor_doc, strict=False))


def test_a_valid_actor_passes_the_check(tmp_path):
    from culture_rules.model.actor import Actor

    doc = _actor(tmp_path, tmp_path)
    assert check_stt_actor(Actor.from_dict(doc, strict=False)) is None


@needs_ffmpeg
def test_a_secret_failure_is_secret_unresolved(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)

    def broken(_name):
        raise RuntimeError(FAKE_BEARER)

    port = AudioTranscribePort(env.store, transport=env.transport, secret_runner=broken)
    _failed(env.invoke(port=port), "secret_unresolved")
    assert env.transport.calls == []


@needs_ffmpeg
def test_the_model_taken_rule_applies(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    holder = _actor(env.audio_root, env.output_root, actor_id="aaa")
    env.store.put(ACTORS_COLLECTION, holder)
    res = _failed(env.invoke(), "model_taken")
    assert "aaa" in res.error


@needs_ffmpeg
def test_a_passed_deadline_sends_nothing(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    _failed(env.invoke(seconds=-1), "stt_unavailable", retryable=True)
    assert env.transport.calls == []


@needs_ffmpeg
def test_the_key_never_appears_in_outputs_or_files(tmp_path, short_audio):
    env = Env(tmp_path, short_audio)
    res = env.invoke()
    assert FAKE_BEARER not in json.dumps(res.output)
    assert FAKE_BEARER not in Path(res.output["result_path"]).read_text()
    assert env.secret_lookups == ["STT_KEY"]


# ---- the default transport against a real local server --------------------------------------


@needs_ffmpeg
def test_the_urllib_transport_posts_multipart_to_a_local_server(tmp_path, short_audio):
    seen: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.update(path=self.path, auth=self.headers["Authorization"])
            seen["parts"] = sorted(_multipart(self.headers["Content-Type"], body))
            out = b'{"text": "hi"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *_):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        env = Env(tmp_path, short_audio, base_url=base)
        port = AudioTranscribePort(env.store, secret_runner=env._secret)
        res = env.invoke(port=port, language="he")
        assert res.outcome == COMPLETED, res
        assert seen == {
            "path": "/v1/audio/transcriptions",
            "auth": "Bearer " + FAKE_BEARER,
            "parts": ["file", "language"],
        }
    finally:
        server.shutdown()
        server.server_close()


def test_the_urllib_transport_raises_oserror_on_a_closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    with pytest.raises(OSError):
        urllib_transport(f"http://127.0.0.1:{free}/v1/audio/transcriptions", b"", {}, 2)


# ---- audio_sha256 optional, missing audio final (ffmpeg-independent) -------------------------


@pytest.fixture
def fake_audio(tmp_path, monkeypatch):
    """Opaque bytes as the audio, with the ffmpeg-backed helpers faked out."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"not really audio" * 100_000)  # ~1.6 MB: more than one hash chunk
    monkeypatch.setattr(stt.audio_split, "require_tools", lambda: None)
    monkeypatch.setattr(stt.audio_split, "probe_duration", lambda p: 10.0)
    monkeypatch.setattr(stt.audio_split, "detect_silences", lambda p: [])

    def extract(path, start, end, dest):
        Path(dest).write_bytes(b"RIFFfake")
        return Path(dest)

    monkeypatch.setattr(stt.audio_split, "extract_window", extract)
    monkeypatch.setattr(stt, "_parse", lambda raw, s, e: {"text": "hello"})
    return src


def test_without_audio_sha256_the_port_hashes_the_file_and_writes_under_it(
    tmp_path, fake_audio, monkeypatch
):
    monkeypatch.setattr(stt, "HASH_CHUNK", 4096)  # force many chunked reads
    env = Env(tmp_path, fake_audio, transport=lambda *a: (200, b"{}"))
    sha = _sha(env.audio)
    given = env.invoke(key="k1")
    assert given.outcome == COMPLETED, given
    omitted = env.invoke(key="k2", port=env.new_port(), audio_sha256=None)
    assert omitted.outcome == COMPLETED, omitted
    assert omitted.output["audio_sha256"] == sha
    assert omitted.output["result_path"] == given.output["result_path"]
    assert omitted.output["result_path"] == result_path(
        str((env.output_root / "transcripts").resolve()), sha, MODEL
    )
    assert omitted.output == given.output


def test_a_first_run_without_audio_sha256_writes_the_result_under_the_hash(tmp_path, fake_audio):
    env = Env(tmp_path, fake_audio, transport=lambda *a: (200, b"{}"))
    res = env.invoke(audio_sha256=None)
    assert res.outcome == COMPLETED, res
    sha = _sha(env.audio)
    assert res.output["audio_sha256"] == sha
    assert (env.output_root / "transcripts" / sha).is_dir()
    assert Path(res.output["result_path"]).parent.name == sha


def test_a_missing_audio_file_is_a_final_audio_missing(tmp_path, fake_audio):
    env = Env(tmp_path, fake_audio, transport=lambda *a: (200, b"{}"))
    gone = env.audio_root / "sub" / "gone.wav"
    for over in ({"audio_sha256": None}, {"audio_sha256": "0" * 64}):
        res = _failed(env.invoke(audio_path=str(gone), **over), "audio_missing")
        assert "sub/gone.wav" in res.error
        assert str(env.audio_root) not in res.error
    assert not (env.output_root / "transcripts").exists()


def test_a_missing_file_outside_audio_root_is_still_path_refused(tmp_path, fake_audio):
    env = Env(tmp_path, fake_audio)
    _failed(env.invoke(audio_path=str(tmp_path / "nope.wav")), "path_refused")


def test_a_wrong_given_audio_sha256_is_still_audio_mismatch(tmp_path, fake_audio):
    env = Env(tmp_path, fake_audio, transport=lambda *a: (200, b"{}"))
    _failed(env.invoke(audio_sha256="1" * 64), "audio_mismatch")
    assert not (env.output_root / "transcripts" / ("1" * 64)).exists()
