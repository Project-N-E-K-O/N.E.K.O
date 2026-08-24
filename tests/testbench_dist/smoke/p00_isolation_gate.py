"""Isolation gate: testbench_dist must not leak into tests/testbench.

C1 — no testbench_dist / TESTBENCH_FROZEN / IS_FROZEN strings in testbench sources
C2 — no ``import tests.testbench_dist`` / ``from tests.testbench_dist`` in testbench
C3 — bootstrap describe_paths writes under user_data when --standalone-paths applied
"""
from __future__ import annotations

import ast
import re
import sys
import tempfile
from pathlib import Path

_DIST = Path(__file__).resolve().parents[1]
_PROJECT = _DIST.parents[1]
_TESTBENCH = _PROJECT / "tests" / "testbench"

_FORBIDDEN_SUBSTRINGS = (
    "testbench_dist",
    "TESTBENCH_FROZEN",
    "IS_FROZEN",
    "sys._MEIPASS",
)

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+tests\.testbench_dist|import\s+tests\.testbench_dist)\b",
    re.MULTILINE,
)

_SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", "_subagent_handoff"}


def _iter_source_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".js", ".mjs"}:
            continue
        yield path


def check_c1_c2() -> list[str]:
    failures: list[str] = []
    for path in _iter_source_files(_TESTBENCH):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(_PROJECT)
        for needle in _FORBIDDEN_SUBSTRINGS:
            if needle in text:
                failures.append(f"C1: {rel} contains forbidden '{needle}'")
        if path.suffix == ".py" and _IMPORT_RE.search(text):
            failures.append(f"C2: {rel} imports tests.testbench_dist")
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("tests.testbench_dist"):
                            failures.append(f"C2: {rel} import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod.startswith("tests.testbench_dist"):
                        failures.append(f"C2: {rel} from {mod}")
    return failures


def check_c3_bootstrap_paths() -> list[str]:
    failures: list[str] = []
    if str(_PROJECT) not in sys.path:
        sys.path.insert(0, str(_PROJECT))
    # Drop testbench dir from path[0] shadow
    sys.path[:] = [p for p in sys.path if Path(p).resolve() != _TESTBENCH.resolve()]

    from tests.testbench_dist.src.bootstrap import apply_standalone_patches, describe_paths

    with tempfile.TemporaryDirectory(prefix="tb_dist_gate_") as tmp:
        user_data = Path(tmp) / "user_data"
        apply_standalone_patches(bundle_dir=_PROJECT, user_data_dir=user_data)
        paths = describe_paths()
        for key in ("DATA_DIR", "LOGS_DIR", "SANDBOXES_DIR", "LIVE_DIR", "API_KEYS_PATH"):
            value = Path(paths[key])
            if user_data.resolve() not in value.resolve().parents and value.resolve() != user_data.resolve():
                # API_KEYS_PATH is a file under user_data
                if key == "API_KEYS_PATH" and value.parent.resolve() == user_data.resolve():
                    continue
                if key != "API_KEYS_PATH" and value.resolve() == user_data.resolve():
                    continue
                # Allow exact children
                try:
                    value.resolve().relative_to(user_data.resolve())
                except ValueError:
                    failures.append(f"C3: {key}={value} not under {user_data}")
        if not Path(paths["API_KEYS_PATH"]).is_file():
            failures.append("C3: api_keys.json was not created")
    return failures


def main() -> int:
    failures = check_c1_c2() + check_c3_bootstrap_paths()
    if failures:
        print("[FAIL] p00_isolation_gate")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("[OK] p00_isolation_gate (C1–C3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
