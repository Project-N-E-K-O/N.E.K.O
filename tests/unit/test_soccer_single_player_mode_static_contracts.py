from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOCCER_SCRIPT = PROJECT_ROOT / "static/game/games/soccer/soccer-demo.js"


def _source() -> str:
    return SOCCER_SCRIPT.read_text(encoding="utf-8")


def test_single_player_hotkey_is_only_available_for_explicit_test_urls():
    source = _source()

    assert "get('test') === 'true'" in source
    keydown_block = source.split("window.addEventListener('keydown', e => {", 1)[1].split(
        "// 通用踢球",
        1,
    )[0]
    assert "soccerTestEnabled" in keydown_block
    assert "e.key === '[' || e.code === 'BracketLeft'" in keydown_block
    assert "toggleSinglePlayerMode('keyboard_bracket_left')" in keydown_block


def test_single_player_mode_stops_ai_motion_kicks_and_ball_collision():
    source = _source()

    toggle_block = source.split("function toggleSinglePlayerMode", 1)[1].split(
        "window.addEventListener('mousemove'",
        1,
    )[0]
    assert "if (!soccerTestEnabled) return false" in toggle_block
    assert "singlePlayerMode = !singlePlayerMode" in toggle_block
    assert "state.ai.vx = 0" in toggle_block
    assert "state.ai.vy = 0" in toggle_block
    assert "aiWindupRemaining = 0" in toggle_block
    assert "aiRetreatSec = 0" in toggle_block
    assert "return singlePlayerMode" in toggle_block

    reset_block = source.split("function resetPositions", 1)[1].split(
        "window.addEventListener('mousemove'",
        1,
    )[0]
    assert "if (!singlePlayerMode)" in reset_block

    kick_block = source.split("function kickBall", 1)[1].split("function kickDirFor", 1)[0]
    assert "if (singlePlayerMode && kicker === state.ai) return false" in kick_block

    ai_kick_block = source.split("function aiTryKick", 1)[1].split("function stepCharacter", 1)[0]
    assert "if (singlePlayerMode)" in ai_kick_block

    collision_block = source.split("function resolveCharBall", 1)[1].split("function checkGoal", 1)[0]
    assert "if (singlePlayerMode && c === state.ai) return" in collision_block

    ai_decide_block = source.split("function aiDecide", 1)[1].split("function aiTarget", 1)[0]
    assert "if (singlePlayerMode)" in ai_decide_block

    unstick_block = source.split("function unstickBall", 1)[1].split("AI 说话子系统", 1)[0]
    assert "if (!singlePlayerMode)" in unstick_block

    loop_block = source.split("function loop(t)", 1)[1].split("function render()", 1)[0]
    assert "if (singlePlayerMode)" in loop_block
    assert "state.ai.vx = 0" in loop_block
    assert "state.ai.vy = 0" in loop_block


def test_single_player_mode_displays_ran_away_without_extending_mood_schema():
    source = _source()

    assert "singlePlayerMode ? '气跑了' : moodKey" in source
    debug_mood_block = source.split("function debugMoodLabel", 1)[1].split(
        "function debugDifficultyLabel",
        1,
    )[0]
    assert "if (singlePlayerMode) return '气跑了'" in debug_mood_block
    assert "singlePlayerMode," in source.split("_snapshot: () =>", 1)[1]
    assert "MOOD_KEYS = ['calm', 'happy', 'angry', 'relaxed', 'sad', 'surprised']" in source


def test_manual_mood_and_difficulty_controls_require_explicit_test_url():
    source = _source()

    keydown_block = source.split("window.addEventListener('keydown', e => {", 1)[1].split(
        "// 通用踢球",
        1,
    )[0]
    manual_hotkey_block = keydown_block.split(
        "// 仅测试 URL 允许人工切换难度与心情。",
        1,
    )[1]
    assert "if (soccerTestEnabled)" in manual_hotkey_block
    assert "setDifficulty(difficultyHotkey, 'difficulty-hotkey')" in manual_hotkey_block
    assert "setMood(MOOD_KEYS[idx], { manual: true })" in manual_hotkey_block

    panel_block = source.split("function handleMoodDebugButton", 1)[1].split(
        "moodDebugPanel?.querySelectorAll('button')",
        1,
    )[0]
    assert "if (!soccerTestEnabled && (mood || difficulty || action === 'rotation-toggle')) return" in panel_block
    assert "btn.disabled = !soccerTestEnabled" in source

    debug_api_block = source.split("window.SoccerDemoDebug = {", 1)[1].split(
        "// 正常 LLM 模式下不自动轮换心情",
        1,
    )[0]
    assert debug_api_block.count("if (!soccerTestEnabled) return getMoodDebugSnapshot()") >= 3
