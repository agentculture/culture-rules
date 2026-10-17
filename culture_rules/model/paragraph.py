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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.model import condition as cond
from culture_rules.model.describe import CONDITION_FALLBACK, condition_words, step_text
from culture_rules.model.workflow import STEP_KINDS

__all__ = ["GAP", "PHRASES", "compose", "step_paragraph"]

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
}

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
