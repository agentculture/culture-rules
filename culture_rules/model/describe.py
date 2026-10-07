"""Plain, deterministic descriptions of a rule and of a workflow (d19). Standard-library only.

No AI and no free text: every phrase comes from the definition's config through the small,
fixed vocabularies below (one entry per step kind, built-in, action kind, trigger kind and
condition operator). Anything outside them falls back to its raw name, so describing never
fails on an unknown kind or a malformed config. The names, descriptions and other prose
fields of a definition are deliberately not used.

The result is a list of *entries*, each ``{"label", "text", "depth"}`` plus ``"step"`` (the
step id) on workflow entries:

* a rule reads as labelled lines - ``When``, ``If`` / ``and`` / ``or``, ``After``, ``Run``,
  ``On``, ``Then``, ``On failure``, ``Key``, ``Group``, ``Disabled``;
* a workflow reads as numbered steps (``1``, ``3.1`` ...), a loop's body one level deeper.

:func:`render` turns entries into the text lines the CLI prints and the API returns as
``lines``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

__all__ = [
    "ACTION_WORDS",
    "BUILTIN_WORDS",
    "CMP_SYMBOLS",
    "action_text",
    "condition_text",
    "describe_rule",
    "describe_workflow",
    "render",
    "step_text",
    "trigger_text",
]

#: Comparison operators as symbols (``conclusion ≠ success``).
CMP_SYMBOLS: dict[str, str] = {"==": "=", "!=": "≠", "<": "<", "<=": "≤", ">": ">", ">=": "≥"}
#: ``not (a == b)`` reads as ``a ≠ b``; only the equalities negate safely in words.
_NEGATED_CMP: dict[str, str] = {"==": "≠", "!=": "="}

#: Built-in ``code`` steps (``config.builtin``): a gloss appended after the name, or a
#: replacement name. ``action`` is handled separately (it reads as its action kind).
BUILTIN_WORDS: dict[str, str] = {
    "gate": "test gate",
    "github.threads": "github.threads{as}: unresolved threads by trusted authors",
    "github.threads_addressed": "github.threads_addressed",
}

#: Per action kind: which literal params are worth a word, as ``(param, template)``.
#: A param holding a reference or a ``{{ }}`` template is resolved at run time, so it is
#: left out rather than shown half-resolved.
ACTION_WORDS: dict[str, tuple[tuple[str, str], ...]] = {
    "message": (("channel", "to {}"),),
    "mesh.message": (("channel", "to {}"),),
    "discord.message": (("channel", "to {}"),),
    "github.comment": (),
    "github.push": (),
    "github.review_reply": (),
    "jira.comment": (("issue", "on {}"),),
    "http.call": (("method", "{}"), ("url", "{}")),
    "machine.command": (("command", "`{}`"),),
    "noop": (),
}

_TRIGGER_WORDS = {"manual": "started by hand", "event": "an event"}
_WAIT_GUARDS = {"head_unchanged": "stop if the PR head moves"}
_REF = re.compile(r"^(trigger|workflow|vars|rule|rules|run|inputs|steps)\.[\w.]+$")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


# --------------------------------------------------------------------------- helpers


def _plain(obj: Any) -> dict[str, Any]:
    """A model's dict form, or the mapping itself; anything else is an empty dict."""
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    return dict(obj) if isinstance(obj, Mapping) else {}


def _entry(label: str, text: str, depth: int = 0, step: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"label": label, "text": text, "depth": depth}
    if step is not None:
        out["step"] = step
    return out


def _num(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _dynamic(value: Any) -> bool:
    """A run-time reference, ``$ref``/``$var`` object or ``{{ }}`` template."""
    if isinstance(value, Mapping):
        return True
    return isinstance(value, str) and ("{{" in value or bool(_REF.match(value.strip())))


def _literal(value: Any) -> str:
    if isinstance(value, str):
        return value if value else '""'
    if isinstance(value, list):
        return "{" + ", ".join(_literal(v) for v in value) + "}"
    if isinstance(value, float | int) and not isinstance(value, bool):
        return _num(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _placement(p: Any, *, as_actor: bool = True) -> str:
    """``on spark2`` / ``as github-app`` / ``on a machine with gpu, cuda``; empty if none."""
    p = _plain(p)
    if p.get("machine"):
        return f"on {p['machine']}"
    if p.get("actor"):
        return f"as {p['actor']}" if as_actor else f"on {p['actor']}'s machine"
    req = p.get("requirement")
    if isinstance(req, list | tuple) and req:
        return "on a machine with " + ", ".join(str(r) for r in req)
    return ""


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------- conditions


def _operand(o: Any) -> str:
    if not isinstance(o, Mapping) or len(o) != 1:
        return "?"
    ((kind, value),) = o.items()
    if kind == "field":
        return str(value).rsplit(".", 1)[-1] or "?"
    if kind == "var":
        return f"vars.{value}"
    if kind == "literal":
        return _literal(value)
    return "?"


def condition_text(node: Any, *, nested: bool = False) -> str:
    """One condition tree in symbols: ``conclusion ≠ success``, ``repo ∈ vars.repos``.

    Fields read as their last path segment; nested groups are parenthesised. An unknown or
    malformed node reads as ``?`` (or its raw ``op``) instead of raising.
    """
    if not isinstance(node, Mapping):
        return "?"
    op = node.get("op")
    if op == "compare":
        sym = CMP_SYMBOLS.get(str(node.get("cmp")), str(node.get("cmp")))
        return f"{_operand(node.get('left'))} {sym} {_operand(node.get('right'))}"
    if op in ("and", "or"):
        args = node.get("args") if isinstance(node.get("args"), list) else []
        text = f" {op} ".join(condition_text(a, nested=True) for a in args)
        return f"({text})" if nested and len(args) > 1 else text
    if op == "not":
        return _negated(node.get("arg"))
    if op == "exists":
        return f"{_operand(node.get('arg'))} exists"
    if op == "in":
        return f"{_operand(node.get('value'))} ∈ {_operand(node.get('items'))}"
    if op == "matches":
        return f"{_operand(node.get('value'))} matches /{node.get('pattern', '')}/"
    return str(op) if op else "?"


def _negated(arg: Any) -> str:
    op = arg.get("op") if isinstance(arg, Mapping) else None
    if op == "in":
        return f"{_operand(arg.get('value'))} ∉ {_operand(arg.get('items'))}"
    if op == "exists":
        return f"{_operand(arg.get('arg'))} missing"
    if op == "matches":
        return f"{_operand(arg.get('value'))} does not match /{arg.get('pattern', '')}/"
    if op == "compare" and arg.get("cmp") in _NEGATED_CMP:
        sym = _NEGATED_CMP[arg["cmp"]]
        return f"{_operand(arg.get('left'))} {sym} {_operand(arg.get('right'))}"
    return f"not ({condition_text(arg)})"


def _condition_entries(tree: Any) -> list[dict[str, Any]]:
    """``If`` first clause, then one ``and``/``or`` line per further top-level clause."""
    if not isinstance(tree, Mapping):
        return []
    op = tree.get("op")
    args = tree.get("args")
    if op in ("and", "or") and isinstance(args, list) and args:
        first, *rest = args
        return [_entry("If", condition_text(first, nested=True))] + [
            _entry(op, condition_text(a, nested=True)) for a in rest
        ]
    return [_entry("If", condition_text(tree))]


# --------------------------------------------------------------------------- actions


def _actor_of(params: Mapping[str, Any]) -> str:
    actor = params.get("actor")
    return f"as {actor}" if isinstance(actor, str) and actor and not _dynamic(actor) else ""


def action_text(action: Any, where: str = "") -> str:
    """``github.comment as github-app``; literal params named in :data:`ACTION_WORDS`, then
    ``where`` (a step's placement), then the kind's guard words."""
    a = _plain(action)
    kind = str(a.get("kind") or "?")
    params = a.get("params") if isinstance(a.get("params"), Mapping) else {}
    words = [kind, _actor_of(params)]
    for param, template in ACTION_WORDS.get(kind, ()):
        value = params.get(param)
        if value not in (None, "") and not _dynamic(value):
            words.append(template.format(value))
    words.append(where)
    if kind == "github.push" and params.get("gate_verdict"):
        words.append("(only on a passing gate)")
    if kind == "github.review_reply" and params.get("resolve") is True:
        words.append("and resolve")
    return _join(*words)


# --------------------------------------------------------------------------- triggers


def trigger_text(trigger: Any) -> str:
    t = _plain(trigger)
    kind = str(t.get("kind") or "?")
    params = t.get("params") if isinstance(t.get("params"), Mapping) else {}
    if kind == "event":
        return str(params.get("type") or _TRIGGER_WORDS["event"])
    if kind == "schedule":
        tz = params.get("tz") or "UTC"
        return f"cron {params.get('cron', '?')} ({tz})"
    if kind == "probe":
        mode = "on a change" if params.get("mode") != "condition" else "when the condition holds"
        return _join(
            f"probe `{params.get('command', '?')}`",
            f"as {params['actor']}" if params.get("actor") else "",
            f"on cron {params.get('schedule', '?')},",
            mode,
        )
    return _TRIGGER_WORDS.get(kind, kind)


# --------------------------------------------------------------------------- rules


def _key_text(template: str) -> str:
    """``pr-fixer:{trigger.data.repository}#{…number}`` -> ``pr-fixer:{repository}#{number}``."""
    return _PLACEHOLDER.sub(lambda m: "{" + m.group(1).rsplit(".", 1)[-1] + "}", template)


def _workflow_line(ref: Mapping[str, Any], workflow: Any) -> str:
    text = f"workflow {ref.get('id', '?')}"
    if ref.get("version") is not None:
        text += f" v{ref['version']}"
    if workflow is None:
        return text
    wf = _plain(workflow)
    if not wf:
        return text + " (not found)"
    if ref.get("version") is not None and wf.get("version", 1) != ref["version"]:
        # the engine refuses this pairing (workflow_version_unavailable): never describe
        # another version under the pinned one's name
        return text + " (version unavailable)"
    steps = wf.get("steps") if isinstance(wf.get("steps"), list) else []
    text += f" ({len(steps)} step{'' if len(steps) == 1 else 's'}"
    return text + (", disabled)" if wf.get("enabled") is False else ")")


def describe_rule(rule: Any, workflow: Any = None) -> list[dict[str, Any]]:
    """Entries describing ``rule`` (a :class:`Rule` or its dict form).

    ``workflow`` (the referenced workflow, optional) adds its step count; pass ``{}`` for a
    workflow that does not exist (``not found``). A rule pinned to a version other than the
    stored one reads ``version unavailable``, as the engine refuses to run it.
    """
    r = _plain(rule)
    out = [_entry("When", trigger_text(r.get("trigger")))]
    if r.get("condition"):
        out += _condition_entries(r["condition"])
    after = [
        *(f"{x} (must)" for x in r.get("must_after") or ()),
        *(f"{x} (may)" for x in r.get("may_after") or ()),
    ]
    if after:
        out.append(_entry("After", ", ".join(after)))
    if r.get("supersedes"):
        out.append(_entry("Supersedes", ", ".join(str(x) for x in r["supersedes"])))
    ref = r.get("workflow")
    if isinstance(ref, Mapping):
        out.append(_entry("Run", _workflow_line(ref, workflow)))
    where = _placement(r.get("placement"), as_actor=False)
    if where:
        out.append(_entry("On", where.removeprefix("on ")))
    if r.get("action"):
        out.append(_entry("Then", action_text(r["action"])))
    if r.get("on_failure"):
        out.append(_entry("On failure", action_text(r["on_failure"])))
    key = [_key_text(str(r["concurrency_key"]))] if r.get("concurrency_key") else []
    if r.get("max_attempts"):
        key.append(f"≤{r['max_attempts']} attempts")
    if key:
        out.append(_entry("Key", ", ".join(key)))
    if r.get("exclusive_group"):
        out.append(_entry("Group", f"{r['exclusive_group']}, priority {r.get('priority', 0)}"))
    if r.get("enabled") is False:
        out.append(_entry("Disabled", ""))
    return out


# --------------------------------------------------------------------------- workflows


def _wait_text(config: Mapping[str, Any]) -> str:
    text = f"wait {_num(config.get('seconds', '?'))} s"
    guard = config.get("guard")
    if isinstance(guard, Mapping) and guard.get("value"):
        value = str(guard["value"])
        detail = ", ".join(
            x for x in (value, f"as {guard['actor']}" if guard.get("actor") else "") if x
        )
        text += f"; {_WAIT_GUARDS.get(value, 'guard')} ({detail})"
    return text


def _code_text(step: Mapping[str, Any], config: Mapping[str, Any], where: str) -> str:
    builtin = config.get("builtin")
    if builtin == "action":
        return action_text(config.get("action") or {}, where)
    if isinstance(builtin, str) and builtin:
        actor = config.get("actor")
        as_actor = f" as {actor}" if isinstance(actor, str) and actor else ""
        words = BUILTIN_WORDS.get(builtin)
        if words is None:
            return _join(f"builtin {builtin}{as_actor}", where)
        if "{as}" in words:  # the gloss follows the actor and placement
            extra = [as_actor.strip(), "" if where == as_actor.strip() else where]
            return words.replace("{as}", "".join(f" {x}" for x in extra if x))
        return _join(words + as_actor, where)
    command = config.get("command")
    return _join(f"code `{command}`" if isinstance(command, str) and command else "code", where)


def _loop_text(step: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    bound = step.get("max_iterations")
    if step.get("kind") == "for_each":
        items = config.get("items") or "items"
        what = "item" if items == "items" else f"of {items}"
        return f"for each {what}" + (f" (≤{bound})" if bound else "")
    text = f"retry up to {bound}×" if bound else "retry"
    if config.get("until"):
        text += f", until {condition_text(config['until'])}"
    return text


def step_text(step: Any) -> str:
    """The words for one step (a loop's own line, without its body)."""
    s = _plain(step)
    kind = s.get("kind")
    config = s.get("config") if isinstance(s.get("config"), Mapping) else {}
    where = _placement(s.get("placement"))
    if kind == "wait":
        text = _wait_text(config)
    elif kind == "code":
        text = _code_text(s, config, where)
    elif kind == "ai":
        actor = _plain(s.get("placement")).get("actor")
        text = f"{actor} (agent)" if actor else _join("agent", where)
    elif kind == "actor_task":
        actor = _plain(s.get("placement")).get("actor") or config.get("actor")
        text = f"task for {actor}" if actor else _join("task", where)
    elif kind in ("for_each", "retry_until"):
        text = _loop_text(s, config)
    else:  # logic and anything unknown read as their kind
        text = _join(str(kind or "step"), where)
    extras = []
    retry = s.get("retry")
    if isinstance(retry, Mapping) and (retry.get("max_attempts") or 1) > 1:
        extras.append(f"≤{retry['max_attempts']} attempts")
    if s.get("enabled") is False:
        extras.append("disabled")
    return text + (f" ({', '.join(extras)})" if extras else "")


def _step_entries(steps: Any, prefix: str, depth: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(steps if isinstance(steps, list | tuple) else (), start=1):
        s = _plain(raw)
        label = f"{prefix}{i}"
        text = step_text(s)
        body = s.get("body") if isinstance(s.get("body"), list | tuple) else ()
        # a one-step body reads inline, but only a leaf: a nested loop keeps its own body
        if len(body) == 1 and not _plain(body[0]).get("body"):
            out.append(_entry(label, f"{text}: {step_text(body[0])}", depth, str(s.get("id"))))
            continue
        out.append(_entry(label, text + (":" if body else ""), depth, str(s.get("id", "?"))))
        out += _step_entries(body, f"{label}.", depth + 1)
    return out


def describe_workflow(workflow: Any) -> list[dict[str, Any]]:
    """Entries describing ``workflow`` (a :class:`Workflow` or its dict form): its steps in
    order, numbered, a loop's body nested; a disabled workflow ends with ``Disabled``."""
    wf = _plain(workflow)
    out = _step_entries(wf.get("steps"), "", 0)
    if wf.get("enabled") is False:
        out.append(_entry("Disabled", ""))
    return out


# --------------------------------------------------------------------------- text


def render(entries: list[dict[str, Any]]) -> list[str]:
    """Entries as text lines: ``3.1 agent — qwen-fixer (agent)`` / ``When github.pr...``."""
    lines = []
    for e in entries:
        indent = "  " * int(e.get("depth") or 0)
        head = f"{e['label']} {e['step']} —" if "step" in e else str(e["label"])
        lines.append(f"{indent}{_join(head, str(e.get('text') or ''))}")
    return lines
