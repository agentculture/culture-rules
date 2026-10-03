"""The committed HTTP contract ``api/openapi.json``: render, write and compare (stdlib only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["CONTRACT_PATH", "build_openapi", "render_openapi"]

CONTRACT_PATH = Path("api") / "openapi.json"


def render_openapi(schema: dict[str, Any]) -> str:
    """Canonical text of the contract: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_openapi() -> dict[str, Any]:
    """Generate the OpenAPI document from the app (needs the ``server`` extra)."""
    from culture_rules.server.app import create_app  # noqa: PLC0415
    from culture_rules.store.memory import MemoryStore  # noqa: PLC0415

    return create_app(MemoryStore(), host="contract").openapi()
