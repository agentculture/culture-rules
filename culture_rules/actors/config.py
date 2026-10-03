"""Actor configuration from a DB record or a repo's ``culture.yaml``.

The shape logic is cited (cite-don't-import) from
``culture/culture_core/config.py`` lines 301-330 (``load_culture_yaml``: the
top-level single-agent shape versus the ``agents:`` list shape) and 273-296
(``_parse_agent_entry``: known fields vs. extras). Nothing is imported from culture.

YAML parsing needs the optional ``yaml`` extra (``pip install culture-rules[yaml]``);
it is imported lazily so the core package stays dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CULTURE_YAML = "culture.yaml"

# Fields read from an agent entry; everything else is preserved in ``extras``.
_KNOWN_FIELDS = (
    "suffix",
    "backend",
    "model",
    "channels",
    "thinking",
    "system_prompt",
    "tags",
    "icon",
)


class ActorConfigError(ValueError):
    """Raised when a culture.yaml or record cannot be turned into an ActorConfig."""


@dataclass(frozen=True)
class ActorConfig:
    """Configuration of one actor. ``harness`` is culture's ``backend``."""

    key: str
    kind: str = "agent"
    harness: str = "claude"
    model: str | None = None
    channels: tuple[str, ...] = ()
    thinking: str | None = None
    system_prompt: str = ""
    tags: tuple[str, ...] = ()
    icon: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    # Provenance only: excluded from equality so repo- and DB-built configs compare equal.
    source: str = field(default="repo", compare=False)

    def __hash__(self) -> int:  # extras is a dict; hash on identity-bearing scalars
        return hash((self.key, self.kind, self.harness, self.model))


def _from_mapping(raw: dict[str, Any], source: str, where: str) -> ActorConfig:
    raw = dict(raw)
    key = raw.pop("suffix", None) or raw.pop("key", None)
    if not key:
        raise ActorConfigError(f"Agent entry in {where} is missing a 'suffix' field")
    # Accept the record-side spellings too: harness == backend.
    harness = raw.pop("backend", None) or raw.pop("harness", None) or "claude"
    kind = raw.pop("kind", "agent")
    extras_in = raw.pop("extras", None)
    known = {k: raw.pop(k) for k in _KNOWN_FIELDS if k in raw}
    extras = dict(raw)
    if isinstance(extras_in, dict):
        extras.update(extras_in)
    return ActorConfig(
        key=str(key),
        kind=kind,
        harness=harness,
        model=known.get("model"),
        channels=tuple(known.get("channels") or ()),
        thinking=known.get("thinking"),
        system_prompt=known.get("system_prompt") or "",
        tags=tuple(known.get("tags") or ()),
        icon=known.get("icon"),
        extras=extras,
        source=source,
    )


def parse_culture_yaml(text: str, where: str = CULTURE_YAML) -> list[ActorConfig]:
    """Parse culture.yaml text in either shape into a list of ActorConfig."""
    try:
        import yaml  # lazy: optional extra
    except ImportError as exc:
        raise ActorConfigError(
            "PyYAML is required to read culture.yaml; install with "
            "'pip install culture-rules[yaml]'"
        ) from exc
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ActorConfigError(f"Invalid YAML in {where}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ActorConfigError(f"{where} must contain a mapping at the top level")
    if isinstance(raw.get("agents"), list):  # multi-agent shape
        entries = raw["agents"]
    else:  # single-agent shape
        entries = [raw]
    for entry in entries:
        if not isinstance(entry, dict):
            raise ActorConfigError(f"Agent entry in {where} must be a mapping")
    return [_from_mapping(e, "repo", where) for e in entries]


def load_all_from_repo(path: str | Path) -> list[ActorConfig]:
    """Load every agent from a culture.yaml file, or from ``<dir>/culture.yaml``."""
    p = Path(path)
    if p.is_dir():
        p = p / CULTURE_YAML
    if not p.is_file():
        raise FileNotFoundError(f"No culture.yaml found at {p}")
    return parse_culture_yaml(p.read_text(encoding="utf-8"), str(p))


def load_from_repo(path: str | Path, suffix: str | None = None) -> ActorConfig:
    """Load one agent (the first, or the one matching ``suffix``) from a repo's culture.yaml."""
    configs = load_all_from_repo(path)
    if suffix is None:
        return configs[0]
    for cfg in configs:
        if cfg.key == suffix:
            return cfg
    raise ActorConfigError(f"Agent with suffix {suffix!r} not found in {path}")


def load_from_record(record: dict[str, Any]) -> ActorConfig:
    """Build an ActorConfig from a DB record (same field names as culture.yaml)."""
    if not isinstance(record, dict):
        raise ActorConfigError("Actor record must be a mapping")
    rec = {k: v for k, v in record.items() if k not in ("_id", "id", "source")}
    return _from_mapping(rec, "db", "record")
