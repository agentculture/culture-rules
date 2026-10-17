"""A workflow's steps as one paragraph: each step's description joined by fixed phrases
(#69). Standard-library only, and no AI.

Every joining word comes from :data:`PHRASES`, chosen from a fact of the step graph or a
config key of the step it introduces; the rest of the paragraph is the steps' own
descriptions, unchanged. The phrases follow the engine's step semantics (engine/runs.py,
read here, not imported):

* a top-level step runs once every step feeding it through an edge is done
  (``_deps_done``), so the joins are read off the edge graph, on its transitive reduction
  (an edge already implied by a longer path never adds a join);
* steps with the same predecessors and no path between them run at the same time, unless
  two or more of them carry a ``config.when``: then they read as alternatives;
* a loop's body runs in declared order (``_earlier_iteration_steps_ok``), whatever its
  edges say, so body steps read ``Then`` one after another;
* ``config.when``, ``config.on_error: "skip"``, a retry policy, a wait step's seconds and
  guard, ``for_each`` and ``retry_until`` each add their own phrase; a ``when`` shared by
  every remaining step of a level is said once;
* consecutive steps sharing an identical ``when`` (d5) read as one sentence: the first says
  the condition, and each following step whose phrase would be only ``Then, if <it>:`` is
  joined with ``; then`` - the one place a description is touched: the previous one's final
  period is dropped and the joined one's first letter lower-cased (not before a non-letter
  or an all-caps word such as ``EPUB``).

A step whose kind or config the table has no phrase for reads as its d19 line
(:func:`culture_rules.model.describe.step_text`) with no phrase, and a step with an empty
description reads as a visible gap naming it; describing never raises.

The rules around the steps (t7, c37): :func:`workflow_paragraph` opens with one built
sentence per rule that starts the workflow (:func:`rule_sentence`: its trigger, condition,
relationships and placement) and closes with the Then side (:func:`then_sentence`: where it
continues, what runs when it is done and on failure, how it runs). Those sentences are
composed from :data:`PHRASES` entries ending with a period (:data:`SENTENCE_KEYS`) filled
with describe.py's words (``trigger_text``, ``condition_words``, ``action_text``, the
placement and run-key words); a rule's name and description, and an action's name and body,
are never read. Which rules start a workflow and which continue into another mirrors the
editor's fold (web/src/fold/model.ts ``foldModel``): :func:`fold_rules`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.model import condition as cond
from culture_rules.model.describe import (
    CONDITION_FALLBACK,
)
from culture_rules.model.describe import _key_text as key_text
from culture_rules.model.describe import _placement as placement_text
from culture_rules.model.describe import (
    action_text,
    condition_words,
    step_text,
    trigger_text,
)
from culture_rules.model.workflow import STEP_KINDS

__all__ = [
    "GAP",
    "PHRASES",
    "SENTENCE_KEYS",
    "compose",
    "compose_sentence",
    "fold_rules",
    "onward_of",
    "rule_sentence",
    "step_paragraph",
    "then_sentence",
    "workflow_paragraph",
]

#: Every joining phrase, one per construct; each ends with a colon and the step's
#: description follows it as its own sentence (all but ``then_grouped``, see below). A
#: step's phrase is one or more of these, composed by :func:`compose`
#: (``Then:`` + ``If {condition}:`` -> ``Then, if x is 1:``).
PHRASES: dict[str, str] = {
    # how a step follows the ones before it
    "first": "First:",
    "then": "Then:",
    "same_time": "At the same time:",
    "depending": "Depending on the conditions:",
    "in_any_case": "In any case:",
    "once_both": "Once both are done:",
    "once_those": "Once those are done:",
    "once_one": "Once {step} is done:",
    "once_many": "Once {steps} are done:",
    "from_start": "Separately, from the start:",
    "each_time": "Each time:",
    # what the step's own config adds
    "from_here_on": "From here on, only if {condition}:",
    "from_here_on_unspoken": "From here on, only if their shared condition holds:",
    "if": "If {condition}:",
    "if_unspoken": "If its condition holds:",
    "wait": "It waits {duration}:",
    "wait_guard": "And the run stops if the PR head moves:",
    "for_each": "For each item (up to {n}):",
    "retry_until": "It tries until {condition}, at most {times}:",
    "retry_until_unspoken": "It tries until its condition holds, at most {times}:",
    "retry": "Trying up to {n} times:",
    "on_error_skip": "Carrying on even if it fails:",
    "disabled": "Disabled, so it never runs:",
    # d5: the one connector inside a sentence, not followed by a colon - a step whose phrase
    # would be exactly ``Then, if <the previous step's condition>:`` joins its sentence
    "then_grouped": "; then",
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
    "rule_disabled": "but it is disabled.",
    # the Then side
    "continues": "Then it continues into {workflows}.",
    "continues_none": "Nothing continues from here.",
    "ends": "When it is done, it runs {action}.",
    "ends_noop": "When it is done, nothing else happens.",
    "ends_differs": "When it is done, it runs {actions}, depending on the rule that started it.",
    "on_failure": "If it fails, it runs {action}.",
    "on_failure_differs": "If it fails, it runs {actions}, depending on the rule that started it.",
    "runs_key": "It runs one at a time per {key}.",
    "runs_attempts": "with at most {n} attempts.",
    "runs_differs": "How it runs differs per rule that starts it.",
}

#: The :data:`PHRASES` entries that build the rule and Then sentences (each ends with a
#: period); every other entry joins a step's description (and ends with a colon).
SENTENCE_KEYS: frozenset[str] = frozenset(
    k
    for k in PHRASES
    if k.startswith(("starts_", "rule_", "continues", "ends", "on_failure", "runs_"))
)

#: What stands in for a step with no description: visible, and naming the step.
GAP = "[{step} has no description yet]"

_WAIT_GUARDS = ("head_unchanged",)
_TIMES = {1: "once", 2: "twice"}
_UNITS = ((3600, "hour"), (60, "minute"))

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
    """One phrase from table entries, in order: each entry without its colon, the later ones
    starting lower-case, joined by commas and ended by one colon."""
    texts = []
    for i, (key, params) in enumerate(parts):
        template = PHRASES[key][:-1]
        if i:
            template = template[0].lower() + template[1:]
        texts.append(template.format(**params))
    return ", ".join(texts) + ":" if texts else ""


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


def _own_parts(s: Mapping[str, Any], shared: bool, said_if: bool) -> list[Part]:
    """The phrases a step's own config adds, in a fixed order."""
    config = _config(s)
    parts: list[Part] = []
    if "when" in config and not shared and not said_if:
        parts.append(_condition_part("if", config["when"]))
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
    retry = s.get("retry")
    if isinstance(retry, Mapping) and (retry.get("max_attempts") or 1) > 1:
        parts.append(("retry", {"n": retry["max_attempts"]}))
    if config.get("on_error") == "skip":
        parts.append(("on_error_skip", {}))
    if s.get("enabled") is False:
        parts.append(("disabled", {}))
    return parts


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
    """The joining phrases of the ``k``-th step of group ``g``."""
    group = groups[g]
    has_when = _when(steps[group[k]]) is not None
    if _alternatives(steps, group):
        if k == 0:
            join = _join(graph, steps, groups, g)
            lead = [] if join[0] in ("first", "then") else [join]
            return lead + [("depending", {})] + ([] if has_when else [("in_any_case", {})])
        return [] if has_when else [("in_any_case", {})]
    return [_join(graph, steps, groups, g) if k == 0 else ("same_time", {})]


# --------------------------------------------------------------------------- segments


def _segment(
    s: dict[str, Any], parts: list[Part], ok: bool, label: str, depth: int
) -> dict[str, Any]:
    if not ok:
        return _seg(s, "", [], f"{label} {_sid(s)} — {_d19(s)}", depth, fallback=True)
    description = s.get("description")
    text = description if isinstance(description, str) else ""
    keys = [k for k, _ in parts]
    phrase = PHRASES["then_grouped"] if keys == ["then_grouped"] else compose(parts)
    return _seg(s, phrase, keys, text, depth, fallback=False)


def _d19(s: Mapping[str, Any]) -> str:
    """The step's d19 words; a config even those cannot read (``step_text`` compares the
    retry bound as a number) reads as its kind alone."""
    try:
        return step_text(s)
    except (TypeError, ValueError, AttributeError):
        return str(s.get("kind") or "step")


def _seg(
    s: Mapping[str, Any], phrase: str, keys: list[str], text: str, depth: int, *, fallback: bool
) -> dict[str, Any]:
    return {
        "phrase": phrase,
        "step": _sid(s),
        "text": text,
        "keys": keys,
        "depth": depth,
        "gap": not fallback and not text,
        "fallback": fallback,
        "grouped": keys == ["then_grouped"],
        "name": _label(s),
    }


def _level_parts(
    steps: list[dict[str, Any]], ok: list[bool], leads: dict[int, list[Part]]
) -> dict[int, list[Part]]:
    """Each step's full phrase at one level: its lead, then the shared condition where it
    starts, then its own config's phrases."""
    shared = _shared_from(steps, ok)
    out = {}
    for i, s in enumerate(steps):
        parts = list(leads[i])
        said_if = False
        if shared is not None and i == shared:
            parts.append(_condition_part("from_here_on", _when(s)))
            said_if = True
        in_run = shared is not None and i > shared
        parts += _own_parts(s, in_run, said_if) if ok[i] else []
        out[i] = parts or [("same_time", {})]
    return _grouped(steps, ok, out)


_IF_KEYS = ("if", "if_unspoken")


def _ends_sentence(s: Mapping[str, Any]) -> bool:
    description = s.get("description")
    return isinstance(description, str) and description.endswith(".")


def _grouped(
    steps: list[dict[str, Any]], ok: list[bool], parts: dict[int, list[Part]]
) -> dict[int, list[Part]]:
    """d5: a step whose phrase is exactly ``Then, if <C>:``, right after a step that said
    the identical condition ``C`` (or was itself joined to one), joins its sentence. A loop
    (its body reads in between), a gap or a description without a final period ends the
    sentence instead."""
    open_when: Any = None
    for i, s in enumerate(steps):
        keys = [k for k, _ in parts[i]]
        joins = (
            open_when is not None
            and ok[i]
            and len(keys) == 2
            and keys[0] == "then"
            and keys[1] in _IF_KEYS
            and _when(s) == open_when
            and isinstance(s.get("description"), str)
            and bool(s["description"])
        )
        if joins:
            parts[i] = [("then_grouped", {})]
        elif ok[i] and any(k in _IF_KEYS for k in keys):
            open_when = _when(s)
        else:
            open_when = None
        if s.get("kind") in ("for_each", "retry_until") or not _ends_sentence(s):
            open_when = None
    return parts


def _body_segments(loop: dict[str, Any], label: str) -> list[dict[str, Any]]:
    body = _body(loop)
    ok = [_recognised(b) for b in body]
    leads = {i: [("each_time", {}) if i == 0 else ("then", {})] for i in range(len(body))}
    parts = _level_parts(body, ok, leads)
    return [_segment(b, parts[i], ok[i], f"{label}.{i + 1}", 1) for i, b in enumerate(body)]


def step_paragraph(workflow: Any) -> dict[str, Any]:
    """``workflow``'s steps (a :class:`Workflow` or its dict form) as one paragraph.

    Returns ``{"segments", "text"}``: one segment per step, top level in a stable
    topological order (declared order where it is one) and each loop's body right after its
    loop, in declared order. A segment is ``{"phrase", "step", "text", "keys", "depth",
    "gap", "fallback", "grouped", "name"}``: ``keys`` names the :data:`PHRASES` entries its
    phrase is composed from, ``text`` is the step's description unchanged (``gap`` when it
    is empty), and a step the table has no phrase for has no phrase and its d19 line as ``text``
    (``fallback``). ``text`` joins the segments: the phrase, then the description or
    :data:`GAP`, or the d19 line alone; a ``grouped`` segment (d5) continues the previous
    sentence with ``; then`` and its description, first letter lower-cased.
    """
    segments = _segments(_plain(workflow))
    return {"segments": segments, "text": _render(segments)}


def _segments(wf: dict[str, Any]) -> list[dict[str, Any]]:
    steps = [_plain(s) for s in _list(wf.get("steps"))]
    graph = _Graph(steps, _list(wf.get("edges")))
    order = graph.order()
    groups = _groups(graph, steps, order)
    leads: dict[int, list[Part]] = {}
    for g, group in enumerate(groups):
        for k, i in enumerate(group):
            leads[i] = _lead_parts(graph, steps, groups, g, k)
    # the shared-condition run is read in reading order, so re-index by position in it
    ordered = [steps[i] for i in order]
    ok = [_recognised(s) for s in ordered]
    parts = _level_parts(ordered, ok, {n: leads[i] for n, i in enumerate(order)})
    out: list[dict[str, Any]] = []
    for n, i in enumerate(order):
        s = steps[i]
        out.append(_segment(s, parts[n], ok[n], str(i + 1), 0))
        if s.get("kind") in ("for_each", "retry_until"):
            out += _body_segments(s, str(i + 1))
    return out


def _lower_first(text: str) -> str:
    """``Packs it.`` -> ``packs it.``; text opening with a non-letter (a quote, a digit) or
    an all-caps word (``EPUB``, ``PR``) is left as it is."""
    word = text.split(" ", 1)[0]
    if not text[:1].isupper() or (len(word) > 1 and word.isupper()):
        return text
    return text[0].lower() + text[1:]


def _render(segments: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for seg in segments:
        if seg["grouped"] and chunks and chunks[-1].endswith("."):
            chunks[-1] = f"{chunks[-1][:-1]}{seg['phrase']} {_lower_first(seg['text'])}"
            continue
        if seg["fallback"]:
            chunks.append(seg["text"])
            continue
        shown = seg["text"] or GAP.format(step=seg["name"])
        chunks.append(f"{seg['phrase']} {shown}")
    return " ".join(chunks)


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
    if not sources:
        predecessor: dict[str, Any] = {"kind": "any"}
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
    ``workflow_id`` or ``ambiguous`` with ``workflow_ids`` - and ``from_workflow_id``). Both
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
    after any workflow, or naming it among several (SimpleView.tsx ``onwardOf``)."""
    out = []
    for e in fold:
        if e["kind"] != "continuation" or e["workflow_id"] == workflow_id:
            continue
        pred = e["predecessor"]
        if (
            e["from_workflow_id"] == workflow_id
            or pred["kind"] == "any"
            or (pred["kind"] == "ambiguous" and workflow_id in pred["workflow_ids"])
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
        parts.append(("rule_group", {"group": group, "priority": rule.get("priority", 0)}))
    return parts


def rule_sentence(rule: Any) -> dict[str, Any]:
    """One built sentence for a rule that starts a workflow (a :class:`Rule` or its dict
    form): how it fires (its trigger in describe.py's words, or, for a continuation, the
    workflow whose run it follows), its condition in words (a continuation's predecessor term
    is already said), its relationships, its placement and whether it is disabled.

    Returns ``{"part": "start", "rule", "kind", "enabled", "keys", "text"}``; ``keys`` names
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
    if r.get("enabled") is False:
        parts.append(("rule_disabled", {}))
    return _sentence(
        "start", parts, rule=str(r.get("id") or "?"), kind=entry["kind"], enabled=entry["enabled"]
    )


def _distinct(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _either(labels: list[str]) -> str:
    """``a`` / ``a or b`` / ``a, b or c``."""
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1]


def _action_kind(action: Any) -> Any:
    return _plain(action).get("kind")


def _ends(rules: list[dict[str, Any]]) -> list[Part]:
    actions = [r.get("action") for r in rules]
    if all(_action_kind(a) == "noop" for a in actions):
        return [("ends_noop", {})]
    texts = _distinct([action_text(a) for a in actions])
    if len(texts) == 1:
        return [("ends", {"action": texts[0]})]
    return [("ends_differs", {"actions": _either(texts)})]


def _on_failure(rules: list[dict[str, Any]]) -> list[Part]:
    actions = [r.get("on_failure") for r in rules]
    texts = _distinct([action_text(a) for a in actions if a])
    if not texts:
        return []
    if len(texts) == 1 and all(actions):
        return [("on_failure", {"action": texts[0]})]
    return [("on_failure_differs", {"actions": _either(texts)})]


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
    continuations of other workflows that start after it, from :func:`onward_of`), what runs
    when it is done (the rules' ``action``), what runs on failure, and how it runs (run key
    and attempt budget). ``rules`` are the rules that start the workflow; a value they all
    hold reads once, values that differ read as alternatives. Each sentence is
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
        for parts in (_ends(plain), _on_failure(plain), _runs(plain)):
            if parts:
                out.append(_sentence("then", parts, rules=ids))
    return out


def workflow_paragraph(workflow: Any, rules: Any) -> dict[str, Any]:
    """``workflow``'s whole paragraph, given every rule (the fold picks the ones that start
    it and the ones that continue from it): one sentence per starting rule
    (:func:`rule_sentence`, in input order; ``No rule starts it yet.`` when none does), the
    step paragraph (:func:`step_paragraph`), then the Then side (:func:`then_sentence`).

    Returns ``{"segments", "text"}``; each segment carries ``part`` (``start``, ``step`` or
    ``then``), step segments are :func:`step_paragraph`'s, and ``text`` joins them."""
    wf = _plain(workflow)
    wid = str(wf.get("id") or "")
    fold = fold_rules(rules)
    owned = [e for e in fold if e["workflow_id"] == wid]
    if owned:
        starts = [rule_sentence(e["rule"]) for e in owned]
    else:
        starts = [_sentence("start", [("starts_none", {})], rule=None, kind=None, enabled=None)]
    steps = step_paragraph(wf)
    thens = then_sentence([e["rule"] for e in owned], onward_of(fold, wid))
    segments = starts + [{"part": "step", **seg} for seg in steps["segments"]] + thens
    text = " ".join(
        x for x in (*(s["text"] for s in starts), steps["text"], *(t["text"] for t in thens)) if x
    )
    return {"segments": segments, "text": text}
