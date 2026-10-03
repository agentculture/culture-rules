"""Static screen for ``matches`` patterns that can backtrack catastrophically.

Python's ``re`` is a backtracking engine with no time limit, and a ``matches`` condition runs
a rule author's pattern on external event data. :func:`unsafe_reason` inspects the parsed
pattern (the standard library's own parser, ``re._parser``) and names the first shape that
can make matching super-linear, or returns ``None`` for a pattern it considers safe:

- a backreference (``\\1``, ``(?P=name)``, ``(?(1)…)``): not boundable;
- a nested quantifier: a variable-length quantifier inside a repeated group, as in ``(a+)+``,
  ``(a*)*`` or ``(\\w+\\s?)+``;
- an alternation inside a repeated group whose branches have different lengths and can
  start with the same character or be empty, as in ``(a|b|ab)+`` or ``(a|aa)+`` (which the
  parser factors into ``a(?:|a)``); ``(ab|c)+`` is fine;
- an alternation whose branches may match the same text, as in ``(a?|a)`` or ``(ab|a.)``:
  the engine may try both, and such choices multiply;
- more than one pair of variable-length parts that can trade the same text, as in
  ``a*a*a*b``, ``.*a.*a.*b`` or ``a*a*x a*a*x``: the work on a failing match grows with the
  input length to the power of the number of trades, and trades multiply across separators,
  so one trade (quadratic, e.g. ``.*foo.*``) is allowed and a second is refused.

The screen is conservative: when the parser is unavailable or the parsed shape is unexpected,
the pattern is reported unsafe. Standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

try:  # Python 3.11+ (the project requires 3.12); the parser is private, hence the guard
    from re import _constants as _sre_c
    from re import _parser as _sre_parse
except ImportError:  # pragma: no cover - only on an interpreter without re._parser
    _sre_c = None  # type: ignore[assignment]
    _sre_parse = None  # type: ignore[assignment]

__all__ = ["unsafe_reason"]

#: Code points always sampled when comparing character sets (Latin-1 plus Latin Extended-A).
_BASE_SAMPLE = frozenset(range(0x180))
#: A quantified group or a backreference: what the parser-free fallback refuses.
_FALLBACK_REFUSE = re.compile(r"\)[*+?{]|\\[1-9]|\(\?P=|\(\?\(")
_DEGREE_LIMIT = 2
_PREFIX_LIMIT = 16
_RUN_LIMIT = 256


#: How an alternative goes on after its fixed-width start: (first characters, may be empty).
_Tail = tuple["_Chars", bool]


class _Unsafe(Exception):
    """Raised inside the analysis with the reason a pattern is refused."""


@dataclass(frozen=True)
class _Chars:
    """A character set: the sampled code points it matches, and whether it matches others."""

    points: frozenset[int] = frozenset()
    outside: bool = False

    def __and__(self, other: _Chars) -> _Chars:
        return _Chars(self.points & other.points, self.outside and other.outside)

    def __or__(self, other: _Chars) -> _Chars:
        return _Chars(self.points | other.points, self.outside or other.outside)

    def __bool__(self) -> bool:
        return bool(self.points) or self.outside


@dataclass(frozen=True)
class _Chain:
    """Where a match can be ambiguous, along one path through the pattern.

    ``runs`` holds the alphabets of open stretches of text: a variable-length part can
    match a string over the alphabet, and so can every required character since. A new
    variable-length part that shares an open alphabet can trade that text with the earlier
    one, so ``degree`` (the power of the input length the work on a failing match grows
    with, less one) goes up. Trades multiply even across disjoint separators (``a*a*x`` twice
    is already quartic), so the degree counts the whole path, and a second trade is refused.
    """

    runs: frozenset[_Chars] = frozenset()
    degree: int = 0

    def variable(self, chars: _Chars, optional: bool) -> _Chain:
        runs = {chars}
        traded = False
        for alphabet in self.runs:
            shared = alphabet & chars
            if shared:
                runs.add(shared)
                traded = True
            if shared or optional:
                runs.add(alphabet)
        return self._checked(runs, self.degree + int(traded))

    def separator(self, chars: _Chars) -> _Chain:
        return _Chain(frozenset(a for a in self.runs if a & chars), self.degree)

    def add_degree(self, degree: int) -> _Chain:
        return self._checked(self.runs, self.degree + degree)

    def merge(self, other: _Chain) -> _Chain:
        return self._checked(self.runs | other.runs, max(self.degree, other.degree))

    @staticmethod
    def _checked(runs: set[_Chars] | frozenset[_Chars], degree: int) -> _Chain:
        if degree >= _DEGREE_LIMIT:
            raise _Unsafe(
                "more than one pair of quantifiers can match the same text "
                "(split the pattern into several `matches` joined with &&)"
            )
        if len(runs) > _RUN_LIMIT:
            raise _Unsafe("too many overlapping quantifiers to check")
        return _Chain(frozenset(runs), degree)


@dataclass(frozen=True)
class _Prefix:
    """An alternative's fixed-width start, and its open continuation (``None``: it ends)."""

    fixed: tuple[_Chars, ...]
    tail: _Tail | None


def _category_has(category: Any, ch: str) -> bool:
    name = str(category)
    if name.endswith("DIGIT"):
        hit = ch.isdecimal()
    elif name.endswith("SPACE"):
        hit = ch.isspace()
    elif name.endswith("WORD"):
        hit = ch.isalnum() or ch == "_"
    elif name.endswith("LINEBREAK"):
        hit = ch == "\n"
    else:
        return True
    return not hit if "_NOT_" in name else hit


def _set_member(op: Any, av: Any, ch: str) -> bool:
    if op is _sre_c.LITERAL:
        return ord(ch) == av
    if op is _sre_c.RANGE:
        return av[0] <= ord(ch) <= av[1]
    if op is _sre_c.CATEGORY:
        return _category_has(av, ch)
    return True  # an unknown set member: assume it matches


def _in_has(items: Any, ch: str) -> bool:
    negate = any(op is _sre_c.NEGATE for op, _ in items)
    hit = any(_set_member(op, av, ch) for op, av in items if op is not _sre_c.NEGATE)
    return hit != negate


def _atom_has(op: Any, av: Any, ch: str) -> bool:
    if op is _sre_c.LITERAL:
        return ord(ch) == av
    if op is _sre_c.NOT_LITERAL:
        return ord(ch) != av
    if op is _sre_c.IN:
        return _in_has(av, ch)
    return True  # ANY, or an atom this screen does not model


def _atom_outside(op: Any, av: Any) -> bool:
    """Whether the atom matches "everything else" beyond the sample: ``.``, ``[^…]``, ``[^x]``.

    The sample holds representatives of every category (digits, letters, spaces), so a
    category's overlap with another set already shows within it.
    """
    if op is _sre_c.LITERAL:
        return False
    if op is _sre_c.IN:
        return any(o is _sre_c.NEGATE for o, _ in av)
    return True


class _Analysis:
    """One pass over a parsed pattern; raises :class:`_Unsafe` with the first refusal."""

    def __init__(self, parsed: Any, fold: bool) -> None:
        self.fold = fold
        self.sample = _BASE_SAMPLE | frozenset(self._literals(parsed))

    # ------------------------------------------------------------------ character sets

    def _literals(self, items: Any) -> list[int]:
        out: list[int] = []
        for op, av in items:
            if op is _sre_c.LITERAL or op is _sre_c.NOT_LITERAL:
                out.append(av)
            elif op is _sre_c.RANGE:
                out.extend(av)
            elif op is _sre_c.IN:
                out.extend(self._literals(av))
            else:
                out.extend(self._literals(_children(op, av)))
        return out

    def _variants(self, point: int) -> set[str]:
        ch = chr(point)
        if not self.fold:
            return {ch}
        return {v for v in (ch, ch.lower(), ch.upper()) if len(v) == 1}

    def _atom(self, op: Any, av: Any) -> _Chars:
        points = frozenset(
            p for p in self.sample if any(_atom_has(op, av, v) for v in self._variants(p))
        )
        return _Chars(points, _atom_outside(op, av))

    def chars(self, items: Any) -> _Chars:
        """Every character any atom in ``items`` can match."""
        out = _Chars()
        for op, av in items:
            if op in _ATOMS:
                out = out | self._atom(op, av)
            else:
                out = out | self.chars(_children(op, av))
        return out

    def first(self, items: Any) -> tuple[_Chars, bool]:
        """(the characters ``items`` can start with, whether ``items`` can match empty)."""
        out = _Chars()
        for op, av in items:
            if op in _ATOMS:
                return out | self._atom(op, av), False
            chars, nullable = self._first_of(op, av)
            out = out | chars
            if not nullable:
                return out, False
        return out, True

    def _first_of(self, op: Any, av: Any) -> tuple[_Chars, bool]:
        if op in _REPEATS:
            chars, nullable = self.first(av[2])
            return chars, nullable or av[0] == 0
        if op is _sre_c.BRANCH:
            firsts = [self.first(alt) for alt in av[1]]
            union = _Chars()
            for chars, _ in firsts:
                union = union | chars
            return union, any(nullable for _, nullable in firsts)
        if op in _ZERO_WIDTH:
            return _Chars(), True
        return self.first(_children(op, av))

    def prefix(self, items: Any) -> _Prefix:
        """The character sets of the fixed-width start of ``items``, then how it goes on."""
        flat = _flatten(items)
        out: list[_Chars] = []
        for i, (op, av) in enumerate(flat):
            if len(out) >= _PREFIX_LIMIT:
                return _Prefix(tuple(out), (_Chars(frozenset(self.sample), True), True))
            if op in _ATOMS:
                out.append(self._atom(op, av))
            elif op not in _ZERO_WIDTH:
                return _Prefix(tuple(out), self.first(flat[i:]))
        return _Prefix(tuple(out), None)

    # ------------------------------------------------------------------ the walk

    def walk(self, items: Any, chain: _Chain) -> _Chain:
        for op, av in items:
            chain = self._step(op, av, chain)
        return chain

    def _step(self, op: Any, av: Any, chain: _Chain) -> _Chain:
        if op in _BACKREFS:
            raise _Unsafe("backreferences are not allowed")
        if op in _REPEATS:
            return self._repeat(av, chain)
        if op is _sre_c.BRANCH:
            return self._branch(av[1], chain)
        if op is _sre_c.ASSERT or op is _sre_c.ASSERT_NOT:
            inner = self.walk(av[1], _Chain())  # runs again at every place it is tried
            return chain.add_degree(inner.degree)
        if op is _sre_c.AT:
            return chain
        if op in _ATOMS:
            return chain.separator(self._atom(op, av))
        return self.walk(_children(op, av), chain)  # groups

    def _repeat(self, av: Any, chain: _Chain) -> _Chain:
        low, high, body = av
        if high > 1:
            self._fixed_width(body)
            self.walk(body, _Chain())  # ambiguous alternatives inside the body
            chars = self.chars(body)
            return chain.separator(chars) if low == high else chain.variable(chars, low == 0)
        walked = self.walk(body, chain)
        if low == high:
            return walked
        if self._variable_part(body) is not None:
            return walked.merge(chain)  # taken or skipped; its own variable parts are counted
        return chain.variable(self.chars(body), True)

    def _branch(self, alts: Any, chain: _Chain) -> _Chain:
        self._refuse_ambiguous(alts)
        widths = {_width(alt) for alt in alts}
        if None not in widths and len(widths) > 1:  # fixed-width alternatives of two lengths
            chars = self.chars([(_sre_c.BRANCH, (None, alts))])
            return chain.variable(chars, 0 in widths)
        best = _Chain()
        for alt in alts:
            best = best.merge(self.walk(alt, chain))
        return best

    def _refuse_ambiguous(self, alts: Any) -> None:
        prefixes = [self.prefix(alt) for alt in alts]
        for i, left in enumerate(prefixes):
            if any(_ambiguous(left, right) for right in prefixes[i + 1 :]):
                raise _Unsafe("two alternatives can match the same text")

    def _variable_part(self, items: Any, *, repeated: bool = False) -> str | None:
        """Why ``items`` holds a part of variable length, or ``None`` if it holds none.

        ``repeated``: ``items`` is a repeated body, where alternatives of different lengths
        are allowed only when no two of them can start with the same character.
        """
        for op, av in items:
            if op in _BACKREFS:
                return "backreferences are not allowed"
            if op in _REPEATS and av[0] != av[1]:
                return "nested quantifier: a variable-length part inside a repeat"
            reason = self._branch_reason(av[1], repeated) if op is _sre_c.BRANCH else None
            if reason is None and op not in _ATOMS:
                reason = self._variable_part(_children(op, av), repeated=repeated)
            if reason is not None:
                return reason
        return None

    def _branch_reason(self, alts: Any, repeated: bool) -> str | None:
        if len({_width(alt) for alt in alts}) == 1:
            return None
        firsts = [self.first(alt) for alt in alts]
        if not repeated or any(nullable for _, nullable in firsts):
            return "an alternation inside a repeat can match different lengths"
        for i, (left, _) in enumerate(firsts):
            if any(left & right for right, _ in firsts[i + 1 :]):
                return "alternatives of different lengths inside a repeat start alike"
        return None

    def _fixed_width(self, items: Any) -> None:
        """Refuse a repeated body holding anything of variable length (nested quantifier)."""
        reason = self._variable_part(items, repeated=True)
        if reason is not None:
            raise _Unsafe(reason)


def _width(items: Any) -> int | None:
    """The fixed number of characters ``items`` match, or ``None`` if it varies."""
    total = 0
    for op, av in items:
        width = _item_width(op, av)
        if width is None:
            return None
        total += width
    return total


def _item_width(op: Any, av: Any) -> int | None:
    """The fixed width of one parsed item ``(op, av)``, or ``None`` if it varies."""
    if op in _ATOMS:
        return 1
    if op in _REPEATS:
        inner = _width(av[2])
        return None if inner is None or av[0] != av[1] else inner * av[0]
    if op is _sre_c.BRANCH:
        widths = {_width(alt) for alt in av[1]}
        return widths.pop() if len(widths) == 1 else None
    if op in _ZERO_WIDTH:
        return 0
    return _width(_children(op, av))


def _ambiguous(left: _Prefix, right: _Prefix) -> bool:
    """Whether two alternatives may match one text (conservatively).

    They are told apart by a position where their fixed characters are disjoint, by one
    ending (fixed-width) where the other must go on, or by an open continuation that cannot
    start with the other's next character.
    """
    if any(not (a & b) for a, b in zip(left.fixed, right.fixed)):
        return False
    if len(left.fixed) == len(right.fixed):
        return _same_length_ambiguous(left.tail, right.tail)
    short, long_ = (left, right) if len(left.fixed) < len(right.fixed) else (right, left)
    if short.tail is None:
        return False  # the shorter one ends where the longer one needs more
    return bool(short.tail[0] & long_.fixed[len(short.fixed)])


def _same_length_ambiguous(left: _Tail | None, right: _Tail | None) -> bool:
    if left is None or right is None:
        other = left or right
        return other is None or other[1]  # both end, or the open one may end here too
    return bool(left[0] & right[0]) or left[1] or right[1]


def _flatten(items: Any) -> list:
    """``items`` with groups expanded in place (their contents match in sequence)."""
    out: list = []
    for op, av in items:
        if op is _sre_c.SUBPATTERN or op is _sre_c.ATOMIC_GROUP:
            out.extend(_flatten(_children(op, av)))
        else:
            out.append((op, av))
    return out


def _children(op: Any, av: Any) -> list:
    """The nested items of a compound node (groups, repeats, branches, lookarounds)."""
    if op in _REPEATS:
        return list(av[2])
    if op is _sre_c.SUBPATTERN:
        return list(av[-1])
    if op is _sre_c.ATOMIC_GROUP:
        return list(av)
    if op is _sre_c.BRANCH:
        return [item for alt in av[1] for item in alt]
    if op is _sre_c.ASSERT or op is _sre_c.ASSERT_NOT:
        return list(av[1])
    if op is _sre_c.GROUPREF_EXISTS:
        return [item for part in av[1:] if part is not None for item in part]
    return []


def _uses_ignorecase(items: Any) -> bool:
    for op, av in items:
        if op is _sre_c.SUBPATTERN and av[1] & re.IGNORECASE:
            return True
        if op not in _ATOMS and _uses_ignorecase(_children(op, av)):
            return True
    return False


if _sre_c is not None:
    _ATOMS = frozenset({_sre_c.LITERAL, _sre_c.NOT_LITERAL, _sre_c.ANY, _sre_c.IN})
    _REPEATS = frozenset(
        {_sre_c.MAX_REPEAT, _sre_c.MIN_REPEAT, getattr(_sre_c, "POSSESSIVE_REPEAT", None)}
    ) - {None}
    _BACKREFS = frozenset({_sre_c.GROUPREF, _sre_c.GROUPREF_EXISTS})
    _ZERO_WIDTH = frozenset({_sre_c.AT, _sre_c.ASSERT, _sre_c.ASSERT_NOT})


def _analyse(pattern: str) -> str | None:
    parsed = _sre_parse.parse(pattern)
    flags = getattr(getattr(parsed, "state", None), "flags", re.IGNORECASE)
    fold = bool(flags & re.IGNORECASE) or _uses_ignorecase(parsed)
    try:
        _Analysis(parsed, fold).walk(parsed, _Chain())
    except _Unsafe as exc:
        return str(exc)
    return None


@lru_cache(maxsize=512)
def unsafe_reason(pattern: str) -> str | None:
    """Why ``pattern`` may backtrack catastrophically, or ``None`` if it looks safe.

    ``pattern`` must already compile. Conservative: an unanalysable pattern is unsafe.
    """
    if _sre_parse is None:  # pragma: no cover - only without re._parser
        if _FALLBACK_REFUSE.search(pattern):
            return "quantified groups and backreferences need the re parser to be checked"
        return None
    try:
        return _analyse(pattern)
    except (re.error, AttributeError, IndexError, KeyError, TypeError, ValueError):
        return "the pattern could not be analysed for backtracking safety"
