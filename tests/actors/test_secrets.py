"""t19: secret references resolved at run time via grant (deviation d2 replaced shushu)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from culture_rules.actors import secrets
from culture_rules.actors.secrets import (
    Redactor,
    SecretError,
    assert_refs_only,
    grant_run_argv,
    is_secret_ref,
    parse_ref,
    resolve,
    run_with_secrets,
)
from culture_rules.io.bundle import Bundle, SecretRef
from culture_rules.io.exchange import bundle_files, export_bundle
from tests.model.factories import make_actor

VALUE = "s3cr3t-value-XYZ"


def test_parse_ref_accepts_grant_only():
    assert parse_ref("grant:AWS_KEY") == ("grant", "AWS_KEY")
    assert is_secret_ref("grant:AWS_KEY")
    for bad in ("plain-value", "env:X", "shushu:aws/key", "grant:", "grant:a b"):
        assert not is_secret_ref(bad)
        with pytest.raises(SecretError):
            parse_ref(bad)


def test_resolve_uses_grant_get_argv_without_shell(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return SimpleNamespace(stdout=VALUE + "\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve("grant:GH_TOKEN") == VALUE
    argv, kw = calls[0]
    assert argv == ["grant", "get", "GH_TOKEN"]
    assert not kw.get("shell")


def test_resolve_injected_runner_and_failure_wrapped():
    assert resolve("grant:A", runner=lambda name: "v-" + name) == "v-A"

    def boom(argv, **kw):
        raise subprocess.CalledProcessError(1, argv, stderr="hidden secret")

    with pytest.raises(SecretError) as err:
        resolve("grant:HIDDEN", _run=boom)
    assert "grant:HIDDEN" in str(err.value)


def test_resolve_error_never_contains_value():
    def run(argv, **kw):
        raise OSError(f"leaked {VALUE}")

    with pytest.raises(SecretError):
        resolve("grant:X", _run=run)


def test_grant_run_argv_injects_without_values():
    argv = grant_run_argv(["tool", "--flag"], {"TOKEN": "grant:GH", "KEY": "grant:AWS"})
    assert argv[:2] == ["grant", "run"]
    assert argv[argv.index("--") + 1 :] == ["tool", "--flag"]
    assert "--inject" in argv
    assert "TOKEN=GH" in argv and "KEY=AWS" in argv
    with pytest.raises(SecretError):
        grant_run_argv(["x"], {"TOKEN": "literal-value"})
    with pytest.raises(SecretError):
        grant_run_argv(["x"], {"bad name": "grant:A"})


def test_run_with_secrets_never_resolves_in_process():
    seen = []

    def fake(argv, **kw):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    out = run_with_secrets(["tool"], {"T": "grant:GH"}, _run=fake)
    assert out.returncode == 0
    assert seen[0][:2] == ["grant", "run"] and "T=GH" in seen[0]


def test_redactor_masks_resolved_values_and_is_used_by_resolve():
    red = Redactor()
    assert resolve("grant:A", runner=lambda n: VALUE, redactor=red) == VALUE
    assert VALUE not in red.redact(f"token={VALUE} again {VALUE}")
    assert red.redact(f"token={VALUE}") == "token=***"
    assert red.redact({"a": [f"x{VALUE}"], "b": 3}) == {"a": ["x***"], "b": 3}
    assert red.redact("nothing here") == "nothing here"


def test_redactor_ignores_trivially_short_values():
    red = Redactor()
    red.add("a")
    assert red.redact("banana") == "banana"


def test_assert_refs_only_flags_literal_secrets_in_params():
    assert_refs_only({"token": "grant:GH", "url": "http://localhost:1", "n": 3})
    assert_refs_only({"nested": {"api_key": "grant:K"}, "list": [{"password": "grant:P"}]})
    with pytest.raises(SecretError) as err:
        assert_refs_only({"nested": {"api_key": VALUE}})
    assert "nested.api_key" in str(err.value) and VALUE not in str(err.value)
    with pytest.raises(SecretError):
        assert_refs_only({"list": [{"password": "hunter2"}]})


def test_exported_actor_with_secret_yields_only_reference(tmp_path):
    pytest.importorskip("yaml")
    actor = make_actor(params={"api_token": "grant:GH_TOKEN", "channel": "#ops"})
    assert_refs_only(actor.params)
    bundle = Bundle(actors=(actor,), secrets=(SecretRef(name="gh", ref="grant:GH_TOKEN"),))
    red = Redactor()
    resolve("grant:GH_TOKEN", runner=lambda n: VALUE, redactor=red)  # resolved on this host
    files = bundle_files(bundle)
    export_bundle(bundle, tmp_path, apply=True)
    on_disk = "\n".join(p.read_text() for p in tmp_path.rglob("*.yaml"))
    for text in [*files.values(), on_disk]:
        assert "grant:GH_TOKEN" in text
        assert VALUE not in text


def test_secretref_accepts_grant_scheme():
    SecretRef(name="gh", ref="grant:GH_TOKEN").check()
    with pytest.raises(ValueError):
        SecretRef(name="gh", ref=VALUE).check()


def test_backup_uses_shared_resolver():
    from culture_rules.ops import backup

    assert backup.resolve_secret("grant:A", runner=lambda n: "x" + n) == "xA"
    assert backup.resolve_secret("plain") == "plain"
    assert secrets.resolve_or_literal("plain") == "plain"
    with pytest.raises(backup.BackupConfigError):
        backup.resolve_secret("grant:A", runner=lambda n: (_ for _ in ()).throw(SecretError("x")))


@pytest.mark.parametrize(
    "key", ["author", "auth", "auth_mode", "max_tokens", "token_budget", "token_budget_warn_pct"]
)
def test_ordinary_params_are_not_mistaken_for_secrets(key):
    assert_refs_only({key: "plain value"})


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "apiKey",
        "password",
        "passwd",
        "github_token",
        "token",
        "client-secret",
        "credentials",
        "private_key",
        "GITHUB_TOKEN",
        "githubToken",
        "auth_token",
    ],
)
def test_secret_keys_still_require_references(key):
    with pytest.raises(SecretError):
        assert_refs_only({key: "literal-value"})
    assert_refs_only({key: "grant:NAME"})
