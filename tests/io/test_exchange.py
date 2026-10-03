"""t11: export/import of rules, workflows, actors and secret references."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from culture_rules.io.bundle import Bundle, SecretRef
from culture_rules.io.exchange import (
    bundle_from_store,
    export_bundle,
    import_bundle,
    read_bundle,
)
from culture_rules.io.gitrepo import load_from_repo, save_to_repo
from culture_rules.store.memory import MemoryStore
from tests.model.factories import make_actor, make_rule, make_workflow

yaml = pytest.importorskip("yaml")


def _bundle() -> Bundle:
    return Bundle(
        rules=(make_rule(), make_rule(id="r-two", name="Two", must_after=(), may_after=())),
        workflows=(make_workflow(),),
        actors=(make_actor(),),
        secrets=(SecretRef(name="gh-token", ref="env:GITHUB_TOKEN"),),
    )


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.mark.parametrize("fmt", ["yaml", "json"])
def test_export_import_roundtrip_is_identical(tmp_path, fmt):
    bundle = _bundle()
    plan = export_bundle(bundle, tmp_path / "out", fmt=fmt, apply=True)
    assert plan.applied
    ext = "yaml" if fmt == "yaml" else "json"
    assert (tmp_path / "out" / "rules" / f"r-review.{ext}").is_file()
    assert (tmp_path / "out" / "workflows" / f"wf-summary.{ext}").is_file()
    read = read_bundle(tmp_path / "out")
    assert read.errors == []
    assert read.bundle == bundle


def test_export_dry_run_writes_nothing(tmp_path):
    plan = export_bundle(_bundle(), tmp_path / "out", apply=False)
    assert not plan.applied
    assert not (tmp_path / "out").exists()
    assert {c.path for c in plan.changes} >= {"rules/r-review.yaml", "workflows/wf-summary.yaml"}


def test_secrets_are_references_only(tmp_path):
    export_bundle(_bundle(), tmp_path, apply=True)
    text = (tmp_path / "secrets" / "gh-token.yaml").read_text()
    assert "env:GITHUB_TOKEN" in text
    with pytest.raises(ValueError):
        export_bundle(
            Bundle(secrets=(SecretRef(name="x", ref="hunter2-the-actual-value"),)),
            tmp_path / "bad",
            apply=True,
        )


def test_secret_value_field_on_import_is_an_error(tmp_path):
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "x.yaml").write_text("name: x\nref: env:X\nvalue: hunter2\n")
    read = read_bundle(tmp_path)
    assert any("value" in e.path for e in read.errors)


def test_import_is_dry_run_diff_unless_apply(tmp_path):
    export_bundle(_bundle(), tmp_path / "src", apply=True)
    store = MemoryStore()
    plan = import_bundle(tmp_path / "src", store)
    assert not plan.applied and plan.errors == []
    assert {c.action for c in plan.changes} == {"add"}
    assert store.find("rules") == []  # nothing written
    assert "r-review" in plan.render()

    applied = import_bundle(tmp_path / "src", store, apply=True)
    assert applied.applied
    assert {d["id"] for d in store.find("rules")} == {"r-review", "r-two"}
    assert bundle_from_store(store) == _bundle()

    again = import_bundle(tmp_path / "src", store)
    assert {c.action for c in again.changes} == {"unchanged"}


def test_import_diff_shows_changes(tmp_path):
    store = MemoryStore()
    export_bundle(_bundle(), tmp_path / "src", apply=True)
    import_bundle(tmp_path / "src", store, apply=True)
    changed = replace(_bundle().rules[0], name="Renamed")
    export_bundle(Bundle(rules=(changed,)), tmp_path / "src", apply=True)
    plan = import_bundle(tmp_path / "src", store)
    change = next(c for c in plan.changes if c.id == "r-review")
    assert change.action == "change"
    assert "Renamed" in change.diff and "-" in change.diff


def test_import_reports_unknown_fields_as_errors_and_refuses_apply(tmp_path):
    export_bundle(_bundle(), tmp_path / "src", apply=True)
    path = tmp_path / "src" / "rules" / "r-two.yaml"
    data = yaml.safe_load(path.read_text())
    data["bogus"] = 1
    path.write_text(yaml.safe_dump(data))
    store = MemoryStore()
    plan = import_bundle(tmp_path / "src", store, apply=True)
    assert not plan.applied
    assert any("bogus" in e.path and e.code == "unknown_field" for e in plan.errors)
    assert store.find("rules") == []  # all-or-nothing


def test_import_reports_invalid_and_mismatched_files(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "a.json").write_text(json.dumps({"id": "b", "name": "x"}))
    (tmp_path / "rules" / "broken.yaml").write_text("{: [")
    read = read_bundle(tmp_path)
    codes = {e.code for e in read.errors}
    assert "id_mismatch" in codes and "parse" in codes


def test_yaml_missing_gives_clear_error(tmp_path, monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "yaml":
            raise ImportError("no yaml")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(RuntimeError, match="culture-rules\\[yaml\\]"):
        export_bundle(_bundle(), tmp_path, fmt="yaml", apply=True)


def test_bad_ids_cannot_escape_directory(tmp_path):
    bad = replace(make_rule(), id="../evil")
    with pytest.raises(ValueError):
        export_bundle(Bundle(rules=(bad,)), tmp_path / "o", apply=True)


# --- git target -----------------------------------------------------------


@pytest.fixture
def repos(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True
    )
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)
    return bare, work


def test_save_to_and_load_from_second_git_repo(repos):
    bare, work = repos
    bundle = _bundle()
    dry = save_to_repo(bundle, work, directory="defs", apply=False)
    assert not dry.committed and not (work / "defs").exists()

    done = save_to_repo(bundle, work, directory="defs", apply=True, push=True)
    assert done.committed and done.pushed and done.commit
    assert "defs/rules/r-review.yaml" in _git(work, "ls-files")
    assert _git(work, "rev-parse", "HEAD").strip() == done.commit

    loaded = load_from_repo(bare, directory="defs")
    assert loaded.errors == []
    assert loaded.bundle == bundle

    # saving the same content again is a no-op (no empty commit)
    again = save_to_repo(bundle, work, directory="defs", apply=True)
    assert not again.committed


def test_load_from_repo_missing_directory_is_error(repos):
    bare, work = repos
    save_to_repo(_bundle(), work, directory="defs", apply=True, push=True)
    loaded = load_from_repo(bare, directory="nope")
    assert loaded.errors and loaded.errors[0].code == "missing_directory"


def test_save_to_repo_commits_only_the_export_plan(repos):
    _, work = repos
    (work / "private-notes.txt").write_text("secret\n")
    (work / "other").mkdir()
    (work / "other" / "staged.txt").write_text("staged\n")
    _git(work, "add", "other/staged.txt")
    done = save_to_repo(_bundle(), work, directory="defs", apply=True)
    assert done.committed
    files = _git(work, "show", "--name-only", "--pretty=format:", "HEAD").split()
    assert files and all(f.startswith("defs/") for f in files)
    assert "private-notes.txt" not in files and "other/staged.txt" not in files
    # the unrelated work stays exactly as it was
    assert "private-notes.txt" in _git(work, "status", "--porcelain")
    assert "A  other/staged.txt" in _git(work, "status", "--porcelain")


@pytest.mark.parametrize(
    "path", ["rule/x.yaml", "rules/x.txt", "rules/sub/x.yaml", "x.yaml", "bogus/x.json"]
)
def test_read_files_reports_misplaced_files(path):
    from culture_rules.io.exchange import read_files

    result = read_files({path: "id: x\n"})
    assert [(e.path, e.code) for e in result.errors] == [(path, "unrecognised_path")]
    assert result.bundle == Bundle()


def test_read_files_reports_duplicate_ids_across_formats():
    from culture_rules.io.exchange import bundle_files, read_files

    one = Bundle(rules=(make_rule(must_after=(), may_after=(), supersedes=()),))
    yml = bundle_files(one, "yaml")["rules/r-review.yaml"]
    js = bundle_files(one, "json")["rules/r-review.json"]
    result = read_files({"rules/r-review.yaml": yml, "rules/r-review.json": js})
    assert [e.code for e in result.errors] == ["duplicate_id"]
    assert result.errors[0].path == "rules/r-review.yaml"  # json sorts first and wins
    assert len(result.bundle.rules) == 1
