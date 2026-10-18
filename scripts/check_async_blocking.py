#!/usr/bin/env python3
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Static check: forbid blocking stdlib calls inside ``async def`` bodies.

Complements ruff's ASYNC210/220/221/222/251 rules by catching gaps the
flake8-async port does not cover:

* ``threading.Thread.join(timeout=...)`` / ``multiprocessing.Process.join(...)``
* ``queue.Queue.get(...)`` (blocks by default)
* Raw ``socket.recv`` / ``accept`` / ``connect`` on blocking sockets

Heuristic: the analyzer only inspects statements whose **nearest enclosing
function is ``async def``**. A nested sync ``def`` (a common pattern for
thread targets, ``run_in_executor`` callbacks, etc.) masks the outer async
context — its body is NOT inspected. This matches ruff's behaviour and
avoids false positives on dedicated worker code.

Receiver matching uses the *tail name* (rightmost component) of the call
receiver. That lets us tell ``self.q.get()`` (flagged, queue-like) apart
from ``self.ws.send()`` (not flagged, websocket). Names that are too
generic (``send``, ``put``, ``wait``) are not checked here — they hit
httpx / websockets / asyncio.Event far more often than real blocking
stdlib objects, so the noise is not worth the signal.

Depth-1 transitive check
------------------------
A sync helper in this repo, called *directly* from an ``async def``, can
still block the loop even though ruff and the direct-body pass above see
nothing wrong (the helper is a plain ``def``; its body is off-loaded in
our model). A second pass addresses exactly that class:

1. Scan every module-level / class-level ``def`` (not nested inside an
   ``async def``) and mark it as "risky" when its direct body contains a
   recognised blocking stdlib call (``PIL.Image.open``, Fernet
   encrypt/decrypt, ``shutil.*``, ``time.sleep``, ``requests.*``,
   ``subprocess.run``/``call``/``check_*``, ``urllib.request.urlopen``,
   ``utils.file_utils.atomic_write_text`` / ``atomic_write_bytes`` /
   ``atomic_write_json``, plus
   the queue/thread/socket tail-name heuristics above). Helpers whose
   own name is too generic to identify by name alone (``save``,
   ``load``, ``run``, …) are deliberately NOT indexed — see
   ``GENERIC_HELPER_NAMES``.
2. Walk every ``async def`` body again, and for each bare ``foo(...)``
   or ``obj.foo(...)`` call match the tail name against the risky index.
   A hit is reported as: *blocking sync helper '<name>' ... called
   directly from async context; wrap the call in
   ``await asyncio.to_thread(...)``*.

We deliberately stop at depth 1 — tracing deeper chains needs type
inference and name-based heuristics past depth 1 produce more noise than
signal. General imports are not resolved. The two ``utils.file_utils``
atomic writers are the narrow exception: their direct aliases and module
aliases are resolved because letting import spelling bypass this PR's
central fsync guard would defeat the rule. False positives from name
collisions can be silenced per line with
``# noqa: ASYNC_BLOCK — <reason>``.

Every violation prints as ``path:line:col  CODE  message``. Exit status
is 1 when any violation is found, 0 otherwise.

Suppress a specific line with ``# noqa: ASYNC_BLOCK`` — please follow it
with a one-line justification (reviewed manually).

Usage:
    python scripts/check_async_blocking.py [paths...]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Callable, Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = [
    "app/main_server",
    "main_logic",
    "main_routers",
    "utils",
    # memory_server package + its import chain (memory/ package)
    "app/memory_server",
    "memory",
    # agent_server package + its import chain (brain/ package; main_logic
    # already in scope above)
    "app/agent_server",
    "brain",
    # shared services (chat_sync / theater) running on the main server loop
    "services",
]
# NOTE: ``plugin/`` is intentionally NOT in the default scope. Plugin code uses
# pyzmq sockets (``sock.connect`` / ``sock.recv`` via async zmq) and
# ``asyncio.Queue`` heavily, both of which match the tail-name heuristics here
# and produce a high false-positive rate (``await sock.recv`` on zmq,
# ``await asyncio.wait_for(q.get(), …)`` on asyncio.Queue). Ruff's ASYNC*
# rules still cover plugin code via pyproject.toml. A follow-up can
# type-disambiguate queue/socket kinds and bring plugin into scope.

CODE = "ASYNC_BLOCK"
NOQA_TOKEN = "noqa: ASYNC_BLOCK"

# Receiver tail-name filters. A call matches if the rightmost identifier
# component of the receiver (see _tail_name) equals one of the EXACT names
# or ends with one of the SUFFIX tokens. Tight enough to avoid httpx /
# websockets / zmq-socket false positives while still catching realistic
# names like ``tts_thread`` / ``request_queue`` / ``tts_sock``.
QUEUE_EXACT = {"q", "queue", "queues"}
QUEUE_SUFFIX = ("_q", "_queue")

THREAD_EXACT = {"t", "thread", "worker", "proc", "process"}
THREAD_SUFFIX = ("_thread", "_process", "_proc", "_worker")

SOCKET_EXACT = {"sock", "socket"}
SOCKET_SUFFIX = ("_sock", "_socket")

# ── depth-1 transitive detection ────────────────────────────────────────
# Specific (receiver_tail, attr) pairs for stdlib blocking operations.
# These are used to classify a sync ``def`` as "risky" when its body
# contains such a call, and to describe *why* it is risky. Matching is
# on the tail (rightmost) identifier of the receiver — ``self.x.y.save``
# collapses to ``y.save``, which disambiguates ``fernet.encrypt`` vs.
# ``self.encrypt``. Overly generic pairs are deliberately omitted.
RISKY_ATTR_PAIRS: dict[tuple[str, str], str] = {
    # Module-qualified utils/file_utils atomic persistence: after `from utils import file_utils`,
    # `file_utils.atomic_write_json(...)` is an attribute call, missed by
    # RISKY_BARE_CALLS (ast.Name only). Omitting these creates a backdoor for a perfectly
    # normal import style.
    ("file_utils", "atomic_write_text"): "utils.file_utils.atomic_write_text",
    ("file_utils", "atomic_write_bytes"): "utils.file_utils.atomic_write_bytes",
    ("file_utils", "atomic_write_json"): "utils.file_utils.atomic_write_json",
    # Resolves workshop_config.json and may read/retry/rebase it. Calling it
    # from an async handler was the source of several lock/I/O relays in this
    # PR; route code must use its async twin.
    ("workshop_utils", "get_workshop_path"): "utils.workshop_utils.get_workshop_path",
    # PIL
    ("Image", "open"): "PIL.Image.open",
    ("_PILImage", "open"): "PIL.Image.open",
    ("PILImage", "open"): "PIL.Image.open",
    ("PIL", "open"): "PIL.Image.open",
    # pyautogui (screen capture goes through native code, blocks for tens
    # of ms on each frame)
    ("pyautogui", "screenshot"): "pyautogui.screenshot",
    # Fernet
    ("fernet", "encrypt"): "Fernet.encrypt",
    ("fernet", "decrypt"): "Fernet.decrypt",
    ("Fernet", "encrypt"): "Fernet.encrypt",
    ("Fernet", "decrypt"): "Fernet.decrypt",
    # requests
    ("requests", "get"): "requests.get",
    ("requests", "post"): "requests.post",
    ("requests", "put"): "requests.put",
    ("requests", "delete"): "requests.delete",
    ("requests", "patch"): "requests.patch",
    ("requests", "head"): "requests.head",
    ("requests", "options"): "requests.options",
    ("requests", "request"): "requests.request",
    # subprocess
    ("subprocess", "run"): "subprocess.run",
    ("subprocess", "call"): "subprocess.call",
    ("subprocess", "check_call"): "subprocess.check_call",
    ("subprocess", "check_output"): "subprocess.check_output",
    ("subprocess", "Popen"): "subprocess.Popen",
    # urllib
    ("request", "urlopen"): "urllib.request.urlopen",
    ("urllib", "urlopen"): "urllib.request.urlopen",
    # time
    ("time", "sleep"): "time.sleep",
    # shutil (belt + suspenders: any sync helper wrapping these that is
    # itself called from async bypasses the direct-body shutil checks)
    ("shutil", "copy"): "shutil.copy",
    ("shutil", "copy2"): "shutil.copy2",
    ("shutil", "copyfile"): "shutil.copyfile",
    ("shutil", "copyfileobj"): "shutil.copyfileobj",
    ("shutil", "copytree"): "shutil.copytree",
    ("shutil", "move"): "shutil.move",
    ("shutil", "rmtree"): "shutil.rmtree",
    # json: load() reads from a file handle (always blocks on read);
    # loads() is in-memory string parse (NOT blocking, NOT flagged).
    # NOTE: kept high-signal entries here. We deliberately do NOT add
    # ``open`` (builtin), ``os.listdir`` / ``os.makedirs`` / ``os.path.getmtime``
    # / ``os.path.getsize`` / ``Thread.is_alive`` here — they each have
    # legitimate hot-path patterns (sync file create + offloaded copyfileobj,
    # microsecond-scale stat / GIL checks) that would force noisy noqas.
    # Add them later if the FP cost gets cheaper (per-pattern context filter).
    ("json", "load"): "json.load (reads from file handle)",
}

# Bare-name (no receiver) function calls that indicate blocking —
# catches ``from time import sleep``, ``from shutil import rmtree``,
# ``from urllib.request import urlopen`` etc. Names must be distinctive
# enough that a collision with a user-defined function of the same name
# inside the scanned tree is unlikely. ``sleep`` is distinctive;
# ``copy`` is NOT (too many generic helpers), so we only flag it via
# attribute form above.
RISKY_BARE_CALLS: dict[str, str] = {
    "urlopen": "urllib.request.urlopen",
    "rmtree": "shutil.rmtree",
    # ``from time import sleep`` — distinctive enough; await asyncio.sleep
    # is an attribute call (``asyncio.sleep``) so it won't be confused with
    # this bare ``sleep`` form.
    "sleep": "time.sleep",
    # Atomic utils/file_utils persistence has distinctive names, avoiding collisions. One
    # atomic_write serializes mkdir, directory-wide scandir for crash-left temp files,
    # mkstemp, write, and unbounded os.fsync on caller thread: event-loop use blocks
    # all other coroutines for physical disk flush duration. Async counterparts atomic_write_json_async /
    # atomic_write_text_async already wrap asyncio.to_thread, with 77 production uses;
    # missing piece is caller discipline, never async-safe primitives.
    "atomic_write_text": "utils.file_utils.atomic_write_text",
    "atomic_write_bytes": "utils.file_utils.atomic_write_bytes",
    "atomic_write_json": "utils.file_utils.atomic_write_json",
    "get_workshop_path": "utils.workshop_utils.get_workshop_path",
}

_ATOMIC_WRITE_LABELS = {
    "atomic_write_text": "utils.file_utils.atomic_write_text",
    "atomic_write_bytes": "utils.file_utils.atomic_write_bytes",
    "atomic_write_json": "utils.file_utils.atomic_write_json",
}


def _collect_atomic_write_aliases(
    tree: ast.Module,
) -> tuple[dict[str, str], set[str]]:
    """Return direct-name aliases and module aliases for ``utils.file_utils``.

    This intentionally resolves only the atomic-write family. General import
    resolution would turn the checker's name-based depth-1 pass into a much
    larger cross-module analysis problem; these names are high-signal and
    are the guard this PR is specifically adding.
    """
    direct: dict[str, str] = {}
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.module == "utils.file_utils":
                for alias in node.names:
                    label = _ATOMIC_WRITE_LABELS.get(alias.name)
                    if label is not None:
                        direct[alias.asname or alias.name] = label
            elif node.module == "utils":
                for alias in node.names:
                    if alias.name == "file_utils":
                        modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "utils.file_utils" and alias.asname:
                    modules.add(alias.asname)
    return direct, modules

# Exclude overly generic helper names from depth-1 risk index.
#
# Index matches names: pass 1 records synchronous defs containing blocking calls; pass 2 reports
# matching calls in async defs. Accurate with distinctive names (save_storage_policy is unique),
# noisy with generic verbs. Adding atomic_write_* showed a helper named
# `save` matching unrelated PIL `img.save(png_path)` and
# `TokenTracker.get_instance().save()` calls.
#
# Same tradeoff as queue/thread/socket receiver suffixes in this file: never guess for generic
# names; prefer misses to false positives. Truly blocking `save` helpers are missed,
# requiring review or future type inference.
GENERIC_HELPER_NAMES = frozenset({
    "save", "load", "read", "write", "flush", "close", "open", "dump",
    "update", "apply", "run", "start", "stop", "reset", "clear", "sync",
    "commit", "persist", "get", "set", "add", "append", "remove", "delete",
    "copy", "move", "send", "put", "wait", "join", "process", "handle",
})


def _tail_matches(tail: str, exact: set[str], suffix: tuple[str, ...]) -> bool:
    if not tail:
        return False
    lowered = tail.lower()
    if lowered in exact:
        return True
    return any(lowered.endswith(s) for s in suffix)


def _tail_name(node: ast.expr) -> str:
    """Return the rightmost identifier component of an expression.

    ``x.y.z`` → ``"z"``; ``x`` → ``"x"``; anything more complex → ``""``.
    """
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _tail_name(node.func)
    if isinstance(node, ast.Subscript):
        return _tail_name(node.value)
    return ""


def _describe_blocking_call(
    call: ast.Call,
    atomic_aliases: tuple[dict[str, str], set[str]] | None = None,
) -> str | None:
    """If the call matches a known blocking stdlib operation, return a
    short human-readable description; otherwise ``None``.

    This is the knowledge used by the depth-1 transitive pass to decide
    whether a plain ``def`` is risky to call from an async context.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        receiver_tail = _tail_name(func.value)
        attr = func.attr
        if atomic_aliases is not None:
            _direct_aliases, module_aliases = atomic_aliases
            if (
                isinstance(func.value, ast.Name)
                and func.value.id in module_aliases
                and attr in _ATOMIC_WRITE_LABELS
            ):
                return _ATOMIC_WRITE_LABELS[attr]
        label = RISKY_ATTR_PAIRS.get((receiver_tail, attr))
        if label is not None:
            return label
        # queue.Queue.get / Thread.join / socket.recv forms — also mark a
        # plain def that calls these as risky, since being wrapped in a
        # sync helper doesn't make them any less blocking.
        if attr == "get" and _tail_matches(receiver_tail, QUEUE_EXACT, QUEUE_SUFFIX):
            return "queue.Queue.get()"
        if attr == "join" and _tail_matches(receiver_tail, THREAD_EXACT, THREAD_SUFFIX):
            return "Thread/Process.join()"
        # NOTE: ``is_alive()`` deliberately NOT flagged. It takes the GIL for
        # microseconds (vs. join() which can block indefinitely). Wrapping in
        # ``await asyncio.to_thread(t.is_alive)`` adds ~100µs roundtrip
        # overhead, which is a net regression in the per-audio-chunk TTS
        # watchdog loop where this pattern dominates. If a future hot loop
        # needs flagging, narrow the rule to "inside for/while body" only.
        if _tail_matches(receiver_tail, SOCKET_EXACT, SOCKET_SUFFIX) and attr in {"recv", "accept", "connect"}:
            return f"blocking socket.{attr}()"
        return None
    if isinstance(func, ast.Name):
        if atomic_aliases is not None:
            direct_aliases, _module_aliases = atomic_aliases
            label = direct_aliases.get(func.id)
            if label is not None:
                return label
        return RISKY_BARE_CALLS.get(func.id)
    return None


class RiskySyncDefIndexer(ast.NodeVisitor):
    """First pass: find plain ``def`` helpers whose direct body calls a
    known-blocking stdlib operation. A helper's body is scanned with
    nested ``def`` / ``async def`` / lambda bodies excluded (they do not
    execute when the outer helper returns). We only index top-level
    functions and methods of top-level classes — lambdas, deeply nested
    defs, and defs inside another ``async def`` cannot escape to an
    unrelated async-call site.
    """

    def __init__(self) -> None:
        # name -> (file-relative-or-absolute path str, lineno, reason)
        # Filled by module-level driver.
        self.risky: dict[str, tuple[str, int, str]] = {}

    def index_module(
        self,
        tree: ast.Module,
        path: Path,
        atomic_aliases: tuple[dict[str, str], set[str]] | None = None,
    ) -> None:
        if atomic_aliases is None:
            atomic_aliases = _collect_atomic_write_aliases(tree)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self._consider_def(node, path, atomic_aliases)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        self._consider_def(item, path, atomic_aliases)

    def _consider_def(
        self,
        node: ast.FunctionDef,
        path: Path,
        atomic_aliases: tuple[dict[str, str], set[str]],
    ) -> None:
        if node.name in GENERIC_HELPER_NAMES:
            # Name too generic to identify; see GENERIC_HELPER_NAMES comments.
            return
        reason = _sync_body_has_blocking_call(node, atomic_aliases)
        if reason is None:
            return
        # First-wins: if two helpers share a name, keep the first so the
        # message points at a real definition. Name collisions are rare
        # enough in this repo that this is fine.
        self.risky.setdefault(node.name, (str(path), node.lineno, reason))


def _collect_async_def_names(tree: ast.Module) -> set[str]:
    """Collect top-level + one-level-nested ``async def`` names from a
    module. Used to disambiguate name collisions: if a name is defined
    as both a sync helper (risky) and an async method elsewhere in
    scope, a call site ``await foo(...)`` is ambiguous under name-only
    matching, so we drop that name from the risky index rather than
    report false positives on the async side. Symmetric with the sync
    indexer's scope — top-level defs + methods of top-level classes.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef):
                    names.add(item.name)
    return names


def _sync_body_has_blocking_call(
    func: ast.FunctionDef,
    atomic_aliases: tuple[dict[str, str], set[str]] | None = None,
) -> str | None:
    """Scan ``func``'s body for a known-blocking call, descending into
    control-flow (if/for/with/try) but NOT into nested functions or
    lambdas — a nested function's code only runs when invoked, which by
    definition won't happen just because the outer helper is called.
    Returns a short reason string, or ``None``.
    """
    stack: list[ast.AST] = list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # prune nested scope
        if isinstance(node, ast.Call):
            reason = _describe_blocking_call(node, atomic_aliases)
            if reason is not None:
                return reason
        for child in ast.iter_child_nodes(node):
            stack.append(child)
    return None


# asyncio entry points whose argument must be an awaitable, mapped to the
# keyword name of that argument. ``gather`` takes every positional arg.
_ASYNCIO_AWAITABLE_ARG = {
    "wait_for": "fut",
    "shield": "arg",
    "ensure_future": "coro_or_future",
    "create_task": "coro",
    "run_coroutine_threadsafe": "coro",
}


# Receivers trusted for ``<receiver>.create_task(coro)``: event loops and
# ``asyncio.TaskGroup``. Two ways in: a plain name whose every binding in
# its resolving scope comes from one of the asyncio constructors below
# (``async with asyncio.TaskGroup() as group`` /
# ``loop = asyncio.get_running_loop()``, see ``_scope_info``), or,
# for attribute / call receivers such as ``self._loop`` that scope analysis
# cannot see, the same tail-name matching as the queue/thread/socket
# heuristics. A plain name is never trusted by its spelling alone. An unrelated
# ``scheduler.create_task(...)`` may accept an already-computed value, so it
# gets no suppression.
TASK_OWNER_CONSTRUCTORS = {
    "TaskGroup", "get_running_loop", "get_event_loop", "new_event_loop",
}
TASK_OWNER_EXACT = {
    "loop", "event_loop", "get_running_loop", "get_event_loop",
    "tg", "task_group", "taskgroup",
}
TASK_OWNER_SUFFIX = ("_loop", "_tg", "_task_group")


def _asyncio_is_module(tree: ast.AST) -> bool:
    """True when every binding of the name ``asyncio`` in the file is ``import asyncio``.

    All asyncio-based trust in this module (awaitable slots, task-owner
    constructors) keys on the spelling ``asyncio``. If anything else ever
    binds that name -- a parameter, an assignment, ``import x as asyncio``,
    ``from pkg import asyncio`` or ``*``, a def/class, ``except ... as asyncio`` --
    the whole file falls back to the plain checks rather than resolving
    which binding is live at each call.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == "asyncio":
            return False
        if isinstance(node, ast.arg) and node.arg == "asyncio":
            return False
        if isinstance(node, ast.Import):
            for alias in node.names:
                # ``import asyncio`` / ``import asyncio.subprocess`` bind the
                # package; any ``... as asyncio`` other than the plain module
                # binds something else.
                if alias.asname == "asyncio" and alias.name != "asyncio":
                    return False
        elif isinstance(node, ast.ImportFrom):
            # ``from pkg import *`` may bind ``asyncio`` too.
            if any((alias.asname or alias.name) in ("asyncio", "*") for alias in node.names):
                return False
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "asyncio":
                return False
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            if "asyncio" in node.names:
                return False
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            if node.name == "asyncio":
                return False
        elif isinstance(node, ast.MatchMapping) and node.rest == "asyncio":
            return False
    return True


def _is_task_owner_constructor(expr: ast.expr | None) -> bool:
    """``asyncio.TaskGroup()`` / ``asyncio.get_running_loop()`` and friends."""
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and isinstance(expr.func.value, ast.Name)
        and expr.func.value.id == "asyncio"
        and expr.func.attr in TASK_OWNER_CONSTRUCTORS
    )


def _annotations_are_postponed(tree: ast.Module) -> bool:
    """True under ``from __future__ import annotations`` (annotations never run)."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _definition_time_exprs(node: ast.AST, *, annotations: bool = True) -> list[ast.AST]:
    """Parts of a def / lambda / class statement evaluated in the enclosing scope.

    Decorators, argument defaults and annotations, the return annotation,
    and class bases / keywords run when the statement executes; only the
    body runs in the new scope. Pass ``annotations=False`` when they are
    postponed (``from __future__ import annotations``) and never evaluated.
    """
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords]
    args = node.args  # type: ignore[attr-defined]
    exprs: list[ast.AST] = [
        *getattr(node, "decorator_list", ()),
        *args.defaults,
        *(d for d in args.kw_defaults if d is not None),
    ]
    if not annotations:
        return exprs
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
        if arg is not None and arg.annotation is not None:
            exprs.append(arg.annotation)
    returns = getattr(node, "returns", None)
    if returns is not None:
        exprs.append(returns)
    return exprs


_DECLARED_GLOBAL = "global"
_DECLARED_NONLOCAL = "nonlocal"
# name -> trusted, or one of the markers above for a ``global`` / ``nonlocal``
# declaration (lookup continues at the module / in enclosing functions).
ScopeVerdicts = dict[str, bool | str]


def _scope_info(
    scope: ast.AST,
    *,
    annotations: bool = True,
    cache: dict[int, tuple[ScopeVerdicts, dict[str, bool], dict[str, bool]]] | None = None,
) -> tuple[ScopeVerdicts, dict[str, bool], dict[str, bool]]:
    """Task-owner verdicts of one scope, plus the writes that escape it.

    Mirrors Python scoping: a name bound anywhere in a function is local to
    the whole function, so the verdict is flow-insensitive. A local name is
    trusted only when every one of its bindings in this scope is
    ``name = asyncio.<constructor>()``, ``name: T = asyncio.<constructor>()``,
    ``(name := asyncio.<constructor>())`` or
    ``async with asyncio.<constructor>() as name`` (a bare annotation binds
    nothing). Parameters, any other assignment / loop / walrus target,
    ``del``, imports, ``except ... as``, match captures and nested def/class
    names make it untrusted. Every binding in a class body is untrusted:
    the namespace fills in execution order and a single verdict cannot say
    which reads happen before a trusted assignment.

    A name declared ``global`` / ``nonlocal`` here gets a marker instead of
    a verdict, and writes through the declaration -- from this scope or a
    nested one -- escape: ``global`` writes land on the module, ``nonlocal``
    writes on the nearest enclosing function that binds the name. The two
    returned dicts map escaping names to whether every such write was a
    constructor binding; the scope that owns the name folds them into its
    own verdict.

    Bodies of nested functions and classes, and comprehension targets, are
    their own scopes; definition-time expressions (decorators, defaults,
    annotations unless postponed, bases) and walrus targets inside a
    comprehension belong to this one.
    """
    if cache is not None and id(scope) in cache:
        return cache[id(scope)]
    is_module = isinstance(scope, ast.Module)
    is_class = isinstance(scope, ast.ClassDef)
    bindings: list[tuple[str, bool]] = []
    declared_global: set[str] = set()
    declared_nonlocal: set[str] = set()
    child_globals: list[tuple[str, bool]] = []
    child_nonlocals: list[tuple[str, bool]] = []
    owner_targets: set[int] = set()

    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = scope.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
            if arg is not None:
                bindings.append((arg.arg, False))
        stack: list[ast.AST] = [scope.body] if isinstance(scope, ast.Lambda) else list(scope.body)
    else:
        stack = list(getattr(scope, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if not isinstance(node, ast.Lambda):
                bindings.append((node.name, False))
            _child, escaping_globals, escaping_nonlocals = _scope_info(
                node, annotations=annotations, cache=cache,
            )
            child_globals.extend(escaping_globals.items())
            child_nonlocals.extend(escaping_nonlocals.items())
            stack.extend(_definition_time_exprs(node, annotations=annotations))
            continue
        if isinstance(node, ast.comprehension):
            stack.append(node.iter)
            stack.extend(node.ifs)
            continue
        if isinstance(node, ast.AnnAssign):
            if node.value is None:
                continue
            if isinstance(node.target, ast.Name) and _is_task_owner_constructor(node.value):
                owner_targets.add(id(node.target))
        elif isinstance(node, ast.Assign) and _is_task_owner_constructor(node.value):
            owner_targets.update(id(t) for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.NamedExpr) and _is_task_owner_constructor(node.value):
            owner_targets.add(id(node.target))
        elif isinstance(node, ast.AsyncWith):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name) and _is_task_owner_constructor(item.context_expr):
                    owner_targets.add(id(item.optional_vars))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bindings.append((node.id, id(node) in owner_targets))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    bindings.append(((alias.asname or alias.name).split(".")[0], False))
        elif isinstance(node, ast.Global):
            if not is_module:
                declared_global.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            declared_nonlocal.update(node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            bindings.append((node.name, False))
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bindings.append((node.rest, False))
        stack.extend(ast.iter_child_nodes(node))

    def merge(into: dict, name: str, trusted: bool) -> None:
        into[name] = into.get(name, True) and trusted

    verdicts: dict = {}
    escaping_globals: dict[str, bool] = {}
    escaping_nonlocals: dict[str, bool] = {}
    for name, trusted in bindings:
        if name in declared_global:
            merge(escaping_globals, name, trusted)
        elif name in declared_nonlocal:
            merge(escaping_nonlocals, name, trusted)
        else:
            merge(verdicts, name, trusted and not is_class)
    for name, trusted in child_globals:
        if is_module:
            # A nested ``global`` write binds the module name even when the
            # module itself never assigns it.
            merge(verdicts, name, trusted)
        else:
            merge(escaping_globals, name, trusted)
    for name, trusted in child_nonlocals:
        if is_module:
            continue
        if not is_class and name in verdicts:
            merge(verdicts, name, trusted)
        else:
            merge(escaping_nonlocals, name, trusted)
    for name in declared_global:
        verdicts[name] = _DECLARED_GLOBAL
    for name in declared_nonlocal:
        verdicts[name] = _DECLARED_NONLOCAL
    result = (verdicts, escaping_globals, escaping_nonlocals)
    if cache is not None:
        cache[id(scope)] = result
    return result


def _slot_calls(expr: ast.expr) -> list[ast.Call]:
    """Calls whose result becomes the value of an awaitable slot.

    A direct call, the value of an assignment expression
    (``(coro := ws.recv())``), either result branch of a conditional
    expression (``a.recv() if c else q.get()``), or the last operand of a
    boolean expression (``existing or ws.recv()``). Conditions, earlier
    boolean operands and nested argument calls keep the normal checks.
    """
    if isinstance(expr, ast.Call):
        return [expr]
    if isinstance(expr, ast.NamedExpr):
        return _slot_calls(expr.value)
    if isinstance(expr, ast.IfExp):
        return _slot_calls(expr.body) + _slot_calls(expr.orelse)
    if isinstance(expr, ast.BoolOp):
        # Only the last operand is the result whenever it is evaluated; an
        # earlier one may be skipped past when it is falsy (``b""``).
        return _slot_calls(expr.values[-1])
    return []


def _expanded_elements(arg: ast.expr) -> list[ast.expr]:
    """Element expressions of a statically visible ``*`` expansion.

    ``*(f(x) for x in xs)`` / ``*[a(), b()]`` → the element calls; any other
    starred value (``*coros``) yields nothing to inspect.
    """
    value = arg.value if isinstance(arg, ast.Starred) else arg
    if isinstance(value, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return [value.elt]
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return list(value.elts)
    return []


def _awaitable_argument_slots(
    call: ast.Call,
    is_task_owner_name: Callable[[str], bool] | None = None,
    *,
    asyncio_is_module: bool = True,
) -> list[ast.expr]:
    """Return the arguments of ``call`` that asyncio requires to be awaitable.

    Recognised forms: ``asyncio.wait_for`` / ``shield`` / ``ensure_future`` /
    ``create_task`` / ``run_coroutine_threadsafe`` (first argument, or the
    first element of a ``*`` literal), ``asyncio.gather`` (every positional
    argument, including the elements of a literal or comprehension
    expanded with ``*``), ``asyncio.as_completed`` (the elements of a
    literal or comprehension first argument), and
    ``<loop or TaskGroup>.create_task`` (see
    ``TASK_OWNER_CONSTRUCTORS`` / ``TASK_OWNER_EXACT``;
    ``is_task_owner_name`` resolves a plain-name receiver). Bare names such
    as an imported ``wait_for`` are not resolved, so a same-named repo
    helper can never hide a real blocking call. With ``asyncio_is_module``
    false (see ``_asyncio_is_module``) the ``asyncio.*`` forms are not
    trusted.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return []
    on_asyncio = (
        asyncio_is_module
        and isinstance(func.value, ast.Name)
        and func.value.id == "asyncio"
    )
    if on_asyncio and func.attr == "as_completed":
        # The first argument is an iterable of awaitables.
        if call.args and not isinstance(call.args[0], ast.Starred):
            return _expanded_elements(call.args[0])
        return [e for kw in call.keywords if kw.arg == "fs" for e in _expanded_elements(kw.value)]
    if on_asyncio and func.attr == "gather":
        slots: list[ast.expr] = []
        for a in call.args:
            if isinstance(a, ast.Starred):
                slots.extend(_expanded_elements(a))
            else:
                slots.append(a)
        return slots
    task_owner = False
    if func.attr == "create_task":
        receiver = func.value
        if isinstance(receiver, ast.Name):
            task_owner = is_task_owner_name is not None and is_task_owner_name(receiver.id)
        else:
            root = receiver
            while isinstance(root, (ast.Attribute, ast.Call, ast.Subscript)):
                root = root.func if isinstance(root, ast.Call) else root.value
            rooted_at_shadowed_asyncio = (
                not asyncio_is_module and isinstance(root, ast.Name) and root.id == "asyncio"
            )
            task_owner = not rooted_at_shadowed_asyncio and _tail_matches(
                _tail_name(receiver), TASK_OWNER_EXACT, TASK_OWNER_SUFFIX
            )
    if task_owner or (on_asyncio and func.attr in _ASYNCIO_AWAITABLE_ARG):
        if call.args:
            first = call.args[0]
            if not isinstance(first, ast.Starred):
                return [first]
            # ``wait_for(*[ws.recv(), 10])``: the first element of a literal
            # fills the slot. A comprehension's element also fills the later
            # arguments (``timeout``, ``loop``), so it gets no suppression.
            value = first.value
            return list(value.elts[:1]) if isinstance(value, (ast.List, ast.Tuple)) else []
        keyword = _ASYNCIO_AWAITABLE_ARG[func.attr]
        return [kw.value for kw in call.keywords if kw.arg == keyword]
    return []


class AsyncBlockingChecker(ast.NodeVisitor):
    def __init__(
        self,
        path: Path,
        source: str,
        risky_helpers: dict[str, tuple[str, int, str]] | None = None,
        ambiguous_async_names: set[str] | None = None,
        atomic_aliases: tuple[dict[str, str], set[str]] | None = None,
    ) -> None:
        self.path = path
        self.source_lines = source.splitlines()
        # Stack of "async" / "sync" — nearest function kind.
        self._func_stack: list[str] = []
        self.violations: list[tuple[int, int, str]] = []
        self._risky = risky_helpers or {}
        # Names defined as BOTH a risky sync helper and an ``async def``
        # somewhere in scope. At an ``await foo(...)`` site we cannot
        # statically tell which one the receiver binds to, so transitive
        # checks on those sites are skipped — but only when awaited.
        # A bare ``foo(...)`` (no await) is still checked against the
        # risky entry, since calling a coroutine without awaiting is a
        # separate bug and the sync variant is the only one that
        # runs-and-blocks when called unawaited.
        self._ambiguous_async_names = ambiguous_async_names or set()
        self._atomic_aliases = atomic_aliases or ({}, set())
        # ids of Call nodes that are the direct target of an ``await`` —
        # suppresses ONLY the queue/thread/socket tail-name heuristics in
        # ``_check_attribute_call``. Those heuristics guess at the
        # receiver's type from its identifier, and an ``await`` in front
        # proves the receiver is an asyncio object (awaiting a blocking
        # ``queue.Queue.get()`` is a TypeError at runtime). Direct
        # blocking stdlib calls and depth-1 transitive sync-helper
        # checks are *not* suppressed — a sync helper that blocks
        # synchronously and then returns an awaitable would still run
        # its blocking body on the event-loop thread before the await.
        # Indexed by id() to avoid mutating the AST.
        self._awaited_call_ids: set[int] = set()
        # ids of Call nodes sitting in an asyncio awaitable-argument slot
        # (see ``_awaitable_argument_slots``). They lift the same
        # queue/thread/socket heuristics as a direct ``await`` and nothing
        # else: unlike ``await foo()``, the ambiguous-name skip in the
        # depth-1 pass does not apply, so ``gather(foo())`` still reports a
        # risky sync ``foo`` exactly as it did before slots existed.
        self._slot_call_ids: set[int] = set()
        # ``(_scope_info verdicts, is_class_body)`` of the module and each
        # enclosing function / class body / comprehension, innermost last;
        # a plain-name receiver resolves in the innermost scope that binds
        # it. A class body is visible only to code directly inside it, not
        # to its methods or comprehensions (Python's own rule).
        self._task_owner_scopes: list[tuple[ScopeVerdicts, bool]] = []
        self._scope_cache: dict[int, tuple[ScopeVerdicts, dict[str, bool], dict[str, bool]]] = {}
        # Set per module in visit_Module; see ``_asyncio_is_module``.
        self._asyncio_is_module = True
        # Set per module in visit_Module; see ``_annotations_are_postponed``.
        self._annotations_run = True

    # ── function tracking ────────────────────────────────────────────────
    def visit_Module(self, node: ast.Module) -> None:
        self._asyncio_is_module = _asyncio_is_module(node)
        self._annotations_run = not _annotations_are_postponed(node)
        self._task_owner_scopes.append((self._verdicts(node), False))
        self.generic_visit(node)
        self._task_owner_scopes.pop()

    def _verdicts(self, scope: ast.AST) -> ScopeVerdicts:
        verdicts = _scope_info(scope, annotations=self._annotations_run, cache=self._scope_cache)[0]
        if not self._asyncio_is_module:
            # Constructor bindings are only meaningful on the real module.
            verdicts = dict.fromkeys(verdicts, False)
        return verdicts

    def _visit_function(self, node: ast.AST, kind: str) -> None:
        for expr in _definition_time_exprs(node, annotations=self._annotations_run):
            self.visit(expr)
        self._func_stack.append(kind)
        self._task_owner_scopes.append((self._verdicts(node), False))
        body = node.body  # type: ignore[attr-defined]
        for child in body if isinstance(body, list) else [body]:
            self.visit(child)
        self._task_owner_scopes.pop()
        self._func_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Decorators, bases and keywords run in the enclosing scope; the
        # body runs in the class namespace.
        for expr in _definition_time_exprs(node, annotations=self._annotations_run):
            self.visit(expr)
        self._task_owner_scopes.append((self._verdicts(node), True))
        for stmt in node.body:
            self.visit(stmt)
        self._task_owner_scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, "async")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, "sync")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_function(node, "sync")

    def _visit_comprehension(self, node: ast.AST) -> None:
        # Comprehension targets live in their own scope and shadow any
        # enclosing task owner of the same name. The outermost iterable is
        # evaluated in the enclosing scope, before any target is bound.
        generators = node.generators  # type: ignore[attr-defined]
        self.visit(generators[0].iter)
        targets = {
            n.id
            for gen in generators
            for n in ast.walk(gen.target)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
        }
        self._task_owner_scopes.append((dict.fromkeys(targets, False), False))
        for index, gen in enumerate(generators):
            self.visit(gen.target)
            if index:
                self.visit(gen.iter)
            for cond in gen.ifs:
                self.visit(cond)
        for field in ("elt", "key", "value"):
            child = getattr(node, field, None)
            if child is not None:
                self.visit(child)
        self._task_owner_scopes.pop()

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def _is_task_owner_name(self, name: str) -> bool:
        for depth, (verdicts, is_class_body) in enumerate(reversed(self._task_owner_scopes)):
            if is_class_body and depth:
                continue
            verdict = verdicts.get(name)
            if verdict is None or verdict == _DECLARED_NONLOCAL:
                continue
            if verdict == _DECLARED_GLOBAL:
                return self._task_owner_scopes[0][0].get(name) is True
            return bool(verdict)
        return False

    # ── helpers ─────────────────────────────────────────────────────────
    def _in_async_context(self) -> bool:
        return bool(self._func_stack) and self._func_stack[-1] == "async"

    def _line_has_noqa(self, lineno: int) -> bool:
        if 1 <= lineno <= len(self.source_lines):
            return NOQA_TOKEN in self.source_lines[lineno - 1]
        return False

    def _flag(self, node: ast.AST, message: str) -> None:
        lineno = getattr(node, "lineno", 0)
        col = getattr(node, "col_offset", 0) + 1
        if self._line_has_noqa(lineno):
            return
        # Report each call site once. Direct and depth-1 transitive rules may both match
        # (`atomic_write_json(...)` is known blocking and a synchronous def containing
        # atomic_write_text). Duplicate reports are noise; retain first match,
        # the more precise direct rule.
        if any(pos == (lineno, col) for pos in ((v[0], v[1]) for v in self.violations)):
            return
        self.violations.append((lineno, col, message))

    def visit_Await(self, node: ast.Await) -> None:
        # Mark the directly-awaited call for targeted suppression in
        # visit_Call (see _awaited_call_ids docstring). Arguments inside
        # X(...) are NOT marked here — only the asyncio awaitable slots
        # in _mark_awaitable_arguments are, and even there the direct
        # blocking-stdlib check still runs: ``await gather(requests.get(...))``
        # evaluates requests.get synchronously before the await.
        if isinstance(node.value, ast.Call):
            self._awaited_call_ids.add(id(node.value))
        self.generic_visit(node)

    def _mark_awaitable_arguments(self, call: ast.Call) -> None:
        # Calls passed where asyncio requires an awaitable get the same
        # heuristic suppression as a directly awaited call:
        # ``asyncio.wait_for(ws.recv(), 10)`` with a blocking
        # ``socket.recv()`` would hand bytes to wait_for and raise
        # TypeError, so the argument must be a coroutine/future. Only
        # these positions are marked; other arguments (and the outer
        # call itself) keep the normal checks.
        for arg in _awaitable_argument_slots(
            call, self._is_task_owner_name, asyncio_is_module=self._asyncio_is_module,
        ):
            for slot_call in _slot_calls(arg):
                self._slot_call_ids.add(id(slot_call))

    # ── the actual checks ────────────────────────────────────────────────
    def visit_Call(self, node: ast.Call) -> None:
        self._mark_awaitable_arguments(node)
        if self._in_async_context():
            awaited = id(node) in self._awaited_call_ids
            # queue/thread/socket heuristics are name-based type guesses;
            # an ``await`` (or an asyncio awaitable slot) proves the
            # receiver is an asyncio object.
            if (
                isinstance(node.func, ast.Attribute)
                and not awaited
                and id(node) not in self._slot_call_ids
            ):
                self._check_attribute_call(node)
            # Direct blocking stdlib calls (requests.get, shutil.copy, …)
            # and depth-1 transitive sync-helper dispatch are checked
            # regardless of await — a sync helper that blocks before
            # returning an awaitable still blocks the loop thread.
            self._check_direct_blocking_call(node)
            self._check_transitive_sync_helper(node, awaited=awaited)
        self.generic_visit(node)

    def _check_direct_blocking_call(self, call: ast.Call) -> None:
        """Flag known-blocking stdlib calls (PIL.Image.open, Fernet
        encrypt/decrypt, time.sleep, shutil.*, requests.*, etc.) that
        appear inline inside an ``async def`` body. Uses the same
        recogniser as the depth-1 risky-helper indexer for symmetry —
        anything blocking-enough to mark a sync helper as risky is also
        blocking when written inline.
        """
        reason = _describe_blocking_call(call, self._atomic_aliases)
        if reason is None:
            return
        # Avoid duplicating with the existing queue/thread/socket flags —
        # those carry richer messages from _check_attribute_call.
        if reason in (
            "queue.Queue.get()",
            "Thread/Process.join()",
        ) or reason.startswith("blocking socket."):
            return
        self._flag(
            call,
            f"{reason} blocks the event loop; wrap in await asyncio.to_thread(...).",
        )

    def _check_transitive_sync_helper(self, call: ast.Call, *, awaited: bool) -> None:
        """Depth-1: flag ``foo(...)`` or ``obj.foo(...)`` where ``foo``
        is the name of an indexed risky sync helper. We intentionally
        match on tail name only — resolving receivers would need import
        tracking, and in this repo the helper names we care about
        (``compress_screenshot``, ``load_cookies_from_file``, etc.) are
        distinctive enough that collisions are rare.

        When ``awaited`` is True and the name also exists as an
        ``async def`` somewhere in scope, we skip — the receiver most
        likely binds to the async variant (awaiting the sync variant's
        non-awaitable return is a runtime TypeError). Non-awaited
        calls are still checked: a bare ``foo(...)`` invocation of a
        sync blocking helper is exactly the case this pass was
        designed to catch.
        """
        name = _tail_name(call.func)
        if not name:
            return
        if awaited and name in self._ambiguous_async_names:
            return
        hit = self._risky.get(name)
        if hit is None:
            return
        def_path, def_lineno, reason = hit
        self._flag(
            call,
            f"blocking sync helper '{name}' (defined at {def_path}:{def_lineno}, "
            f"uses {reason}) called directly from async context; wrap the call "
            f"in await asyncio.to_thread(...).",
        )

    def _check_attribute_call(self, call: ast.Call) -> None:
        attr = call.func.attr  # type: ignore[union-attr]
        receiver = call.func.value  # type: ignore[union-attr]
        tail = _tail_name(receiver)
        if not tail:
            return

        if attr == "get" and _tail_matches(tail, QUEUE_EXACT, QUEUE_SUFFIX) and self._queue_get_is_blocking(call):
            self._flag(
                call,
                "queue.Queue.get() blocks the event loop; use asyncio.Queue "
                "or `await asyncio.to_thread(q.get, ...)`.",
            )
            return

        if attr == "join" and _tail_matches(tail, THREAD_EXACT, THREAD_SUFFIX):
            self._flag(
                call,
                "Thread/Process.join() blocks the event loop; use "
                "`await asyncio.to_thread(t.join, timeout)`.",
            )
            return

        if _tail_matches(tail, SOCKET_EXACT, SOCKET_SUFFIX):
            if attr == "recv":
                self._flag(
                    call,
                    "Blocking socket.recv(); use asyncio.open_connection() "
                    "/ loop.sock_recv() / asyncio.to_thread.",
                )
                return
            if attr == "accept":
                self._flag(
                    call,
                    "Blocking socket.accept(); use asyncio.start_server() "
                    "or loop.sock_accept().",
                )
                return
            if attr == "connect":
                self._flag(
                    call,
                    "Blocking socket.connect(); use asyncio.open_connection() "
                    "or loop.sock_connect().",
                )
                return

    @staticmethod
    def _queue_get_is_blocking(call: ast.Call) -> bool:
        """Signature: ``queue.Queue.get(block=True, timeout=None)``.

        Non-blocking forms (NOT flagged):
            q.get(False)                  # positional block=False
            q.get(block=False)
            q.get(True, 0)                # positional block=True, timeout=0
            q.get(timeout=0)
        Everything else (including ``q.get(False, 5)`` or calls we can't
        statically resolve) is treated as potentially blocking.
        """
        block_arg: ast.expr | None = call.args[0] if len(call.args) >= 1 else None
        timeout_arg: ast.expr | None = call.args[1] if len(call.args) >= 2 else None
        for kw in call.keywords:
            if kw.arg == "block":
                block_arg = kw.value
            elif kw.arg == "timeout":
                timeout_arg = kw.value

        if isinstance(block_arg, ast.Constant) and block_arg.value is False:
            return False
        if isinstance(timeout_arg, ast.Constant) and timeout_arg.value == 0:
            return False
        return True


def _iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_file() and p.suffix == ".py":
            yield p
        elif p.is_dir():
            yield from sorted(p.rglob("*.py"))


def _parse_file(path: Path) -> tuple[str, ast.Module] | None:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"{path}: skipped — {e}", file=sys.stderr)
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"{path}:{e.lineno}: syntax error — {e.msg}", file=sys.stderr)
        return None
    return source, tree


def check_file(
    path: Path,
    source: str,
    tree: ast.Module,
    risky_helpers: dict[str, tuple[str, int, str]] | None = None,
    ambiguous_async_names: set[str] | None = None,
    atomic_aliases: tuple[dict[str, str], set[str]] | None = None,
) -> list[tuple[int, int, str]]:
    if atomic_aliases is None:
        atomic_aliases = _collect_atomic_write_aliases(tree)
    checker = AsyncBlockingChecker(
        path,
        source,
        risky_helpers=risky_helpers,
        ambiguous_async_names=ambiguous_async_names,
        atomic_aliases=atomic_aliases,
    )
    checker.visit(tree)
    return checker.violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check for blocking calls in async def bodies.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan (default: main event-loop surface)",
    )
    args = parser.parse_args(argv)

    raw_paths = args.paths or DEFAULT_PATHS
    targets = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw_paths]

    # Pass 1: parse every file once, build the risky-sync-def index.
    parsed: list[tuple[Path, str, ast.Module]] = []
    indexer = RiskySyncDefIndexer()
    async_names: set[str] = set()
    for file in _iter_python_files(targets):
        parsed_file = _parse_file(file)
        if parsed_file is None:
            continue
        source, tree = parsed_file
        parsed.append((file, source, tree))
        rel = file.relative_to(REPO_ROOT) if file.is_relative_to(REPO_ROOT) else file
        # Re-seed indexer.risky entries with the relative path for nicer
        # error messages, but preserve first-wins.
        local_indexer = RiskySyncDefIndexer()
        local_indexer.index_module(
            tree,
            rel,
            atomic_aliases=_collect_atomic_write_aliases(tree),
        )
        for name, info in local_indexer.risky.items():
            indexer.risky.setdefault(name, info)
        async_names |= _collect_async_def_names(tree)

    # Build the "ambiguous" set — names that exist as BOTH a risky
    # sync helper and an ``async def`` somewhere in scope. Used by
    # visit_Call to skip the transitive check only at ``await foo(...)``
    # sites where the receiver could bind either way. Non-awaited
    # ``foo(...)`` calls are still checked against the risky entry:
    # even if there's an async variant somewhere, a bare call that
    # hits the sync one blocks, and our report should surface it.
    # Example trigger: ``brain/computer_use.py`` defines a sync
    # ``run_instruction`` while openclaw/browser_use adapters
    # expose ``async def run_instruction`` — ``await adapter.run_X()``
    # is fine, a hypothetical bare ``run_instruction(...)`` is not.
    ambiguous_async_names = async_names & set(indexer.risky)

    # Pass 2: walk async bodies for direct blocking + depth-1 transitive.
    total = 0
    for file, source, tree in parsed:
        for lineno, col, msg in check_file(
            file,
            source,
            tree,
            risky_helpers=indexer.risky,
            ambiguous_async_names=ambiguous_async_names,
        ):
            rel = file.relative_to(REPO_ROOT) if file.is_relative_to(REPO_ROOT) else file
            print(f"{rel}:{lineno}:{col}  {CODE}  {msg}")
            total += 1

    if total:
        print(
            f"\n{total} blocking-call violation(s) found inside async def bodies.\n"
            "Fix by awaiting the async equivalent or wrapping in "
            "`await asyncio.to_thread(...)`. Add `# noqa: ASYNC_BLOCK — <reason>` "
            "only when the call genuinely runs off the event loop.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
