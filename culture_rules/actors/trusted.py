"""Workflows trusted to review and push (d20 round 2, d21): definition digests pinned in code.

The PR fixer's safety rests on the exact shape of its workflow: the gate is the actor-less
built-in, the agent is an ``ai`` step, the reviewer runs through the locked bridge path,
one verdict step per try. Editing the workflow (an actor-routed "gate" that supplies a fake
diff, a decoy step standing in for the real writer, two verdict steps per try ...) could
undo that in ways per-step checks keep chasing. So the class is closed here: ``github.push``
and the built-in ``review`` step refuse any run whose **pinned** workflow definition does
not hash to a digest in :data:`TRUSTED_WORKFLOWS` (``workflow_not_trusted``), and since the
split into chained rules (d21) each digest has a **role**: ``pr-fixer`` (the single d20
workflow), ``pr-fix`` (builds and gates the commit), ``review-commit`` (reviews it) or
``publish-fix`` (pushes it). A run may only do what its role allows, and a push verifies the
role of every run in its chain. An edited workflow still runs; it can never push.

The digest (:func:`workflow_digest`) is sha256 over the definition the run pinned, put
through the workflow model (so field order and defaults do not matter) without
``version`` (which every save bumps) and ``notes`` (free text, on the workflow and every
step, loop bodies included), as compact sorted JSON. It is recomputed from the
run's pinned definition, never read from a stored digest.

The digest is versioned (spec c16): :func:`workflow_digest_v2` also leaves out
``description`` (workflow and every step), so editing prose never changes it. A run keeps
its role when its v1 digest is in :data:`TRUSTED_WORKFLOWS` or its v2 digest is in
:data:`TRUSTED_WORKFLOWS_V2`, the same role either way (a definition matching two roles is
trusted in none). The v1 set holds the older pins and is retired in a later release.

Rolling out a legitimate change: edit the workflow in ``docs/rules/pr-fixer/workflows/``;
``tests/rules/test_trusted_workflow.py`` then fails and prints the new v2 digest (or run
``python -m culture_rules.actors.trusted workflow-v2 < FILE``); add it to
its role's set in :data:`TRUSTED_WORKFLOWS_V2` in the same PR (keep the old one while runs
pinned to it may still push, remove it in a later release); ship the wheel, upgrade every
node, then import the workflow. Standard-library only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

__all__ = [
    "ROLE_FIX",
    "ROLE_PUBLISH",
    "ROLE_REVIEW",
    "ROLE_SINGLE",
    "TRUSTED_ACTOR_DIGESTS",
    "TRUSTED_WORKFLOWS",
    "TRUSTED_WORKFLOWS_V2",
    "actor_digest",
    "actor_refusal",
    "doc_refusal",
    "is_pinned",
    "trusted_workflow_digests",
    "workflow_digest",
    "workflow_digest_v2",
    "workflow_refusal",
    "workflow_role",
]

_DIGEST_PREFIX = "sha256:"
"""The scheme prefix on every digest here, workflow (v1 and v2) and actor alike."""

ROLE_SINGLE = "pr-fixer"
"""The d20 single workflow: fixes, gates, reviews and pushes in one run (its own review)."""
ROLE_FIX = "pr-fix"
"""d21: quiet period, threads, agent and gate; builds the commit, never reviews or pushes."""
ROLE_REVIEW = "review-commit"
"""d21: the independent review of a ``pr-fix`` run's gated commit; records the verdict."""
ROLE_PUBLISH = "publish-fix"
"""d21: pushes a commit a ``review-commit`` run approved, then answers the threads."""

TRUSTED_WORKFLOWS: dict[str, frozenset[str]] = {
    # docs/rules/pr-fixer/workflows/pr-fixer.json as shipped in 0.13.0 (d20 round 2); a copy
    # lives in tests/rules/fixtures/pr-fixer-single/. Kept while runs pinned to it may still
    # push; the split replaces it (d21) - drop it in a later release.
    ROLE_SINGLE: frozenset(
        {"sha256:01ece1cd69f995aeb0e931553546aa905bdfa20bc7fc530dfbaea8c75f4fc5d6"}
    ),
    # docs/rules/pr-fixer/workflows/pr-fix.json with the d25 GitGuardian hold (d21's version
    # without the hold never ran live, so it is not trusted); the first digest is that 3-try
    # version, kept while runs pinned to it may still push; the second is the #35 d30
    # one-try version (max_iterations 1, ends at the gate's verdict, exports
    # gate_instruction) - drop the first in a later release; the third is #39's (one agent
    # step per fixer level - agent_qwen, or agent_codex and the Codex hand-off - then select
    # feeding the single gate; inputs fixer, reviewer, attempt, level; outputs implementer
    # and reviewer). The d30 one stays trusted while runs pinned to it may still push; the
    # fourth is #56's (a Claude level: agent_claude on spark with its own hand-off,
    # handoff_export_claude run_as culture-claude-fixer and handoff_import_claude, feeding
    # select's claude source; the Codex export gains run_as culture-codex-fixer). #39's
    # stays trusted while runs pinned to it may still push.
    ROLE_FIX: frozenset(
        {
            "sha256:04570dee963d1fbf6ae55680d1ecc16c702463c858663d4fc61b557d3243b6a8",
            "sha256:307e82fc96eba7a36be1781b2f550728fbf232d37d776ed878071ae7f74ae032",
            "sha256:721e25bea3163437e4d6808eb2e817057e4f9900d2f7123ee0a00f1880136005",
            "sha256:2255b0820f32a355eff9b20e4ab9dea506660d12b54e29130a248b8cf58a708c",
        }
    ),
    # docs/rules/pr-fixer/workflows/review-commit.json: the first is d21's (one review step on
    # codex-reviewer), kept while runs pinned to it may still record a review; the second is
    # #39's (review_codex or review_qwen, routed by the try's reviewer input, and a verdict
    # step that reads review_steps and takes the one that succeeded)
    ROLE_REVIEW: frozenset(
        {
            "sha256:79064f76264a469fc83ef2ed7e2b2d9e2979fbf58f339428319ac3c2a7b158e6",
            "sha256:0653ba8c61a2eb0368819ac07b2a9b08ad3a8e6b023c6bfcf8ff4b93682b2bbd",
        }
    ),
    # docs/rules/pr-fixer/workflows/publish-fix.json (d21)
    ROLE_PUBLISH: frozenset(
        {"sha256:0fa92074e1cfa67c1388f0423fc379063fd82c5886bc619c5ef4ffcc83c23680"}
    ),
}
"""Per role, the digests of the workflow definitions trusted in it (d21). A digest belongs
to one role: the role says what a run of that workflow may do - record a review, build the
commit a review approves, push an approved commit - and the push verifies the WHOLE chain
by role (:mod:`culture_rules.actors.lineage`)."""

TRUSTED_WORKFLOWS_V2: dict[str, frozenset[str]] = {
    # Spec 2026-10-17 workflow connecting phrases (c16, q5 option B): the v2 digests
    # (:func:`workflow_digest_v2`, prose left out) of the definitions committed when the
    # versioned digest shipped. A run is trusted when its v1 digest is in TRUSTED_WORKFLOWS
    # or its v2 digest is here, in the same role either way. TRUSTED_WORKFLOWS (v1) stays
    # for runs pinned to older definitions and is retired in a later release, as the d20
    # and d21 pins were; new pins go here only.
    # the single d20 workflow (tests/rules/fixtures/pr-fixer-single) is historical: it stays
    # trusted through its v1 pin only and gets no v2 pin (spec h33, deviation d4)
    ROLE_SINGLE: frozenset(),
    # docs/rules/pr-fixer/workflows/pr-fix.json (#56's, v1 sha256:2255b082...)
    ROLE_FIX: frozenset(
        {
            # the pre-rename definition (d12): stays while runs pinned to it may still push;
            # remove in a later release
            "sha256:4a92df7e0b127246fe668c8ea2233169088f1d9ee06df0bad33ebe2036dad769",
            # the committed definition with the plain stand-in step names (d12)
            "sha256:aaa5f90e2a391abcdebe93962dd697b173c5e7691bb6ad66763b82d6d670af47",
        }
    ),
    # docs/rules/pr-fixer/workflows/review-commit.json (#39's, v1 sha256:0653ba8c...)
    ROLE_REVIEW: frozenset(
        {"sha256:e0a782fafb7aa406d83058290a45901afae7112fc33d1a0398a3e5ea31dec4ac"}
    ),
    # docs/rules/pr-fixer/workflows/publish-fix.json (d21, v1 sha256:0fa92074...)
    ROLE_PUBLISH: frozenset(
        {"sha256:540cb216fcc1ccccf8d5b7d29abd07427e9a22f6ad40bd930c3036890a3f9c84"}
    ),
}
"""Per role, the v2 digests trusted in it (spec c16). Same roles as :data:`TRUSTED_WORKFLOWS`;
a definition must come out in one role whichever version matched."""


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


def trusted_workflow_digests() -> frozenset[str]:
    """Every trusted v1 digest, whatever its role."""
    return frozenset().union(*TRUSTED_WORKFLOWS.values())


def workflow_role(run: Mapping[str, Any] | None) -> str | None:
    """The role of the run's **pinned** workflow (recomputed from its definition, never a
    stored digest), or ``None`` when it is not a trusted workflow."""
    pin = (run or {}).get("workflow") if isinstance(run, Mapping) else None
    definition = pin.get("definition") if isinstance(pin, Mapping) else None
    if not isinstance(definition, Mapping):
        return None
    try:
        v1 = workflow_digest(definition)
        v2 = workflow_digest_v2(definition)
    except Exception:  # noqa: BLE001 - fail closed: no error path grants a role
        return None
    roles = {
        role for role, digests in TRUSTED_WORKFLOWS.items() if v1 is not None and v1 in digests
    }
    roles |= {
        role for role, digests in TRUSTED_WORKFLOWS_V2.items() if v2 is not None and v2 in digests
    }
    # one role whichever version matched; a definition in two roles is trusted in none
    return next(iter(roles)) if len(roles) == 1 else None


def workflow_refusal(
    run: Mapping[str, Any] | None, roles: tuple[str, ...] | None = None
) -> str | None:
    """``workflow_not_trusted`` unless the run's pinned workflow is a trusted one (in one of
    ``roles``, when given)."""
    role = workflow_role(run)
    if role is None or (roles is not None and role not in roles):
        return "workflow_not_trusted"
    return None


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
#: The ``github-app`` digest is the live actor's (fixture: tests/rules/fixtures/
#: github-app.live.json). Compute a new one from the stored document (``culture-rules actors
#: show github-app --json``, a bare actor object) with ``python -m
#: culture_rules.actors.trusted actor < file.json``. Its ``repos`` are deliberately not part
#: of the digest (see ``_APP_CONNECTION``).
TRUSTED_ACTOR_DIGESTS: dict[str, frozenset[str]] = {
    # docs/rules/pr-fixer/actors/codex-reviewer.json (d20 round 3)
    "codex-reviewer": frozenset(
        {"sha256:dc26a418ca33b5e604542a03569c0ff31ad7b86b04140d56776ece4e72b3a339"}
    ),
    # docs/rules/pr-fixer/actors/qwen-reviewer.json (#39): reviews a Codex try. Qwen Code on
    # spark2 in mode plan; its bridge_url and callback_url are the committed (loopback)
    # values, so a live actor with other endpoints needs its own digest here
    # #56: it also reviews every Claude try. spark2's Qwen bridge listens only on its
    # tailnet address, so the live actor carries spark2's tailnet bridge_url and the
    # API's callback_url (the values qwen-fixer runs with); only that digest is pinned
    # here, never the address.
    "qwen-reviewer": frozenset(
        {
            "sha256:5b0e1aa73c6222a757b8357fec19b2c86d937848353bfe443c4e268fffda8b1a",
            "sha256:94ea1151c1ff1f1f6b47fcd6b331ca86c3f3ccedc6e15e6fb7b6d25ad07d6618",
        }
    ),
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
    "locked_instruction",
    "reviewer",
    "max_bound_input_chars",
)
_APP_FIELDS = ("id", "kind", "machine")
_APP_PARAMS = ("surface", "commit_author", "permissions")
_APP_CONNECTION = ("app_id", "installation_id", "private_key")
"""Not ``repos``: which repositories the App can reach is scope, not review integrity, and
guildmaster adds repos at provisioning (d18); a digest over it would block every push after
each new repo until a release. Every push still needs a trusted workflow, a genuine Codex
approval of the exact commit, and the fixer rules' own allow-list (``vars.fixer_repos``)."""


def _actor_projection(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of an actor that decide what it can review or push, normalised."""
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
