"""Trigger kinds carry required params (spec c3, h1, c42)."""

from __future__ import annotations

import pytest

from culture_rules.model.rule import TRIGGER_KINDS, Trigger
from culture_rules.model.validate import validate
from tests.model.factories import make_rule


def _errs(kind: str, params: dict) -> set[tuple[str, str]]:
    rule = make_rule(trigger=Trigger(kind=kind, params=params))
    return {(e.path, e.code) for e in validate(rule)}


def test_trigger_kinds_constant() -> None:
    assert set(TRIGGER_KINDS) == {"event", "schedule", "probe", "manual"}


@pytest.mark.parametrize("params", [{}, {"type": ""}, {"type": "  "}, {"type": 3}])
def test_event_requires_type(params: dict) -> None:
    assert ("trigger.params.type", "trigger_type_required") in _errs("event", params)


def test_event_with_type_is_valid() -> None:
    assert _errs("event", {"type": "demo.greet"}) == set()


def test_schedule_requires_cron() -> None:
    assert ("trigger.params.cron", "trigger_cron_required") in _errs("schedule", {})
    assert _errs("schedule", {"cron": "*/5 * * * *", "tz": "UTC"}) == set()


@pytest.mark.parametrize("missing", ["actor", "command", "schedule", "mode"])
def test_probe_requires_fields(missing: str) -> None:
    params = {"actor": "a", "command": "c", "schedule": "* * * * *", "mode": "change"}
    del params[missing]
    assert (f"trigger.params.{missing}", "trigger_param_required") in _errs("probe", params)


def test_probe_mode_must_be_known() -> None:
    params = {"actor": "a", "command": "c", "schedule": "* * * * *", "mode": "bogus"}
    assert ("trigger.params.mode", "trigger_param_invalid") in _errs("probe", params)
    for mode in ("change", "condition"):
        assert _errs("probe", {**params, "mode": mode}) == set()


def test_unknown_kind_rejected() -> None:
    assert ("trigger.kind", "trigger_kind_unknown") in _errs("banana", {})


def test_manual_takes_no_params() -> None:
    assert _errs("manual", {}) == set()
