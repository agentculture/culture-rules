"""#45 t2: the wheel carries install.sh, the pr-fixer bundle and its two manifests.

``hatch_build.py`` force-includes them as ``culture_rules/deploy_data`` (the wheel itself is
built in tests/packaging/test_wheel.py); ``culture_rules.deploy_data`` finds them in the
installed package, or in the source tree when running from a checkout.
"""

from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

from culture_rules import deploy_data
from culture_rules.io.exchange import read_bundle

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "rules" / "pr-fixer"
TARGET = "culture_rules/deploy_data"


def hook():
    spec = importlib.util.spec_from_file_location("hatch_build", ROOT / "hatch_build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]


# --------------------------------------------------------------------------- the build hook


def test_the_hook_force_includes_install_sh_the_bundle_and_both_manifests():
    got = hook().deploy_force_include(ROOT)
    assert got == {
        str(ROOT / "deploy" / "node" / "install.sh"): f"{TARGET}/install.sh",
        str(BUNDLE / "workflows"): f"{TARGET}/pr-fixer/workflows",
        str(BUNDLE / "rules"): f"{TARGET}/pr-fixer/rules",
        str(BUNDLE / "actors"): f"{TARGET}/pr-fixer/actors",
        str(BUNDLE / "variables.json"): f"{TARGET}/pr-fixer/variables.json",
        str(BUNDLE / "release.json"): f"{TARGET}/pr-fixer/release.json",
    }
    assert all(Path(src).exists() for src in got)


def test_the_hook_skips_what_an_sdist_already_packages(tmp_path):
    """A wheel built from the sdist finds the files in the package tree: never add them twice."""
    mod = hook()
    for target in mod.DEPLOY_SOURCES.values():
        path = tmp_path / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    assert mod.deploy_force_include(tmp_path) == {}


def test_the_hook_refuses_a_tree_without_the_sources(tmp_path):
    build_hook = hook()
    with pytest.raises(RuntimeError, match="install.sh"):
        build_hook.deploy_force_include(tmp_path)


# --------------------------------------------------------------------------- the helpers


def test_from_a_checkout_the_helpers_find_the_source_tree():
    assert deploy_data.install_script() == ROOT / "deploy" / "node" / "install.sh"
    assert deploy_data.bundle_dir() == BUNDLE
    assert deploy_data.variables() == json.loads((BUNDLE / "variables.json").read_text())
    assert deploy_data.release() == json.loads((BUNDLE / "release.json").read_text())


def test_the_helpers_prefer_the_packaged_copy(tmp_path, monkeypatch):
    packaged = tmp_path / "deploy_data"
    (packaged / "pr-fixer" / "rules").mkdir(parents=True)
    (packaged / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (packaged / "pr-fixer" / "variables.json").write_text('[{"name": "v"}]', encoding="utf-8")
    (packaged / "pr-fixer" / "release.json").write_text('{"version": "9"}', encoding="utf-8")
    monkeypatch.setattr(deploy_data, "_packaged", lambda: packaged)
    assert deploy_data.install_script() == packaged / "install.sh"
    assert deploy_data.bundle_dir() == packaged / "pr-fixer"
    assert deploy_data.variables() == [{"name": "v"}]
    assert deploy_data.release() == {"version": "9"}


def test_neither_packaged_nor_in_a_checkout_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy_data, "_packaged", lambda: tmp_path / "none")
    monkeypatch.setattr(deploy_data, "_source_root", lambda: tmp_path / "none")
    with pytest.raises(FileNotFoundError):
        deploy_data.install_script()
    with pytest.raises(FileNotFoundError):
        deploy_data.bundle_dir()


# --------------------------------------------------------------------------- release.json


def test_release_json_matches_pyproject_and_names_only_what_ships():
    release = json.loads((BUNDLE / "release.json").read_text(encoding="utf-8"))
    assert set(release) == {"version", "new_rules", "new_variables"}
    assert release["version"] == pyproject_version()  # later releases bump both together
    read = read_bundle(BUNDLE)
    assert read.errors == []
    rules = {r.id for r in read.bundle.rules}
    names = {v["name"] for v in json.loads((BUNDLE / "variables.json").read_text())}
    assert isinstance(release["new_rules"], list)
    assert isinstance(release["new_variables"], list)
    assert set(release["new_rules"]) <= rules, set(release["new_rules"]) - rules
    assert set(release["new_variables"]) <= names, set(release["new_variables"]) - names
