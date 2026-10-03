"""Audit scenarios for the service-token verbs (consumed by tests/engine/test_audit_lifecycle)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from culture_rules.auth.tokens import ServiceTokens


def _issue(life: Any, store: Any) -> Callable[[], Any]:
    return lambda: ServiceTokens(store).issue("root", name="ci", roles=["viewer"])


def _revoke(life: Any, store: Any) -> Callable[[], Any]:
    issued = ServiceTokens(store).issue("root", name="ci", roles=["viewer"])
    return lambda: ServiceTokens(store).revoke(issued.id, "root")


AUTH_AUDIT_SCENARIOS = {
    "service_tokens.issue": _issue,
    "service_tokens.revoke": _revoke,
}
