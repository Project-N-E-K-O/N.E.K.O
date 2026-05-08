"""P24 lint-drift smoke — source-level assertion for the 5 chokepoint rules.

This smoke doesn't exercise any runtime behavior; it just scans source
files to confirm the invariants in ``.cursor/rules/*.mdc`` still hold.
Treat it as CI's backup for when rules aren't enforced at edit time.

Rules covered (P24 §13.8):

1. **i18n-fmt-naming**: no ``i18n(...)(...)`` curry usage
2. **no-hardcoded-chinese-in-ui**: business JS doesn't hardcode CJK
   (soft check — prints but doesn't fail, since legacy comments still have CJK)
3. **single-append-message**: no bare ``session.messages.append()``
   outside the chokepoint helper (pending until Day 2)
4. **atomic-io-only**: no bare ``os.replace()`` / ``gzip.open("w")``
   outside ``atomic_io.py`` (pending until Day 2)
5. **emit-grep-listener**: no dead ``emit('xxx')`` (every emit must have
   >= 1 matching ``on('xxx', ...)`` subscriber, with a whitelist for
   implicit emitters like ``state.js::set()``)

Implemented entirely in stdlib (no ripgrep dep) so CI-friendly on any
Python 3.11+ install.

Usage:

    .venv/Scripts/python.exe tests/testbench/smoke/p24_lint_drift_smoke.py

Exits 0 on all-clean, 1 on any hard violation.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

# Force utf-8 on stdout so unicode bullets don't crash on Windows GBK
if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STATIC_DIR = _PROJECT_ROOT / "tests" / "testbench" / "static"
_STATIC_UI_DIR = _STATIC_DIR / "ui"
_STATIC_CORE_DIR = _STATIC_DIR / "core"
_PIPELINE_DIR = _PROJECT_ROOT / "tests" / "testbench" / "pipeline"
_ROUTERS_DIR = _PROJECT_ROOT / "tests" / "testbench" / "routers"


class Violation:
    __slots__ = ("rule", "path", "line", "content")

    def __init__(self, rule: str, path: Path, line: int, content: str) -> None:
        self.rule = rule
        self.path = path
        self.line = line
        self.content = content.rstrip()

    def __str__(self) -> str:
        rel = self.path.relative_to(_PROJECT_ROOT)
        return f"[{self.rule}] {rel}:{self.line} {self.content[:100]}"


def _iter_files(
    root: Path,
    suffixes: tuple[str, ...],
    exclude_names: frozenset[str] = frozenset(),
) -> list[Path]:
    """Walk ``root`` and yield files matching suffix, skipping excluded names."""
    if not root.exists():
        return []
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in suffixes:
            continue
        if path.name in exclude_names:
            continue
        out.append(path)
    return out


def _grep_file(path: Path, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    """Return [(lineno, line_content), ...] for each match."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            hits.append((lineno, line))
    return hits


# ── Rule 1: i18n-fmt-naming ─────────────────────────────────────────────


_I18N_CURRY_RE = re.compile(r"i18n\([^)]+\)\(")


def check_i18n_curry() -> list[Violation]:
    """No ``i18n(...)(...)`` double-call. Must be zero hits."""
    violations: list[Violation] = []
    for path in _iter_files(_STATIC_DIR, (".js",)):
        for lineno, line in _grep_file(path, _I18N_CURRY_RE):
            violations.append(Violation("i18n-fmt-naming", path, lineno, line))
    return violations


# ── Rule 2: no-hardcoded-chinese-in-ui (soft) ───────────────────────────


# Match CJK inside a single/double/template quoted string literal.
# Comments (`//` or `/* */`) and plain code don't trigger.
# Note: This is a heuristic — won't catch template literals with CJK
# across multiple lines, but catches the most common "hardcoded UI
# text" pattern.
_CJK_STRING_RE = re.compile(
    r"""(['"`])[^'"`\n]*[\u4e00-\u9fff][^'"`\n]*\1"""
)
_CJK_EXCLUDE = frozenset({"i18n.js"})


_COMMENT_LINE_RE = re.compile(r"^\s*(//|\*|/\*)")


def check_hardcoded_cjk() -> list[Violation]:
    """Business JS with CJK *inside string literals*, excluding comments.

    Comments with CJK are allowed (project-wide style) and don't trigger.
    Only literal strings containing CJK in non-comment code lines are
    flagged. This is a heuristic — doesn't handle block comments spanning
    multiple lines perfectly, but catches most real violations.
    """
    violations: list[Violation] = []
    for root in (_STATIC_UI_DIR, _STATIC_CORE_DIR):
        for path in _iter_files(root, (".js",), exclude_names=_CJK_EXCLUDE):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hits_in_file: list[tuple[int, str]] = []
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _COMMENT_LINE_RE.match(line):
                    continue
                if _CJK_STRING_RE.search(line):
                    hits_in_file.append((lineno, line))
            if hits_in_file:
                lineno, line = hits_in_file[0]
                violations.append(Violation(
                    "no-hardcoded-chinese-in-ui", path, lineno,
                    f"(+{len(hits_in_file) - 1} more in file) {line.strip()[:120]}",
                ))
    return violations


# ── Rule 3: single-append-message (pending Day 2) ───────────────────────


_APPEND_RE = re.compile(r"session\.messages\.append\(")
_APPEND_EXCLUDE = frozenset({"messages_writer.py"})


def check_single_append_message() -> tuple[list[Violation], bool]:
    chokepoint = _PIPELINE_DIR / "messages_writer.py"
    is_active = chokepoint.exists()
    if not is_active:
        return [], False

    violations: list[Violation] = []
    for root in (_PIPELINE_DIR, _ROUTERS_DIR):
        for path in _iter_files(root, (".py",), exclude_names=_APPEND_EXCLUDE):
            for lineno, line in _grep_file(path, _APPEND_RE):
                violations.append(Violation("single-append-message", path, lineno, line))
    return violations, True


# ── Rule 4: atomic-io-only (pending Day 2) ──────────────────────────────


_OS_REPLACE_RE = re.compile(r"os\.replace\(")
_GZIP_WRITE_RE = re.compile(r"gzip\.open\([^)]*['\"]w")
_PY_COMMENT_RE = re.compile(r"^\s*#")
_ATOMIC_EXCLUDE = frozenset({
    "atomic_io.py",      # The chokepoint itself
    "boot_cleanup.py",   # Only references os.replace in docstring
    "autosave.py",       # Legitimate rolling-slot rename uses os.replace
                         # (no content write; atomic_write still handles new slot 0)
    "live_runtime_log.py",  # P24 hotfix #105: current.log → previous.log
                            # boot rotation is a file-rename, not a write.
                            # The actual writes go through a ``open(..., buffering=1)``
                            # text handle tee'd from sys.stdout/stderr, and the
                            # failure mode atomic_io protects against (partial
                            # content on crash) is orthogonal to this use case —
                            # we WANT partial content to be preserved as forensic
                            # evidence of the crash itself.
})


def check_atomic_io_only() -> tuple[list[Violation], bool]:
    chokepoint = _PIPELINE_DIR / "atomic_io.py"
    is_active = chokepoint.exists()
    if not is_active:
        return [], False

    violations: list[Violation] = []
    for root in (_PIPELINE_DIR, _ROUTERS_DIR):
        for path in _iter_files(root, (".py",), exclude_names=_ATOMIC_EXCLUDE):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _PY_COMMENT_RE.match(line):
                    continue  # skip comment lines (docstrings, # comments)
                if _OS_REPLACE_RE.search(line) or _GZIP_WRITE_RE.search(line):
                    violations.append(Violation("atomic-io-only", path, lineno, line))
    return violations, True


# ── Rule 5: emit-grep-listener ──────────────────────────────────────────


_EMIT_RE = re.compile(r"emit\(['\"]([a-z_:]+)['\"]")
_ON_RE = re.compile(r"\bon\(['\"]([a-z_:]+)['\"]")

# state.js's `set(key, value)` auto-emits `<key>:change`; these don't
# appear as literal `emit(...)` calls but listeners are legitimate.
_IMPLICIT_EMITTERS = frozenset({
    "session:change",
    "active_workspace:change",
    "errors:change",
    "ui_prefs:change",
})


def _collect_events(
    pattern: re.Pattern[str],
    roots: tuple[Path, ...],
) -> dict[str, list[Violation]]:
    result: dict[str, list[Violation]] = {}
    for root in roots:
        for path in _iter_files(root, (".js",)):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for m in pattern.finditer(line):
                    event = m.group(1)
                    result.setdefault(event, []).append(
                        Violation("emit-grep-listener", path, lineno, line),
                    )
    return result


def check_event_bus_drift() -> list[Violation]:
    emit_events = _collect_events(_EMIT_RE, (_STATIC_DIR,))
    on_events = _collect_events(_ON_RE, (_STATIC_DIR,))

    violations: list[Violation] = []
    # Dead emit: emit exists but no listener
    for event, entries in emit_events.items():
        if event in on_events:
            continue
        # Just report one entry per event to avoid noise
        v = entries[0]
        v.content = f"dead emit '{event}' (no on(...) anywhere)"
        violations.append(v)

    # Dead subscription: on exists but no emitter (check whitelist of implicit)
    for event, entries in on_events.items():
        if event in emit_events or event in _IMPLICIT_EMITTERS:
            continue
        v = entries[0]
        v.content = f"dead subscription '{event}' (no emit(...) anywhere, not implicit)"
        violations.append(v)

    return violations


# ── Orchestration ──────────────────────────────────────────────────────


def _report(title: str, violations: list[Violation], *, hard: bool) -> None:
    print("")
    print(f"* {title}")
    if not violations:
        print("  [ok] zero hits")
        return
    tag = "[ERR]" if hard else "[warn]"
    print(f"  {tag} {len(violations)} hit(s):")
    for v in violations[:15]:
        print(f"    {v}")
    if len(violations) > 15:
        print(f"    ... and {len(violations) - 15} more")


def main() -> int:
    print("=" * 66)
    print(" P24 Lint-Drift Smoke  (checks .cursor/rules/*.mdc invariants)")
    print("=" * 66)

    hard_violations: list[Violation] = []
    skipped: list[str] = []

    # Rule 1
    v1 = check_i18n_curry()
    _report("Rule 1 | i18n-fmt-naming (i18n(x)(y) curry)", v1, hard=True)
    hard_violations.extend(v1)

    # Rule 2 (soft)
    v2 = check_hardcoded_cjk()
    _report("Rule 2 | no-hardcoded-chinese-in-ui (soft, first-hit per file)",
            v2, hard=False)

    # Rule 3
    v3, r3_active = check_single_append_message()
    if r3_active:
        _report("Rule 3 | single-append-message (bare .messages.append)",
                v3, hard=True)
        hard_violations.extend(v3)
    else:
        skipped.append("Rule 3 (pending Day 2: pipeline/messages_writer.py)")

    # Rule 4
    v4, r4_active = check_atomic_io_only()
    if r4_active:
        _report("Rule 4 | atomic-io-only (bare os.replace / gzip write)",
                v4, hard=True)
        hard_violations.extend(v4)
    else:
        skipped.append("Rule 4 (pending Day 2: pipeline/atomic_io.py)")

    # Rule 5
    v5 = check_event_bus_drift()
    _report("Rule 5 | emit-grep-listener (event bus drift)", v5, hard=True)
    hard_violations.extend(v5)

    # Summary
    print("")
    print("=" * 66)
    if skipped:
        print(" Pending rules (await chokepoint helpers):")
        for s in skipped:
            print(f"   - {s}")
        print("")
    if hard_violations:
        print(f" [FAIL] {len(hard_violations)} hard violation(s).")
        return 1
    print(" [PASS] All hard rules clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
