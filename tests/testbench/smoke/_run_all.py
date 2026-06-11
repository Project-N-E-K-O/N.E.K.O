"""Smoke runner: execute one or more *_smoke.py scripts with a clean summary.

Why this exists
---------------
Testbench smoke tests are standalone scripts (not pytest), invoked via the
project's .venv Python. Running them directly from shells (PowerShell / cmd /
bash / the user's favourite IDE terminal) is a minefield:

* PowerShell does not support ``&&``; chained commands need ``;``.
* PowerShell heredoc (``cat << 'EOF'``) does not exist.
* ``python -c "..."`` string escaping is fragile for f-strings / quotes.
* ``python -m pytest`` does not work since these are not pytest modules.

This runner sidesteps every one of those by (a) being pure Python and
(b) discovering smoke files via glob so you never hardcode the list.

Usage
-----
From the project root (or anywhere — the script resolves paths itself)::

    # Run every smoke
    .venv/Scripts/python.exe tests/testbench/smoke/_run_all.py

    # Run only P25 smokes
    .venv/Scripts/python.exe tests/testbench/smoke/_run_all.py p25_*

    # Run a specific one
    .venv/Scripts/python.exe tests/testbench/smoke/_run_all.py p24_integration

    # List without running
    .venv/Scripts/python.exe tests/testbench/smoke/_run_all.py --list

    # Fail-fast (stop on first failure)
    .venv/Scripts/python.exe tests/testbench/smoke/_run_all.py --fail-fast

Or just double-click ``_run_all.cmd`` on Windows / run ``./_run_all.sh`` on
POSIX (companion wrapper scripts in this directory).
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
import time
from pathlib import Path

SMOKE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SMOKE_DIR.parents[2]


def _resolve_python() -> Path:
    """Find the project's venv Python (Windows / POSIX)."""

    candidates: list[Path] = []
    if os.name == "nt":
        candidates.append(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    else:
        candidates.append(PROJECT_ROOT / ".venv" / "bin" / "python")
        candidates.append(PROJECT_ROOT / ".venv" / "bin" / "python3")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # Fall back to sys.executable; this still works if the user ran us with
    # the right Python already (e.g. `uv run python ...`).
    return Path(sys.executable)


def _discover(patterns: list[str]) -> list[Path]:
    """Find *_smoke.py files in SMOKE_DIR matching user patterns.

    * No patterns -> all smoke files.
    * Patterns match against the *stem* (e.g. ``p25_external_events_smoke``)
      with fnmatch semantics; leading/trailing ``*`` and ``_smoke`` suffix
      are added forgivingly if missing.
    """

    all_smokes = sorted(
        p for p in SMOKE_DIR.glob("*_smoke.py") if p.is_file()
    )
    if not patterns:
        return all_smokes

    def _normalise(pat: str) -> list[str]:
        if any(ch in pat for ch in "*?["):
            return [pat]
        return [pat, f"{pat}*", f"*{pat}*", f"{pat}_smoke", f"*{pat}*_smoke"]

    matched: list[Path] = []
    seen: set[Path] = set()
    for pat in patterns:
        for candidate in _normalise(pat):
            for path in all_smokes:
                if fnmatch.fnmatch(path.stem, candidate) and path not in seen:
                    matched.append(path)
                    seen.add(path)
    return matched


def _run_one(python_exe: Path, smoke: Path, timeout: int) -> tuple[int, float, str, str]:
    start = time.time()
    try:
        result = subprocess.run(
            [str(python_exe), str(smoke)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            timeout=timeout,
        )
        elapsed = time.time() - start
        return result.returncode, elapsed, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - start
        return (
            124,
            elapsed,
            exc.stdout or "",
            (exc.stderr or "") + f"\n[TIMEOUT after {timeout}s]",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one or more testbench smoke tests. "
        "No pattern = run everything."
    )
    parser.add_argument(
        "patterns",
        nargs="*",
        help="fnmatch-style filters against smoke stems "
        "(e.g. 'p25_*', 'p24_integration'). "
        "If omitted, every *_smoke.py runs.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List matching smokes and exit (do not run).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first failing smoke.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Per-smoke timeout in seconds (default: 180).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each smoke's full stdout/stderr (even on success).",
    )
    args = parser.parse_args()

    python_exe = _resolve_python()
    smokes = _discover(args.patterns)

    print(f"[smoke] runner    : {Path(__file__).relative_to(PROJECT_ROOT)}")
    print(f"[smoke] project   : {PROJECT_ROOT}")
    print(f"[smoke] python    : {python_exe}")
    print(f"[smoke] matched   : {len(smokes)} smoke(s)")
    if args.patterns:
        print(f"[smoke] patterns  : {args.patterns}")
    print()

    if not smokes:
        print("[smoke] No smoke files match the given patterns.")
        if args.patterns:
            print("[smoke] Try one of these stems:")
            for p in sorted(SMOKE_DIR.glob("*_smoke.py")):
                print(f"         - {p.stem}")
        return 1

    if args.list:
        for path in smokes:
            print(f"  {path.stem}")
        return 0

    results: list[tuple[str, int, float, str, str]] = []
    overall_start = time.time()
    for index, smoke in enumerate(smokes, start=1):
        name = smoke.stem
        print(f"[{index:>2}/{len(smokes)}] {name} ...", flush=True)
        rc, elapsed, stdout, stderr = _run_one(python_exe, smoke, args.timeout)
        results.append((name, rc, elapsed, stdout, stderr))
        status = "PASS" if rc == 0 else f"FAIL rc={rc}"
        print(f"         -> {status}  ({elapsed:.2f}s)")
        if rc != 0 or args.verbose:
            if stdout:
                print("         --- stdout (tail) ---")
                for line in stdout.splitlines()[-30:]:
                    print(f"         {line}")
            if stderr:
                print("         --- stderr (tail) ---")
                for line in stderr.splitlines()[-30:]:
                    print(f"         {line}")
            print()
        if rc != 0 and args.fail_fast:
            print("[smoke] --fail-fast: stopping on first failure.")
            break

    overall = time.time() - overall_start
    print()
    print("=" * 68)
    print(f"{'Smoke':<45}{'Status':<12}{'Seconds':>10}")
    print("-" * 68)
    pass_count = 0
    fail_count = 0
    for name, rc, elapsed, _o, _e in results:
        status = "PASS" if rc == 0 else f"FAIL rc={rc}"
        print(f"{name:<45}{status:<12}{elapsed:>10.2f}")
        if rc == 0:
            pass_count += 1
        else:
            fail_count += 1
    print("=" * 68)
    print(
        f"Total: {pass_count + fail_count} smoke(s), "
        f"{pass_count} PASS / {fail_count} FAIL, "
        f"elapsed {overall:.2f}s"
    )
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
