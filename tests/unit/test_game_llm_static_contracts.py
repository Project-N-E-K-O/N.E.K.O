import re
from pathlib import Path

import pytest

from config.prompts_game import (
    get_soccer_pregame_context_prompt,
    get_soccer_quick_lines_prompt,
    get_soccer_quick_lines_user_prompt,
    get_soccer_system_prompt,
)
from main_routers import game_router
from scripts import check_no_temperature


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_game_llm_paths_do_not_send_temperature_kwarg():
    assert check_no_temperature.main([
        "main_routers/game_router.py",
        "main_logic/omni_offline_client.py",
    ]) == 0


@pytest.mark.unit
def test_soccer_game_prompts_follow_user_language():
    zh_prompt = get_soccer_system_prompt("zh").format(name="Lan", personality="likes soccer")
    en_prompt = get_soccer_system_prompt("en").format(name="Lan", personality="likes soccer")
    ja_prompt = get_soccer_system_prompt("ja").format(name="Lan", personality="likes soccer")
    es_prompt = get_soccer_system_prompt("es").format(name="Lan", personality="likes soccer")

    assert "你正在和玩家踢一场足球比赛" in zh_prompt
    assert "Output only the spoken line" in en_prompt
    assert en_prompt != zh_prompt
    assert ja_prompt != en_prompt
    assert es_prompt == en_prompt  # es falls back to en until its own i18n is added


@pytest.mark.unit
def test_soccer_quick_lines_and_pregame_prompts_are_localized():
    quick_prompt = get_soccer_quick_lines_prompt("ko").format(
        name="Lan",
        personality="likes soccer",
    )
    quick_prompt_en = get_soccer_quick_lines_prompt("en").format(
        name="Lan",
        personality="likes soccer",
    )
    user_prompt = get_soccer_quick_lines_user_prompt("ru")
    user_prompt_en = get_soccer_quick_lines_user_prompt("en")
    pregame_prompt = get_soccer_pregame_context_prompt("pt")
    pregame_prompt_zh = get_soccer_pregame_context_prompt("zh")

    assert quick_prompt != quick_prompt_en
    assert user_prompt != user_prompt_en
    assert pregame_prompt != pregame_prompt_zh


@pytest.mark.unit
def test_pregame_prompt_must_not_be_format_called():
    """Pregame schema uses literal {} for JSON output; callers must not .format() it.
    If a future change needs a {placeholder}, every JSON literal must be doubled first."""
    for lang in ("zh", "en", "ja", "ko", "ru"):
        prompt = get_soccer_pregame_context_prompt(lang)
        with pytest.raises(KeyError):
            prompt.format()


@pytest.mark.unit
def test_build_game_prompt_uses_requested_language():
    prompt = game_router._build_game_prompt(
        "soccer",
        "Lan",
        "likes soccer",
        language="en",
    )

    assert "Output only the spoken line" in prompt
    assert "你正在和玩家踢一场足球比赛" not in prompt


@pytest.mark.unit
def test_game_voice_stt_gate_freezes_route_session_and_restores_mic():
    capture_js = (ROOT / "static" / "app-audio-capture.js").read_text(encoding="utf-8")

    assert "function getGameVoiceSttRouteSnapshot()" in capture_js
    assert "recognition._gameVoiceRouteSnapshot = routeSnapshot;" in capture_js
    assert "submitGameVoiceSttTranscript(finalText, recognition._gameVoiceRouteSnapshot)" in capture_js
    assert "result.reason === 'session_id_mismatch'" in capture_js
    assert "function restoreOrdinaryMicCaptureAfterGameVoiceSttStop" in capture_js
    assert "restoreOrdinaryMicCaptureAfterGameVoiceSttStop('gate stop')" in capture_js


@pytest.mark.unit
def test_game_voice_route_end_avoids_double_mic_restore():
    websocket_js = (ROOT / "static" / "app-websocket.js").read_text(encoding="utf-8")

    assert "window.stopGameVoiceSttGate({ restoreOrdinaryMic: false });" in websocket_js


@pytest.mark.unit
def test_realtime_client_has_no_game_route_surface():
    """omni_realtime_client.py must not carry any game-route-specific
    APIs after Phase 1 of the dialog-passthrough refactor — that logic
    belongs in main_routers/game_router.py + main_logic/core.py
    (mirror_*) + main_logic/cross_server.py (mirror_meta detection)."""
    realtime_py = (ROOT / "main_logic" / "omni_realtime_client.py").read_text(encoding="utf-8")

    assert "set_game_route_stt_only" not in realtime_py
    assert "_game_route_stt_only" not in realtime_py
    assert "qwen_manual_commit" not in realtime_py
    assert "_active_instructions" not in realtime_py
    assert "_can_forward_model_output" not in realtime_py
    assert "_qwen_server_vad_turn_detection_config" not in realtime_py
    assert "_openai_audio_input_config" not in realtime_py
