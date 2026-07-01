import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")
    try:
        return subprocess.run(
            [node_path, "-"],
            input=script,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"node harness timed out: {exc}")


def test_normalized_move_scene_can_reuse_previous_spotlight_key():
    script = textwrap.dedent(
        """
        const { normalizeTutorialScene } = require('./static/tutorial/core/script-normalizer.js');

        const scene = normalizeTutorialScene({
            id: 'day6_agent_task_hud_control',
            text: 'line',
            voiceKey: 'voice',
            target: '#agent-task-hud',
            cursorAction: 'move',
            spotlightKey: 'day6_agent_task_hud',
        });

        console.log(JSON.stringify(scene.timeline));
        """
    )

    result = _run_node_harness(script)

    assert result.returncode == 0, result.stderr
    timeline = json.loads(result.stdout)
    spotlight = next(event for event in timeline if event["command"] == "spotlight.show")
    assert spotlight["key"] == "day6_agent_task_hud"
    cursor_move = next(event for event in timeline if event["command"] == "cursor.move")
    assert cursor_move["target"] == "#agent-task-hud"


def test_node_harness_has_timeout():
    source = Path(__file__).read_text(encoding="utf-8")
    helper = source.split("def _run_node_harness(script: str)", 1)[1].split(
        "def test_normalized_move_scene_can_reuse_previous_spotlight_key",
        1,
    )[0]

    assert "timeout=10" in helper
    assert "except subprocess.TimeoutExpired as exc:" in helper
