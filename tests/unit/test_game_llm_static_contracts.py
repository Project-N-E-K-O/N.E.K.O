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
    assert es_prompt != en_prompt


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
def test_realtime_reconnect_resets_active_instructions():
    realtime_py = (ROOT / "main_logic" / "omni_realtime_client.py").read_text(encoding="utf-8")

    assert not re.search(
        r"if\s+self\._active_instructions\s+is\s+None\s*:\s*\n\s*self\._active_instructions\s*=\s*instructions",
        realtime_py,
    )
    assert re.search(
        r"self\.instructions\s*=\s*instructions\s*\n\s*self\._active_instructions\s*=\s*instructions",
        realtime_py,
    )
