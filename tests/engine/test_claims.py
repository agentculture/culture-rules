"""Unit tests for idempotency keys and Claims argument handling (store-agnostic)."""

from __future__ import annotations

import ast
import subprocess  # nosec B404 - runs this interpreter only
import sys
from datetime import timedelta
from pathlib import Path

import pytest

import culture_rules.engine.claims as claims_module
from culture_rules.engine.claims import Claims, firing_key, idempotency_key
from culture_rules.store.memory import MemoryStore

FIRING_GOLDEN = "ik1:29b652fa52625790c8f2d6691ace500a11a66bdb6180994d0944f92e2c1425cd"
GOLDEN = "ik1:657fa1e4c37c220da15b809c70b270e3127200f96f93a2c0d2b7cd78eb4dbe7b"


def test_key_is_a_stable_hash_of_run_and_step():
    # Pinned value: every host, process and release must derive the same key.
    assert idempotency_key("run-1", "step-a") == GOLDEN


def test_key_is_identical_in_a_fresh_interpreter():
    code = (
        "from culture_rules.engine.claims import idempotency_key;"
        "print(idempotency_key('run-1', 'step-a'))"
    )
    out = subprocess.run(  # nosec B603 - fixed argv, this interpreter
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env={"PYTHONHASHSEED": "12345", "PATH": ""},
    )
    assert out.stdout.strip() == GOLDEN


def test_key_takes_no_attempt():
    with pytest.raises(TypeError):
        idempotency_key("run-1", "step-a", 2)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        idempotency_key("run-1", "step-a", attempt=2)  # type: ignore[call-arg]


def test_key_separates_its_inputs():
    assert idempotency_key("run-1", "step-a") != idempotency_key("run-1", "step-b")
    assert idempotency_key("run-1", "step-a") != idempotency_key("run-2", "step-a")
    # No ambiguity from concatenation.
    assert idempotency_key("ab", "c") != idempotency_key("a", "bc")
    assert idempotency_key("a:b", "c") != idempotency_key("a", "b:c")


def test_firing_key_is_disjoint_from_step_keys():
    # Pinned: every host must derive the same firing key for the same rule and event.
    assert firing_key("rule-1", "event-1") == FIRING_GOLDEN
    assert firing_key("rule-1", "event-1") != idempotency_key("rule-1", "event-1")
    assert firing_key("rule-1", "event-1") != firing_key("rule-1", "event-2")
    assert firing_key("rule-1", "event-1").startswith("ik1:")


@pytest.mark.parametrize("bad", ["", None, 5])
def test_key_rejects_bad_ids(bad):
    with pytest.raises(ValueError):
        idempotency_key(bad, "step-a")
    with pytest.raises(ValueError):
        idempotency_key("run-1", bad)
    with pytest.raises(ValueError):
        firing_key(bad, "event-1")


def test_claims_rejects_bad_holder_and_lease():
    store = MemoryStore()
    with pytest.raises(ValueError):
        Claims(store, "")
    zero = timedelta(0)
    with pytest.raises(ValueError):
        Claims(store, "spark", lease=zero)


def test_claim_rejects_unknown_keys_without_a_kind():
    claims = Claims(MemoryStore(), "spark")
    with pytest.raises(ValueError):
        claims.claim("", kind="step")
    with pytest.raises(ValueError):
        claims.claim("ik1:x", kind="bogus")


def test_is_completed_false_for_unknown_key():
    assert Claims(MemoryStore(), "spark").is_completed("ik1:none") is False


def test_claims_module_has_no_third_party_imports():
    tree = ast.parse(Path(claims_module.__file__).read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add(node.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"culture_rules"}
    assert roots <= allowed, roots - allowed
