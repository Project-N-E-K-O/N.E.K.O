import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_card_maker_render_and_model_save_context_behaviour():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")

    script = PROJECT_ROOT / "tests" / "unit" / "card_maker_render.test.js"
    result = run_node_script(
        node,
        f"require({json.dumps(str(script))});",
        cwd=PROJECT_ROOT,
        timeout=30,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# tests 6" in result.stdout
    assert "# pass 6" in result.stdout
