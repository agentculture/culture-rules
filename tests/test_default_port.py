"""One default API address, read everywhere (culture-rules#141)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from culture_rules import defaults
from culture_rules.client.http import DEFAULT_API_URL as CLIENT_DEFAULT
from culture_rules.server import serve

ROOT = Path(__file__).resolve().parent.parent


def test_the_default_port_is_productions_lan_port():
    assert defaults.DEFAULT_PORT == 8791


def test_the_default_url_is_built_from_host_and_port():
    assert defaults.DEFAULT_API_URL == "http://127.0.0.1:8791"


def test_the_server_binds_the_shared_default():
    assert serve.DEFAULT_PORT is defaults.DEFAULT_PORT


def test_the_client_talks_to_the_shared_default():
    assert CLIENT_DEFAULT is defaults.DEFAULT_API_URL


def test_the_web_dev_proxy_uses_the_same_default():
    text = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    match = re.search(r'CULTURE_RULES_API_URL \?\? "([^"]+)"', text)
    assert match is not None
    assert match.group(1) == defaults.DEFAULT_API_URL


#: Files allowed to name the old port: history (CHANGELOG) and the module that explains the move.
_MAY_NAME_OLD = ("CHANGELOG.md", "culture_rules/defaults.py")
_OLD_PORT = re.compile(r"(?<![\d.])8765(?!\d)")


def _committed_text_files_outside_tests():
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for rel in out:
        if rel.startswith("tests/") or rel in _MAY_NAME_OLD:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            yield rel, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary


def test_no_committed_file_states_the_old_port():
    stale = [rel for rel, text in _committed_text_files_outside_tests() if _OLD_PORT.search(text)]
    assert stale == []


def test_the_serve_explain_entry_states_the_shared_port():
    from culture_rules.explain.catalog import _SERVE

    assert f"or {defaults.DEFAULT_PORT})" in _SERVE
