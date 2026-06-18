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

"""Static check: enforce LLM budget / timeout discipline.

Project policy (see docs/design/llm-prompt-budget.md and
.agent/rules/neko-guide.md §"LLM budget / timeout"):

  * Every LLM **output** must be bounded by a token budget AND a timeout.
  * Every LLM **input** (the strings concatenated into ``messages``) must be
    bounded by a token budget before it reaches the wire.

Two rules in one walker:

LLM_OUTPUT_BUDGET  (construction-site, hard rule)
   Every ``create_chat_llm(...)`` / ``ChatOpenAI(...)`` (and aliases ending in
   ``ChatOpenAI``, e.g. ``_ChatOpenAI``) construction MUST carry both:
     - a token budget kwarg: ``max_completion_tokens=`` or ``max_tokens=``
     - a ``timeout=`` (or ``request_timeout=``) kwarg
   Rationale: without a token budget the model's reply can run away (cost +
   latency + context blow-up); without a timeout a hung upstream wedges the
   whole async pipeline — neither has a safe default in ``utils/llm_client.py``
   (``timeout`` defaults to the SDK's long default, the token field is simply
   omitted from the request body).

   Why the construction site? ``ChatOpenAI._params(**overrides)`` lets a single
   client set budget/timeout per call instead. Those sites legitimately leave
   the constructor bare — mark them with ``# noqa: LLM_OUTPUT_BUDGET`` and a
   one-line justification ("budget+timeout set per-call via invoke_raw"), the
   same escape-hatch convention as the other static checks.

LLM_INPUT_BUDGET  (call-site, heuristic)
   Every LLM call (``.ainvoke`` / ``.invoke`` / ``.astream`` / ``.ainvoke_raw``
   / ``.invoke_raw``) whose prompt argument carries **dynamic** content
   (variables, f-strings, concatenation — i.e. anything that isn't a pure
   string/constant literal) must show evidence of input budgeting somewhere in
   the enclosing function: a call to one of the ``utils.tokenize`` truncation
   helpers (``truncate_to_tokens`` / ``truncate_head_tail_tokens`` / ...), or a
   reference to a ``*_MAX_TOKENS`` budget constant.

   This is a deliberately coarse heuristic — it cannot prove a given string was
   truncated, only that the function is "budget-aware". False positives are
   suppressed with ``# noqa: LLM_INPUT_BUDGET`` plus a justification (e.g. the
   intentionally-uncapped user-text sites enumerated in llm-prompt-budget.md
   section 6, or constant-only health-check pings).

Scope
-----
Whole repo minus ``EXCLUDE_DIRS`` (plugin payloads, tests, frontend, config,
templates, static, vendored/build dirs). ``utils/llm_client.py`` itself is
exempt — it is the canonical client wrapper that declares the parameters.

Suppression
-----------
Append ``# noqa: LLM_OUTPUT_BUDGET`` or ``# noqa: LLM_INPUT_BUDGET`` to any
line spanned by the offending node (start line through end line). A bare
``# noqa`` matches any code. Use sparingly — these rules exist for good reason.

Output
------
Every violation prints as ``path:line:col  CODE  message``. Exit 1 on any
violation, 0 otherwise.

Usage:
    python scripts/check_llm_budget.py [paths...]
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PATHS: list[str] = ["."]

# Directories never scanned. Mirrors check_prompt_hygiene.py: config holds the
# legit prompt constants; plugin payloads are third-party; tests/ fixtures use
# bare clients on mocks; frontend is TS; templates/static have no Python LLM
# call sites; local_server is a subprocess-spawned helper without LLM calls.
EXCLUDE_DIRS = {
    ".venv", "venv",
    ".git", "__pycache__", ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "dist", "build", "node_modules",
    "frontend", "static", "templates",
    "config", "plugin", "tests",
    "local_server",
}

# The canonical client wrapper declares ``max_completion_tokens`` / ``timeout``
# / etc. as parameters and forwards them; it is the implementation of the
# contract, not a call site. This script trivially mentions the kwarg names too.
EXCLUDE_FILES = {
    "scripts/check_llm_budget.py",
    "utils/llm_client.py",
}

# ── LLM_OUTPUT_BUDGET (construction site) ─────────────────────────────────

# A construction is matched when the callee is one of the chat factories
# or any class name ending in ``ChatOpenAI`` (``ChatOpenAI`` / ``_ChatOpenAI``
# / ``BUChatOpenAI``). Matched on a bare Name (``ChatOpenAI(...)``) or the last
# Attribute component (``mod.ChatOpenAI(...)``).
FACTORY_NAMES = {"create_chat_llm", "create_chat_llm_async"}
CHAT_CLASS_SUFFIX = "ChatOpenAI"

# Token-budget kwargs (either satisfies the budget requirement) — the client
# routes whichever is present to the right wire field by base_url.
BUDGET_KWARGS = {"max_completion_tokens", "max_tokens"}
# Timeout kwargs.
TIMEOUT_KWARGS = {"timeout", "request_timeout"}

CODE_OUTPUT = "LLM_OUTPUT_BUDGET"

# ── LLM_INPUT_BUDGET (call site, heuristic) ───────────────────────────────

LLM_CALL_ATTRS = {"ainvoke", "invoke", "astream", "ainvoke_raw", "invoke_raw"}

# Names whose presence anywhere in the enclosing function marks it as
# "budget-aware". The tokenize helpers do real truncation; a ``*_MAX_TOKENS``
# constant reference signals a deliberate token cap is in play.
# Only helpers that ENFORCE a token cap count as evidence. ``count_tokens`` /
# ``acount_tokens`` merely measure, and ``truncate_to_last_sentence_end`` trims
# to a sentence boundary with no max-token argument — none of them actually
# bound the input, so they must not satisfy the rule.
BUDGET_HELPER_NAMES = {
    "truncate_to_tokens",
    "atruncate_to_tokens",
    "truncate_head_tail_tokens",
}
# Token budget constants only — ``_MAX_ITEMS`` (item counts), ``_MAX_CHARS``
# (character caps), and a bare ``_BUDGET`` / ``_TOKEN_BUDGET`` are deliberately
# excluded so a function with a non-token cap is not mistaken for
# input-token-budget-aware.
BUDGET_CONST_RE = re.compile(r"_MAX_TOKENS$")

CODE_INPUT = "LLM_INPUT_BUDGET"


# ── shared helpers ────────────────────────────────────────────────────────


def _has_noqa(line: str, code: str) -> bool:
    """True if `line` has ``# noqa`` (bare) or ``# noqa: ...,CODE,...``."""
    m = re.search(r"#\s*noqa\b(?:\s*:\s*([A-Za-z0-9_,\s]+?))?(?=#|$)", line)
    if not m:
        return False
    raw = m.group(1)
    if raw is None or not raw.strip():
        return True
    codes = {c.strip() for c in raw.split(",") if c.strip()}
    return code in codes


def _node_has_noqa(node: ast.AST, source_lines: list[str], code: str) -> bool:
    start = getattr(node, "lineno", 0) or 0
    end = getattr(node, "end_lineno", start) or start
    if start <= 0:
        return False
    last = min(end, len(source_lines))
    for ln in range(start, last + 1):
        if _has_noqa(source_lines[ln - 1], code):
            return True
    return False


def _callee_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _has_kwarg(call: ast.Call, names: set[str]) -> bool:
    """True if the call passes one of ``names`` with a non-``None`` value.

    A literal ``None`` (``timeout=None`` / ``max_completion_tokens=None``) is
    treated as ABSENT: ``ChatOpenAI._params`` omits a falsy token limit and the
    SDK timeout stays unset, so forwarding an optional config default as ``None``
    must not satisfy the hard rule. Non-literal values (variables / expressions)
    still count — we can't prove those are ``None`` statically."""
    for kw in call.keywords:
        if kw.arg not in names:
            continue
        if isinstance(kw.value, ast.Constant) and kw.value.value is None:
            continue
        return True
    return False


def _is_constant_prompt(node: ast.AST | None) -> bool:
    """True if the prompt argument is *trivially constant* — a literal string,
    or a list/tuple of dict literals whose ``content`` values are all string
    constants. Such calls carry no unbounded dynamic input, so the input-budget
    heuristic skips them (health-check pings, fixed system-only prompts)."""
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_is_constant_message(el) for el in node.elts)
    return False


def _is_constant_message(node: ast.AST) -> bool:
    """A single message element with only constant content."""
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            kname = k.value if isinstance(k, ast.Constant) else None
            if kname == "content":
                return isinstance(v, ast.Constant) and isinstance(v.value, str)
        return False
    # SystemMessage("literal") / HumanMessage(content="literal")
    if isinstance(node, ast.Call):
        name = _callee_name(node)
        if name in {"SystemMessage", "HumanMessage", "AIMessage", "ChatMessage"}:
            if node.args:
                a0 = node.args[0]
                return isinstance(a0, ast.Constant) and isinstance(a0.value, str)
            for kw in node.keywords:
                if kw.arg == "content":
                    return isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
        return False
    return False


def _build_parent_func_map(tree: ast.Module) -> dict[int, ast.AST]:
    """Map every node's id() -> nearest enclosing Function/AsyncFunction."""
    m: dict[int, ast.AST] = {}

    def _walk(node: ast.AST, cur: ast.AST | None) -> None:
        is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        nxt = node if is_func else cur
        # Function nodes map to themselves; non-function nodes map to the
        # nearest enclosing function; top-level nodes stay out of the map.
        if is_func:
            m[id(node)] = node
        elif cur is not None:
            m[id(node)] = cur
        for child in ast.iter_child_nodes(node):
            _walk(child, nxt)

    _walk(tree, None)
    return m


def _function_is_budget_aware(func: ast.AST | None) -> bool:
    """True if the function's *own* body references a tokenize truncation helper
    or a ``*_MAX_TOKENS`` constant.

    Does NOT descend into nested ``def`` / ``async def`` / ``lambda`` bodies — a
    budget marker that lives only inside an inner helper says nothing about the
    enclosing function's own LLM call (avoids a false negative)."""
    if func is None:
        return False

    def _is_marker(name: str) -> bool:
        return name in BUDGET_HELPER_NAMES or bool(BUDGET_CONST_RE.search(name))

    stack: list[ast.AST] = [func]
    first = True
    while stack:
        node = stack.pop()
        # Skip the bodies of nested functions (but always scan ``func`` itself).
        if not first and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            continue
        first = False
        if isinstance(node, ast.Name) and _is_marker(node.id):
            return True
        if isinstance(node, ast.Attribute) and _is_marker(node.attr):
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


# ── checker ───────────────────────────────────────────────────────────────


class LLMBudgetChecker(ast.NodeVisitor):
    def __init__(self, path: Path, source_lines: list[str], tree: ast.Module) -> None:
        self.path = path
        self.source_lines = source_lines
        self.func_map = _build_parent_func_map(tree)
        self.violations: list[tuple[int, int, str, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee_name(node)
        if name is not None:
            if name in FACTORY_NAMES or name.endswith(CHAT_CLASS_SUFFIX):
                self._check_output_budget(node, name)
            if name in LLM_CALL_ATTRS:
                self._check_input_budget(node, name)
        self.generic_visit(node)

    def _check_output_budget(self, node: ast.Call, name: str) -> None:
        # NOTE: a ``**kwargs`` splat is NOT an exemption. We only see explicit
        # keyword args, so a construction that hides its budget/timeout inside a
        # splatted dict must carry a justified ``# noqa: LLM_OUTPUT_BUDGET`` —
        # otherwise the hard rule would be trivially bypassable (see PR review).
        missing: list[str] = []
        if not _has_kwarg(node, BUDGET_KWARGS):
            missing.append("token budget (max_completion_tokens= / max_tokens=)")
        if not _has_kwarg(node, TIMEOUT_KWARGS):
            missing.append("timeout=")
        if not missing:
            return
        if _node_has_noqa(node, self.source_lines, CODE_OUTPUT):
            return
        lineno = node.lineno
        col = (node.col_offset or 0) + 1
        msg = (
            f"{name}(...) is missing {' and '.join(missing)}. Every LLM client "
            f"construction must bound output with a token budget AND a timeout. "
            f"If they are set per-call via invoke/ainvoke/..(**overrides), add "
            f"`# noqa: {CODE_OUTPUT}` with a one-line justification."
        )
        self.violations.append((lineno, col, CODE_OUTPUT, msg))

    def _check_input_budget(self, node: ast.Call, name: str) -> None:
        # Resolve the prompt argument: first positional, or messages=/input=.
        prompt_arg: ast.AST | None = None
        if node.args:
            prompt_arg = node.args[0]
        else:
            for kw in node.keywords:
                if kw.arg in {"messages", "input"}:
                    prompt_arg = kw.value
                    break
        # Constant-only prompts carry no unbounded input — skip.
        if _is_constant_prompt(prompt_arg):
            return
        func = self.func_map.get(id(node))
        if _function_is_budget_aware(func):
            return
        if _node_has_noqa(node, self.source_lines, CODE_INPUT):
            return
        lineno = node.lineno
        col = (node.col_offset or 0) + 1
        msg = (
            f".{name}(...) sends dynamic input but the enclosing function shows "
            f"no token-budget discipline (no truncate_to_tokens / "
            f"truncate_head_tail_tokens / *_MAX_TOKENS constant). Truncate each "
            f"user/external/plugin string before it reaches messages (see "
            f"docs/design/llm-prompt-budget.md), or add `# noqa: {CODE_INPUT}` "
            f"with a justification if the input is intentionally uncapped."
        )
        self.violations.append((lineno, col, CODE_INPUT, msg))


# ── file iteration ──────────────────────────────────────────────────────────


def _is_excluded(path: Path) -> bool:
    # Match EXCLUDE_DIRS only against components *relative to the repo root* —
    # otherwise a repo living under e.g. /home/dist/... would match "dist" on
    # every path and silently pass CI.
    try:
        rel_path = path.relative_to(REPO_ROOT)
        rel = rel_path.as_posix()
        parts = set(rel_path.parts)
    except ValueError:
        rel = path.as_posix()
        parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
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


def _parse_file(path: Path) -> tuple[ast.Module | None, list[str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"{path}: skipped — {e}", file=sys.stderr)
        return None, []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"{path}:{e.lineno}: syntax error — {e.msg}", file=sys.stderr)
        return None, []
    return tree, source.splitlines()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Enforce LLM budget/timeout: every client construction must set a "
            "token budget AND a timeout; every dynamic LLM call must be "
            "input-budget-aware."
        )
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
        tree, lines = _parse_file(file)
        if tree is None:
            continue
        checker = LLMBudgetChecker(file, lines, tree)
        checker.visit(tree)
        for lineno, col, code, msg in sorted(checker.violations):
            try:
                rel = file.relative_to(REPO_ROOT)
            except ValueError:
                rel = file
            print(f"{rel}:{lineno}:{col}  {code}  {msg}")
            total += 1

    if total:
        print(
            f"\n{total} LLM budget violation(s) found.\n"
            "Policy: every LLM client construction MUST set a token budget "
            "(max_completion_tokens= / max_tokens=) AND a timeout=; every "
            "dynamic LLM call MUST truncate its input. Suppress a single site "
            "with `# noqa: LLM_OUTPUT_BUDGET` or `# noqa: LLM_INPUT_BUDGET` "
            "plus a justification. See docs/design/llm-prompt-budget.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
