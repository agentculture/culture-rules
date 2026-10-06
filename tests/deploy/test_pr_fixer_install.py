"""deploy/pr-fixer: the fixer-user and bridge installers' dry-run plans (t18).

Nothing here touches the host: both scripts are dry-run by default, and the
tests run them with a throwaway HOME.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "pr-fixer"
INSTALL = DEPLOY / "install.sh"
CREATE_USER = DEPLOY / "create-user.sh"
BOT = "rules-culture-dev[bot] <337624453+rules-culture-dev[bot]@users.noreply.github.com>"


def run(tmp_path: Path, script: Path, *args: str, check: bool = True):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(  # nosec B603 B607 - fixed argv, test only
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "USER": "culture-fixer"},
        check=check,
    )


def plan(tmp_path: Path, *args: str) -> str:
    return run(tmp_path, INSTALL, "--host", "100.64.0.9", *args).stdout


def config_from(out: str, backend: str) -> dict:
    """The JSON config block the plan prints for one bridge."""
    marker = f"--- {backend}.json ---\n"
    body = out.split(marker, 1)[1].split("\n---", 1)[0]
    return json.loads(body)


def test_dry_run_writes_nothing(tmp_path):
    out = plan(tmp_path)
    assert "Dry run: nothing was written" in out
    assert not (tmp_path / "home" / ".config").exists()


def test_the_qwen_bridge_binds_the_tailnet_host_and_authors_as_the_app_bot(tmp_path):
    cfg = config_from(plan(tmp_path), "qwen")
    assert cfg["host"] == "100.64.0.9"
    assert cfg["port"] == 8093
    assert cfg["commit_author"] == BOT
    assert cfg["repo_allowlist_prefixes"] == ["https://github.com/agentculture/"]
    assert cfg["max_concurrent"] == 1


def test_only_the_qwen_bridge_is_planned_on_the_fixer_machine(tmp_path):
    """d8: Codex stays on spark; the fixer machine runs only the qwen bridge."""
    assert "codex" not in plan(tmp_path).lower()


def test_no_token_is_ever_written_to_the_config(tmp_path):
    assert "auth_token" not in config_from(plan(tmp_path), "qwen")


def test_every_token_and_the_model_key_are_injected_by_grant(tmp_path):
    out = plan(tmp_path)
    assert "--inject QWEN_BRIDGE_AUTH_TOKEN=FIXER_QWEN_BRIDGE_TOKEN" in out
    assert "--inject QWEN_CUSTOM_API_KEY_CORTEX=FIXER_CORTEX_API_KEY" in out
    assert "--inject GH_TOKEN=FIXER_GITHUB_TOKEN" in out
    assert "--inject SONAR_TOKEN=FIXER_SONAR_TOKEN" in out


def test_the_qwen_bridge_pins_a_handshake_approved_qwen_and_cortex(tmp_path):
    cfg = config_from(plan(tmp_path), "qwen")
    assert cfg["default_model"] == "cortex"
    assert cfg["qwen_agent_versions"] == ["0.24.7"]


def test_a_wildcard_empty_or_malformed_host_is_refused(tmp_path):
    for host in ("0.0.0.0", "", "spark2 ; rm -rf /"):
        done = run(tmp_path, INSTALL, "--host", host, check=False)
        assert done.returncode == 1, host


def test_create_user_is_a_dry_run_without_apply(tmp_path):
    out = run(tmp_path, CREATE_USER).stdout
    assert "useradd" in out and "culture-fixer" in out
    assert "enable-linger culture-fixer" in out
    assert "Dry run: nothing was changed" in out


def test_create_user_authorizes_exactly_one_public_key(tmp_path):
    key = tmp_path / "id.pub"
    key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB4 spark@spark\n")
    out = run(tmp_path, CREATE_USER, "--authorize-key", str(key)).stdout
    assert f"authorize {key}" in out
    key.write_text("not a key\n")
    assert run(tmp_path, CREATE_USER, "--authorize-key", str(key), check=False).returncode == 1
