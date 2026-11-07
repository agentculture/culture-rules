"""#45 t2: the wheel carries install.sh and the release manifest; d2: and no rule bundle.

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

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "deploy" / "release.json"
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


def test_the_hook_force_includes_install_sh_and_the_release_manifest_only():
    got = hook().deploy_force_include(ROOT)
    assert got == {
        str(ROOT / "deploy" / "node" / "install.sh"): f"{TARGET}/install.sh",
        str(RELEASE): f"{TARGET}/release.json",
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


def test_from_a_checkout_the_installer_is_the_source_one():
    assert deploy_data.install_script() == ROOT / "deploy" / "node" / "install.sh"


def test_from_a_checkout_the_release_is_deploy_release_json():
    assert deploy_data.release_manifest() == RELEASE
    assert deploy_data.release() == json.loads(RELEASE.read_text(encoding="utf-8"))


def test_the_helpers_prefer_the_packaged_copy(tmp_path, monkeypatch):
    packaged = tmp_path / "deploy_data"
    packaged.mkdir()
    (packaged / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (packaged / "release.json").write_text('{"version": "9"}', encoding="utf-8")
    monkeypatch.setattr(deploy_data, "_packaged", lambda: packaged)
    assert deploy_data.install_script() == packaged / "install.sh"
    assert deploy_data.release() == {"version": "9"}


def test_neither_packaged_nor_in_a_checkout_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy_data, "_packaged", lambda: tmp_path / "none")
    monkeypatch.setattr(deploy_data, "_source_root", lambda: tmp_path / "none")
    with pytest.raises(FileNotFoundError):
        deploy_data.install_script()
    with pytest.raises(FileNotFoundError):
        deploy_data.release()


def test_the_package_offers_no_bundle():
    """d2: the wheel carries no rule bundle, so there is nothing to default to."""
    assert not hasattr(deploy_data, "bundle_dir")
    assert not hasattr(deploy_data, "variables")


# --------------------------------------------------------------------------- release.json


def release_manifest() -> dict:
    return json.loads(RELEASE.read_text(encoding="utf-8"))


def test_release_json_has_the_three_fields():
    assert set(release_manifest()) == {"version", "new_rules", "new_variables"}


def test_release_json_matches_pyproject():
    assert release_manifest()["version"] == pyproject_version()  # bumped together


def test_release_json_new_rules_is_a_list():
    assert isinstance(release_manifest()["new_rules"], list)


def test_release_json_new_variables_is_a_list():
    assert isinstance(release_manifest()["new_variables"], list)
