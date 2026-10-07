"""The PR fixer's agent review (d20): a built-in ``review`` code step and push's check.

After the test gate passes, a reviewer agent (``codex-reviewer``: Codex through the
cultureagent codex bridge, sandbox ``read-only``) reviews the fix commit. This module reads
what it said, deterministically, and records the result where ``github.push`` checks it.

The reviewer's verdict
======================

The bridge hands back only the ``summary`` string of the agent's final message, so the
reviewer writes its verdict object *as* that string (the reviewer brief in the workflow
says how). :func:`parse_review` accepts exactly one JSON object (optionally inside one
closing json code fence) and nothing else::

    {"verdict": "approve" | "request_changes",
     "findings": [{"path": str, "line": int | null, "severity": str, "detail": str}],
     "reviewed_commit": "<full sha>"}

``severity`` is one of :data:`SEVERITIES`. Fail closed, always: prose around the object,
two objects, duplicate keys, a missing or mistyped field, another verdict word, a short or
other SHA, more than :data:`MAX_FINDINGS` findings - each is ``review_invalid`` (another
commit is ``review_commit_mismatch``), never an approval. An ``approve`` that carries a
``critical`` or ``high`` finding contradicts itself and is read as ``request_changes``; a
``request_changes`` with no finding says nothing to fix and is ``review_invalid``.

The built-in ``review`` step
============================

It runs as the last step of the fixer's ``retry_until`` body, so its outputs are the
loop's result (``until`` and ``carry`` read them). It takes nothing that matters for safety
from wired inputs: it reads the run document in the store for its own iteration - the
``gate`` step's outputs, the reviewer step's inputs and outputs and the implementer step's
outputs (step ids in ``config.gate_step``, ``config.review_step``,
``config.implementer_step``; defaults ``gate``, ``review``, ``agent``) - and the actors from
the pinned workflow's placements and the ``actors`` collection. Its only wired input,
``task`` (the original instruction), is prose for the next attempt.

* gate verdict not ``pass``/``no_gate``: ``review`` is ``not_run`` and the gate's own
  ``instruction`` goes on to the next attempt;
* the gate's diff was cut at its size cap (``diff_truncated``): the reviewer never saw all
  of it, so ``review`` is ``request_changes`` with one finding asking for a smaller fix;
* otherwise the reviewer step must have run on exactly the gate's ``commit_sha``,
  ``start_sha`` and ``diff``, as an actor and backend other than the implementer's, with
  sandbox ``read-only``, changing nothing (bridge status ``no_changes``, no commits, not
  dirty, head unmoved), and its summary must parse.

Outputs: ``verdict`` (the gate's), ``review`` (``approve`` / ``request_changes`` /
``not_run``), ``findings``, ``reviewed_commit`` and ``instruction`` (the next attempt's:
the gate's text, or the findings plus the original task; none on approval). Anything else
fails the step - and so the run, which hands back - with ``code: detail``:
``review_missing``, ``review_invalid``, ``review_commit_mismatch``,
``reviewer_not_read_only``, ``reviewer_is_implementer``, ``gate_missing``, ``bad_config``,
``run_not_found``.

Every outcome, failures included, overwrites the run's one record in
:data:`REVIEWS_COLLECTION` (document id = run id), so an older approval never outlives a
newer result.

``github.push``'s check
=======================

:func:`review_refusal` is called by the push port for **every** push, whatever the workflow
wires: the run's record must exist (``review_missing``), say ``approve``
(``review_rejected``), name exactly the commit being pushed as both the commit asked about
and the commit reviewed (``review_commit_mismatch``), and show a reviewer whose actor id and
backend both differ from the implementer's (``reviewer_is_implementer``). A workflow edited
to drop the review therefore cannot push. Standard-library only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = [
    "APPROVE",
    "MAX_FINDINGS",
    "NOT_RUN",
    "REQUEST_CHANGES",
    "REVIEWS_COLLECTION",
    "REVIEW_BUILTIN",
    "SEVERITIES",
    "ReviewError",
    "ReviewVerdictPort",
    "parse_review",
    "review_refusal",
]

REVIEW_BUILTIN = "review"
"""``config.builtin`` of the code step that reads the reviewer's verdict."""
REVIEWS_COLLECTION = "fixer_reviews"
"""One document per run (id = run id): the latest review outcome, read by ``github.push``."""

APPROVE, REQUEST_CHANGES, NOT_RUN = "approve", "request_changes", "not_run"
VERDICTS = (APPROVE, REQUEST_CHANGES)
SEVERITIES = ("critical", "high", "medium", "low", "info")
BLOCKING = frozenset({"critical", "high"})
MAX_FINDINGS = 50
READ_ONLY = "read-only"
NO_CHANGES = "no_changes"
_PASSING_GATE = ("pass", "no_gate")

_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_FENCE_RE = re.compile(r"^```json[ \t]*\n(?P<body>.*)\n```$", re.DOTALL)
_LOOP_KEY_RE = re.compile(r"^(?P<parent>[^\[\]/]+)\[(?P<i>\d+)\]/(?P<step>[^/]+)$")
_RUNS = "runs"  # culture_rules.engine.runs.RUNS_COLLECTION (not imported: no engine cycle)
_ACTORS = "actors"  # culture_rules.node.actors.ACTORS_COLLECTION
_KEEP = (
    "Do not delete, skip or weaken tests, and do not edit CI, lint, coverage or Sonar "
    "configuration or add suppression markers."
)


class ReviewError(Exception):
    """Not an approval: ``code`` (with ``detail``) becomes the step error."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- parsing


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ReviewError("review_invalid", "the verdict object repeats a key")
    return dict(pairs)


def _object(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, str) or not summary.strip():
        raise ReviewError("review_invalid", "the reviewer returned no verdict text")
    text = summary.strip()
    fenced = _FENCE_RE.match(text)
    if fenced is not None:
        text = fenced.group("body").strip()
    try:
        doc = json.loads(text, object_pairs_hook=_no_duplicates)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise ReviewError("review_invalid", "the verdict is not exactly one JSON object") from exc
    if not isinstance(doc, dict):
        raise ReviewError("review_invalid", "the verdict is not a JSON object")
    return doc


def _finding(raw: Any, n: int) -> dict[str, Any]:
    where = f"finding {n}"
    if not isinstance(raw, dict):
        raise ReviewError("review_invalid", f"{where} is not an object")
    path, line = raw.get("path"), raw.get("line")
    severity, detail = raw.get("severity"), raw.get("detail")
    if not isinstance(path, str):
        raise ReviewError("review_invalid", f"{where}: path must be a string")
    if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 0):
        raise ReviewError("review_invalid", f"{where}: line must be a non-negative integer or null")
    if severity not in SEVERITIES:
        raise ReviewError("review_invalid", f"{where}: severity must be one of {SEVERITIES}")
    if not isinstance(detail, str) or not detail.strip():
        raise ReviewError("review_invalid", f"{where}: detail must be non-empty text")
    return {"path": path, "line": line, "severity": severity, "detail": detail}


def parse_review(summary: Any, commit_sha: str) -> dict[str, Any]:
    """The reviewer's verdict on ``commit_sha`` from its summary text (see the module doc).

    Returns ``{verdict, findings, reviewed_commit}``; raises :class:`ReviewError`
    (``review_invalid`` / ``review_commit_mismatch``) for anything that is not a clear
    verdict on exactly that commit."""
    if not isinstance(commit_sha, str) or not _SHA_RE.match(commit_sha):
        raise ReviewError("review_invalid", "the commit to review is not a full SHA")
    doc = _object(summary)
    for name in ("verdict", "findings", "reviewed_commit"):
        if name not in doc:
            raise ReviewError("review_invalid", f"the verdict object has no {name!r}")
    verdict, raw, reviewed = doc["verdict"], doc["findings"], doc["reviewed_commit"]
    if verdict not in VERDICTS:
        raise ReviewError("review_invalid", f"verdict must be one of {VERDICTS}")
    if not isinstance(raw, list):
        raise ReviewError("review_invalid", "findings must be a list")
    if len(raw) > MAX_FINDINGS:
        raise ReviewError("review_invalid", f"more than {MAX_FINDINGS} findings")
    if not isinstance(reviewed, str) or not _SHA_RE.match(reviewed):
        raise ReviewError("review_invalid", "reviewed_commit must be a full lowercase SHA")
    if reviewed != commit_sha:
        raise ReviewError(
            "review_commit_mismatch",
            f"the reviewer reviewed {reviewed[:12]}, not {commit_sha[:12]}",
        )
    findings = [_finding(f, n) for n, f in enumerate(raw, start=1)]
    if verdict == APPROVE and any(f["severity"] in BLOCKING for f in findings):
        verdict = REQUEST_CHANGES  # an approval with a blocking finding contradicts itself
    if verdict == REQUEST_CHANGES and not findings:
        raise ReviewError("review_invalid", "request_changes names no finding")
    return {"verdict": verdict, "findings": findings, "reviewed_commit": reviewed}


# --------------------------------------------------------------------------- push's check


def _distinct(a: Any, b: Any) -> bool:
    """Both known (non-empty strings) and different, case-insensitively."""
    return (
        isinstance(a, str)
        and isinstance(b, str)
        and bool(a.strip())
        and bool(b.strip())
        and a.strip().casefold() != b.strip().casefold()
    )


def review_refusal(store: Any, run_id: str, commit_sha: str) -> str | None:
    """Why ``github.push`` may not push ``commit_sha`` for run ``run_id``, or ``None``.

    Reads the run's review record (written only by the built-in ``review`` step), never a
    param, so it holds whatever the workflow wires: ``review_missing``,
    ``review_rejected``, ``review_commit_mismatch``, ``reviewer_is_implementer``."""
    doc = store.get(REVIEWS_COLLECTION, run_id) if isinstance(run_id, str) and run_id else None
    if not doc or doc.get("run_id") != run_id:
        return "review_missing"
    if doc.get("verdict") != APPROVE:
        return "review_rejected"
    if doc.get("commit_sha") != commit_sha or doc.get("reviewed_commit") != commit_sha:
        return "review_commit_mismatch"
    if not _distinct(doc.get("reviewer_actor"), doc.get("implementer_actor")):
        return "reviewer_is_implementer"
    if not _distinct(doc.get("reviewer_backend"), doc.get("implementer_backend")):
        return "reviewer_is_implementer"
    return None


# --------------------------------------------------------------------------- the step


def _state(run: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    return next((s for s in run.get("steps", ()) if s.get("key") == key), None)


def _body_steps(run: Mapping[str, Any], parent: str) -> dict[str, Any]:
    """The pinned workflow's body steps of loop ``parent``, by id (raw dicts)."""
    definition = ((run.get("workflow") or {}).get("definition")) or {}
    for s in definition.get("steps") or ():
        if isinstance(s, Mapping) and s.get("id") == parent:
            return {b.get("id"): b for b in s.get("body") or () if isinstance(b, Mapping)}
    return {}


def _placed_actor(step: Mapping[str, Any] | None) -> str | None:
    placement = (step or {}).get("placement") or {}
    actor = placement.get("actor") if isinstance(placement, Mapping) else None
    return actor if isinstance(actor, str) and actor else None


def _backend(reported: Any, declared: Any) -> str | None:
    """The backend the bridge reported, checked against the actor's declared harness."""
    rep = reported.strip().casefold() if isinstance(reported, str) and reported.strip() else None
    dec = declared.strip().casefold() if isinstance(declared, str) and declared.strip() else None
    if rep and dec and rep != dec:
        raise ReviewError(
            "review_invalid", f"the bridge reported backend {rep!r} for a {dec!r} actor"
        )
    return rep or dec


def _unreviewable(g: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Findings for a change the reviewer cannot be shown in full (too large, or not text):
    never reviewed, the next attempt asks for a smaller, text-only fix."""
    problems = g.get("diff_problems")
    if not isinstance(problems, list) or not problems:
        problems = [f"the diff is {g.get('diff_chars')} characters, over the reviewer's limit"]
    ask = (
        "The reviewer cannot be shown this change in full. Make a smaller, text-only fix: "
        "no binary files, file mode changes, symlinks or submodule pointers."
    )
    return [
        {"path": "", "line": None, "severity": "high", "detail": f"{p}. {ask}"}
        for p in problems[:MAX_FINDINGS]
        if isinstance(p, str)
    ] or [{"path": "", "line": None, "severity": "high", "detail": ask}]


def _finding_line(f: Mapping[str, Any]) -> str:
    where = f["path"] + (f":{f['line']}" if f.get("line") is not None else "")
    return f"- [{f['severity']}] {where} {f['detail']}".replace("  ", " ").rstrip()


def _changes_instruction(commit: str, findings: list[dict[str, Any]], task: Any) -> str:
    lines = "\n".join(_finding_line(f) for f in findings)
    text = (
        f"An independent reviewer requested changes to your previous attempt (commit "
        f"{commit[:12]}). That commit was not pushed and is discarded: start again from the "
        f"PR head, redo the fix and address every finding below. {_KEEP}\n\n"
        f"Findings:\n{lines}"
    )
    if isinstance(task, str) and task.strip():
        text += f"\n\nThe original task:\n{task.strip()}"
    return text


class ReviewVerdictPort:
    """ActorPort for the built-in ``review`` code step (see the module docstring)."""

    supports_idempotency_key = True  # re-reading the same run state gives the same answer

    def __init__(self, store: Any, *, clock: Any = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        ensure = getattr(store, "ensure_collections", None)
        if callable(ensure):
            ensure(REVIEWS_COLLECTION)

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        facts: dict[str, Any] = {}
        try:
            out = self._judge(input, context, facts)
        except ReviewError as exc:
            self._record(context, {**facts, "verdict": exc.code, "error": str(exc)})
            return InvocationResult.failed(str(exc), retryable=False)
        return InvocationResult.completed(out)

    # ------------------------------------------------------------------ helpers

    def _record(self, context: InvocationContext, fields: Mapping[str, Any]) -> None:
        """Overwrite the run's one review record (an older approval never outlives this)."""
        self._store.put(
            REVIEWS_COLLECTION,
            {
                "id": context.run_id,
                "run_id": context.run_id,
                "step": context.step_id,
                "commit_sha": None,
                "reviewed_commit": None,
                "findings": [],
                "reviewer_actor": None,
                "reviewer_backend": None,
                "implementer_actor": None,
                "implementer_backend": None,
                **fields,
                "recorded_at": self._clock().isoformat(),
            },
        )

    def _actor(self, actor_id: str | None, role: str) -> Mapping[str, Any]:
        doc = self._store.get(_ACTORS, actor_id) if actor_id else None
        if not doc or doc.get("deleted_at") or doc.get("enabled") is False:
            raise ReviewError("review_invalid", f"the {role} actor {actor_id!r} is not available")
        return doc

    # ------------------------------------------------------------------ the judgement

    def _judge(
        self, input: Mapping[str, Any], context: InvocationContext, facts: dict[str, Any]
    ) -> dict[str, Any]:
        where = _LOOP_KEY_RE.match(context.step_id or "")
        if where is None:
            raise ReviewError("bad_config", "the review step runs inside the fix loop")
        config = context.config or {}
        names = {
            role: config.get(f"{role}_step", default)
            for role, default in (("gate", "gate"), ("review", "review"), ("implementer", "agent"))
        }
        if not all(isinstance(n, str) and n for n in names.values()):
            raise ReviewError("bad_config", "gate_step, review_step and implementer_step")
        run = self._store.get(_RUNS, context.run_id)
        if not run:
            raise ReviewError("run_not_found", context.run_id)
        parent, i = where.group("parent"), int(where.group("i"))

        def state(role: str) -> dict[str, Any] | None:
            return _state(run, f"{parent}[{i}]/{names[role]}")

        gate = state("gate")
        if not gate or gate.get("status") != "succeeded":
            raise ReviewError("gate_missing", "the gate step of this attempt did not succeed")
        g = gate.get("outputs") or {}
        gate_verdict = g.get("verdict")
        out: dict[str, Any] = {
            "verdict": gate_verdict,
            "review": NOT_RUN,
            "findings": [],
            "reviewed_commit": None,
            "instruction": g.get("instruction"),
        }
        facts["gate_verdict"] = gate_verdict
        if gate_verdict not in _PASSING_GATE:
            self._record(context, {**facts, "verdict": NOT_RUN})
            return out
        commit, start = g.get("commit_sha"), g.get("start_sha")
        if not (isinstance(commit, str) and _SHA_RE.match(commit)):
            raise ReviewError("review_invalid", "the gate reported no commit_sha")
        if not (isinstance(start, str) and _SHA_RE.match(start)):
            raise ReviewError("review_invalid", "the gate reported no start_sha")
        facts["commit_sha"] = commit
        task = input.get("task")
        if g.get("diff_truncated") is True:
            findings = _unreviewable(g)
            self._record(context, {**facts, "verdict": REQUEST_CHANGES, "findings": findings})
            return {
                **out,
                "review": REQUEST_CHANGES,
                "findings": findings,
                "instruction": _changes_instruction(commit, findings, task),
            }
        if g.get("diff_truncated") is not False or not isinstance(g.get("diff"), str):
            raise ReviewError("review_invalid", "the gate did not report the diff it verified")
        parsed = self._review(run, parent, names, state, g, facts)
        self._record(context, {**facts, **parsed})
        approved = parsed["verdict"] == APPROVE
        return {
            **out,
            "review": parsed["verdict"],
            "findings": parsed["findings"],
            "reviewed_commit": parsed["reviewed_commit"],
            "instruction": (
                None if approved else _changes_instruction(commit, parsed["findings"], task)
            ),
        }

    def _review(
        self,
        run: Mapping[str, Any],
        parent: str,
        names: Mapping[str, str],
        state: Any,
        g: Mapping[str, Any],
        facts: dict[str, Any],
    ) -> dict[str, Any]:
        commit, start = g["commit_sha"], g["start_sha"]
        review = state("review")
        if not review or review.get("status") != "succeeded":
            raise ReviewError("review_missing", "the reviewer did not review this commit")
        given = review.get("inputs") or {}
        if (
            given.get("commit_sha") != commit
            or given.get("head_sha") != start
            or given.get("diff") != g.get("diff")
            or given.get("diff_truncated") is not False
        ):
            raise ReviewError(
                "review_invalid", "the reviewer was not given the gate's commit and diff"
            )
        body = _body_steps(run, parent)
        review_def, impl_def = body.get(names["review"]), body.get(names["implementer"])
        reviewer_id, implementer_id = _placed_actor(review_def), _placed_actor(impl_def)
        facts.update(reviewer_actor=reviewer_id, implementer_actor=implementer_id)
        reviewer = self._actor(reviewer_id, "reviewer")
        implementer = self._actor(implementer_id, "implementer")
        step_sandbox = ((review_def or {}).get("config") or {}).get("sandbox")
        if (reviewer.get("params") or {}).get("sandbox") != READ_ONLY or step_sandbox not in (
            None,
            READ_ONLY,
        ):
            raise ReviewError("reviewer_not_read_only", f"{reviewer_id} is not read-only")
        r = review.get("outputs") or {}
        impl_state = state("implementer") or {}
        reviewer_backend = _backend(r.get("backend"), reviewer.get("harness"))
        implementer_backend = _backend(
            (impl_state.get("outputs") or {}).get("backend"), implementer.get("harness")
        )
        facts.update(reviewer_backend=reviewer_backend, implementer_backend=implementer_backend)
        if not _distinct(reviewer_id, implementer_id) or not _distinct(
            reviewer_backend, implementer_backend
        ):
            raise ReviewError(
                "reviewer_is_implementer",
                f"reviewer {reviewer_id}/{reviewer_backend} vs implementer "
                f"{implementer_id}/{implementer_backend}",
            )
        status = r.get("status")
        changed = status in ("completed", "uncommitted")  # the bridge saw commits or edits
        wrote = changed or bool(r.get("commits")) or r.get("dirty") is not False
        moved = r.get("head_before") != start or r.get("head_after") != start
        if wrote or moved:
            raise ReviewError("reviewer_not_read_only", "the reviewer changed its checkout")
        if status != NO_CHANGES:
            raise ReviewError("review_invalid", f"the reviewer's session ended {status!r}")
        return parse_review(r.get("summary"), commit)
