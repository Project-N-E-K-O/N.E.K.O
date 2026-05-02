#!/usr/bin/env python3
"""Static check: forbid trailing-slash route paths on FastAPI router/app decorators.

Why this exists
---------------
Project convention: every backend HTTP/WebSocket endpoint is declared WITHOUT
a trailing slash. The convention matches Stripe / GitHub / Google / AWS /
Microsoft REST API Guidelines, but more importantly it sidesteps a class of
production failure under reverse proxies:

* FastAPI/Starlette's ``redirect_slashes=True`` (default) will 307-redirect
  ``/foo/`` to ``/foo``.
* The ``Location`` header is an **absolute** URL built from the request
  ``Host``. Behind a reverse proxy that doesn't preserve ``Host`` (or with
  ``proxy_headers`` off, i.e. ``NEKO_BEHIND_PROXY != 1``), that absolute URL
  points at the internal ``127.0.0.1:<port>`` and the browser dies with
  ``ERR_CONNECTION_REFUSED``.
* PR #938 (chara_manager regression on LAN reverse proxies) was exactly this
  bug. See ``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") and
  ``main_routers/characters_router.py`` docstring for the full write-up.

Avoiding the redirect entirely (= never declaring a trailing slash) makes
the whole class of failure impossible.

What it flags
-------------
Any decorator call of the form:

    @router.get("/foo/")           # trailing-slash literal
    @router.post("/foo/bar/")
    @router.websocket("/ws/foo/")
    @app.put("/api/v1/foo/")

where ``router`` is any name and the HTTP method comes from
``METHOD_NAMES``. Path is the first positional argument or ``path=...``.

Allowed exceptions
------------------
* ``@router.get("/")`` on a router whose declared ``APIRouter(prefix=...)``
  is empty — the literal root page. We have exactly one of these:
  ``main_routers/pages_router.py`` serving ``index.html``. Routers with a
  non-empty prefix are NOT exempt because ``prefix="/api/foo"`` +
  ``@router.get("/")`` registers ``/api/foo/`` which IS a trailing-slash
  route (and triggers the same 307 hazard). Reported by CodeRabbit on
  PR #1082.
* **Explicit alias pair** — when the same function carries BOTH
  ``@router.get("/foo")`` and ``@router.get("/foo/")``, the trailing-slash
  one is a deliberate alias. The 307-redirect attack vector is gone (the
  slash form returns 200 directly), so the convention's safety property is
  preserved. SPA entry points (``/ui`` + ``/ui/``) use this pattern. The
  alias check is **per HTTP method**: a trailing-slash GET needs a sibling
  no-slash GET (a sibling POST does NOT cover it, because GET callers
  hitting the no-slash form would still trigger the 307). For
  ``api_route(..., methods=[...])``, every method in the list must have a
  matching sibling.

Scope
-----
Default scan: ``main_routers/`` + ``*_server.py`` at the repo root +
``plugin/server/routes/``. Pass paths explicitly to scan elsewhere.

Suppression
-----------
None. If you genuinely need a trailing-slash route, delete this script in
the same PR and justify it in the description.

Output
------
Every violation prints as ``path:line:col  API_TRAILING_SLASH  message``.
Exit status is 1 when any violation is found, 0 otherwise.

Usage::

    uv run python scripts/check_api_trailing_slash.py [paths...]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PATHS: list[str] = [
    "main_routers",
    "main_server.py",
    "memory_server.py",
    "agent_server.py",
    "plugin/server/routes",
]

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
    "plugin/plugins",
}

EXCLUDE_FILES: set[str] = {
    "scripts/check_api_trailing_slash.py",
}

# Decorator method names that register a route. Covers FastAPI HTTP verbs
# plus WebSocket. ``api_route`` (used to register multiple methods at once)
# is included for completeness.
METHOD_NAMES = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "head",
    "options",
    "trace",
    "websocket",
    "api_route",
}

CODE = "API_TRAILING_SLASH"


def _is_route_decorator(node: ast.expr) -> str | None:
    """If ``node`` is ``@<something>.<METHOD>(...)`` return METHOD; else None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in METHOD_NAMES:
        return None
    return func.attr


def _extract_path_arg(call: ast.Call) -> tuple[str, int, int] | None:
    """Pull the route path string + its lineno/col from the decorator call.

    The path is the first positional arg, or the ``path=`` kwarg. We only
    flag string literals — dynamic paths can't be checked statically and
    are vanishingly rare for routes anyway.
    """
    # path=... kwarg first — explicit beats implicit
    for kw in call.keywords:
        if kw.arg == "path" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value, kw.value.lineno, kw.value.col_offset + 1
    # Otherwise, first positional
    if call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value, first.lineno, first.col_offset + 1
    return None


# Methods that ``api_route(..., methods=[...])`` defaults to when the kwarg
# is omitted. FastAPI matches Starlette here: GET only.
_API_ROUTE_DEFAULT_METHODS: frozenset[str] = frozenset({"GET"})


def _extract_methods(call: ast.Call, decorator_method: str) -> frozenset[str]:
    """Return the uppercase HTTP method set the decorator registers.

    For ``@router.get/post/...`` the method is implicit in the attribute
    name. For ``@router.api_route(..., methods=[...])`` it comes from the
    ``methods`` kwarg (default GET). For ``@router.websocket(...)`` we use
    the synthetic name ``WEBSOCKET`` so it never collides with HTTP verbs.
    """
    if decorator_method == "websocket":
        return frozenset({"WEBSOCKET"})
    if decorator_method != "api_route":
        return frozenset({decorator_method.upper()})
    # api_route — read methods=[...] kwarg, default GET (matches FastAPI)
    for kw in call.keywords:
        if kw.arg != "methods":
            continue
        if not isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
            # Dynamic methods list — give up safely (treat as no method, so
            # alias-pair check fails and the trailing slash gets flagged).
            return frozenset()
        out: set[str] = set()
        for elt in kw.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.add(elt.value.upper())
            else:
                # Mixed static/dynamic list — e.g. ``methods=["GET", SOME_CONST]``.
                # Returning the partial set would let a sibling that only covers
                # the static methods (e.g. an explicit GET sibling) falsely
                # exempt the trailing-slash decorator while the dynamic method
                # remains uncovered. Fail closed. Reported by CodeRabbit on
                # PR #1082.
                return frozenset()
        return frozenset(out)
    return _API_ROUTE_DEFAULT_METHODS


def _collect_router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Map each module-level router/app name to its declared ``prefix=``.

    Looks for assignments like::

        router = APIRouter(prefix="/api/foo")
        router = APIRouter()              # → ""
        app    = FastAPI()                # → "" (FastAPI has no prefix)

    Used by the ``"/"`` exemption: a literal ``@router.get("/")`` is the
    legitimate root only when the router has no prefix; otherwise the
    effective registered path is ``<prefix>/`` which IS a trailing-slash
    route. Reported by CodeRabbit on PR #1082.

    Anything we can't resolve statically (re-imported router, dynamic
    construction, etc.) is omitted from the map. The downstream check
    treats "unknown router" conservatively — the ``"/"`` exemption fires
    only on a known-empty prefix.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        # We only care about single-target Name = Call assignments.
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        ctor = None
        if isinstance(func, ast.Name):
            ctor = func.id
        elif isinstance(func, ast.Attribute):
            ctor = func.attr
        if ctor not in ("APIRouter", "FastAPI"):
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value
                break
        out[target.id] = prefix
    return out


def _decorator_owner(call: ast.Call) -> str | None:
    """Return the variable name on the LHS of ``@<name>.<method>(...)``."""
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        return call.func.value.id
    return None


class TrailingSlashChecker(ast.NodeVisitor):
    def __init__(self, path: Path, router_prefixes: dict[str, str]) -> None:
        self.path = path
        self.router_prefixes = router_prefixes
        self.violations: list[tuple[int, int, str]] = []

    def _visit_decorated(self, node: ast.AST) -> None:
        decos = getattr(node, "decorator_list", []) or []

        # First pass: collect every (METHOD, path) pair this function
        # registers. The alias-pair exemption must match BOTH method and
        # path — see _is_aliased_safely() below for why a same-path /
        # different-method "sibling" is not safe.
        sibling: set[tuple[str, str]] = set()
        for deco in decos:
            method_name = _is_route_decorator(deco)
            if method_name is None:
                continue
            extracted = _extract_path_arg(deco)
            if extracted is None:
                continue
            for m in _extract_methods(deco, method_name):
                sibling.add((m, extracted[0]))

        for deco in decos:
            decorator_method = _is_route_decorator(deco)
            if decorator_method is None:
                continue
            extracted = _extract_path_arg(deco)
            if extracted is None:
                continue
            route_path, lineno, col = extracted
            if not route_path.endswith("/"):
                continue
            # Allow the literal root page — but ONLY when the owning router
            # has an empty prefix. ``APIRouter(prefix="/api/foo")`` +
            # ``@router.get("/")`` registers the effective path
            # ``/api/foo/`` which IS a trailing-slash route, exactly the
            # bug this lint exists to catch. Reported by CodeRabbit on
            # PR #1082. If we can't resolve the owner statically (cross-
            # module router etc.) be conservative and don't exempt.
            if route_path == "/":
                owner = _decorator_owner(deco)
                if owner is not None and self.router_prefixes.get(owner, None) == "":
                    continue
            # Allow ONLY if every method this trailing-slash decorator
            # registers ALSO has a sibling no-slash decorator for the same
            # method. Otherwise a caller hitting the no-slash form with an
            # uncovered method still triggers Starlette's 307 redirect (the
            # exact bug this lint exists to prevent). Reported by Codex on
            # PR #1082 — without the per-method check, mixed pairs like
            # ``@router.get('/foo/')`` + ``@router.post('/foo')`` would slip
            # through even though ``GET /foo`` still 307s.
            no_slash = route_path.rstrip("/")
            methods = _extract_methods(deco, decorator_method)
            if methods and all((m, no_slash) in sibling for m in methods):
                continue
            self.violations.append(
                (
                    lineno,
                    col,
                    f"route path {route_path!r} ends with '/'. Drop the trailing "
                    f"slash (e.g. {route_path.rstrip('/') or ''!r}). Project "
                    "convention forbids trailing-slash routes — see "
                    ".agent/rules/neko-guide.md (§'API URL 末尾不带斜杠') "
                    "and main_routers/characters_router.py docstring. If you "
                    "genuinely need both forms, register an explicit alias by "
                    "stacking @router.<METHOD>('/foo') above "
                    "@router.<METHOD>('/foo/') on the same function — and the "
                    "method must match (a sibling POST won't cover a GET that "
                    "still 307s).",
                )
            )
        self.generic_visit(node)

    # FastAPI route decorators land on functions / async functions / class
    # methods. We don't care which, but ast splits them.
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_decorated(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_decorated(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_decorated(node)


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
        if not p.exists():
            # Don't blow up on optional default paths that are absent — the
            # default list spans both main_server and plugin/, but a checkout
            # of just one slice should still lint cleanly.
            continue
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
    checker = TrailingSlashChecker(path, _collect_router_prefixes(tree))
    checker.visit(tree)
    return checker.violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid trailing-slash route paths in FastAPI decorators."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "Files/directories to scan (default: main_routers/ + *_server.py "
            "at repo root + plugin/server/routes/)."
        ),
    )
    args = parser.parse_args(argv)

    raw_paths = args.paths or DEFAULT_PATHS
    targets = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw_paths]

    total = 0
    parse_failures = 0
    for file in _iter_python_files(targets):
        tree = _parse_file(file)
        if tree is None:
            # Read failure / SyntaxError. Don't silently skip — that's
            # fail-open (the file might have contained the very violation
            # we exist to catch). Reported by CodeRabbit on PR #1082.
            parse_failures += 1
            continue
        for lineno, col, msg in check_file(file, tree):
            rel = file.relative_to(REPO_ROOT) if file.is_relative_to(REPO_ROOT) else file
            print(f"{rel}:{lineno}:{col}  {CODE}  {msg}")
            total += 1

    if total:
        print(
            f"\n{total} trailing-slash route path(s) found.\n"
            "Project convention: every backend HTTP/WebSocket endpoint is "
            "declared WITHOUT a trailing slash. This avoids Starlette's "
            "absolute-URL 307 redirect under reverse proxies (root cause of "
            "the PR #938 chara_manager regression). See .agent/rules/"
            "neko-guide.md (§'API URL 末尾不带斜杠') and main_routers/"
            "characters_router.py docstring for the full write-up.",
            file=sys.stderr,
        )
    if parse_failures:
        print(
            f"\n{parse_failures} file(s) could not be parsed and were skipped — "
            "fix the syntax/encoding errors above before re-running. The lint "
            "exits non-zero in this case to avoid silently passing files it "
            "didn't actually scan.",
            file=sys.stderr,
        )
    if total or parse_failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
