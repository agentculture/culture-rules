"""The PR fixer's live status comment (d26): one comment per fix chain, edited as it moves.

A chain opts in through its rules: a ``github.comment`` action or ``on_failure`` with
``params.status: true`` (:func:`status_actor`) names the App actor that writes the chain's
status comment. The shipped PR fixer rules all do.

**Start.** Each node cycle, the node on the App actor's machine (:meth:`StatusBoard.tick`)
looks at the running runs. The first run of an opted-in chain that is past its hold - the
built-in ``gitguardian.hold`` step succeeded, else its first ``wait`` step, else at once -
posts the chain's status comment as the App: what started it (checks settled, a comment,
a review, with the head SHA), the run link and the stage list. A run stopped by the quiet
period or the GitGuardian hold therefore never posts. The comment is recorded in
:data:`STATUS_COLLECTION`, keyed by the chain's **root**: the run an external event started
(:func:`chain_root` walks ``rules.run.succeeded`` links back, each verified with
:func:`culture_rules.actors.lineage.upstream`). The claim is an insert, so a chain posts at
most once; a re-fix in the same chain finds the same comment.

**Live edits.** The same tick re-renders every open chain (agent working, with its latest
status notes; gate verdict; review verdict and findings count; pushed head or the
hand-back) and edits the comment (``PATCH /repos/{o}/{r}/issues/comments/{id}``) when the
stage list changed - at most every :data:`EDIT_FLOOR_S` - or when only the notes changed -
at most every :data:`NOTES_EVERY_S`. A deleted comment (404) is posted afresh and the
record follows it.

**The end.** The chain-end action (``status: true``, see
:class:`~culture_rules.node.actions.github.GitHubCommentPort`) calls
:meth:`StatusBoard.finish`: its rendered body becomes the comment's final section, above the
final stage list and the agent's fix summary, edited in at once (after at most the floor's
remainder). No other comment is posted. A chain that ends without one (cancelled,
superseded, or idle for :data:`IDLE_END`) is closed by the tick.

Everything relayed from the agent or a bridge goes through
:mod:`culture_rules.apps.public_text`; engine facts (verdicts, SHAs, run ids, codes) are
rendered only when they match their expected shape. Standard-library only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.apps.public_text import clean_block
from culture_rules.store.port import DuplicateKeyError

__all__ = [
    "EDIT_FLOOR_S",
    "IDLE_END",
    "NOTES_EVERY_S",
    "STATUS_COLLECTION",
    "STATUS_NOTES_KEPT",
    "STATUS_NOTE_HINT",
    "Chain",
    "StatusBoard",
    "chain_root",
    "render",
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
_POSTED, _POSTING, _UNKNOWN = "posted", "posting", "unknown"

_WORD = re.compile(r"[a-z][a-z0-9_]{0,39}")
_SHA = re.compile(r"[0-9a-f]{7,64}")
_RUN_ID = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
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


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _digest(text: str) -> str:
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
    moment = _parse(text)
    return moment.strftime("%H:%M UTC") if moment else "?"


def notes_lines(chain: Chain) -> list[str]:
    """The agent's latest notes (already cleaned when stored) and its last activity."""
    lines = [f"- {_hhmm(n.get('at'))}: {n.get('text')}" for n in chain.notes]
    if chain.last_activity:
        lines += ["", f"Last agent activity: {_hhmm(chain.last_activity)}."]
    return ["**Agent notes**", "", *lines] if lines else []


def _summary(chain: Chain) -> str:
    fix = chain.latest(lambda r: r.get(_STATUS) == _SUCCEEDED and _agent_loop(r))
    outputs = fix.get(_OUTPUTS) if fix and isinstance(fix.get(_OUTPUTS), Mapping) else {}
    return clean_block(outputs.get("summary"), 1200, keep_urls=False)


def render(chain: Chain, final: str | None = None) -> str:
    """The whole status comment (module doc): the final section (or a headline), the
    trigger and stages, the agent's notes, its fix summary, the run link and a marker."""
    root_id = str(chain.root.get("id"))
    head = final or "**PR fixer is working on this PR.** This comment is updated as it goes."
    parts = [head, "", "**PR fixer status**", "", _trigger_line(chain.root), ""]
    parts += stage_lines(chain)
    notes = notes_lines(chain) if not final else []
    if notes:
        parts += ["", *notes]
    summary = _summary(chain)
    if summary:
        parts += ["", "**Fix summary**", "", summary]
    link = run_link(root_id)
    if link:
        parts += ["", f"Chain started with run: {link}"]
    parts += ["", MARKER.format(root_id if _RUN_ID.fullmatch(root_id) else "?")]
    return "\n".join(parts)


# --------------------------------------------------------------------------- the board

AppLookup = Callable[[str, str], Any]
"""``(actor_id, repo) -> GitHubApp`` (raises ``GitHubError`` when it cannot serve)."""


@dataclass(frozen=True)
class Target:
    """Where a chain's comment lives: the App actor, the repository and the PR."""

    actor: str
    repo: str
    number: int


def _target(root: Mapping[str, Any], actor: str) -> Target | None:
    trigger = root.get(_TRIGGER) if isinstance(root.get(_TRIGGER), Mapping) else {}
    data = trigger.get("data") if isinstance(trigger.get("data"), Mapping) else {}
    repo, number = data.get("repository"), data.get(_NUMBER)
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        return None
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    return Target(actor, repo, number)


def _same_pr(target: Target, where: tuple[str, int] | None) -> bool:
    if where is None:
        return True
    repo, number = where
    return target.repo.lower() == str(repo).lower() and target.number == number


class StatusBoard:
    """Posts and edits the status comments (module doc). One per process: the reporter's
    tick and the chain-end action share its lock, so their edits never interleave here."""

    def __init__(
        self,
        store: Any,
        *,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        floor_s: float = EDIT_FLOOR_S,
        notes_every_s: float = NOTES_EVERY_S,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._floor = floor_s
        self._notes_every = notes_every_s
        self._lock = threading.RLock()
        self._roots: dict[str, str] = {}

    # ------------------------------------------------------------------ reading

    def root_of(self, run: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """:func:`chain_root`, remembered per run (a run's root never changes)."""
        rid = run.get("id")
        known = self._roots.get(rid) if isinstance(rid, str) else None
        if known is not None:
            return self._store.get(_RUNS, known)
        root = chain_root(self._store, run)
        if root is not None and isinstance(rid, str):
            if len(self._roots) > 10_000:
                self._roots.clear()
            self._roots[rid] = str(root.get("id"))
        return root

    def chain(self, root: Mapping[str, Any]) -> Chain:
        """The chain of ``root``: the runs on its key since it, whose root it is."""
        key = root.get(_KEY)
        since = str(root.get(_CREATED_AT) or "")
        found = self._store.find(_RUNS, {_KEY: key}) if key else [root]
        runs = [
            r
            for r in found
            if str(r.get(_CREATED_AT) or "") >= since
            and (r.get("id") == root.get("id") or self._roots_to(r, root))
        ]
        runs.sort(key=lambda r: (str(r.get(_CREATED_AT) or ""), str(r.get("id"))))
        chain = Chain(root=root, runs=runs or [root])
        self._notes(chain)
        return chain

    def _roots_to(self, run: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
        found = self.root_of(run)
        return found is not None and found.get("id") == root.get("id")

    def _notes(self, chain: Chain) -> None:
        """The latest fix run's agent notes and last activity (its bridge invocations)."""
        fix = chain.latest(_agent_loop)
        if fix is None:
            return
        docs = self._store.find(_BRIDGE, {"run_id": fix.get("id")})
        docs.sort(key=lambda d: (d.get("attempt") or 0, str(d.get(_CREATED_AT) or "")))
        notes = [n for d in docs for n in d.get("status_notes") or () if isinstance(n, Mapping)]
        chain.notes = notes[-STATUS_NOTES_KEPT:]
        times = [d.get("last_event_at") for d in docs if isinstance(d.get("last_event_at"), str)]
        chain.last_activity = max(times) if times else None

    # ------------------------------------------------------------------ the tick

    def tick(self, apps: AppLookup, serves: Callable[[str], bool]) -> int:
        """Start the comments of chains past their hold, refresh the open ones; return how
        many comments were posted or edited. ``serves(actor_id)`` says whether this node
        may act as that App actor (it lives on this machine, or on none)."""
        done = 0
        with self._lock:
            for run in self._store.find(_RUNS, {_STATUS: _ACTIVE}):
                done += self._start(apps, serves, run)
            for doc in self._store.find(STATUS_COLLECTION, {_FINAL: False}):
                done += self._refresh(apps, serves, doc)
        return done

    def _start(self, apps: AppLookup, serves: Callable[[str], bool], run: Mapping) -> int:
        actor = run_status_actor(run)
        if actor is None or not serves(actor) or not past_hold(run):
            return 0
        root = self.root_of(run)
        if root is None or self._store.get(STATUS_COLLECTION, str(root.get("id"))):
            return 0
        target = _target(root, actor)
        claim = self._claim(root, target) if target is not None else None
        if claim is None:
            return 0
        chain = self.chain(root)
        return 0 if self._post(apps, claim, render(chain), self._sigs(chain)) else 1

    def _claim(self, root: Mapping[str, Any], target: Target) -> dict[str, Any] | None:
        doc = {
            "id": str(root.get("id")),
            "repo": target.repo,
            _NUMBER: target.number,
            _ACTOR: target.actor,
            _KEY: root.get(_KEY),
            _STATE: _POSTING,
            _FINAL: False,
            _COMMENT_ID: None,
            "url": None,
            _EDITS: 0,
            _CREATED_AT: _iso(self._clock()),
        }
        try:
            return self._store.insert(STATUS_COLLECTION, doc)
        except DuplicateKeyError:
            return None

    @staticmethod
    def _sigs(chain: Chain) -> dict[str, str]:
        return {
            _STAGE_SIG: _digest("\n".join(stage_lines(chain))),
            _NOTES_SIG: _digest("\n".join(notes_lines(chain))),
        }

    def _changes(self, doc: Mapping[str, Any], changes: Mapping[str, Any]) -> None:
        """Merge ``changes`` into the record (compare-and-set on its edit count)."""
        current = self._store.get(STATUS_COLLECTION, doc["id"]) or {}
        expected = {_EDITS: current.get(_EDITS, 0)}
        merged = {**changes, _EDITS: current.get(_EDITS, 0) + 1}
        self._store.update_if(STATUS_COLLECTION, doc["id"], expected, merged)

    def _post(self, apps: AppLookup, doc: Mapping, body: str, sigs: Mapping) -> _Failure | None:
        """Post ``body`` as ``doc``'s comment and record it; the failure, if any (the
        comment's outcome is then unknown: it is never posted again by the tick)."""
        from culture_rules.apps.github import GitHubError  # noqa: PLC0415

        try:
            out = apps(doc[_ACTOR], doc["repo"]).post_comment(doc["repo"], doc[_NUMBER], body)
        except GitHubError as exc:
            log.warning("status comment on %s#%s not posted: %s", doc["repo"], doc[_NUMBER], exc)
            self._changes(doc, {_STATE: _UNKNOWN, _ERROR: exc.code})
            return _Failure(exc.code, exc.retryable)
        changes = {
            _STATE: _POSTED,
            _COMMENT_ID: out.get(_COMMENT_ID),
            "url": out.get("url"),
            _LAST_EDIT_AT: _iso(self._clock()),
            **sigs,
        }
        self._changes(doc, changes)
        return None

    def _refresh(self, apps: AppLookup, serves: Callable[[str], bool], doc: Mapping) -> int:
        if doc.get(_STATE) != _POSTED or not serves(str(doc.get(_ACTOR))):
            return 0
        root = self._store.get(_RUNS, doc["id"])
        if root is None:
            self._changes(doc, {_FINAL: True, "final_at": _iso(self._clock())})
            return 0
        chain = self.chain(root)
        closing = self._closing(chain)
        if closing is not None:
            return 0 if self._finalize(apps, doc, chain, closing) else 1
        sigs = self._sigs(chain)
        if not self._due(doc, sigs):
            return 0
        if self._edit(apps, doc, render(chain), sigs):
            return 0
        self._repair(apps, doc["id"])
        return 1

    def _repair(self, apps: AppLookup, doc_id: str) -> None:
        """A chain finished (on another process) while this tick edited its comment: write
        the final body again, so the last edit is the final one."""
        doc = self._store.get(STATUS_COLLECTION, doc_id) or {}
        if doc.get(_FINAL) and isinstance(doc.get(_FINAL_BODY), str):
            self._edit(apps, doc, doc[_FINAL_BODY], {})

    def _closing(self, chain: Chain) -> str | None:
        """The final section of a chain that ended without a status action, else None."""
        if not chain.ended:
            return None
        last = chain.runs[-1]
        status = last.get(_STATUS)
        link = run_link(last.get("id"))
        if status == "cancelled":
            return f"**PR fixer stopped:** the run was cancelled.\n\nRun: {link}"
        if status == "superseded":
            return f"**PR fixer stopped:** the PR head moved; a new run takes over.\n\nRun: {link}"
        finished = _parse(last.get("finished_at"))
        if finished is not None and self._clock() - finished >= IDLE_END:
            return f"**PR fixer: the chain ended** (last run {_run_word(last)}).\n\nRun: {link}"
        return None

    def _due(self, doc: Mapping[str, Any], sigs: Mapping[str, str]) -> bool:
        last = _parse(doc.get(_LAST_EDIT_AT))
        since = (self._clock() - last).total_seconds() if last else float("inf")
        if sigs[_STAGE_SIG] != doc.get(_STAGE_SIG):
            return since >= self._floor
        if sigs[_NOTES_SIG] != doc.get(_NOTES_SIG):
            return since >= self._notes_every
        return False

    def _edit(self, apps: AppLookup, doc: Mapping, body: str, sigs: Mapping) -> _Failure | None:
        """Edit ``doc``'s comment to ``body``; a deleted comment (404) is posted again."""
        from culture_rules.apps.github import GitHubError  # noqa: PLC0415

        try:
            apps(doc[_ACTOR], doc["repo"]).update_issue_comment(doc["repo"], doc[_COMMENT_ID], body)
        except GitHubError as exc:
            if exc.code == "http_404":
                log.info("status comment %s was deleted: posting it again", doc[_COMMENT_ID])
                return self._post(apps, doc, body, sigs)
            log.warning("status comment %s not edited: %s", doc.get(_COMMENT_ID), exc)
            return _Failure(exc.code, exc.retryable)
        self._changes(doc, {_LAST_EDIT_AT: _iso(self._clock()), **sigs})
        return None

    def _finalize(self, apps: AppLookup, doc: Mapping, chain: Chain, final: str) -> _Failure | None:
        body = render(chain, final=clean_block(final, FINAL_CAP))
        self._changes(doc, {_FINAL: True, "final_at": _iso(self._clock()), _FINAL_BODY: body})
        fresh = self._store.get(STATUS_COLLECTION, doc["id"]) or doc
        if fresh.get(_STATE) == _POSTED and fresh.get(_COMMENT_ID):
            return self._edit(apps, fresh, body, self._sigs(chain))
        return self._post(apps, fresh, body, self._sigs(chain))

    # ------------------------------------------------------------------ the end

    def finish(
        self,
        apps: AppLookup,
        run: Mapping[str, Any] | None,
        text: str,
        *,
        where: tuple[str, int] | None = None,
    ) -> dict[str, Any] | None:
        """Write ``text`` (the chain-end action's body) as the final section of ``run``'s
        chain's status comment: edit it, or post it when the chain has none. ``None`` when
        ``run`` is not in an opted-in chain, or its PR is not ``where`` (``(repo, number)``,
        the action's own): the caller posts a plain comment. A failed write is
        ``{"error": code, "retryable": bool}``."""
        actor = run_status_actor(run)
        root = self.root_of(run) if run is not None and actor else None
        target = _target(root, actor) if root is not None and actor else None
        if target is None or not _same_pr(target, where):
            return None
        root_id = str(root.get("id"))
        with self._lock:
            doc = self._store.get(STATUS_COLLECTION, root_id) or self._claim(root, target)
            doc = doc or self._store.get(STATUS_COLLECTION, root_id)
            self._wait_floor(doc)
            failure = self._finalize(apps, doc, self.chain(root), text)
            done = self._store.get(STATUS_COLLECTION, root_id) or {}
        if failure is not None:
            return {_ERROR: failure.code, "retryable": failure.retryable}
        return {_COMMENT_ID: done.get(_COMMENT_ID), "url": done.get("url"), _STATUS: True}

    def _wait_floor(self, doc: Mapping[str, Any]) -> None:
        last = _parse(doc.get(_LAST_EDIT_AT))
        if last is None:
            return
        left = self._floor - (self._clock() - last).total_seconds()
        if left > 0:
            self._sleep(min(left, self._floor))


@dataclass(frozen=True)
class _Failure:
    """Why a post or edit failed (the GitHub error's code and whether a retry may help)."""

    code: str
    retryable: bool


def open_chains(store: Any) -> Iterable[Mapping[str, Any]]:
    """The status comments still edited live (for operators and tests)."""
    return store.find(STATUS_COLLECTION, {_FINAL: False})
