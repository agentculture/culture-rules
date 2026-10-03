"""Without extras the core keeps zero runtime deps: the server package never imports fastapi."""

from __future__ import annotations

import subprocess
import sys

SNIPPET = """
import sys
sys.modules['fastapi'] = None
sys.modules['uvicorn'] = None
sys.modules['starlette'] = None
import culture_rules.server
import culture_rules.server.serve
import culture_rules.server.service
import culture_rules.server.events
print('ok')
"""


def test_server_package_imports_without_fastapi():
    done = subprocess.run([sys.executable, "-c", SNIPPET], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok", done.stderr


def test_serve_reports_missing_extra_cleanly():
    code = SNIPPET.replace("print('ok')", "") + (
        "from culture_rules.server.serve import serve, ServerExtraMissing\n"
        "from culture_rules.store.memory import MemoryStore\n"
        "try:\n    serve(MemoryStore())\nexcept ServerExtraMissing as e:\n    print('missing', e)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.stdout.startswith("missing"), done.stdout + done.stderr
    assert "culture-rules[server]" in done.stdout
