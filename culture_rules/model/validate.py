"""Validation of model objects: returns structured errors, never raises.

``validate(obj)`` walks a model object and returns a list of
:class:`ValidationError` (``path``, ``code``, ``message``); an empty list means
valid. ``validate_data(cls, data)`` parses a dict or JSON text first and turns
parse failures into the same structured errors.

Two passes per object: a generic *structural* pass driven by the dataclass type
hints (required fields, scalar types, ``Literal`` membership such as actor and
step kinds), then a *semantic* pass per model type. Semantic checks guard every
comparison with ``isinstance`` so a structurally broken value yields an error,
not an exception.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from string import Formatter
from typing import Any, Literal, get_args, get_origin

from culture_rules.model import condition as condition_tree
from culture_rules.model import serde
from culture_rules.model.action import Action
from culture_rules.model.action_kinds import ACTION_KINDS, is_lenient, param_type_ok, resolve_kind
from culture_rules.model.action_step import (
    ACTION_SPEC_FIELDS,
    is_action_step,
    ref_problems,
    validation_params,
)
from culture_rules.model.actor import Actor
from culture_rules.model.app_actor import app_param_errors
from culture_rules.model.common import SCHEMA_VERSION, RetryPolicy
from culture_rules.model.graph import find_cycle
from culture_rules.model.machine import Machine, MachineInstall
from culture_rules.model.placement import PLACEMENT_FORMS, Placement
from culture_rules.model.refs import (
    LITERAL_KEY,
    REF_KEY,
    TRIGGER_FIELDS,
    is_input_reference,
    is_reference,
    ref_errors,
    run_error_refs,
    structured_form,
    var_name,
)
from culture_rules.model.rule import TRIGGER_KINDS, Rule, Trigger, WorkflowRef
from culture_rules.model.variable import VALID_VARIABLE_NAME_RE
from culture_rules.model.variable_refs import condition_variable_refs
from culture_rules.model.workflow import LOOP_KINDS, Edge, Output, Port, Step, Variable, Workflow

__all__ = [
    "CATALOG_CODES",
    "ValidationError",
    "validate",
    "validate_data",
    "variable_ref_errors",
]

#: Pseudo step id an edge uses to read from the workflow's own inputs.
INPUTS_NODE = "inputs"
_CHANNEL_ID = re.compile(r"^\d{1,25}$")
_RESERVED_STEP_IDS = frozenset({"inputs", "outputs", "vars", "steps", "trigger"})
_PROBE_MODES = ("change", "condition")

#: ``trigger`` alone or ``trigger.<f>...``; it is a reference when ``f`` is an envelope
#: field (``trigger.sh`` is a file name, not a reference - see culture_rules.model.refs).
_TRIGGER_EXACT = re.compile(r"^\s*trigger(?:\.([^\s.]+)(?:\.[^\s.]+)*)?\s*$")
_TRIGGER_TEMPLATE = re.compile(r"(?:\{\{|\$\{)\s*trigger\b")
_SUPPORTED_MAJOR = int(SCHEMA_VERSION.split(".")[0])


@dataclass(frozen=True)
class ValidationError:
    """One problem found in a model object."""

    path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}


Errors = list[ValidationError]
_join = serde.join


def _err(errors: Errors, path: str, code: str, message: str) -> None:
    errors.append(ValidationError(path, code, message))


# --- public API -----------------------------------------------------------


#: Save-time catalog checks (action kinds/params, trigger kinds/params). A document stored
#: before they existed must stay loadable, runnable and visible as a neighbour, so
#: ``validate(obj, stored=True)`` drops exactly these codes; every structural check stays.
CATALOG_CODES = frozenset(
    {
        "action_kind_unknown",
        "action_param_required",
        "action_param_type",
        "trigger_kind_unknown",
        "trigger_type_required",
        "trigger_cron_required",
        "trigger_param_required",
        "trigger_param_invalid",
    }
)


def validate(obj: Any, *, stored: bool = False) -> list[ValidationError]:
    """Validate a model object; returns ``[]`` when valid.

    ``stored=True`` is for content read back from the store (running it, or using it as a
    neighbour of a save): the save-time :data:`CATALOG_CODES` checks are skipped. Saves stay
    strict (the default).
    """
    errors: Errors = []
    if not (dataclasses.is_dataclass(obj) and type(obj) in _SEMANTIC):
        _err(errors, "", "type", f"not a culture_rules model: {type(obj).__name__}")
        return errors
    _validate(obj, "", errors)
    if stored:
        return [e for e in errors if e.code not in CATALOG_CODES]
    return errors


def variable_ref_errors(rule: Rule, defined: Collection[str]) -> list[ValidationError]:
    """``variable_undefined`` for every shared variable ``rule`` references that is not in
    ``defined`` (the names the store holds), each naming the variable.

    :func:`validate` is pure, so the save path (which can read the store) calls this with
    the defined names: a rule that references an undefined variable is refused at save.
    """
    errors: Errors = []
    missing = sorted(condition_variable_refs(rule.condition) - set(defined))
    if missing:
        names = ", ".join(repr(n) for n in missing)
        _err(
            errors,
            "condition",
            "variable_undefined",
            f"the condition references undefined shared variable(s): {names}",
        )
    inputs = rule.workflow.inputs if rule.workflow is not None else {}
    for input_name, mapping in sorted(inputs.items()):
        name = var_name(mapping)
        if name is not None and name not in defined:
            _err(
                errors,
                _join(_join("workflow", "inputs"), input_name),
                "variable_undefined",
                f"input {input_name!r} references undefined shared variable {name!r}",
            )
    return errors


def validate_data(cls: type, data: Any) -> tuple[Any | None, list[ValidationError]]:
    """Parse ``data`` (dict or JSON text) into ``cls`` and validate it.

    Returns ``(obj, errors)``; ``obj`` is ``None`` when the data could not be parsed.
    """
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            return None, [ValidationError("", "json", f"invalid JSON: {exc.msg}")]
    try:
        obj = serde.from_dict(cls, data)
    except serde.ModelParseError as exc:
        return None, [ValidationError(exc.path, exc.code, exc.message)]
    return obj, validate(obj)


# --- generic structural pass ----------------------------------------------


def _validate(obj: Any, path: str, errors: Errors) -> None:
    hints = serde.field_types(type(obj))
    for f in dataclasses.fields(obj):
        _check_value(hints[f.name], getattr(obj, f.name), _join(path, f.name), f.name, errors)
    _SEMANTIC[type(obj)](obj, path, errors)


def _check_value(tp: Any, value: Any, path: str, name: str, errors: Errors) -> None:
    if value is None:
        if not serde.is_optional(tp):
            _err(errors, path, "required", f"{name} is required")
        return
    if tp is Any or serde.union_members(tp):
        return  # a multi-member union is checked by the model's semantic validator
    tp = serde.strip_optional(tp)
    origin = get_origin(tp)
    if origin is Literal:
        _check_literal(tp, value, path, name, errors)
        return
    if origin is tuple:
        _check_array(tp, value, path, name, errors)
        return
    if origin is dict:
        _check_object(tp, value, path, name, errors)
        return
    if dataclasses.is_dataclass(tp):
        _check_nested(tp, value, path, errors)
        return
    _check_scalar(tp, value, path, errors)


def _check_literal(tp: Any, value: Any, path: str, name: str, errors: Errors) -> None:
    allowed = get_args(tp)
    if value not in allowed:
        code = "invalid_kind" if name == "kind" else "invalid_value"
        _err(errors, path, code, f"{value!r} is not one of {', '.join(allowed)}")


def _check_array(tp: Any, value: Any, path: str, name: str, errors: Errors) -> None:
    if not isinstance(value, (tuple, list)):
        _err(errors, path, "type", "expected an array")
        return
    item = get_args(tp)[0]
    for i, v in enumerate(value):
        _check_value(item, v, _join(path, i), name, errors)


def _check_object(tp: Any, value: Any, path: str, name: str, errors: Errors) -> None:
    if not isinstance(value, dict):
        _err(errors, path, "type", "expected an object")
        return
    item = get_args(tp)[1]
    for k, v in value.items():
        _check_value(item, v, _join(path, str(k)), name, errors)


def _check_nested(tp: Any, value: Any, path: str, errors: Errors) -> None:
    if type(value) is not tp:
        _err(errors, path, "type", f"expected {tp.__name__}")
        return
    _validate(value, path, errors)


def _check_scalar(tp: type, value: Any, path: str, errors: Errors) -> None:
    if tp is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif tp is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, tp)
    if not ok:
        _err(errors, path, "type", f"expected {tp.__name__}, got {type(value).__name__}")


# --- helpers ----------------------------------------------------------------


def _nonempty(obj: Any, names: tuple[str, ...], path: str, errors: Errors) -> None:
    for name in names:
        value = getattr(obj, name)
        if isinstance(value, str) and not value.strip():
            _err(errors, _join(path, name), "empty", f"{name} must not be empty")


def _positive_number(value: Any, path: str, errors: Errors) -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or value <= 0:
            _err(errors, path, "range", "must be a finite number > 0")


def _unique(values: list[tuple[str, Any]], what: str, errors: Errors) -> None:
    seen: set[Any] = set()
    for path, value in values:
        if not isinstance(value, str):
            continue
        if value in seen:
            _err(errors, path, "duplicate", f"duplicate {what} {value!r}")
        seen.add(value)


def _schema_version(value: Any, path: str, errors: Errors) -> None:
    if not isinstance(value, str):
        return
    m = re.fullmatch(r"(\d+)\.(\d+)", value)
    if m is None:
        _err(errors, path, "schema_version", f"{value!r} is not MAJOR.MINOR")
    elif int(m.group(1)) > _SUPPORTED_MAJOR:
        _err(
            errors,
            path,
            "schema_version",
            f"schema_version {value} is newer than supported major {_SUPPORTED_MAJOR}",
        )


def _is_trigger_ref(value: str) -> bool:
    if _TRIGGER_TEMPLATE.search(value):
        return True
    m = _TRIGGER_EXACT.match(value)
    return bool(m) and (m.group(1) is None or m.group(1) in TRIGGER_FIELDS)


def _is_trigger_target(target: Any) -> bool:
    """Whether a ``{"$ref": target}`` points into the trigger scope (any field)."""
    return isinstance(target, str) and target.strip().split(".")[0] == "trigger"


def _trigger_refs(value: Any, path: str) -> Iterator[str]:
    """Paths inside an arbitrary JSON value that reference the trigger scope.

    ``{"$literal": ...}`` values are never references; ``{"$ref": "trigger..."}`` always is.
    """
    form = structured_form(value)
    if form == LITERAL_KEY:
        return
    if form == REF_KEY:
        if _is_trigger_target(value[REF_KEY]):
            yield _join(path, REF_KEY)
        return
    if isinstance(value, str):
        if _is_trigger_ref(value):
            yield path
    else:
        yield from _nested_trigger_refs(value, path)


def _nested_trigger_refs(value: Any, path: str) -> Iterator[str]:
    """:func:`_trigger_refs` for the keys and items of a container (nothing for a scalar)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and _is_trigger_ref(k):
                yield _join(path, k)
            yield from _trigger_refs(v, _join(path, str(k)))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _trigger_refs(v, _join(path, i))


def _report_trigger_refs(value: Any, path: str, errors: Errors) -> None:
    for p in _trigger_refs(value, path):
        _err(errors, p, "trigger_reference", "workflows must not reference the trigger")


# --- semantic pass per model ---------------------------------------------


def _below(value: float, floor: float) -> bool:
    """``value < floor``, counting NaN as below (it is never ``>= floor``)."""
    return value < floor or (isinstance(value, float) and math.isnan(value))


def _check_retry(obj: RetryPolicy, path: str, errors: Errors) -> None:
    if isinstance(obj.max_attempts, int) and obj.max_attempts < 1:
        _err(errors, _join(path, "max_attempts"), "range", "max_attempts must be >= 1")
    if isinstance(obj.backoff_s, (int, float)) and _below(obj.backoff_s, 0):
        _err(errors, _join(path, "backoff_s"), "range", "backoff_s must be >= 0")
    if isinstance(obj.backoff_multiplier, (int, float)) and _below(obj.backoff_multiplier, 1):
        _err(errors, _join(path, "backoff_multiplier"), "range", "backoff_multiplier must be >= 1")


def _check_placement(obj: Placement, path: str, errors: Errors) -> None:
    given = [n for n in PLACEMENT_FORMS if getattr(obj, n) is not None]
    # a set-but-empty field still counts as given: the schema rejects it alongside a real form
    if obj.form is None or len(given) > 1:
        _err(
            errors,
            path,
            "placement_form",
            "placement must set exactly one non-empty form of machine, actor, requirement"
            f" (got: {', '.join(given) or 'none'})",
        )
    if isinstance(obj.requirement, (tuple, list)):
        for i, cap in enumerate(obj.requirement):
            if isinstance(cap, str) and not cap.strip():
                _err(errors, _join(_join(path, "requirement"), i), "empty", "empty capability")


def _check_action(obj: Action, path: str, errors: Errors) -> None:
    _nonempty(obj, ("kind",), path, errors)
    _positive_number(obj.timeout_s, _join(path, "timeout_s"), errors)
    if isinstance(obj.kind, str) and obj.kind.strip():
        _check_action_kind(obj, path, errors)


def _check_action_kind(obj: Action, path: str, errors: Errors) -> None:
    """The kind must be catalogued and its params typed (see model/action_kinds.py)."""
    spec = resolve_kind(obj.kind)
    if spec is None:
        _err(
            errors,
            _join(path, "kind"),
            "action_kind_unknown",
            f"unknown action kind {obj.kind!r}; expected one of {', '.join(ACTION_KINDS)}",
        )
        return
    if not isinstance(obj.params, dict):
        return
    pp = _join(path, "params")
    lenient = is_lenient(obj.kind)
    for name, p in spec.params.items():
        value = obj.params.get(name)
        if _unset(value):
            if p.required and not lenient:
                _err(
                    errors,
                    _join(pp, name),
                    "action_param_required",
                    f"{obj.kind} requires params.{name}",
                )
        elif not param_type_ok(p, value):
            _err(
                errors,
                _join(pp, name),
                "action_param_type",
                f"params.{name} must be {p.type} (or a reference/template)",
            )
    if not lenient:
        _check_any_of(obj, spec.any_of, pp, errors)
    _check_comment_status(obj, pp, errors)


def _check_any_of(
    obj: Action, groups: tuple[tuple[str, ...], ...], pp: str, errors: Errors
) -> None:
    """Each ``any_of`` group needs at least one of its params set (e.g. content or
    content_path for llm.complete)."""
    for group in groups:
        if all(_unset(obj.params.get(name)) for name in group):
            _err(
                errors,
                _join(pp, group[0]),
                "action_param_required",
                f"{obj.kind} requires params.{' or params.'.join(group)}",
            )


def _unset(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _check_comment_status(obj: Action, pp: str, errors: Errors) -> None:
    """d26: ``github.comment`` writes either its chain's status comment (``status: true``)
    or a once-only comment (``once_key``), never both."""
    if obj.kind != "github.comment" or obj.params.get("status") is not True:
        return
    if obj.params.get("once_key") not in (None, ""):
        _err(
            errors,
            _join(pp, "status"),
            "action_param_conflict",
            "params.status and params.once_key do not combine",
        )


def _check_trigger(obj: Trigger, path: str, errors: Errors) -> None:
    _nonempty(obj, ("kind",), path, errors)
    if not obj.kind.strip():
        return
    if obj.kind not in TRIGGER_KINDS:
        _err(
            errors,
            _join(path, "kind"),
            "trigger_kind_unknown",
            f"unknown trigger kind {obj.kind!r}; expected one of {', '.join(TRIGGER_KINDS)}",
        )
        return
    params = obj.params if isinstance(obj.params, dict) else {}
    pp = _join(path, "params")
    if obj.kind == "event" and not _present(params, "type"):
        _err(
            errors,
            _join(pp, "type"),
            "trigger_type_required",
            "an event trigger requires a non-empty params.type",
        )
    elif obj.kind == "schedule" and not _present(params, "cron"):
        _err(errors, _join(pp, "cron"), "trigger_cron_required", "schedule requires params.cron")
    elif obj.kind == "probe":
        _check_probe_params(params, pp, errors)
    if obj.kind == "event" and str(params.get("type", "")).startswith("discord."):
        _check_discord_filters(params, pp, errors)


def _filter_item(value: Any) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _check_discord_filters(params: dict, pp: str, errors: Errors) -> None:
    """A ``discord.*`` trigger's filters (engine.matching): ``include_bots`` a bool;
    ``channels``, ``users`` and ``roles`` an id/name or a list of them."""
    if "include_bots" in params and not isinstance(params["include_bots"], bool):
        _err(
            errors,
            _join(pp, "include_bots"),
            "trigger_param_invalid",
            "params.include_bots must be true or false",
        )
    for name in ("channels", "users", "roles"):
        value = params.get(name)
        if not _filter_value_ok(value):
            _err(
                errors,
                _join(pp, name),
                "trigger_param_invalid",
                f"params.{name} must be an id or name, or a list of them",
            )
            continue
        if name == "channels" and value is not None and not _channel_ids_ok(value):
            _err(
                errors,
                _join(pp, name),
                "trigger_param_invalid",
                'params.channels holds numeric Discord channel ids, or "all"',
            )


def _filter_value_ok(value: Any) -> bool:
    """Absent, one id or name, or a list of them."""
    if value is None or _filter_item(value):
        return True
    return isinstance(value, (list, tuple)) and all(_filter_item(v) for v in value)


def _channel_ids_ok(value: Any) -> bool:
    """Every ``channels`` entry is a numeric Discord id or ``all`` (a name never matches)."""
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        text = str(item).strip()
        if text and text.casefold() != "all" and not _CHANNEL_ID.match(text):
            return False
    return True


def _present(params: dict, name: str) -> bool:
    value = params.get(name)
    return isinstance(value, str) and bool(value.strip())


def _check_probe_params(params: dict, pp: str, errors: Errors) -> None:
    for name in ("actor", "command", "schedule", "mode"):
        if not _present(params, name):
            _err(
                errors,
                _join(pp, name),
                "trigger_param_required",
                f"probe requires params.{name}",
            )
    if _present(params, "mode") and params["mode"] not in _PROBE_MODES:
        _err(
            errors,
            _join(pp, "mode"),
            "trigger_param_invalid",
            "probe params.mode must be one of: change, condition",
        )


def _check_workflow_ref(obj: WorkflowRef, path: str, errors: Errors) -> None:
    _nonempty(obj, ("id",), path, errors)
    if isinstance(obj.version, int) and obj.version < 1:
        _err(errors, _join(path, "version"), "range", "version must be >= 1")
    for name, mapping in obj.inputs.items():
        if not _input_mapping_ok(mapping):
            _err(
                errors,
                _join(_join(path, "inputs"), name),
                "invalid_input_mapping",
                'a workflow input is a string, {"$ref": <string>}, {"$literal": <value>} '
                'or {"$var": <variable name>}',
            )


def _input_mapping_ok(mapping: Any) -> bool:
    if isinstance(mapping, str):
        return True
    name = var_name(mapping)
    if name is not None:
        return VALID_VARIABLE_NAME_RE.fullmatch(name) is not None
    form = structured_form(mapping)
    return form == LITERAL_KEY or (form == REF_KEY and isinstance(mapping[REF_KEY], str))


_KEY_PLACEHOLDER_RE = re.compile(r"trigger(?:\.[A-Za-z0-9_-]+)+")


def _concurrency_key_problem(template: str) -> str | None:
    """Why ``template`` is not a valid concurrency key, or ``None``: only
    ``{trigger.<path>}`` placeholders, balanced braces (``{{`` / ``}}`` escape one), no
    format spec or conversion - the same template the engine resolves
    (:func:`culture_rules.engine.claims.resolve_concurrency_key`)."""
    if not template.strip():
        return "concurrency_key must not be empty"
    unescaped = template.replace("{{", "").replace("}}", "")
    if re.search(r"\{[^{}]*[:!]", unescaped):
        return "format specs and conversions are not allowed"
    try:
        fields = list(Formatter().parse(template))
    except ValueError as exc:
        return f"unbalanced braces: {exc}"
    for _literal, name, _spec, _conversion in fields:
        if name is not None and not _KEY_PLACEHOLDER_RE.fullmatch(name):
            return f"only {{trigger.<path>}} placeholders are allowed, not {{{name}}}"
    return None


def _check_rule(obj: Rule, path: str, errors: Errors) -> None:
    _nonempty(obj, ("id", "name"), path, errors)
    _schema_version(obj.schema_version, _join(path, "schema_version"), errors)
    if isinstance(obj.max_attempts, int) and obj.max_attempts < 1:
        _err(errors, _join(path, "max_attempts"), "range", "max_attempts must be >= 1")
    _check_budget_fields(obj, path, errors)
    if isinstance(obj.concurrency_key, str):
        problem = _concurrency_key_problem(obj.concurrency_key)
        if problem is not None:
            _err(errors, _join(path, "concurrency_key"), "invalid_template", problem)
    if obj.exclusive_group is not None:
        _nonempty(obj, ("exclusive_group",), path, errors)
    if isinstance(obj.condition, dict):
        _check_rule_condition(obj.condition, path, errors)
    for field in ("action", "on_failure"):
        _check_action_refs(obj, field, path, errors)
    for rel in ("must_after", "may_after", "supersedes"):
        _check_relation(obj, rel, path, errors)


def _check_budget_fields(obj: Rule, path: str, errors: Errors) -> None:
    if obj.counts_toward_budget is False:
        if obj.concurrency_key is None:
            _err(
                errors,
                _join(path, "counts_toward_budget"),
                "requires_concurrency_key",
                "counts_toward_budget applies only to a rule with a concurrency_key",
            )
        if obj.max_attempts is not None:
            _err(
                errors,
                _join(path, "counts_toward_budget"),
                "conflict",
                "a rule outside the attempt budget cannot set max_attempts",
            )
    if obj.resets_attempt_budget is True and obj.concurrency_key is None:
        _err(
            errors,
            _join(path, "resets_attempt_budget"),
            "requires_concurrency_key",
            "resets_attempt_budget applies only to a rule with a concurrency_key",
        )


def _check_rule_condition(condition: dict, path: str, errors: Errors) -> None:
    try:
        condition_tree.validate(condition)
    except condition_tree.ConditionError as exc:
        _err(errors, _join(path, "condition"), "condition_invalid", str(exc))


def _check_action_refs(obj: Rule, field: str, path: str, errors: Errors) -> None:
    """The references in a rule's ``action`` or ``on_failure`` params (``run.error`` only
    for ``on_failure``)."""
    act = getattr(obj, field)
    if not isinstance(act, Action) or not isinstance(act.params, dict):
        return
    params_path = _join(_join(path, field), "params")
    has_workflow = obj.workflow is not None
    for p, reason in ref_errors(act.params, params_path, _join, has_workflow=has_workflow):
        _err(errors, p, "invalid_reference", reason)
    if field == "action":
        for p in run_error_refs(act.params, params_path, _join):
            _err(errors, p, "invalid_reference", "run.error is set only for on_failure")


def _check_relation(obj: Rule, rel: str, path: str, errors: Errors) -> None:
    """One relationship list of a rule: unique, non-empty ids that are not the rule itself."""
    ids = getattr(obj, rel)
    if not isinstance(ids, (tuple, list)):
        return
    rel_path = _join(path, rel)
    _unique([(_join(rel_path, i), v) for i, v in enumerate(ids)], "rule id", errors)
    for i, rid in enumerate(ids):
        if isinstance(rid, str) and not rid.strip():
            _err(errors, _join(rel_path, i), "empty", "empty rule id")
        elif rid == obj.id:
            _err(errors, _join(rel_path, i), "self_reference", f"rule cannot {rel} itself")


def _check_ports(ports: Any, path: str, errors: Errors) -> None:
    named = [(_join(_join(path, i), "name"), p.name) for i, p in _items(ports, Port)]
    _unique(named, "port", errors)


def _check_port(obj: Port, path: str, errors: Errors) -> None:
    _nonempty(obj, ("name",), path, errors)


def _check_variable(obj: Variable, path: str, errors: Errors) -> None:
    _nonempty(obj, ("name",), path, errors)


def _check_output(obj: Output, path: str, errors: Errors) -> None:
    _nonempty(obj, ("name",), path, errors)


def _check_edge(obj: Edge, path: str, errors: Errors) -> None:
    _nonempty(obj, ("source", "source_port", "target", "target_port"), path, errors)


def _check_wait_config(config: dict, path: str, errors: Errors) -> None:
    """Validate wait-step config: seconds (required, > 0) and head_unchanged guard."""
    if not isinstance(config, dict):
        _err(errors, path, "type", "wait steps require a config object with seconds")
        return
    if "seconds" not in config:
        _err(
            errors,
            _join(path, "seconds"),
            "required",
            "wait steps require config.seconds (a positive number)",
        )
        return
    seconds = config["seconds"]
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        _err(errors, _join(path, "seconds"), "type", "wait config.seconds must be a number")
        return
    if not math.isfinite(seconds) or seconds <= 0:
        _err(
            errors,
            _join(path, "seconds"),
            "range",
            "wait config.seconds must be a finite number > 0",
        )
        return
    guard = config.get("guard")
    if guard is None:
        return
    if not isinstance(guard, dict):
        _err(errors, _join(path, "guard"), "type", "wait config.guard must be an object")
        return
    guard_value = guard.get("value")
    if guard_value != "head_unchanged":
        _err(
            errors,
            _join(path, "guard.value"),
            "invalid_guard",
            f"wait guard value must be 'head_unchanged' (got {guard_value!r})",
        )
        return
    ref = guard.get("ref")
    if not ref or not isinstance(ref, str):
        _err(
            errors,
            _join(path, "guard.ref"),
            "missing_ref",
            "head_unchanged guard requires a config.guard.ref pointing to an input or variable",
        )
        return
    if not ref.startswith(("inputs.", "vars.")):
        _err(
            errors,
            _join(path, "guard.ref"),
            "invalid_ref",
            "head_unchanged guard ref must point to an input (inputs.<name>) "
            "or variable (vars.<name>)",
        )


def _check_step(obj: Step, path: str, errors: Errors) -> None:
    _nonempty(obj, ("id",), path, errors)
    if isinstance(obj.id, str) and obj.id in _RESERVED_STEP_IDS:
        _err(errors, _join(path, "id"), "reserved", f"step id {obj.id!r} is reserved")
    _positive_number(obj.timeout_s, _join(path, "timeout_s"), errors)
    _check_ports(obj.inputs, _join(path, "inputs"), errors)
    _check_ports(obj.outputs, _join(path, "outputs"), errors)
    max_path = _join(path, "max_iterations")
    if obj.kind in LOOP_KINDS:
        if obj.max_iterations is None:
            _err(errors, max_path, "loop_max_required", f"{obj.kind} loop needs max_iterations")
        elif isinstance(obj.max_iterations, int) and obj.max_iterations < 1:
            _err(errors, max_path, "range", "max_iterations must be >= 1")
    else:
        if obj.max_iterations is not None:
            _err(errors, max_path, "not_allowed", "only loop steps take max_iterations")
        if obj.body:
            _err(errors, _join(path, "body"), "not_allowed", "only loop steps have a body")
        if obj.kind == "wait":
            _check_wait_config(obj.config, _join(path, "config"), errors)
        elif is_action_step(obj.kind, obj.config):
            _check_action_step(obj, _join(path, "config.action"), errors)
    _check_when_explain(obj, _join(path, "config"), errors)


def _check_on_error(obj: Step, value: Any, where: str, errors: Errors) -> None:
    """``config.on_error`` (#53 t8): ``"skip"`` tolerates the step's own failure, on a step
    the executor dispatches (not a loop or wait step)."""
    if obj.kind in LOOP_KINDS or obj.kind == "wait":
        _err(errors, where, "not_allowed", f"a {obj.kind} step takes no on_error")
    elif value != "skip":
        _err(errors, where, "on_error_invalid", 'on_error must be "skip"')


def _check_when_explain(obj: Step, path: str, errors: Errors) -> None:
    """``config.when`` (d20): a condition tree over the step's inputs, on a step the executor
    dispatches (not a loop or wait step). ``config.explain``: a result field name, on a
    ``retry_until`` loop only."""
    config = obj.config if isinstance(obj.config, dict) else {}
    if "when" in config:
        where = _join(path, "when")
        if obj.kind in LOOP_KINDS or obj.kind == "wait":
            _err(errors, where, "not_allowed", f"a {obj.kind} step takes no when")
        else:
            try:
                condition_tree.validate(config["when"])
            except condition_tree.ConditionError as exc:
                _err(errors, where, "when_invalid", f"when must be a condition tree: {exc}")
    if "on_error" in config:
        _check_on_error(obj, config["on_error"], _join(path, "on_error"), errors)
    if "explain" in config:
        where = _join(path, "explain")
        if obj.kind != "retry_until":
            _err(errors, where, "not_allowed", "only a retry_until loop takes explain")
        elif not isinstance(config["explain"], str) or not config["explain"].strip():
            _err(errors, where, "explain_invalid", "explain must name a result field")


def _check_action_step(obj: Step, path: str, errors: Errors) -> None:
    """A built-in action step (d12): a catalogued kind whose params pass the rule-action
    kind-param checks; its references must be ``inputs.<declared input port>``."""
    spec = obj.config.get("action")
    kind = spec.get("kind") if isinstance(spec, dict) else None
    params = spec.get("params", {}) if isinstance(spec, dict) else None
    if not isinstance(kind, str) or not kind.strip() or not isinstance(params, dict):
        _err(errors, path, "action_step_invalid", "needs config.action {kind, params: object}")
        return
    _check_action_spec_fields(spec, path, errors)
    _check_action_kind(Action(kind=kind, params=validation_params(params)), path, errors)
    ports = {p.name for _i, p in _items(obj.inputs, Port)}
    for where, reason in ref_problems(params, ports):
        _err(errors, _join(_join(path, "params"), where), "action_step_ref", reason)
    if _is_actor_ref(params.get("actor")):
        _err(
            errors,
            _join(_join(path, "params"), "actor"),
            "action_actor_not_literal",
            "an action step names its actor by id; a reference here is never resolved",
        )


def _is_actor_ref(value: Any) -> bool:
    """Whether ``params.actor`` is written as anything but a plain id: the node dispatches
    through the value as given, so ``inputs.actor`` would name an actor called
    ``inputs.actor``, and a ``{"$ref"|"$literal": ...}`` object names no actor at all."""
    if structured_form(value) is not None:
        return True
    if not isinstance(value, str):
        return False
    return "{{" in value or is_input_reference(value) or is_reference(value)


def _check_action_spec_fields(spec: dict, path: str, errors: Errors) -> None:
    """``config.action``'s own fields, typed as on a rule :class:`Action` (an untyped dict
    gets no serde pass): a non-boolean ``idempotent`` (``"false"``) must never pass for
    true. ``retry`` / ``timeout_s`` are the step's own, and any other key is unknown."""
    for key, value in spec.items():
        where = _join(path, str(key))
        if key in ACTION_SPEC_FIELDS:
            _check_scalar(ACTION_SPEC_FIELDS[key], value, where, errors)
        elif key in ("retry", "timeout_s"):
            _err(errors, where, "not_allowed", f"an action step's {key} is the step's own")
        elif key not in ("kind", "params"):
            _err(errors, where, "unknown_field", "unknown field")


def _check_actor(obj: Actor, path: str, errors: Errors) -> None:
    _nonempty(obj, ("id", "name"), path, errors)
    _schema_version(obj.schema_version, _join(path, "schema_version"), errors)
    if obj.config_source == "repo" and not obj.repo:
        _err(errors, _join(path, "repo"), "required", "config_source 'repo' needs repo")
    if obj.kind == "app":
        for sub, code, message in app_param_errors(obj.params):
            _err(errors, _join(_join(path, "params"), sub), code, message)


def _check_machine(obj: Machine, path: str, errors: Errors) -> None:
    _nonempty(obj, ("name",), path, errors)
    _schema_version(obj.schema_version, _join(path, "schema_version"), errors)


#: A grant secret name, as install.sh's --secret takes it (never a value).
_GRANT_NAME_RE = re.compile(r"[A-Z0-9_]+")
#: The run-as account in a ``sudo -n -u <user>`` prefix (a POSIX-ish user name).
_RUN_AS_USER_RE = re.compile(r"[a-z_][a-z0-9_.-]*")
#: A control character (U+0000-U+001F, U+007F): never in a value install.sh receives.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
#: Install fields install.sh takes as one word: no control character and no whitespace.
_INSTALL_WORD_FIELDS = ("node_name", "wheelhouse", "python", "events_host", "ssh_target")


#: An ssh/scp target both read alike (#57 Codex r1): ``[user@]host`` of letters, digits, ``.``,
#: ``_`` and ``-``, never starting with ``-``; no ``:``, ``/`` or URL (scp reads ``host:path``).
SSH_TARGET_RE = re.compile(r"(?:[A-Za-z0-9._][A-Za-z0-9._-]*@)?[A-Za-z0-9._][A-Za-z0-9._-]*")


def is_ssh_target(value: str) -> bool:
    """Whether ssh and scp both read ``value`` as the same ``[user@]host``."""
    return SSH_TARGET_RE.fullmatch(value) is not None


def has_control_char(value: str) -> bool:
    """Whether ``value`` holds a control character (U+0000-U+001F or U+007F)."""
    return _CONTROL_RE.search(value) is not None


def is_install_word(value: str) -> bool:
    """Whether ``value`` may reach install.sh as one word: no control character, no space."""
    return not has_control_char(value) and not any(c.isspace() for c in value)


def _sudo_prefix_error(value: str) -> str | None:
    """Why ``value`` is not a ``sudo -n -u <user> [...]`` run-as prefix, or ``None``.

    Mirrors install.sh's --gate-run-as checks: one line, no quotes or backslashes (the prefix
    is split on whitespace, never by a shell), and it must open with ``sudo -n -u <user>``.
    """
    if has_control_char(value) or any(c in value for c in "'\"\\"):
        return "must be one line without control characters, quotes or backslashes"
    words = value.split()
    if len(words) < 4 or words[0].rsplit("/", 1)[-1] != "sudo" or words[1:3] != ["-n", "-u"]:
        return "must start with 'sudo -n -u <user>'"
    if not _RUN_AS_USER_RE.fullmatch(words[3]):
        return f"{words[3]!r} is not a user name"
    return None


def _check_install_words(obj: MachineInstall, path: str, errors: Errors) -> None:
    """Every one-word install field: no control character (an env-file line break) or space."""
    for field in _INSTALL_WORD_FIELDS:
        value = getattr(obj, field)
        if isinstance(value, str) and value and not is_install_word(value):
            _err(
                errors,
                _join(path, field),
                "invalid_value",
                f"{field} must be one word without control characters or whitespace",
            )


def _check_install_ssh_target(obj: MachineInstall, path: str, errors: Errors) -> None:
    """An ssh target is ``[user@]host`` that ssh and scp read alike (#57): never an option
    (``-...``), a URL, a port or a ``host:path``."""
    if isinstance(obj.ssh_target, str) and obj.ssh_target and not is_ssh_target(obj.ssh_target):
        _err(errors, _join(path, "ssh_target"), "invalid_value", "ssh_target must be [user@]host")


def _check_install_secrets(obj: MachineInstall, path: str, errors: Errors) -> None:
    """Every secret is a grant secret name, never a value."""
    if not isinstance(obj.secrets, (tuple, list)):
        return
    for i, name in enumerate(obj.secrets):
        if isinstance(name, str) and not _GRANT_NAME_RE.fullmatch(name):
            _err(
                errors,
                _join(_join(path, "secrets"), i),
                "invalid_secret_name",
                "a secret is a grant secret name ([A-Z0-9_]+), never a value",
            )


def _check_install_run_as(obj: MachineInstall, path: str, errors: Errors) -> None:
    if not isinstance(obj.gate_run_as, str):
        return
    why = _sudo_prefix_error(obj.gate_run_as)
    if why:
        _err(errors, _join(path, "gate_run_as"), "invalid_run_as", f"gate_run_as {why}")


def _check_install_extras(obj: MachineInstall, path: str, errors: Errors) -> None:
    if not isinstance(obj.extras, (tuple, list)):
        return
    for i, extra in enumerate(obj.extras):
        if isinstance(extra, str) and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", extra):
            _err(errors, _join(_join(path, "extras"), i), "invalid_value", "not an extra")


def _check_install_port(obj: MachineInstall, path: str, errors: Errors) -> None:
    port = obj.events_port
    if isinstance(port, int) and not isinstance(port, bool) and not 1 <= port <= 65535:
        _err(errors, _join(path, "events_port"), "range", "events_port must be 1-65535")


def _check_machine_install(obj: MachineInstall, path: str, errors: Errors) -> None:
    _nonempty(obj, _INSTALL_WORD_FIELDS, path, errors)
    _check_install_words(obj, path, errors)
    _check_install_ssh_target(obj, path, errors)
    _check_install_secrets(obj, path, errors)
    _check_install_run_as(obj, path, errors)
    _check_install_extras(obj, path, errors)
    _check_install_port(obj, path, errors)


def _items(seq: Any, cls: type) -> list[tuple[int, Any]]:
    """(index, item) pairs of ``seq`` whose items are instances of ``cls``."""
    if not isinstance(seq, (tuple, list)):
        return []
    return [(i, x) for i, x in enumerate(seq) if isinstance(x, cls)]


def _iter_steps(steps: Any, path: str) -> Iterator[tuple[str, Step]]:
    """Every step, including loop bodies, with its path."""
    if not isinstance(steps, (tuple, list)):
        return
    for i, step in enumerate(steps):
        if not isinstance(step, Step):
            continue
        step_path = _join(path, i)
        yield step_path, step
        yield from _iter_steps(step.body, _join(step_path, "body"))


def _port_types(ports: Any) -> dict[str, Any]:
    if not isinstance(ports, (tuple, list)):
        return {}
    return {p.name: p.type for p in ports if isinstance(p, (Port, Output))}


def _compatible(src: Any, dst: Any) -> bool:
    return src == dst or "any" in (src, dst) or (src, dst) == ("integer", "number")


def _check_workflow(obj: Workflow, path: str, errors: Errors) -> None:
    _nonempty(obj, ("id", "name"), path, errors)
    _schema_version(obj.schema_version, _join(path, "schema_version"), errors)
    if isinstance(obj.version, int) and obj.version < 1:
        _err(errors, _join(path, "version"), "range", "version must be >= 1")
    _check_ports(obj.inputs, _join(path, "inputs"), errors)
    for what in ("variables", "outputs"):
        items = getattr(obj, what)
        cls = Variable if what == "variables" else Output
        named = [(_join(_join(path, what), i), x.name) for i, x in _items(items, cls)]
        _unique(named, what[:-1], errors)

    steps = list(_iter_steps(obj.steps, _join(path, "steps")))
    _unique([(_join(p, "id"), s.id) for p, s in steps], "step id", errors)
    by_id = {s.id: s for _, s in steps if isinstance(s.id, str)}

    # A workflow is trigger-agnostic: no reference to the trigger scope anywhere.
    for step_path, step in steps:
        _report_trigger_refs(step.config, _join(step_path, "config"), errors)
    for i, var in _items(obj.variables, Variable):
        _report_trigger_refs(
            var.default, _join(_join(_join(path, "variables"), i), "default"), errors
        )

    _check_edges(obj, by_id, path, errors)
    _check_outputs(obj, by_id, path, errors)


def _check_edges(obj: Workflow, by_id: dict[str, Step], path: str, errors: Errors) -> None:
    inputs = _port_types(obj.inputs)
    graph: dict[str, set[str]] = {}
    for i, edge in _items(obj.edges, Edge):
        e_path = _join(_join(path, "edges"), i)
        if not all(isinstance(getattr(edge, f.name), str) for f in dataclasses.fields(Edge)):
            continue  # structural pass already reported it
        ports = _edge_ports(edge, e_path, inputs, by_id, errors)
        if ports is None:
            continue
        _check_wiring(edge, e_path, *ports, errors)
        if edge.source != INPUTS_NODE:
            graph.setdefault(edge.source, set()).add(edge.target)
    cycle = find_cycle(graph)
    if cycle:
        _err(errors, _join(path, "edges"), "cycle", "edges form a cycle: " + " -> ".join(cycle))


def _edge_ports(
    edge: Edge, e_path: str, inputs: dict[str, Any], by_id: dict[str, Step], errors: Errors
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(source ports, target ports) of an edge whose ends resolve; else reported, None."""
    if _is_trigger_ref(edge.source):
        _err(errors, _join(e_path, "source"), "trigger_reference", "edge reads the trigger")
        return None
    if edge.source == INPUTS_NODE:
        src_ports = inputs
    elif edge.source in by_id:
        src_ports = _port_types(by_id[edge.source].outputs)
    else:
        _err(errors, _join(e_path, "source"), "unknown_step", f"no step {edge.source!r}")
        return None
    if edge.target not in by_id:
        _err(errors, _join(e_path, "target"), "unknown_step", f"no step {edge.target!r}")
        return None
    return src_ports, _port_types(by_id[edge.target].inputs)


def _check_wiring(
    edge: Edge,
    e_path: str,
    src_ports: dict[str, Any],
    dst_ports: dict[str, Any],
    errors: Errors,
) -> None:
    ok = True
    if edge.source_port not in src_ports:
        _err(errors, _join(e_path, "source_port"), "unknown_port", f"no {edge.source_port!r}")
        ok = False
    if edge.target_port not in dst_ports:
        _err(errors, _join(e_path, "target_port"), "unknown_port", f"no {edge.target_port!r}")
        ok = False
    if ok and not _compatible(src_ports[edge.source_port], dst_ports[edge.target_port]):
        _err(
            errors,
            e_path,
            "port_type_mismatch",
            f"{src_ports[edge.source_port]} output wired to "
            f"{dst_ports[edge.target_port]} input",
        )


def _check_outputs(obj: Workflow, by_id: dict[str, Step], path: str, errors: Errors) -> None:
    inputs = _port_types(obj.inputs)
    variables = {v.name for _, v in _items(obj.variables, Variable)}
    for i, out in _items(obj.outputs, Output):
        src = out.source
        if not isinstance(src, str):
            continue
        s_path = _join(_join(_join(path, "outputs"), i), "source")
        if _is_trigger_ref(src):
            _err(errors, s_path, "trigger_reference", "workflows must not reference the trigger")
            continue
        parts = src.split(".")
        ok = (
            (len(parts) == 2 and parts[0] == "inputs" and parts[1] in inputs)
            or (len(parts) == 2 and parts[0] == "vars" and parts[1] in variables)
            or (
                len(parts) == 4
                and parts[0] == "steps"
                and parts[2] == "outputs"
                and parts[1] in by_id
                and parts[3] in _port_types(by_id[parts[1]].outputs)
            )
        )
        if not ok:
            _err(errors, s_path, "invalid_reference", f"{src!r} does not resolve")


_SEMANTIC: dict[type, Callable[[Any, str, Errors], None]] = {
    Rule: _check_rule,
    Trigger: _check_trigger,
    WorkflowRef: _check_workflow_ref,
    Action: _check_action,
    RetryPolicy: _check_retry,
    Placement: _check_placement,
    Workflow: _check_workflow,
    Step: _check_step,
    Port: _check_port,
    Variable: _check_variable,
    Output: _check_output,
    Edge: _check_edge,
    Actor: _check_actor,
    Machine: _check_machine,
    MachineInstall: _check_machine_install,
}
