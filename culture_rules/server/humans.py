"""A person's human actor, created on their first Cloudflare Access sign-in.

:func:`ensure_human` inserts, if absent, an actor of kind ``human`` for an ``sso`` principal:

- **id** - the slugged email (:func:`human_id`): lower-cased, every run of characters
  outside ``[a-z0-9]`` becomes one ``-``, leading/trailing ``-`` dropped
  (``ori.nachum@gmail.com`` -> ``ori-nachum-gmail-com``). Deterministic, so every instance
  and every concurrent request agrees on the one document to insert.
- **name** - the email; ``params.email`` keeps it too, so the actor maps back to the identity.

Who gets one: only an ``sso`` principal whose identity is an email. Service tokens (bearer
or Access service tokens, ``kind == "service"``) and agents never do, and neither does the
LAN listener, which only accepts service tokens. The insecure dev identity header resolves to
an ``sso`` principal too, so a dev identity that *is* an email (``bob@example.com``) gets an
actor - it stands in for an Access person on a laptop - while ``anonymous`` or a bare nick
(``alice``) does not. Webhook requests (no principal) are skipped by the caller.

Insert only, never update: if a document with that id already exists - edited by an operator,
or soft-deleted (``deleted_at`` set) - it is left exactly as it is, so edits survive and a
deleted person is not resurrected by signing in again (restore it through the API). Two
emails that slug to the same id share the first one's actor. The insert and its audit entry
(verb ``definitions.create``, identity = the person) commit in one transaction; a concurrent
insert of the same id loses with ``DuplicateKeyError``, which counts as "exists".

:class:`HumanSignIn` is the per-process (per-app) cache the auth middleware calls: once an
identity is known to have its actor, later requests cost no store call. Failures are logged
as warnings and never raised - a broken store must not fail sign-in - and are not cached, so
the next request tries again.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Literal

from culture_rules.auth.principal import Principal
from culture_rules.engine.audit import AuditLog
from culture_rules.model.actor import Actor
from culture_rules.model.validate import validate
from culture_rules.store.port import DuplicateKeyError, StoragePort

__all__ = ["ACTORS", "HumanSignIn", "ensure_human", "human_id", "is_email"]

ACTORS = "actors"
_log = logging.getLogger(__name__)
_NOT_SLUG = re.compile(r"[^a-z0-9]+")

Outcome = Literal["created", "exists", "skipped"]


def is_email(identity: str) -> bool:
    """``local@domain`` with both parts non-blank (no whitespace)."""
    local, at, domain = identity.strip().rpartition("@")
    return bool(at and local and domain) and not any(c.isspace() for c in identity.strip())


def human_id(email: str) -> str:
    """The actor id for ``email``: its lower-cased slug (``a.b@c.io`` -> ``a-b-c-io``)."""
    return _NOT_SLUG.sub("-", email.strip().lower()).strip("-")


def _person_email(principal: Principal | None) -> str | None:
    """The email of an ``sso`` principal that should have a human actor, else ``None``."""
    if principal is None or principal.kind != "sso" or not is_email(principal.identity):
        return None
    email = principal.identity.strip()
    return email if human_id(email) else None


def ensure_human(
    store: StoragePort, principal: Principal | None, *, audit: AuditLog | None = None
) -> Outcome:
    """Insert the principal's human actor if absent; never touches an existing document.

    Returns ``"created"``, ``"exists"`` (including a soft-deleted or concurrently created
    one) or ``"skipped"`` (not an ``sso`` email principal). Store errors propagate.
    """
    email = _person_email(principal)
    if email is None:
        return "skipped"
    ident = human_id(email)
    actor = Actor(
        id=ident,
        name=email,
        kind="human",
        description="Created on first Cloudflare Access sign-in",
        params={"email": email},
    )
    errors = validate(actor)
    if errors:
        raise ValueError(f"human actor {ident!r} is invalid: {errors[0].message}")
    audit = audit or AuditLog()
    try:
        with store.transaction() as tx:
            if tx.get(ACTORS, ident) is not None:
                return "exists"
            after = tx.insert(ACTORS, {"id": ident, **actor.to_dict()})
            audit.write(
                tx,
                identity=email,
                verb="definitions.create",
                collection=ACTORS,
                target_id=ident,
                before=None,
                after=after,
            )
    except DuplicateKeyError:
        return "exists"
    return "created"


class HumanSignIn:
    """The middleware's entry point: ensure once per identity per process; never raises."""

    def __init__(self, store: StoragePort, audit: AuditLog | None = None) -> None:
        self._store = store
        self._audit = audit
        self._known: set[str] = set()
        self._lock = threading.Lock()

    def needed(self, principal: Principal | None) -> bool:
        """True when this principal should get an actor and is not yet known to have one."""
        email = _person_email(principal)
        if email is None:
            return False
        with self._lock:
            return email not in self._known

    def __call__(self, principal: Principal | None) -> None:
        email = _person_email(principal)
        if email is None or not self.needed(principal):
            return
        try:
            ensure_human(self._store, principal, audit=self._audit)
        except Exception as exc:  # noqa: BLE001 - a sign-in must never fail on this
            _log.warning(
                "could not ensure human actor %s: %s: %s",
                human_id(email),
                type(exc).__name__,
                exc,
            )
            return
        with self._lock:
            self._known.add(email)

    def clear(self) -> None:
        """Forget every known identity (tests; a fresh process starts empty anyway)."""
        with self._lock:
            self._known.clear()
