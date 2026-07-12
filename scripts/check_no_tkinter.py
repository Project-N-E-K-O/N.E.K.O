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

"""Static check: forbid `tkinter` (and submodules) anywhere in the repo.

Why this exists — architecture rule
-----------------------------------
**This codebase is split frontend / backend, and all GUI operations
belong on the frontend.** The Python side (FastAPI + uvicorn + asyncio)
is a headless service: it serves HTTP, talks to LLMs, manages state,
runs background workers. It does NOT open windows, render dialogs, or
own a Tcl/Tk event loop. Anything the user clicks, drags, or sees lives
in the Electron main/renderer process (Node.js / Chromium) — directory
pickers via Electron's `dialog.showOpenDialog`, screenshot framing via
`desktopCapturer` + a renderer-side overlay, app chrome via the
existing HTML/React UI.

`tkinter` is the canonical violation of that split. Every time someone
reaches for it, they're putting a UI widget toolkit into the backend,
which:

1. Wires a Tcl/Tk event loop into a process that is supposed to be
   request-driven and async. The two loops fight; you end up with
   threading hacks, focus glitches, "why is my window behind
   everything", and `mainloop()` calls inside FastAPI handlers.
2. Couples backend behaviour to the user's local display server. The
   backend stops being relocatable — it can't run remotely (envisioned
   by `NEKO_ACTIVITY_TRACKER_REMOTE`), can't run headless under
   Docker/CI without a virtual framebuffer, and can't be exercised by
   ordinary HTTP integration tests.
3. Drags Tcl/Tk into the packaged dist (tens of MB of runtime + DLLs).
4. Crashes the WHOLE app under Nuitka. Concrete past incident: PR
   #1014's Windows `_run_windows_interactive_screenshot` opened a
   tk overlay for screenshot region selection. Nuitka builds without
   ``--enable-plugin=tk-inter`` (the default) make ``tkinter.__init__``
   raise ``SystemExit("Nuitka: Need to use '--enable-plugin=tk-inter'
   option…")`` on the first ``tk.Tk()`` call. ``SystemExit`` inherits
   from ``BaseException`` not ``Exception``, so the request handler's
   ``try/except Exception`` doesn't contain it — it escaped the asyncio
   worker thread, killed uvicorn, and Electron tore down with its dead
   Python child. End-user symptom: press the screenshot button, the
   entire app closes.

The fix is not "wrap it in `except BaseException`". The fix is "the
backend should never have been opening a window in the first place".
That's what this lint enforces.

What goes where (rule of thumb)
-------------------------------
* **Directory / file pickers** → Electron's `dialog` module (renderer
  → main IPC). For non-Electron contexts (raw `index.html` in browser,
  CLI scripts) fall back to the platform-native bridges already in
  `main_routers/storage_location_router.py`: PowerShell on Windows,
  osascript on macOS, zenity/kdialog/yad on Linux. None of those open
  Tk; they shell out to the OS dialog. If the native bridge fails,
  surface `_DirectoryPickerUnavailable` to the frontend and let the
  user type the path manually — do NOT add a "tk fallback".
* **Screenshot region selection** → Electron's `desktopCapturer` plus a
  transparent BrowserWindow overlay in the renderer. Code path is
  already in `static/app/app-buttons.js::captureDesktopRegionDirectly`.
* **Status notifications, error dialogs, confirmation modals** →
  existing React/HTML UI in the renderer. The backend reports state via
  HTTP/WebSocket; the frontend renders it.
* **Anything else GUI** → frontend.

What it flags
-------------
Any of the following at module scope, function scope, or inside ``if``
/ ``try`` blocks (the AST walker descends everywhere):

    import tkinter
    import tkinter.filedialog
    import tkinter as tk
    from tkinter import filedialog
    from tkinter.ttk import Frame
    from tkinter import Tk as Root

Comments, docstrings, and string literals mentioning the name are NOT
flagged — only real ``import`` statements. That keeps kill-warning
comments ("don't add tkinter back") legal.

Suppression
-----------
None. The architecture rule is the whole point — a per-line escape
hatch would let "just this once" creep back in. If you genuinely need
tkinter (you almost certainly don't), delete this script in the same
PR and justify in the description why this particular GUI operation
must live in the Python process instead of the Electron renderer.

Output
------
Every violation prints as ``path:line:col  NO_TKINTER  message``. Exit
status is 1 when any violation is found, 0 otherwise.

Usage:
    python scripts/check_no_tkinter.py [paths...]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PATHS: list[str] = ["."]

EXCLUDE_DIRS = {
    ".venv",
    "venv",
    "frontend",
    "dist",
    "build",
    "node_modules",
    ".git",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "plugin/plugins",  # third-party plugin payloads, not our code
}

EXCLUDE_FILES = {
    "scripts/check_no_tkinter.py",
}

CODE = "NO_TKINTER"

BANNED_PACKAGES = {
    "tkinter",
}


def _is_banned(module_name: str | None) -> str | None:
    if not module_name:
        return None
    head = module_name.split(".", 1)[0]
    if head in BANNED_PACKAGES:
        return head
    return None


class TkinterImportChecker(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[tuple[int, int, str]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            banned = _is_banned(alias.name)
            if banned is not None:
                self.violations.append(
                    (
                        node.lineno,
                        node.col_offset + 1,
                        f"`import {alias.name}` is forbidden — GUI operations belong "
                        f"on the Electron frontend (Node.js renderer/main), not the "
                        f"Python backend. The backend is a headless HTTP/async "
                        f"service; opening Tk windows from it breaks the "
                        f"frontend/backend split, blocks remote/headless "
                        f"deployment, and crashes Nuitka builds. See module "
                        f"docstring for what to use instead.",
                    )
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        banned = _is_banned(node.module)
        if banned is not None:
            names = ", ".join(a.name for a in node.names) or "*"
            self.violations.append(
                (
                    node.lineno,
                    node.col_offset + 1,
                    f"`from {node.module} import {names}` is forbidden — GUI "
                    f"operations belong on the Electron frontend (Node.js "
                    f"renderer/main), not the Python backend. The backend is a "
                    f"headless HTTP/async service; opening Tk windows from it "
                    f"breaks the frontend/backend split, blocks remote/headless "
                    f"deployment, and crashes Nuitka builds. See module docstring "
                    f"for what to use instead.",
                )
            )


def _is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()
    if rel in EXCLUDE_FILES:
        return True
    for ex in EXCLUDE_DIRS:
        if "/" in ex and (rel == ex or rel.startswith(ex + "/")):
            return True
    return False


def _iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_file():
            if p.suffix == ".py" and not _is_excluded(p):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if not _is_excluded(f):
                    yield f


def _parse_file(path: Path) -> ast.Module | None:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"{path}: skipped — {e}", file=sys.stderr)
        return None
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"{path}:{e.lineno}: syntax error — {e.msg}", file=sys.stderr)
        return None


def check_file(path: Path, tree: ast.Module) -> list[tuple[int, int, str]]:
    checker = TkinterImportChecker(path)
    checker.visit(tree)
    return checker.violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid `tkinter` imports anywhere in the repo."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan (default: entire repo, minus EXCLUDE_DIRS).",
    )
    args = parser.parse_args(argv)

    raw_paths = args.paths or DEFAULT_PATHS
    targets = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw_paths]

    total = 0
    for file in _iter_python_files(targets):
        tree = _parse_file(file)
        if tree is None:
            continue
        for lineno, col, msg in check_file(file, tree):
            rel = file.relative_to(REPO_ROOT) if file.is_relative_to(REPO_ROOT) else file
            print(f"{rel}:{lineno}:{col}  {CODE}  {msg}")
            total += 1

    if total:
        print(
            f"\n{total} forbidden-import violation(s) found.\n"
            "Architecture rule: this codebase splits frontend/backend, and ALL GUI "
            "operations belong on the Electron frontend (Node.js renderer/main), "
            "not the Python backend. The backend is a headless service — it serves "
            "HTTP, talks to LLMs, manages state. It does not own a Tcl/Tk event "
            "loop, does not open windows, and must remain relocatable (remote "
            "deployment, headless CI). Reach for Electron's `dialog` / "
            "`desktopCapturer` / a renderer-side React modal instead; if Electron "
            "isn't available in the context (raw browser, CLI script), use the "
            "platform-native shell bridges in storage_location_router.py "
            "(PowerShell / osascript / zenity-kdialog-yad) and surface "
            "`_DirectoryPickerUnavailable` on failure rather than falling back to "
            "Tk. Past incident proving this matters: PR #1014's Windows tk "
            "screenshot overlay crashed the whole app under Nuitka builds without "
            "`--enable-plugin=tk-inter` (SystemExit escaped the asyncio worker and "
            "killed uvicorn). If you genuinely need tkinter, delete this script in "
            "the same PR and explain why this particular GUI operation must live "
            "in the Python process instead of the renderer.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
