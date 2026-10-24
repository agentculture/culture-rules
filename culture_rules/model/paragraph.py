"""A workflow's steps as a paragraph, one sentence per line: each step's description after a
lead-in from fixed phrases (#69, d11). Standard-library only, and no AI.

Every joining word comes from :data:`PHRASES`, chosen from a fact of the step graph or a
config key of the step it introduces; the rest of the paragraph is the steps' own
descriptions. The phrases follow the engine's step semantics (engine/runs.py, read here, not
imported):

* a top-level step runs once every step feeding it through an edge is done
  (``_deps_done``), so the joins are read off the edge graph, on its transitive reduction
  (an edge already implied by a longer path never adds a join);
* steps with the same predecessors and no path between them run at the same time, unless
  two or more of them carry a ``config.when``: then they read as alternatives;
* a loop's body runs in declared order (``_earlier_iteration_steps_ok``), whatever its
  edges say, so body steps read ``then`` one after another;
* ``config.when`` adds a lead-in (``If <it>``; a ``when`` shared by every remaining step of
  a level is said once, ``From here on, only if <it>``); a wait step's seconds and guard,
  ``for_each`` and ``retry_until`` bounds, a retry policy and ``config.on_error: "skip"``
  add trailing clauses after the text.

How the steps make sentences (d11):

* the text follows its lead-in with no colon, its first letter lower-cased
  (:func:`lower_first`: not before a non-letter, nor in a first word with another capital,
  such as ``PR``, ``EPUB`` or ``GitGuardian``) and its final period dropped (the sentence
  supplies its own);
* a plain step (its join is ``Then``, no wait, retry or loop) whose condition is the
  previous step's (or both have none) joins that step's sentence with ``; then``; any other
  step - a join (``Once both are done``), a condition change, a loop, a wait, a retry -
  starts a new sentence, and so does the step after a loop's body, after a disabled step,
  after a step the table has no phrase for and after a group of siblings;
* siblings read together in one sentence (d13): ``Then, at the same time, a and b`` (three
  or more: ``a, b and c``), each later sibling opening with a connector (``,`` or `` and``,
  followed by its own condition if it has one); alternatives keep their reading in the same
  shape (``Depending on the conditions, if x, a and, if y, b``). A disabled sibling, one the
  table has no phrase for, one where a shared condition starts and the one after a loop
  sibling's body read as their own sentence (``At the same time``);
* a loop's body always starts its own sentence (d13), ``Each time``, a line the editor
  indents.

A step with no description is stood in for by its name, else by a generic line built only
from the d19 vocabulary (:func:`generic_line`); the segment is marked ``standin`` (italic in
the editor, ``[ ]`` in the CLI) and stays inside its sentence. A step whose kind or config the
table has no phrase for reads as its own sentence, its d19 line
(:func:`culture_rules.model.describe.step_text`) with no lead-in; describing never raises.

The rules around the steps (t7, c37): :func:`workflow_paragraph` opens with one built
sentence per rule that starts the workflow (:func:`rule_sentence`: its trigger, condition,
relationships and placement) and closes with the Then side (:func:`then_sentence`: where it
continues, what happens when it is done and on failure, how it runs), one sentence per line.
Those sentences are composed from :data:`PHRASES` entries ending with a period
(:data:`SENTENCE_KEYS`) filled with describe.py's words (``trigger_text``,
``condition_words``, ``action_text``, the placement and run-key words); a rule's name and
description, and an action's name and body, are never read. Which rules start a workflow
and which continue into another mirrors the editor's fold (web/src/fold/model.ts
``foldModel``): :func:`fold_rules`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from culture_rules.model import condition as cond
from culture_rules.model.describe import (
    CONDITION_FALLBACK,
)
from culture_rules.model.describe import _actor_of as actor_words
from culture_rules.model.describe import _comment_words as comment_words
from culture_rules.model.describe import _key_text as key_text
from culture_rules.model.describe import _kind_text as kind_text
from culture_rules.model.describe import _placement as placement_text
from culture_rules.model.describe import (
    action_text,
    condition_words,
    step_text,
    trigger_text,
)
from culture_rules.model.workflow import STEP_KINDS

__all__ = [
    "BARE_LEADS",
    "JOIN_KEYS",
    "LEAD_KEYS",
    "MARK_KEYS",
    "PHRASES",
    "SENTENCE_KEYS",
    "SIBLING_KEYS",
    "STANDIN_KEYS",
    "TAIL_KEYS",
    "compose",
    "compose_sentence",
    "fold_rules",
    "generic_line",
    "lower_first",
    "mark_disabled",
    "onward_of",
    "render_marked",
    "rule_sentence",
    "standin_text",
    "step_paragraph",
    "then_sentence",
    "workflow_paragraph",
]

#: Every phrase, one per construct. A step reads ``<lead-in> <text><trailing clauses>``:
#: its lead-in is one or more lead-in entries composed by :func:`compose`
#: (``Then`` + ``If {condition}`` -> ``Then, if x is 1``), a step joining the sentence before
#: it opens with a connector (``; then``) instead, and its trailing clauses follow its text,
#: each after a comma. The rule and Then sentences are composed from the entries ending with a
#: period (:data:`SENTENCE_KEYS`).
PHRASES: dict[str, str] = {
    # lead-ins: how a step follows the ones before it
    "first": "First",
    "then": "Then",
    "same_time": "At the same time",
    "depending": "Depending on the conditions",
    "in_any_case": "In any case",
    "once_both": "Once both are done",
    "once_those": "Once those are done",
    "once_one": "Once {step} is done",
    "once_many": "Once {steps} are done",
    "from_start": "Separately, from the start",
    "each_time": "Each time",
    # lead-ins the step's condition adds
    "from_here_on": "From here on, only if {condition}",
    "from_here_on_unspoken": "From here on, only if their shared condition holds",
    "if": "If {condition}",
    "if_unspoken": "If its condition holds",
    # connectors: a step continuing the sentence before it (d11), or a sibling read with the
    # ones before it (d13: ``a, b and c``)
    "then_grouped": "; then",
    "list_grouped": ",",
    "and_grouped": " and",
    # trailing clauses after the step's text
    "wait": "waiting {duration}",
    "wait_guard": "and the run stops if the PR head moves",
    "for_each": "for each item (up to {n})",
    "retry_until": "until {condition}, at most {times}",
    "retry_until_unspoken": "until its condition holds, at most {times}",
    "retry": "trying up to {n} times",
    "on_error_skip": "carrying on even if it fails",
    # stand-ins for a step with neither a description nor a name, where the d19 words would
    # only repeat the trailing clauses (see generic_line)
    "standin_wait": "a pause",
    "standin_loop": "a loop",
    # t7: the rule sentences around the steps. Each ends with a period; a sentence is one
    # or more of them, composed by :func:`compose_sentence` (``It starts when x arrives`` +
    # ``on spark2.`` -> ``It starts when x arrives, on spark2.``).
    # how a rule that starts the workflow fires
    "starts_event": "It starts when {trigger} arrives.",
    "starts_on": "It starts on {trigger}.",
    "starts_manual": "It is {trigger}.",
    "starts_succeeded": "It starts when {workflow} succeeds.",
    "starts_failed": "It starts when {workflow} fails.",
    "starts_ended": "It starts when {workflow} ends ({outcome}).",
    "starts_none": "No rule starts it yet.",
    # what the rule's own config adds
    "rule_if": "if {condition}.",
    "rule_if_unspoken": "if its condition holds.",
    "rule_must_after": "only after {rules} succeeded.",
    "rule_may_after": "reading the outputs of {rules} if {it} ran.",
    "rule_supersedes": "instead of {rules} for the same event.",
    "rule_group": "only if no higher-priority rule of group {group} matches (priority {priority}).",
    "rule_where": "{where}.",
    # the Then side
    "continues": "Then it continues into {workflows}.",
    "continues_none": "Nothing continues from here.",
    "ends": "When it is done, it runs {action}.",
    "ends_chain": "Where the chain ends here, it runs {action}.",
    "ends_status": "When it is done, it updates the chain's status comment on GitHub{by}.",
    "ends_status_chain": (
        "Where the chain ends here, it updates the chain's status comment on GitHub{by}."
    ),
    "ends_noop": "When it is done, nothing else happens.",
    "ends_differs": "When it is done, it runs {actions}, depending on the rule that started it.",
    "on_failure": "If it fails, it runs {action}.",
    "on_failure_status": (
        "If it fails, it updates the chain's status comment on GitHub with the failure{by}."
    ),
    "on_failure_status_same": "If it fails, it updates that comment with the failure.",
    "on_failure_differs": "If it fails, it runs {actions}, depending on the rule that started it.",
    "runs_key": "It runs one at a time per {key}.",
    "runs_attempts": "with at most {n} attempts.",
    "runs_differs": "How it runs differs per rule that starts it.",
    # how a status-comment sentence names who writes it (its ``{by}``, after a space)
    "by": "({words})",
    # the CLI's mark on a disabled rule or step (d13), never part of ``text``
    "disabled_mark": "(disabled)",
}

#: The :data:`PHRASES` entries that build the rule and Then sentences (each ends with a
#: period).
SENTENCE_KEYS: frozenset[str] = frozenset(
    k
    for k in PHRASES
    if k.startswith(("starts_", "rule_", "continues", "ends", "on_failure", "runs_"))
)
#: Connectors: a step that continues the sentence before it opens with one of these.
JOIN_KEYS: frozenset[str] = frozenset({"then_grouped", "list_grouped", "and_grouped"})
#: The connectors of a sibling read with the ones before it (d13).
SIBLING_KEYS: frozenset[str] = frozenset({"list_grouped", "and_grouped"})
#: Trailing clauses, after a step's text.
TAIL_KEYS: frozenset[str] = frozenset(
    {
        "wait",
        "wait_guard",
        "for_each",
        "retry_until",
        "retry_until_unspoken",
        "retry",
        "on_error_skip",
    }
)
#: Generic stand-in words.
STANDIN_KEYS: frozenset[str] = frozenset({"standin_wait", "standin_loop"})
#: Marks a renderer adds around the text, never in it.
MARK_KEYS: frozenset[str] = frozenset({"disabled_mark"})
#: Lead-ins: a step that starts a sentence opens with one or more of these.
LEAD_KEYS: frozenset[str] = frozenset(
    set(PHRASES) - SENTENCE_KEYS - JOIN_KEYS - TAIL_KEYS - STANDIN_KEYS - MARK_KEYS - {"by"}
)
#: A lead-in that is only one of these is followed by the text directly (``First quiet
#: period``); any other is followed by a comma (``Then, if x is 1, quiet period``).
BARE_LEADS: frozenset[str] = frozenset(
    {"first", "then", "same_time", "then_grouped", "list_grouped", "and_grouped"}
)

_WAIT_GUARDS = ("head_unchanged",)
_TIMES = {1: "once", 2: "twice"}
_UNITS = ((3600, "hour"), (60, "minute"))
_LOOPS = ("for_each", "retry_until")
_IF_KEYS = ("if", "if_unspoken", "from_here_on", "from_here_on_unspoken")

Part = tuple[str, dict[str, Any]]


# --------------------------------------------------------------------------- helpers


def _plain(obj: Any) -> dict[str, Any]:
    """A model's dict form, or the mapping itself; anything else is an empty dict."""
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    return dict(obj) if isinstance(obj, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _sid(s: Mapping[str, Any]) -> str:
    return str(s.get("id") or "?")


def _label(s: Mapping[str, Any]) -> str:
    """How the paragraph names a step: its display name, else its id."""
    name = s.get("name")
    return name if isinstance(name, str) and name else _sid(s)


def _config(s: Mapping[str, Any]) -> Mapping[str, Any]:
    config = s.get("config")
    return config if isinstance(config, Mapping) else {}


def _count(value: Any) -> int | None:
    """A positive whole number, else ``None``."""
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
    return value if ok else None


def _seconds(value: Any) -> float | None:
    ok = isinstance(value, int | float) and not isinstance(value, bool) and value > 0
    return float(value) if ok else None


def _valid_condition(tree: Any) -> bool:
    try:
        cond.validate(tree)
    except (cond.ConditionError, TypeError, ValueError, RecursionError):
        return False
    return True


def _duration(seconds: float) -> str:
    """``5 minutes``, ``1 hour``, ``45 seconds``: the largest whole unit."""
    for size, unit in _UNITS:
        if seconds >= size and seconds % size == 0:
            n = int(seconds // size)
            return f"{n} {unit}{'' if n == 1 else 's'}"
    n_text = str(int(seconds)) if seconds.is_integer() else str(seconds)
    return f"{n_text} second{'' if seconds == 1 else 's'}"


def _names(labels: list[str]) -> str:
    """``a and b`` / ``a, b and c``."""
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def compose(parts: list[Part]) -> str:
    """One lead-in from table entries, in order: the first as it stands, the later ones
    starting lower-case, joined by commas (``Then, if x is 1``; `` and, if x is 1``; after
    an entry that is itself a comma, by a space: ``, if x is 1``). A step's ``phrase`` is
    this, followed by a comma unless it is one bare lead (:data:`BARE_LEADS`)."""
    out = ""
    for i, (key, params) in enumerate(parts):
        template = PHRASES[key]
        if i:
            template = template[0].lower() + template[1:]
            out += " " if out.endswith(",") else ", "
        out += template.format(**params)
    return out


def _lead_phrase(parts: list[Part]) -> str:
    if not parts:
        return ""
    bare = len(parts) == 1 and parts[0][0] in BARE_LEADS
    return compose(parts) + ("" if bare else ",")


def _tail_text(parts: list[Part]) -> str:
    return "".join(", " + PHRASES[k].format(**p) for k, p in parts)


def lower_first(text: str) -> str:
    """``Packs it.`` -> ``packs it.``: the text after a lead-in starts lower-case, unless it
    opens with a non-letter (a quote, a digit) or its first word has another capital letter
    (``PR``, ``EPUB``, ``GitGuardian``, ``SonarCloud``)."""
    first = text[:1]
    if not (first.isalpha() and first.isupper()):
        return text
    word = text.split(maxsplit=1)[0]
    if any(c.isupper() for c in word[1:]):
        return text
    return first.lower() + text[1:]


# --------------------------------------------------------------------------- recognition


def _recognised(s: Mapping[str, Any]) -> bool:
    """Whether every config the phrases read is one the table has words for."""
    if s.get("kind") not in STEP_KINDS:
        return False
    if "config" in s and not isinstance(s["config"], Mapping):
        return False
    config = _config(s)
    if "when" in config and not _valid_condition(config["when"]):
        return False
    if "on_error" in config and config["on_error"] != "skip":
        return False
    retry = s.get("retry")
    if retry is not None and (not isinstance(retry, Mapping) or not _retry_ok(retry)):
        return False
    return _kind_ok(s, config)


def _retry_ok(retry: Mapping[str, Any]) -> bool:
    return "max_attempts" not in retry or _count(retry["max_attempts"]) is not None


def _kind_ok(s: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    kind = s.get("kind")
    if kind == "wait":
        guard = config.get("guard")
        guard_ok = guard is None or (
            isinstance(guard, Mapping) and guard.get("value") in _WAIT_GUARDS
        )
        return _seconds(config.get("seconds")) is not None and guard_ok
    if kind == "for_each":
        return _count(s.get("max_iterations")) is not None
    if kind == "retry_until":
        return _count(s.get("max_iterations")) is not None and _valid_condition(config.get("until"))
    return True


# --------------------------------------------------------------------------- the graph


class _Graph:
    """The top-level steps' dependencies, as the engine reads them (``_deps_done``): an edge
    into a top-level step from another step, a loop's body step standing for its loop."""

    def __init__(self, steps: list[dict[str, Any]], edges: list[Any]) -> None:
        self.ids = [_sid(s) for s in steps]
        owner = {sid: sid for sid in self.ids}
        for s in steps:
            for b in _body(s):
                owner.setdefault(_sid(b), _sid(s))
        self.preds: dict[str, set[str]] = {sid: set() for sid in self.ids}
        for raw in edges:
            e = _plain(raw)
            source, target = owner.get(str(e.get("source"))), str(e.get("target"))
            if source is not None and target in self.preds and source != target:
                self.preds[target].add(source)
        succ: dict[str, set[str]] = {sid: set() for sid in self.ids}
        for target, sources in self.preds.items():
            for source in sources:
                succ[source].add(target)
        self.reach = {sid: self._reach(succ, sid) for sid in self.ids}

    @staticmethod
    def _reach(succ: dict[str, set[str]], start: str) -> set[str]:
        """Every step reachable from ``start`` through edges."""
        seen: set[str] = set()
        todo = [start]
        while todo:
            for nxt in succ[todo.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return seen

    def reduced(self, sid: str) -> set[str]:
        """``sid``'s predecessors on the transitive reduction: one reached through another
        predecessor is implied and dropped."""
        preds = self.preds[sid]
        return {p for p in preds if not any(q != p and q in self.reach[p] for q in preds)}

    def related(self, a: str, b: str) -> bool:
        return b in self.reach[a] or a in self.reach[b]

    def order(self) -> list[int]:
        """Declared positions in a stable topological order (declared order when that already
        is one); steps on a cycle follow in declared order."""
        done: set[str] = set()
        out: list[int] = []
        left = list(range(len(self.ids)))
        while left:
            ready = [i for i in left if self.preds[self.ids[i]] <= done]
            pick = ready[0] if ready else left[0]
            out.append(pick)
            left.remove(pick)
            done.add(self.ids[pick])
        return out


def _body(s: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [_plain(b) for b in _list(s.get("body"))]


# --------------------------------------------------------------------------- phrases


def _when(s: Mapping[str, Any]) -> Any:
    return _config(s).get("when")


def _condition_part(key: str, tree: Any) -> Part:
    words = condition_words(tree)
    if words == CONDITION_FALLBACK:
        return (f"{key}_unspoken", {})
    return (key, {"condition": words})


def _shared_from(steps: list[dict[str, Any]], ok: list[bool]) -> int | None:
    """Where a ``when`` held identically by every remaining step (two or more) starts."""
    for i, s in enumerate(steps[:-1]):
        when = _when(s)
        if ok[i] and when is not None and all(_when(t) == when for t in steps[i + 1 :]):
            return i
    return None


def _tail_parts(s: Mapping[str, Any]) -> list[Part]:
    """The trailing clauses a step's own config adds, in a fixed order."""
    config = _config(s)
    parts: list[Part] = []
    kind = s.get("kind")
    if kind == "wait":
        parts.append(("wait", {"duration": _duration(float(config["seconds"]))}))
        if config.get("guard") is not None:
            parts.append(("wait_guard", {}))
    elif kind == "for_each":
        parts.append(("for_each", {"n": s["max_iterations"]}))
    elif kind == "retry_until":
        n = s["max_iterations"]
        key, params = _condition_part("retry_until", config["until"])
        parts.append((key, {**params, "times": _TIMES.get(n, f"{n} times")}))
    if _retries(s):
        parts.append(("retry", {"n": s["retry"]["max_attempts"]}))
    if config.get("on_error") == "skip":
        parts.append(("on_error_skip", {}))
    return parts


def _retries(s: Mapping[str, Any]) -> bool:
    retry = s.get("retry")
    return isinstance(retry, Mapping) and (retry.get("max_attempts") or 1) > 1


def _groups(graph: _Graph, steps: list[dict[str, Any]], order: list[int]) -> list[list[int]]:
    """Consecutive steps (in reading order) with the same predecessors and no path between
    them: they run at the same time."""
    groups: list[list[int]] = []
    for i in order:
        sid = _sid(steps[i])
        if groups:
            head = groups[-1]
            same = graph.reduced(_sid(steps[head[0]])) == graph.reduced(sid)
            if same and not any(graph.related(_sid(steps[j]), sid) for j in head):
                head.append(i)
                continue
        groups.append([i])
    return groups


def _alternatives(steps: list[dict[str, Any]], group: list[int]) -> bool:
    """Two or more of the group carry a ``when``: they read as alternatives."""
    return len(group) > 1 and sum(_when(steps[i]) is not None for i in group) >= 2


def _join(graph: _Graph, steps: list[dict[str, Any]], groups: list[list[int]], g: int) -> Part:
    """How group ``g``'s first step follows what was read before it."""
    if g == 0:
        return ("first", {})
    preds = graph.reduced(_sid(steps[groups[g][0]]))
    if not preds:
        return ("from_start", {})
    before = groups[g - 1]
    if preds == {_sid(steps[i]) for i in before}:
        if len(before) == 1 or _alternatives(steps, before):
            return ("then", {})
        return ("once_both", {}) if len(before) == 2 else ("once_those", {})
    by_id = {_sid(s): s for s in steps}
    labels = [_label(by_id[p]) for p in sorted(preds, key=graph.ids.index)]
    if len(labels) == 1:
        return ("once_one", {"step": labels[0]})
    return ("once_many", {"steps": _names(labels)})


def _lead_parts(
    graph: _Graph, steps: list[dict[str, Any]], groups: list[list[int]], g: int, k: int
) -> list[Part]:
    """The joining lead-ins of the ``k``-th step of group ``g``."""
    group = groups[g]
    has_when = _when(steps[group[k]]) is not None
    if _alternatives(steps, group):
        if k == 0:
            join = _join(graph, steps, groups, g)
            lead = [] if join[0] in ("first", "then") else [join]
            return lead + [("depending", {})] + ([] if has_when else [("in_any_case", {})])
        return [] if has_when else [("in_any_case", {})]
    return [_join(graph, steps, groups, g) if k == 0 else ("same_time", {})]


def _level_leads(
    steps: list[dict[str, Any]], ok: list[bool], leads: dict[int, list[Part]]
) -> dict[int, list[Part]]:
    """Each step's full lead-in at one level: its join, then the shared condition where it
    starts, or its own condition outside the shared run."""
    shared = _shared_from(steps, ok)
    out = {}
    for i, s in enumerate(steps):
        parts = list(leads[i])
        if shared is not None and i == shared:
            parts.append(_condition_part("from_here_on", _when(s)))
        elif ok[i] and _when(s) is not None and not (shared is not None and i > shared):
            parts.append(_condition_part("if", _when(s)))
        out[i] = parts or [("same_time", {})]
    return out


# --------------------------------------------------------------------------- stand-ins


def generic_line(step: Any) -> str:
    """What stands in for a step with neither a description nor a name: its d19 words
    (describe.py's step kind, built-in, action and agent words, with its placement), never
    free text. A wait or a loop, whose d19 words would only repeat its trailing clauses,
    reads as :data:`PHRASES` ``standin_wait`` / ``standin_loop``; never raises."""
    s = _plain(step)
    kind = s.get("kind")
    if kind == "wait":
        return PHRASES["standin_wait"]
    if kind in _LOOPS:
        return PHRASES["standin_loop"]
    try:
        return kind_text(s, kind, _config(s), placement_text(s.get("placement")))
    except (TypeError, ValueError, AttributeError):
        return str(kind or "step")


def standin_text(step: Any) -> str:
    """What stands in for a step with no description: its name, else :func:`generic_line`."""
    s = _plain(step)
    name = s.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else generic_line(s)


# --------------------------------------------------------------------------- segments


def _d19(s: Mapping[str, Any]) -> str:
    """The step's d19 words; a config even those cannot read (``step_text`` compares the
    retry bound as a number) reads as its kind alone."""
    try:
        return step_text(s)
    except (TypeError, ValueError, AttributeError):
        return str(s.get("kind") or "step")


def _item(
    s: dict[str, Any],
    lead: list[Part],
    ok: bool,
    label: str,
    depth: int,
    level: str,
    k: int,
    sib: tuple[int, int, int, bool] | None = None,
) -> dict[str, Any]:
    """A step before its sentence is settled; ``sib`` is a top-level step's group of
    siblings: ``(group, position in it, its size, whether they are alternatives)``."""
    return {
        "s": s,
        "lead": lead,
        "tail": _tail_parts(s) if ok else [],
        "ok": ok,
        "label": label,
        "depth": depth,
        "level": level,
        "k": k,
        "sib": sib,
    }


def _plain_step(it: Mapping[str, Any]) -> bool:
    """A step that may join the sentence before it: recognised, enabled, not a loop or a wait,
    no retry policy."""
    s = it["s"]
    return (
        it["ok"]
        and s.get("enabled") is not False
        and s.get("kind") not in (*_LOOPS, "wait")
        and not _retries(s)
    )


def _usable(it: Mapping[str, Any]) -> bool:
    return bool(it["ok"]) and it["s"].get("enabled") is not False


def _in_group(it: Mapping[str, Any]) -> bool:
    """A top-level step with siblings."""
    return it["sib"] is not None and it["sib"][2] > 1


def _continues(prev: Mapping[str, Any] | None, it: Mapping[str, Any]) -> bool:
    """Whether ``it`` continues the sentence ``prev`` is in with ``; then`` (d11)."""
    if prev is None or not _usable(it) or not _usable(prev):
        return False
    if it["depth"] > 0 and it["k"] == 0:
        return False  # a loop's body starts its own sentence (d13)
    if prev["level"] != it["level"] or prev["s"].get("kind") in _LOOPS:
        return False  # the step after a loop's body, or after a loop with none
    if _in_group(it) or _in_group(prev):
        return False  # siblings read in a sentence of their own (d13)
    keys = [k for k, _ in it["lead"]]
    if not _plain_step(it) or keys[0] != "then" or any(k.startswith("from_here") for k in keys):
        return False
    return _when(it["s"]) == _when(prev["s"])


def _with_sibling(prev: Mapping[str, Any] | None, it: Mapping[str, Any]) -> bool:
    """Whether ``it`` is read in the sentence of the sibling just before it (d13)."""
    if prev is None or not _in_group(it) or prev["sib"] is None:
        return False
    same = prev["sib"][0] == it["sib"][0] and prev["sib"][1] == it["sib"][1] - 1
    if not same or not _usable(it) or not _usable(prev) or prev["s"].get("kind") in _LOOPS:
        return False
    return not any(k.startswith("from_here") for k, _ in it["lead"])


def _settle(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each step's sentence (0-based) and whether it continues the one before (d11, d13);
    the lead-in of a continuing step becomes its connector (``; then``, ``,``, `` and``),
    and the first of siblings read together says ``at the same time`` after its join."""
    sentence, prev = -1, None
    for it in items:
        it["with_sibling"] = _with_sibling(prev, it)
        joined = it["with_sibling"] or _continues(prev, it)
        if not joined:
            sentence += 1
        it["joined"], it["sentence"] = joined, sentence
        prev = it
    for n, it in enumerate(items):
        _connect(it, items[n + 1] if n + 1 < len(items) else None)
    return items


def _connect(it: dict[str, Any], nxt: Mapping[str, Any] | None) -> None:
    """Rewrite ``it``'s lead-in once :func:`_settle` knows its sentences: a continuing step's
    lead-in becomes its connector, and the first of siblings read together (``nxt`` the step
    after it, or ``None``) says ``at the same time`` after its join."""
    if it["with_sibling"]:
        key = "list_grouped" if nxt is not None and nxt["with_sibling"] else "and_grouped"
        rest = [p for p in it["lead"] if p[0] in (*_IF_KEYS, "in_any_case")]
        it["lead"] = [(key, {}), *rest]
    elif it["joined"]:
        it["lead"] = [("then_grouped", {})]
    elif nxt is not None and nxt["with_sibling"] and not it["sib"][3]:
        lead = it["lead"]
        if lead[0][0] != "same_time":  # its join, then "at the same time"
            it["lead"] = [lead[0], ("same_time", {}), *lead[1:]]


def _segment(it: dict[str, Any], ends: bool) -> dict[str, Any]:
    s = it["s"]
    description = s.get("description")
    written = isinstance(description, str) and bool(description.strip())
    if not it["ok"]:
        text = f"{it['label']} {_sid(s)} — {_d19(s)}"
        return _seg(it, "", [], text, text, "." if ends else "", standin=False, fallback=True)
    text = description if written else standin_text(s)
    shown = lower_first(text)
    if shown.endswith("."):
        shown = shown[:-1]
    tail = _tail_text(it["tail"])
    if ends and not (not tail and shown.endswith(("?", "!"))):
        tail += "."
    keys = [k for k, _ in it["lead"]]
    return _seg(it, _lead_phrase(it["lead"]), keys, text, shown, tail, standin=not written)


def _seg(
    it: Mapping[str, Any],
    phrase: str,
    keys: list[str],
    text: str,
    shown: str,
    tail: str,
    *,
    standin: bool,
    fallback: bool = False,
) -> dict[str, Any]:
    s = it["s"]
    seg = {
        "phrase": phrase,
        "step": _sid(s),
        "text": text,
        "shown": shown,
        "tail": tail,
        "keys": keys,
        "tail_keys": [k for k, _ in it["tail"]],
        "depth": it["depth"],
        "sentence": it["sentence"],
        "gap": standin,
        "standin": standin,
        "fallback": fallback,
        "grouped": it["joined"],
        "name": _label(s),
    }
    if s.get("enabled") is False:
        seg["disabled"] = True  # d8, d13: no words; the CLI marks it, the editor tags it
    return seg


#: how deep nested loop bodies are read (the model's own nesting is bounded too)
_MAX_LOOP_DEPTH = 8


def _body_items(loop: dict[str, Any], label: str, depth: int = 1) -> list[dict[str, Any]]:
    body = _body(loop)
    ok = [_recognised(b) for b in body]
    leads = {i: [("each_time", {}) if i == 0 else ("then", {})] for i in range(len(body))}
    full = _level_leads(body, ok, leads)
    items: list[dict[str, Any]] = []
    for i, b in enumerate(body):
        items.append(_item(b, full[i], ok[i], f"{label}.{i + 1}", depth, _sid(loop), i))
        # the engine refuses a nested loop, but a stored one still reads every step
        if b.get("kind") in _LOOPS and depth < _MAX_LOOP_DEPTH:
            items += _body_items(b, f"{label}.{i + 1}", depth + 1)
    return items


def step_paragraph(workflow: Any) -> dict[str, Any]:
    """``workflow``'s steps (a :class:`Workflow` or its dict form) as sentences, one per line.

    Returns ``{"segments", "text"}``: one segment per step, top level in a stable
    topological order (declared order where it is one) and each loop's body right after its
    loop, in declared order. A segment is ``{"phrase", "step", "text", "shown", "tail",
    "keys", "tail_keys", "depth", "sentence", "gap", "standin", "fallback", "grouped",
    "name"}`` (plus ``"disabled": True`` for a disabled step, d8):

    * ``phrase`` is the lead-in (composed from the :data:`PHRASES` entries named in
      ``keys``), or a connector (``; then``; for a sibling ``,`` or `` and``, ``, if x,``)
      for a ``grouped`` step that continues the sentence before it;
    * ``text`` is the step's description unchanged, or, for a ``standin`` (no description:
      ``gap`` too), its stand-in (:func:`standin_text`); ``shown`` is ``text`` as the sentence
      reads it (:func:`lower_first`, final period dropped);
    * ``tail`` is its trailing clauses (``tail_keys``), plus the sentence's period when the
      sentence ends with it;
    * ``sentence`` is the 0-based sentence it belongs to;
    * a step the table has no phrase for (``fallback``) has no phrase, and its d19 line as
      ``text`` and ``shown``.

    A segment reads ``phrase + " " + shown + tail`` (``shown + tail`` with no phrase); the
    segments of a sentence are concatenated, and ``text`` joins the sentences with ``\\n``.
    """
    segments = _segments(_plain(workflow))
    return {"segments": segments, "text": _render(segments)}


def _segments(wf: dict[str, Any]) -> list[dict[str, Any]]:
    steps = [_plain(s) for s in _list(wf.get("steps"))]
    graph = _Graph(steps, _list(wf.get("edges")))
    order = graph.order()
    groups = _groups(graph, steps, order)
    leads: dict[int, list[Part]] = {}
    sibs: dict[int, tuple[int, int, int, bool]] = {}
    for g, group in enumerate(groups):
        alternatives = _alternatives(steps, group)
        for k, i in enumerate(group):
            leads[i] = _lead_parts(graph, steps, groups, g, k)
            sibs[i] = (g, k, len(group), alternatives)
    # the shared-condition run is read in reading order, so re-index by position in it
    ordered = [steps[i] for i in order]
    ok = [_recognised(s) for s in ordered]
    full = _level_leads(ordered, ok, {n: leads[i] for n, i in enumerate(order)})
    items: list[dict[str, Any]] = []
    for n, i in enumerate(order):
        s = steps[i]
        items.append(_item(s, full[n], ok[n], str(i + 1), 0, "", n, sibs[i]))
        if s.get("kind") in _LOOPS:
            items += _body_items(s, str(i + 1))
    _settle(items)
    return [
        _segment(it, n + 1 == len(items) or items[n + 1]["sentence"] != it["sentence"])
        for n, it in enumerate(items)
    ]


def _chunk(seg: Mapping[str, Any], standin: Callable[[str], str] | None) -> str:
    """One segment as it reads: a step's lead-in, shown text and tail; a sentence's text."""
    if "shown" not in seg:
        return str(seg.get("text") or "")
    shown = str(seg.get("shown") or "")
    if standin and seg.get("standin"):
        shown = standin(shown)
    phrase = str(seg.get("phrase") or "")
    return (f"{phrase} " if phrase else "") + shown + str(seg.get("tail") or "")


def _render(
    segments: list[Mapping[str, Any]],
    mark: Callable[[str], str] | None = None,
    standin: Callable[[str], str] | None = None,
) -> str:
    """The segments as text, one sentence per line; ``mark`` wraps a disabled segment's
    chunk (d8) and ``standin`` a stand-in's shown text, if given."""
    lines: list[str] = []
    last: Any = object()
    for seg in segments:
        chunk = _chunk(seg, standin)
        if not chunk:
            continue
        if mark and seg.get("disabled"):
            chunk = mark(chunk)
        if lines and seg.get("sentence") == last:
            lines[-1] += chunk
        else:
            lines.append(chunk)
        last = seg.get("sentence")
    return "\n".join(lines)


def mark_disabled(chunk: str) -> str:
    """A disabled segment's chunk as the CLI shows it (d13): :data:`PHRASES`
    ``disabled_mark`` after its words, before the sentence's closing period
    (``It starts on cron 0 5 * * *, on spark2 (disabled).``)."""
    mark = " " + PHRASES["disabled_mark"]
    body = chunk.rstrip()
    if body.endswith((".", "?", "!")):
        return body[:-1] + mark + body[-1] + chunk[len(body) :]
    return body + mark + chunk[len(body) :]


def render_marked(
    paragraph: Mapping[str, Any],
    mark: Callable[[str], str],
    standin: Callable[[str], str] | None = None,
) -> str:
    """A paragraph's text (``workflow_paragraph``'s or ``step_paragraph``'s payload, or its
    JSON form) with every disabled segment wrapped by ``mark`` and every stand-in's text by
    ``standin``; without the marks it equals ``text``."""
    segs = [s for s in paragraph.get("segments") or [] if isinstance(s, Mapping)]
    return _render(segs, mark, standin)


# --------------------------------------------------------------------------- the rules (t7)

_RUN_EVENT = "rules.run."
_RUN_OUTCOMES = {"succeeded": "starts_succeeded", "failed": "starts_failed"}


def compose_sentence(parts: list[Part]) -> str:
    """One sentence from table entries, in order: each entry without its period, the later
    ones starting lower-case, joined by commas and ended by one period."""
    texts = []
    for i, (key, params) in enumerate(parts):
        template = PHRASES[key][:-1]
        if i:
            template = template[0].lower() + template[1:]
        texts.append(template.format(**params))
    return ", ".join(texts) + "." if texts else ""


def _sentence(part: str, parts: list[Part], **extra: Any) -> dict[str, Any]:
    return {"part": part, "keys": [k for k, _ in parts], "text": compose_sentence(parts), **extra}


def _ids(value: Any) -> list[str]:
    return [str(x) for x in _list(value) if x not in (None, "")]


def _workflow_literal(field: Any, literal: Any) -> str | None:
    """``data.workflow_id`` compared with a string literal: that literal (fold/model.ts)."""
    if not isinstance(field, Mapping) or not isinstance(literal, Mapping):
        return None
    if field.get("field") != "data.workflow_id" or not isinstance(literal.get("literal"), str):
        return None
    return literal["literal"]


def _predecessor_id(node: Any) -> str | None:
    if not isinstance(node, Mapping) or node.get("op") != "compare" or node.get("cmp") != "==":
        return None
    left, right = node.get("left"), node.get("right")
    found = _workflow_literal(left, right)
    return found if found is not None else _workflow_literal(right, left)


def _predecessor_terms(condition: Any) -> list[Any]:
    """``predecessorTerms``: the qualifying equality nodes in order, through ``and`` only."""
    if not isinstance(condition, Mapping):
        return []
    if condition.get("op") == "and":
        return [t for a in _list(condition.get("args")) for t in _predecessor_terms(a)]
    return [condition] if _predecessor_id(condition) is not None else []


def _either_sources(condition: Any) -> list[str]:
    """``eitherSources``: an ``or`` whose every branch names exactly one predecessor (through
    ``and``) continues from any of them (d3); the distinct ids in order, else none."""
    if not isinstance(condition, Mapping) or condition.get("op") != "or":
        return []
    ids = []
    for branch in _list(condition.get("args")):
        terms = _predecessor_terms(branch)
        if len(terms) != 1:
            return []
        ids.append(_predecessor_id(terms[0]))
    distinct = _distinct([i for i in ids if i is not None])
    return distinct if len(distinct) > 1 else []


def _entry(rule: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    """``entryFor``: a rule on a ``rules.run.*`` event continues from its predecessor."""
    base = {"rule": rule, "workflow_id": workflow_id, "enabled": rule.get("enabled") is not False}
    trigger = _plain(rule.get("trigger"))
    params = trigger.get("params")
    kind = params.get("type") if isinstance(params, Mapping) else None
    if (
        trigger.get("kind") != "event"
        or not isinstance(kind, str)
        or not kind.startswith(_RUN_EVENT)
    ):
        return {**base, "kind": "entry"}
    sources = [_predecessor_id(t) for t in _predecessor_terms(rule.get("condition"))]
    either = [] if sources else _either_sources(rule.get("condition"))
    if either:
        predecessor: dict[str, Any] = {"kind": "either", "workflow_ids": either}
    elif not sources:
        predecessor = {"kind": "any"}
    elif len(sources) == 1:
        predecessor = {"kind": "linked", "workflow_id": sources[0]}
    else:
        predecessor = {"kind": "ambiguous", "workflow_ids": sources}
    linked = predecessor.get("workflow_id")
    return {**base, "kind": "continuation", "predecessor": predecessor, "from_workflow_id": linked}


def fold_rules(rules: Any) -> list[dict[str, Any]]:
    """The editor's fold (web/src/fold/model.ts ``foldModel``), in input order: one entry per
    rule that runs a workflow, ``{"rule", "workflow_id", "enabled", "kind"}``, where ``kind``
    is ``entry`` (it starts the workflow from outside) or ``continuation`` (it fires on a
    ``rules.run.*`` event; it also carries ``predecessor`` - ``any``, ``linked`` with its
    ``workflow_id``, ``ambiguous`` with ``workflow_ids`` (an ``and`` naming several) or
    ``either`` with ``workflow_ids`` (an ``or`` of one per branch, d3) - and
    ``from_workflow_id``). Both
    kinds belong to the workflow they start. A rule with no workflow is left out (D7)."""
    out = []
    for raw in _list(rules):
        rule = _plain(raw)
        ref = _plain(rule.get("workflow"))
        if ref.get("id"):
            out.append(_entry(rule, str(ref["id"])))
    return out


def onward_of(fold: list[dict[str, Any]], workflow_id: str) -> list[dict[str, Any]]:
    """The continuations that start after ``workflow_id`` in another workflow: linked to it,
    after any workflow, or naming it among several or as one of its ``or`` branches
    (SimpleView.tsx ``onwardOf``)."""
    out = []
    for e in fold:
        if e["kind"] != "continuation" or e["workflow_id"] == workflow_id:
            continue
        pred = e["predecessor"]
        if (
            e["from_workflow_id"] == workflow_id
            or pred["kind"] == "any"
            or (pred["kind"] in ("ambiguous", "either") and workflow_id in pred["workflow_ids"])
        ):
            out.append(e)
    return out


def _without_terms(condition: Any, terms: list[Any]) -> Any:
    """``condition``'s all-terms (through nested ``and``) other than ``terms``; ``None`` when
    none are left, the one term alone, else an ``and`` of them."""
    if condition is None or any(condition is t for t in terms):
        return None
    if not isinstance(condition, Mapping) or condition.get("op") != "and":
        return condition

    def flat(node: Any) -> list[Any]:
        if isinstance(node, Mapping) and node.get("op") == "and":
            return [x for a in _list(node.get("args")) for x in flat(a)]
        return [node]

    rest = [t for t in flat(condition) if not any(t is x for x in terms)]
    if not rest:
        return None
    return rest[0] if len(rest) == 1 else {"op": "and", "args": rest}


def _trigger_part(rule: dict[str, Any], entry: dict[str, Any]) -> tuple[Part, Any]:
    """How the rule fires, and the condition left to say once that is said."""
    condition = rule.get("condition")
    trigger = _plain(rule.get("trigger"))
    if entry["kind"] == "continuation" and entry["predecessor"]["kind"] == "linked":
        outcome = str(_plain(trigger.get("params")).get("type"))[len(_RUN_EVENT) :]
        rest = _without_terms(condition, _predecessor_terms(condition))
        source = entry["from_workflow_id"]
        key = _RUN_OUTCOMES.get(outcome)
        if key:
            return (key, {"workflow": source}), rest
        return ("starts_ended", {"workflow": source, "outcome": outcome}), rest
    if entry["kind"] == "continuation" and entry["predecessor"]["kind"] == "either":
        outcome = str(_plain(trigger.get("params")).get("type"))[len(_RUN_EVENT) :]
        source = _either(entry["predecessor"]["workflow_ids"])
        # each branch's other terms belong to that branch only: say the whole condition
        # unless every branch is its predecessor term alone (d3)
        alone = all(
            _without_terms(b, _predecessor_terms(b)) is None
            for b in _list(_plain(condition).get("args"))
        )
        rest = None if alone else condition
        key = _RUN_OUTCOMES.get(outcome)
        if key:
            return (key, {"workflow": source}), rest
        return ("starts_ended", {"workflow": source, "outcome": outcome}), rest
    words = trigger_text(trigger)
    kind = trigger.get("kind")
    if kind == "event":
        return ("starts_event", {"trigger": words}), condition
    if kind == "manual":
        return ("starts_manual", {"trigger": words}), condition
    return ("starts_on", {"trigger": words}), condition


def _relationship_parts(rule: dict[str, Any]) -> list[Part]:
    parts: list[Part] = []
    must, may, sup = (_ids(rule.get(k)) for k in ("must_after", "may_after", "supersedes"))
    if must:
        parts.append(("rule_must_after", {"rules": _names(must)}))
    if may:
        parts.append(
            ("rule_may_after", {"rules": _names(may), "it": "it" if len(may) == 1 else "they"})
        )
    if sup:
        parts.append(("rule_supersedes", {"rules": _names(sup)}))
    group = rule.get("exclusive_group")
    if isinstance(group, str) and group:
        priority = rule.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool):
            priority = 0  # the model's default; never a raw value in prose
        parts.append(("rule_group", {"group": group, "priority": priority}))
    return parts


def rule_sentence(rule: Any) -> dict[str, Any]:
    """One built sentence for a rule that starts a workflow (a :class:`Rule` or its dict
    form): how it fires (its trigger in describe.py's words, or, for a continuation, the
    workflow whose run it follows), its condition in words (a continuation's predecessor term
    is already said), its relationships, its placement and whether it is disabled.

    Returns ``{"part": "start", "rule", "kind", "enabled", "keys", "text"}`` (plus
    ``"disabled": True`` for a disabled rule, d8, d13: no words in ``text``; the CLI marks it
    ``(disabled)``, the editor tags it);
    ``keys`` names
    the :data:`PHRASES` entries the sentence is composed from. Never raises."""
    r = _plain(rule)
    entry = _entry(r, str(_plain(r.get("workflow")).get("id") or ""))
    lead, condition = _trigger_part(r, entry)
    parts = [lead]
    if condition:
        words = condition_words(condition)
        if words == CONDITION_FALLBACK:
            parts.append(("rule_if_unspoken", {}))
        else:
            parts.append(("rule_if", {"condition": words}))
    parts += _relationship_parts(r)
    where = placement_text(r.get("placement"), as_actor=False)
    if where:
        parts.append(("rule_where", {"where": where}))
    sentence = _sentence(
        "start", parts, rule=str(r.get("id") or "?"), kind=entry["kind"], enabled=entry["enabled"]
    )
    if r.get("enabled") is False:
        sentence["disabled"] = True  # d8, d13: no words; the CLI marks it, the editor tags it
    return sentence


def _distinct(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _either(labels: list[str]) -> str:
    """``a`` / ``a or b`` / ``a, b or c``."""
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1]


def _action_kind(action: Any) -> Any:
    return _plain(action).get("kind")


_STACKED = re.compile(r"^(.*)\(([^()]*)\) \(([^()]*)\)$")
_CHAIN_END = "only where its chain ends"


def _unstacked(text: str) -> str:
    """``x (a) (b)`` -> ``x (a, b)``: an action's trailing bracket groups read as one."""
    while m := _STACKED.match(text):
        text = f"{m.group(1)}({m.group(2)}, {m.group(3)})"
    return text


def _status(action: Any) -> bool:
    """A ``github.comment`` that writes its chain's status comment (d26)."""
    a = _plain(action)
    params = a.get("params")
    return (
        a.get("kind") == "github.comment"
        and isinstance(params, Mapping)
        and (params.get("status") is True)
    )


def _chain_end(action: Any) -> bool:
    return _plain(action).get("only_at_chain_end") is True


def _action_words(action: Any) -> str:
    """An action's d19 words, its chain-end note left to the sentence's lead-in."""
    a = _plain(action)
    a.pop("only_at_chain_end", None)
    return _unstacked(action_text(a))


def _by(action: Any) -> str:
    """`` (as github-app)``: who writes a status comment, and its other guard words."""
    params = _plain(_plain(action).get("params"))
    words = [actor_words(params)]
    words += [w.strip("()") for w in comment_words({**params, "status": False})]
    words = [w for w in words if w]
    return " " + PHRASES["by"].format(words=", ".join(words)) if words else ""


def _ends(rules: list[dict[str, Any]]) -> list[Part]:
    actions = [r.get("action") for r in rules]
    if all(_action_kind(a) == "noop" for a in actions):
        return [("ends_noop", {})]
    texts = _distinct([_unstacked(action_text(a)) for a in actions])
    if len(texts) > 1:
        return [("ends_differs", {"actions": _either(texts)})]
    action = actions[0]
    chain = _chain_end(action)
    if _status(action):
        return [("ends_status_chain" if chain else "ends_status", {"by": _by(action)})]
    return [("ends_chain" if chain else "ends", {"action": _action_words(action)})]


def _on_failure(rules: list[dict[str, Any]], said_status: bool) -> list[Part]:
    actions = [r.get("on_failure") for r in rules]
    texts = _distinct([_unstacked(action_text(a)) for a in actions if a])
    if not texts:
        return []
    if len(texts) > 1 or not all(actions):
        return [("on_failure_differs", {"actions": _either(texts)})]
    action = actions[0]
    if _status(action):
        if said_status:
            return [("on_failure_status_same", {})]
        return [("on_failure_status", {"by": _by(action)})]
    return [("on_failure", {"action": texts[0]})]


def _runs(rules: list[dict[str, Any]]) -> list[Part]:
    keys = _distinct(
        [key_text(str(r["concurrency_key"])) if r.get("concurrency_key") else "" for r in rules]
    )
    attempts = _distinct([str(r.get("max_attempts") or "") for r in rules])
    if len(keys) > 1 or len(attempts) > 1:
        return [("runs_differs", {})]
    if not keys[0]:
        return []
    parts: list[Part] = [("runs_key", {"key": keys[0]})]
    if attempts[0]:
        parts.append(("runs_attempts", {"n": attempts[0]}))
    return parts


def then_sentence(rules: Any, onward: Any = ()) -> list[dict[str, Any]]:
    """The Then side of a workflow, as built sentences: where it continues (``onward``: the
    continuations of other workflows that start after it, from :func:`onward_of`), what
    happens when it is done (the rules' ``action``; a status comment, d26, reads as updating
    the chain's status comment, and ``only_at_chain_end`` as ``Where the chain ends here``),
    what happens on failure (a status comment after one reads ``it updates that comment
    with the failure``), and how it runs (run key and attempt budget). ``rules`` are the
    rules that start the workflow; a value they all hold reads once, values that differ read
    as alternatives. An action's trailing bracket groups read as one. Each sentence is
    ``{"part": "then", "keys", "text", "rules"}``."""
    plain = [_plain(r) for r in _list(rules)]
    ids = [str(r.get("id") or "?") for r in plain]
    nxt = [e for e in _list(onward) if isinstance(e, Mapping)]
    out = []
    if nxt:
        targets = _distinct([str(e["workflow_id"]) for e in nxt])
        rule_ids = [str(_plain(e["rule"]).get("id") or "?") for e in nxt]
        out.append(
            _sentence("then", [("continues", {"workflows": _either(targets)})], rules=rule_ids)
        )
    else:
        out.append(_sentence("then", [("continues_none", {})], rules=[]))
    if plain:
        ends = _ends(plain)
        said_status = ends[0][0] in ("ends_status", "ends_status_chain")
        for parts in (ends, _on_failure(plain, said_status), _runs(plain)):
            if parts:
                out.append(_sentence("then", parts, rules=ids))
    return out


def workflow_paragraph(workflow: Any, rules: Any) -> dict[str, Any]:
    """``workflow``'s whole paragraph, given every rule (the fold picks the ones that start
    it and the ones that continue from it): one sentence per starting rule
    (:func:`rule_sentence`, in input order; ``No rule starts it yet.`` when none does), the
    step sentences (:func:`step_paragraph`), then the Then side (:func:`then_sentence`).

    Returns ``{"segments", "text"}``; each segment carries ``part`` (``start``, ``step`` or
    ``then``) and ``sentence`` (0-based over the whole paragraph), step segments are
    :func:`step_paragraph`'s, and ``text`` is the sentences, one per line."""
    wf = _plain(workflow)
    wid = str(wf.get("id") or "")
    fold = fold_rules(rules)
    owned = [e for e in fold if e["workflow_id"] == wid]
    if owned:
        starts = [rule_sentence(e["rule"]) for e in owned]
    else:
        starts = [_sentence("start", [("starts_none", {})], rule=None, kind=None, enabled=None)]
    steps = step_paragraph(wf)["segments"]
    thens = then_sentence([e["rule"] for e in owned], onward_of(fold, wid))
    n = len(starts)
    segments = [{**s, "sentence": i} for i, s in enumerate(starts)]
    segments += [{"part": "step", **seg, "sentence": seg["sentence"] + n} for seg in steps]
    n += len({seg["sentence"] for seg in steps})
    segments += [{**t, "sentence": n + i} for i, t in enumerate(thens)]
    return {"segments": segments, "text": _render(segments)}
