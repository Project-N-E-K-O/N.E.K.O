#!/usr/bin/env python3
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
   plus the queue/thread/socket tail-name heuristics above).
2. Walk every ``async def`` body again, and for each bare ``foo(...)``
   or ``obj.foo(...)`` call match the tail name against the risky index.
   A hit is reported as: *blocking sync helper '<name>' ... called
   directly from async context; wrap the call in
   ``await asyncio.to_thread(...)``*.

We deliberately stop at depth 1 — tracing deeper chains needs type
inference and/or module-level import resolution, and name-based
heuristics past depth 1 produce more noise than signal. Imports are not
resolved; a helper re-exported or aliased at import time will slip
through. That is a known trade-off — easy to add later, hard to make
quiet today. False positives from name collisions can be silenced per
line with ``# noqa: ASYNC_BLOCK — <reason>``.

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
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = [
    "main_server.py",
    "main_logic",
    "main_routers",
    "utils",
    # memory_server.py + its import chain (memory/ package)
    "memory_server.py",
    "memory",
    # agent_server.py + its import chain (brain/ package; main_logic already
    # in scope above)
    "agent_server.py",
    "brain",
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
    # PIL
    ("Image", "open"): "PIL.Image.open",
    ("_PILImage", "open"): "PIL.Image.open",
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
}


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


def _describe_blocking_call(call: ast.Call) -> str | None:
    """If the call matches a known blocking stdlib operation, return a
    short human-readable description; otherwise ``None``.

    This is the knowledge used by the depth-1 transitive pass to decide
    whether a plain ``def`` is risky to call from an async context.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        receiver_tail = _tail_name(func.value)
        attr = func.attr
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
        if _tail_matches(receiver_tail, SOCKET_EXACT, SOCKET_SUFFIX) and attr in {"recv", "accept", "connect"}:
            return f"blocking socket.{attr}()"
        return None
    if isinstance(func, ast.Name):
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

    def index_module(self, tree: ast.Module, path: Path) -> None:
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self._consider_def(node, path)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        self._consider_def(item, path)

    def _consider_def(self, node: ast.FunctionDef, path: Path) -> None:
        reason = _sync_body_has_blocking_call(node)
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


def _sync_body_has_blocking_call(func: ast.FunctionDef) -> str | None:
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
            reason = _describe_blocking_call(node)
            if reason is not None:
                return reason
        for child in ast.iter_child_nodes(node):
            stack.append(child)
    return None


class AsyncBlockingChecker(ast.NodeVisitor):
    def __init__(
        self,
        path: Path,
        source: str,
        risky_helpers: dict[str, tuple[str, int, str]] | None = None,
        ambiguous_async_names: set[str] | None = None,
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

    # ── function tracking ────────────────────────────────────────────────
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._func_stack.append("async")
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_stack.append("sync")
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._func_stack.append("sync")
        self.generic_visit(node)
        self._func_stack.pop()

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
        self.violations.append((lineno, col, message))

    def visit_Await(self, node: ast.Await) -> None:
        # Mark the directly-awaited call for targeted suppression in
        # visit_Call (see _awaited_call_ids docstring). Arguments inside
        # X(...) are NOT marked — a blocking call passed as an argument
        # (``await gather(requests.get(...))``) is still evaluated
        # synchronously before the await.
        if isinstance(node.value, ast.Call):
            self._awaited_call_ids.add(id(node.value))
        self.generic_visit(node)

    # ── the actual checks ────────────────────────────────────────────────
    def visit_Call(self, node: ast.Call) -> None:
        if self._in_async_context():
            awaited = id(node) in self._awaited_call_ids
            # queue/thread/socket heuristics are name-based type guesses;
            # an ``await`` proves the receiver is an asyncio object.
            if isinstance(node.func, ast.Attribute) and not awaited:
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
        reason = _describe_blocking_call(call)
        if reason is None:
            return
        # Avoid duplicating with the existing queue/thread/socket flags —
        # those carry richer messages from _check_attribute_call.
        if reason in ("queue.Queue.get()", "Thread/Process.join()") or reason.startswith(
            "blocking socket."
        ):
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
) -> list[tuple[int, int, str]]:
    checker = AsyncBlockingChecker(
        path,
        source,
        risky_helpers=risky_helpers,
        ambiguous_async_names=ambiguous_async_names,
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
        local_indexer.index_module(tree, rel)
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
    # ``run_instruction`` while openclaw/openfang/browser_use adapters
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
