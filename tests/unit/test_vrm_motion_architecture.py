import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATH = PROJECT_ROOT / "static" / "vrm" / "motion" / "runtime.js"
FRONTEND_TESTS = (
    "vrm_motion_bridge.test.cjs",
    "vrm_motion_semantics.test.cjs",
    "vrm_motion_policy.test.cjs",
    "vrm_motion_player.test.cjs",
)


@pytest.mark.parametrize("test_name", FRONTEND_TESTS)
def test_vrm_motion_frontend_contract(test_name: str):
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")

    test_path = PROJECT_ROOT / "tests" / "frontend" / test_name
    result = run_node_script(
        node_path,
        test_path.read_text(encoding="utf-8"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_closed_stages_use_assistant_speech_guards():
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    process_stage = source.split("async function processStage(stage, turn) {", 1)[1].split(
        "async function processSpeech",
        1,
    )[0]

    assert "speechMode: true," in process_stage
    assert process_stage.index("speechMode: true,") < process_stage.index("stageDirection: true")
