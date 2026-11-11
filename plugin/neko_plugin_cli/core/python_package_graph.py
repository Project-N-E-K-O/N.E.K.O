"""Resolve installed dependency relationships using the host's marker semantics."""
from __future__ import annotations

from collections import deque

from packaging.requirements import Requirement
from packaging.version import Version

from plugin.core.python_dependencies import canonicalize_distribution_name, is_host_provided_requirement


def requirement_applies(requirement: Requirement, extras: set[str] | None = None) -> bool:
    try:
        return requirement.marker is None or any(
            requirement.marker.evaluate({"extra": extra}) for extra in ({""} | (extras or set()))
        )
    except KeyError as exc:
        # Set-valued lock-file markers (extras/dependency_groups) parse, but
        # are unavailable in project/distribution metadata's marker context.
        raise ValueError(f"Marker variable is unavailable for project dependencies: {exc.args[0]}") from exc


def requirement_status(requirement: Requirement, installed: dict[str, dict]) -> tuple[str, str | None]:
    row = installed.get(canonicalize_distribution_name(requirement.name))
    version = row["version"] if row else None
    if not requirement_applies(requirement):
        return "not_applicable", version
    if is_host_provided_requirement(str(requirement)):
        return "host_provided", version
    if row is None:
        return "missing", None
    if requirement.specifier and Version(version) not in requirement.specifier:
        return "version_mismatch", version
    return "satisfied", version


def build_dependency_graph(installed: list[dict], declarations: list[dict]) -> tuple[dict, list[dict]]:
    """Return an adjacency graph, keeping shared packages and cycles as references.

    Declared extras propagate through active requirements. Packages outside that
    closure are inspected with their base requirements and remain visible as
    other installed roots. No imports, filesystem reads or network are needed.
    """
    distributions = {canonicalize_distribution_name(row["name"]): row for row in installed}
    nodes: dict[str, dict] = {}
    edges: dict[str, dict[str, dict]] = {}
    issues: list[dict] = []
    invalid: set[tuple[str, str]] = set()

    def node(name: str) -> str:
        key = canonicalize_distribution_name(name)
        if key not in nodes:
            row = distributions.get(key)
            nodes[key] = {"id": key, "name": row["name"] if row else name,
                          "version": row["version"] if row else None,
                          "direct": bool(row and row["direct"]), "dependencies": [], "required_by": [],
                          "declarations": [], "status": "satisfied" if row else "missing"}
            edges[key] = {}
        return key

    for row in installed:
        node(row["name"])
    queue: deque[Requirement] = deque()
    roots: list[str] = []
    for declaration in declarations:
        if declaration["status"] not in {"satisfied", "missing", "version_mismatch"}:
            continue
        requirement = Requirement(declaration["requirement"])
        key = node(requirement.name)
        nodes[key]["direct"] = True
        nodes[key]["declarations"].append(declaration["requirement"])
        if declaration["status"] == "version_mismatch":
            nodes[key]["status"] = "version_mismatch"
        if key not in roots:
            roots.append(key)
        queue.append(requirement)

    visited: set[tuple[str, tuple[str, ...]]] = set()

    def drain() -> None:
        while queue:
            parent = queue.popleft()
            parent_id = node(parent.name)
            context = (parent_id, tuple(sorted(parent.extras)))
            if context in visited or parent_id not in distributions:
                continue
            visited.add(context)
            for text in distributions[parent_id]["requires_dist"]:
                try:
                    child = Requirement(text)
                    applies = requirement_applies(child, parent.extras)
                except ValueError as exc:
                    if (parent_id, text) not in invalid:
                        invalid.add((parent_id, text))
                        issues.append({"code": "invalid_metadata", "package": parent.name, "message": str(exc)})
                    continue
                if not applies or is_host_provided_requirement(text):
                    continue
                child_id = node(child.name)
                child.marker = None
                status, _ = requirement_status(child, distributions)
                edge = edges[parent_id].setdefault(child_id, {"id": child_id, "requirements": [], "status": status})
                if text not in edge["requirements"]:
                    edge["requirements"].append(text)
                if status in {"missing", "version_mismatch"}:
                    edge["status"] = status
                    nodes[child_id]["status"] = status
                queue.append(child)

    drain()
    visited_names = {name for name, _extras in visited}
    for key in sorted(distributions):
        if key not in visited_names:
            queue.append(Requirement(distributions[key]["name"]))
    drain()
    for key, dependencies in edges.items():
        nodes[key]["dependencies"] = [dependencies[child] for child in sorted(dependencies)]
        for child in dependencies:
            nodes[child]["required_by"].append(key)
    for item in nodes.values():
        item["required_by"].sort()

    def reachable(start: list[str]) -> set[str]:
        seen: set[str] = set()
        pending = list(start)
        while pending:
            key = pending.pop()
            if key not in seen:
                seen.add(key)
                pending.extend(edges[key])
        return seen

    remaining = set(distributions) - reachable(roots)
    other_roots: list[str] = []
    while remaining:
        candidates = sorted(key for key in remaining if not (set(nodes[key]["required_by"]) & remaining))
        # A disconnected cycle still needs an entry point in the tree.
        for key in candidates or [min(remaining)]:
            if key in remaining:
                other_roots.append(key)
                remaining -= reachable([key])
    return {"roots": roots, "other_roots": other_roots,
            "nodes": [nodes[key] for key in sorted(nodes)]}, issues
