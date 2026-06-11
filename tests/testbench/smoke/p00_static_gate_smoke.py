"""P00 static gate smoke — pre-push py_compile + import sweep over testbench code.

Why this is ``p00_`` (not ``p27_`` / not date-prefixed)
-------------------------------------------------------
This smoke is **gate 0** for ``_run_all.py``: it must pass before any
behavioral / contract smoke is even worth running. ``_run_all.py``
discovers smoke files via ``sorted(SMOKE_DIR.glob("*_smoke.py"))`` —
alphabetical — so ``p00_`` sorts before ``p21_``..``p26_`` and runs
first. When a SyntaxError or top-level NameError sneaks in (typically
via ``git rebase`` / ``git merge`` / ``git pull`` / GitHub Web UI
"Apply suggestion"), the runner stops at p00 instead of trying to
import the broken module 17 more times across other smokes and
producing 17 redundant ``ModuleNotFoundError: No module named '<bad>'``
failures that drown out the real cause.

Two layers
----------
**Layer 1: py_compile sweep**
  Walk ``tests/testbench/**/*.py`` and ``py_compile.compile(doraise=True)``
  each one. Catches **SyntaxError** / **IndentationError** / **TabError**.
  Does NOT execute module code, so import-time errors do not show up here.

**Layer 2: importlib sweep**
  ``importlib.import_module`` each module under
  ``pipeline / routers / services / clients / snapshots``. This actually
  runs module-level code, so we catch **NameError** / **top-level use of
  unimported symbols** / **missing third-party packages** (means the
  venv is wrong → fast diagnostic) / any **import-time side-effect bug**.

If the py_compile layer fails, the import layer is **skipped** — every
module in the same package will cascade-fail with
``SyntaxError`` / ``ImportError`` and drown the real error in noise.

Why this catches bugs the other smokes miss
-------------------------------------------
Behavioral smokes import the modules they exercise — but only a
subset of them. A module like ``redact.py`` may sit on the
``diagnostics_store`` / ``persistence`` lazy-import path and never be
loaded by any behavioral smoke. P26 hotfix 2026-04-25 (AGENT_NOTES
§4.27 #121, LESSONS L54): a ``SyntaxError`` in ``redact.py`` shipped
to ``NEKO-dev/main`` even though all 18 behavioral smokes were green,
because none of them touched the redact lazy path. p00 is the
chokepoint that closes that hole — it runs ``py_compile`` on
**every** ``.py`` under ``tests/testbench/``, no exceptions.

Triggers — when this smoke catches stuff
-----------------------------------------
- After ``git rebase`` / ``git merge`` / ``git cherry-pick`` /
  ``git pull --rebase`` pulls in upstream small commits.
- After GitHub Web UI **"Apply suggestion"** / **"Commit suggestion"**
  buttons apply a bot's patch. These don't trigger CI re-runs and
  frequently produce ``+N/-1`` patches whose new lines contain
  structural tokens (``else:`` / ``try:`` / ``with:``) that don't
  merge cleanly with the surrounding context (the redact.py case).
- After any single-file edit that adds an import, removes a symbol,
  renames a function, or moves a constant.

Output
------
On success::

    [P00 static gate] py_compile : 77/77 passed
    [P00 static gate] import     : 43/43 passed
    [P00 static gate] OK

On failure (exits 1)::

    FAIL py_compile tests/testbench/pipeline/redact.py: SyntaxError ...
    [P00 static gate] py_compile : 76/77 passed
    [P00 static gate] FAILED — fix syntax errors before running other smokes.

Usage
-----
Run via ``_run_all.py`` (it picks p00 up automatically as the first
smoke), or directly::

    .venv/Scripts/python.exe tests/testbench/smoke/p00_static_gate_smoke.py

Exits 0 on all-clean, 1 on any failure.

See also
--------
* ``LESSONS_LEARNED.md`` L54 — the design rationale ("rebase / merge
  introduces upstream code → must rerun static gate + smoke before push").
* ``AGENT_NOTES.md`` §4.27 #121 — the redact.py incident that motivated
  promoting these two helpers from one-off ``.git/_lint_check.py`` /
  ``.git/_import_check.py`` scripts to a permanent gate-0 smoke.
"""
from __future__ import annotations

import importlib
import io
import py_compile
import sys
from pathlib import Path

if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TESTBENCH = _PROJECT_ROOT / "tests" / "testbench"

#: Directories to py_compile-sweep. Single root keeps coverage maximal —
#: anything under tests/testbench/ that ends in .py gets checked.
_PYCOMPILE_ROOTS: list[Path] = [_TESTBENCH]

#: Directories whose modules we additionally try to ``import_module``.
#: Restricted to the *runtime* code paths (no smoke/, no docs/, no
#: dialog_templates/) because (a) smoke files are entry-point scripts
#: meant for ``python <file>`` not ``import``; (b) docs/ has no .py;
#: (c) the runtime layer is what actually has to load cleanly when the
#: server boots.
_IMPORT_ROOTS: list[Path] = [
    _TESTBENCH / "pipeline",
    _TESTBENCH / "routers",
    _TESTBENCH / "services",
    _TESTBENCH / "clients",
    _TESTBENCH / "snapshots",
]


def _gate_pycompile() -> tuple[int, int, list[tuple[Path, str]]]:
    """Walk ``_PYCOMPILE_ROOTS`` and ``py_compile`` every ``.py`` file.

    Returns ``(total_files, passed_files, errors)`` where ``errors`` is
    a list of ``(absolute_path, one_line_message)`` tuples.
    """
    errors: list[tuple[Path, str]] = []
    total = 0
    for root in _PYCOMPILE_ROOTS:
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            total += 1
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as exc:
                msg = str(exc).strip().splitlines()[-1]
                errors.append((py, msg))
            except SyntaxError as exc:
                errors.append(
                    (py, f"SyntaxError: {exc.msg} @ line {exc.lineno}")
                )
    return total, total - len(errors), errors


def _gate_import() -> tuple[int, int, list[tuple[str, str]]]:
    """Walk ``_IMPORT_ROOTS`` and ``importlib.import_module`` every module.

    Returns ``(total_modules, passed_modules, errors)`` where ``errors``
    is a list of ``(dotted_module_name, one_line_message)`` tuples.
    Skips ``__init__.py`` (those import as their parent package, which
    we cover transitively when we import the first non-init module).
    """
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))

    errors: list[tuple[str, str]] = []
    total = 0
    for root in _IMPORT_ROOTS:
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            if py.name == "__init__.py":
                continue
            rel = py.relative_to(_PROJECT_ROOT).with_suffix("")
            mod_name = ".".join(rel.parts)
            total += 1
            try:
                importlib.import_module(mod_name)
            except Exception as exc:
                msg = (
                    f"{type(exc).__name__}: {exc}".strip().splitlines()[0]
                )
                errors.append((mod_name, msg))
    return total, total - len(errors), errors


def main() -> int:
    pc_total, pc_pass, pc_errors = _gate_pycompile()
    for path, msg in pc_errors:
        try:
            rel = path.relative_to(_PROJECT_ROOT)
        except ValueError:
            rel = path
        print(f"FAIL py_compile {rel}: {msg}")
    print(
        f"[P00 static gate] py_compile : {pc_pass}/{pc_total} passed"
    )

    if pc_errors:
        print(
            "[P00 static gate] FAILED — fix syntax errors before "
            "running other smokes (importing a syntax-broken module "
            "would cascade-fail every importer and bury the real error)."
        )
        return 1

    im_total, im_pass, im_errors = _gate_import()
    for mod, msg in im_errors:
        print(f"FAIL import     {mod}: {msg}")
    print(
        f"[P00 static gate] import     : {im_pass}/{im_total} passed"
    )

    if im_errors:
        print(
            "[P00 static gate] FAILED — fix the above before running "
            "other smokes."
        )
        return 1

    print("[P00 static gate] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
