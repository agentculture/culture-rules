"""Generic builtin ``list.filter``: keep the items of a list that pass configured rules.

A pure step (no store, no network). Every rule comes from step config; nothing is built in.

Inputs (each may instead sit in ``config``; the input wins): ``items`` - a list of at most
:data:`MAX_LIST_ITEMS` members (anything else is ``bad_input``); ``limit`` - see below. Every
other input is a *context value* the ``where`` condition reads as ``{"var": "<input name>"}``.

Config, applied in this fixed order to each item in input order:

1. ``host_from`` - a dotted path to a URL string in the item (``"."`` is the item itself). The
   item is dropped unless the value is an ``http`` or ``https`` URL with a host: a value that is
   not a string, is empty, holds whitespace or a control character, does not parse, uses another
   scheme (``ftp:``, ``mailto:``, ``javascript:``, ...) or has no host is dropped, never
   repaired. The host (lower-cased, as ``urllib.parse`` reports it) is written to the field named
   by ``host_into`` on the copy of the item that ``where`` sees, so ``host_suffix`` can test it;
   the kept items are the originals, unchanged. ``host_into`` needs ``host_from``.
2. ``dedupe_by`` - a dotted path to a scalar (string, int or float) in the item. The first item
   holding a value stays, later ones with the same value are dropped. The value is compared
   exactly (a string also casefolded when ``dedupe_casefold`` is true); nothing else is
   normalised. An item without such a value cannot be compared and is kept. A value is
   *remembered when first seen*, before ``where`` runs, so a first item that ``where`` rejects
   still shadows its later duplicates.
3. ``where`` - a condition node (:mod:`culture_rules.model.condition`) evaluated per item with
   :func:`~culture_rules.model.condition.evaluate_item`: the item (plus its derived host field)
   is the ``{"item": path}`` operand; it is also ``trigger``, so ``{"field": path}`` reaches the
   outer item inside an ``any``/``all`` (whose own items shadow ``item``); the context values
   are ``variables``. An item the condition does not hold for is dropped; no ``where`` keeps all.
4. ``limit`` - an integer of 0 or more, from the input ``limit`` (a reference) or the config:
   at most that many items are kept, the first in order.

Outputs: ``items`` (the kept originals, in input order), ``count``, and the counts of items
dropped by each rule - ``dropped_host``, ``dropped_duplicate``, ``dropped_where``,
``dropped_limit`` - never the items. Once ``limit`` items are kept the rest are not examined
(the work stops short) and all count as ``dropped_limit``. Final (non-retryable) failures:
``bad_params`` (a bad config value, an invalid ``where`` tree) and ``bad_input`` (``items`` not a
list or too long, ``limit`` not an integer of 0 or more). Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.model.condition import _MISSING, ConditionError, _walk, evaluate_item, validate

__all__ = ["LIST_FILTER_BUILTIN", "ListFilterPort", "MAX_LIST_ITEMS", "list_filter"]

LIST_FILTER_BUILTIN = "list.filter"
#: The longest list the step takes (bounds the work of one run).
MAX_LIST_ITEMS = 1_000
_SCHEMES = ("http", "https")
_UNSAFE_URL = re.compile(r"[\s\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\ufffe\uffff]")
_SCALARS = (str, int, float)
_CONFIG_KEYS = (
    "where",
    "dedupe_by",
    "dedupe_casefold",
    "host_from",
    "host_into",
    "limit",
)


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _path(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise _Refused("bad_params", f"{key} must be a non-empty string")
    return value


def _field_name(config: Mapping[str, Any], key: str) -> str | None:
    name = _path(config, key)
    if name is not None and "." in name:
        raise _Refused("bad_params", f"{key} must be one field name, without dots")
    return name


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _http_host(url: Any) -> str | None:
    """The lower-cased host of an ``http``/``https`` URL, else None (module doc, rule 1)."""
    if not isinstance(url, str) or not url or _UNSAFE_URL.search(url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    return host if parts.scheme in _SCHEMES and host else None


class _Rules:
    """The validated config of one run."""

    def __init__(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> None:
        unknown = sorted(str(key) for key in config if key not in _CONFIG_KEYS + ("builtin",))
        if unknown:
            raise _Refused("bad_params", f"unknown config key {unknown[0][:40]!r}")
        self.where = config.get("where")
        if self.where is not None:
            try:
                validate(self.where)
            except ConditionError as exc:
                raise _Refused("bad_params", f"where: {exc.path}: {exc.message}") from None
        self.dedupe_by = _path(config, "dedupe_by")
        self.casefold = config.get("dedupe_casefold", False)
        if not isinstance(self.casefold, bool):
            raise _Refused("bad_params", "dedupe_casefold must be true or false")
        self.host_from = _path(config, "host_from")
        self.host_into = _field_name(config, "host_into")
        if self.host_into and not self.host_from:
            raise _Refused("bad_params", "host_into needs host_from")
        self.limit = self._limit(input, config)
        self.variables = {key: value for key, value in input.items() if key != "items"}

    @staticmethod
    def _limit(input: Mapping[str, Any], config: Mapping[str, Any]) -> int | None:
        if input.get("limit") is None and config.get("limit") is not None:
            if not _is_int(config["limit"]) or config["limit"] < 0:
                raise _Refused("bad_params", "limit must be an integer of 0 or more")
        limit = _param(input, config, "limit")
        if limit is not None and (not _is_int(limit) or limit < 0):
            raise _Refused("bad_input", "limit must be an integer of 0 or more")
        return limit

    def host(self, item: Any) -> str | None:
        return _http_host(_walk(item, self.host_from) if self.host_from else None)

    def seen_key(self, item: Any) -> Any:
        """The value ``dedupe_by`` compares, or ``_MISSING`` when the item has none."""
        value = _walk(item, self.dedupe_by)
        if not isinstance(value, _SCALARS) or isinstance(value, bool):
            return _MISSING
        return value.casefold() if self.casefold and isinstance(value, str) else value

    def holds(self, item: Any, host: str | None) -> bool:
        if self.where is None:
            return True
        seen = item
        if self.host_into and isinstance(item, Mapping):
            seen = {**item, self.host_into: host}
        context = {
            "trigger": seen if isinstance(seen, Mapping) else {},
            "variables": self.variables,
        }
        return evaluate_item(self.where, seen, context)


def list_filter(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The kept items and the drop counts (module doc), else :class:`_Refused`."""
    rules = _Rules(input, config)
    items = _param(input, config, "items")
    if not isinstance(items, list):
        raise _Refused("bad_input", "items must be a list")
    if len(items) > MAX_LIST_ITEMS:
        raise _Refused("bad_input", f"items: at most {MAX_LIST_ITEMS} members")
    kept: list[Any] = []
    dropped = {"host": 0, "duplicate": 0, "where": 0, "limit": 0}
    seen: set[Any] = set()
    for item in items:
        if rules.limit is not None and len(kept) >= rules.limit:
            dropped["limit"] += 1
            continue
        host = rules.host(item)
        if rules.host_from and host is None:
            dropped["host"] += 1
            continue
        if rules.dedupe_by:
            key = rules.seen_key(item)
            if key is not _MISSING and key in seen:
                dropped["duplicate"] += 1
                continue
            if key is not _MISSING:
                seen.add(key)
        if not rules.holds(item, host):
            dropped["where"] += 1
            continue
        kept.append(item)
    out: dict[str, Any] = {"items": kept, "count": len(kept)}
    out.update({f"dropped_{rule}": number for rule, number in dropped.items()})
    return out


class ListFilterPort:
    """Built-in ``list.filter`` (see the module docstring). Pure; touches nothing."""

    supports_idempotency_key = True

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        try:
            return InvocationResult.completed(list_filter(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
