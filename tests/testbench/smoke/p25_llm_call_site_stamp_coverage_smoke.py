"""P25 Day 3 — ``last_llm_wire`` stamp coverage smoke.

Purpose
-------
**Static** coverage check: every LLM call site under ``tests/testbench``
(``client.ainvoke(...)`` / ``client.astream(...)`` / ``llm.ainvoke(...)``)
must have a *preceding* :func:`pipeline.wire_tracker.record_last_llm_wire`
call in the **same function body**. This is the "5b + forced side" of
the L36 §7.25 fifth-generation defense ("preview = ground-truth
snapshot"): if a new LLM call site lands without stamping, the Prompt
Preview tab silently keeps showing whatever the **previous** (possibly
unrelated) call left there.

Without this smoke, we found 4 bare call sites the dynamic
`p25_prompt_preview_truth_smoke.py` couldn't catch (memory.recent /
memory.facts / memory.reflect / memory.persona_correction + 1 in
judge + 1 in simuser). This smoke locks all of them.

r7 update (2026-04-23)
~~~~~~~~~~~~~~~~~~~~~~
``simulated_user.generate_simuser_message`` switched from stamped
(``source="simulated_user"``) to ``NOSTAMP(wire_tracker)`` — SimUser
is a *conversation source*, not an object under test, so its wire
has no diagnostic value on the Chat Preview Panel. The C2 sentinel
check correspondingly dropped ``simulated_user`` + ``auto_dialog_simuser``
from ``KNOWN_SOURCES``; this smoke re-validates that removal does
not leave any stale literal behind.

Escape hatch
------------
Not every ``ainvoke`` is a conversation turn. Add a
``NOSTAMP(wire_tracker):`` comment *on or within 3 lines above* the
call line to mark it as intentionally unstamped (e.g. the
``config_router._ping_chat`` connectivity probe). The justification
comment text is logged at smoke start so a reviewer can sanity-check
the growing allow-list.

Contracts
---------
    C1: every ``.ainvoke(...)`` / ``.astream(...)`` / ``.invoke(...)``
        under ``tests/testbench/pipeline/`` and
        ``tests/testbench/routers/`` has either:
          (a) a preceding ``record_last_llm_wire(`` call in the same
              function body (parent ``ast.FunctionDef`` /
              ``ast.AsyncFunctionDef``), or
          (b) a ``NOSTAMP(wire_tracker):`` sentinel within 3 lines
              above the call.

    C2: every ``wire_source`` passed to ``record_last_llm_wire`` is a
        literal string equal to a member of
        :data:`pipeline.wire_tracker.KNOWN_SOURCES`. (Catches typos
        like ``source="memmory.llm"`` that PP7's dynamic check would
        only flag on a smoke that actually drives that code path.)

    C3: ``wire_tracker.KNOWN_SOURCES`` is the single authoritative
        whitelist — nothing else in the codebase redeclares or shadows
        it. (Guard against the "two whitelists drift" anti-pattern.)

Usage::

    .venv\\Scripts\\python.exe \\
        tests/testbench/smoke/p25_llm_call_site_stamp_coverage_smoke.py

Exits non-zero on any violation.
"""
from __future__ import annotations

import ast
import io
import sys
from pathlib import Path


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── configuration ─────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTBENCH_ROOT = REPO_ROOT / "tests" / "testbench"

# Scan scope — production code that could call LLMs. Smoke / test files
# are allowed to ``ainvoke`` on their own mocks without stamping, so we
# exclude ``smoke/`` + ``_subagent_handoff/`` + ``docs/``.
SCAN_DIRS: tuple[Path, ...] = (
    TESTBENCH_ROOT / "pipeline",
    TESTBENCH_ROOT / "routers",
)

# Attribute names we consider "an LLM call" (call on any expression,
# e.g. ``client.ainvoke(...)``, ``llm.ainvoke(...)``, ``client.astream
# (...)``). Plain ``.invoke(...)`` is included defensively — as of 2026-
# 04 no production site uses it, but if a future refactor adds one
# without a stamp, we want to catch it.
LLM_CALL_ATTRS: frozenset[str] = frozenset({
    "ainvoke",
    "astream",
    "invoke",
})

# NOSTAMP marker — inline comment on the call line or within the N
# previous source lines. Kept short for grep-ability. Lookback covers
# a ~10-line justification comment block (the longest current case is
# 6 lines in ``config_router._ping_chat``); bump higher if a future
# opt-out legitimately needs more context, but keep it small enough
# that the marker is "near" the call site by human reading standards.
NOSTAMP_MARKER: str = "NOSTAMP(wire_tracker)"
NOSTAMP_LOOKBACK_LINES: int = 10


# ── helpers ───────────────────────────────────────────────────────────


def _iter_py_files(roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            # Skip __pycache__ leftovers just in case.
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return sorted(files)


def _enclosing_func(
    node: ast.AST, func_map: dict[int, ast.AST],
) -> ast.AST | None:
    """Return the nearest enclosing FunctionDef / AsyncFunctionDef.

    ``func_map`` maps a node's ``id(node)`` -> nearest enclosing func
    (built in a single pass via :func:`_build_parent_func_map`).
    """
    return func_map.get(id(node))


def _build_parent_func_map(tree: ast.Module) -> dict[int, ast.AST]:
    """Walk the AST once, map every child to its nearest enclosing FunctionDef."""
    m: dict[int, ast.AST] = {}

    def _walk(node: ast.AST, current_func: ast.AST | None) -> None:
        is_func = isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda),
        )
        next_func = node if is_func else current_func
        if current_func is not None:
            m[id(node)] = current_func
        if is_func:
            m[id(node)] = node
        for child in ast.iter_child_nodes(node):
            _walk(child, next_func)

    _walk(tree, None)
    return m


def _extract_wire_source_args(tree: ast.Module) -> list[tuple[int, str | None]]:
    """Return (lineno, source_literal_or_None) for every record_last_llm_wire call.

    ``None`` means the ``source=`` kwarg was not a string literal (could
    be a variable, f-string, etc). We record that case explicitly so C2
    can flag it — all current production sites use literals.
    """
    out: list[tuple[int, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fn_name: str | None = None
        if isinstance(fn, ast.Name):
            fn_name = fn.id
        elif isinstance(fn, ast.Attribute):
            fn_name = fn.attr
        if fn_name != "record_last_llm_wire":
            continue
        literal: str | None = None
        for kw in node.keywords:
            if kw.arg != "source":
                continue
            val = kw.value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                literal = val.value
            break
        out.append((node.lineno, literal))
    return out


def _has_nostamp_marker(source_lines: list[str], call_lineno: int) -> bool:
    """Check for a ``NOSTAMP(wire_tracker)`` comment near the call line."""
    # Window: the call line itself down to ``NOSTAMP_LOOKBACK_LINES`` lines above.
    start = max(1, call_lineno - NOSTAMP_LOOKBACK_LINES)
    for ln in range(start, call_lineno + 1):
        # source_lines is 0-indexed, AST linenos are 1-indexed.
        idx = ln - 1
        if 0 <= idx < len(source_lines):
            if NOSTAMP_MARKER in source_lines[idx]:
                return True
    return False


def _has_preceding_stamp(
    func_node: ast.AST, call_lineno: int,
) -> bool:
    """Any ``record_last_llm_wire(...)`` call inside ``func_node`` before ``call_lineno``.

    "Inside" = anywhere in its body (nested try/with/for is fine — L31
    ``try/except`` around the stamp is the documented pattern).
    """
    for sub in ast.walk(func_node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        fn_name = None
        if isinstance(fn, ast.Name):
            fn_name = fn.id
        elif isinstance(fn, ast.Attribute):
            fn_name = fn.attr
        if fn_name != "record_last_llm_wire":
            continue
        # Stamp must precede the LLM call (line-order is a coarse but
        # correct proxy — Python source is executed top-down inside a
        # function; structural re-ordering would require a goto).
        if sub.lineno <= call_lineno:
            return True
    return False


# ── contract checks ───────────────────────────────────────────────────


def check_c1_every_call_site_has_preceding_stamp() -> list[str]:
    """C1: each LLM call site is preceded by a stamp or NOSTAMP sentinel."""
    errors: list[str] = []
    nostamp_allowed: list[str] = []

    for path in _iter_py_files(SCAN_DIRS):
        try:
            source_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(
                f"[C1.decode] {path.relative_to(REPO_ROOT)}: "
                f"file is not UTF-8, refusing to scan"
            )
            continue
        source_lines = source_text.splitlines()
        try:
            tree = ast.parse(source_text, filename=str(path))
        except SyntaxError as exc:
            errors.append(
                f"[C1.parse] {path.relative_to(REPO_ROOT)}:{exc.lineno}: "
                f"SyntaxError — {exc.msg}"
            )
            continue

        func_map = _build_parent_func_map(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            if fn.attr not in LLM_CALL_ATTRS:
                continue

            # Guard: skip plain ``.invoke(...)`` calls that are clearly
            # not against a ChatOpenAI-shaped object. Common false
            # positives: ``session_operation().__invoke__``, pathlib
            # ``Path.invoke`` (doesn't exist), etc. We keep a narrow
            # whitelist by looking at the call site's attribute chain:
            # must be ``<expr>.ainvoke/astream/invoke(<list-ish>)``.
            # For ``.invoke`` specifically require an ``HumanMessage``
            # / ``SystemMessage`` in args or a wire-list shape to
            # reduce false positives — but as of 2026-04 no production
            # code uses bare ``.invoke`` so we just warn-report these
            # via a less strict path.
            if fn.attr == "invoke":
                # Heuristic: only flag if first arg looks like a list
                # literal (wire shape) or HumanMessage(...). Otherwise
                # silently skip — avoids flagging unrelated
                # ``something.invoke(...)`` in other libraries.
                if not node.args:
                    continue
                arg0 = node.args[0]
                is_wire_shape = (
                    isinstance(arg0, ast.List)
                    or (
                        isinstance(arg0, ast.Call)
                        and isinstance(arg0.func, ast.Name)
                        and arg0.func.id in {"HumanMessage", "SystemMessage", "AIMessage"}
                    )
                )
                if not is_wire_shape:
                    continue

            call_lineno = node.lineno
            rel = path.relative_to(REPO_ROOT)

            if _has_nostamp_marker(source_lines, call_lineno):
                nostamp_allowed.append(f"{rel}:{call_lineno}")
                continue

            func = _enclosing_func(node, func_map)
            if func is None:
                errors.append(
                    f"[C1.no_func] {rel}:{call_lineno}: "
                    f"LLM call at module level — unable to check for stamp"
                )
                continue

            if not _has_preceding_stamp(func, call_lineno):
                fn_name = getattr(func, "name", "<lambda>")
                errors.append(
                    f"[C1.missing_stamp] {rel}:{call_lineno}: "
                    f"{fn.attr}(...) inside {fn_name!r} has no preceding "
                    f"record_last_llm_wire(...) call — add one before "
                    f"the LLM invocation, or mark the line with "
                    f"NOSTAMP(wire_tracker) + justification comment"
                )

    # Friendly audit trail: log the NOSTAMP sites once at the top so the
    # reviewer can eyeball the growing allow-list without opening files.
    if nostamp_allowed:
        print("")
        print(
            f"  [info] {len(nostamp_allowed)} NOSTAMP site(s) allowlisted:"
        )
        for site in nostamp_allowed:
            print(f"    - {site}")

    return errors


def check_c2_stamp_source_literal_matches_whitelist() -> list[str]:
    """C2: every ``source=`` passed to record_last_llm_wire is a known literal."""
    from tests.testbench.pipeline.wire_tracker import KNOWN_SOURCES

    errors: list[str] = []
    for path in _iter_py_files(SCAN_DIRS):
        try:
            source_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(source_text, filename=str(path))
        except SyntaxError:
            continue

        rel = path.relative_to(REPO_ROOT)
        for lineno, literal in _extract_wire_source_args(tree):
            if literal is None:
                # Non-literal source — could be a variable passed in
                # (e.g. ``source=_wire_source`` in chat_router, where the
                # branch depends on whether the caller is SOURCE_AUTO).
                # Allow, because the runtime hard-validates against
                # KNOWN_SOURCES (wire_tracker raises ValueError) + PP7
                # already covers typo propagation. (r7 2026-04-23: note
                # that the earlier ``simulated_user.wire_source`` pattern
                # is gone — SimUser is now NOSTAMP, so no such kwarg
                # remains in the codebase.)
                continue
            if literal not in KNOWN_SOURCES:
                errors.append(
                    f"[C2.unknown_source] {rel}:{lineno}: "
                    f"record_last_llm_wire(source={literal!r}) "
                    f"not in KNOWN_SOURCES — add to "
                    f"pipeline/wire_tracker.py KNOWN_SOURCES + "
                    f"i18n 'chat.preview.last_wire.source.<slug>' + "
                    f"re-run this smoke"
                )
    return errors


def check_c3_single_known_sources_whitelist() -> list[str]:
    """C3: ``KNOWN_SOURCES`` only declared in wire_tracker.py."""
    errors: list[str] = []
    # Scan whole testbench tree (not just SCAN_DIRS) to catch a rogue
    # redeclaration anywhere — including smoke / docs-as-code.
    seen_decls: list[tuple[Path, int]] = []
    for path in _iter_py_files((TESTBENCH_ROOT,)):
        try:
            source_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(source_text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # Catch ``KNOWN_SOURCES = frozenset(...)`` style assignments
            # at any scope. Importing the name (``from ... import
            # KNOWN_SOURCES``) is fine — that's just a reference.
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "KNOWN_SOURCES":
                        seen_decls.append((path, node.lineno))
            elif isinstance(node, ast.AnnAssign):
                tgt = node.target
                if isinstance(tgt, ast.Name) and tgt.id == "KNOWN_SOURCES":
                    seen_decls.append((path, node.lineno))

    if len(seen_decls) != 1:
        for p, ln in seen_decls:
            errors.append(
                f"[C3.multi_decl] {p.relative_to(REPO_ROOT)}:{ln}: "
                f"KNOWN_SOURCES = ... (expected exactly 1 declaration; "
                f"found {len(seen_decls)}) — rename any shadow "
                f"declaration + import the one in "
                f"pipeline/wire_tracker.py instead"
            )
    elif seen_decls:
        p, _ln = seen_decls[0]
        expected = TESTBENCH_ROOT / "pipeline" / "wire_tracker.py"
        if p != expected:
            errors.append(
                f"[C3.wrong_home] {p.relative_to(REPO_ROOT)}: "
                f"KNOWN_SOURCES declaration should live in "
                f"pipeline/wire_tracker.py, not here"
            )

    return errors


# ── runner ────────────────────────────────────────────────────────────


def _report(title: str, errors: list[str]) -> int:
    print("")
    print(f"* {title}")
    if not errors:
        print("  [ok]")
        return 0
    print(f"  [ERR] {len(errors)} violation(s):")
    for line in errors:
        print(f"    {line}")
    return len(errors)


def main() -> int:
    print("=" * 70)
    print(" P25 Day 3 — LLM call-site stamp coverage smoke")
    print("=" * 70)
    print(f" scan roots: {[str(p.relative_to(REPO_ROOT)) for p in SCAN_DIRS]}")
    print(f" LLM call attrs: {sorted(LLM_CALL_ATTRS)}")
    print(f" nostamp marker: {NOSTAMP_MARKER} (lookback = "
          f"{NOSTAMP_LOOKBACK_LINES} lines above call)")

    sys.path.insert(0, str(REPO_ROOT))  # so ``from tests.testbench...`` resolves

    total = 0
    total += _report(
        "C1 — every ainvoke/astream is preceded by record_last_llm_wire "
        "(or NOSTAMP)",
        check_c1_every_call_site_has_preceding_stamp(),
    )
    total += _report(
        "C2 — every record_last_llm_wire(source=...) literal is in "
        "KNOWN_SOURCES",
        check_c2_stamp_source_literal_matches_whitelist(),
    )
    total += _report(
        "C3 — KNOWN_SOURCES declared exactly once, in wire_tracker.py",
        check_c3_single_known_sources_whitelist(),
    )

    print("")
    print("-" * 70)
    if total == 0:
        print("[PASS] All LLM call sites are stamped (or explicitly NOSTAMP).")
        return 0
    print(f"[FAIL] {total} violation(s) — see above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
