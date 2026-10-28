"""Built-ins ``keyset.filter`` and ``keyset.add``: remember keys of items for a window.

Generic ports over :mod:`culture_rules.keysets`. Every rule comes from the step: nothing here
knows a flow, a URL parameter or a default window.

Parameters (each may sit in ``config`` or in the input; the input wins): ``set`` (the set
name), ``key_field`` (the item field holding the key), ``normalise`` (a mapping for
:func:`culture_rules.keysets.normalise`, ``{}`` for none) and ``window_days`` (a positive
integer); ``keyset.add`` also takes ``max_keys`` (a positive integer). A missing or bad value
fails ``bad_params`` (final).

Input ``items``: a list of mappings, or a list of lists of mappings one level deep (what a
``for_each`` loop hands on through a per-port output; a ``None`` entry is an iteration that
output nothing and holds no items). With the optional parameter ``items_field`` (a non-empty
string), ``items`` is instead a list of output objects, one per iteration (what a ``for_each``
loop's ``results`` port hands on): each entry's ``items_field`` list holds that iteration's
items, and an entry without it (``{}``, a skipped iteration) or ``None`` holds none. An item
with no key field, or an empty or non-string one, is *keyless*.

``keyset.filter`` returns ``items`` in the same shape and order (an output object keeps its
other fields, ``None`` stays ``None``) without those whose
normalised key is in the set within its window; keyless items are kept. Outputs ``items``,
``kept``, ``dropped`` and ``keyless``; it writes nothing and never outputs a stored key.

``keyset.add`` stores every item's normalised key until now plus ``window_days``. More than
``max_keys`` keys fails ``bad_params`` and writes nothing. Outputs ``added`` and
``already_present``; a retried step adds nothing twice. Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules import keysets
from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = [
    "KEYSET_ADD_BUILTIN",
    "KEYSET_FILTER_BUILTIN",
    "KeysetAddPort",
    "KeysetFilterPort",
]

KEYSET_FILTER_BUILTIN = "keyset.filter"
KEYSET_ADD_BUILTIN = "keyset.add"
_DAY = 86400


class _Refused(Exception):
    pass


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _Refused(f"bad_params: {name} must be a positive integer")
    return value


class _Params:
    def __init__(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> None:  # noqa: A002
        def get(name: str) -> Any:
            return input[name] if name in input else config.get(name)

        self.get = get
        try:
            self.set_name = keysets.check_set_name(get("set"))
        except keysets.KeysetError as exc:
            raise _Refused(f"bad_params: {exc}") from None
        field = get("key_field")
        if not isinstance(field, str) or not field:
            raise _Refused("bad_params: key_field must be a non-empty string")
        self.key_field = field
        self.rules = get("normalise")
        if not isinstance(self.rules, Mapping):
            raise _Refused("bad_params: normalise must be a mapping")
        self.window_s = _positive_int(get("window_days"), "window_days") * _DAY
        items_field = get("items_field")
        if items_field is not None and (not isinstance(items_field, str) or not items_field):
            raise _Refused("bad_params: items_field must be a non-empty string")
        self.items_field: str | None = items_field


_SHAPE = "bad_params: items must be a list of mappings or a list of such lists"


def _group_of(entry: Any, items_field: str | None) -> list[Any]:
    """The items one loop iteration's entry holds (``None``: an iteration with none)."""
    if entry is None:
        return []
    if items_field is None:
        group = entry
    elif isinstance(entry, Mapping):
        group = entry.get(items_field, [])
    else:
        raise _Refused(f"bad_params: items must be a list of objects with {items_field!r}")
    if not isinstance(group, list) or not all(isinstance(i, Mapping) for i in group):
        raise _Refused(_SHAPE)
    return group


def _shape(items: Any, items_field: str | None = None) -> tuple[list[Any] | None, list[list]]:
    """``(entries, groups)``: ``entries`` is None for a flat list (one group), else the
    per-iteration entries as given, which :func:`_reshape` rebuilds around the groups."""
    if not isinstance(items, list):
        raise _Refused("bad_params: items must be a list")
    nested = items_field is not None or any(i is None or isinstance(i, list) for i in items)
    if not nested:
        return None, [_group_of(items, None)]
    return items, [_group_of(entry, items_field) for entry in items]


def _reshape(entries: list[Any], groups: list[list], items_field: str | None) -> list[Any]:
    """The entries with each iteration's items replaced by ``groups`` (same order)."""
    out: list[Any] = []
    for entry, group in zip(entries, groups):
        if entry is None or (items_field is not None and items_field not in entry):
            out.append(entry)
        elif items_field is None:
            out.append(group)
        else:
            out.append({**entry, items_field: group})
    return out


def _key_of(item: Mapping[str, Any], params: _Params) -> str | None:
    raw = item.get(params.key_field)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return keysets.check_key(keysets.normalise(raw, params.rules))
    except keysets.KeysetError as exc:
        raise _Refused(f"bad_params: {exc}") from None


def _looks_like_loop_output(groups: list[list[Any]], keys: list[str | None]) -> bool:
    """Whether a keyless item holds a list-valued field, as a loop's output object does."""
    flat = [i for group in groups for i in group]
    return any(
        k is None and any(isinstance(v, list) for v in i.values()) for i, k in zip(flat, keys)
    )


class _KeysetPort:
    supports_idempotency_key = True  # keys are written once, so a retry is a no-op

    def __init__(self, store: Any, clock: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._indexed = False

    def _run(
        self, params: _Params, entries: list[Any] | None, groups: list[list[Any]]
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _params(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> _Params:  # noqa: A002
        return _Params(input, config)

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        try:
            params = self._params(input, config)
            entries, groups = _shape(input.get("items"), params.items_field)
            if not self._indexed:
                keysets.ensure_ttl_index(self._store)
                self._indexed = True
            out = self._run(params, entries, groups)
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
        except keysets.KeysetError as exc:
            return InvocationResult.failed(f"bad_params: {exc}", retryable=False)
        return InvocationResult.completed(out)


class KeysetFilterPort(_KeysetPort):
    """Built-in ``keyset.filter`` (see the module docstring)."""

    def _run(
        self, params: _Params, entries: list[Any] | None, groups: list[list[Any]]
    ) -> dict[str, Any]:
        now = self._clock()
        kept = dropped = keyless = 0
        result = []
        for group in groups:
            keys = [_key_of(i, params) for i in group]
            present = set(keysets.seen(self._store, params.set_name, [k for k in keys if k], now))
            survivors = [i for i, k in zip(group, keys) if k not in present]
            keyless += keys.count(None)
            dropped += len(group) - len(survivors)
            kept += len(survivors)
            result.append(survivors)
        items = result[0] if entries is None else _reshape(entries, result, params.items_field)
        return {"items": items, "kept": kept, "dropped": dropped, "keyless": keyless}


class KeysetAddPort(_KeysetPort):
    """Built-in ``keyset.add`` (see the module docstring)."""

    def _params(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> _Params:  # noqa: A002
        params = super()._params(input, config)
        params.max_keys = _positive_int(params.get("max_keys"), "max_keys")
        return params

    def _run(
        self, params: _Params, entries: list[Any] | None, groups: list[list[Any]]
    ) -> dict[str, Any]:
        found = [_key_of(i, params) for group in groups for i in group]
        keys = [k for k in found if k]
        if _looks_like_loop_output(groups, found):  # d4: likely a missing items_field
            raise _Refused(
                f"bad_params: an item has no {params.key_field!r} key but holds a list "
                "(it looks like a loop output object: set items_field)"
            )
        if len(keys) > params.max_keys:
            raise _Refused(f"bad_params: {len(keys)} keys exceed max_keys {params.max_keys}")
        out = keysets.add_keys(self._store, params.set_name, keys, self._clock(), params.window_s)
        return {**out, "keyless": len(found) - len(keys)}
