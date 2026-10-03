"""``serve``: run the API under uvicorn. FastAPI and uvicorn are imported lazily, here only."""

from __future__ import annotations

import os
from typing import Any

from culture_rules.store.port import StoragePort

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "ServerExtraMissing", "serve", "store_from_env"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class ServerExtraMissing(RuntimeError):
    """The optional ``server`` extra (FastAPI + uvicorn) is not installed."""


def store_from_env() -> StoragePort:
    """The MongoDB store configured by ``CULTURE_RULES_MONGO_*`` (needs the ``store`` extra)."""
    from culture_rules.store.mongo import MongoConfig, MongoStore  # noqa: PLC0415

    return MongoStore(MongoConfig.from_env())


def serve(
    store: StoragePort | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    admins: tuple[str, ...] = (),
    **uvicorn_options: Any,
) -> None:
    """Serve the HTTP API until interrupted. Stateless: run as many copies as you like."""
    try:
        import uvicorn  # noqa: PLC0415 - optional extra, imported lazily

        from culture_rules.server.app import create_app  # noqa: PLC0415
    except ImportError as exc:
        raise ServerExtraMissing(
            "the HTTP API needs the optional extra: pip install 'culture-rules[server]'"
        ) from exc
    app = create_app(store if store is not None else store_from_env(), admins=admins)
    uvicorn.run(
        app,
        host=host or os.environ.get("CULTURE_RULES_HOST", DEFAULT_HOST),
        port=port or int(os.environ.get("CULTURE_RULES_PORT", DEFAULT_PORT)),
        **uvicorn_options,
    )
