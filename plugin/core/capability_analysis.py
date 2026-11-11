"""Bounded, import-free analysis of plugin-owned Python sources for disclosure."""

from __future__ import annotations

import ast
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from collections.abc import Iterator

from plugin.utils.source_paths import iter_plugin_python_files

CAPABILITIES = ("conversations.read", "memory.read", "messages.push")
# Bump when a change to analysis makes persisted reports stale.
ANALYZER_VERSION = 3
MAX_FILES = 500
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_EVIDENCE = 200
MAX_REFERENCE_LENGTH = 4096
# Roughly 4x the largest observed plugin (study_companion ~= 2.1M).
MAX_NODES = 8_000_000
_REFERENCE_BOUNDARY = re.compile(r"\.|\(\)")
_TEST_DIR_NAMES = frozenset({"test", "tests"})


class _AnalysisBudgetExceeded(Exception):
    pass


def _name(node: ast.AST) -> str:
    parts = []
    length = 0
    while isinstance(node, (ast.Attribute, ast.Call)):
        if isinstance(node, ast.Attribute):
            parts.extend((node.attr, "."))
            length += len(node.attr) + 1
            node = node.value
        else:
            parts.append("()")
            length += 2
            node = node.func
        if length > MAX_REFERENCE_LENGTH:
            raise _AnalysisBudgetExceeded
    if isinstance(node, ast.Name):
        if length + len(node.id) > MAX_REFERENCE_LENGTH:
            raise _AnalysisBudgetExceeded
        parts.append(node.id)
    return "".join(reversed(parts))


def _memory_client_rule(method: str) -> bool:
    return (
        "plugin.sdk." in method
        and "MemoryClient()" in method
        and method.endswith((".search", ".get_persona"))
    )


# Declarative capability rules. ``kind`` is "contains" (pattern is a substring
# of the resolved reference) or "endswith". ``predicate`` is an optional extra
# check applied after the pattern matched.
_RULES = (
    {
        "capability": "conversations.read",
        "kind": "contains",
        "patterns": (".bus.conversations", ".reply_tail.subscribe"),
    },
    {
        "capability": "memory.read",
        "kind": "contains",
        "patterns": (".bus.memory.get", ".query_memory"),
    },
    {
        "capability": "memory.read",
        "kind": "contains",
        "patterns": ("",),
        "predicate": _memory_client_rule,
    },
    {
        "capability": "messages.push",
        "kind": "endswith",
        "patterns": (
            ".push_message",
            ".push_message_async",
            ".create_card",
            ".create_view",
        ),
    },
    {
        "capability": "messages.push",
        "kind": "contains",
        "patterns": (".reply_tail.register",),
    },
)

assert {rule["capability"] for rule in _RULES} <= set(CAPABILITIES)


def _capability_for(method: str) -> str | None:
    for rule in _RULES:
        match = (
            any(part in method for part in rule["patterns"])
            if rule["kind"] == "contains"
            else method.endswith(rule["patterns"])
        )
        if not match:
            continue
        predicate = rule.get("predicate")
        if predicate is None or predicate(method):
            return rule["capability"]
    return None


class _AnalysisWalker:
    """Node counter + stop event shared by every traversal of one analysis."""

    def __init__(self, stop: Event | None = None) -> None:
        self.nodes = 0
        self.stop = stop

    def check(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES or (
            self.stop is not None and self.stop.is_set()
        ):
            raise _AnalysisBudgetExceeded

    def scope_nodes(self, body):
        """Walk one lexical scope, leaving nested functions/classes to their own scope."""
        stack = [iter(body)]
        while stack:
            node = next(stack[-1], None)
            if node is None:
                stack.pop()
                continue
            self.check()
            yield node
            stack.append(iter(_outer_scope_children(node)))


def _outer_scope_children(node):
    # Definitions evaluate decorators, bases and defaults in the enclosing scope.
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        if not isinstance(node, ast.Lambda):
            yield from node.decorator_list
        yield from node.args.defaults
        yield from (value for value in node.args.kw_defaults if value is not None)
    elif isinstance(node, ast.ClassDef):
        yield from node.decorator_list
        yield from node.bases
        yield from node.keywords
    else:
        yield from ast.iter_child_nodes(node)


# A binding's second element marks a resolved origin the analysis must not
# report: imports outside `plugin.` (relative imports, the plugin's own
# modules, third-party and stdlib names) and names defined by the plugin's own
# source. Unresolved receivers (self, parameters) keep no origin and still
# match.
def _resolve(
    value: str, bindings: dict[str, tuple[str | None, bool]]
) -> tuple[str, bool]:
    # Bindings already contain resolved paths. A module segment in a resolved
    # path can share a name with a local variable, so never look it up again.
    ends = [match.start() for match in _REFERENCE_BOUNDARY.finditer(value)]
    ends.append(len(value))
    for end in reversed(ends):
        prefix = value[:end]
        if prefix in bindings:
            replacement, external = bindings[prefix]
            if replacement is None:
                return value, False
            if len(replacement) + len(value) - end > MAX_REFERENCE_LENGTH:
                raise _AnalysisBudgetExceeded
            return replacement + value[end:], external
    return value, False


def _sdk_module(path: str) -> bool:
    return path == "plugin" or path.startswith("plugin.")


def _getattr_reference(node: ast.AST) -> tuple[ast.AST, str] | None:
    """getattr(x, "name") — optionally called — reads like x.name."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if (
        isinstance(func, ast.Name)
        and func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and node.args[1].value.isidentifier()
    ):
        return node.args[0], node.args[1].value
    if isinstance(func, ast.Call):
        return _getattr_reference(func)
    return None


def _resolved_references(tree, walker: _AnalysisWalker):
    def scope(body, inherited, parameters=()):
        nodes = list(walker.scope_nodes(body))
        declared, assignments = _collect_declarations(nodes, parameters, walker)
        bindings = _build_bindings(declared, inherited, nodes, walker)
        _propagate_assignments(assignments, bindings, walker)
        yield from _emit_references(nodes, bindings, scope, walker)

    yield from scope(tree.body, {})


def _collect_declarations(nodes, parameters, walker):
    """Names the scope declares plus its assignment statements."""
    declared = set(parameters)
    assignments = []
    for node in nodes:
        walker.check()
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            for target in targets:
                walker.check()
                key = _name(target)
                if key:
                    assignments.append((key, node.value))
                    if isinstance(target, ast.Name):
                        declared.add(key)
        elif isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            declared.add(node.name)
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                declared.add(item.asname or item.name)
        elif isinstance(node, ast.Import):
            for item in node.names:
                declared.add(item.asname or item.name.split(".")[0])
    declared.discard("self")
    return declared, assignments


def _build_bindings(declared, inherited, nodes, walker):
    """Initial import/def bindings, each marked with its resolved-origin flag."""
    bindings: dict[str, tuple[str | None, bool]] = dict.fromkeys(
        declared, (None, False)
    )
    for key, value in inherited.items():
        walker.check()
        if key.partition(".")[0] not in declared:
            bindings[key] = value
    for node in nodes:
        walker.check()
        if isinstance(node, ast.ImportFrom):
            module = "." * node.level
            if node.module:
                module += node.module + "."
            for item in node.names:
                walker.check()
                imported = module + item.name
                bindings[item.asname or item.name] = (
                    imported,
                    not _sdk_module(imported),
                )
        elif isinstance(node, ast.Import):
            for item in node.names:
                walker.check()
                imported = (
                    item.name if item.asname else item.name.split(".")[0]
                )
                bindings[item.asname or item.name.split(".")[0]] = (
                    imported,
                    not _sdk_module(imported),
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings[node.name] = (node.name, True)
    return bindings


def _references_binding(value: str, key: str) -> bool:
    return value == key or value.startswith((key + ".", key + "()"))


def _propagate_assignments(assignments, bindings, walker):
    """Resolve aliases until the final binding of each assignment stops changing."""
    previous = None
    while True:
        resolved_bindings = {}
        for key, node in assignments:
            walker.check()
            value = _name(node)
            if not value or _references_binding(value, key):
                continue
            resolved, external = _resolve(value, bindings)
            if external or not _references_binding(resolved, key):
                binding = resolved, external
                resolved_bindings[key] = binding
                bindings[key] = binding
        if resolved_bindings == previous or not resolved_bindings:
            return
        previous = resolved_bindings


def _propagate_self_attributes(node, bindings, walker):
    """Collect self assignments from methods without inheriting method locals."""
    assignments = []
    for method in node.body:
        walker.check()
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes = walker.scope_nodes(method.body)
            _, method_assignments = _collect_declarations(nodes, (), walker)
            assignments.extend(
                (key, value) for key, value in method_assignments if key.startswith("self.")
            )
    class_bindings = dict(bindings)
    _propagate_assignments(assignments, class_bindings, walker)
    return class_bindings


def _emit_references(nodes, bindings, scope, walker):
    for node in nodes:
        walker.check()
        if isinstance(node, ast.ClassDef):
            yield from scope(
                node.body, _propagate_self_attributes(node, bindings, walker)
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            parameters = [
                arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            ]
            parameters += [
                arg.arg for arg in (args.vararg, args.kwarg) if arg is not None
            ]
            body = [node.body] if isinstance(node, ast.Lambda) else node.body
            yield from scope(body, bindings, parameters)
        elif isinstance(node, (ast.Attribute, ast.Call)):
            getattr_reference = _getattr_reference(node)
            if getattr_reference is not None:
                receiver, attribute = getattr_reference
                resolved, external = _resolve(_name(receiver), bindings)
                yield node, (resolved + "." + attribute, external)
            else:
                yield node, _resolve(
                    _name(node.func if isinstance(node, ast.Call) else node),
                    bindings,
                )


def iter_capability_sources(root: Path) -> Iterator[Path]:
    return (path for path in iter_plugin_python_files(root) if not _is_test_source(path.relative_to(root)))


def _is_test_source(relative: Path) -> bool:
    name = relative.name
    return (
        any(part in _TEST_DIR_NAMES for part in relative.parts[:-1])
        or name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


@dataclass
class CapabilitySources:
    files: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    size: int = 0


def read_capability_sources(root: Path, *, stop: Event | None = None) -> CapabilitySources:
    """Read a bounded snapshot and close source handles before parsing."""
    sources = CapabilitySources()
    try:
        for index, path in enumerate(iter_capability_sources(root)):
            if stop is not None and stop.is_set():
                break
            if index >= MAX_FILES or sources.size >= MAX_SOURCE_BYTES:
                sources.warnings.append({"code": "budget_exceeded"})
                break
            relative = path.relative_to(root).as_posix()
            try:
                size = path.stat().st_size
                if size > MAX_FILE_BYTES or sources.size + size > MAX_SOURCE_BYTES:
                    sources.warnings.append({"code": "source_too_large", "file": relative})
                    continue
                sources.size += size
                with tokenize.open(path) as stream:
                    source = stream.read(MAX_FILE_BYTES + 1)
                if len(source) > MAX_FILE_BYTES:
                    sources.warnings.append({"code": "source_too_large", "file": relative})
                    continue
                sources.files.append((relative, source))
            except (OSError, UnicodeError, SyntaxError, LookupError):
                sources.warnings.append({"code": "source_unreadable", "file": relative})
    except OSError:
        sources.warnings.append({"code": "source_unreadable"})
    return sources


def analyze_capabilities(
    root: Path, *, stop: Event | None = None, sources: CapabilitySources | None = None
) -> dict:
    walker = _AnalysisWalker(stop)
    sources = sources if sources is not None else read_capability_sources(root, stop=stop)
    evidence: list[dict] = []
    capabilities: set[str] = set()
    warnings = list(sources.warnings)
    count = 0
    try:
        for relative, source in sources.files:
            walker.check()
            try:
                tree = ast.parse(source, filename=relative)
            except (SyntaxError, RecursionError):
                warnings.append({"code": "source_unreadable", "file": relative})
                continue
            count += 1
            walker.check()
            seen: set[tuple[str, int]] = set()
            for node, (method, external) in _resolved_references(tree, walker):
                capability = None if external else _capability_for(method)
                if capability:
                    capabilities.add(capability)
                    key = (capability, node.lineno)
                    if key not in seen and len(evidence) < MAX_EVIDENCE:
                        seen.add(key)
                        evidence.append(
                            {
                                "capability": capability,
                                "method": method,
                                "file": relative,
                                "line": node.lineno,
                            }
                        )
            walker.check()
    except (_AnalysisBudgetExceeded, RecursionError):
        warnings.append({"code": "budget_exceeded"})
    status = "partial" if warnings else "completed" if count else "unavailable"
    return {
        "status": status,
        "capabilities": sorted(capabilities),
        "evidence": evidence,
        "warnings": warnings[:50],
        "files_analyzed": count,
        "source_bytes": sources.size,
    }
