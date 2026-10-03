"""Cycle detection over a small directed graph (id -> successor ids); stdlib only."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

__all__ = ["find_cycle"]


def _visit(
    node: str, stack: list[str], graph: Mapping[str, Iterable[str]], state: dict[str, int]
) -> list[str] | None:
    """Depth-first walk from ``node``; the first cycle met, closed on its start, or None."""
    state[node] = 1  # on the stack
    stack.append(node)
    for nxt in sorted(graph.get(node, ())):
        if state.get(nxt) == 1:
            return stack[stack.index(nxt) :] + [nxt]
        if nxt not in state:
            found = _visit(nxt, stack, graph, state)
            if found:
                return found
    stack.pop()
    state[node] = 2  # done
    return None


def find_cycle(graph: Mapping[str, Iterable[str]]) -> list[str] | None:
    """The first cycle found, as ``[a, b, ..., a]``, walking nodes and edges sorted; or None."""
    state: dict[str, int] = {}
    for node in sorted(graph):
        if node not in state:
            found = _visit(node, [], graph, state)
            if found:
                return found
    return None
