"""What a release deploys: the node installer and the release manifest.

The wheel carries them in this package (``hatch_build.py`` force-includes them; they are never
committed here):

* ``install.sh`` - ``deploy/node/install.sh``, the engine node installer;
* ``release.json`` - ``deploy/release.json``, ``{version, new_rules, new_variables}``: the
  release this wheel is (equal to the package version) and the rules and variables it adds.

The wheel carries no rule bundle (d2): ``rollout seed`` and ``rollout import`` take one with
``--bundle DIR``.

Running from a source checkout (an editable install, the tests) the helpers fall back to those
source paths. Stdlib only.
"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

__all__ = ["install_script", "release", "release_manifest"]

_RELEASE = "release.json"


def _packaged() -> Path:
    """This package's directory in the installed wheel (or the source tree)."""
    return Path(str(files(__name__)))


def _source_root() -> Path:
    """The repository root, when running from a checkout."""
    return Path(__file__).resolve().parents[2]


def _find(name: str, source: Path) -> Path:
    packaged = _packaged() / name
    if packaged.is_file():
        return packaged
    if source.is_file():
        return source
    raise FileNotFoundError(f"{name} is neither in the package nor in a source checkout")


def install_script() -> Path:
    """The engine node installer, ``install.sh``."""
    return _find("install.sh", _source_root() / "deploy" / "node" / "install.sh")


def release_manifest() -> Path:
    """The release manifest file, ``release.json``."""
    return _find(_RELEASE, _source_root() / "deploy" / _RELEASE)


def release() -> dict[str, Any]:
    """The release manifest: ``{version, new_rules, new_variables}``."""
    return json.loads(release_manifest().read_text(encoding="utf-8"))
