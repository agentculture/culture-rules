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

Operands::

    {"field": "a.b"}    # dotted path into context["trigger"]
    {"var": "name"}     # context["variables"][name]
    {"literal": <json>} # a JSON scalar or list

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

MAX_PATTERN_LEN = 256
MAX_INPUT_LEN = 10_000
MAX_DEPTH = 64

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
        raise _err("operand must be one of field/var/literal", path)
    ((k, v),) = o.items()
    if k in ("field", "var"):
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


def validate(node: Any, path: str = "$", depth: int = 0) -> None:
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
        _validate_group(node, op, path, depth)
    elif op == "not":
        if "arg" not in node:
            raise _err("not needs arg", path)
        validate(node["arg"], path + ".arg", depth + 1)
    elif op == "exists":
        _check_operand(node.get("arg"), path + ".arg")
    elif op == "in":
        _check_operand(node.get("value"), path + ".value")
        _check_operand(node.get("items"), path + ".items")
    elif op == "matches":
        _validate_matches(node, path)
    else:
        raise _err(f"unknown op {op!r}", path)


def _validate_group(node: dict, op: str, path: str, depth: int) -> None:
    args = node.get("args")
    # At least two args: a single-argument group has no text form of its own, so it
    # would not round-trip through to_text/from_text (Qwen review of t2). Editors unwrap
    # a one-item group before saving.
    if not isinstance(args, list) or len(args) < 2:
        raise _err(f"{op} needs an args list of at least two conditions", path)
    for i, a in enumerate(args):
        validate(a, f"{path}.args[{i}]", depth + 1)


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


# --------------------------------------------------------------------------- evaluation


def _resolve(o: dict, ctx: dict) -> Any:
    if "literal" in o:
        return o["literal"]
    if "var" in o:
        cur = ctx.get("variables") or {}
        return cur.get(o["var"], _MISSING) if isinstance(cur, dict) else _MISSING
    cur = ctx.get("trigger") or {}
    for part in o["field"].split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _eq(a: Any, b: Any) -> bool:
    if a is _MISSING or b is _MISSING:
        return False
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return bool(a == b)


def _compare(op: str, a: Any, b: Any) -> bool:
    if op == "==":
        return _eq(a, b)
    if op == "!=":
        return not _eq(a, b)
    if a is _MISSING or b is _MISSING:
        return False
    if not ((_is_num(a) and _is_num(b)) or (isinstance(a, str) and isinstance(b, str))):
        return False
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


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
    if op == "in":
        value, items = _resolve(node["value"], ctx), _resolve(node["items"], ctx)
        if value is _MISSING or not isinstance(items, list):
            return False
        return any(_eq(value, i) for i in items)
    value = _resolve(node["value"], ctx)  # matches
    if not isinstance(value, str) or len(value) > MAX_INPUT_LEN:
        return False
    return re.fullmatch(node["pattern"], value) is not None


def evaluate(tree: dict, context: dict) -> bool:
    """Evaluate ``tree`` against ``context`` ({"trigger": {...}, "variables": {...}}).

    Pure and deterministic: the context is never mutated.  Missing fields make comparisons
    false rather than raising; a malformed tree raises ConditionError.
    """
    validate(tree)
    return _eval(tree, context if isinstance(context, dict) else {})


# --------------------------------------------------------------------------- text: render

_PREC = {"or": 1, "and": 2}


def _lit_text(v: Any) -> str:
    if isinstance(v, dict) or not _is_json(v):
        raise ConditionError("object literals have no text form")
    if isinstance(v, list):
        return "[" + ", ".join(_lit_text(x) for x in v) + "]"
    return json.dumps(v)


def _operand_text(o: dict) -> str:
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
