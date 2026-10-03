"""The served OpenAPI equals the committed api/openapi.json (the single HTTP contract)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.server.contract import render_openapi  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "api" / "openapi.json"


def test_served_openapi_equals_committed_file():
    served = create_app(MemoryStore()).openapi()
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert served == committed, "route/schema drift: run scripts/export-openapi.py --write"
    assert COMMITTED.read_text(encoding="utf-8") == render_openapi(served)


def test_export_script_check_mode_passes():
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export-openapi.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_contract_covers_the_documented_surface():
    paths = json.loads(COMMITTED.read_text(encoding="utf-8"))["paths"]
    for p in (
        "/health",
        "/rules",
        "/rules/{id}",
        "/workflows",
        "/actors",
        "/machines",
        "/runs",
        "/runs/{run_id}/cancel",
        "/controls/pause",
        "/import",
        "/export",
        "/asks",
        "/asks/{ask_id}/answer",
        "/machines/status",
        "/repos",
        "/events/stream",
    ):
        assert p in paths, p
