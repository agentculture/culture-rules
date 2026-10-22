"""Audio splitting helpers: probe a file, find silences, plan windows, extract WAV windows.

Everything shells out to ``ffprobe`` / ``ffmpeg`` with argv lists (never a shell) and a timeout.
:func:`plan_windows` is pure: it cuts ``0..duration`` into contiguous, non-overlapping windows
of at most ``max_s`` seconds, preferring to cut in the middle of the latest silence that begins
within the last ``search_s`` seconds of a window, else exactly at ``max_s``.

Missing tools raise :class:`ToolMissing` (the port maps it to ``ffmpeg_missing``); a non-zero
exit or unparsable output raises :class:`AudioUndecodable` (``audio_undecodable``). A 480 s
window as 16 kHz mono PCM16 WAV is about 15.4 MB, under 16 MiB.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404
from pathlib import Path

MAX_WINDOW_S = 480.0
SEARCH_S = 60.0
SAMPLE_RATE = 16000
PROBE_TIMEOUT_S = 60
DETECT_TIMEOUT_S = 900
EXTRACT_TIMEOUT_S = 300

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


class ToolMissing(RuntimeError):
    """ffmpeg or ffprobe is not on PATH."""


class AudioUndecodable(RuntimeError):
    """ffprobe/ffmpeg failed on the audio or produced output we cannot parse."""


def require_tools() -> None:
    """Raise :class:`ToolMissing` naming the first of ``ffmpeg``, ``ffprobe`` not on PATH."""
    for name in ("ffmpeg", "ffprobe"):
        if shutil.which(name) is None:
            raise ToolMissing(f"{name} not found on PATH")


def _run(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(  # nosec B603
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise ToolMissing(f"{argv[0]} not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioUndecodable(f"{argv[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise AudioUndecodable(f"{argv[0]} exited {proc.returncode}: {tail[0]}")
    return proc


def probe_duration(path: str | Path) -> float:
    """Return the audio duration in seconds via ffprobe."""
    proc = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        PROBE_TIMEOUT_S,
    )
    try:
        value = float(proc.stdout.strip())
    except ValueError as exc:
        raise AudioUndecodable("ffprobe gave no parsable duration") from exc
    if not value > 0:
        raise AudioUndecodable("audio has no duration")
    return value


def detect_silences(
    path: str | Path, noise_db: int = -35, min_s: float = 0.5
) -> list[tuple[float, float]]:
    """Return ``(start, end)`` silence spans from ffmpeg ``silencedetect`` (parsed from stderr).

    A trailing silence with no end yields ``(start, start)``.
    """
    proc = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_s}",
            "-f",
            "null",
            "-",
        ],
        DETECT_TIMEOUT_S,
    )
    spans: list[tuple[float, float]] = []
    start: float | None = None
    for line in proc.stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            if start is not None:
                spans.append((start, start))
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            spans.append((start, max(start, float(m.group(1)))))
            start = None
    if start is not None:
        spans.append((start, start))
    return spans


def plan_windows(
    duration: float,
    silences: list[tuple[float, float]],
    max_s: float = MAX_WINDOW_S,
    search_s: float = SEARCH_S,
) -> list[tuple[float, float]]:
    """Cut ``0..duration`` into contiguous windows of at most ``max_s`` seconds.

    Each cut is the middle of the latest silence that begins within the last ``search_s``
    seconds of the window (clamped to the window limit), else the limit itself.
    """
    windows: list[tuple[float, float]] = []
    start = 0.0
    while duration - start > max_s:
        limit = start + max_s
        lo = max(start, limit - search_s)
        cut = limit
        for s_start, s_end in sorted(silences, reverse=True):
            if lo <= s_start <= limit:
                mid = min((s_start + s_end) / 2, limit)
                if mid > start:
                    cut = mid
                    break
        windows.append((start, cut))
        start = cut
    windows.append((start, duration))
    return windows


def extract_window(path: str | Path, start: float, end: float, dest: str | Path) -> Path:
    """Write ``start..end`` seconds of ``path`` to ``dest`` as 16 kHz mono PCM16 WAV."""
    dest = Path(dest)
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{end - start:.3f}",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ],
        EXTRACT_TIMEOUT_S,
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise AudioUndecodable("ffmpeg wrote no audio")
    return dest
