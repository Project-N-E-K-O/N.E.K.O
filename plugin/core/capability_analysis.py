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
from collections.abc import Iterator

from plugin.core.permission_declarations import (
    LEGACY_PERMISSION_NAMES,
    PermissionDeclarations,
    PermissionIssue,
    parse_permission_declarations,
)
from plugin.utils.source_paths import is_vendor_sync_path, iter_plugin_python_files

CAPABILITIES = ("conversations.read", "memory.read", "messages.push")
# Bump when a change to analysis makes persisted reports stale.
# 5: permission ids, declarations, blind spots; legacy capabilities unchanged.
ANALYZER_VERSION = 5
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
            return replacement + value[end:], external, True
    return value, False, None


def _sdk_origin(path: str) -> bool:
    return path == "plugin.sdk" or path.startswith(("plugin.sdk.", "plugin.sdk("))


def _sdk_module(path: str) -> bool:
    return path == "plugin" or path.startswith("plugin.")


def _getattr_reference(node: ast.AST, bindings=None) -> tuple[ast.AST, str] | None:
    """getattr(x, "name") — optionally called — reads like x.name.

    Skipped when the plugin binds its own ``getattr`` (bindings given).
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if (
        isinstance(func, ast.Name)
        and func.id == "getattr"
        and (bindings is None or "getattr" not in bindings)
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and node.args[1].value.isidentifier()
    ):
        return node.args[0], node.args[1].value
    if isinstance(func, ast.Call):
        return _getattr_reference(func, bindings)
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
    # Read with a subscript (x[k]).
    subscripted: bool = False
    # The root name holds a value read from the environment earlier
    # (tok = os.environ.get()), whose own line is already the evidence. Other
    # stored call results (p = Path(); p.write_text()) still count.
    via_call_result: bool = False


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
        _propagate_assignments(assignments, bindings, walker)
        # Names rebound here, plus those inherited from enclosing scopes that
        # this scope does not bind itself (it may import its own copy).
        rebound = _derived_rebound(
            _rebound_names(nodes, walker) | (outer_rebound - declared), assignments, walker
        )
        own = {id(node) for node in nodes}
        written = _written_targets(nodes, walker)
        subscripted = {
            id(node.value) for node in nodes if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
        }
        comprehension_locals = _comprehension_locals(nodes, walker)

        def nested(body, inherited, parameters=(), function="", owner=None):
            # A function or lambda defined inside a comprehension also sees
            # the comprehension's own names.
            extra = comprehension_locals.get(id(owner), frozenset()) if owner is not None else frozenset()
            return scope(body, inherited, parameters, function, rebound | extra)

        for reference in _emit_references(nodes, bindings, nested, walker, function):
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
                if id(reference.node) in subscripted:
                    changes["subscripted"] = True
                if changes:
                    reference = replace(reference, **changes)
            yield reference

    yield from scope(tree.body, {})


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
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _DATA_WRITE_METHODS
        ):
            written.update((id(node.func), id(node.func.value)))
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
            if _root_name(_name(value)) in names:
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


def _qualified(function: str, name: str) -> str:
    return f"{function}.{name}" if function else name


def _emit_references(nodes, bindings, scope, walker, function=""):
    for node in nodes:
        walker.check()
        if isinstance(node, ast.ClassDef):
            yield from scope(
                node.body,
                _propagate_self_attributes(node, bindings, walker),
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
                resolved, external, bound = _resolve_with_origin(raw, bindings)
                # getattr(x, "f") only retrieves f; getattr(x, "f")() calls it.
                yield _Reference(
                    node, resolved + "." + attribute, external, bound, function,
                    isinstance(node, ast.Call) and isinstance(node.func, ast.Call),
                    raw_root=_root_name(raw),
                )
            else:
                raw = _name(node.func if isinstance(node, ast.Call) else node)
                resolved, external, bound = _resolve_with_origin(raw, bindings)
                raw_root = _root_name(raw)
                yield _Reference(
                    node, resolved, external, bound, function,
                    isinstance(node, ast.Call),
                    raw_root=raw_root,
                    via_call_result=bool(bound)
                    and raw_root != raw
                    and _is_data_read_result(_resolve(raw_root, bindings)[0]),
                )
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            # A bare name bound to a data attribute (from os import environ)
            # reads it; other bare names are left to the attribute and call
            # references so the legacy capability scan is unchanged.
            resolved, external, bound = _resolve_with_origin(node.id, bindings)
            # Only the mapping itself, not a stored result (tok = os.environ.get()).
            if external and bound and resolved in _DATA_READS:
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

_MODELS = "plugin_sdk.models:invoke"
_NETWORK = "system.network:connect"
_PROCESS = "system.process:spawn"
_ENV = "system.env:read"
_FS_READ = "system.fs:read"
_FS_WRITE = "system.fs:write"

# Standard-library and well-known third-party origins. A reference matches a
# pattern when it equals it or continues it with "." or "()".
_EXTERNAL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        _NETWORK,
        (
            "socket.socket",
            "socket.create_connection",
            "socket.create_server",
            "urllib.request.urlopen",
            "urllib.request.build_opener",
            "http.client.HTTPConnection",
            "http.client.HTTPSConnection",
            "asyncio.open_connection",
            "asyncio.start_server",
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
            "websocket.create_connection",
            "websocket.WebSocketApp",
            "urllib3.PoolManager",
            "urllib3.request",
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
    "ftplib": _NETWORK,
    "smtplib": _NETWORK,
    "subprocess": _PROCESS,
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
    "concurrent.futures.thread.ThreadPoolExecutor",
    "multiprocessing.pool.ThreadPool",
)
# Analysis warnings that leave part of the plugin unscanned.
_NOT_ANALYZED_WARNINGS = frozenset(
    {"budget_exceeded", "source_too_large", "source_unreadable", "analysis_failed"}
)
# Non-Python code the scan does not read (hosted UI scripts and the like).
_NON_PYTHON_CODE_SUFFIXES = frozenset({".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue"})
_NON_PYTHON_SKIPPED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", "vendor"})
# Files and directories visited; generous so asset-heavy plugins finish.
_MAX_TREE_ENTRIES = 20000
_MAX_NON_PYTHON_REPORTED = 20
_MAX_MANIFEST_BYTES = 1024 * 1024


def _matches(name: str, patterns) -> bool:
    return any(
        name == pattern or name.startswith((pattern + ".", pattern + "("))
        for pattern in patterns
    )


def _origin_name(reference: _Reference) -> str | None:
    """Fully qualified external name, or None when the origin is not external."""
    if reference.bound is None and reference.method in _BUILTINS:
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
    if not reference.external:
        # Definite only for the SDK itself, not the plugin's own plugin.* modules.
        sdk_resolved = reference.bound is True and _sdk_origin(reference.method)
        sdk_basis = direct if sdk_resolved else BASIS_SUFFIX_MATCH
        capability = _capability_for(reference.method)
        if capability:
            results.append((LEGACY_CAPABILITY_PERMISSIONS[capability], sdk_basis, {}))
        call_target = reference.method.removesuffix("()")
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
        if _matches(name, patterns):
            basis = direct
            if _matches(name, _DATA_READS):
                if reference.written or (reference.is_call and name.rsplit(".", 1)[-1] in _DATA_WRITE_METHODS):
                    # Writing the environment is not reading it; keep it as
                    # a hint rather than definite evidence.
                    basis = named
                elif not reference.is_call:
                    basis = named if reference.rebound else BASIS_RESOLVED_REFERENCE
            results.append((permission, basis, {}))
    return results


def _import_hints(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.ImportFrom):
        modules = [node.module] if node.module and not node.level else []
    else:
        modules = [item.name for item in node.names]
    hints = set()
    for module in modules:
        for prefix, permission in _IMPORT_HINTS.items():
            if module == prefix or module.startswith(prefix + "."):
                hints.add(permission)
    return hints


def _reference_blind_spot(reference: _Reference) -> str | None:
    node = reference.node
    name = None if reference.via_call_result else _origin_name(reference)
    method = reference.method
    if name is not None:
        # Even a literal target is not followed by the scan, so it is a blind
        # spot either way; so is reaching modules through sys.modules.
        if reference.is_call and name in _DYNAMIC_IMPORT_CALLS:
            return "dynamic_import"
        # Looking a module up (sys.modules[name], .get, .pop) is dynamic;
        # a membership test ("x" in sys.modules) is not.
        if name == "sys.modules" and reference.subscripted:
            return "dynamic_import"
        if reference.is_call and name in ("sys.modules.get", "sys.modules.pop", "sys.modules.setdefault"):
            return "dynamic_import"
        if reference.is_call and name == "builtins.getattr":
            # A constant name reads like attribute access; see _getattr_reference.
            if _literal_string(_call_argument(node, 1, "name")) is not None:
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
        # Executor threads, the default one included, do not carry the
        # caller's context variables.
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


def skipped_inventory(root: Path, stop: Event | None = None) -> dict:
    """What the Python scan does not read: vendor/ and non-Python code files.

    Bounded by _MAX_TREE_ENTRIES; ``truncated`` tells the walk stopped early.
    Also part of the cached report's source revision, so adding or removing
    such files refreshes the report.
    """
    inventory: dict = {"vendor": False, "non_python": [], "truncated": False, "unreadable": False}
    try:
        inventory["vendor"] = (root / "vendor").is_dir()
        visited = 0
        def unreadable(_error: OSError) -> None:
            inventory["unreadable"] = True

        for current, directories, files in os.walk(root, onerror=unreadable, followlinks=False):
            if stop is not None and stop.is_set():
                inventory["truncated"] = True
                break
            base = Path(current)
            visited += len(directories)
            if visited > _MAX_TREE_ENTRIES:
                inventory["truncated"] = True
                return inventory
            directories[:] = sorted(
                name
                for name in directories
                if name not in _NON_PYTHON_SKIPPED_DIRS
                and not is_vendor_sync_path((base / name).relative_to(root))
            )
            for name in sorted(files):
                visited += 1
                if visited > _MAX_TREE_ENTRIES:
                    inventory["truncated"] = True
                    return inventory
                if Path(name).suffix.lower() in _NON_PYTHON_CODE_SUFFIXES:
                    inventory["non_python"].append((Path(current) / name).relative_to(root).as_posix())
    except OSError:
        inventory["unreadable"] = True
    return inventory


def _skipped_parts(root: Path, stop: Event | None) -> list[dict]:
    """Blind-spot rows for vendor/ and non-Python code files, bounded."""
    inventory = skipped_inventory(root, stop)
    skipped: list[dict] = []
    if inventory["vendor"]:
        skipped.append({"reason": "vendor", "file": "vendor"})
    skipped.extend(
        {"reason": "non_python", "file": relative}
        for relative in inventory["non_python"][:_MAX_NON_PYTHON_REPORTED]
    )
    if inventory["truncated"]:
        skipped.append({"reason": "budget_exceeded", "file": None})
    if inventory["unreadable"]:
        skipped.append({"reason": "unreadable", "file": None})
    return skipped


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
    sources.manifest = _read_manifest(root)
    sources.skipped = _skipped_parts(root, stop)
    return sources


class _PermissionCollector:
    def __init__(self) -> None:
        # permission -> (file, line) -> evidence row
        self.findings: dict[str, dict[tuple[str, int], dict]] = {}
        self.import_hints: dict[str, dict] = {}
        self.blind_spots: dict[tuple[str, str | None, int | None], dict] = {}

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
        key = (kind, file, line)
        # Unscanned parts are always kept; per-reference spots share the cap.
        if key in self.blind_spots or (kind != "not_analyzed" and len(self.blind_spots) >= MAX_BLIND_SPOTS):
            return
        self.blind_spots[key] = {"type": kind, "file": file, "line": line, **extra}

    def scan(self, relative: str, reference: _Reference) -> None:
        node = reference.node
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and any(item.name == "*" for item in node.names):
                self.add_blind_spot("dynamic_import", relative, node.lineno, method="import *")
            for permission in _import_hints(node):
                self.import_hints.setdefault(
                    permission,
                    {
                        "file": relative,
                        "line": node.lineno,
                        "function": reference.function,
                        "method": "import",
                        "basis": BASIS_IMPORT_ONLY,
                    },
                )
            return
        row = {
            "file": relative,
            "line": node.lineno,
            "function": reference.function,
            "method": reference.method,
        }
        for permission, basis, extra in _reference_permissions(reference):
            self.add_finding(permission, basis, {**row, **extra})
        kind = _reference_blind_spot(reference)
        if kind:
            self.add_blind_spot(kind, relative, node.lineno, method=reference.method)

    def report(self, declarations: PermissionDeclarations, warnings, skipped) -> tuple[dict, list[dict]]:
        for warning in warnings:
            code = warning.get("code")
            if code in _NOT_ANALYZED_WARNINGS:
                self.add_blind_spot("not_analyzed", warning.get("file"), None, reason=code)
        for item in skipped:
            self.add_blind_spot("not_analyzed", item.get("file"), None, reason=item["reason"])
        for permission, row in self.import_hints.items():
            if permission not in self.findings:
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
        blind_spots = sorted(
            self.blind_spots.values(),
            key=lambda row: (row["file"] or "", row["line"] or 0, row["type"]),
        )
        return permissions, blind_spots


def analyze_capabilities(
    root: Path,
    *,
    stop: Event | None = None,
    sources: CapabilitySources | None = None,
    declarations: PermissionDeclarations | None = None,
) -> dict:
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
                node, method, external = reference.node, reference.method, reference.external
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
    permissions, blind_spots = collector.report(declarations, warnings, sources.skipped)
    return {
        "status": status,
        "capabilities": sorted(capabilities),
        "evidence": evidence,
        "warnings": warnings[:50],
        "files_analyzed": count,
        "source_bytes": sources.size,
        "declarations": declarations.status,
        "invalid_declarations": sorted(declarations.invalid),
        "permissions": permissions,
        "blind_spots": blind_spots,
    }


def compare_declarations(report: dict) -> list[PermissionIssue]:
    """Declared vs. found, with the severities ``check -r`` applies.

    Used but not declared: an error when the evidence resolves to an SDK or
    standard-library origin, otherwise a warning (it may be a false match).
    Transition: a plugin without any [permissions] table gets a warning
    instead of that error, saying it will become an error later.
    Declared but not found: a warning, since dynamic use is invisible to the
    scan. Blind spots are reported separately (see ``blind_spot_issues``).
    """
    if report.get("declarations") == "invalid":
        return []
    issues: list[PermissionIssue] = []
    status = report.get("status")
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
        elif item.get("declared") is not None and not findings and status != "unavailable":
            issues.append(PermissionIssue("warning", "declared_unused", {"permission": permission}))
    return issues


def scan_note(report: dict) -> str:
    """Which user-facing scan sentence applies: never a safety claim.

    "no_extra_usage": completed, no blind spots and nothing beyond the
    declarations; "completed": the same but with undeclared findings;
    "blind_spots": known blind spots or partial; "unavailable" otherwise.
    """
    status = report.get("status")
    if status not in ("completed", "partial"):
        return "unavailable"
    if report.get("blind_spots") or status == "partial":
        return "blind_spots"
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
    from the scan only, ``unconfirmed`` rows are blind-spot types the scan
    could not see through. The three sources are never merged into one row.
    """
    declared: list[dict] = []
    detected: list[dict] = []
    for permission, item in (report.get("permissions") or {}).items():
        if item.get("declared"):
            declared.append({"permission": permission, "source": "declared", "level": item["declared"]})
        elif item.get("findings"):
            first = item["findings"][0]
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
    blind_types = sorted({row["type"] for row in report.get("blind_spots") or []}, key=BLIND_SPOT_TYPES.index)
    rows.extend({"type": kind, "source": "unconfirmed"} for kind in blind_types)
    return {"rows": rows, "note": scan_note(report), "blind_spot_types": blind_types}
