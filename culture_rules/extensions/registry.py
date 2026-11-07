"""The extension registry: what apps add to the engine, held in one place.

An extension is a name and a ``register(ext)`` function. :meth:`Registry.install` calls it
with an :class:`Extension` handle, through which it adds:

* **action kinds** - a :class:`~culture_rules.model.action_kinds.ActionKind` (its typed
  ``params`` as ``ParamSpec`` entries), optional legacy aliases and whether its required
  params are enforced (``lenient``);
* **action ports** - ``kind -> factory(store)``, the port the node serves
  ``action:<kind>`` with; ``needs_module``/``extra`` name an optional extra, and a node
  without that module serves an ``extra_missing`` port instead (nothing is imported to
  check);
* **built-in code steps** - ``name -> factory(store)``, served to a ``code`` step with no
  actor whose ``config.builtin`` names it (``action`` is reserved: the executor routes it);
* **event types** - the dotted types the extension's surface emits;
* **webhook routes** - ``POST /hooks/<name> -> factory(store)`` returning a FastAPI router;
  the API mounts it outside the OpenAPI contract and lets it through without a principal
  (the handler authenticates the delivery);
* **node listeners** - ``name -> factory(NodeContext)`` returning an object with
  ``tick(report)`` (run once per node cycle, after the probe stage) and optionally
  ``close()``; ``long_lived`` ones hold connections and are skipped by ``node run --once``;
* **node collections** - a provider of the store collections its code uses, created up
  front by every node.

Registration is idempotent (an equal entry again is a no-op) and refuses a conflicting
duplicate name with :class:`ExtensionError`, naming the extension that holds it; a refused
install leaves nothing behind. Factories are called only when the engine builds its ports,
routes or listeners, so registering imports nothing: :class:`Lazy` names a module and an
attribute and imports them on the call, which keeps each optional extra lazy.

Standard library only: this module imports nothing from culture_rules at run time.
"""

from __future__ import annotations

import importlib
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from culture_rules.model.action_kinds import ActionKind

__all__ = [
    "EVENT_TYPE_RE",
    "RESERVED_BUILTINS",
    "WEBHOOK_PREFIX",
    "ActionPortEntry",
    "BuiltinEntry",
    "EventTypeEntry",
    "Extension",
    "ExtensionError",
    "KindEntry",
    "Lazy",
    "NodeContext",
    "NodeListenerEntry",
    "Registry",
    "WebhookRouteEntry",
]

#: Lowercase dotted segments, at least two (the events-cli rule, as ``model.app_actor``).
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")
#: Built-in names core serves itself (``action``: an action step, routed by the executor).
RESERVED_BUILTINS = frozenset({"action"})
#: Every webhook route lives under this prefix (the access-log filter keys on ``/hooks``).
WEBHOOK_PREFIX = "/hooks/"
_ROUTE_RE = re.compile(r"^/hooks/[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)*$")

Factory = Callable[[Any], Any]


class ExtensionError(ValueError):
    """A registration that conflicts with another or is malformed."""


@dataclass(frozen=True)
class Lazy:
    """A factory that imports ``module`` on the call and calls its ``attr``.

    ``attr`` is called with the store when ``store`` is true, else with no argument. Equal
    by value, so the same registration made twice is a no-op.
    """

    module: str
    attr: str
    store: bool = False

    def __call__(self, store: Any) -> Any:
        target = getattr(importlib.import_module(self.module), self.attr)
        return target(store) if self.store else target()


@dataclass(frozen=True)
class NodeContext:
    """What a node listener factory gets: the node's store, host and seams."""

    store: Any
    host: str
    identity: str
    clock: Callable[[], Any]
    resolve_secret: Callable[[str], str] | None = None
    #: per-listener overrides a caller passed to the node (tests inject fakes here)
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KindEntry:
    kind: ActionKind
    extension: str
    aliases: tuple[str, ...] = ()
    lenient: bool = False


@dataclass(frozen=True)
class ActionPortEntry:
    kind: str
    factory: Factory
    extension: str
    needs_module: str | None = None
    extra: str | None = None


@dataclass(frozen=True)
class BuiltinEntry:
    name: str
    factory: Factory
    extension: str


@dataclass(frozen=True)
class EventTypeEntry:
    name: str
    extension: str
    summary: str = ""


@dataclass(frozen=True)
class WebhookRouteEntry:
    path: str
    factory: Factory
    extension: str


@dataclass(frozen=True)
class NodeListenerEntry:
    name: str
    factory: Factory
    extension: str
    long_lived: bool = False


@dataclass(frozen=True)
class _CollectionsEntry:
    provider: Callable[[], Iterable[str]]
    extension: str


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExtensionError(f"{what} must be a non-empty string, not {value!r}")
    return value


class Extension:
    """The handle a ``register(ext)`` function adds its entries through."""

    def __init__(self, registry: Registry, name: str) -> None:
        self._registry = registry
        self.name = name

    def action_kind(
        self, kind: ActionKind, *, aliases: Iterable[str] = (), lenient: bool = False
    ) -> None:
        """Catalogue ``kind`` (and its legacy ``aliases``); ``lenient``: required params are
        not enforced."""
        self._registry._add_kind(KindEntry(kind, self.name, tuple(aliases), lenient))

    def action_port(
        self,
        kind: str,
        factory: Factory,
        *,
        needs_module: str | None = None,
        extra: str | None = None,
    ) -> None:
        """Serve ``action:<kind>`` with ``factory(store)``; without ``needs_module`` installed
        the node serves an ``extra_missing`` port naming ``extra``."""
        entry = ActionPortEntry(_name(kind, "kind"), factory, self.name, needs_module, extra)
        self._registry._add("ports", entry.kind, entry, "action port")

    def builtin(self, name: str, factory: Factory) -> None:
        """Serve the built-in code step ``name`` with ``factory(store)``."""
        if _name(name, "builtin name") in RESERVED_BUILTINS:
            raise ExtensionError(f"builtin {name!r} is reserved by core")
        self._registry._add("builtins", name, BuiltinEntry(name, factory, self.name), "builtin")

    def event_type(self, name: str, *, summary: str = "") -> None:
        """Declare an event type this extension's surface emits."""
        if not EVENT_TYPE_RE.match(_name(name, "event type")):
            raise ExtensionError(f"{name!r} is not a lowercase dotted event type")
        self._registry._add("events", name, EventTypeEntry(name, self.name, summary), "event type")

    def webhook_route(self, path: str, factory: Factory) -> None:
        """Mount ``POST <path>`` (under ``/hooks/``) from ``factory(store)``, a router."""
        if not _ROUTE_RE.match(_name(path, "webhook path")):
            raise ExtensionError(f"webhook path {path!r} must be a literal path under /hooks/")
        entry = WebhookRouteEntry(path, factory, self.name)
        self._registry._add("routes", path, entry, "webhook route")

    def node_listener(self, name: str, factory: Factory, *, long_lived: bool = False) -> None:
        """Tick ``factory(NodeContext)`` once per node cycle (see the module docstring)."""
        entry = NodeListenerEntry(_name(name, "listener name"), factory, self.name, long_lived)
        self._registry._add("listeners", name, entry, "node listener")

    def node_collections(self, provider: Callable[[], Iterable[str]]) -> None:
        """Store collections this extension's code uses; every node creates them."""
        key = (self.name, getattr(provider, "__qualname__", repr(provider)))
        entry = _CollectionsEntry(provider, self.name)
        self._registry._add("collections", key, entry, "node collections")


class Registry:
    """The registered extensions and their entries (see the module docstring)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._installed: dict[str, Callable[[Extension], None]] = {}
        self._tables: dict[str, dict[Any, Any]] = {
            name: {}
            for name in (
                "kinds",
                "ports",
                "builtins",
                "events",
                "routes",
                "listeners",
                "collections",
            )
        }
        self._aliases: dict[str, str] = {}
        self._catalogue: dict[str, ActionKind] = {}

    # ------------------------------------------------------------------ installing

    def install(self, name: str, register: Callable[[Extension], None]) -> bool:
        """Install extension ``name``; False when ``register`` is already installed under it.

        Another function under a taken name, or any entry that conflicts, raises
        :class:`ExtensionError` and leaves the registry as it was."""
        _name(name, "extension name")
        with self._lock:
            prior = self._installed.get(name)
            if prior is register:
                return False
            if prior is not None:
                raise ExtensionError(f"extension {name!r} is already installed")
            self._installed[name] = register
            try:
                register(Extension(self, name))
            except BaseException:
                self.uninstall(name)
                raise
            return True

    def uninstall(self, name: str) -> None:
        """Remove extension ``name`` and every entry it registered (unknown: no-op)."""
        with self._lock:
            self._installed.pop(name, None)
            for table in self._tables.values():
                for key in [k for k, e in table.items() if e.extension == name]:
                    del table[key]
            self._aliases = {
                alias: kind
                for alias, kind in self._aliases.items()
                if kind in self._tables["kinds"]
            }
            self._catalogue = {k: e.kind for k, e in self._tables["kinds"].items()}

    def installed(self) -> tuple[str, ...]:
        """The installed extensions, in install order."""
        return tuple(self._installed)

    def _add(self, table: str, key: Any, entry: Any, what: str) -> None:
        with self._lock:
            prior = self._tables[table].get(key)
            if prior is None:
                self._tables[table][key] = entry
            elif prior != entry:
                raise ExtensionError(
                    f"{what} {key!r} is already registered by extension {prior.extension!r}"
                )

    def _add_kind(self, entry: KindEntry) -> None:
        name = _name(getattr(entry.kind, "name", None), "action kind name")
        with self._lock:
            names = (name, *entry.aliases)
            for alias in entry.aliases:
                _name(alias, "alias")
                owner = self._aliases.get(alias, name)
                if owner != name or alias in self._tables["kinds"]:
                    raise ExtensionError(f"alias {alias!r} is already registered")
            if name in self._aliases or len(set(names)) != len(names):
                raise ExtensionError(f"action kind {name!r} is already registered as an alias")
            self._add("kinds", name, entry, "action kind")
            self._aliases.update({alias: name for alias in entry.aliases})
            self._catalogue[name] = entry.kind

    # ------------------------------------------------------------------ reading

    def action_kinds(self) -> Mapping[str, ActionKind]:
        """The catalogue, ``name -> ActionKind`` in registration order (read-only, live)."""
        return MappingProxyType(self._catalogue)

    def resolve_kind(self, kind: str) -> ActionKind | None:
        """The catalogue entry for ``kind`` (aliases resolved), or None if unknown."""
        return self._catalogue.get(self._aliases.get(kind, kind))

    def is_lenient(self, kind: str) -> bool:
        """Whether ``kind``'s required params are not enforced (legacy-tolerant kinds)."""
        entry = self._tables["kinds"].get(self._aliases.get(kind, kind))
        return bool(entry and entry.lenient)

    def aliases(self, kind: str) -> tuple[str, ...]:
        """The legacy names of ``kind``."""
        entry = self._tables["kinds"].get(kind)
        return entry.aliases if entry else ()

    def action_ports(self) -> tuple[ActionPortEntry, ...]:
        return tuple(self._tables["ports"].values())

    def builtins(self) -> tuple[BuiltinEntry, ...]:
        return tuple(self._tables["builtins"].values())

    def event_types(self) -> Mapping[str, EventTypeEntry]:
        return MappingProxyType(dict(self._tables["events"]))

    def webhook_routes(self) -> tuple[WebhookRouteEntry, ...]:
        return tuple(self._tables["routes"].values())

    def node_listeners(self) -> tuple[NodeListenerEntry, ...]:
        return tuple(self._tables["listeners"].values())

    def node_collections(self) -> tuple[str, ...]:
        """Every extension's collections (each provider is called now), first seen first."""
        names: dict[str, None] = {}
        for entry in tuple(self._tables["collections"].values()):
            names.update(dict.fromkeys(entry.provider()))
        return tuple(names)
