#!/usr/bin/env python3
"""Static check: enforce prompt-internationalisation conventions.

Two rules in one walker:

INLINE_PROMPT_NON_EN
   Any string passed to an LLM call site (``ainvoke`` / ``create`` /
   ``update_session`` / ``connect(instructions=)`` / etc.) and any
   module-level constant whose name (case-insensitive) contains
   ``prompt`` / ``instruction`` / ``system_`` MUST be English-bodied.
   The trigger is CJK character ratio (CJK Unified + Kana + Hangul) above
   30% over non-whitespace characters. The threshold leaves room for short
   embedded examples (real Chinese user phrases inside an otherwise-English
   prompt — see PR #974 for the reference fix).

I18N_NOT_IN_CONFIG
   Any dict literal whose keys form a multi-language map — at least two of
   ``{'zh', 'en', 'ja', 'ko', 'ru', 'zh-CN', 'zh-TW', 'es', 'pt'}`` AND
   including ``'en'`` — belongs in ``config/prompts_*.py``. Such dicts
   anywhere else are flagged.

The project's i18n convention:
   - Inline prompts at the call site MUST be English-only.
   - Multi-language prompts MUST live in ``config/prompts_*.py``, served
     via ``_loc(MULTILANG_DICT, language_code)`` or
     ``get_xxx_prompt(lang)``.

Suppression
-----------
Append ``# noqa: INLINE_PROMPT_NON_EN`` or ``# noqa: I18N_NOT_IN_CONFIG``
to any line spanned by the offending node (start line through end line —
useful for triple-quoted strings where the start line ends with ``\"\"\"``).
Bare ``# noqa`` matches any code in this script. Use sparingly — these
rules exist for good reason.

Output
------
Every violation prints as ``path:line:col  CODE  message``. Exit 1 on any
violation, 0 otherwise.

Usage:
    python scripts/check_prompt_hygiene.py [paths...]
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

# Directories never scanned. config/, plugin/, templates/, static/ are by
# project convention out of scope: config holds the legit multi-lang
# prompts; plugin payloads are third-party; templates/static don't have
# Python LLM call sites. tests/ is excluded because test fixtures
# intentionally use multi-lang strings to exercise the i18n pipeline.
# frontend/ is TS, not Python.
EXCLUDE_DIRS = {
    ".venv", "venv",
    ".git", "__pycache__", ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "dist", "build", "node_modules",
    "frontend", "static", "templates",
    "config", "plugin", "tests",
    "local_server",  # subprocess-spawned TTS / telemetry servers — no LLM call sites
}

# This script defines the language-code set as part of its rules, which
# makes it look like an i18n dict to itself. Skip it.
EXCLUDE_FILES = {
    "scripts/check_prompt_hygiene.py",
}

# CJK ratio threshold. Strings above this are considered "non-English-bodied".
# 30% gives room for inlined real-language examples inside an otherwise-
# English prompt. Tweaking down is safe; tweaking up is risky.
CJK_THRESHOLD = 0.30

# Language codes recognised as i18n keys. Union of codes used by
# config/prompts_*.py (zh / zh-CN / zh-TW / en / ja / ko / ru) plus es/pt
# (used by static/locales).
LANG_CODES = {"zh", "zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
REQUIRED_LANG_KEY = "en"
MIN_LANG_KEYS = 2

# Variable / constant name regex. Case-insensitive substring match.
# False-positives at the name level are filtered by CJK_THRESHOLD on value.
PROMPT_NAME_RE = re.compile(r"prompt|instruction|system_", re.IGNORECASE)

# Method names whose call argument trees we inspect for inline prompts.
# Match the last component of an Attribute (``foo.ainvoke``) or a Name
# (``ainvoke``). Includes LangChain (.ainvoke / .invoke / .astream / .stream),
# OpenAI (.chat.completions.create), Anthropic (.messages.create),
# OmniRealtimeClient (.update_session / .connect / .prime_context /
# .prompt_ephemeral), and Gemini (.generate_content / .generate).
LLM_METHOD_NAMES = {
    "ainvoke", "invoke", "astream", "stream",
    "create",
    "update_session",
    "prime_context",
    "prompt_ephemeral",
    "connect",
    "generate", "generate_content",
}

# Keys whose values count as prompt content inside dict-literal messages
# like ``{"role": "system", "content": "..."}``.
PROMPT_FIELD_KEYS = {
    "content", "instructions", "system",
    "system_prompt", "system_instruction",
}

# Same set, used for kwargs (``ainvoke(..., system="...")``).
PROMPT_KWARGS = PROMPT_FIELD_KEYS

# LangChain message wrapper class names. ``SystemMessage(content=...)``
# and friends are unwrapped to their content arg.
LC_MESSAGE_NAMES = {"SystemMessage", "HumanMessage", "AIMessage", "ChatMessage"}

# CJK ranges: CJK Unified, Hiragana, Katakana, Hangul Syllables.
CJK_RANGES = (
    ("一", "鿿"),
    ("぀", "ゟ"),
    ("゠", "ヿ"),
    ("가", "힣"),
)

CODE_INLINE = "INLINE_PROMPT_NON_EN"
CODE_I18N = "I18N_NOT_IN_CONFIG"


def _has_non_ascii(s: str) -> bool:
    """True if `s` contains any character outside ASCII (codepoint > 127).
    Used to distinguish genuine i18n content (translated prose with CJK,
    Cyrillic, accented Latin) from code-mapping dicts whose values are
    pure-ASCII identifiers like 'cmn-CN' or 'Chinese'."""
    return any(ord(ch) > 127 for ch in s)


def _cjk_ratio(s: str) -> float:
    """Fraction of non-whitespace chars that are CJK (Unified, Kana, Hangul)."""
    cjk = 0
    total = 0
    for ch in s:
        if ch.isspace():
            continue
        total += 1
        for lo, hi in CJK_RANGES:
            if lo <= ch <= hi:
                cjk += 1
                break
    return cjk / total if total else 0.0


def _has_noqa(line: str, code: str) -> bool:
    """True if `line` contains `# noqa` (bare) or `# noqa: ...,CODE,...`.

    Tolerates a trailing explanatory comment after the noqa, e.g.
    ``# noqa: CODE  # rationale``. The codes block stops at the next
    ``#`` or end-of-line — matching ruff/flake8 behaviour."""
    m = re.search(r"#\s*noqa\b(?:\s*:\s*([A-Za-z0-9_,\s]+?))?(?=#|$)", line)
    if not m:
        return False
    raw = m.group(1)
    if raw is None or not raw.strip():
        return True
    codes = {c.strip() for c in raw.split(",") if c.strip()}
    return code in codes


def _string_value(node: ast.AST | None) -> str | None:
    """Return the literal string content of a Constant or JoinedStr (f-string).
    None if the node is not string-shaped. For f-strings, only LITERAL
    constant segments contribute — interpolated expressions are skipped."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
        return "".join(parts)
    return None


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_method_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


class PromptHygieneChecker(ast.NodeVisitor):
    def __init__(self, path: Path, source_lines: list[str]) -> None:
        self.path = path
        self.source_lines = source_lines
        self.violations: list[tuple[int, int, str, str]] = []

    # Module-scope first: rule (b) module-level prompt-named constants.
    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            self._check_module_assign(stmt)
        self.generic_visit(node)

    def _check_module_assign(self, stmt: ast.AST) -> None:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                self._maybe_check_named(target, stmt.value)
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value is not None:
                self._maybe_check_named(stmt.target, stmt.value)

    def _maybe_check_named(self, target: ast.AST, value: ast.AST) -> None:
        if not isinstance(target, ast.Name):
            return
        if not PROMPT_NAME_RE.search(target.id):
            return
        s = _string_value(value)
        if s is None:
            return
        self._flag_inline(value, s, source=f"const {target.id}")

    # Rule (a): LLM call argument analysis.
    def visit_Call(self, node: ast.Call) -> None:
        method = _call_method_name(node)
        if method in LLM_METHOD_NAMES:
            for arg in node.args:
                self._scan_prompt_args(arg)
            for kw in node.keywords:
                if kw.arg in PROMPT_KWARGS:
                    s = _string_value(kw.value)
                    if s is not None:
                        self._flag_inline(kw.value, s, source=f"kwarg {kw.arg}=")
                elif kw.arg == "messages":
                    self._scan_prompt_args(kw.value)
        self.generic_visit(node)

    def _scan_prompt_args(self, node: ast.AST) -> None:
        # List/tuple of messages → recurse.
        if isinstance(node, (ast.List, ast.Tuple)):
            for el in node.elts:
                self._scan_prompt_args(el)
            return
        # Dict literal {"role": ..., "content": "..."}.
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                kname = _const_str(k)
                if kname in PROMPT_FIELD_KEYS:
                    s = _string_value(v)
                    if s is not None:
                        self._flag_inline(v, s, source=f'dict["{kname}"]')
            return
        # SystemMessage(content="...") / HumanMessage("...").
        if isinstance(node, ast.Call):
            method = _call_method_name(node)
            if method in LC_MESSAGE_NAMES:
                if node.args:
                    s = _string_value(node.args[0])
                    if s is not None:
                        self._flag_inline(node.args[0], s, source=f"{method}()")
                for kw in node.keywords:
                    if kw.arg == "content":
                        s = _string_value(kw.value)
                        if s is not None:
                            self._flag_inline(
                                kw.value, s, source=f"{method}(content=)"
                            )
            return
        # Bare string at top level (e.g. ainvoke("..."), prime_context(text)).
        if isinstance(node, (ast.Constant, ast.JoinedStr)):
            s = _string_value(node)
            if s is not None:
                self._flag_inline(node, s, source="positional arg")

    # Rule i18n: dict literal that looks like a language map.
    def visit_Dict(self, node: ast.Dict) -> None:
        keys: list[str | None] = [_const_str(k) for k in node.keys]
        if any(k is None for k in keys):
            self.generic_visit(node)
            return
        keys_set: set[str] = {k for k in keys if k is not None}
        lang_keys = keys_set & LANG_CODES
        if len(lang_keys) >= MIN_LANG_KEYS and REQUIRED_LANG_KEY in lang_keys:
            values = [_string_value(v) for v in node.values]
            if all(v is not None for v in values):
                # Filter out code-mapping dicts (lang -> short ASCII code/name
                # like 'zh' -> 'cmn-CN' or 'zh' -> 'Chinese'). Genuine i18n
                # content has at least one non-ASCII value somewhere — Chinese
                # / Japanese / Korean / Cyrillic prose translations.
                if any(_has_non_ascii(v) for v in values if v is not None):
                    self._flag_i18n(node, sorted(lang_keys))
        self.generic_visit(node)

    # ---- helpers ----

    def _flag_inline(self, node: ast.AST, s: str, source: str) -> None:
        ratio = _cjk_ratio(s)
        if ratio < CJK_THRESHOLD:
            return
        if self._is_noqa(node, CODE_INLINE):
            return
        lineno = getattr(node, "lineno", 0) or 0
        col = (getattr(node, "col_offset", 0) or 0) + 1
        msg = (
            f"inline prompt at {source} has {ratio:.0%} CJK characters "
            f"(threshold {int(CJK_THRESHOLD * 100)}%); rewrite the body in English "
            f"or move the multi-language version into config/prompts_*.py."
        )
        self.violations.append((lineno, col, CODE_INLINE, msg))

    def _flag_i18n(self, node: ast.Dict, lang_keys: list[str]) -> None:
        if self._is_noqa(node, CODE_I18N):
            return
        lineno = getattr(node, "lineno", 0) or 0
        col = (getattr(node, "col_offset", 0) or 0) + 1
        msg = (
            f"multi-language dict (keys: {', '.join(lang_keys)}) belongs in "
            f"config/prompts_*.py, not in regular code. Convention: import the "
            f"dict from config.prompts_xxx and resolve via _loc(DICT, lang)."
        )
        self.violations.append((lineno, col, CODE_I18N, msg))

    def _is_noqa(self, node: ast.AST, code: str) -> bool:
        start = getattr(node, "lineno", 0) or 0
        end = getattr(node, "end_lineno", start) or start
        if start <= 0:
            return False
        last = min(end, len(self.source_lines))
        for ln in range(start, last + 1):
            if _has_noqa(self.source_lines[ln - 1], code):
                return True
        return False


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
            "Enforce prompt-i18n conventions: inline prompts must be English; "
            "multi-language dicts must live in config/prompts_*.py."
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
        checker = PromptHygieneChecker(file, lines)
        checker.visit(tree)
        for lineno, col, code, msg in checker.violations:
            try:
                rel = file.relative_to(REPO_ROOT)
            except ValueError:
                rel = file
            print(f"{rel}:{lineno}:{col}  {code}  {msg}")
            total += 1

    if total:
        print(
            f"\n{total} prompt-hygiene violation(s) found.\n"
            "Inline LLM prompts MUST be English-only. Multi-language prompts MUST "
            "live in config/prompts_*.py. To override a single line, append "
            "`# noqa: INLINE_PROMPT_NON_EN` or `# noqa: I18N_NOT_IN_CONFIG`.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
