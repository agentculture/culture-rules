"""t34: the built wheel ships culture_rules/web_dist (needs npm + uv; slow, skipped without)."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.slow


@pytest.mark.skipif(
    not (shutil.which("npm") and shutil.which("uv") and (ROOT / "web" / "node_modules").is_dir()),
    reason="needs npm, uv and web/node_modules (npm ci in web/)",
)
def test_wheel_contains_the_web_build(tmp_path):
    subprocess.run(["npm", "run", "build"], cwd=ROOT / "web", check=True, capture_output=True)
    out = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)], cwd=ROOT, check=True, capture_output=True
    )
    (wheel,) = out.glob("culture_rules-*.whl")
    names = zipfile.ZipFile(wheel).namelist()
    assert "culture_rules/web_dist/index.html" in names
    assert any(n.startswith("culture_rules/web_dist/assets/") for n in names)


def test_build_hook_force_includes_the_build(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("hatch_build", ROOT / "hatch_build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.web_force_include(tmp_path, require=False) == {}
    with pytest.raises(RuntimeError):
        mod.web_force_include(tmp_path, require=True)
    (tmp_path / "web" / "dist").mkdir(parents=True)
    (tmp_path / "web" / "dist" / "index.html").write_text("x")
    assert mod.web_force_include(tmp_path, require=True) == {
        str(tmp_path / "web" / "dist"): "culture_rules/web_dist"
    }
