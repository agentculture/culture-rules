"""deploy/node/install.sh dry-run: the plan an upgrade would apply (nothing is written)."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "node" / "install.sh"


def plan(tmp_path: Path, *args: str) -> str:
    home = tmp_path / "home"
    (home / ".config" / "culture-rules").mkdir(parents=True, exist_ok=True)
    wheel = tmp_path / "culture_rules-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"")
    done = subprocess.run(  # nosec B603 B607 - fixed argv, test only
        ["bash", str(SCRIPT), "--node-name", "n1", "--wheel", str(wheel), *args],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        check=True,
    )
    return done.stdout


def test_an_upgrade_keeps_the_installed_mongo_ca(tmp_path):
    ca = tmp_path / "home" / ".config" / "culture-rules" / "mongo-ca.pem"
    ca.parent.mkdir(parents=True)
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    out = plan(tmp_path)
    assert f"CULTURE_RULES_MONGO_TLS_CA_FILE={ca}" in out


def test_no_ca_anywhere_writes_no_ca_line(tmp_path):
    assert "CULTURE_RULES_MONGO_TLS_CA_FILE" not in plan(tmp_path)


def test_each_secret_is_injected_under_its_culture_rules_variable(tmp_path):
    out = plan(tmp_path, "--secret", "RULES_DISCORD_BOT_TOKEN", "--secret", "team/gh-key.v2")
    assert "--inject CULTURE_RULES_SECRET_RULES_DISCORD_BOT_TOKEN=RULES_DISCORD_BOT_TOKEN" in out
    assert "--inject CULTURE_RULES_SECRET_TEAM_GH_KEY_V2=team/gh-key.v2" in out
    assert "Dry run: nothing was written" in out
