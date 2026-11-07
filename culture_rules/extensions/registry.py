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
  its ``label`` and typed ``inputs``/``outputs`` (:class:`BuiltinPortSpec` each) are data the
  API serves as the builtin catalogue (``GET /steps/builtins``), so an editor keeps no table;
* **event types** - the dotted types the extension's surface emits;
* **webhook routes** - ``POST /hooks/<name> -> factory(store)`` returning a FastAPI router;
  the API mounts it outside the OpenAPI contract and lets it through without a principal
  (the handler authenticates the delivery);
* **node listeners** - ``name -> factory(NodeContext)`` returning an object with
  ``tick(report)`` (run once per node cycle, after the probe stage) and optionally
  ``close()``; ``long_lived`` ones hold connections and are skipped by ``node run --once``;
* **node collections** - a provider of the store collections its code uses, created up
  front by every node;
* **API routes** - ``name -> mount(app, ApiContext)``: routes an app adds to the API *inside*
  the OpenAPI contract (``GET /actors/{id}/discord/targets``); the API mounts them in
  registration order at one fixed point, so the contract's path order holds. ``roles``
  declares the role each of its routes needs (``(method, path template, role)``), which the
  auth policy reads (:meth:`Registry.route_role`);
* **params validators** - ``kind -> validate(params, path)``, the kind's own save-time checks
  beyond its typed params; each problem is a ``(path, code, message)`` triple, which
  ``model.validate`` reports as a ``ValidationError``;
* **trigger filters** - ``namespace -> match/validate/describe`` for the event types
  ``<namespace>.*``: ``match(params, data, own)`` admits an event to an event trigger (the
  engine finds it with one dict lookup per event), ``validate(params, path)`` sees *every*
  event trigger's params at save (so it can refuse its filter params on another type) and
  ``describe(params)`` gives the words after the type;
* **phrases** - ``kind -> words`` (the literal params worth a word, ``(param, template)`` or
  ``(param, template, test)``, and ``after(params)``, words after the placement) and a
  built-in's ``phrase``: what ``model.describe`` says for an app's kind or built-in;
* **health sections** - ``name -> provider(store, now)``, a key of the node's health document
  (after ``hooks``); ``empty()`` is its value when the store is unreachable;
* **event reservations** - ``name -> check(envelope)``, a reason an envelope may not enter from
  the bus or a webhook (types and id prefixes only the engine writes), checked by
  ``events.emit.reserved_reason`` in registration order.

Registration is idempotent (an equal entry again is a no-op) and refuses a conflicting
duplicate name with :class:`ExtensionError`, naming the extension that holds it; a refused
install leaves nothing behind. Factories are called only when the engine builds its ports,
routes or listeners, so registering imports nothing: :class:`Lazy` names a module and an
attribute and imports them on the call, which keeps each optional extra lazy.

Standard library only: this module imports nothing from culture_rules at run time.
"""

from __future__ import annotations

import functools
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
    "RESERVED_HEALTH_SECTIONS",
    "ROLES",
    "WEBHOOK_PREFIX",
    "ActionPhrasesEntry",
    "ActionPortEntry",
    "ApiContext",
    "ApiRoutesEntry",
    "BuiltinEntry",
    "BuiltinPortSpec",
    "EventReservationEntry",
    "EventTypeEntry",
    "Extension",
    "ExtensionError",
    "HealthSectionEntry",
    "KindEntry",
    "Lazy",
    "NodeContext",
    "PORT_TYPES",
    "NodeListenerEntry",
    "ParamsValidatorEntry",
    "Problem",
    "Registry",
    "TriggerFilterEntry",
    "WebhookRouteEntry",
]

#: Lowercase dotted segments, at least two (the events-cli rule, as ``model.app_actor``).
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")
#: Built-in names core serves itself (``action``: an action step, routed by the executor).
RESERVED_BUILTINS = frozenset({"action"})
#: Every webhook route lives under this prefix (the access-log filter keys on ``/hooks``).
WEBHOOK_PREFIX = "/hooks/"
_ROUTE_RE = re.compile(r"^/hooks/[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)*$")
#: The first segment of the event types a trigger filter covers (``discord``: ``discord.*``).
_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
#: The health document's own keys; a section may not take one.
RESERVED_HEALTH_SECTIONS = frozenset(
    {"host", "at", "version", "store", "heartbeat", "executor", "hooks"}
    | {"chain_needs_review", "status"}
)
#: The API's roles, lowest first (``auth.policy``).
ROLES = ("viewer", "editor", "admin")
_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"})
#: A workflow port's types (``model.workflow.PORT_TYPES``; a test pins the two together).
PORT_TYPES = ("string", "number", "integer", "boolean", "object", "array", "any")
_TEMPLATE_RE = re.compile(r"^(/([A-Za-z0-9._~-]+|\{[A-Za-z_][A-Za-z0-9_]*\}))+$")

Factory = Callable[[Any], Any]
#: A problem a params or trigger-filter validator reports: ``(path, code, message)``.
Problem = tuple[str, str, str]


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
class ApiContext:
    """What an API routes mount gets: the API's store and seams."""

    store: Any
    resolve_secret: Callable[[str], str] | None = None
    #: per-mount overrides a caller passed to the API (tests inject fakes here)
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
class BuiltinPortSpec:
    """One typed port a built-in takes or gives, as an editor pre-fills it."""

    name: str
    type: str = "string"
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "required": self.required}


@dataclass(frozen=True)
class BuiltinEntry:
    name: str
    factory: Factory
    extension: str
    phrase: str = ""
    label: str = ""
    inputs: tuple[BuiltinPortSpec, ...] = ()
    outputs: tuple[BuiltinPortSpec, ...] = ()

    @property
    def shown_label(self) -> str:
        """How a picker names it: its label, else its phrase, else its name."""
        return self.label or self.phrase or self.name


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
class ApiRoutesEntry:
    name: str
    mount: Callable[[Any, ApiContext], None]
    extension: str
    #: ``(method, path template, role)`` per route it mounts (see :meth:`Registry.route_role`)
    roles: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class ParamsValidatorEntry:
    kind: str
    validate: Callable[[Mapping[str, Any], str], Iterable[Problem]]
    extension: str


@dataclass(frozen=True)
class TriggerFilterEntry:
    namespace: str
    match: Callable[[Mapping[str, Any], Mapping[str, Any], bool], bool]
    extension: str
    validate: Callable[[Mapping[str, Any], str], Iterable[Problem]] | None = None
    describe: Callable[[Mapping[str, Any]], Iterable[str]] | None = None


@dataclass(frozen=True)
class ActionPhrasesEntry:
    kind: str
    #: ``(param, template)`` or ``(param, template, test)``: see :meth:`Extension.action_phrases`
    words: tuple[tuple[Any, ...], ...]
    extension: str
    after: Callable[[Mapping[str, Any]], Iterable[str]] | None = None


def _none() -> None:
    return None


@dataclass(frozen=True)
class HealthSectionEntry:
    name: str
    provider: Callable[[Any, Any], Any]
    extension: str
    empty: Callable[[], Any] = _none


@dataclass(frozen=True)
class EventReservationEntry:
    name: str
    check: Callable[[Mapping[str, Any]], str | None]
    extension: str


@dataclass(frozen=True)
class _CollectionsEntry:
    provider: Callable[[], Iterable[str]]
    extension: str


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExtensionError(f"{what} must be a non-empty string, not {value!r}")
    return value


def _callable(value: Any, what: str) -> Any:
    if not callable(value):
        raise ExtensionError(f"{what} must be callable, not {value!r}")
    return value


def _optional_callable(value: Any, what: str) -> Any:
    return None if value is None else _callable(value, what)


def _words(words: Iterable[Any]) -> tuple[tuple[Any, ...], ...]:
    """Phrase words as a tuple: each ``(param, template)`` or ``(param, template, test)``."""
    out = []
    for word in words:
        word = tuple(word) if isinstance(word, (list, tuple)) else (word,)
        ok = len(word) in (2, 3) and all(isinstance(w, str) and w for w in word[:2])
        if not ok or (len(word) == 3 and not callable(word[2])):
            raise ExtensionError(
                f"phrase word {word!r} must be (param, template) or (param, template, test)"
            )
        out.append(word)
    return tuple(out)


def _ports(ports: Iterable[Any], what: str) -> tuple[BuiltinPortSpec, ...]:
    """Ports as specs: each a :class:`BuiltinPortSpec` or ``(name[, type[, required]])``,
    names unique."""
    if isinstance(ports, (str, bytes)):
        raise ExtensionError(f"builtin {what} must be a list of ports, not {ports!r}")
    out: list[BuiltinPortSpec] = []
    for port in ports:
        if not isinstance(port, BuiltinPortSpec):
            parts = tuple(port) if isinstance(port, (list, tuple)) else (port,)
            if not 1 <= len(parts) <= 3:
                raise ExtensionError(f"builtin port {port!r} must be (name[, type[, required]])")
            port = BuiltinPortSpec(*parts)
        _name(port.name, f"builtin {what} port name")
        if port.type not in PORT_TYPES:
            raise ExtensionError(f"builtin port type {port.type!r} is not one of {PORT_TYPES}")
        if not isinstance(port.required, bool):
            raise ExtensionError(f"builtin port required must be a bool, not {port.required!r}")
        out.append(port)
    if len({p.name for p in out}) != len(out):
        raise ExtensionError(f"builtin {what} port names must be unique")
    return tuple(out)


def _roles(roles: Iterable[Any]) -> tuple[tuple[str, str, str], ...]:
    out = []
    for role in roles:
        method, template, name = tuple(role) if isinstance(role, (list, tuple)) else (role, 0, 0)
        if method not in _METHODS:
            raise ExtensionError(f"route method {method!r} is not one of {sorted(_METHODS)}")
        if not isinstance(template, str) or not _TEMPLATE_RE.match(template):
            raise ExtensionError(f"route path {template!r} must be an absolute path template")
        if name not in ROLES:
            raise ExtensionError(f"route role {name!r} is not one of {', '.join(ROLES)}")
        out.append((method, template, name))
    return tuple(out)


@functools.lru_cache(maxsize=256)
def _template_regex(template: str) -> re.Pattern[str]:
    """``/actors/{actor_id}/x`` as a whole-path pattern, one segment per placeholder."""
    parts = re.split(r"\{[A-Za-z_][A-Za-z0-9_]*\}", template)
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in parts) + "$")


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

    def builtin(
        self,
        name: str,
        factory: Factory,
        *,
        phrase: str = "",
        label: str = "",
        inputs: Iterable[Any] = (),
        outputs: Iterable[Any] = (),
    ) -> None:
        """Serve the built-in code step ``name`` with ``factory(store)``; ``phrase`` is what
        ``model.describe`` says for it (empty: ``builtin <name>``), ``label`` how a picker
        names it (empty: the phrase), and ``inputs``/``outputs`` the typed ports an editor
        pre-fills, each a :class:`BuiltinPortSpec` or ``(name[, type[, required]])`` (empty:
        none declared)."""
        if _name(name, "builtin name") in RESERVED_BUILTINS:
            raise ExtensionError(f"builtin {name!r} is reserved by core")
        for value, what in ((phrase, "phrase"), (label, "label")):
            if not isinstance(value, str):
                raise ExtensionError(f"builtin {what} must be a string, not {value!r}")
        entry = BuiltinEntry(
            name,
            factory,
            self.name,
            phrase,
            label,
            _ports(inputs, "inputs"),
            _ports(outputs, "outputs"),
        )
        self._registry._add("builtins", name, entry, "builtin")

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

    def api_routes(
        self,
        name: str,
        mount: Callable[[Any, ApiContext], None],
        *,
        roles: Iterable[tuple[str, str, str]] = (),
    ) -> None:
        """Add contract routes to the API: ``mount(app, ApiContext)``; ``roles`` declares the
        role each route needs, ``(method, path template, role)`` (see the module
        docstring)."""
        entry = ApiRoutesEntry(_name(name, "api routes name"), mount, self.name, _roles(roles))
        self._registry._add("api", name, entry, "api routes")

    def params_validator(
        self, kind: str, validate: Callable[[Mapping[str, Any], str], Iterable[Problem]]
    ) -> None:
        """Check ``kind``'s params at save: ``validate(params, path)`` returns its problems,
        ``(path, code, message)`` each (``path`` is the action's ``params`` path)."""
        entry = ParamsValidatorEntry(
            _name(kind, "kind"), _callable(validate, "params validator"), self.name
        )
        self._registry._add("validators", kind, entry, "params validator")

    def trigger_filter(
        self,
        namespace: str,
        *,
        match: Callable[[Mapping[str, Any], Mapping[str, Any], bool], bool],
        validate: Callable[[Mapping[str, Any], str], Iterable[Problem]] | None = None,
        describe: Callable[[Mapping[str, Any]], Iterable[str]] | None = None,
    ) -> None:
        """Filter event triggers on the types ``<namespace>.*`` (see the module docstring):
        ``match(params, data, own)`` (``own``: the event is the engine's own, admitted by
        ``include_self``), ``validate(params, path)`` and ``describe(params)``."""
        if not _NAMESPACE_RE.match(_name(namespace, "trigger filter namespace")):
            raise ExtensionError(f"{namespace!r} is not one lowercase event-type segment")
        entry = TriggerFilterEntry(
            namespace,
            _callable(match, "trigger filter match"),
            self.name,
            _optional_callable(validate, "trigger filter validate"),
            _optional_callable(describe, "trigger filter describe"),
        )
        self._registry._add("filters", namespace, entry, "trigger filter")

    def action_phrases(
        self,
        kind: str,
        *,
        words: Iterable[tuple[Any, ...]] = (),
        after: Callable[[Mapping[str, Any]], Iterable[str]] | None = None,
    ) -> None:
        """What ``model.describe`` says for ``kind`` after its name and actor: ``words``, the
        literal params worth a word as ``(param, template)`` (a literal value fills the
        template's ``{}``) or ``(param, template, test)`` (the template as is, when
        ``test(value)`` holds); then ``after(params)``, words after the placement."""
        entry = ActionPhrasesEntry(
            _name(kind, "kind"), _words(words), self.name, _optional_callable(after, "after")
        )
        self._registry._add("phrases", kind, entry, "action phrases")

    def health_section(
        self,
        name: str,
        provider: Callable[[Any, Any], Any],
        *,
        empty: Callable[[], Any] = _none,
    ) -> None:
        """Add ``name`` to the node's health document: ``provider(store, now)``, JSON;
        ``empty()`` when the store is unreachable."""
        if _name(name, "health section") in RESERVED_HEALTH_SECTIONS:
            raise ExtensionError(f"health section {name!r} is core's")
        entry = HealthSectionEntry(
            name, _callable(provider, "health provider"), self.name, _callable(empty, "empty")
        )
        self._registry._add("health", name, entry, "health section")

    def event_reservation(
        self, name: str, check: Callable[[Mapping[str, Any]], str | None]
    ) -> None:
        """Refuse envelopes from the bus and webhooks: ``check(envelope)`` returns why (a
        type or id prefix only the engine writes), or ``None``. Its fields ``id``, ``type``,
        ``kind`` and ``source`` are non-empty strings or absent when it is called."""
        entry = EventReservationEntry(
            _name(name, "reservation name"), _callable(check, "reservation check"), self.name
        )
        self._registry._add("reservations", name, entry, "event reservation")


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
                "api",
                "validators",
                "filters",
                "phrases",
                "health",
                "reservations",
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
            # refilled in place, never rebound: a view from action_kinds() stays live
            kept = {a: k for a, k in self._aliases.items() if k in self._tables["kinds"]}
            self._aliases.clear()
            self._aliases.update(kept)
            self._catalogue.clear()
            self._catalogue.update({k: e.kind for k, e in self._tables["kinds"].items()})

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
            self._aliases.update(dict.fromkeys(entry.aliases, name))
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

    def api_routes(self) -> tuple[ApiRoutesEntry, ...]:
        return tuple(self._tables["api"].values())

    def params_validator(
        self, kind: str
    ) -> Callable[[Mapping[str, Any], str], Iterable[Problem]] | None:
        """``kind``'s params validator (aliases resolved), or None."""
        entry = self._tables["validators"].get(self._aliases.get(kind, kind))
        return entry.validate if entry else None

    def trigger_filter(self, event_type: Any) -> TriggerFilterEntry | None:
        """The filter for ``event_type``'s namespace (``discord.x`` -> ``discord``), or None:
        one dict lookup, the engine's per-event path."""
        if not isinstance(event_type, str):
            return None
        head, dot, _ = event_type.partition(".")
        return self._tables["filters"].get(head) if dot else None

    def trigger_filters(self) -> tuple[TriggerFilterEntry, ...]:
        return tuple(self._tables["filters"].values())

    def action_phrases(self, kind: str) -> ActionPhrasesEntry | None:
        return self._tables["phrases"].get(kind)

    def action_phrase_kinds(self) -> tuple[str, ...]:
        return tuple(self._tables["phrases"])

    def builtin_phrases(self) -> dict[str, str]:
        """``name -> phrase`` of the built-ins registered with one."""
        return {b.name: b.phrase for b in tuple(self._tables["builtins"].values()) if b.phrase}

    def health_sections(self) -> tuple[HealthSectionEntry, ...]:
        return tuple(self._tables["health"].values())

    def event_reservations(self) -> tuple[EventReservationEntry, ...]:
        return tuple(self._tables["reservations"].values())

    def route_role(self, method: str, path: str) -> str | None:
        """The role an extension declared for ``method path`` (a concrete path), or None."""
        method = method.upper()
        for entry in tuple(self._tables["api"].values()):
            for m, template, role in entry.roles:
                if m == method and _template_regex(template).match(path):
                    return role
        return None

    def node_collections(self) -> tuple[str, ...]:
        """Every extension's collections (each provider is called now), first seen first."""
        names: dict[str, None] = {}
        for entry in tuple(self._tables["collections"].values()):
            names.update(dict.fromkeys(entry.provider()))
        return tuple(names)
