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
   repaired. So is every URL whose host ``urllib.parse`` might read differently from a
   browser: one holding a backslash anywhere (a browser reads it as ``/``), userinfo (an ``@``
   in the authority), a ``%`` in the authority (a browser decodes it), any non-ASCII character in
   the authority (a browser maps it through IDNA), a port that is not a number, or a host that is
   not plain ASCII letters, digits, ``-``, ``_`` and ``.`` (or a bracketed IPv6 address). An
   internationalised host is kept only in its ``xn--`` form. The host (lower-cased) is written to
   the field named by ``host_into`` on the copy of the item that ``where`` sees, so
   ``host_suffix`` can test it; the kept items are the originals, unchanged. ``host_into`` needs
   ``host_from``.
2. ``dedupe_by`` - a dotted path to a scalar (string, int or float) in the item. The first item
   holding a value stays, later ones with the same value are dropped. The value is compared
   exactly (a string also casefolded when ``dedupe_casefold`` is true); nothing else is
   normalised. An item without such a value cannot be compared and is kept. A value is
   *remembered when first seen*, before ``where`` runs, so a first item that ``where`` rejects
   still shadows its later duplicates.
3. ``where`` - a condition node (:mod:`culture_rules.model.condition`), validated once per run
   (:func:`~culture_rules.model.condition.prepare`) and evaluated per item under ONE
   evaluation budget shared by every item of the run (running out fails the step
   ``bad_input``, never hangs): the item (plus its derived host field) is the
   ``{"item": path}`` operand; it is also ``trigger``, so ``{"field": path}`` reaches the outer
   item inside an ``any``/``all`` (whose own items shadow ``item``); the context values
   are ``variables``. An item the condition does not hold for is dropped; no ``where`` keeps all.
4. ``limit`` - an integer of 0 or more, from the input ``limit`` (a reference) or the config:
   at most that many items are kept, the first in order.

Outputs: ``items`` (the kept originals, in input order), ``count``, and the counts of items
dropped by each rule - ``dropped_host``, ``dropped_duplicate``, ``dropped_where``,
``dropped_limit`` - never the items. Once ``limit`` items are kept the rest are not examined
(the work stops short) and all count as ``dropped_limit``. Final (non-retryable) failures:
``bad_params`` (a bad config value, an invalid ``where`` tree) and ``bad_input`` (``items`` not a
list or too long, ``limit`` not an integer of 0 or more, the ``where`` evaluations over the run's
items taking more than :data:`~culture_rules.model.condition.MAX_EVAL_STEPS` steps). Standard
library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.model.action_step import ENGINE_CONFIG_KEYS
from culture_rules.model.condition import (
    MISSING,
    Budget,
    ConditionError,
    EvaluationBudgetError,
    PreparedCondition,
    prepare,
    walk_path,
)

__all__ = ["LIST_FILTER_BUILTIN", "ListFilterPort", "MAX_LIST_ITEMS", "list_filter"]

LIST_FILTER_BUILTIN = "list.filter"
#: The longest list the step takes (bounds the work of one run).
MAX_LIST_ITEMS = 1_000
_SCHEMES = ("http", "https")
#: Control characters, lone surrogates and non-characters (whitespace is ``_SPACE``'s).
_CONTROL = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff\ufffe\uffff]")
_SPACE = re.compile(r"\s")
#: A host a browser opens as written: ASCII letters, digits, ``-``, ``_``, ``.`` only.
_PLAIN_HOST = re.compile(r"[a-z0-9_.-]+", re.ASCII)
#: An IPv6 address as ``urlsplit`` reports it (brackets removed).
_IPV6_HOST = re.compile(r"[0-9a-f:.]*:[0-9a-f:.]*", re.ASCII)
#: Authority characters on which ``urlsplit`` and a browser may disagree about the host.
_AMBIGUOUS_AUTHORITY = ("@", "%")
_SCALARS = (str, int, float)
_CONFIG_KEYS = (
    "items",
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
    """The lower-cased host of an ``http``/``https`` URL, else None (module doc, rule 1):
    only when ``urlsplit``'s host is the host a browser would open."""
    if not isinstance(url, str) or not url or "\\" in url:
        return None
    if _CONTROL.search(url) or _SPACE.search(url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - a port that is not a number raises: refused
    except ValueError:
        return None
    if parts.scheme not in _SCHEMES or not host or not parts.netloc.isascii():
        return None
    if any(mark in parts.netloc for mark in _AMBIGUOUS_AUTHORITY):
        return None
    if _PLAIN_HOST.fullmatch(host) or _IPV6_HOST.fullmatch(host):
        return host
    return None


class _Rules:
    """The validated config of one run."""

    def __init__(self, input: Mapping[str, Any], config: Mapping[str, Any]) -> None:
        unknown = sorted(str(k) for k in config if k not in _CONFIG_KEYS + ENGINE_CONFIG_KEYS)
        if unknown:
            raise _Refused("bad_params", f"unknown config key {unknown[0][:40]!r}")
        self.where: PreparedCondition | None = None
        if config.get("where") is not None:
            try:
                self.where = prepare(config["where"])
            except ConditionError as exc:
                raise _Refused("bad_params", f"where: {exc.path}: {exc.message}") from None
        self.budget = Budget()  # one for every item of the run
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
        return _http_host(walk_path(item, self.host_from) if self.host_from else None)

    def seen_key(self, item: Any) -> Any:
        """The value ``dedupe_by`` compares, or ``MISSING`` when the item has none."""
        value = walk_path(item, self.dedupe_by)
        if not isinstance(value, _SCALARS) or isinstance(value, bool):
            return MISSING
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
        try:
            return self.where.evaluate_item(seen, context, self.budget)
        except EvaluationBudgetError as exc:
            raise _Refused("bad_input", f"where: {exc.message}") from None


def _items(input: Mapping[str, Any], config: Mapping[str, Any]) -> list[Any]:
    items = _param(input, config, "items")
    if not isinstance(items, list):
        raise _Refused("bad_input", "items must be a list")
    if len(items) > MAX_LIST_ITEMS:
        raise _Refused("bad_input", f"items: at most {MAX_LIST_ITEMS} members")
    return items


class _Pass:
    """One pass over the items: what is kept, what each rule dropped, the dedupe keys."""

    def __init__(self, rules: _Rules) -> None:
        self.rules = rules
        self.kept: list[Any] = []
        self.dropped = {"host": 0, "duplicate": 0, "where": 0, "limit": 0}
        self.seen: set[Any] = set()

    def drop_reason(self, item: Any) -> str | None:
        """The rule that drops ``item`` (module doc order), or None to keep it."""
        rules = self.rules
        if rules.limit is not None and len(self.kept) >= rules.limit:
            return "limit"
        host = rules.host(item)
        if rules.host_from and host is None:
            return "host"
        if rules.dedupe_by and self._duplicate(rules.seen_key(item)):
            return "duplicate"
        return None if rules.holds(item, host) else "where"

    def _duplicate(self, key: Any) -> bool:
        if key is MISSING:
            return False
        if key in self.seen:
            return True
        self.seen.add(key)  # remembered when first seen, before where runs
        return False

    def run(self, items: list[Any]) -> dict[str, Any]:
        for item in items:
            reason = self.drop_reason(item)
            if reason is None:
                self.kept.append(item)
            else:
                self.dropped[reason] += 1
        out: dict[str, Any] = {"items": self.kept, "count": len(self.kept)}
        out.update({f"dropped_{rule}": number for rule, number in self.dropped.items()})
        return out


def list_filter(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The kept items and the drop counts (module doc), else :class:`_Refused`."""
    rules = _Rules(input, config)
    return _Pass(rules).run(_items(input, config))


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
