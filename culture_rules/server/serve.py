"""``serve``: run the API under uvicorn. FastAPI and uvicorn are imported lazily, here only.

Two listeners, on purpose (see :mod:`culture_rules.auth.resolve`):

- ``lan`` on ``CULTURE_RULES_HOST:CULTURE_RULES_PORT`` (default ``127.0.0.1:8765``): service
  tokens only; a ``Cf-Access-Jwt-Assertion`` header is ignored there.
- ``loopback`` on ``CULTURE_RULES_ACCESS_LISTEN`` (the address ``cloudflared`` forwards to),
  which honours Cloudflare Access JWTs pinned to ``CULTURE_RULES_ACCESS_TEAM_DOMAIN`` and
  ``CULTURE_RULES_ACCESS_AUD``. The three Access variables are all set or all unset; a partial
  tuple is refused (:class:`~culture_rules.auth.access.AccessConfigError`).

Roles for SSO users come from ``CULTURE_RULES_ADMINS`` / ``CULTURE_RULES_EDITORS``
(comma-separated identities); everyone else Access lets through is a viewer.
``CULTURE_RULES_INSECURE_DEV_IDENTITY=1`` re-enables the unauthenticated ``X-Culture-Identity``
dev header (everyone is admin) - for a laptop only; it is off by default.

``/health`` reports on the engine node named by ``CULTURE_RULES_NODE_NAME`` (or
``serve --node-name``); unset, the short hostname. Set it to the name the node runs under
(``culture-rules node run`` defaults its ``--host`` to the same variable), or ``/health``
stays ``degraded`` waiting for a heartbeat that never comes.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from culture_rules.auth.access import AccessConfigError, AccessListenerConfig, AccessVerifier
from culture_rules.auth.resolve import LAN, LOOPBACK, AuthSettings
from culture_rules.ops.nodename import node_name as default_node_name
from culture_rules.store.port import StoragePort

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "Listener",
    "ServerExtraMissing",
    "build_listeners",
    "serve",
    "store_from_env",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
_TRUE = ("1", "true", "yes", "on")


class ServerExtraMissing(RuntimeError):
    """The optional ``server`` extra (FastAPI + uvicorn) is not installed."""


@dataclass(frozen=True)
class Listener:
    """One bound app: ``lan`` (service tokens) or ``loopback`` (also Cloudflare Access)."""

    name: str
    host: str
    port: int
    app: Any


def store_from_env() -> StoragePort:
    """The MongoDB store configured by ``CULTURE_RULES_MONGO_*`` (needs the ``store`` extra)."""
    from culture_rules.store.mongo import MongoConfig, MongoStore  # noqa: PLC0415

    return MongoStore(MongoConfig.from_env())


def _create_app() -> Callable[..., Any]:
    try:
        import fastapi  # noqa: F401, PLC0415 - optional extra, imported lazily

        from culture_rules.server.app import create_app  # noqa: PLC0415
    except ImportError as exc:
        raise ServerExtraMissing(
            "the HTTP API needs the optional extra: pip install 'culture-rules[server]'"
        ) from exc
    return create_app


def _names(value: str | None) -> frozenset[str]:
    return frozenset(v.strip() for v in (value or "").split(",") if v.strip())


def build_listeners(
    store: StoragePort,
    *,
    env: Mapping[str, str] | None = None,
    host: str | None = None,
    port: int | None = None,
    admins: tuple[str, ...] = (),
    fetch_jwks: Callable[[], Any] | None = None,
    node_name: str | None = None,
) -> list[Listener]:
    """The apps to serve: always ``lan``; plus ``loopback`` when Access is fully configured.

    ``node_name`` is the engine node ``/health`` reports on (default:
    ``CULTURE_RULES_NODE_NAME`` from ``env``, else the short hostname).
    """
    env = os.environ if env is None else env
    node = (node_name or "").strip() or default_node_name(env)
    access_cfg = AccessListenerConfig.from_env(env)  # raises on a partial tuple
    create_app = _create_app()
    common = {
        "admins": _names(env.get("CULTURE_RULES_ADMINS")) | frozenset(admins),
        "editors": _names(env.get("CULTURE_RULES_EDITORS")),
        "insecure_dev_identity": (env.get("CULTURE_RULES_INSECURE_DEV_IDENTITY") or "").lower()
        in _TRUE,
    }
    create = functools.partial(create_app, host=node)
    lan_host = host or env.get("CULTURE_RULES_HOST") or DEFAULT_HOST
    lan_port = int(port or env.get("CULTURE_RULES_PORT") or DEFAULT_PORT)
    listeners = [Listener(LAN, lan_host, lan_port, create(store, auth=AuthSettings(LAN, **common)))]
    if access_cfg is not None:
        if (access_cfg.host, access_cfg.port) == (lan_host, lan_port):
            raise AccessConfigError("the Access listener must not share the LAN listener address")
        verifier = AccessVerifier(
            access_cfg.team_domain, access_cfg.audience, fetch_jwks=fetch_jwks
        )
        settings = AuthSettings(LOOPBACK, access=verifier, **common)
        app = create(store, auth=settings)
        listeners.append(Listener(LOOPBACK, access_cfg.host, access_cfg.port, app))
    return listeners


def _run_servers(configs: list[Any]) -> None:
    import asyncio  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415 - optional extra, imported lazily

    async def main() -> None:
        await asyncio.gather(*(uvicorn.Server(c).serve() for c in configs))

    asyncio.run(main())


def serve(
    store: StoragePort | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    admins: tuple[str, ...] = (),
    fetch_jwks: Callable[[], Any] | None = None,
    node_name: str | None = None,
    **uvicorn_options: Any,
) -> None:
    """Serve the HTTP API (one or two listeners) until interrupted. Stateless: run many."""
    _create_app()  # fail fast and cleanly without the extra
    import uvicorn  # noqa: PLC0415 - optional extra, imported lazily

    listeners = build_listeners(
        store if store is not None else store_from_env(),
        host=host,
        port=port,
        admins=admins,
        fetch_jwks=fetch_jwks,
        node_name=node_name,
    )
    _run_servers(
        [
            uvicorn.Config(lst.app, host=lst.host, port=lst.port, **uvicorn_options)
            for lst in listeners
        ]
    )
