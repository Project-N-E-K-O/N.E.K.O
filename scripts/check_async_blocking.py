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
]

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


class AsyncBlockingChecker(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source_lines = source.splitlines()
        # Stack of "async" / "sync" — nearest function kind.
        self._func_stack: list[str] = []
        self.violations: list[tuple[int, int, str]] = []

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

    # ── the actual checks ────────────────────────────────────────────────
    def visit_Call(self, node: ast.Call) -> None:
        if self._in_async_context() and isinstance(node.func, ast.Attribute):
            self._check_attribute_call(node)
        self.generic_visit(node)

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


def check_file(path: Path) -> list[tuple[int, int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"{path}: skipped — {e}", file=sys.stderr)
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"{path}:{e.lineno}: syntax error — {e.msg}", file=sys.stderr)
        return []
    checker = AsyncBlockingChecker(path, source)
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

    total = 0
    for file in _iter_python_files(targets):
        for lineno, col, msg in check_file(file):
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
