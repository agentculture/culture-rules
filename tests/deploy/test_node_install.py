"""deploy/node/install.sh dry-run: the plan an upgrade would apply (nothing is written)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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


# ------------------------------------------------------------ --gate-run-as (t20 defect 1)

SUDO_PREFIX = "sudo -n -u culture-fixer -- /usr/bin/env PATH=/opt/fixer/bin:/usr/bin:/bin"


def test_without_gate_run_as_the_unit_keeps_no_new_privileges(tmp_path):
    out = plan(tmp_path)
    assert "NoNewPrivileges=true" in out
    assert "CULTURE_RULES_GATE_RUN_AS" not in out


def test_a_sudo_gate_run_as_is_written_and_relaxes_no_new_privileges(tmp_path):
    out = plan(tmp_path, "--gate-run-as", SUDO_PREFIX)
    assert f'CULTURE_RULES_GATE_RUN_AS="{SUDO_PREFIX}"' in out
    assert "NoNewPrivileges=false" in out and "NoNewPrivileges=true" not in out


def test_a_non_sudo_gate_run_as_keeps_no_new_privileges(tmp_path):
    out = plan(tmp_path, "--gate-run-as", "/usr/local/bin/as-fixer --")
    assert 'CULTURE_RULES_GATE_RUN_AS="/usr/local/bin/as-fixer --"' in out
    assert "NoNewPrivileges=true" in out


@pytest.mark.parametrize("bad", ["sudo -n\n-u x", "sudo -n\r", "   "])
def test_a_multi_line_or_blank_gate_run_as_is_refused(tmp_path, bad):
    with pytest.raises(subprocess.CalledProcessError) as exc:
        plan(tmp_path, "--gate-run-as", bad)
    assert "--gate-run-as" in exc.value.stderr


def _stubs(tmp_path: Path) -> Path:
    """uv and systemctl stand-ins: uv venv makes the venv's executables, the rest no-ops."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = venv ]; then\n'
        "  for last; do :; done\n"
        '  mkdir -p "$last/bin"\n'
        '  printf "#!/bin/sh\\n" > "$last/bin/python"\n'
        '  printf "#!/bin/sh\\necho culture-rules 9.9.9\\n" > "$last/bin/culture-rules"\n'
        '  chmod +x "$last/bin/python" "$last/bin/culture-rules"\n'
        "fi\n"
    )
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n")
    for f in (uv, systemctl):
        f.chmod(0o755)
    return bin_dir


def test_apply_writes_the_prefix_so_the_node_reads_it_back_verbatim(tmp_path):
    prefix = SUDO_PREFIX + ' odd\\word "q" $HOME `x`'
    home = tmp_path / "home"
    home.mkdir()
    wheel = tmp_path / "culture_rules-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"")
    subprocess.run(  # nosec B603 B607 - fixed argv, test only
        ["bash", str(SCRIPT), "--node-name", "n1", "--wheel", str(wheel)]
        + ["--gate-run-as", prefix, "--apply"],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": f"{_stubs(tmp_path)}:/usr/bin:/bin"},
        check=True,
    )
    env_file = home / ".config" / "culture-rules" / "node.env"
    unit = (home / ".config" / "systemd" / "user" / "culture-rules-node.service").read_text()
    assert "NoNewPrivileges=false" in unit and "NoNewPrivileges=true" not in unit
    # systemd's EnvironmentFile double quotes unescape \\ \" \$ \` exactly as POSIX sh does.
    read_back = subprocess.run(  # nosec B603 B607 - fixed argv, test only
        ["sh", "-c", '. "$1"; printf %s "$CULTURE_RULES_GATE_RUN_AS"', "sh", str(env_file)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nowhere"},
        check=True,
    )
    assert read_back.stdout == prefix
