"""t37: cloudflared deploy templates and the rules.culture.dev runbook (static checks)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "cloudflared"
DOC = ROOT / "docs" / "operations" / "rules-culture-dev.md"
UNIT = DEPLOY / "cloudflared-rules.service"
ENV = DEPLOY / "cloudflared-rules.env.example"
HOSTNAME = "rules.culture.dev"
PLACEHOLDER = re.compile(r"@[A-Z][A-Z0-9_]*@")


def _render(text: str) -> str:
    values = {"TUNNEL_TOKEN_SECRET": "RULES_CULTURE_DEV_TUNNEL_TOKEN"}
    return PLACEHOLDER.sub(lambda m: values.get(m.group(0).strip("@"), "X"), text)


def test_templates_exist() -> None:
    assert UNIT.is_file()
    assert ENV.is_file()
    assert DOC.is_file()


def test_unit_renders_with_no_leftover_placeholders() -> None:
    rendered = _render(UNIT.read_text())
    assert not PLACEHOLDER.search(rendered)
    assert "[Service]" in rendered
    assert "[Install]" in rendered


def test_unit_seals_token_via_grant_run_inject() -> None:
    rendered = _render(UNIT.read_text())
    assert "grant run --inject TUNNEL_TOKEN=RULES_CULTURE_DEV_TUNNEL_TOKEN" in rendered
    assert "shushu" not in rendered.lower()
    assert "--token" not in rendered  # the token never rides argv
    assert "TUNNEL_TOKEN_FILE" not in rendered
    assert "cloudflared tunnel --no-autoupdate run" in rendered


def test_origin_is_the_loopback_access_listener_only() -> None:
    text = UNIT.read_text() + ENV.read_text() + DOC.read_text()
    assert "CULTURE_RULES_ACCESS_LISTEN=127.0.0.1:" in ENV.read_text()
    assert "CULTURE_RULES_ACCESS_TEAM_DOMAIN=" in ENV.read_text()
    assert "CULTURE_RULES_ACCESS_AUD=" in ENV.read_text()
    assert "--service http://127.0.0.1:" in DOC.read_text()
    # the origin must never be a non-loopback address
    for m in re.finditer(r"--service\s+(\S+)", text):
        assert m.group(1).startswith("http://127.0.0.1:"), m.group(1)


def test_templates_contain_no_secret_material() -> None:
    for path in (UNIT, ENV, DOC):
        text = path.read_text()
        assert not re.search(r"eyJ[A-Za-z0-9_-]{20,}", text), path  # JWT / tunnel token blob
        assert not re.search(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", text
        )
        assert not re.search(r"\b[0-9a-f]{32,}\b", text), path  # account ids / AUD tags
        assert "CLOUDFLARE_API_TOKEN=" not in text, path


def test_doc_records_dry_run_then_apply_and_id_table() -> None:
    text = DOC.read_text()
    dry = text.index("cultureflare remote-login setup")
    assert "--apply" in text[dry:]
    first_block = text[dry : text.index("```", dry)]
    assert "--apply" not in first_block.replace("--apply` ", "")  # first invocation is a dry run
    assert HOSTNAME in text
    for field in ("tunnel id", "Access app id", "AUD tag", "tunnel name"):
        assert field.lower() in text.lower(), field
    assert "grant set" in text
    assert "grant run --inject" in text
    assert "shushu" not in text.lower()
    assert "302" in text  # unauthenticated request gets the Access redirect
    assert "hand-turn" in text.lower()


def test_doc_covers_every_serving_host() -> None:
    text = DOC.read_text()
    for host in ("spark", "thor", "orin"):
        assert host in text


def test_scan_secrets_clean_on_new_files() -> None:
    rels = [str(p.relative_to(ROOT)) for p in (UNIT, ENV, DOC)]
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scan-secrets.py"), *rels],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
