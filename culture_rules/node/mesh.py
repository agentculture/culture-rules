"""Posting to a Culture mesh channel (shared by run reports and the message action)."""

from __future__ import annotations

import subprocess  # nosec B404 - argv list only, never a shell
from collections.abc import Callable
from typing import Any

__all__ = ["MESH_POST_TIMEOUT_S", "MeshPoster"]

MESH_POST_TIMEOUT_S = 15.0


class MeshPoster:
    """Posts to a Culture mesh channel with ``culture channel message <channel> <text>``."""

    def __init__(
        self,
        executable: str,
        *,
        run: Callable[..., Any] | None = None,
        timeout: float = MESH_POST_TIMEOUT_S,
    ) -> None:
        self._executable = executable
        self._run = run or subprocess.run
        self._timeout = timeout

    def post(self, channel: str, text: str) -> None:
        argv = [self._executable, "channel", "message", channel, text]
        self._run(  # nosec B603 - fixed argv list, shell=False
            argv, check=True, capture_output=True, text=True, timeout=self._timeout
        )
