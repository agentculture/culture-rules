"""What a release deploys: the node installer, the pr-fixer bundle and its two manifests.

The wheel carries them in this package (``hatch_build.py`` force-includes them; they are never
committed here):

* ``install.sh`` - ``deploy/node/install.sh``, the engine node installer;
* ``pr-fixer/{workflows,rules,actors}/*.json`` - ``docs/rules/pr-fixer``, in the import format;
* ``pr-fixer/variables.json`` - every seed variable ``{name, value, description}``
  (``seed-variables.sh`` reads the same file);
* ``pr-fixer/release.json`` - ``{version, new_rules, new_variables}``: the release this bundle
  belongs to (equal to the package version) and what it adds.

Running from a source checkout (an editable install, the tests) the helpers fall back to those
source paths. Stdlib only.
"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

__all__ = ["bundle_dir", "install_script", "release", "variables"]

BUNDLE = "pr-fixer"
_VARIABLES = "variables.json"


def _packaged() -> Path:
    """This package's directory in the installed wheel (or the source tree)."""
    return Path(str(files(__name__)))


def _source_root() -> Path:
    """The repository root, when running from a checkout."""
    return Path(__file__).resolve().parents[2]


def install_script() -> Path:
    """The engine node installer, ``install.sh``."""
    packaged = _packaged() / "install.sh"
    if packaged.is_file():
        return packaged
    source = _source_root() / "deploy" / "node" / "install.sh"
    if source.is_file():
        return source
    raise FileNotFoundError("install.sh is neither in the package nor in a source checkout")


def bundle_dir() -> Path:
    """The pr-fixer bundle: ``workflows/``, ``rules/``, ``actors/`` and the two manifests."""
    packaged = _packaged() / BUNDLE
    if (packaged / _VARIABLES).is_file():
        return packaged
    source = _source_root() / "docs" / "rules" / BUNDLE
    if (source / _VARIABLES).is_file():
        return source
    raise FileNotFoundError("the pr-fixer bundle is neither in the package nor in a checkout")


def _read(name: str) -> Any:
    return json.loads((bundle_dir() / name).read_text(encoding="utf-8"))


def variables() -> list[dict[str, Any]]:
    """The seed variables, each ``{name, value, description}``, in seeding order."""
    return _read(_VARIABLES)


def release() -> dict[str, Any]:
    """The release manifest: ``{version, new_rules, new_variables}``."""
    return _read("release.json")
