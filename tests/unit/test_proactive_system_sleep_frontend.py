"""Frontend half of the #3552 fix: no catch-up proactive tick after system sleep,
and ``PASS_SYSTEM_INACTIVE`` is not counted as an attempt.

The behavioural suite lives in ``tests/frontend/proactive_system_sleep.test.cjs``
(node:test; it runs the real ``static/app/app-proactive.js`` in a vm context).
This file is only the pytest entry point.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_proactive_system_sleep_node_suite() -> None:
    from tests.node_harness import run_node_script

    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")

    test_path = ROOT / "tests" / "frontend" / "proactive_system_sleep.test.cjs"
    result = run_node_script(
        node_path,
        test_path.read_text(encoding="utf-8"),
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
