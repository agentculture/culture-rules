"""The ``stt`` extension: ``audio.transcribe`` on a speech-to-text endpoint of an agent actor.

The port is :class:`culture_rules.extensions.stt.port.AudioTranscribePort`; ffmpeg cuts the
audio into windows at silences (:mod:`culture_rules.extensions.stt.audio_split`).
Registering imports nothing but the kind's typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import REQ_STR, STR, kind

__all__ = ["KINDS", "register"]

KINDS = (
    kind(
        "audio.transcribe",
        "Transcribe an audio file with a speech-to-text endpoint on an agent actor",
        label="Transcribe an audio file",
        actors=({"kinds": ["agent"], "has": ["base_url"]},),
        actor=REQ_STR,
        audio_path=REQ_STR,
        audio_sha256=STR,  # optional: without it the port hashes the file itself
        output_dir=REQ_STR,
        language=STR,
        model=STR,
    ),
)


def register(ext: Extension) -> None:
    """``audio.transcribe`` (``port.AudioTranscribePort``)."""
    for k in KINDS:
        ext.action_kind(k)
    port = Lazy(__name__ + ".port", "AudioTranscribePort", store=True)
    ext.action_port("audio.transcribe", port)
