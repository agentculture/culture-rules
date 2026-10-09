"""The PR fixer's live status comment (d26): one comment per fix chain, edited as it moves.

This module **reads** a chain and **renders** its comment;
:mod:`culture_rules.node.status_board` owns the comment's record and every GitHub call.

**Opting in.** A chain opts in through its rules: a ``github.comment`` action or
``on_failure`` with ``params.status: true`` (:func:`status_actor`) names the App actor that
writes the chain's status comment. The shipped PR fixer rules all do.

**The chain.** Its **root** is the run an external event started (:func:`chain_root` walks
the ``rules.run.succeeded`` links back, each verified with
:func:`culture_rules.actors.lineage.upstream`); the comment is keyed by it, so a re-fix
writes into the same comment. A run counts as started once it is past its hold
(:func:`past_hold`: the built-in ``gitguardian.hold`` step succeeded, else its first
``wait`` step, else at once), so a run stopped by the quiet period or the GitGuardian hold
never posts.

**The comment** (:func:`render`): the final section (or :data:`HEADLINE`), what started the
chain, the stages of the current round (hold, agent, gate, review, push) and the earlier
rounds, the agent's status notes and last activity, its fix summary, the root's run link
and a hidden marker naming the root. Relayed text - notes, the summary, the chain-end
text - is untrusted: :mod:`culture_rules.apps.public_text` normalizes, checks (known secret
values included) and escapes it into inert Markdown. Engine facts (verdicts, SHAs, run ids,
codes, logins) are rendered only when they have their expected shape, and links and the
marker come from engine values only. The assembled body is checked once more as a whole
(:func:`_guard`): an untrusted section that, alone or with the rest, holds a secret becomes
``[withheld]``. Standard-library only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.apps.public_text import (
    WITHHELD,
    escape,
    inert_block,
    known_secret_in,
    withheld,
)

__all__ = [
    "EDIT_FLOOR_S",
    "IDLE_END",
    "NOTES_EVERY_S",
    "STATUS_COLLECTION",
    "STATUS_NOTES_KEPT",
    "STATUS_NOTE_HINT",
    "HEADLINE",
    "Chain",
    "Final",
    "chain_root",
    "digest",
    "marker_of",
    "notes_lines",
    "past_hold",
    "plain_final",
    "pr_of",
    "render",
    "run_link",
    "run_status_actor",
    "stage_lines",
    "status_actor",
]

log = logging.getLogger(__name__)

STATUS_COLLECTION = "fixer_status_comments"
"""One document per fix chain, keyed by the chain's root run id: the status comment."""
STATUS_NOTES_KEPT = 5
"""How many of the agent's latest status notes a bridge invocation keeps."""
EDIT_FLOOR_S = 5.0
"""The least time between two edits of one status comment."""
NOTES_EVERY_S = 60.0
"""The least time between two edits that only bring new agent notes."""
FINAL_CAP = 3000
"""The longest final section (the chain-end action's body), in characters."""
SUMMARY_CAP = 1200
"""The longest fix summary, in characters."""
HEADLINE = "**PR fixer is working on this PR.** This comment is updated as it goes."
IDLE_END = timedelta(minutes=15)
"""A chain whose runs all finished this long ago without a final edit is closed by the tick
(the chain hold's TTL: nothing continues it any more)."""
STATUS_NOTE_HINT = (
    "\n\nStatus notes (optional): to tell the people on this PR what you are doing, run the "
    'shell command `echo "STATUS: <one short sentence>"`. The engine shows the text after '
    "STATUS: in the PR's status comment (the latest few notes, at most one update a minute). "
    "Plain words only: links, mentions and markup are removed, and a note that looks like a "
    "secret is dropped. Never put tokens, keys or passwords in a note."
)
"""Appended to the instruction of an agent step whose run writes a status comment."""
PUBLIC_URL = os.environ.get("CULTURE_RULES_PUBLIC_URL", "https://rules.culture.dev").rstrip("/")
"""Where runs are linked from the comment (``<url>/api/runs/<id>``)."""
MARKER = "<!-- culture-rules:fixer-status {} -->"

_STATUS = "status"
_ACTOR = "actor"
_TRIGGER = "trigger"
_CREATED_AT = "created_at"
_COMMENT_ID = "comment_id"
_FINAL = "final"
_EDITS = "edits"
_STATE = "state"
_WAITING = "waiting"
_WORKING = "working"
_SUCCEEDED = "succeeded"
_FAILED = "failed"
_OUTPUTS = "outputs"
_ERROR = "error"
_NUMBER = "number"
_KEY = "concurrency_key"
_STAGE_SIG = "stage_sig"
_NOTES_SIG = "notes_sig"
_LAST_EDIT_AT = "last_edit_at"
_FINAL_BODY = "final_body"
_RUNS = "runs"  # culture_rules.engine.runs.RUNS_COLLECTION (no engine import cycle)
_BRIDGE = "bridge_invocations"  # culture_rules.actors.agent.BRIDGE_INVOCATIONS
_ACTIVE = "running"
_RUN_DONE = (_SUCCEEDED, _FAILED, "cancelled", "superseded")
_STEP_OPEN = ("dispatching", _WAITING, "blocked", "sleeping")
_HOLD_BUILTIN = "gitguardian.hold"
_GATE_BUILTIN = "gate"
_REVIEW_BUILTIN = "review"
_PUSH_KIND = "github.push"
_COMMENT_KIND = "github.comment"
FINISHED = "PR fixer finished."  # a final section with no text of its own

_WORD = re.compile(r"[a-z][a-z0-9_]{0,39}")
_SHA = re.compile(r"[0-9a-f]{7,64}")
_RUN_ID = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


# --------------------------------------------------------------------------- validated facts


def _word(value: Any) -> str | None:
    """A verdict, status or error code: a short lower-case word, else None."""
    return value if isinstance(value, str) and _WORD.fullmatch(value) else None


def _sha(value: Any, size: int = 12) -> str | None:
    return value[:size] if isinstance(value, str) and _SHA.fullmatch(value) else None


def _login(value: Any) -> str | None:
    return value if isinstance(value, str) and _LOGIN.fullmatch(value) else None


def run_link(run_id: Any) -> str:
    """The public link of a run (only for an id of the expected shape)."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        return ""
    return f"{PUBLIC_URL}/api/runs/{run_id}"


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def digest(text: str) -> str:
    """A short, stable digest of rendered text (what changed since the last edit)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- opting in


def status_actor(rule_def: Any) -> str | None:
    """The App actor of a rule that writes its chain's status comment: its ``github.comment``
    action or ``on_failure`` with ``params.status: true`` and a literal ``params.actor``."""
    if not isinstance(rule_def, Mapping):
        return None
    for key in ("action", "on_failure"):
        action = rule_def.get(key)
        if not isinstance(action, Mapping) or action.get("kind") != _COMMENT_KIND:
            continue
        params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
        actor = params.get(_ACTOR)
        if params.get(_STATUS) is True and isinstance(actor, str) and actor:
            return actor
    return None


def run_status_actor(run: Mapping[str, Any] | None) -> str | None:
    """:func:`status_actor` of the rule a run pinned."""
    pinned = (run or {}).get("rule") if isinstance(run, Mapping) else None
    return status_actor(pinned.get("definition") if isinstance(pinned, Mapping) else None)


def chain_root(store: Any, run: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The run an external event started, walking back the verified ``rules.run.succeeded``
    links of ``run``'s chain; ``None`` when a link does not verify or loops."""
    from culture_rules.actors.lineage import LineageError, upstream  # noqa: PLC0415
    from culture_rules.events.emit import MAX_EVENT_HOPS  # noqa: PLC0415

    current = run
    for _ in range(MAX_EVENT_HOPS + 1):
        trigger = current.get(_TRIGGER)
        kind = trigger.get("type") if isinstance(trigger, Mapping) else None
        if not (isinstance(kind, str) and kind.startswith("rules.run.")):
            return current
        try:
            current = upstream(store, current)
        except LineageError:
            return None
    return None


# --------------------------------------------------------------------------- run shapes


def _definition(run: Mapping[str, Any]) -> Mapping[str, Any]:
    pin = run.get("workflow")
    definition = pin.get("definition") if isinstance(pin, Mapping) else None
    return definition if isinstance(definition, Mapping) else {}


def _steps(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [s for s in _definition(run).get("steps") or () if isinstance(s, Mapping)]


def _config(step: Mapping[str, Any]) -> Mapping[str, Any]:
    config = step.get("config")
    return config if isinstance(config, Mapping) else {}


def _builtin(step: Mapping[str, Any]) -> Any:
    return _config(step).get("builtin")


def _state(run: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    found = next((s for s in run.get("steps") or () if s.get("key") == key), None)
    return found if isinstance(found, Mapping) else {}


def _outputs(state: Mapping[str, Any]) -> Mapping[str, Any]:
    out = state.get(_OUTPUTS)
    return out if isinstance(out, Mapping) else {}


def _agent_loop(run: Mapping[str, Any]) -> tuple[str, str, str, int] | None:
    """``(loop, agent, gate, tries)``: the run's ``retry_until`` loop holding an ``ai`` step
    and the built-in gate (a fix run), else None."""
    for step in _steps(run):
        if step.get("kind") != "retry_until":
            continue
        body = [b for b in step.get("body") or () if isinstance(b, Mapping)]
        agent = next((b for b in body if b.get("kind") == "ai"), None)
        gate = next((b for b in body if _builtin(b) == _GATE_BUILTIN), None)
        if agent and gate:
            tries = step.get("max_iterations")
            return str(step["id"]), str(agent["id"]), str(gate["id"]), tries or 1
    return None


def _step_where(run: Mapping[str, Any], test: Callable[[Mapping[str, Any]], bool]) -> str | None:
    found = next((s for s in _steps(run) if test(s)), None)
    return str(found["id"]) if found else None


def _review_step(run: Mapping[str, Any]) -> str | None:
    return _step_where(run, lambda s: _builtin(s) == _REVIEW_BUILTIN)


def _push_step(run: Mapping[str, Any]) -> str | None:
    def is_push(step: Mapping[str, Any]) -> bool:
        action = _config(step).get("action")
        return isinstance(action, Mapping) and action.get("kind") == _PUSH_KIND

    return _step_where(run, is_push)


def past_hold(run: Mapping[str, Any]) -> bool:
    """Whether ``run`` is past its hold (module doc): the built-in GitGuardian hold
    succeeded, else its first ``wait`` step, else at once."""
    hold = _step_where(run, lambda s: _builtin(s) == _HOLD_BUILTIN)
    hold = hold or _step_where(run, lambda s: s.get("kind") == "wait")
    return hold is None or _state(run, hold).get(_STATUS) == _SUCCEEDED


# --------------------------------------------------------------------------- the chain


@dataclass
class Chain:
    """One fix chain as the store has it: its root and its runs, oldest first."""

    root: Mapping[str, Any]
    runs: list[Mapping[str, Any]]
    notes: list[Mapping[str, Any]] = field(default_factory=list)
    last_activity: str | None = None

    @property
    def fixes(self) -> list[Mapping[str, Any]]:
        return [r for r in self.runs if _agent_loop(r)]

    def latest(self, test: Callable[[Mapping[str, Any]], Any]) -> Mapping[str, Any] | None:
        found = [r for r in self.runs if test(r)]
        return found[-1] if found else None

    @property
    def ended(self) -> bool:
        return all(r.get(_STATUS) in _RUN_DONE for r in self.runs)


# --------------------------------------------------------------------------- rendering

_SETTLED = "github.pr.checks_settled"
_TRIGGERS = {
    _SETTLED: "checks settled",
    "github.comment.created": "a comment",
    "github.review.submitted": "a review",
    "github.review_comment.created": "a review comment",
}
_STATE_WORDS = {_SUCCEEDED: "done", _FAILED: _FAILED, "skipped": "skipped"}
STAGES = ("Quiet period and GitGuardian hold", "Agent", "Test gate", "Review", "Push")


def _trigger_line(root: Mapping[str, Any]) -> str:
    trigger = root.get(_TRIGGER) if isinstance(root.get(_TRIGGER), Mapping) else {}
    data = trigger.get("data") if isinstance(trigger.get("data"), Mapping) else {}
    kind = str(trigger.get("type"))
    what = _TRIGGERS.get(kind, "an event")
    conclusion = _word(data.get("conclusion"))
    author = _login(data.get("author"))
    if kind == _SETTLED and conclusion:
        what += f" ({conclusion})"
    elif kind != _SETTLED and author:
        what += f" by {author}"
    sha = _sha(data.get("head_sha"))
    return f"Started by {what}" + (f" at `{sha}`" if sha else "") + "."


def _step_word(state: Mapping[str, Any], default: str = _WAITING) -> str:
    status = state.get(_STATUS)
    if status in _STEP_OPEN:
        return _WORKING
    return _STATE_WORDS.get(str(status), default)


def _hold_line(fix: Mapping[str, Any] | None) -> str:
    if fix is None:
        return _WAITING
    return "done" if past_hold(fix) else _step_word({_STATUS: "dispatching"})


def _fix_lines(fix: Mapping[str, Any] | None) -> tuple[str, str]:
    """The agent's and the gate's words for the latest try of ``fix``."""
    loop = _agent_loop(fix) if fix else None
    if fix is None or loop is None:
        return _WAITING, _WAITING
    name, agent, gate, tries = loop
    i = _state(fix, name).get("iteration")
    i = i if isinstance(i, int) and i >= 0 else 0
    agent_state = _state(fix, f"{name}[{i}]/{agent}")
    gate_state = _state(fix, f"{name}[{i}]/{gate}")
    agent_word = _step_word(agent_state)
    if agent_word == _WORKING:
        agent_word += f" (try {i + 1} of {tries})"
    verdict = _word(_outputs(gate_state).get("verdict"))
    gate_word = f"verdict {verdict}" if verdict else _step_word(gate_state)
    return agent_word, gate_word


def _review_line(review: Mapping[str, Any] | None) -> str:
    if review is None:
        return _WAITING
    out = _outputs(_state(review, _review_step(review) or ""))
    verdict = _word(out.get("review"))
    if verdict:
        findings = out.get("findings")
        count = len(findings) if isinstance(findings, list) else 0
        return f"{verdict} ({count} finding{'s' if count != 1 else ''})"
    return _run_word(review)


def _run_word(run: Mapping[str, Any]) -> str:
    status = run.get(_STATUS)
    if status == _ACTIVE:
        return _WORKING
    error = run.get(_ERROR) if isinstance(run.get(_ERROR), Mapping) else {}
    code = _word(error.get("code"))
    return f"{status} ({code})" if status == _FAILED and code else str(_word(status) or "?")


def _push_line(publish: Mapping[str, Any] | None) -> str:
    if publish is None:
        return _WAITING
    out = _outputs(_state(publish, _push_step(publish) or ""))
    head = _sha(out.get("head_after"))
    if out.get("pushed") is True and head:
        return f"pushed `{head}`"
    if out.get("pushed") is False and head:
        return f"nothing to push (head `{head}`)"
    return _run_word(publish)


def _ai_actor(run: Mapping[str, Any]) -> str | None:
    """The actor an ``ai`` step of ``run`` (top level or in a loop) is placed on."""
    for step in _steps(run):
        inner = [b for b in step.get("body") or () if isinstance(b, Mapping)]
        for candidate in (step, *inner):
            placement = candidate.get("placement")
            if candidate.get("kind") == "ai" and isinstance(placement, Mapping):
                return _login(placement.get(_ACTOR))
    return None


def _with_actor(label: str, run: Mapping[str, Any] | None) -> str:
    actor = _ai_actor(run) if run is not None else None
    return f"{label} ({actor})" if actor else label


def _rounds_line(chain: Chain) -> str:
    """The earlier fix rounds of a re-fixed chain, one phrase each."""
    reviews = [r for r in chain.runs if _review_step(r)]
    rounds = []
    for n, review in enumerate(reviews[: max(len(chain.fixes) - 1, 0)], start=1):
        rounds.append(f"round {n}: review {_review_line(review)}")
    return ("Earlier: " + "; ".join(rounds) + ".") if rounds else ""


def stage_lines(chain: Chain) -> list[str]:
    """The current round's stages, one ``- **state** stage`` line each."""
    fix = chain.latest(_agent_loop)
    review = chain.latest(_review_step)
    if review is not None and fix is not None and review[_CREATED_AT] < fix[_CREATED_AT]:
        review = None  # the review of an earlier round
    publish = chain.latest(_push_step)
    agent, gate = _fix_lines(fix)
    words = (_hold_line(fix), agent, gate, _review_line(review), _push_line(publish))
    labels = list(STAGES)
    labels[1] = _with_actor(labels[1], fix)
    labels[3] = _with_actor(labels[3], review)
    lines = [f"- **{word}** {stage}" for word, stage in zip(words, labels, strict=True)]
    earlier = _rounds_line(chain)
    return [*lines, "", earlier] if earlier else lines


def _hhmm(text: Any) -> str:
    moment = parse_time(text)
    return moment.strftime("%H:%M UTC") if moment else "?"


def notes_lines(chain: Chain) -> list[str]:
    """The agent's latest notes (stored normalized; escaped here), one line each."""
    return [f"- {_hhmm(n.get('at'))}: {escape(str(n.get('text') or ''))}" for n in chain.notes]


def _notes_sections(chain: Chain, known: frozenset[str]) -> list[tuple[str, bool]]:
    """The notes heading, the notes (untrusted; withheld whole when, together, they carry a
    secret: one split across notes) and the agent's last activity."""
    lines = notes_lines(chain)
    joined = " ".join(str(n.get("text") or "") for n in chain.notes)
    out: list[tuple[str, bool]] = []
    if lines:
        text = WITHHELD if withheld(joined, known) else "\n".join(lines)
        out += [("**Agent notes**", False), (text, True)]
    if chain.last_activity:
        out.append((f"Last agent activity: {_hhmm(chain.last_activity)}.", False))
    return out


def _summary(chain: Chain, known: frozenset[str]) -> str:
    fix = chain.latest(lambda r: r.get(_STATUS) == _SUCCEEDED and _agent_loop(r))
    outputs = fix.get(_OUTPUTS) if fix and isinstance(fix.get(_OUTPUTS), Mapping) else {}
    return inert_block(outputs.get("summary"), SUMMARY_CAP, known)


@dataclass(frozen=True)
class Final:
    """A chain's final section: ``text`` (untrusted: the chain-end action's rendered body,
    or the engine's closing words) and the run it reports, linked by the engine."""

    text: str
    run_id: str | None = None


def _guard(
    sections: list[tuple[str, bool]],
    known: frozenset[str],
    fallback: Callable[[], str],
) -> str:
    """The body, checked as a whole right before it is sent (module doc).

    Untrusted sections get every check (heuristics and known secrets), alone and together;
    one that fails is ``[withheld]``. Engine-validated facts (logins, SHAs, run ids,
    verdicts) only get the known-secret check: a valid login that looks random is no
    secret. A body whose engine facts still hold a known secret is replaced by
    ``fallback()``, built from facts that cannot."""
    parts = [(WITHHELD if bad and withheld(text, known) else text, bad) for text, bad in sections]
    untrusted = "\n\n".join(text for text, bad in parts if bad)
    if withheld(untrusted, known):
        parts = [(WITHHELD if bad else text, bad) for text, bad in parts]
    body = "\n\n".join(text for text, _ in parts)
    return fallback() if known_secret_in(body, known) else body


def _outcome(chain: Chain) -> str:
    """The chain's end in one validated word, for a fallback final."""
    last = chain.runs[-1]
    status = last.get(_STATUS)
    if status in ("cancelled", "superseded"):
        return "stopped"
    if status == _FAILED:
        return "handed back"
    if _push_step(last):
        return "pushed"
    if _review_step(last):
        return "reviewed (review-only)"
    return "finished"


def _fallback(chain: Chain, final: Final | None, root_id: str) -> str:
    """A body built only from engine values that hold no secret: for a final, it still
    reads final (outcome and run link), never the working headline."""
    if final is None:
        return "\n\n".join((HEADLINE, MARKER.format(root_id)))
    parts = [f"**PR fixer finished**: {_outcome(chain)}."]
    link = run_link(final.run_id)
    if link:
        parts.append(f"Run: {link}")
    return "\n\n".join((*parts, MARKER.format(root_id)))


def render(chain: Chain, final: Final | None = None, known: Iterable[str] = ()) -> str:
    """The whole status comment (module doc): the final section (or the headline), the
    trigger and stages, the agent's notes, its fix summary, the run link and a marker.
    Untrusted text is escaped and checked; links and the marker come from engine values."""
    known = frozenset(known)
    root_id = str(chain.root.get("id"))
    root_id = root_id if _RUN_ID.fullmatch(root_id) else "?"
    sections: list[tuple[str, bool]] = []
    if final is not None:
        sections.append((inert_block(final.text, FINAL_CAP, known) or FINISHED, True))
        link = run_link(final.run_id)
        sections += [(f"Run: {link}", False)] if link else []
    else:
        sections.append((HEADLINE, False))
    sections += [
        ("**PR fixer status**", False),
        (_trigger_line(chain.root), False),
        ("\n".join(stage_lines(chain)), False),
    ]
    if final is None:
        sections += _notes_sections(chain, known)
    summary = _summary(chain, known)
    if summary:
        sections += [("**Fix summary**", False), (summary, True)]
    link = run_link(root_id)
    if link:
        sections.append((f"Chain started with run: {link}", False))
    sections.append((MARKER.format(root_id), False))
    return _guard(sections, known, lambda: _fallback(chain, final, root_id))


def marker_of(root_id: str) -> str:
    """The hidden marker naming a chain's root in its status comment."""
    return MARKER.format(root_id)


def pr_of(root: Mapping[str, Any]) -> tuple[str, int] | None:
    """The repository and PR number of a chain's root run (validated), else None."""
    trigger = root.get(_TRIGGER) if isinstance(root.get(_TRIGGER), Mapping) else {}
    data = trigger.get("data") if isinstance(trigger.get("data"), Mapping) else {}
    repo, number = data.get("repository"), data.get(_NUMBER)
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        return None
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    return repo, number


def plain_final(text: Any, run_id: Any, known: Iterable[str] = ()) -> str:
    """A chain-end text outside a status chain: the text inert, then the engine's run link
    (the same checks as a status comment)."""
    known = frozenset(known)
    sections = [(inert_block(text, FINAL_CAP, known) or FINISHED, True)]
    link = run_link(run_id)
    if link:
        sections.append((f"Run: {link}", False))
    return _guard(sections, known, lambda: FINISHED)
