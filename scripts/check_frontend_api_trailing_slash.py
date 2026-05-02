#!/usr/bin/env python3
"""Static check: forbid trailing-slash ``/api/...`` URL literals in frontend code.

Why this exists
---------------
Counterpart to ``scripts/check_api_trailing_slash.py``. The backend script
forbids declaring trailing-slash routes; this one forbids calling them.

If frontend code does ``fetch('/api/foo/')`` while the backend route is
``/api/foo``, FastAPI/Starlette 307-redirects to the no-slash form with an
**absolute** ``Location`` built from the request ``Host``. Behind a reverse
proxy that doesn't preserve ``Host`` (or with ``proxy_headers`` off, i.e.
``NEKO_BEHIND_PROXY != 1``), that absolute URL points at the internal
``127.0.0.1:<port>`` and the browser dies with ``ERR_CONNECTION_REFUSED``.
PR #938 (chara_manager regression on LAN reverse proxies) was exactly this
bug. See ``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") and
``main_routers/characters_router.py`` docstring for the full write-up.

What it flags
-------------
String literals like ``'/api/...'``, ``"/api/..."``, ```/api/...``` whose
last character before the closing quote is ``/``. Quote types covered:
single, double, backtick.

What it does NOT flag (intentional)
-----------------------------------
* **Prefix builders** — a ``/api/...`` literal whose closing quote is
  immediately followed by a string-concat continuation (``+``, template
  ``${`` interpolation closing brace, etc.). Example:
  ``'/api/characters/catgirl/' + encodeURIComponent(name)`` is fine — the
  trailing ``/`` is a path separator before an appended segment, the final
  URL still has no trailing slash.
* **The ``/api/`` prefix alone** — too short to be a real endpoint, very
  likely a base-URL constant that gets things appended.

The detection is a regex check rather than a JS parser to keep CI fast and
zero-dep. Edge cases (e.g. a literal that's stored in a variable and only
*sometimes* concatenated) will produce false negatives, which is the
acceptable failure mode — we'd rather miss an exotic dynamic call than flag
every prefix-builder.

Scope
-----
Default: ``static/``, ``frontend/``, ``templates/``. Pass paths explicitly
to scan elsewhere.

Suppression
-----------
Add ``// noqa: API_TRAILING_SLASH`` on the same line for JS/TS or
``<!-- noqa: API_TRAILING_SLASH -->`` for HTML if you genuinely need the
trailing slash (e.g. you're calling a third-party API that requires it).

Output
------
Every violation prints as ``path:line:col  API_TRAILING_SLASH  message``.
Exit status is 1 when any violation is found, 0 otherwise.

Usage::

    uv run python scripts/check_frontend_api_trailing_slash.py [paths...]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PATHS: list[str] = ["static", "frontend", "templates"]

EXCLUDE_DIRS = {
    ".venv",
    "venv",
    "dist",
    "build",
    "node_modules",
    ".git",
    "__pycache__",
    ".vite",
    ".cache",
    ".next",
    "coverage",
    "plugin/plugins",  # third-party plugin payloads, not our code
}

EXCLUDE_FILES: set[str] = {
    "scripts/check_frontend_api_trailing_slash.py",
}

# File extensions worth scanning. We deliberately skip ``.json`` (locale
# strings, package manifests) — they'd produce a flood of false hits if
# we ever shipped an API URL inside one, but in practice they don't.
SCAN_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".html", ".htm"}

CODE = "API_TRAILING_SLASH"

# Match an ``/api/...`` string literal. Three quote variants. We capture
# the path part separately from any optional ``?query`` / ``#fragment``
# tail so the violation check can ask "did the URL PATH end with '/'?",
# which is the right question — Starlette's 307 fires on path-vs-route
# mismatch, query string is irrelevant. Reported by Codex on PR #1082:
# the previous regex hard-coded ``/<close-quote>`` and silently missed
# ``fetch('/api/foo/?x=1')`` even though it still 307s.
#
# Path segment chars: anything except quote, ``+`` (concat continuation
# heuristic), whitespace, ``?`` and ``#`` (those start the qf tail).
# Slash IS allowed in path so multi-segment URLs match. Require at least
# one char after ``/api/`` to rule out the bare prefix.
_LITERAL_RE = re.compile(
    r"""(?P<quote>['"`])/api/(?P<urlpath>[^'"`+\s?#]+)(?P<qf>[?#][^'"`+\s]*)?(?P=quote)""",
)

# After the closing quote, what comes next? If the next non-whitespace token
# is ``+`` (string concat) or ``,`` ``)`` ``;`` ``}`` ``]`` (call-site /
# statement terminator) we can decide. ``+`` → prefix builder, allow.
# Anything else (terminator) → standalone URL, flag.
_NEXT_TOKEN_RE = re.compile(r"\s*(\+|[,);:\]}\n])")

# Same-line suppression marker. Mirrors how check_prompt_hygiene does noqa.
_NOQA_RE = re.compile(r"(?:#|//|<!--)\s*noqa\s*:\s*API_TRAILING_SLASH", re.IGNORECASE)


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


def _iter_source_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if not p.exists():
            continue
        if p.is_file():
            if p.suffix.lower() in SCAN_SUFFIXES and not _is_excluded(p):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in SCAN_SUFFIXES and not _is_excluded(f):
                    yield f


def _check_text(source: str) -> Iterator[tuple[int, int, str, str]]:
    """Yield (lineno, col, literal, suggestion) for each violation."""
    # Build a line-offset table once so we can map absolute char offsets
    # back to (line, col) without re-scanning.
    line_starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            line_starts.append(i + 1)

    def _locate(pos: int) -> tuple[int, int]:
        # Binary search would be faster but lines are cheap here.
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, pos - line_starts[lo] + 1

    for m in _LITERAL_RE.finditer(source):
        urlpath = m.group("urlpath")
        # The actual violation check: did the URL **path** end with '/'?
        # The query/fragment tail (qf) is irrelevant — Starlette's 307
        # fires on path-vs-route mismatch, query string isn't part of it.
        if not urlpath.endswith("/"):
            continue

        # Skip if this line carries a noqa marker for this code.
        line_no, col = _locate(m.start())
        line_end_idx = source.find("\n", m.start())
        if line_end_idx == -1:
            line_end_idx = len(source)
        line_text = source[line_starts[line_no - 1]:line_end_idx]
        if _NOQA_RE.search(line_text):
            continue

        # Look at what follows the close quote — if it's '+' or '${' or
        # similar concat continuation, this is a prefix builder, not a
        # standalone URL.
        after = source[m.end():m.end() + 64]
        nxt = _NEXT_TOKEN_RE.match(after)
        if nxt and nxt.group(1) == "+":
            continue
        # Template-literal interpolation: ``/api/foo/${id}`` — the regex
        # won't match those at all (they use ``${`` mid-literal, the
        # closing-quote check fails). So no extra branch needed here.

        qf = m.group("qf") or ""
        suggestion = f"/api/{urlpath.rstrip('/')}{qf}"
        literal = m.group(0)
        yield line_no, col, literal, suggestion


class _ReadFailed(Exception):
    """Raised by check_file when a file can't be read; main() turns this
    into a non-zero exit so a read failure doesn't silently pass the lint
    (fail-closed). Reported by CodeRabbit on PR #1082."""


def check_file(path: Path) -> list[tuple[int, int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"{path}: read failed — {e}", file=sys.stderr)
        raise _ReadFailed(str(path)) from e
    out: list[tuple[int, int, str]] = []
    for lineno, col, literal, suggestion in _check_text(source):
        out.append(
            (
                lineno,
                col,
                f"URL literal {literal} has a trailing-slash path component. "
                f"Drop it (e.g. {suggestion!r}). Project convention: every "
                "frontend API call must match the backend's no-trailing-slash "
                "route — see .agent/rules/neko-guide.md (§'API URL 末尾不带斜杠') "
                "and docs/contributing/code-style.md. If this is a prefix "
                "builder that gets a segment appended, write it as a template "
                "literal (e.g. `/api/foo/${id}`) or string-concat (e.g. "
                "'/api/foo/' + id) so the lint can recognise it.",
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid trailing-slash /api/... URL literals in frontend code."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan (default: static/ + frontend/ + templates/).",
    )
    args = parser.parse_args(argv)

    raw_paths = args.paths or DEFAULT_PATHS
    targets = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw_paths]

    total = 0
    read_failures = 0
    for file in _iter_source_files(targets):
        try:
            results = check_file(file)
        except _ReadFailed:
            read_failures += 1
            continue
        for lineno, col, msg in results:
            rel = file.relative_to(REPO_ROOT) if file.is_relative_to(REPO_ROOT) else file
            print(f"{rel}:{lineno}:{col}  {CODE}  {msg}")
            total += 1

    if total:
        print(
            f"\n{total} trailing-slash /api/... URL literal(s) found.\n"
            "Project convention: every frontend API call must match the "
            "backend's no-trailing-slash route. Triggering Starlette's 307 "
            "slash-redirect breaks under reverse proxies that don't preserve "
            "Host (root cause of the PR #938 chara_manager regression). "
            "See .agent/rules/neko-guide.md (§'API URL 末尾不带斜杠') and "
            "docs/contributing/code-style.md.",
            file=sys.stderr,
        )
    if read_failures:
        print(
            f"\n{read_failures} file(s) could not be read and were skipped — "
            "fix the encoding/permission errors above before re-running. The "
            "lint exits non-zero in this case to avoid silently passing files "
            "it didn't actually scan.",
            file=sys.stderr,
        )
    if total or read_failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
