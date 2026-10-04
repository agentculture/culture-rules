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

The public webhook receivers (``POST /hooks/github``, ``POST /hooks/jira``) are mounted on
every listener; the Jira one may carry its secret as ``?token=``, so :func:`serve` installs
:class:`HookQueryFilter` on the ``uvicorn.access`` logger, which drops the query string from
any access-log line whose path mentions ``/hooks``.
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from culture_rules.auth.access import AccessConfigError, AccessListenerConfig, AccessVerifier
from culture_rules.auth.resolve import LAN, LOOPBACK, AuthSettings
from culture_rules.ops.nodename import node_name as default_node_name
from culture_rules.store.port import StoragePort

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "HookQueryFilter",
    "Listener",
    "ServerExtraMissing",
    "build_listeners",
    "install_hook_log_filter",
    "serve",
    "store_from_env",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
_TRUE = ("1", "true", "yes", "on")
_ACCESS_LOGGER = "uvicorn.access"
_HOOK_MARK = "/hooks"


class HookQueryFilter(logging.Filter):
    """Strip the query string from uvicorn access-log lines for webhook-like paths.

    uvicorn logs ``(client, method, path-with-query, http_version, status)``; a webhook's
    query may hold a secret (Jira's ``?token=``), so ``args[2]`` is cut at the first ``?``
    whenever the percent-decoded, lower-cased path contains ``/hooks`` - the real routes, the
    ``/api`` alias and the near-miss variants the auth middleware refuses (a misconfigured
    sender's token must not reach the log either). Never drops a record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path, sep, _ = args[2].partition("?")
            if sep and _HOOK_MARK in unquote(path).lower():
                record.args = (*args[:2], path, *args[3:])
        return True


def install_hook_log_filter() -> None:
    """Attach :class:`HookQueryFilter` to ``uvicorn.access`` once (idempotent)."""
    access = logging.getLogger(_ACCESS_LOGGER)
    if not any(isinstance(f, HookQueryFilter) for f in access.filters):
        access.addFilter(HookQueryFilter())


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
        import fastapi  # noqa: F401, PLC0415 - optional extra imported lazily

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

    import uvicorn  # noqa: PLC0415 - optional extra imported lazily

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
    import uvicorn  # noqa: PLC0415 - optional extra imported lazily

    listeners = build_listeners(
        store if store is not None else store_from_env(),
        host=host,
        port=port,
        admins=admins,
        fetch_jwks=fetch_jwks,
        node_name=node_name,
    )
    configs = [
        uvicorn.Config(lst.app, host=lst.host, port=lst.port, **uvicorn_options)
        for lst in listeners
    ]
    # after Config: building one (re)configures uvicorn's loggers from its log config
    install_hook_log_filter()
    _run_servers(configs)
