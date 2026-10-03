"""The name this machine's engine node reports under (standard library only).

``culture-rules node run`` heartbeats under this name, and ``culture-rules serve`` reports
``/health`` for it, so both must agree. ``CULTURE_RULES_NODE_NAME`` sets it for both (e.g.
``spark`` on a host whose hostname is ``spark-f8a9``); unset, it is the short hostname.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping

__all__ = ["NODE_NAME_ENV", "node_name"]

NODE_NAME_ENV = "CULTURE_RULES_NODE_NAME"


def node_name(env: Mapping[str, str] | None = None) -> str:
    """``CULTURE_RULES_NODE_NAME`` when set (and not blank), else the short hostname."""
    value = ((os.environ if env is None else env).get(NODE_NAME_ENV) or "").strip()
    return value or socket.gethostname().split(".")[0]
