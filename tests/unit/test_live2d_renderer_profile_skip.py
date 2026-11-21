"""Live2D renderer profile skip: the Electron pet window and Linux X11 desktop runtime
skip the Cubism per-frame WebGL profile save/restore (issue #3593).

The behavioural suite lives in ``tests/frontend/live2d_renderer_profile_skip.test.cjs``
(node:test; it loads the real ``static/live2d/live2d-core.js``). This file is only the
pytest entry point.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_live2d_renderer_profile_skip_node_suite() -> None:
    from tests.node_harness import run_node_script

    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")

    test_path = ROOT / "tests" / "frontend" / "live2d_renderer_profile_skip.test.cjs"
    result = run_node_script(
        node_path,
        test_path.read_text(encoding="utf-8"),
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
