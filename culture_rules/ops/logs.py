"""Structured JSON logs, run-id context and cross-node step trails.

- :func:`log_context` binds ``run_id`` / ``step_id`` / ``host`` to the current
  execution context (a :mod:`contextvars` variable, so it follows threads and
  asyncio tasks); :class:`JsonFormatter` writes one JSON object per record with
  those three fields.
- :func:`step_trail` answers "what happened to run X, on which nodes": it merges the
  run document's ``history`` with the audit entries targeting the run, so any node
  sharing the store returns the same trail.
- :func:`stamp_envelope` / :func:`emit_for_run` put ``runId`` / ``correlationId`` onto
  events-cli envelopes before they are published.

Standard-library only.
"""

from __future__ import annotations

import contextvars
import copy
import json
import logging
import socket
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.audit import AUDIT_COLLECTION
from culture_rules.events.emit import Emitter, derive_envelope
from culture_rules.store.port import StoreOps

__all__ = [
    "JsonFormatter",
    "configure_logging",
    "current_context",
    "emit_for_run",
    "log_context",
    "stamp_envelope",
    "step_trail",
]

_FIELDS = ("run_id", "step_id", "host")
_OURS = "_culture_rules_json_handler"
_CTX: contextvars.ContextVar[Mapping[str, str | None]] = contextvars.ContextVar(
    "culture_rules_log_context", default=dict.fromkeys(_FIELDS)
)


def current_context() -> dict[str, str | None]:
    """The ``run_id`` / ``step_id`` / ``host`` bound to this context (None when unset)."""
    return dict(_CTX.get())


@contextmanager
def log_context(
    *, run_id: str | None = None, step_id: str | None = None, host: str | None = None
) -> Iterator[None]:
    """Bind fields for the duration of the block; unset arguments inherit the outer value."""
    outer = _CTX.get()
    merged = dict(outer)
    for name, value in (("run_id", run_id), ("step_id", step_id), ("host", host)):
        if value is not None:
            merged[name] = value
    token = _CTX.set(merged)
    try:
        yield
    finally:
        _CTX.reset(token)


class JsonFormatter(logging.Formatter):
    """One JSON object per record: ``ts``, ``level``, ``logger``, ``message`` + context."""

    def __init__(self, *, host: str | None = None) -> None:
        super().__init__()
        self.host = host or socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        ctx = _CTX.get()
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="microseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": ctx.get("run_id"),
            "step_id": ctx.get("step_id"),
            "host": ctx.get("host") or self.host,
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging(
    *, level: int = logging.INFO, host: str | None = None, stream: Any = None
) -> logging.Handler:
    """Attach a JSON handler to the root logger and return it.

    Idempotent: a handler attached by an earlier call is replaced, so the root logger
    never carries more than one of them (no duplicated lines).
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(host=host))
    setattr(handler, _OURS, True)
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _OURS, False)]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)
    return handler


def step_trail(store: StoreOps, run_id: str) -> list[dict[str, Any]]:
    """Time-ordered trail of a run: its history transitions plus audit entries.

    Each item: ``source`` (``run`` | ``audit``), ``at``, ``host``, ``event``, ``step``
    and, for audit entries, ``identity``. Unknown runs yield an empty list.
    """
    items: list[dict[str, Any]] = []
    run = store.get("runs", run_id)
    for h in (run or {}).get("history", ()):
        items.append(
            {
                "source": "run",
                "at": h.get("at"),
                "host": h.get("host"),
                "event": h.get("event"),
                "step": h.get("step"),
                "rev": h.get("rev"),
            }
        )
    for e in store.find(AUDIT_COLLECTION):
        if (e.get("target") or {}).get("id") == run_id:
            items.append(
                {
                    "source": "audit",
                    "at": e.get("at"),
                    "host": e.get("host"),
                    "event": e.get("verb"),
                    "step": None,
                    "identity": e.get("identity"),
                }
            )
    return sorted(items, key=lambda i: (str(i["at"]), i["source"], i.get("rev") or 0))


def stamp_envelope(
    envelope: Mapping[str, Any],
    *,
    run_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Copy of ``envelope`` with ``runId`` / ``correlationId`` set.

    ``run_id`` defaults to the context's; an existing ``correlationId`` is kept unless
    ``correlation_id`` is given explicitly.
    """
    out = copy.deepcopy(dict(envelope))
    run = run_id or _CTX.get().get("run_id")
    if run:
        out["runId"] = run
    if correlation_id:
        out["correlationId"] = correlation_id
    return out


def emit_for_run(
    emitter: Emitter,
    type: str,
    data: Mapping[str, Any] | None = None,
    *,
    run_id: str | None = None,
    correlation_id: str | None = None,
    cause: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build, stamp with run/correlation ids and publish an envelope through ``emitter``."""
    env = derive_envelope(
        cause, type=type, source=emitter.source, data=data, run_id=run_id or _ctx_run()
    )
    env = stamp_envelope(env, run_id=run_id, correlation_id=correlation_id)
    emitter.sink.publish(env)
    return env


def _ctx_run() -> str | None:
    return _CTX.get().get("run_id")
