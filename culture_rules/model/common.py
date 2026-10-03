"""Shared model plumbing: the ``Model`` mixin, schema version and ``RetryPolicy``."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Self

from culture_rules.model import serde

__all__ = ["SCHEMA_VERSION", "Model", "RetryPolicy", "doc"]

#: Version of the wire format of every top-level document (Rule, Workflow,
#: Actor, Machine). Adding an optional field is a minor bump; renaming or
#: removing a field (or changing its meaning) is a major bump.
SCHEMA_VERSION = "1.0"


def doc(text: str, **kwargs: Any) -> Any:
    """``dataclasses.field`` with a description that ends up in the JSON Schema."""
    return dataclasses.field(metadata={"doc": text}, **kwargs)


class Model:
    """Mixin giving every frozen model dataclass a stable, lossless JSON form."""

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-compatible dict; keys are the snake_case field names."""
        return serde.to_plain(self)

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, compact separators, deterministic."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_dict(cls, data: Any, *, strict: bool = True) -> Self:
        """Parse a dict; raises :class:`serde.ModelParseError` on a shape mismatch.

        ``strict=False`` ignores unknown fields (tolerant read for stored documents).
        """
        return serde.from_dict(cls, data, strict=strict)

    @classmethod
    def from_json(cls, text: str | bytes, *, strict: bool = True) -> Self:
        """Parse JSON text; raises :class:`serde.ModelParseError` on bad JSON or shape."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise serde.ModelParseError("", f"invalid JSON: {exc.msg}", code="json") from exc
        return serde.from_dict(cls, data, strict=strict)


@dataclass(frozen=True, kw_only=True)
class RetryPolicy(Model):
    """How often a step or action is re-attempted after a failure."""

    max_attempts: int = doc("Total attempts including the first (>= 1)", default=1)
    backoff_s: float = doc("Delay before the first retry, seconds (>= 0)", default=0.0)
    backoff_multiplier: float = doc("Factor applied to the delay per retry (>= 1)", default=1.0)
