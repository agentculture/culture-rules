"""Workflows trusted to push (d20 round 2): a set of definition digests pinned in code.

The PR fixer's safety rests on the exact shape of its workflow: the gate is the actor-less
built-in, the agent is an ``ai`` step, the reviewer runs through the locked bridge path,
one verdict step per try. Editing the workflow (an actor-routed "gate" that supplies a fake
diff, a decoy step standing in for the real writer, two verdict steps per try ...) could
undo that in ways per-step checks keep chasing. So the class is closed here: ``github.push``
and the built-in ``review`` step refuse any run whose **pinned** workflow definition does
not hash to a digest in :data:`TRUSTED_WORKFLOW_DIGESTS` (``workflow_not_trusted``). An
edited workflow still runs; it can never push.

The digest (:func:`workflow_digest`) is sha256 over the definition the run pinned, put
through the workflow model (so field order and defaults do not matter) without
``version`` (which every save bumps), as compact sorted JSON. It is recomputed from the
run's pinned definition, never read from a stored digest.

Rolling out a legitimate change: edit ``docs/rules/pr-fixer/workflows/pr-fixer.json``;
``tests/rules/test_trusted_workflow.py`` then fails and prints the new digest; add it to
the set in the same PR (keep the old one while runs pinned to it may still push, remove it
in a later release); ship the wheel, upgrade every node, then import the workflow. It is a
set so the split workflows planned for d21 can be listed beside it. Standard-library only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

__all__ = [
    "TRUSTED_ACTOR_DIGESTS",
    "TRUSTED_WORKFLOW_DIGESTS",
    "actor_digest",
    "actor_refusal",
    "workflow_digest",
    "workflow_refusal",
]

TRUSTED_WORKFLOW_DIGESTS: frozenset[str] = frozenset(
    {
        # pr-fixer, docs/rules/pr-fixer/workflows/pr-fixer.json (d20 round 2)
        "sha256:01ece1cd69f995aeb0e931553546aa905bdfa20bc7fc530dfbaea8c75f4fc5d6",
    }
)


def workflow_digest(definition: Any) -> str | None:
    """The trust digest of a workflow definition (dict or model), or ``None`` if it does
    not parse as a workflow."""
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
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def workflow_refusal(run: Mapping[str, Any] | None) -> str | None:
    """``workflow_not_trusted`` unless the run's pinned workflow is a trusted one."""
    pin = (run or {}).get("workflow") if isinstance(run, Mapping) else None
    definition = pin.get("definition") if isinstance(pin, Mapping) else None
    if not isinstance(definition, Mapping):
        return "workflow_not_trusted"
    return (
        None if workflow_digest(definition) in TRUSTED_WORKFLOW_DIGESTS else "workflow_not_trusted"
    )


# --------------------------------------------------------------------------- actors (round 3)

#: Per actor id, the digests of its security-relevant fields that may review or push
#: (Codex round-3 review #1). Actor documents live outside the workflow digest, so an actor
#: editor could otherwise point ``codex-reviewer`` at another bridge that reports approval,
#: or loosen the App's allowlist or commit author. The verdict step refuses an untrusted
#: reviewer actor and ``github.push`` an untrusted App actor (``actor_not_trusted``); the
#: review record snapshots the reviewer digest it checked. ``qwen-fixer`` is not listed:
#: its output is reviewed. A fully malicious admin who controls actors and a bridge is out
#: of scope; this makes such a change need a release.
#:
#: ``github-app`` is empty on purpose: its live values (App id, installation, key reference,
#: repos, commit author) are not in this repository. Until the operator adds the live
#: digest, every push refuses ``actor_not_trusted`` (fail closed). Compute it from an
#: exported actor with ``python -m culture_rules.actors.trusted actor <file.json>``.
TRUSTED_ACTOR_DIGESTS: dict[str, frozenset[str]] = {
    # docs/rules/pr-fixer/actors/codex-reviewer.json (d20 round 3)
    "codex-reviewer": frozenset(
        {"sha256:dc26a418ca33b5e604542a03569c0ff31ad7b86b04140d56776ece4e72b3a339"}
    ),
    "github-app": frozenset(),
}

_AGENT_FIELDS = ("id", "kind", "harness", "model", "machine")
_AGENT_PARAMS = (
    "bridge_url",
    "callback_url",
    "bridge_token",
    "sandbox",
    "model",
    "mode",
    "locked_instruction",
    "reviewer",
    "max_bound_input_chars",
)
_APP_FIELDS = ("id", "kind", "machine")
_APP_PARAMS = ("surface", "commit_author", "permissions")
_APP_CONNECTION = ("app_id", "installation_id", "private_key", "repos")


def _actor_projection(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of an actor that decide what it can review or push, normalised."""
    params = doc.get("params") if isinstance(doc.get("params"), Mapping) else {}
    if doc.get("kind") == "app":
        conn = params.get("connection") if isinstance(params.get("connection"), Mapping) else {}
        out = {k: doc.get(k) for k in _APP_FIELDS}
        out["params"] = {k: params.get(k) for k in _APP_PARAMS}
        connection = {k: conn.get(k) for k in _APP_CONNECTION}
        repos = connection.get("repos")
        if isinstance(repos, list):
            connection["repos"] = sorted({str(r).casefold() for r in repos})
        out["params"]["connection"] = connection
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
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def actor_refusal(store: Any, actor_id: Any) -> tuple[str | None, str | None]:
    """``(refusal, digest)``: ``actor_not_trusted`` unless the actor's current stored
    document hashes to a digest listed for its id."""
    doc = store.get("actors", actor_id) if isinstance(actor_id, str) and actor_id else None
    if not doc or doc.get("deleted_at") or doc.get("enabled") is False:
        return "actor_not_trusted", None
    digest = actor_digest(doc)
    allowed = TRUSTED_ACTOR_DIGESTS.get(actor_id, frozenset())
    return (None if digest in allowed else "actor_not_trusted"), digest


def main(argv: list[str] | None = None) -> int:
    """``python -m culture_rules.actors.trusted actor|workflow FILE``: print the digest."""
    import sys  # noqa: PLC0415

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in ("actor", "workflow"):
        print("usage: python -m culture_rules.actors.trusted actor|workflow FILE", file=sys.stderr)
        return 1
    with open(args[1], encoding="utf-8") as fh:
        doc = json.load(fh)
    digest = actor_digest(doc) if args[0] == "actor" else workflow_digest(doc)
    if digest is None:
        print(f"not a valid {args[0]} document", file=sys.stderr)
        return 1
    print(digest)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
