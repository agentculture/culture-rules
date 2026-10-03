"""Shared fixtures: one fully-populated instance of every model."""

from __future__ import annotations

import pytest

from culture_rules.model.actor import Actor
from culture_rules.model.machine import Machine
from culture_rules.model.rule import Rule
from culture_rules.model.workflow import Workflow
from tests.model.factories import make_actor, make_machine, make_rule, make_workflow


@pytest.fixture
def rule() -> Rule:
    return make_rule()


@pytest.fixture
def workflow() -> Workflow:
    return make_workflow()


@pytest.fixture
def actor() -> Actor:
    return make_actor()


@pytest.fixture
def machine() -> Machine:
    return make_machine()
