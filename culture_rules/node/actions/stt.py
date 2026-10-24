"""``audio.transcribe`` action port: one audio file to an OpenAI-compatible STT endpoint.

Params (``culture_rules.model.action_kinds``): ``actor`` (required; falls back to
``context.actor``), ``audio_path`` (an absolute path to the audio file), optional ``audio_sha256``
(64 lowercase hex characters: the file's expected sha256; when absent the port hashes the
file itself), ``output_dir`` (an absolute
directory the result goes under), and optional ``language`` (sent as the ``language`` form
field only when given) and ``model`` (else the actor's ``params.model``, else its ``model``).

The bound actor is an ``agent`` actor whose ``params`` carry ``base_url`` (checked like
``llm.complete``'s), ``model``, an optional ``api_key`` (a ``grant:<NAME>`` reference,
resolved at call time and sent as a bearer token), and the two allowlisted roots
``audio_root`` and ``output_root`` (absolute paths). An actor with no ``audio_root`` or no
``output_root`` refuses every path. ``concurrency_pool`` / ``max_concurrency`` apply
through :class:`~culture_rules.actors.limits.LimitedActor`, as for ``llm.complete``, and the
one-enabled-endpoint-actor-per-model rule refuses at run time (``model_taken``).

Order of work (every refusal before the first request names no body, header value or
secret):

1. ``audio_sha256``, when given, is validated by regex before it forms any path
   (``invalid_request``);
2. the audio path is resolved (symlinks followed) and must lie under ``audio_root``; a file
   that does not exist is ``audio_missing`` (final), and without ``audio_sha256`` the file
   is hashed here, in 1 MiB reads, to form the result path; ``output_dir`` (and its ``<audio_sha256>`` directory, when it
   exists) must lie under ``output_root`` (``path_refused``);
3. ``ffmpeg`` and ``ffprobe`` must be on PATH (``ffmpeg_missing``);
4. a given ``audio_sha256`` must match the file, hashed in 1 MiB reads (``audio_mismatch``);
5. an existing ``<output_dir>/<audio_sha256>/transcript.<model-slug>.json`` whose
   ``audio_sha256`` and ``model`` match is returned as is: no request, no ffmpeg run;
6. the file is probed, its silences found and its windows planned
   (:mod:`~culture_rules.node.actions.audio_split`, at most 480 s each);
7. each window whose reply is not already in the work directory
   (:class:`~culture_rules.node.actions.transcript_file.WorkDir`) is extracted as 16 kHz
   mono WAV into a temporary directory and POSTed as multipart (``file`` =
   ``window-NNNN.wav``, ``audio/wav``; ``language`` when given) to
   ``<base_url>/v1/audio/transcriptions``; the reply (``{text}`` or ``{text, segments}``)
   is validated and kept in the work directory;
8. the result document is built, written atomically, and the work directory removed.

A failed window leaves the earlier windows' replies on disk, so the engine's retry sends
only the windows still missing. The port never loops: retrying is the engine's job. Each
request's timeout is the attempt's remaining time, capped at :data:`MAX_TIMEOUT`.

Outcome: completed ``{result_path, result_sha256, audio_sha256, model, language,
segment_count, window_count, empty_window_count}`` - never the transcript text. Failures
(``"<code>: <detail>"``): ``stt_unavailable`` (the only retryable one: connection error,
timeout, 5xx, 408, 429 or a passed attempt deadline), ``stt_rejected`` (any other non-2xx,
including 413), ``audio_undecodable`` (ffprobe/ffmpeg failed, or a 400 whose body says
``invalid_audio``), ``stt_bad_response``, ``path_refused``, ``audio_missing`` (final: the
audio file does not exist; the message names its path relative to ``audio_root``),
``invalid_request``,
``audio_mismatch``, ``ffmpeg_missing``, ``model_taken``, ``stt_actor_invalid``,
``actor_kind_mismatch`` and ``secret_unresolved``.

Standard-library only.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from culture_rules.actors.model_rule import senior_holder
from culture_rules.actors.secrets import SecretError, is_secret_ref, resolve
from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.model.actor import Actor
from culture_rules.node.actions import audio_split, transcript_file
from culture_rules.node.actions._ledger import DEFAULT_LEDGER_SIZE, ReplayLedger
from culture_rules.node.actions.library import encode_multipart
from culture_rules.node.actions.llm import _base_url_problem, _opener
from culture_rules.node.actions.machine import load_bound_actor
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = [
    "MAX_RESPONSE_BYTES",
    "MAX_TIMEOUT",
    "AudioTranscribePort",
    "Transport",
    "check_stt_actor",
    "urllib_transport",
]

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TIMEOUT = 900.0
HASH_CHUNK = 1024 * 1024
TRANSCRIPTIONS_PATH = "/audio/transcriptions"
LEDGER_SIZE = DEFAULT_LEDGER_SIZE

UNAVAILABLE = "stt_unavailable"
REJECTED = "stt_rejected"
BAD_RESPONSE = "stt_bad_response"
UNDECODABLE = "audio_undecodable"
PATH_REFUSED = "path_refused"
INVALID = "invalid_request"
MISMATCH = "audio_mismatch"
AUDIO_MISSING = "audio_missing"
FFMPEG_MISSING = "ffmpeg_missing"
ACTOR_INVALID = "stt_actor_invalid"

_RETRYABLE_4XX = frozenset({408, 429})
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_-]{1,35}$")
_MODEL = "model"
_REQUIRED = ("audio_path", "output_dir")

Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, bytes]]
"""``(url, body, headers, timeout) -> (status, body)``; raises OSError /
``http.client.HTTPException`` when the server cannot be reached."""


class _Refused(Exception):
    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}")
        self.retryable = retryable

    def result(self) -> InvocationResult:
        return InvocationResult.failed(str(self), retryable=self.retryable)


# ------------------------------------------------------------------ the default transport


def urllib_transport(
    url: str, data: bytes, headers: Mapping[str, str], timeout: float
) -> tuple[int, bytes]:
    """POST ``data`` to ``url`` with urllib: no environment proxies, no redirects."""
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        response = _opener().open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc  # a non-2xx answer is still an answer
    with response:
        status = int(getattr(response, "status", None) or response.code)
        return status, response.read(MAX_RESPONSE_BYTES + 1)


# ------------------------------------------------------------------ actor and input checks


def _root_problem(name: str, value: Any) -> str | None:
    if value in (None, ""):
        return None  # no root: every path is refused at call time
    if not isinstance(value, str) or not os.path.isabs(value):
        return f"params.{name} must be an absolute path"
    return None


def check_stt_actor(actor: Actor) -> str | None:
    """Why ``actor`` cannot serve ``audio.transcribe`` (None when it can)."""
    if actor.kind != "agent":
        return f"audio.transcribe needs an agent actor, not {actor.kind!r}"
    params = actor.params
    problem = _base_url_problem(params.get("base_url"))
    if problem:
        return problem
    model = params.get(_MODEL) or actor.model
    if not isinstance(model, str) or not model.strip():
        return "params.model must be a non-empty string"
    key = params.get("api_key")
    if key not in (None, "") and not is_secret_ref(key):
        return "params.api_key must be a 'grant:<NAME>' reference"
    for name in ("audio_root", "output_root"):
        problem = _root_problem(name, params.get(name))
        if problem:
            return problem
    return None


def _transcriptions_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    suffix = TRANSCRIPTIONS_PATH if base.endswith("/v1") else "/v1" + TRANSCRIPTIONS_PATH
    return base + suffix


def _check_input(input: Mapping[str, Any]) -> None:
    for name in _REQUIRED:
        value = input.get(name)
        if not isinstance(value, str) or not value:
            raise _Refused(INVALID, f"{name} is required")
    sha = input.get("audio_sha256")
    if sha is not None and not (isinstance(sha, str) and _SHA_RE.match(sha)):
        raise _Refused(INVALID, "audio_sha256 must be 64 lowercase hex characters")
    language = input.get("language")
    if language not in (None, "") and not (
        isinstance(language, str) and _LANGUAGE_RE.match(language)
    ):
        raise _Refused(INVALID, "language must be a short language code")
    model = input.get(_MODEL)
    if model is not None and not (isinstance(model, str) and model.strip()):
        raise _Refused(INVALID, "model must be a non-empty string")


def _root(actor: Actor, name: str) -> Path:
    value = actor.params.get(name)
    if not isinstance(value, str) or not value:
        raise _Refused(PATH_REFUSED, f"actor {actor.id!r} has no {name}")
    try:
        return Path(value).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _Refused(PATH_REFUSED, f"{name} does not exist") from exc


def _audio_file(actor: Actor, audio_path: str) -> Path:
    root = _root(actor, "audio_root")
    if not os.path.isabs(audio_path):
        raise _Refused(PATH_REFUSED, "audio_path must be absolute")
    try:
        path = Path(audio_path).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise _Refused(PATH_REFUSED, "audio_path cannot be resolved") from exc
    if not path.is_relative_to(root):
        raise _Refused(PATH_REFUSED, "audio_path is outside the actor's audio_root")
    if not path.exists():
        raise _Refused(
            AUDIO_MISSING, f"audio file {path.relative_to(root).as_posix()!r} is missing"
        )
    if not path.is_file():
        raise _Refused(PATH_REFUSED, "audio_path is not a regular file")
    return path


def _output_dir(actor: Actor, output_dir: str, audio_sha256: str) -> Path:
    root = _root(actor, "output_root")
    if not os.path.isabs(output_dir):
        raise _Refused(PATH_REFUSED, "output_dir must be absolute")
    path = Path(output_dir).resolve(strict=False)  # existing parts' symlinks are followed
    target = (path / audio_sha256).resolve(strict=False)
    if not (path.is_relative_to(root) and target.is_relative_to(root)):
        raise _Refused(PATH_REFUSED, "output_dir is outside the actor's output_root")
    return path


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ the response


def _status_failure(status: int, raw: bytes) -> _Refused:
    if status >= 500 or status in _RETRYABLE_4XX:
        return _Refused(UNAVAILABLE, f"server answered {status}", retryable=True)
    if status == 400 and b"invalid_audio" in raw[:65536]:
        return _Refused(UNDECODABLE, "the server could not decode the audio (400)")
    return _Refused(REJECTED, f"server answered {status}")


def _parse(raw: bytes, start: float, end: float) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _Refused(BAD_RESPONSE, "response too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:  # UnicodeDecodeError is a ValueError
        raise _Refused(BAD_RESPONSE, "response is not JSON") from exc
    if not isinstance(data, dict):
        raise _Refused(BAD_RESPONSE, "response is not an object")
    reply = {k: data[k] for k in ("text", "segments") if k in data and data[k] is not None}
    try:  # the same validation the result builder applies, before the reply is kept
        transcript_file.build_result("0" * 64, "m", None, end - start, [(start, end, reply)])
    except transcript_file.BadReply as exc:
        raise _Refused(BAD_RESPONSE, str(exc)) from exc
    return reply


def _outputs(doc: Mapping[str, Any], path: str) -> dict[str, Any]:
    segments = doc.get("segments")
    return {
        "result_path": path,
        "result_sha256": _hash_file(path),
        "audio_sha256": doc.get("audio_sha256"),
        _MODEL: doc.get(_MODEL),
        "language": doc.get("language"),
        "segment_count": len(segments) if isinstance(segments, list) else 0,
        "window_count": doc.get("window_count"),
        "empty_window_count": doc.get("empty_window_count"),
    }


# ------------------------------------------------------------------ the port


class AudioTranscribePort:
    """ActorPort for ``audio.transcribe`` (see the module docstring)."""

    # a transcription writes only its own result file, so repeating one is safe; a completed
    # key replays from this process's ledger, and a repeat after that finds the file (no-op)
    supports_idempotency_key = True

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secret_runner: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport or urllib_transport
        self._secret_runner = secret_runner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ledger = ReplayLedger(LEDGER_SIZE)

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        replayed = self.replay(idempotency_key)
        if replayed is not None:
            return replayed
        refused = self.refuse_before_admit(input)
        if refused is not None:
            return refused
        actor, doc, failure = load_bound_actor(self._store, input, context)
        if failure is not None or actor is None:
            return failure or InvocationResult.failed("actor_not_found", retryable=False)
        try:
            result = self._transcribe(input, actor, doc, deadline)
        except _Refused as exc:
            return exc.result()
        if result.outcome == COMPLETED:
            self._ledger.put(idempotency_key, result)
        return result

    def replay(self, idempotency_key: str) -> InvocationResult | None:
        """The completed result already recorded for ``idempotency_key``, else None."""
        return self._ledger.get(idempotency_key)

    def refuse_before_admit(self, input: Mapping[str, Any]) -> InvocationResult | None:
        """``invalid_request`` for input that can never run (asked before a slot is taken)."""
        try:
            _check_input(input)
        except _Refused as exc:
            return exc.result()
        return None

    # -------------------------------------------------------------- checks

    def _model_taken(self, actor: Actor, doc: Mapping[str, Any] | None) -> None:
        candidate = dict(doc or {})
        candidate.setdefault("id", actor.id)
        candidate.setdefault("kind", actor.kind)
        candidate.setdefault(_MODEL, actor.model)
        holder = senior_holder(self._store.find(ACTORS_COLLECTION, {"kind": "agent"}), candidate)
        if holder is not None:
            raise _Refused("model_taken", str(holder))

    def _check_actor(self, actor: Actor, doc: Mapping[str, Any] | None) -> None:
        if actor.kind != "agent":
            raise _Refused(
                "actor_kind_mismatch",
                f"audio.transcribe needs an agent actor, {actor.id!r} is {actor.kind!r}",
            )
        problem = check_stt_actor(actor)
        if problem:
            raise _Refused(ACTOR_INVALID, f"{actor.id!r}: {problem}")
        self._model_taken(actor, doc)

    def _headers(self, params: Mapping[str, Any], content_type: str) -> dict[str, str]:
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        key = params.get("api_key")
        if key:
            try:
                headers["Authorization"] = "Bearer " + resolve(key, self._secret_runner)
            except SecretError as exc:
                raise _Refused("secret_unresolved", "params.api_key") from exc
        return headers

    def _remaining(self, deadline: datetime) -> float:
        timeout = (deadline - self._clock()).total_seconds()
        if timeout <= 0:
            raise _Refused(UNAVAILABLE, "attempt deadline already passed", retryable=True)
        return min(timeout, MAX_TIMEOUT)

    # -------------------------------------------------------------- the work

    def _transcribe(
        self,
        input: Mapping[str, Any],
        actor: Actor,
        doc: Mapping[str, Any] | None,
        deadline: datetime,
    ) -> InvocationResult:
        self._check_actor(actor, doc)
        given = input.get("audio_sha256")
        model = str(input.get(_MODEL) or actor.params.get(_MODEL) or actor.model).strip()
        language = input.get("language") or None
        audio = _audio_file(actor, input["audio_path"])
        sha = given or _hash_file(str(audio))
        out_dir = str(_output_dir(actor, input["output_dir"], sha))
        try:
            audio_split.require_tools()
        except audio_split.ToolMissing as exc:
            raise _Refused(FFMPEG_MISSING, str(exc)) from exc
        if given and _hash_file(str(audio)) != given:
            raise _Refused(MISMATCH, "the audio file's sha256 differs from audio_sha256")
        target = transcript_file.result_path(out_dir, sha, model)
        existing = transcript_file.read_existing(target)
        if (
            isinstance(existing, dict)
            and existing.get("audio_sha256") == sha
            and existing.get(_MODEL) == model
        ):
            return InvocationResult.completed(_outputs(existing, target))
        try:
            return self._run_windows(actor, audio, sha, model, language, out_dir, target, deadline)
        except audio_split.ToolMissing as exc:
            raise _Refused(FFMPEG_MISSING, str(exc)) from exc
        except audio_split.AudioUndecodable as exc:
            raise _Refused(UNDECODABLE, str(exc)) from exc

    def _run_windows(
        self,
        actor: Actor,
        audio: Path,
        sha: str,
        model: str,
        language: str | None,
        out_dir: str,
        target: str,
        deadline: datetime,
    ) -> InvocationResult:
        duration = audio_split.probe_duration(audio)
        windows = audio_split.plan_windows(duration, audio_split.detect_silences(audio))
        work = transcript_file.WorkDir(out_dir, sha, model)
        url = _transcriptions_url(actor.params["base_url"])
        replies: list[tuple[float, float, Any]] = []
        auth: dict[str, str] | None = None
        with tempfile.TemporaryDirectory(prefix="stt-") as tmp:
            for index, (start, end) in enumerate(windows, start=1):
                reply = work.get(index)
                if reply is None:
                    if auth is None:
                        auth = self._headers(actor.params, "")  # resolved once, when needed
                    reply = self._send(audio, index, start, end, language, url, auth, tmp, deadline)
                    work.put(index, reply)
                replies.append((start, end, reply))
        try:
            result = transcript_file.build_result(sha, model, language, duration, replies)
        except transcript_file.BadReply as exc:
            raise _Refused(BAD_RESPONSE, f"a kept window reply: {exc}") from exc
        transcript_file.write_atomic(target, result)
        work.clear()
        return InvocationResult.completed(_outputs(result, target))

    def _send(
        self,
        audio: Path,
        index: int,
        start: float,
        end: float,
        language: str | None,
        url: str,
        auth: Mapping[str, str],
        tmp: str,
        deadline: datetime,
    ) -> dict[str, Any]:
        name = f"window-{index:04d}.wav"
        wav = audio_split.extract_window(audio, start, end, os.path.join(tmp, name))
        data = wav.read_bytes()
        wav.unlink()
        fields = [("language", language)] if language else []
        content_type, body = encode_multipart(fields, [("file", name, "audio/wav", data)])
        headers = {**auth, "Content-Type": content_type}
        timeout = self._remaining(deadline)
        try:
            status, raw = self._transport(url, body, headers, timeout)
        except (OSError, http.client.HTTPException) as exc:  # URLError/timeouts are OSErrors
            raise _Refused(UNAVAILABLE, type(exc).__name__, retryable=True) from exc
        if not 200 <= status < 300:
            raise _status_failure(status, raw)
        return _parse(raw, start, end)
