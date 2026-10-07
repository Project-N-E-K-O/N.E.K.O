"""Pytest entry point for the default-model/goodbye return behavior suite."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_default_model_goodbye_return_node_suite() -> None:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")

    suite_path = PROJECT_ROOT / "tests" / "frontend" / "default_model_goodbye_return.test.cjs"
    result = run_node_script(
        node_path,
        f"require({json.dumps(str(suite_path))});",
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
