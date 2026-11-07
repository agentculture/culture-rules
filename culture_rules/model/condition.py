"""Condition predicate tree, deterministic evaluator and CEL-style text view.

The *tree* is the stored form: a plain JSON-compatible structure of dicts and lists.
The text form (``to_text`` / ``from_text``) is an advanced, secondary view over the same
tree.  Nothing here ever executes user-supplied Python; the evaluator is a small recursive
interpreter over the tree.

Tree shape (every node is a dict with an ``op`` key)::

    {"op": "compare", "cmp": "==|!=|<|<=|>|>=", "left": operand, "right": operand}
    {"op": "and" | "or", "args": [node, ...]}          # at least one arg
    {"op": "not", "arg": node}
    {"op": "exists", "arg": operand}
    {"op": "in", "value": operand, "items": operand}   # items resolves to a list
    {"op": "matches", "value": operand, "pattern": "<regex>"}  # re.fullmatch, length-capped
    {"op": "contains", "value": operand, "needle": operand}
    {"op": "host_suffix", "value": operand, "suffix": operand}
    {"op": "any" | "all", "items": operand, "where": node}      # per-item sub-condition

``contains`` is case-insensitive (``casefold``). With a string ``value`` and string ``needle``
it is a substring test; with a list ``value`` it is membership (strings compare casefolded,
anything else by ``==``). An empty needle, a missing operand or any other type is false.

``host_suffix`` takes a bare host (not a URL) as ``value`` and a domain as ``suffix``: true when
the host equals the suffix or ends with ``"." + suffix``, case-insensitively and ignoring one
trailing dot on either side and a leading dot on the suffix. It is not a plain ``endswith``:
``evilexample.com`` does not match ``example.com``. Non-strings and an empty suffix are false.

``any`` / ``all`` run ``where`` once per element of the list ``items`` resolves to. Inside
``where`` the current element is the operand ``{"item": "<path>"}`` (a dotted path into the
element, or ``"."`` for the element itself); an inner ``any``/``all`` shadows the outer item.
``all`` over an empty list is true and ``any`` is false; a missing or non-list ``items`` makes
both false, as does a list longer than ``MAX_ITEMS``. Item operands outside ``any``/``all``
resolve to missing, unless the caller supplies an item with :func:`evaluate_item`. ``any`` and
``all`` nest at most ``MAX_ITEM_NESTING`` deep. These ops have no text form.

A ``matches`` pattern must also pass :func:`culture_rules.model.regex_safety.unsafe_reason`
(no nested quantifiers, ambiguous alternations, backreferences, or more than one pair of
quantifiers that can trade the same text), since ``re`` has no time bound and the input is
external event data. :func:`evaluate` validates first, so a stored pattern that fails the
screen raises :class:`UnsafePatternError` instead of running; the engine records that as a
non-match.

Every comparison with a missing operand is false, ``!=`` included (as in CEL); explicit
negation still applies, so ``!(a == b)`` on a missing field is true.

Operands::

    {"field": "a.b"}    # dotted path into context["trigger"]
    {"var": "name"}     # context["variables"][name]
    {"literal": <json>} # a JSON scalar or list
    {"item": "a.b"}     # dotted path into the current item (any/all, evaluate_item)

Text form (CEL-style subset)::

    trigger.pr.state == "open" && !(vars.limit < 3) || "bug" in trigger.labels
    exists(trigger.a.b)    matches(trigger.title, "fix: .*")

Precedence: ``!`` > comparison/``in`` > ``&&`` > ``||``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from culture_rules.model.regex_safety import unsafe_reason

MAX_PATTERN_LEN = 256
#: Longest string a ``matches`` runs on; a longer value is a non-match (bounds regex work).
MAX_INPUT_LEN = 2_000
MAX_DEPTH = 64
#: Longest list an ``any`` / ``all`` walks; a longer list makes the op false (bounds work).
MAX_ITEMS = 1_000
#: Deepest nesting of ``any`` / ``all`` (work is at most ``MAX_ITEMS ** MAX_ITEM_NESTING``).
MAX_ITEM_NESTING = 2

CMP_OPS = ("==", "!=", "<=", ">=", "<", ">")
_IDENT = re.compile(r"[A-Za-z_]\w*\Z", re.ASCII)


class ConditionError(ValueError):
    """Structured error for an invalid tree or an unevaluable condition."""

    code = "invalid_condition"

    def __init__(self, message: str, path: str = "$") -> None:
        super().__init__(message)
        self.message = message
        self.path = path

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "path": self.path}


class ConditionParseError(ConditionError):
    """Structured error for malformed condition text."""

    code = "parse_error"

    def __init__(self, message: str, position: int = 0) -> None:
        super().__init__(message, path="$")
        self.position = position

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "position": self.position}


class UnsafePatternError(ConditionError):
    """A ``matches`` pattern whose shape can backtrack catastrophically."""

    code = "unsafe_pattern"


@dataclass(frozen=True)
class ParseResult:
    tree: dict | None
    error: ConditionParseError | None


_MISSING = object()


# --------------------------------------------------------------------------- validation


def _err(msg: str, path: str) -> ConditionError:
    return ConditionError(msg, path)


def _check_operand(o: Any, path: str) -> None:
    if not isinstance(o, dict) or len(o) != 1:
        raise _err("operand must be one of field/var/literal/item", path)
    ((k, v),) = o.items()
    if k in ("field", "var", "item"):
        if not isinstance(v, str) or not v:
            raise _err(f"{k} must be a non-empty string", path)
    elif k == "literal":
        if not _is_json(v):
            raise _err("literal must be JSON-compatible", path)
    else:
        raise _err(f"unknown operand kind {k!r}", path)


def _is_json(v: Any) -> bool:
    if v is None or isinstance(v, (bool, int, float, str)):
        return True
    if isinstance(v, list):
        return all(_is_json(x) for x in v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and _is_json(x) for k, x in v.items())
    return False


def validate(node: Any, path: str = "$", depth: int = 0, nesting: int = 0) -> None:
    """Raise ConditionError if ``node`` is not a well-formed predicate tree."""
    if depth > MAX_DEPTH:
        raise _err("condition nested too deeply", path)
    if not isinstance(node, dict):
        raise _err("node must be an object", path)
    op = node.get("op")
    if op == "compare":
        if node.get("cmp") not in CMP_OPS:
            raise _err(f"cmp must be one of {CMP_OPS}", path)
        _check_operand(node.get("left"), path + ".left")
        _check_operand(node.get("right"), path + ".right")
    elif op in ("and", "or"):
        _validate_group(node, op, path, depth, nesting)
    elif op == "not":
        if "arg" not in node:
            raise _err("not needs arg", path)
        validate(node["arg"], path + ".arg", depth + 1, nesting)
    elif op in _OPERAND_FIELDS:
        for key in _OPERAND_FIELDS[op]:
            _check_operand(node.get(key), f"{path}.{key}")
    elif op == "matches":
        _validate_matches(node, path)
    elif op in ("any", "all"):
        _validate_per_item(node, op, path, depth, nesting)
    else:
        raise _err(f"unknown op {op!r}", path)


#: Ops whose whole shape is a fixed set of operand keys.
_OPERAND_FIELDS = {
    "exists": ("arg",),
    "in": ("value", "items"),
    "contains": ("value", "needle"),
    "host_suffix": ("value", "suffix"),
}


def _validate_per_item(node: dict, op: str, path: str, depth: int, nesting: int) -> None:
    if nesting >= MAX_ITEM_NESTING:
        raise _err(f"{op} nested more than {MAX_ITEM_NESTING} deep", path)
    _check_operand(node.get("items"), path + ".items")
    if "where" not in node:
        raise _err(f"{op} needs where", path)
    validate(node["where"], path + ".where", depth + 1, nesting + 1)


def _validate_group(node: dict, op: str, path: str, depth: int, nesting: int = 0) -> None:
    args = node.get("args")
    # At least two args: a single-argument group has no text form of its own, so it
    # would not round-trip through to_text/from_text (Qwen review of t2). Editors unwrap
    # a one-item group before saving.
    if not isinstance(args, list) or len(args) < 2:
        raise _err(f"{op} needs an args list of at least two conditions", path)
    for i, a in enumerate(args):
        validate(a, f"{path}.args[{i}]", depth + 1, nesting)


def _validate_matches(node: dict, path: str) -> None:
    _check_operand(node.get("value"), path + ".value")
    pat = node.get("pattern")
    if not isinstance(pat, str):
        raise _err("pattern must be a string", path)
    if len(pat) > MAX_PATTERN_LEN:
        raise _err(f"pattern longer than {MAX_PATTERN_LEN} characters", path)
    try:
        re.compile(pat)
    except re.error as exc:
        raise _err(f"invalid pattern: {exc}", path) from exc
    reason = unsafe_reason(pat)
    if reason is not None:
        raise UnsafePatternError(f"pattern may backtrack catastrophically: {reason}", path)


# --------------------------------------------------------------------------- evaluation


def _walk(cur: Any, path: str) -> Any:
    """Follow the dotted ``path`` through nested dicts; ``"."`` is ``cur`` itself."""
    if path == ".":
        return cur
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _resolve(o: dict, ctx: dict) -> Any:
    if "literal" in o:
        return o["literal"]
    if "var" in o:
        cur = ctx.get("variables") or {}
        return cur.get(o["var"], _MISSING) if isinstance(cur, dict) else _MISSING
    if "item" in o:
        return _walk(ctx["item"], o["item"]) if "item" in ctx else _MISSING
    return _walk(ctx.get("trigger") or {}, o["field"])


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _eq(a: Any, b: Any) -> bool:
    if a is _MISSING or b is _MISSING:
        return False
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return bool(a == b)


def _compare(op: str, a: Any, b: Any) -> bool:
    if a is _MISSING or b is _MISSING:
        return False  # every comparison, `!=` included; `!(a == b)` stays true
    if op == "==":
        return _eq(a, b)
    if op == "!=":
        return not _eq(a, b)
    if not ((_is_num(a) and _is_num(b)) or (isinstance(a, str) and isinstance(b, str))):
        return False
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _eval_in(node: dict, ctx: dict) -> bool:
    value, items = _resolve(node["value"], ctx), _resolve(node["items"], ctx)
    if value is _MISSING or not isinstance(items, list):
        return False
    return any(_eq(value, i) for i in items)


def _eval_matches(node: dict, ctx: dict) -> bool:
    value = _resolve(node["value"], ctx)
    if not isinstance(value, str) or len(value) > MAX_INPUT_LEN:
        return False
    return re.fullmatch(node["pattern"], value) is not None


def _eval_contains(node: dict, ctx: dict) -> bool:
    value, needle = _resolve(node["value"], ctx), _resolve(node["needle"], ctx)
    if not isinstance(needle, str) or not needle:
        return False
    folded = needle.casefold()
    if isinstance(value, str):
        return folded in value.casefold()
    if isinstance(value, list):
        return any(isinstance(item, str) and item.casefold() == folded for item in value)
    return False


def _norm_host(text: str) -> str:
    text = text.strip().casefold()
    return text[:-1] if text.endswith(".") else text


def host_matches_suffix(host: Any, suffix: Any) -> bool:
    """True when ``host`` equals ``suffix`` or ends with ``"." + suffix`` (case-insensitive).

    One trailing dot is ignored on both, a leading dot on the suffix too; non-strings and an
    empty suffix are false. ``evilexample.com`` does not match ``example.com``.
    """
    if not isinstance(host, str) or not isinstance(suffix, str):
        return False
    host, suffix = _norm_host(host), _norm_host(suffix).lstrip(".")
    if not host or not suffix:
        return False
    return host == suffix or host.endswith("." + suffix)


def _eval_host_suffix(node: dict, ctx: dict) -> bool:
    return host_matches_suffix(_resolve(node["value"], ctx), _resolve(node["suffix"], ctx))


def _eval_per_item(node: dict, ctx: dict) -> bool:
    items = _resolve(node["items"], ctx)
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        return False
    holds = (_eval(node["where"], {**ctx, "item": item}) for item in items)
    return any(holds) if node["op"] == "any" else all(holds)


def _eval(node: dict, ctx: dict) -> bool:
    op = node["op"]
    if op == "compare":
        return _compare(node["cmp"], _resolve(node["left"], ctx), _resolve(node["right"], ctx))
    if op == "and":
        return all(_eval(a, ctx) for a in node["args"])
    if op == "or":
        return any(_eval(a, ctx) for a in node["args"])
    if op == "not":
        return not _eval(node["arg"], ctx)
    if op == "exists":
        return _resolve(node["arg"], ctx) is not _MISSING
    return _LEAF_OPS[op](node, ctx)


_LEAF_OPS = {
    "in": _eval_in,
    "matches": _eval_matches,
    "contains": _eval_contains,
    "host_suffix": _eval_host_suffix,
    "any": _eval_per_item,
    "all": _eval_per_item,
}


def evaluate(tree: dict, context: dict) -> bool:
    """Evaluate ``tree`` against ``context`` ({"trigger": {...}, "variables": {...}}).

    Pure and deterministic: the context is never mutated.  Missing fields make comparisons
    (``!=`` included) false rather than raising; a malformed tree raises ConditionError, and
    a ``matches`` pattern that fails the backtracking screen raises UnsafePatternError
    without running.
    """
    validate(tree)
    return _eval(tree, context if isinstance(context, dict) else {})


def evaluate_item(tree: dict, item: Any, context: dict | None = None) -> bool:
    """Evaluate ``tree`` for one ``item``, addressable as ``{"item": "<path>"}`` operands.

    The reusable per-item entry point (``list.filter`` evaluates a configured condition per
    element with it). ``context`` is the usual trigger/variables context, optional. Validates
    like :func:`evaluate`; the inputs are never mutated.
    """
    validate(tree)
    base = context if isinstance(context, dict) else {}
    return _eval(tree, {**base, "item": item})


# --------------------------------------------------------------------------- text: render

_PREC = {"or": 1, "and": 2}


def _lit_text(v: Any) -> str:
    if isinstance(v, dict) or not _is_json(v):
        raise ConditionError("object literals have no text form")
    if isinstance(v, list):
        return "[" + ", ".join(_lit_text(x) for x in v) + "]"
    return json.dumps(v)


def _operand_text(o: dict) -> str:
    if "item" in o:
        raise ConditionError("item operands have no text form")
    if "literal" in o:
        return _lit_text(o["literal"])
    if "var" in o:
        if not _IDENT.match(o["var"]):
            raise ConditionError(f"variable name {o['var']!r} has no text form")
        return "vars." + o["var"]
    parts = o["field"].split(".")
    if not all(_IDENT.match(p) for p in parts):
        raise ConditionError(f"field path {o['field']!r} has no text form")
    return "trigger." + o["field"]


def _text(node: dict, parent: int) -> str:
    op = node["op"]
    if op in ("contains", "host_suffix", "any", "all"):
        raise ConditionError(f"{op} has no text form")
    if op == "compare":
        return f"{_operand_text(node['left'])} {node['cmp']} {_operand_text(node['right'])}"
    if op in ("and", "or"):
        sym = " && " if op == "and" else " || "
        s = sym.join(_text(a, _PREC[op]) for a in node["args"])
        return f"({s})" if _PREC[op] < parent or (len(node["args"]) > 1 and parent == 3) else s
    if op == "not":
        return (
            "!" + _text(node["arg"], 3)
            if node["arg"]["op"] in ("not", "exists", "matches")
            else "!(" + _text(node["arg"], 0) + ")"
        )
    if op == "exists":
        return f"exists({_operand_text(node['arg'])})"
    if op == "in":
        return f"{_operand_text(node['value'])} in {_operand_text(node['items'])}"
    return f"matches({_operand_text(node['value'])}, {json.dumps(node['pattern'])})"


def to_text(tree: dict) -> str:
    """Render a tree as CEL-style text (advanced view)."""
    validate(tree)
    return _text(tree, 0)


# --------------------------------------------------------------------------- text: parse

# One pattern per token kind, tried in this order at each position (first match wins).
_TOKEN_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("str", re.compile(r'"(?:[^"\\]|\\.)*"')),
    ("num", re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")),
    ("id", re.compile(r"[A-Za-z_]\w*", re.ASCII)),
    ("op", re.compile(r"&&|\|\||==|!=|<=|>=|<|>|!|\(|\)|\[|\]|,|\.")),
)
_SPACE = re.compile(r"\s*")


def _next_token(text: str, pos: int) -> tuple[str, str, int, int] | None:
    """(kind, value, start, end) of the token after any whitespace at ``pos``, or None."""
    space = _SPACE.match(text, pos)
    start = space.end() if space else pos  # \s* always matches; the guard is for typing
    for kind, pattern in _TOKEN_KINDS:
        m = pattern.match(text, start)
        if m:
            return kind, m.group(), start, m.end()
    return None


def _tokenize(text: str) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    pos = 0
    while True:
        tok = _next_token(text, pos)
        if tok is None:
            if text[pos:].strip():
                bad = pos + len(text[pos:]) - len(text[pos:].lstrip())
                raise ConditionParseError(f"unexpected character {text[bad]!r}", bad)
            break
        kind, value, start, pos = tok
        out.append((kind, value, start))
    out.append(("end", "", len(text)))
    return out


class _Parser:
    def __init__(self, text: str) -> None:
        self.toks = _tokenize(text)
        self.i = 0
        self.depth = 0

    @property
    def tok(self) -> tuple[str, str, int]:
        return self.toks[self.i]

    def _is(self, val: str) -> bool:
        k, v, _ = self.tok
        return k in ("op", "id") and v == val

    def _eat(self, val: str) -> None:
        if not self._is(val):
            self._fail(f"expected {val!r}")
        self.i += 1

    def _fail(self, msg: str) -> None:
        k, v, p = self.tok
        got = "end of input" if k == "end" else repr(v)
        raise ConditionParseError(f"{msg}, got {got}", p)

    def parse(self) -> dict:
        node = self.or_()
        if self.tok[0] != "end":
            self._fail("unexpected token")
        return node

    def _nary(self, op: str, sym: str, sub: Any) -> dict:
        args = [sub()]
        while self._is(sym):
            self.i += 1
            args.append(sub())
        return args[0] if len(args) == 1 else {"op": op, "args": args}

    def or_(self) -> dict:
        return self._nary("or", "||", self.and_)

    def and_(self) -> dict:
        return self._nary("and", "&&", self.unary)

    def unary(self) -> dict:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            self._fail("expression nested too deeply")
        try:
            if self._is("!"):
                self.i += 1
                return {"op": "not", "arg": self.unary()}
            if self._is("("):
                self.i += 1
                node = self.or_()
                self._eat(")")
                return node
            if self.tok[0] == "id" and self.tok[1] in ("exists", "matches"):
                return self.call()
            return self.comparison()
        finally:
            self.depth -= 1

    def call(self) -> dict:
        name = self.tok[1]
        self.i += 1
        self._eat("(")
        arg = self.operand()
        if name == "exists":
            self._eat(")")
            return {"op": "exists", "arg": arg}
        self._eat(",")
        if self.tok[0] != "str":
            self._fail("expected a string pattern")
        pat = self.string()
        self._eat(")")
        return {"op": "matches", "value": arg, "pattern": pat}

    def comparison(self) -> dict:
        left = self.operand()
        if self._is("in"):
            self.i += 1
            return {"op": "in", "value": left, "items": self.operand()}
        k, v, _ = self.tok
        if k == "op" and v in CMP_OPS:
            self.i += 1
            return {"op": "compare", "cmp": v, "left": left, "right": self.operand()}
        self._fail("expected a comparison operator or 'in'")
        raise AssertionError  # pragma: no cover

    def string(self) -> str:
        _, v, p = self.tok
        try:
            s = json.loads(v)
        except ValueError as exc:
            raise ConditionParseError("invalid string literal", p) from exc
        self.i += 1
        return s

    def operand(self) -> dict:
        k, v, p = self.tok
        if k == "str":
            return {"literal": self.string()}
        if k == "num":
            self.i += 1
            return {"literal": json.loads(v)}
        if k == "op" and v == "[":
            return {"literal": self.list_()}
        if k == "id" and v in ("true", "false", "null"):
            self.i += 1
            return {"literal": {"true": True, "false": False, "null": None}[v]}
        if k == "id" and v in ("trigger", "vars"):
            return self.reference(v, p)
        self._fail("expected a value (literal, trigger.<path> or vars.<name>)")
        raise AssertionError  # pragma: no cover

    def reference(self, root: str, p: int) -> dict:
        """``trigger.<path>`` or ``vars.<name>``, with ``root`` the current token."""
        self.i += 1
        parts = []
        while self._is("."):
            self.i += 1
            if self.tok[0] != "id":
                self._fail("expected a name after '.'")
            parts.append(self.tok[1])
            self.i += 1
        if not parts:
            self._fail(f"expected '.name' after {root!r}")
        if root == "vars":
            if len(parts) != 1:
                raise ConditionParseError("variables take a single name", p)
            return {"var": parts[0]}
        return {"field": ".".join(parts)}

    def list_(self) -> list:
        self._eat("[")
        items: list = []
        if not self._is("]"):
            while True:
                o = self.operand()
                if "literal" not in o:
                    self._fail("list items must be literals")
                items.append(o["literal"])
                if self._is(","):
                    self.i += 1
                    continue
                break
        self._eat("]")
        return items


def from_text(text: str) -> dict:
    """Parse CEL-style text into a tree; raises ConditionParseError (structured) if malformed."""
    tree = _Parser(text).parse()
    validate(tree)
    return tree


def parse(text: str) -> ParseResult:
    """Non-raising variant of from_text: returns ParseResult(tree, error)."""
    try:
        return ParseResult(from_text(text), None)
    except ConditionParseError as exc:
        return ParseResult(None, exc)
    except ConditionError as exc:
        return ParseResult(None, ConditionParseError(exc.message, 0))
