"""``schema_version``, the stored-document envelope and the write guard.

Adapter-neutral helpers every :class:`~culture_rules.store.port.StoragePort`
adapter uses so the envelope rules are enforced identically everywhere.
Standard-library only.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culture_rules.store.port import Document, SchemaDowngradeError, VersionSkewError

_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.\d+)?$")


@dataclass(frozen=True, order=True)
class SchemaVersion:
    """A document schema version, ``MAJOR.MINOR``.

    A minor bump adds optional fields (old nodes may still write the document,
    ignoring what they do not understand). A major bump renames or removes
    fields; nodes that only understand an older major refuse to write it.
    """

    major: int
    minor: int = 0

    @classmethod
    def parse(cls, value: Any) -> SchemaVersion:
        """Parse ``"M"``, ``"M.m"``, ``"M.m.p"`` (patch dropped), an int major or SchemaVersion."""
        if isinstance(value, SchemaVersion):
            return value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return cls(value, 0)
        if isinstance(value, str):
            match = _VERSION_RE.match(value)
            if match:
                return cls(int(match.group(1)), int(match.group(2) or 0))
        raise ValueError(f"invalid schema_version: {value!r}")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, tuple):
            return (self.major, self.minor) == other
        if isinstance(other, SchemaVersion):
            return (self.major, self.minor) == (other.major, other.minor)
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.major, self.minor))


@dataclass(frozen=True)
class Envelope:
    """The envelope fields of a stored document; everything else is ignored."""

    id: str
    schema_version: SchemaVersion
    updated_at: str

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Envelope:
        try:
            return cls(
                id=require_id(document.get("id")),
                schema_version=SchemaVersion.parse(document["schema_version"]),
                updated_at=str(document["updated_at"]),
            )
        except KeyError as exc:
            raise ValueError(f"document is missing envelope field {exc}") from None


def utc_timestamp(moment: datetime | None = None) -> str:
    """ISO-8601 UTC timestamp with microseconds (the ``updated_at`` format)."""
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def require_collection(collection: Any) -> str:
    if not isinstance(collection, str) or not collection:
        raise ValueError("collection must be a non-empty string")
    return collection


def require_id(doc_id: Any) -> str:
    if not isinstance(doc_id, str) or not doc_id:
        raise ValueError("document id must be a non-empty string")
    return doc_id


def matches(document: Mapping[str, Any] | None, where: Mapping[str, Any] | None) -> bool:
    """Top-level equality match; a None value also matches a missing field."""
    if document is None:
        return False
    for key, want in (where or {}).items():
        if document.get(key) != want:
            return False
    return True


def merge_changes(
    existing: Mapping[str, Any] | None, doc_id: str, changes: Mapping[str, Any]
) -> Document:
    """Apply top-level ``changes`` to a copy of ``existing`` (or a new doc ``doc_id``)."""
    if "id" in changes and changes["id"] != doc_id:
        raise ValueError("update_if cannot change a document's id")
    merged = copy.deepcopy(dict(existing)) if existing is not None else {"id": doc_id}
    merged.update(copy.deepcopy(dict(changes)))
    return merged


def prepare_write(
    document: Any,
    existing: Mapping[str, Any] | None,
    node_version: SchemaVersion,
    now: str,
) -> Document:
    """Validate a write and return the deep-copied document to store.

    Stamps ``schema_version`` (the node's, if absent) and ``updated_at``; raises
    :class:`VersionSkewError` if the new or the existing document is of a newer
    major than ``node_version``, and :class:`SchemaDowngradeError` if the write
    would lower the existing document's version.
    """
    if not isinstance(document, Mapping):
        raise ValueError("document must be a mapping")
    out = copy.deepcopy(dict(document))
    require_id(out.get("id"))
    out.setdefault("schema_version", str(node_version))
    new_version = SchemaVersion.parse(out["schema_version"])
    if existing is not None:
        old_version = SchemaVersion.parse(existing.get("schema_version", "0.0"))
        if old_version.major > node_version.major:
            raise VersionSkewError(
                f"document {out['id']!r} is schema {old_version}; "
                f"this node supports up to major {node_version.major}"
            )
        if new_version < old_version:
            raise SchemaDowngradeError(
                f"refusing to downgrade {out['id']!r} from schema {old_version} to {new_version}"
            )
    if new_version.major > node_version.major:
        raise VersionSkewError(
            f"cannot write schema {new_version}; "
            f"this node supports up to major {node_version.major}"
        )
    out["updated_at"] = now
    return out
