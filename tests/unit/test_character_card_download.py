"""Guard character exports against duplicate save dialogs and empty files."""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


def test_character_card_download_node_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for character export tests")
    root = Path(__file__).resolve().parents[2]
    suite = root / "tests" / "frontend" / "character_card_download.test.cjs"
    result = run_node_script(
        node,
        f"require({json.dumps(str(suite))});",
        cwd=root,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
