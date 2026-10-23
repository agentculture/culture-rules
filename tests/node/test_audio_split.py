"""Tests for the audio split module (ffprobe duration, silence-aware windows, WAV extract)."""

from __future__ import annotations

import shutil
import subprocess
import wave

import pytest

from culture_rules.node.actions import audio_split as a

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


def _check_windows(windows, duration, max_s=480):
    assert windows[0][0] == 0
    assert abs(windows[-1][1] - duration) < 0.1
    for (s, e), nxt in zip(windows, windows[1:] + [None]):
        assert 0 < e - s <= max_s
        if nxt:
            assert abs(nxt[0] - e) < 0.1


def test_plan_windows_short_is_single():
    assert a.plan_windows(100, []) == [(0, 100)]


def test_plan_windows_no_silence_cuts_at_max():
    w = a.plan_windows(1500, [])
    _check_windows(w, 1500)
    assert w[0] == (0, 480)
    assert w[1] == (480, 960)


def test_plan_windows_cuts_in_silence_in_last_search_window():
    sil = [(100.0, 101.0), (440.0, 442.0), (470.0, 472.0), (900.0, 901.0)]
    w = a.plan_windows(1500, sil)
    _check_windows(w, 1500)
    assert w[0][1] == pytest.approx(471.0)  # latest silence inside 420..480
    assert 900 <= w[1][1] <= 901 or w[1][1] - w[1][0] <= 480


def test_plan_windows_ignores_silence_outside_search_range():
    w = a.plan_windows(1500, [(300.0, 302.0)])
    assert w[0][1] == 480
    _check_windows(w, 1500)


def test_plan_windows_silence_straddling_limit_is_clamped():
    w = a.plan_windows(1000, [(470.0, 500.0)])
    assert 470 <= w[0][1] <= 480
    _check_windows(w, 1000)


def test_require_tools_missing_names_ffmpeg(monkeypatch):
    monkeypatch.setattr(a.shutil, "which", lambda name: None)
    with pytest.raises(a.ToolMissing) as ei:
        a.require_tools()
    assert "ffmpeg" in str(ei.value)


def test_require_tools_names_ffprobe(monkeypatch):
    monkeypatch.setattr(a.shutil, "which", lambda n: None if n == "ffprobe" else "/x/" + n)
    with pytest.raises(a.ToolMissing, match="ffprobe"):
        a.require_tools()


@pytest.fixture
def gapped(tmp_path):
    """20 s of sine with a 2 s silence from 8 s to 10 s."""
    out = tmp_path / "in.wav"
    subprocess.run(  # nosec B603
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-filter_complex",
            "[0][1][2]concat=n=3:v=0:a=1",
            str(out),
        ],
        check=True,
        timeout=60,
    )
    return out


@needs_ffmpeg
def test_probe_and_silences(gapped):
    assert a.probe_duration(gapped) == pytest.approx(20.0, abs=0.2)
    sil = a.detect_silences(gapped)
    assert len(sil) == 1
    assert sil[0][0] == pytest.approx(8.0, abs=0.2)
    assert sil[0][1] == pytest.approx(10.0, abs=0.2)


@needs_ffmpeg
def test_extract_window_is_16k_mono_pcm16(gapped, tmp_path):
    dest = tmp_path / "w.wav"
    a.extract_window(gapped, 2, 12, dest)
    with wave.open(str(dest)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert w.getnframes() / 16000 == pytest.approx(10.0, abs=0.1)


@needs_ffmpeg
def test_480s_window_under_16_mib(tmp_path):
    src = tmp_path / "long.wav"
    subprocess.run(  # nosec B603
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=500",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(src),
        ],
        check=True,
        timeout=120,
    )
    dest = tmp_path / "w.wav"
    a.extract_window(src, 0, 480, dest)
    assert dest.stat().st_size < 16 * 1024 * 1024


@needs_ffmpeg
def test_undecodable_raises(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(a.AudioUndecodable):
        a.probe_duration(bad)
    with pytest.raises(a.AudioUndecodable):
        a.extract_window(bad, 0, 5, tmp_path / "o.wav")
