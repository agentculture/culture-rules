"""Actor pins and canonical workflow digests for configuration changes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

__all__ = [
    "TRUSTED_ACTOR_DIGESTS",
    "actor_digest",
    "actor_refusal",
    "doc_refusal",
    "is_pinned",
    "workflow_digest",
    "workflow_digest_v2",
]

_DIGEST_PREFIX = "sha256:"
"""The scheme prefix on every digest here, workflow (v1 and v2) and actor alike."""


def _drop_notes(node: dict[str, Any]) -> None:
    """Remove ``notes`` from a workflow doc and from every step, recursing into loop bodies."""
    node.pop("notes", None)
    for step in node.get("steps") or node.get("body") or ():
        _drop_notes(step)


def workflow_digest(definition: Any) -> str | None:
    """The trust digest of a workflow definition (dict or model), or ``None`` if it does
    not parse as a workflow. ``version`` and ``notes`` (workflow and every step, loop
    bodies included) are left out of the hash."""
    from culture_rules.model.workflow import Workflow  # noqa: PLC0415 - model import is cheap

    try:
        model = (
            definition
            if isinstance(definition, Workflow)
            else Workflow.from_dict(dict(definition), strict=False)
        )
        doc = model.to_dict()
    except Exception:  # noqa: BLE001 - anything unparseable is simply not trusted
        return None
    doc.pop("version", None)
    _drop_notes(doc)
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _DIGEST_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


_PROSE = ("description", "notes")
"""The free-text fields the v2 digest leaves out, on the workflow and every step."""


def _drop_prose(node: dict[str, Any]) -> None:
    """Remove :data:`_PROSE` from a workflow doc and from every step, loop bodies included.
    Port descriptions stay in."""
    for key in _PROSE:
        node.pop(key, None)
    for step in node.get("steps") or node.get("body") or ():
        _drop_prose(step)


def workflow_digest_v2(definition: Any) -> str | None:
    """The v2 trust digest (spec c16): :func:`workflow_digest` that also leaves out
    ``description`` (workflow and every step, loop bodies included), so editing prose never
    changes it. ``None`` if the definition does not parse as a workflow."""
    from culture_rules.model.workflow import Workflow  # noqa: PLC0415 - model import is cheap

    try:
        model = (
            definition
            if isinstance(definition, Workflow)
            else Workflow.from_dict(dict(definition), strict=False)
        )
        doc = model.to_dict()
    except Exception:  # noqa: BLE001 - anything unparseable is simply not trusted
        return None
    doc.pop("version", None)
    _drop_prose(doc)
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _DIGEST_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


TRUSTED_ACTOR_DIGESTS: dict[str, frozenset[str]] = {
    # the live rules-culture-dev App actor as of 2026-10-07 (App 5183824, installation
    # 167755039, key grant:RULES_GITHUB_APP_PRIVATE_KEY, surface github, machine spark, NO
    # commit_author); tests/rules/fixtures/github-app.live.json is that document. The second
    # digest is the same actor with params.commit_author "rules-culture-dev[bot]"
    # (tests/rules/fixtures/github-app.with-author.json), approved by the operator on
    # 2026-10-08; both are trusted while the live actor moves over, then the first goes
    "github-app": frozenset(
        {
            "sha256:0d358b11bfa3e6bac5bcb6ba1b57bb88419023e335f87d42ad87f096c9352a63",
            "sha256:a44486a36bf12ff13dc8e6545e42b751d03b91480200e636e90c0731d4764468",
        }
    ),
}

_AGENT_FIELDS = ("id", "kind", "harness", "model", "machine")
_AGENT_PARAMS = (
    "bridge_url",
    "callback_url",
    "bridge_token",
    "sandbox",
    "model",
    "mode",
    "reviewer",
    "max_bound_input_chars",
)
_APP_FIELDS = ("id", "kind", "machine")
_APP_PARAMS = ("surface", "commit_author", "permissions")
_APP_CONNECTION = ("app_id", "installation_id", "private_key")
"""Repository scope is excluded so provisioning can add repositories independently."""


def _actor_projection(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The security-relevant fields of an actor, normalised."""
    params = doc.get("params") if isinstance(doc.get("params"), Mapping) else {}
    if doc.get("kind") == "app":
        conn = params.get("connection") if isinstance(params.get("connection"), Mapping) else {}
        out = {k: doc.get(k) for k in _APP_FIELDS}
        out["params"] = {k: params.get(k) for k in _APP_PARAMS}
        out["params"]["connection"] = {k: conn.get(k) for k in _APP_CONNECTION}
        return out
    out = {k: doc.get(k) for k in _AGENT_FIELDS}
    out["params"] = {k: params.get(k) for k in _AGENT_PARAMS}
    return out


def actor_digest(doc: Any) -> str | None:
    """The trust digest of an actor document (or model), or ``None`` if it is not one."""
    if hasattr(doc, "to_dict"):
        doc = doc.to_dict()
    if not isinstance(doc, Mapping) or not isinstance(doc.get("id"), str):
        return None
    text = json.dumps(
        _actor_projection(doc), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _DIGEST_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def actor_refusal(store: Any, actor_id: Any) -> tuple[str | None, str | None]:
    """``(refusal, digest)``: ``actor_not_trusted`` unless the actor's current stored
    document hashes to a digest listed for its id."""
    doc = store.get("actors", actor_id) if isinstance(actor_id, str) and actor_id else None
    return doc_refusal(actor_id, doc)


def doc_refusal(actor_id: Any, doc: Any) -> tuple[str | None, str | None]:
    """:func:`actor_refusal` for a document already read (a snapshot the caller then uses
    for its work, so the trusted document and the used one cannot differ)."""
    if not isinstance(doc, Mapping) or doc.get("deleted_at") or doc.get("enabled") is False:
        return "actor_not_trusted", None
    if doc.get("id") != actor_id:
        return "actor_not_trusted", None
    digest = actor_digest(doc)
    allowed = TRUSTED_ACTOR_DIGESTS.get(actor_id, frozenset()) if isinstance(actor_id, str) else ()
    return (None if digest in allowed else "actor_not_trusted"), digest


def is_pinned(actor_id: Any) -> bool:
    """Whether ``actor_id`` is one whose configuration must match a pinned digest."""
    return isinstance(actor_id, str) and actor_id in TRUSTED_ACTOR_DIGESTS


def main(argv: list[str] | None = None, stdin: Any = None) -> int:
    """``python -m culture_rules.actors.trusted actor|workflow|workflow-v2 < FILE``: print
    the digest (``workflow`` is v1, ``workflow-v2`` the one new pins use).

    The document is read from standard input, never from a path argument, so the helper
    opens no file it is told to (an operator pipes the exported document in)."""
    import sys  # noqa: PLC0415

    args = sys.argv[1:] if argv is None else argv
    source = sys.stdin if stdin is None else stdin
    digests = {
        "actor": actor_digest,
        "workflow": workflow_digest,
        "workflow-v2": workflow_digest_v2,
    }
    if len(args) != 1 or args[0] not in digests:
        print(
            "usage: python -m culture_rules.actors.trusted actor|workflow|workflow-v2 < FILE",
            file=sys.stderr,
        )
        return 1
    try:
        doc = json.loads(source.read(_MAX_DOC_CHARS + 1)[: _MAX_DOC_CHARS + 1])
    except ValueError:
        print("stdin is not a JSON document", file=sys.stderr)
        return 1
    digest = digests[args[0]](doc)
    if digest is None:
        print(f"not a valid {args[0]} document", file=sys.stderr)
        return 1
    print(digest)
    return 0


#: An exported actor or workflow is a few KB; anything larger is not one.
_MAX_DOC_CHARS = 1_000_000


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
