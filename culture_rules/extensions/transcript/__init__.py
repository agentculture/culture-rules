"""The ``transcript`` extension: ``transcript.render``, one transcript as an EPUB or Markdown.

The port is :class:`culture_rules.extensions.transcript.port.TranscriptRenderPort`; the
transcript file format it reads is core's (:mod:`culture_rules.node.actions.transcript_file`,
shared with ``llm.complete`` and ``audio.transcribe``). Registering imports nothing but the
kind's typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import REQ_STR, STR, ParamSpec, kind

__all__ = ["KINDS", "register"]

KINDS = (
    kind(
        "transcript.render",
        "Render one transcript and its summary as an EPUB or Markdown (right to left for Hebrew)",
        label="Make a book or Markdown from a transcript",
        actors=({"has_any": ["transcripts_root", "output_root"]},),
        actorless=True,
        actor=STR,  # optional: its transcripts_root/output_root is a second boundary
        result_path=REQ_STR,
        root=REQ_STR,  # the transcripts root: result_path must resolve under it
        recorded_at=REQ_STR,  # ISO-8601 with a zone; dated in Asia/Jerusalem
        language=STR,
        summary=ParamSpec("any"),  # llm.complete's output: {text} or a string, or none
        format=ParamSpec(
            "str",
            choices=(("epub", "EPUB book"), ("md", "Markdown")),
            choose="EPUB book (default)",
        ),  # epub (default) or md: summary, key facts, then the transcript
    ),
)


def register(ext: Extension) -> None:
    """``transcript.render`` (``port.TranscriptRenderPort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_phrases("transcript.render", words=(("result_path", "of {}"),))
    port = Lazy(__name__ + ".port", "TranscriptRenderPort", store=True)
    ext.action_port("transcript.render", port)
