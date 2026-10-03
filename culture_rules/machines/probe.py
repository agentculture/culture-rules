"""Platform probe: which load/GPU tools exist here. Never fatal; argv lists, short timeouts."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = ["PLATFORM_TOOLS", "PROBE_TIMEOUT_S", "ProbeResult", "probe_platform", "probe_usb"]

PLATFORM_TOOLS: tuple[str, ...] = ("nvidia-smi", "tegrastats")
PROBE_TIMEOUT_S = 5.0

Which = Callable[[str], str | None]
Run = Callable[..., Any]


@dataclass(frozen=True)
class ProbeResult:
    """Which tools exist (``tools``) and the GPU utilisation fraction if readable."""

    tools: dict[str, bool] = field(default_factory=dict)
    gpu_load: float | None = None


def _run(run: Run, argv: list[str]) -> str | None:
    """Run ``argv`` (never a shell) with a short timeout; None on any failure."""
    try:
        done = run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, check=False)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    return done.stdout if getattr(done, "returncode", 1) == 0 else None


def _gpu_load(run: Run) -> float | None:
    out = _run(
        run,
        ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
    )
    if not out or not out.strip():
        return None
    try:
        values = [float(line) for line in out.split() if line.strip()]
    except ValueError:
        return None
    return max(values) / 100.0 if values else None


def probe_platform(which: Which = shutil.which, run: Run = subprocess.run) -> ProbeResult:
    """Record which of :data:`PLATFORM_TOOLS` exist; read GPU load when nvidia-smi does."""
    tools = {tool: which(tool) is not None for tool in PLATFORM_TOOLS}
    gpu = _gpu_load(run) if tools["nvidia-smi"] else None
    return ProbeResult(tools=tools, gpu_load=gpu)


def probe_usb(
    ids: Iterable[str], which: Which = shutil.which, run: Run = subprocess.run
) -> dict[str, bool]:
    """Probe USB-attached requirements (``vid:pid``); only runs when ids are requested."""
    wanted: Sequence[str] = [i.lower() for i in ids]
    if not wanted:
        return {}
    listing = _run(run, ["lsusb"]) if which("lsusb") else None
    text = (listing or "").lower()
    return {i: f"id {i}" in text for i in wanted}


def read_load() -> dict[str, float]:
    """Best-effort cpu (1-min load per core) and mem (used fraction) readings."""
    load: dict[str, float] = {}
    try:
        load["cpu"] = round(os.getloadavg()[0] / (os.cpu_count() or 1), 4)
    except OSError:
        pass
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            info = {k: int(v.split()[0]) for k, v in (ln.split(":", 1) for ln in handle)}
        load["mem"] = round(1 - info["MemAvailable"] / info["MemTotal"], 4)
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        pass
    return load
