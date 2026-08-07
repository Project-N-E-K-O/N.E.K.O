"""Validate module graphs and select their ordered terminal nodes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


class ModuleGraphError(ValueError):
    """A module graph cannot be selected safely."""


@dataclass(frozen=True)
class ModuleNode:
    """One normalized candidate in a single module slot."""

    stable_key: str
    payload: Any
    index: int
    xp: int | float = 0
    credits: int | float = 0
    explicit_top: bool = False
    next_refs: tuple[str, ...] = ()
    predecessor_refs: tuple[str, ...] = ()
    has_explicit_graph: bool = False


def select_terminal_modules(
    nodes: Iterable[ModuleNode],
    *,
    infer_adjacent_indexes: bool = False,
) -> tuple[ModuleNode, ...]:
    """Return all terminal nodes with the primary candidate first."""
    candidates = tuple(nodes)
    by_key: dict[str, ModuleNode] = {}
    for node in candidates:
        _validate_node(node)
        if node.stable_key in by_key:
            raise ModuleGraphError(
                f"duplicate module key {node.stable_key!r}")
        by_key[node.stable_key] = node

    edges: dict[str, set[str]] = {key: set() for key in by_key}
    has_explicit_graph = any(
        node.has_explicit_graph or node.next_refs or node.predecessor_refs
        for node in candidates
    )
    if has_explicit_graph:
        referenced: set[str] = set()
        for node in candidates:
            for reference in node.next_refs:
                _require_reference(reference, by_key)
                edges[node.stable_key].add(reference)
                referenced.add(reference)
            for reference in node.predecessor_refs:
                _require_reference(reference, by_key)
                edges[reference].add(node.stable_key)
                referenced.add(reference)
        for node in candidates:
            if not (
                node.has_explicit_graph
                or node.next_refs
                or node.predecessor_refs
                or node.stable_key in referenced
            ):
                raise ModuleGraphError(
                    f"uncovered module {node.stable_key!r} in explicit graph")
    elif infer_adjacent_indexes:
        layers: dict[int, list[str]] = {}
        for node in candidates:
            layers.setdefault(node.index, []).append(node.stable_key)
        indexes = sorted(layers)
        for lower, higher in zip(indexes, indexes[1:]):
            for source in layers[lower]:
                edges[source].update(layers[higher])

    _reject_cycles(edges)
    terminals = (node for node in candidates if not edges[node.stable_key])
    return tuple(sorted(terminals, key=_terminal_sort_key))


def _require_reference(
    reference: str,
    candidates: dict[str, ModuleNode],
) -> None:
    if reference not in candidates:
        raise ModuleGraphError(f"dangling module reference {reference!r}")


def _validate_node(node: ModuleNode) -> None:
    if not isinstance(node.stable_key, str) or not node.stable_key:
        raise ModuleGraphError("module key must be a non-empty string")
    if not isinstance(node.explicit_top, bool):
        raise ModuleGraphError(
            f"invalid explicit_top for module {node.stable_key!r}")
    _validate_rank_number(node.index, node.stable_key, "index", integer=True)
    _validate_rank_number(node.xp, node.stable_key, "xp")
    _validate_rank_number(node.credits, node.stable_key, "credits")


def _validate_rank_number(
    value: Any,
    stable_key: str,
    field: str,
    *,
    integer: bool = False,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (integer and not isinstance(value, int))
    ):
        raise ModuleGraphError(
            f"invalid {field} for module {stable_key!r}")
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ModuleGraphError(
            f"invalid {field} for module {stable_key!r}") from exc
    if not math.isfinite(converted) or converted < 0:
        raise ModuleGraphError(
            f"invalid {field} for module {stable_key!r}")


def _reject_cycles(edges: dict[str, set[str]]) -> None:
    indegree = {key: 0 for key in edges}
    for targets in edges.values():
        for target in targets:
            indegree[target] += 1
    pending = [key for key, degree in indegree.items() if degree == 0]
    visited = 0
    while pending:
        source = pending.pop()
        visited += 1
        for target in edges[source]:
            indegree[target] -= 1
            if indegree[target] == 0:
                pending.append(target)
    if visited != len(edges):
        raise ModuleGraphError("module graph contains a cycle")


def _terminal_sort_key(node: ModuleNode) -> tuple[Any, ...]:
    return (
        -int(node.explicit_top),
        -node.index,
        -node.xp,
        -node.credits,
        node.stable_key,
    )


__all__ = [
    "ModuleGraphError",
    "ModuleNode",
    "select_terminal_modules",
]
