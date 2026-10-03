"""JSON / YAML text codec. YAML needs the optional ``yaml`` extra and is imported lazily."""

from __future__ import annotations

import json
from typing import Any

__all__ = ["EXTENSIONS", "dumps", "format_of", "loads"]

#: Extensions read on import, per format. Export writes the first one.
EXTENSIONS: dict[str, tuple[str, ...]] = {"yaml": (".yaml", ".yml"), "json": (".json",)}


def format_of(filename: str) -> str | None:
    """``yaml`` / ``json`` for a recognised extension, else ``None``."""
    for fmt, exts in EXTENSIONS.items():
        if filename.endswith(exts):
            return fmt
    return None


def _yaml() -> Any:
    try:
        import yaml  # noqa: PLC0415 - optional extra, imported lazily
    except ImportError as exc:
        raise RuntimeError(
            "YAML support needs the optional extra: pip install 'culture-rules[yaml]'"
        ) from exc
    return yaml


def dumps(data: dict[str, Any], fmt: str) -> str:
    """Serialise a plain dict deterministically (trailing newline included)."""
    if fmt == "json":
        return json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if fmt == "yaml":
        return _yaml().safe_dump(data, sort_keys=True, default_flow_style=False, allow_unicode=True)
    raise ValueError(f"unknown format {fmt!r} (expected 'yaml' or 'json')")


def loads(text: str, fmt: str) -> Any:
    """Parse text; raises ``ValueError`` (any parser error is normalised) on bad input."""
    if fmt == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if fmt == "yaml":
        yaml = _yaml()
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML: {exc}") from exc
    raise ValueError(f"unknown format {fmt!r} (expected 'yaml' or 'json')")
