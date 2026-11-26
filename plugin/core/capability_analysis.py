"""Bounded, import-free analysis of plugin-owned Python sources for disclosure."""

from __future__ import annotations

import ast
import os
import re
import tokenize
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event
from collections.abc import Callable, Iterator

from plugin.core.permission_declarations import (
    PermissionDeclarations,
    PermissionIssue,
    parse_permission_declarations,
)
from plugin.sdk.shared.permissions import LEGACY_PERMISSION_NAMES, PERMISSIONS
from plugin.utils.source_paths import is_vendor_sync_path, iter_plugin_python_files

# What the analyzer reports, in the earlier capability names that stored
# reports and approvals use; each maps to a permission id in
# plugin.sdk.shared.permissions.
CAPABILITIES = tuple(sorted(spec.legacy_name for spec in PERMISSIONS.values() if spec.legacy_name))
# Bump when a change to analysis makes persisted reports stale.
# 5: permission ids, declarations, blind spots; legacy capabilities unchanged.
# 6: run_in_executor(None, ...) is no longer a context_loss blind spot.
# 7: system.network:listen (servers moved out of system.network:connect).
# 8: blind spots split into user-facing (blind_spots, blind_spot_files),
#    not_analyzed hints and developer-only context_loss; literal
#    import_module("x") followed like an import.
# 9: constant getattr on an external origin followed through names and call
#    chains; AF_UNIX sockets and create_datagram_endpoint(local_addr=).
ANALYZER_VERSION = 9
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

    def poll(self) -> None:
        if self.stop is not None and self.stop.is_set():
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
    resolved, external, _bound = _resolve_with_origin(value, bindings)
    return resolved, external


def _resolve_with_origin(
    value: str, bindings: dict[str, tuple[str | None, bool]]
) -> tuple[str, bool, bool | None]:
    """Like ``_resolve``; the third item tells how the root name is bound.

    True: rewritten through a binding (an import, alias or definition).
    False: bound in this scope but not resolvable (a parameter, a local).
    None: not bound at all (a builtin or an undefined global).
    """
    # Bindings already contain resolved paths. A module segment in a resolved
    # path can share a name with a local variable, so never look it up again.
    ends = [match.start() for match in _REFERENCE_BOUNDARY.finditer(value)]
    ends.append(len(value))
    for end in reversed(ends):
        prefix = value[:end]
        if prefix in bindings:
            replacement, external = bindings[prefix]
            if replacement is None:
                return value, False, False
            if len(replacement) + len(value) - end > MAX_REFERENCE_LENGTH:
                raise _AnalysisBudgetExceeded
            return _extend(replacement, value[end:]), external, True
    return value, False, None


def _sdk_origin(path: str) -> bool:
    return path == "plugin.sdk" or path.startswith(("plugin.sdk.", "plugin.sdk("))


def _sdk_module(path: str) -> bool:
    return path == "plugin" or path.startswith("plugin.")


def _constant_getattr(node: ast.AST, bindings=None) -> tuple[ast.AST, str] | None:
    """(receiver, name) of the bare builtin getattr(x, "name") itself.

    Skipped when the plugin binds its own ``getattr`` (bindings given).
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and (bindings is None or "getattr" not in bindings)
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and node.args[1].value.isidentifier()
    ):
        return node.args[0], node.args[1].value
    return None


def _getattr_reference(node: ast.AST, bindings=None) -> tuple[ast.AST, str] | None:
    """getattr(x, "name") — optionally called — reads like x.name.

    Skipped when the plugin binds its own ``getattr`` (bindings given).
    """
    if not isinstance(node, ast.Call):
        return None
    found = _constant_getattr(node, bindings)
    if found is not None:
        return found
    if isinstance(node.func, ast.Call):
        return _getattr_reference(node.func, bindings)
    return None


def _plain_name(node: ast.AST) -> str:
    """Like _name, spelling a constant getattr(x, "a") as x.a."""
    parts = []
    length = 0
    while True:
        found = _constant_getattr(node)
        if found is not None:
            node, attribute = found
            parts.extend((attribute, "."))
            length += len(attribute) + 1
        elif isinstance(node, ast.Attribute):
            parts.extend((node.attr, "."))
            length += len(node.attr) + 1
            node = node.value
        elif isinstance(node, ast.Call):
            parts.append("()")
            length += 2
            node = node.func
        else:
            break
        if length > MAX_REFERENCE_LENGTH:
            raise _AnalysisBudgetExceeded
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return "".join(reversed(parts))


class _Reflected(str):
    """A resolved path that went through a followed constant getattr.

    ``legacy`` is the path as it resolved before getattr was followed (rooted
    in the unresolved "getattr()"); the legacy capability scan keeps reading
    that one, so its result does not change.
    """

    def __new__(cls, value: str, legacy: str):
        path = super().__new__(cls, value)
        path.legacy = legacy
        return path


def _extend(resolved: str, suffix: str) -> str:
    """resolved + suffix, keeping a _Reflected path's legacy spelling."""
    if isinstance(resolved, _Reflected):
        return _Reflected(resolved + suffix, resolved.legacy + suffix)
    return resolved + suffix


def _legacy(method: str, external: bool) -> tuple[str, bool]:
    """(method, external) as the legacy capability scan reads them."""
    if isinstance(method, _Reflected):
        # Rooted in the bare, unresolved getattr: never external.
        return str(method.legacy), False
    return method, external


# A socket.socket(AF_UNIX) object in a resolved path. "[" never appears in a
# name built by _name, so this cannot collide with source spelling; it is
# turned back into "socket.socket()" before a reference is reported.
_UNIX_SOCKET = "socket.socket[AF_UNIX]()"
_UNIX_FAMILIES = frozenset({"socket.AF_UNIX", "socket.AddressFamily.AF_UNIX"})
# asyncio loop.create_datagram_endpoint(); family= is keyword-only there.
_DATAGRAM_ENDPOINTS = frozenset(
    f"asyncio.{loop}().create_datagram_endpoint"
    for loop in ("get_event_loop", "get_running_loop", "new_event_loop")
)


def _dotted_name(node: ast.AST | None, walker=None) -> str:
    """a.b.c for a plain dotted name, else "" (no calls, so the walk is
    as long as the name; charged to the budget when a walker is given)."""
    parts = []
    while isinstance(node, ast.Attribute):
        if walker is not None:
            walker.check()
        parts.append(node.attr)
        node = node.value
        if len(parts) > MAX_REFERENCE_LENGTH:
            raise _AnalysisBudgetExceeded
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


# Bindings key marking a name whose value is not certain for the AF_UNIX
# check (rebound, assigned more than once ...). "[" never starts a name, so
# it cannot collide with a real binding; other lookups never see it.
_UNTRUSTED = "[untrusted]"


def _untrusted(name: str, bindings) -> bool:
    return _UNTRUSTED + name in bindings


def _unix_family(call: ast.Call, index: int | None, bindings, walker=None) -> bool:
    """The family argument statically resolves to socket.AF_UNIX, through
    names that are not rebound (for socket in ...: socket.AF_UNIX is not)."""
    argument = _call_argument(call, index, "family")
    # Cheap test first: this runs at every call level of every chain.
    # A bare name may be an alias (family = socket.AF_UNIX); a dotted one
    # must end in AF_UNIX.
    if not (isinstance(argument, ast.Name) or (isinstance(argument, ast.Attribute) and argument.attr == "AF_UNIX")):
        return False
    if index == 0 and (
        len(call.args) > 3
        or any(isinstance(item, ast.Starred) for item in call.args)
        or any(item.arg in ("fileno", None) for item in call.keywords)
    ):
        # socket.socket(..., fileno=fd) wraps whatever socket fd is; the
        # family only labels it (and *args / **kwargs may carry fileno).
        return False
    raw = _dotted_name(argument, walker)
    if not raw or _untrusted(_root_name(raw), bindings):
        return False
    resolved, external, bound = _resolve_with_origin(raw, bindings)
    return bool(external and bound) and resolved in _UNIX_FAMILIES


def _local_construction(node: ast.Call, called: str, external, bound, bindings) -> bool:
    """socket.socket(AF_UNIX) or create_datagram_endpoint(family=AF_UNIX)
    itself (family is keyword-only on create_datagram_endpoint)."""
    if not (external and bound):
        return False
    if called == "socket.socket":
        return _unix_family(node, 0, bindings)
    return called in _DATAGRAM_ENDPOINTS and _unix_family(node, None, bindings)


def _unmark_local_socket(resolved: str) -> tuple[str, bool]:
    if resolved.startswith(_UNIX_SOCKET):
        plain = "socket.socket()" + resolved[len(_UNIX_SOCKET):]
        if isinstance(resolved, _Reflected):
            plain = _Reflected(plain, resolved.legacy)
        return plain, True
    return resolved, False


_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_RELATIVE_MODULE_NAME = re.compile(r"(\.+)((?:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)?)")


def _absolute_module(name: str, package: str | None) -> str | None:
    """importlib's resolution of a relative name against a literal package;
    None when it cannot be proven (no package, or one the dots climb out of)."""
    match = _RELATIVE_MODULE_NAME.fullmatch(name)
    if match is None or package is None or not _MODULE_NAME.fullmatch(package):
        return None
    level, rest = len(match.group(1)), match.group(2)
    bits = package.rsplit(".", level - 1)
    if len(bits) < level:
        return None
    base = bits[0]
    return f"{base}.{rest}" if rest else base


def _literal_import(node: ast.AST, bindings) -> tuple[str, str] | None:
    """(module loaded, module returned) of a literal dynamic import, which is
    followed like an import.

    importlib.import_module("x") (any binding of it) loads and returns x; a
    relative name is resolved against a literal ``package``. The bare
    builtin __import__("x.y") with no other argument loads x.y and returns
    the top-level x. Any other form (a non-literal name or package, a
    rebound import function) is None and stays a dynamic_import blind spot.
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    # Only a plain name or name.attr (module.import_module, or an alias such
    # as self.load) can resolve to these functions; a cheap test first, as
    # this runs at every level of every call chain.
    if isinstance(func, ast.Name):
        if func.id != "__import__" and func.id not in bindings:
            return None
    elif not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and (func.attr == "import_module" or f"{func.value.id}.{func.attr}" in bindings)
    ):
        return None
    resolved, external, bound = _resolve_with_origin(_name(func), bindings)
    # Not through a followed getattr (im = getattr(importlib, "import_module")):
    # that stays a dynamic_import blind spot, as getattr(...)("x") does.
    if bound and external and resolved == "importlib.import_module" and not isinstance(resolved, _Reflected):
        if any(item.arg not in ("name", "package") for item in node.keywords) or len(node.args) > 2:
            return None
        module = _literal_string(_call_argument(node, 0, "name"))
        if module is None:
            return None
        if module.startswith("."):
            package = _call_argument(node, 1, "package")
            module = _absolute_module(module, _literal_string(package))
            if module is None:
                return None
        elif not _MODULE_NAME.fullmatch(module):
            return None
        return module, module
    if isinstance(func, ast.Name) and func.id == "__import__" and bound is None:
        if len(node.args) != 1 or node.keywords:
            return None
        module = _literal_string(node.args[0])
        if module is None or not _MODULE_NAME.fullmatch(module):
            return None
        return module, module.split(".", 1)[0]
    return None


def _external_getattr(node: ast.AST, bindings, walker) -> str | None:
    """getattr(x, "name") itself, as x.name, when x has an external origin.

    Only external origins are followed, and the result is a _Reflected path,
    so the legacy capability scan (which reads non-external references)
    sees what it saw before getattr was followed.
    """
    found = _constant_getattr(node, bindings)
    if found is None:
        return None
    receiver, attribute = found
    inner = _resolve_chain_root(receiver, bindings, walker)
    resolved, external, bound = inner[:3] if inner else _resolve_with_origin(_name(receiver), bindings)
    if not (external and bound and resolved):
        return None
    if len(resolved) + len(attribute) + 1 > MAX_REFERENCE_LENGTH:
        raise _AnalysisBudgetExceeded
    # Before: the unresolved "getattr()" (_name of this call).
    return _Reflected(resolved + "." + attribute, "getattr()")


# Kinds of chain root found by _resolve_chain_root.
_ROOT_IMPORT = "import"
_ROOT_GETATTR = "getattr"
_ROOT_SOCKET = "socket"


def _resolve_chain_root(raw_node: ast.AST, bindings, walker) -> tuple[str, bool, bool, str] | None:
    """Resolve an attribute / call chain rooted in something _name cannot
    spell, as the equivalent plain spelling:

    - a literal dynamic import (importlib.import_module("os").system()), as
      if the module were imported;
    - a constant getattr on an external origin (getattr(pathlib, "Path")()
      .write_text()), as the attribute access;
    - socket.socket(socket.AF_UNIX) (see _UNIX_SOCKET).

    The fourth item is the kind of root (_ROOT_*). Each part of the chain is
    resolved at most once, so nested forms stay linear.
    """
    parts = []
    node = raw_node
    while isinstance(node, (ast.Attribute, ast.Call)):
        walker.check()
        root = None
        literal = _literal_import(node, bindings)
        if literal is not None:
            root = literal[1], not _sdk_module(literal[1]), _ROOT_IMPORT
        else:
            reflected = _external_getattr(node, bindings, walker)
            if reflected is not None:
                root = reflected, True, _ROOT_GETATTR
            elif isinstance(node, ast.Call) and _unix_family(node, 0, bindings, walker):
                func = node.func
                through_getattr = _constant_getattr(func, bindings) is not None
                if through_getattr:
                    called = _external_getattr(func, bindings, walker)
                    if called is None:
                        # The rest of the chain is that getattr: unresolved.
                        return None
                elif isinstance(func, (ast.Name, ast.Attribute)):
                    # Only a plain dotted name (socket.socket, sock, self.make),
                    # or an attribute of a literal import; a longer chain is
                    # left to the next steps of this walk.
                    dotted = _dotted_name(func, walker)
                    called, external, bound = _resolve_with_origin(dotted, bindings) if dotted else (None, False, False)
                    if not dotted and isinstance(func, ast.Attribute):
                        imported = _literal_import(func.value, bindings)
                        if imported is not None:
                            called, external, bound = f"{imported[1]}.{func.attr}", True, True
                    if not (external and bound):
                        called = None
                else:
                    called = None
                if called == "socket.socket":
                    root = _extend(called, "()")
                    root = (
                        _Reflected(_UNIX_SOCKET, root.legacy) if isinstance(root, _Reflected) else _UNIX_SOCKET,
                        True,
                        _ROOT_SOCKET,
                    )
                elif through_getattr:
                    # Resolved already; the next step would repeat it.
                    root = _extend(called, "()"), True, _ROOT_GETATTR
        if root is not None:
            prefix, external, kind = root
            resolved = _extend(prefix, "".join(reversed(parts)))
            if len(resolved) > MAX_REFERENCE_LENGTH:
                raise _AnalysisBudgetExceeded
            return resolved, external, True, kind
        if isinstance(node, ast.Attribute):
            parts.extend((node.attr, "."))
            node = node.value
        else:
            parts.append("()")
            node = node.func
        if len(parts) > MAX_REFERENCE_LENGTH:
            raise _AnalysisBudgetExceeded
    return None


@dataclass(frozen=True, eq=False)
class _Reference:
    """One resolved reference, with what the permission scan needs on top."""

    node: ast.AST
    method: str
    external: bool
    # See _resolve_with_origin.
    bound: bool | None
    function: str
    is_call: bool
    # The root name is also rebound in this scope by a loop, with, except,
    # walrus, unpacking or unresolvable assignment target, so the resolved
    # origin is not certain.
    rebound: bool = False
    # Root name as written in the source, before alias resolution.
    raw_root: str = ""
    # Only stored into or deleted from (x[k] = v, del x[k]), never read.
    written: bool = False
    # The right side of an `in` / `not in` test.
    membership: bool = False
    # The root is an alias of a name bound locally (a parameter, a loop
    # target ...) that shares a builtin's spelling, not of the builtin.
    shadowed_builtin_alias: bool = False
    # The root name holds a value read from the environment earlier
    # (tok = os.environ.get()), whose own line is already the evidence. Other
    # stored call results (p = Path(); p.write_text()) still count.
    via_call_result: bool = False
    # A literal dynamic import (importlib.import_module("x")): the module,
    # reported like an import; the call itself is not a blind spot.
    imported: str = ""
    # An AF_UNIX socket (or datagram endpoint): it only reaches local socket
    # files, so its connect / listen evidence is possible, not definite.
    local_socket: bool = False


def _resolved_references(tree, walker: _AnalysisWalker):
    """Yield ``(node, (method, external))`` like before; see _references."""
    for reference in _references(tree, walker):
        if not isinstance(reference.node, (ast.Import, ast.ImportFrom)):
            yield reference.node, (reference.method, reference.external)


def _references(tree, walker: _AnalysisWalker):
    def scope(body, inherited, parameters=(), function="", outer_rebound=frozenset()):
        nodes = list(walker.scope_nodes(body))
        declared, assignments = _collect_declarations(nodes, parameters, walker)
        bindings = _build_bindings(declared, inherited, nodes, walker)
        # Names rebound here, plus those inherited from enclosing scopes that
        # this scope does not bind itself (it may import its own copy).
        rebound = _derived_rebound(
            _rebound_names(nodes, walker) | (outer_rebound - declared), assignments, walker
        )
        comprehension_locals = _comprehension_locals(nodes, walker)
        # Also assigned and imported here, or bound by a comprehension in
        # this scope: not known to keep one value (only weakens local-socket
        # tracking; the aliases themselves resolve as before).
        assigned = {key for key, _value in assignments}
        imported = {
            item.asname or item.name.split(".")[0]
            for node in nodes
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for item in node.names
        } & assigned
        comprehended = set().union(*comprehension_locals.values()) if comprehension_locals else set()
        # Names a match pattern captures (case s:, case [*s], case {**s}).
        captured = {
            name
            for node in nodes
            if isinstance(node, (ast.MatchAs, ast.MatchStar, ast.MatchMapping))
            for name in ((node.rest,) if isinstance(node, ast.MatchMapping) else (node.name,))
            if name
        }
        # Parameters may already hold another socket (def f(sock): if sock
        # is None: sock = socket.socket(AF_UNIX)).
        _propagate_assignments(
            assignments, bindings, walker,
            rebound | shared | imported | comprehended | captured | set(parameters),
        )
        own = {id(node) for node in nodes}
        written = _written_targets(nodes, walker)
        membership = {
            id(comparator)
            for node in nodes
            if isinstance(node, ast.Compare)
            for op, comparator in zip(node.ops, node.comparators)
            if isinstance(op, (ast.In, ast.NotIn))
        }
        # e = eval where eval is a parameter or loop target here: not the builtin.
        shadowed_aliases = {
            key
            for key, value in assignments
            if isinstance(value, ast.Name)
            and value.id in _BUILTINS
            and (value.id in rebound or (value.id in bindings and bindings[value.id][0] is None))
        }
        # ... and aliases of those aliases (b = a).
        grown = True
        while grown:
            grown = False
            for key, value in assignments:
                walker.check()
                if isinstance(value, ast.Name) and value.id in shadowed_aliases and key not in shadowed_aliases:
                    shadowed_aliases.add(key)
                    grown = True

        def nested(body, inherited, parameters=(), function="", owner=None):
            # A function or lambda defined inside a comprehension also sees
            # the comprehension's own names.
            extra = comprehension_locals.get(id(owner), frozenset()) if owner is not None else frozenset()
            return scope(body, inherited, parameters, function, rebound | extra)

        for reference in _emit_references(nodes, bindings, nested, walker, function, shared):
            # Nested scopes already applied their own view of rebinding.
            if id(reference.node) in own:
                changes = {}
                if not reference.rebound and (
                    reference.raw_root in rebound
                    or reference.raw_root in comprehension_locals.get(id(reference.node), ())
                ):
                    changes["rebound"] = True
                if id(reference.node) in written:
                    changes["written"] = True
                if id(reference.node) in membership:
                    changes["membership"] = True
                if reference.raw_root in shadowed_aliases:
                    changes["shadowed_builtin_alias"] = True
                if changes:
                    reference = replace(reference, **changes)
            yield reference

    shared = _shared_names(tree, walker)
    yield from scope(tree.body, {})


def _shared_names(tree, walker) -> frozenset[str]:
    """Names a global / nonlocal statement lets another scope assign.

    File-wide on purpose: a name in any global / nonlocal statement is
    treated as possibly reassigned in every scope. That only keeps sockets
    from being treated as local (the safe side). One extra linear pass; it
    honours the stop event but is not charged to the node budget, which the
    scopes already count.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        walker.poll()
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return frozenset(names)


def _written_targets(nodes, walker) -> set[int]:
    """Values that are only changed, not read: x[k] = v, del x[k], x.update(...).

    For a mutating method call both the receiver and the bound method count.
    """
    # x[k] += v reads x[k] before storing it, so it is not write-only.
    augmented = {id(node.target) for node in nodes if isinstance(node, ast.AugAssign)}
    written = set()
    for node in nodes:
        walker.check()
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and id(node) not in augmented
        ):
            written.add(id(node.value))
        elif isinstance(node, ast.Attribute) and node.attr in _DATA_WRITE_METHODS:
            # x.update(...) or taking x.update: the receiver is changed, not read.
            written.update((id(node), id(node.value)))
    return written


def _root_name(method: str) -> str:
    return re.split(r"[.(]", method, maxsplit=1)[0]


def _target_names(target, names: set[str]) -> None:
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            _target_names(item, names)
    elif isinstance(target, ast.Starred):
        _target_names(target.value, names)


def _rebound_names(nodes, walker) -> frozenset[str]:
    """Names (re)bound by targets the alias resolution does not follow.

    Only used to weaken permission evidence; the legacy capability scan keeps
    resolving these names exactly as before.
    """
    names: set[str] = set()
    for node in nodes:
        walker.check()
        if isinstance(node, (ast.For, ast.AsyncFor)):
            # Comprehension targets stay inside the comprehension; see
            # _comprehension_locals.
            _target_names(node.target, names)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    _target_names(item.optional_vars, names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.NamedExpr):
            _target_names(node.target, names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if node.value is None:
                # A bare annotation (requests: Any) binds nothing.
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            # A value the alias resolution cannot follow (a lambda, a
            # conditional, a subscript ...) leaves the previous binding in
            # place, so the name's origin is no longer certain.
            # A constant (the optional-import fallback "requests = None") is
            # not an alternative origin worth weakening the import for.
            opaque = (
                isinstance(node, ast.AugAssign)
                or (not _name(node.value) and not isinstance(node.value, ast.Constant))
            )
            for target in targets:
                if isinstance(target, (ast.Tuple, ast.List)) or (opaque and isinstance(target, ast.Name)):
                    _target_names(target, names)
    return frozenset(names)


def _is_data_read_result(resolved: str) -> bool:
    return "()" in resolved and _matches(resolved.split("(", 1)[0], _DATA_READS + ("os.getenv", "os.getenvb"))


def _comprehension_locals(nodes, walker) -> dict[int, frozenset[str]]:
    """Node id -> names a comprehension binds around that node."""
    local: dict[int, set[str]] = {}
    for node in nodes:
        walker.check()
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            names: set[str] = set()
            for generator in node.generators:
                _target_names(generator.target, names)
            # The first iterable is evaluated in the enclosing scope.
            outer = {id(child) for child in ast.walk(node.generators[0].iter)}
            for child in ast.walk(node):
                walker.check()
                if id(child) not in outer:
                    local.setdefault(id(child), set()).update(names)
    return {key: frozenset(value) for key, value in local.items()}


def _derived_rebound(rebound: frozenset[str], assignments, walker) -> frozenset[str]:
    """Aliases of rebound names are uncertain too (run = subprocess.run)."""
    names = set(rebound)
    changed = True
    while changed:
        changed = False
        for key, value in assignments:
            walker.check()
            if "." in key or key in names:
                continue
            if _root_name(_plain_name(value)) in names:
                names.add(key)
                changed = True
    return frozenset(names)


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
        if key.startswith(_UNTRUSTED):
            # A name this scope binds itself is judged here, not by the
            # enclosing scope's rebinding of the same spelling.
            if key[len(_UNTRUSTED):] not in declared:
                bindings[key] = value
        elif key.partition(".")[0] not in declared:
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


def _propagate_assignments(assignments, bindings, walker, uncertain_names=frozenset()):
    """Resolve aliases until the final binding of each assignment stops changing.

    ``uncertain_names`` are also bound by other targets (loop, with ...) or
    from other scopes (global / nonlocal).
    """
    counts: dict[str, int] = {}
    for key, _node in assignments:
        counts[key] = counts.get(key, 0) + 1
    # Bound more than once: the name may hold another socket, or another
    # value than the AF_UNIX family, so it is not known to be local.
    uncertain = {key for key, count in counts.items() if count > 1} | set(uncertain_names)
    # A family name already bound (imported or inherited) and also assigned
    # here (from socket import AF_UNIX; AF_UNIX = 2).
    uncertain.update(key for key in counts if bindings.get(key, (None,))[0] in _UNIX_FAMILIES)
    # self.x already bound by the class to something other than this local
    # socket (another method sets it, or it is set through setattr).
    uncertain.update(
        key
        for key in counts
        if "." in key and key in bindings and not (bindings[key][0] or "").startswith(_UNIX_SOCKET)
    )
    # Root name each plain-name assignment reads from (alias = family).
    roots: dict[str, str] = {}
    readers: dict[str, list[str]] = {}
    for key, node in assignments:
        walker.check()
        if "." not in key:
            root = _root_name(_plain_name(node))
            if root:
                readers.setdefault(root, []).append(key)
                roots[key] = root
    # Aliases of an uncertain name are uncertain too.
    pending = list(uncertain)
    while pending:
        walker.check()
        for reader in readers.get(pending.pop(), ()):
            if reader not in uncertain:
                uncertain.add(reader)
                pending.append(reader)
    # Settled before any socket is resolved through them. Kept apart from
    # the bindings, which resolve as before (the legacy scan reads them).
    for key in uncertain:
        if "." not in key:
            bindings[_UNTRUSTED + key] = (None, False)
    # Assignments whose getattr / socket root leads back to themselves
    # (b = getattr(a, "x"); a = getattr(b, "y")) would grow on every round;
    # they keep the reading they had before getattr was followed. Each name
    # is walked once (white / on the current path / done).
    cyclic: set[str] = set()
    done: set[str] = set()
    for start in roots:
        path: list[str] = []
        on_path: set[str] = set()
        name = start
        while name in roots and name not in done and name not in on_path:
            walker.check()
            path.append(name)
            on_path.add(name)
            name = roots[name]
        if name in on_path:
            cyclic.update(path[path.index(name):])
        done.update(path)

    def bind(key, resolved, external):
        if key in uncertain:
            resolved = _unmark_local_socket(resolved)[0]
        binding = resolved, external
        resolved_bindings[key] = binding
        bindings[key] = binding

    # What each assigned name was bound to before propagation; see below.
    before = {key: bindings.get(key) for key in counts}
    rounds = 0
    previous = None
    while True:
        rounds += 1
        resolved_bindings = {}
        for key, node in assignments:
            walker.check()
            literal = _resolve_chain_root(node, bindings, walker)
            # As with a plain x = x.attr, a getattr or socket rooted in the
            # name it is assigned to is not followed (it would grow forever);
            # the binding stays what it was before getattr was followed.
            if literal is not None and (
                literal[3] == _ROOT_IMPORT
                or (key not in cyclic and not _references_binding(_plain_name(node), key))
            ):
                bind(key, literal[0], literal[1])
                continue
            value = _name(node)
            if not value or _references_binding(value, key):
                continue
            resolved, external = _resolve(value, bindings)
            if external or not _references_binding(resolved, key):
                bind(key, resolved, external)
        # A _Reflected path equals its plain text; compare the legacy
        # spelling too, or an alias chain could stop one round early.
        state = {key: (value, getattr(value[0], "legacy", None)) for key, value in resolved_bindings.items()}
        if state == previous or not resolved_bindings:
            return
        if rounds > 2 * len(assignments) + 2:
            # An alias chain settles within one round per assignment. Still
            # changing after that is an indirect cycle that grows every round
            # (import pathlib as a; b = a.Path; a = b.cwd); its names keep
            # what they were bound to before.
            for key, value in state.items():
                if previous is None or previous.get(key) != value:
                    if before[key] is None:
                        bindings.pop(key, None)
                    else:
                        bindings[key] = before[key]
            return
        previous = state


def _propagate_self_attributes(node, bindings, walker, shared=frozenset()):
    """Collect self assignments from methods without inheriting method locals."""
    assignments = []
    # Method parameters and locals are not shadowed here (as before), so one
    # spelled like a family name is only kept out of the AF_UNIX check.
    parameters: set[str] = set()
    set_through: set[str] = set()
    writes: dict[str, int] = {}
    for method in node.body:
        walker.check()
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = method.args
            parameters.update(
                arg.arg
                for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
                if arg is not None
            )
            nodes = list(walker.scope_nodes(method.body))
            _, method_assignments = _collect_declarations(nodes, (), walker)
            assignments.extend(
                (key, value) for key, value in method_assignments if key.startswith("self.")
            )
            # Method locals (AF_UNIX = socket.AF_INET) shadow module names too.
            parameters.update(key for key, _value in method_assignments if "." not in key)
            _self_writes(method, writes, set_through, walker)
    # self.x written in a way the alias resolution does not follow (a tuple,
    # for / with / augmented target, a nested function, setattr): another
    # value may be stored there. Cross-class writes (o.x = ..., a subclass)
    # are not seen either; the local-socket downgrade accepts that.
    plain: dict[str, int] = {}
    for key, _value in assignments:
        plain[key] = plain.get(key, 0) + 1
    set_through.update(key for key, count in writes.items() if count > plain.get(key, 0))
    if "*" in set_through:
        set_through.update(key for key, _value in assignments)
    class_bindings = dict(bindings)
    for name in parameters:
        class_bindings[_UNTRUSTED + name] = (None, False)
    _propagate_assignments(assignments, class_bindings, walker, shared | set_through)
    for name in parameters:
        # Methods see their own parameters; do not distrust module names there.
        if not _untrusted(name, bindings):
            del class_bindings[_UNTRUSTED + name]
    return class_bindings


def _self_writes(method, writes: dict[str, int], set_through: set[str], walker) -> None:
    """Count every store to self.x in a method, nested scopes included, and
    note setattr(self, ...) and aliases of self ("*": any attribute)."""
    for node in ast.walk(method):
        walker.poll()
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            key = f"self.{node.attr}"
            writes[key] = writes.get(key, 0) + 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("setattr", "delattr"):
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "self":
                attribute = _literal_string(node.args[1]) if len(node.args) > 1 else None
                set_through.add(f"self.{attribute}" if attribute else "*")
        elif isinstance(node, ast.Attribute) and node.attr == "__dict__" and isinstance(node.value, ast.Name) and node.value.id == "self":
            set_through.add("*")
        elif (
            isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            # me = self; me.x = ...
            set_through.add("*")


def _qualified(function: str, name: str) -> str:
    return f"{function}.{name}" if function else name


def _emit_references(nodes, bindings, scope, walker, function="", shared=frozenset()):
    for node in nodes:
        walker.check()
        if isinstance(node, ast.ClassDef):
            yield from scope(
                node.body,
                _propagate_self_attributes(node, bindings, walker, shared),
                function=_qualified(function, node.name),
                owner=node,
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
            name = function if isinstance(node, ast.Lambda) else _qualified(function, node.name)
            yield from scope(body, bindings, parameters, function=name, owner=node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            yield _Reference(node, "", False, None, function, False)
        elif isinstance(node, (ast.Attribute, ast.Call)):
            getattr_reference = _getattr_reference(node, bindings)
            if getattr_reference is not None:
                receiver, attribute = getattr_reference
                raw = _name(receiver)
                inner = _resolve_chain_root(receiver, bindings, walker)
                resolved, external, bound = inner[:3] if inner else _resolve_with_origin(raw, bindings)
                method, local_socket = _unmark_local_socket(_extend(resolved, "." + attribute))
                # getattr(x, "f") only retrieves f; getattr(x, "f")() calls it.
                is_call = isinstance(node, ast.Call) and isinstance(node.func, ast.Call)
                if not local_socket and is_call:
                    # getattr(socket, "socket")(socket.AF_UNIX), and the same
                    # for a datagram endpoint (as the attribute call below).
                    local_socket = _local_construction(node, method, external, bound, bindings)
                yield _Reference(
                    node, method, external, bound, function, is_call,
                    raw_root=_root_name(_plain_name(receiver)) if inner and inner[3] != _ROOT_IMPORT else _root_name(raw),
                    local_socket=local_socket,
                )
            else:
                target = node.func if isinstance(node, ast.Call) else node
                raw = _name(target)
                raw_root = _root_name(raw)
                literal = _resolve_chain_root(target, bindings, walker)
                if literal is not None:
                    resolved, external, bound, kind = literal
                    via_call_result = False
                    if kind != _ROOT_IMPORT:
                        raw_root = _root_name(_plain_name(target))
                        # As for the attribute (tok = os.environ.get("X");
                        # getattr(tok, "strip")()): the read is already reported.
                        via_call_result = _is_data_read_result(_resolve(raw_root, bindings)[0])
                else:
                    resolved, external, bound = _resolve_with_origin(raw, bindings)
                    via_call_result = (
                        bool(bound)
                        and raw_root != raw
                        and _is_data_read_result(_resolve(raw_root, bindings)[0])
                    )
                resolved, local_socket = _unmark_local_socket(resolved)
                if not local_socket and isinstance(node, ast.Call):
                    local_socket = _local_construction(node, resolved, external, bound, bindings)
                yield _Reference(
                    node, resolved, external, bound, function,
                    isinstance(node, ast.Call),
                    raw_root=raw_root,
                    via_call_result=via_call_result,
                    imported=(_literal_import(node, bindings) or ("",))[0],
                    local_socket=local_socket,
                )
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            # A bare name bound to a data attribute (from os import environ)
            # reads it; other bare names are left to the attribute and call
            # references so the legacy capability scan is unchanged.
            resolved, external, bound = _resolve_with_origin(node.id, bindings)
            # Only the mapping itself, not a stored result (tok = os.environ.get()).
            if external and bound and (resolved in _DATA_READS or resolved == "sys.modules"):
                yield _Reference(node, resolved, external, bound, function, False, raw_root=node.id)


def iter_capability_sources(root: Path) -> Iterator[Path]:
    return (
        path for path in iter_plugin_python_files(root, ordered=True)
        if not _is_test_source(path.relative_to(root))
    )


def _is_test_source(relative: Path) -> bool:
    name = relative.name
    return (
        any(part in _TEST_DIR_NAMES for part in relative.parts[:-1])
        or name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


# Legacy capability names (still used by the consent flow) and the permission
# id each one is reported under in ``permissions``.
LEGACY_CAPABILITY_PERMISSIONS = LEGACY_PERMISSION_NAMES
assert set(LEGACY_CAPABILITY_PERMISSIONS) == set(CAPABILITIES)

# How a permission finding was established, strongest first.
# resolved_call: a call whose target resolves to an SDK / standard-library
# origin. resolved_reference: reading a resolved data attribute such as
# os.environ. resolved_name: a resolved callable or type only named (a type
# check, an alias, a base class), not called here. suffix_match: the name
# matches but its receiver is not resolved. import_only: only the import.
BASIS_RESOLVED_CALL = "resolved_call"
BASIS_RESOLVED_REFERENCE = "resolved_reference"
BASIS_RESOLVED_NAME = "resolved_name"
BASIS_SUFFIX_MATCH = "suffix_match"
BASIS_IMPORT_ONLY = "import_only"
BASIS_DECLARED_ONLY = "declared_only"
_BASIS_RANK = {
    BASIS_RESOLVED_CALL: 5,
    BASIS_RESOLVED_REFERENCE: 4,
    BASIS_RESOLVED_NAME: 3,
    BASIS_SUFFIX_MATCH: 2,
    BASIS_IMPORT_ONLY: 1,
}
DIRECT_BASES = frozenset({BASIS_RESOLVED_CALL, BASIS_RESOLVED_REFERENCE})
# Static evidence for these is never definite: the path may be inside the
# plugin's own data directory, which needs no permission.
POSSIBLE_ONLY_PERMISSIONS = frozenset({"system.fs:read", "system.fs:write"})
MAX_PERMISSION_EVIDENCE = 50
MAX_BLIND_SPOTS = 200

BLIND_SPOT_TYPES = (
    "dynamic_import",
    "reflective_access",
    "code_exec",
    "native_loader",
    "context_loss",
    "not_analyzed",
)
# Blind spots that are counted and shown to users. context_loss is only for
# developers (neko-plugin check); not_analyzed is a hint, never counted.
COUNTED_BLIND_SPOT_TYPES = (
    "dynamic_import",
    "reflective_access",
    "code_exec",
    "native_loader",
)
# Minimal high-risk marker for the permission panel. A shared risk-tier table
# for permissions and blind spots is planned to replace this set.
HIGH_RISK_BLIND_SPOT_TYPES = frozenset({"native_loader"})

_MODELS = "plugin_sdk.models:invoke"
_NETWORK = "system.network:connect"
_LISTEN = "system.network:listen"
_PROCESS = "system.process:spawn"
_ENV = "system.env:read"
_FS_READ = "system.fs:read"
_FS_WRITE = "system.fs:write"

# Methods that make a plain socket accept connections. They continue the
# "socket.socket" network pattern, so _NOT_UNDER keeps them out of it.
_SOCKET_LISTEN_METHODS = ("bind", "listen", "accept")
# Only a hint: a client may bind a local address before connecting (a UDP
# server binds without listen, so it is not dropped either).
_HINT_ONLY = ("socket.socket().bind",)

# Standard-library and well-known third-party origins. A reference matches a
# pattern when it equals it or continues it with "." or "()".
_EXTERNAL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        _NETWORK,
        (
            "socket.socket",
            "socket.create_connection",
            "urllib.request.urlopen",
            "urllib.request.build_opener",
            "urllib.request.urlretrieve",
            "http.client.HTTPConnection",
            "http.client.HTTPSConnection",
            "asyncio.open_connection",
            "ftplib.FTP",
            "ftplib.FTP_TLS",
            "smtplib.SMTP",
            "smtplib.SMTP_SSL",
            "imaplib.IMAP4",
            "imaplib.IMAP4_SSL",
            "poplib.POP3",
            "poplib.POP3_SSL",
            *(
                f"{module}.{name}"
                for module in ("requests", "httpx")
                for name in ("get", "post", "put", "patch", "delete", "head", "options", "request")
            ),
            "requests.Session",
            "requests.session",
            "httpx.stream",
            "httpx.Client",
            "httpx.AsyncClient",
            "aiohttp.ClientSession",
            "aiohttp.request",
            "websockets.connect",
            "websockets.client.connect",
            "websockets.asyncio.client.connect",
            "websockets.sync.client.connect",
            "websocket.create_connection",
            "websocket.WebSocketApp",
            "urllib3.PoolManager",
            "urllib3.request",
        ),
    ),
    (
        _LISTEN,
        (
            "socket.create_server",
            # A socket stored in a name resolves through it (s = socket.socket();
            # s.bind(...)); a with-target or a parameter does not.
            *(f"socket.socket().{method}" for method in _SOCKET_LISTEN_METHODS),
            "asyncio.start_server",
            *(
                f"asyncio.{loop}().create_server"
                for loop in ("get_event_loop", "get_running_loop", "new_event_loop")
            ),
            "http.server.HTTPServer",
            "http.server.ThreadingHTTPServer",
            *(
                f"socketserver.{kind}{protocol}Server"
                for kind in ("", "Threading", "Forking")
                for protocol in ("TCP", "UDP")
            ),
            "wsgiref.simple_server.make_server",
            "wsgiref.simple_server.WSGIServer",
            "xmlrpc.server.SimpleXMLRPCServer",
            "xmlrpc.server.DocXMLRPCServer",
            "websockets.serve",
            "websockets.server.serve",
            "websockets.asyncio.server.serve",
            "websockets.sync.server.serve",
            "websockets.asyncio.server.Server",
            "aiohttp.web.run_app",
            "aiohttp.web.TCPSite",
            "aiohttp.web.SockSite",
            "hypercorn.asyncio.serve",
            "hypercorn.trio.serve",
            "uvicorn.run",
            "uvicorn.Server",
            "flask.Flask().run",
        ),
    ),
    (
        _PROCESS,
        (
            "subprocess.Popen",
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.getoutput",
            "subprocess.getstatusoutput",
            "os.system",
            "os.popen",
            "os.posix_spawn",
            "os.posix_spawnp",
            "os.startfile",
            *(f"os.spawn{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
            *(f"os.exec{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
            "asyncio.create_subprocess_exec",
            "asyncio.create_subprocess_shell",
            "multiprocessing.Process",
            "multiprocessing.Pool",
            "multiprocessing.pool.Pool",
            # A context alone starts nothing; only what is created on it.
            "multiprocessing.get_context().Process",
            "multiprocessing.get_context().Pool",
            # Managers run a server process.
            "multiprocessing.Manager",
            "multiprocessing.get_context().Manager",
            "multiprocessing.managers.SyncManager().start",
            "multiprocessing.managers.BaseManager().start",
            "concurrent.futures.ProcessPoolExecutor",
            "concurrent.futures.process.ProcessPoolExecutor",
            "os.fork",
            "os.forkpty",
            "pty.fork",
            "pty.spawn",
        ),
    ),
    (_ENV, ("os.environ", "os.environb", "os.getenv", "os.getenvb")),
    (
        _FS_READ,
        (
            "os.listdir",
            "os.scandir",
            "os.walk",
            "pathlib.Path().read_text",
            "pathlib.Path().read_bytes",
            "pathlib.Path().iterdir",
            "pathlib.Path().glob",
            "pathlib.Path().rglob",
            "glob.glob",
            "glob.iglob",
        ),
    ),
    (
        _FS_WRITE,
        (
            "os.remove",
            "os.unlink",
            "os.rename",
            "os.replace",
            "os.mkdir",
            "os.makedirs",
            "os.rmdir",
            "os.removedirs",
            "shutil.copy",
            "shutil.copy2",
            "shutil.copyfile",
            "shutil.copytree",
            "shutil.move",
            "shutil.rmtree",
            # Saves the download to a file (a temporary one without a name).
            "urllib.request.urlretrieve",
            "pathlib.Path().write_text",
            "pathlib.Path().write_bytes",
            "pathlib.Path().mkdir",
            "pathlib.Path().touch",
            "pathlib.Path().unlink",
            "pathlib.Path().rmdir",
            "pathlib.Path().rename",
            "pathlib.Path().replace",
        ),
    ),
)
# Patterns a permission's own patterns would also match but which belong to
# another permission only (a socket that binds listens, it does not connect).
_NOT_UNDER: dict[str, tuple[str, ...]] = {
    _NETWORK: tuple(f"socket.socket().{method}" for method in _SOCKET_LISTEN_METHODS),
}
# Data attributes whose mere read is the use (no call needed).
_DATA_READS = ("os.environ", "os.environb")
# Methods on those that change the mapping rather than read it.
# pop / popitem / setdefault return a value, so they read too.
_DATA_WRITE_METHODS = frozenset({"update", "clear", "__setitem__", "__delitem__"})
# SDK access points reported only under a permission id (no legacy name).
_SDK_PERMISSION_SUFFIXES = ((".models.get_client", _MODELS),)
# open()-like calls: function -> positional index of the mode argument.
_OPEN_CALLS = {
    "builtins.open": 1,
    "io.open": 1,
    "codecs.open": 1,
    "aiofiles.open": 1,
    "pathlib.Path().open": 0,
    "os.open": None,
}
# Importing these alone suggests the permission (weakest basis).
_IMPORT_HINTS = {
    "socket": _NETWORK,
    "requests": _NETWORK,
    "httpx": _NETWORK,
    "aiohttp": _NETWORK,
    "websockets": _NETWORK,
    "websocket": _NETWORK,
    "urllib3": _NETWORK,
    "urllib.request": _NETWORK,
    "http.client": _NETWORK,
    "http.server": _LISTEN,
    "socketserver": _LISTEN,
    "wsgiref.simple_server": _LISTEN,
    "xmlrpc.server": _LISTEN,
    "ftplib": _NETWORK,
    "smtplib": _NETWORK,
    "subprocess": _PROCESS,
    "multiprocessing": _PROCESS,
}
# Builtins the scan cares about; only when the name is not rebound.
_BUILTINS = frozenset({"open", "eval", "exec", "compile", "getattr", "__import__"})

_DYNAMIC_IMPORT_CALLS = ("importlib.import_module", "importlib.__import__", "builtins.__import__")
_CODE_EXEC_CALLS = ("builtins.eval", "builtins.exec", "builtins.compile", "runpy.run_path", "runpy.run_module")
_NATIVE_LOADERS = (
    "ctypes.CDLL",
    "ctypes.WinDLL",
    "ctypes.OleDLL",
    "ctypes.PyDLL",
    "ctypes.cdll",
    "ctypes.windll",
    "ctypes.oledll",
    "ctypes.pydll",
    "ctypes.LibraryLoader",
    "_ctypes.dlopen",
    "cffi.FFI().dlopen",
)
_CONTEXT_LOSS = (
    "threading.Thread",
    "threading.Timer",
    "_thread.start_new_thread",
    "concurrent.futures.ThreadPoolExecutor",
    "concurrent.futures.ProcessPoolExecutor",
    "concurrent.futures.process.ProcessPoolExecutor",
    "concurrent.futures.thread.ThreadPoolExecutor",
    "multiprocessing.pool.ThreadPool",
    # A loop the plugin makes itself has a plain default executor, so
    # run_in_executor(None, ...) on it loses the context (see below).
    "asyncio.new_event_loop",
    "asyncio.events.new_event_loop",
    "asyncio.Runner",
    "asyncio.runners.Runner",
)
# Analysis warnings that leave part of the plugin unscanned.
_NOT_ANALYZED_WARNINGS = frozenset(
    {"budget_exceeded", "source_too_large", "source_unreadable", "analysis_failed"}
)
# Non-Python code the scan does not read (hosted UI scripts and the like).
_NON_PYTHON_CODE_SUFFIXES = frozenset({".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue"})
_NON_PYTHON_SKIPPED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", "vendor"})
# Skipped directories that may hold code the plugin ships or runs; listed as
# not analyzed. vendor/ has its own reason; .git and __pycache__ are not code.
_LISTED_SKIPPED_DIRS = frozenset({".venv", "venv", "node_modules"})
# Files and directories visited; generous so asset-heavy plugins finish.
_MAX_TREE_ENTRIES = 20000
_MAX_NON_PYTHON_REPORTED = 20
_MAX_MANIFEST_BYTES = 1024 * 1024


# Reading these off a function (subprocess.run.__name__) does not use it.
_METADATA_ATTRIBUTES = frozenset({"__name__", "__qualname__", "__doc__", "__module__"})


def _matches(name: str, patterns) -> bool:
    for pattern in patterns:
        if name == pattern or name.startswith(pattern + "("):
            return True
        if name.startswith(pattern + "."):
            attribute = re.split(r"[.(]", name[len(pattern) + 1:], maxsplit=1)[0]
            if attribute not in _METADATA_ATTRIBUTES:
                return True
    return False


def _origin_name(reference: _Reference) -> str | None:
    """Fully qualified external name, or None when the origin is not external."""
    if reference.bound is None and reference.method in _BUILTINS:
        return "builtins." + reference.method
    # An alias of a builtin (execute = eval): the binding resolved to the bare
    # builtin name. A plugin-defined or imported name of the same spelling is
    # bound as external instead and does not get here.
    if (
        reference.bound is True
        and not reference.external
        and reference.method in _BUILTINS
        and not reference.shadowed_builtin_alias
    ):
        return "builtins." + reference.method
    if reference.external and reference.bound:
        return reference.method
    return None


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_argument(call: ast.Call, index: int | None, keyword: str) -> ast.AST | None:
    for item in call.keywords:
        if item.arg == keyword:
            return item.value
    if index is not None and index < len(call.args):
        return call.args[index]
    return None


def _open_permissions(reference: _Reference, name: str) -> tuple[tuple[str, ...], str]:
    """Permissions an open()-like call may need, and the mode as read/write/unknown."""
    index = _OPEN_CALLS[name]
    node = reference.node
    if index is None or not isinstance(node, ast.Call):
        return (_FS_READ, _FS_WRITE), "unknown"
    argument = _call_argument(node, index, "mode")
    if argument is None:
        unpacked = any(item.arg is None for item in node.keywords) or any(
            isinstance(item, ast.Starred) for item in node.args
        )
        if unpacked:
            # *args / **kwargs may carry the mode.
            return (_FS_READ, _FS_WRITE), "unknown"
        return (_FS_READ,), "read"
    mode = _literal_string(argument)
    if mode is None:
        return (_FS_READ, _FS_WRITE), "unknown"
    if "+" in mode:
        return (_FS_READ, _FS_WRITE), "read_write"
    if any(flag in mode for flag in "wax"):
        return (_FS_WRITE,), "write"
    return (_FS_READ,), "read"


def _reference_permissions(reference: _Reference) -> list[tuple[str, str, dict]]:
    """(permission, basis, extra evidence fields) for one reference."""
    results: list[tuple[str, str, dict]] = []
    if reference.rebound:
        direct = named = BASIS_SUFFIX_MATCH
    else:
        direct = BASIS_RESOLVED_CALL if reference.is_call else BASIS_RESOLVED_NAME
        named = BASIS_RESOLVED_NAME
    legacy_method, legacy_external = _legacy(reference.method, reference.external)
    if not legacy_external:
        # Definite only for the SDK itself, not the plugin's own plugin.* modules.
        sdk_resolved = (
            reference.bound is True and legacy_method is reference.method and _sdk_origin(legacy_method)
        )
        sdk_basis = direct if sdk_resolved else BASIS_SUFFIX_MATCH
        capability = _capability_for(legacy_method)
        if capability:
            results.append((LEGACY_CAPABILITY_PERMISSIONS[capability], sdk_basis, {}))
        call_target = legacy_method.removesuffix("()")
        for suffix, permission in _SDK_PERMISSION_SUFFIXES:
            if call_target.endswith(suffix):
                results.append((permission, sdk_basis, {}))
    name = _origin_name(reference)
    if name is None or reference.via_call_result:
        return results
    if name in _OPEN_CALLS and reference.is_call:
        permissions, mode = _open_permissions(reference, name)
        results.extend((permission, direct, {"mode": mode}) for permission in permissions)
        return results
    for permission, patterns in _EXTERNAL_RULES:
        if _matches(name, patterns) and not _matches(name, _NOT_UNDER.get(permission, ())):
            basis = direct
            if _matches(name, _DATA_READS):
                # A mutating method, called or only taken (set_env = os.environ.update).
                if reference.written or name.rsplit(".", 1)[-1] in _DATA_WRITE_METHODS:
                    # Writing the environment is not reading it; keep it as
                    # a hint rather than definite evidence.
                    basis = named
                elif not reference.is_call:
                    basis = named if reference.rebound else BASIS_RESOLVED_REFERENCE
            elif _matches(name, _HINT_ONLY):
                basis = named
            results.append((permission, basis, {}))
    if reference.is_call and name in _DATAGRAM_ENDPOINTS:
        results.extend((_LISTEN, basis, {}) for basis in _datagram_listen(reference.node, direct, named))
    if reference.local_socket:
        # Only local socket files are reached: possible use at most.
        results = [
            (
                permission,
                named if permission in (_NETWORK, _LISTEN) and _BASIS_RANK[basis] > _BASIS_RANK[named] else basis,
                extra,
            )
            for permission, basis, extra in results
        ]
    return results


def _datagram_listen(node: ast.AST, direct: str, named: str) -> tuple[str, ...]:
    """create_datagram_endpoint(factory, local_addr=...) binds a local
    address others can send to; with only remote_addr= (or sock=) it does not."""
    if not isinstance(node, ast.Call):
        return ()
    if not any(item.arg == "local_addr" for item in node.keywords) and any(
        isinstance(item, ast.Starred) for item in node.args[:2]
    ):
        # *args may fill, shift or leave out the local address.
        return (named,)
    local = _call_argument(node, 1, "local_addr")
    if local is None:
        unpacked = any(item.arg is None for item in node.keywords) or any(
            isinstance(item, ast.Starred) for item in node.args
        )
        # *args / **kwargs may carry local_addr.
        return (named,) if unpacked else ()
    if isinstance(local, ast.Constant) and local.value is None:
        return ()
    return (direct,)


def _import_hints(node: ast.Import | ast.ImportFrom | str) -> dict[str, set[str]]:
    """Permission -> the _IMPORT_HINTS prefixes the import matched.

    A string is a module a literal dynamic import returns.
    """
    if isinstance(node, str):
        modules = [node]
    elif isinstance(node, ast.ImportFrom):
        # The imported name may be a submodule (from http import server).
        modules = (
            [node.module, *(f"{node.module}.{item.name}" for item in node.names)]
            if node.module and not node.level
            else []
        )
    else:
        modules = [item.name for item in node.names]
    hints: dict[str, set[str]] = {}
    for module in modules:
        for prefix, permission in _IMPORT_HINTS.items():
            if module == prefix or module.startswith(prefix + "."):
                hints.setdefault(permission, set()).add(prefix)
    return hints


def _explained_by_listen(prefixes: set[str], listen_rows) -> bool:
    """Every hinted module already has direct listen evidence (a server-only
    use of aiohttp or websockets), so the import says nothing about connecting.

    Judged per _IMPORT_HINTS prefix (the top-level package), not per call: a
    plugin that also uses that package as a client in a way the scan cannot
    resolve loses the import-only connect hint too.
    """
    direct = [row["method"] for row in listen_rows if row["basis"] in DIRECT_BASES]
    return bool(prefixes) and all(
        any(method.startswith((prefix + ".", prefix + "(")) for method in direct) for prefix in prefixes
    )


def _reference_blind_spot(reference: _Reference) -> str | None:
    node = reference.node
    name = None if reference.via_call_result else _origin_name(reference)
    method = reference.method
    if name is not None:
        # A non-literal target is not followed by the scan, so it is a blind
        # spot; so is reaching modules through sys.modules.
        if reference.is_call and name in _DYNAMIC_IMPORT_CALLS:
            # A literal module is followed like an import (see _literal_import),
            # unless the import function's name is also rebound in this scope.
            return None if reference.imported and not reference.rebound else "dynamic_import"
        # Reaching modules through sys.modules (lookups, copies, views,
        # aliases) is dynamic; only a membership test ("x" in sys.modules) is not.
        if _matches(name, ("sys.modules",)) and not reference.membership:
            return "dynamic_import"
        if reference.is_call and name == "builtins.getattr":
            # A constant name on the bare builtin reads like attribute access
            # (see _getattr_reference); qualified or aliased spellings are not
            # followed, so they stay a blind spot.
            bare = (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and reference.bound is None  # not rebound, e.g. by from builtins import getattr
            )
            if bare and _literal_string(_call_argument(node, 1, "name")) is not None:
                return None
            return "reflective_access"
        if _matches(name, ("operator.attrgetter",)):
            return "reflective_access"
        if reference.is_call and name in _CODE_EXEC_CALLS:
            return "code_exec"
        if _matches(name, _NATIVE_LOADERS):
            return "native_loader"
        if _matches(name, _CONTEXT_LOSS):
            return "context_loss"
    if method.endswith(".__dict__") and (
        (reference.bound is True and _sdk_origin(method)) or ("." + method).endswith(".ctx.__dict__")
    ):
        return "reflective_access"
    if reference.is_call and method.endswith(".run_in_executor"):
        # The plugin runtime installs a context-copying default executor on
        # the loops it runs plugin code on, so a literal None executor keeps
        # the caller's context variables. Any other executor does not. The
        # scan cannot tell which loop a call runs on; making a loop and
        # replacing the default executor are reported where they happen.
        executor = _call_argument(node, 0, "executor")
        if isinstance(executor, ast.Constant) and executor.value is None:
            return None
        return "context_loss"
    if reference.is_call and method.endswith(".set_default_executor"):
        return "context_loss"
    return None


@dataclass
class CapabilitySources:
    files: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    size: int = 0
    # Parsed plugin.toml; None when absent or unreadable.
    manifest: dict | None = None
    # Parts of the plugin the scan does not read: [{"reason", "file"}].
    skipped: list[dict] = field(default_factory=list)


def _read_manifest(root: Path) -> dict | None:
    path = root / "plugin.toml"
    try:
        if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
            return None
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError, UnicodeError):
        return None


def skipped_inventory(
    root: Path,
    stop: Event | None = None,
    include: Callable[[Path, bool], bool] | None = None,
) -> dict:
    """What the Python scan does not read: vendor/, environment and package
    directories (.venv/, venv/, node_modules/), non-Python code files and
    test-named Python files (tests/, test_*.py, *_test.py, conftest.py).

    Bounded by _MAX_TREE_ENTRIES; ``truncated`` tells the walk stopped early.
    Also part of the cached report's source revision, so adding or removing
    such files refreshes the report. ``include`` (see read_capability_sources)
    prunes directories it rejects before they count toward the bound.
    """
    inventory: dict = {
        "vendor": [], "skipped_dir": [], "non_python": [], "test_source": [],
        "truncated": False, "unreadable": False,
    }
    try:
        visited = 0
        def unreadable(_error: OSError) -> None:
            inventory["unreadable"] = True

        for current, directories, files in os.walk(root, onerror=unreadable, followlinks=False):
            if stop is not None and stop.is_set():
                inventory["truncated"] = True
                break
            base = Path(current)
            # The Python scan skips vendor/ at any depth; list each one.
            inventory["vendor"].extend(
                (base / name).relative_to(root).as_posix() for name in sorted(directories) if name == "vendor"
            )
            inventory["skipped_dir"].extend(
                (base / name).relative_to(root).as_posix()
                for name in sorted(directories)
                if name in _LISTED_SKIPPED_DIRS
            )
            visited += len(directories)
            if visited > _MAX_TREE_ENTRIES:
                inventory["truncated"] = True
                return inventory
            directories[:] = sorted(
                name
                for name in directories
                if name not in _NON_PYTHON_SKIPPED_DIRS
                and not is_vendor_sync_path((base / name).relative_to(root))
                and (include is None or include((base / name).relative_to(root), True))
            )
            for name in sorted(files):
                visited += 1
                if visited > _MAX_TREE_ENTRIES:
                    inventory["truncated"] = True
                    return inventory
                relative = (base / name).relative_to(root)
                if Path(name).suffix.lower() in _NON_PYTHON_CODE_SUFFIXES:
                    inventory["non_python"].append(relative.as_posix())
                elif name.lower().endswith(".py") and _is_test_source(relative):
                    # Same files iter_capability_sources leaves out; they may
                    # still ship and be imported at runtime.
                    inventory["test_source"].append(relative.as_posix())
    except OSError:
        inventory["unreadable"] = True
    return inventory


def _skipped_parts(
    root: Path, stop: Event | None, include: Callable[[Path, bool], bool] | None = None
) -> list[dict]:
    """not_analyzed rows for vendor/, skipped environment and package
    directories, non-Python code and test-named files, bounded per reason;
    a reason cut short gets one more row with ``file`` None and ``omitted``.

    ``include`` drops paths the caller does not compare (see
    read_capability_sources).
    """
    inventory = skipped_inventory(root, stop, include)
    skipped: list[dict] = []
    for reason, is_dir in (
        ("vendor", True), ("skipped_dir", True), ("non_python", False), ("test_source", False),
    ):
        files = inventory[reason]
        if include is not None:
            files = [relative for relative in files if include(Path(relative), is_dir)]
        skipped.extend({"reason": reason, "file": relative} for relative in files[:_MAX_NON_PYTHON_REPORTED])
        if len(files) > _MAX_NON_PYTHON_REPORTED:
            skipped.append(
                {"reason": reason, "file": None, "omitted": len(files) - _MAX_NON_PYTHON_REPORTED}
            )
    if inventory["truncated"]:
        skipped.append({"reason": "budget_exceeded", "file": None})
    if inventory["unreadable"]:
        skipped.append({"reason": "unreadable", "file": None})
    return skipped


def read_capability_sources(
    root: Path,
    *,
    stop: Event | None = None,
    include: Callable[[Path, bool], bool] | None = None,
) -> CapabilitySources:
    """Read a bounded snapshot and close source handles before parsing.

    ``include`` (given a plugin-relative path and whether it is a directory)
    filters files before they count toward the file and size budgets, and
    the list of unscanned parts.
    """
    sources = CapabilitySources()
    paths = iter_capability_sources(root)
    if include is not None:
        paths = (path for path in paths if include(path.relative_to(root), False))
    try:
        for index, path in enumerate(paths):
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
    sources.manifest = _read_manifest(root)
    sources.skipped = _skipped_parts(root, stop, include)
    return sources


class _PermissionCollector:
    def __init__(self) -> None:
        # permission -> (file, line) -> evidence row
        self.findings: dict[str, dict[tuple[str, int], dict]] = {}
        self.import_hints: dict[str, dict] = {}
        self.import_hint_prefixes: dict[str, set[str]] = {}
        # Counted blind spots, context_loss and not_analyzed hints are kept
        # apart; each per-reference kind has its own cap.
        self.blind_spots: dict[tuple[str, str | None, int | None], dict] = {}
        self.context_loss: dict[tuple[str, str | None, int | None], dict] = {}
        self.not_analyzed: dict[tuple[str | None, str | None], dict] = {}
        # file -> blind-spot type -> count; not capped, so the per-file
        # summary stays exact when the row list is cut at MAX_BLIND_SPOTS.
        self.file_counts: dict[str, dict[str, int]] = {}
        self._counted: set[tuple[str, str | None, int | None]] = set()

    def add_finding(self, permission: str, basis: str, row: dict) -> None:
        rows = self.findings.setdefault(permission, {})
        key = (row["file"], row["line"])
        previous = rows.get(key)
        if previous is None:
            if len(rows) >= MAX_PERMISSION_EVIDENCE:
                # Keep the strongest evidence: replace the weakest row.
                weakest = min(rows, key=lambda item: _BASIS_RANK[rows[item]["basis"]])
                if _BASIS_RANK[rows[weakest]["basis"]] >= _BASIS_RANK[basis]:
                    return
                del rows[weakest]
            rows[key] = {**row, "basis": basis}
        elif _BASIS_RANK[basis] > _BASIS_RANK[previous["basis"]]:
            rows[key] = {**row, "basis": basis}

    def add_blind_spot(self, kind: str, file: str | None, line: int | None, **extra) -> None:
        if kind == "not_analyzed":
            # Unscanned parts are always kept, one row per file and reason.
            self.not_analyzed.setdefault(
                (file, extra.get("reason")), {"type": kind, "file": file, "line": None, **extra}
            )
            return
        key = (kind, file, line)
        if kind == "context_loss":
            if key not in self.context_loss and len(self.context_loss) < MAX_BLIND_SPOTS:
                self.context_loss[key] = {"type": kind, "file": file, "line": line, **extra}
            return
        if key not in self._counted:
            self._counted.add(key)
            counts = self.file_counts.setdefault(file or "", {})
            counts[kind] = counts.get(kind, 0) + 1
        if key in self.blind_spots or len(self.blind_spots) >= MAX_BLIND_SPOTS:
            return
        self.blind_spots[key] = {
            "type": kind, "file": file, "line": line, **extra,
            "high_risk": kind in HIGH_RISK_BLIND_SPOT_TYPES,
        }

    def _add_import_hints(self, relative: str, reference: _Reference, hints: dict[str, set[str]]) -> None:
        for permission, prefixes in hints.items():
            self.import_hint_prefixes.setdefault(permission, set()).update(prefixes)
            self.import_hints.setdefault(
                permission,
                {
                    "file": relative,
                    "line": reference.node.lineno,
                    "function": reference.function,
                    "method": "import",
                    "basis": BASIS_IMPORT_ONLY,
                },
            )

    def scan(self, relative: str, reference: _Reference) -> None:
        node = reference.node
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and any(item.name == "*" for item in node.names):
                self.add_blind_spot("dynamic_import", relative, node.lineno, method="import *")
            self._add_import_hints(relative, reference, _import_hints(node))
            return
        if reference.imported and not reference.rebound:
            self._add_import_hints(relative, reference, _import_hints(reference.imported))
        row = {
            "file": relative,
            "line": node.lineno,
            "function": reference.function,
            "method": str(reference.method),
        }
        for permission, basis, extra in _reference_permissions(reference):
            self.add_finding(permission, basis, {**row, **extra})
        kind = _reference_blind_spot(reference)
        if kind:
            self.add_blind_spot(kind, relative, node.lineno, method=str(reference.method))

    def report(self, declarations: PermissionDeclarations, warnings, skipped) -> tuple[dict, dict]:
        for warning in warnings:
            code = warning.get("code")
            if code in _NOT_ANALYZED_WARNINGS:
                self.add_blind_spot("not_analyzed", warning.get("file"), None, reason=code)
        for item in skipped:
            extra = {key: value for key, value in item.items() if key not in ("file", "reason")}
            self.add_blind_spot("not_analyzed", item.get("file"), None, reason=item["reason"], **extra)
        for permission, row in self.import_hints.items():
            if permission in self.findings:
                continue
            if permission == _NETWORK and _explained_by_listen(
                self.import_hint_prefixes[permission], self.findings.get(_LISTEN, {}).values()
            ):
                continue
            self.findings[permission] = {(row["file"], row["line"]): row}
        permissions: dict[str, dict] = {}
        for permission in sorted(set(self.findings) | declarations.ids()):
            rows = sorted(
                self.findings.get(permission, {}).values(),
                key=lambda row: (row["file"], row["line"]),
            )
            basis = (
                max((row["basis"] for row in rows), key=_BASIS_RANK.__getitem__)
                if rows
                else BASIS_DECLARED_ONLY
            )
            permissions[permission] = {
                "declared": declarations.level(permission),
                "findings": rows,
                "basis": basis,
            }
        def ordered(rows):
            return sorted(rows, key=lambda row: (row["file"] or "", row["line"] or 0, row["type"]))

        files = []
        for file in sorted(self.file_counts):
            types = {
                kind: self.file_counts[file][kind]
                for kind in COUNTED_BLIND_SPOT_TYPES
                if kind in self.file_counts[file]
            }
            files.append(
                {
                    "file": file,
                    "count": sum(types.values()),
                    "types": types,
                    "high_risk": any(kind in HIGH_RISK_BLIND_SPOT_TYPES for kind in types),
                }
            )
        spots = {
            "blind_spots": ordered(self.blind_spots.values()),
            "blind_spot_files": files,
            "not_analyzed": sorted(
                self.not_analyzed.values(),
                key=lambda row: (row["file"] is None, row["file"] or "", row.get("reason") or ""),
            ),
            "context_loss": ordered(self.context_loss.values()),
        }
        return permissions, spots


def analyze_capabilities(
    root: Path,
    *,
    stop: Event | None = None,
    sources: CapabilitySources | None = None,
    declarations: PermissionDeclarations | None = None,
    include_context_loss: bool = False,
) -> dict:
    """Static permission and capability report for one plugin directory.

    ``blind_spots`` lists the counted blind spots with their lines (the
    evidence for developer details); ``blind_spot_files`` sums them up per
    file without lines, for user-facing views. ``not_analyzed`` lists parts
    the scan did not read, a hint that is never counted. ``context_loss``
    (threads, pools, loops the plugin makes) is only added with
    ``include_context_loss``, for ``neko-plugin check``.
    """
    walker = _AnalysisWalker(stop)
    sources = sources if sources is not None else read_capability_sources(root, stop=stop)
    if declarations is None:
        declarations, _issues = parse_permission_declarations(sources.manifest or {})
    evidence: list[dict] = []
    capabilities: set[str] = set()
    warnings = list(sources.warnings)
    collector = _PermissionCollector()
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
            for reference in _references(tree, walker):
                collector.scan(relative, reference)
                if isinstance(reference.node, (ast.Import, ast.ImportFrom)):
                    continue
                node = reference.node
                method, external = _legacy(reference.method, reference.external)
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
    permissions, spots = collector.report(declarations, warnings, sources.skipped)
    report = {
        "status": status,
        "capabilities": sorted(capabilities),
        "evidence": evidence,
        "warnings": warnings[:50],
        "files_analyzed": count,
        "source_bytes": sources.size,
        "declarations": declarations.status,
        "invalid_declarations": sorted(declarations.invalid),
        "permissions": permissions,
        "blind_spots": spots["blind_spots"],
        "blind_spot_files": spots["blind_spot_files"],
        "not_analyzed": spots["not_analyzed"],
    }
    if include_context_loss:
        report["context_loss"] = spots["context_loss"]
    return report


def compare_declarations(report: dict) -> list[PermissionIssue]:
    """Declared vs. found, with the severities ``check -r`` applies.

    Used but not declared: an error when the evidence resolves to an SDK or
    standard-library origin, otherwise a warning (it may be a false match).
    Transition: a plugin without any [permissions] table gets a warning
    instead of that error, saying it will become an error later.
    Declared but not found: a warning, since dynamic use is invisible to the
    scan; only info when part of the plugin was not read (partial scan or
    unscanned files), as the use may be there. Blind spots are reported
    separately.
    """
    if report.get("declarations") == "invalid":
        return []
    issues: list[PermissionIssue] = []
    status = report.get("status")
    unread = status == "partial" or bool(report.get("not_analyzed"))
    # Ids whose entry is itself invalid already have an error of their own.
    invalid = set(report.get("invalid_declarations") or [])
    for permission, item in (report.get("permissions") or {}).items():
        findings = item.get("findings") or []
        if permission in invalid:
            continue
        if item.get("declared") is None and findings:
            direct = [row for row in findings if row.get("basis") in DIRECT_BASES]
            definite = bool(direct) and permission not in POSSIBLE_ONLY_PERMISSIONS
            shown = direct if definite else findings
            locations = [f"{row['file']}:{row['line']}" for row in shown[:3]]
            if len(shown) > len(locations):
                locations.append(f"+{len(shown) - len(locations)}")
            if not definite:
                severity, code = "warning", "used_undeclared_possible"
            elif report.get("declarations") == "declared":
                severity, code = "error", "used_undeclared"
            else:
                severity, code = "warning", "used_undeclared_no_table"
            issues.append(
                PermissionIssue(
                    severity,
                    code,
                    {
                        "permission": permission,
                        "locations": ", ".join(locations),
                        "more": str(max(0, len(shown) - 3)),
                    },
                )
            )
        elif item.get("declared") is not None and not findings and (status != "unavailable" or unread):
            severity = "info" if unread else "warning"
            issues.append(PermissionIssue(severity, "declared_unused", {"permission": permission}))
    return issues


def scan_note(report: dict) -> str:
    """Which user-facing scan sentence applies: never a safety claim.

    "no_extra_usage": completed, no blind spots, every part read and nothing
    beyond the declarations; "completed": the same but with undeclared
    findings; "blind_spots": counted blind spots; "not_analyzed": none of
    those, but the scan was partial or skipped parts (a hint, no sentence);
    "unavailable" otherwise. context_loss never changes the note.
    """
    status = report.get("status")
    if status not in ("completed", "partial"):
        return "unavailable"
    if report.get("blind_spots"):
        return "blind_spots"
    if status == "partial" or report.get("not_analyzed"):
        return "not_analyzed"
    undeclared = any(
        item.get("findings") and item.get("declared") is None
        for item in (report.get("permissions") or {}).values()
    )
    # Found use beyond the declarations: no summary sentence, the
    # comparison issues say it.
    return "completed" if undeclared else "no_extra_usage"


def summarize_permissions(report: dict) -> dict:
    """Summary rows, each labelled with where it comes from.

    ``declared`` rows are the author's own statement, ``detected`` rows come
    from the scan only, ``unconfirmed`` rows are counted blind-spot types the
    scan could not see through (not context_loss or not_analyzed). The three sources are never merged into one row.
    """
    declared: list[dict] = []
    detected: list[dict] = []
    for permission, item in (report.get("permissions") or {}).items():
        if item.get("declared"):
            declared.append({"permission": permission, "source": "declared", "level": item["declared"]})
        elif item.get("findings"):
            # Point at a row of the reported (strongest) basis.
            first = next(
                (row for row in item["findings"] if row.get("basis") == item.get("basis")),
                item["findings"][0],
            )
            detected.append(
                {
                    "permission": permission,
                    "source": "detected",
                    "basis": item.get("basis"),
                    "file": first.get("file"),
                    "line": first.get("line"),
                }
            )
    rows = declared + detected
    blind_types = sorted(
        {row["type"] for row in report.get("blind_spots") or [] if row.get("type") in COUNTED_BLIND_SPOT_TYPES},
        key=COUNTED_BLIND_SPOT_TYPES.index,
    )
    rows.extend({"type": kind, "source": "unconfirmed"} for kind in blind_types)
    return {"rows": rows, "note": scan_note(report), "blind_spot_types": blind_types}
