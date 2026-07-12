import re
from pathlib import Path

import pytest

from config.prompts.prompts_soccer import (
    get_soccer_pregame_context_prompt,
    get_soccer_quick_lines_prompt,
    get_soccer_quick_lines_user_prompt,
    get_soccer_system_prompt,
)
from main_routers import game_router
from main_routers.game_router import runtime as gr_runtime
from scripts import check_no_temperature


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_game_llm_paths_do_not_send_temperature_kwarg():
    assert check_no_temperature.main([
        "main_routers/game_router",
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
    assert es_prompt != en_prompt  # es now ships its own Spanish localization
    assert es_prompt.startswith("Eres Lan")


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
def test_soccer_realtime_context_posts_local_mutation_headers():
    html = ROOT.joinpath("templates/soccer_demo.html").read_text(encoding="utf-8")
    headers_block = html.split("function _getLocalMutationHeaders()", 1)[1].split(
        "function _refreshLocalMutationHeaders()",
        1,
    )[0]
    context_block = html.split("async function _sendRealtimeGameContext(source, items = [])", 1)[1].split(
        "async function _mirrorGameAssistantText",
        1,
    )[0]

    assert "window.nekoLocalMutationSecurity" in headers_block
    assert "getMutationHeaders" in headers_block
    assert "headers['X-CSRF-Token'] = config.autostart_csrf_token;" in headers_block
    assert "cache: 'no-store'" in headers_block
    assert "credentials: 'same-origin'" in context_block
    assert "headers,\n        body: bodyJson" in context_block
    assert "await postWithHeaders(await _getLocalMutationHeaders())" in context_block
    assert "errorPayload.error_code === 'csrf_validation_failed'" in context_block
    assert "await postWithHeaders(await _refreshLocalMutationHeaders())" in context_block


@pytest.mark.unit
def test_soccer_template_posts_session_debug_errors():
    html = ROOT.joinpath("templates/soccer_demo.html").read_text(encoding="utf-8")
    debug_start_anchor = "function _sendSoccerDebugLog(payload)"
    debug_end_anchor = "function soccerSessionDebugLog"
    assert debug_start_anchor in html
    assert debug_end_anchor in html
    debug_block = html.split(debug_start_anchor, 1)[1].split(debug_end_anchor, 1)[0]

    assert "/api/game/logs" in html
    assert "/api/game/logs/enable" in html
    assert "window.SoccerDemoDebugLog = soccerSessionDebugLog" in html
    assert "window.EnableSoccerSessionDebugLog = enableSoccerSessionDebugLog" in html
    assert "window.addEventListener('error'" in html
    assert "window.addEventListener('unhandledrejection'" in html
    assert "console.warn = function soccerDebugConsoleWarn" in html
    assert "console.error = function soccerDebugConsoleError" in html
    assert "sessionDebugLogEnabled: false" in html
    assert "sessionDebugLogEnablePromise: null" in html
    assert "sessionDebugLogEnableGeneration: 0" in html
    assert "sessionDebugLogMutationHeaders: null" in html
    assert "if (!_llm.sessionDebugLogEnabled) return;" in debug_block
    assert "function resetSoccerSessionDebugLogEnableState()" in html
    assert "resetSoccerSessionDebugLogEnableState();" in html
    assert "SOCCER_SESSION_DEBUG_ENABLE_TIMEOUT_MS" in html
    assert "function _hasSoccerSessionDebugLogSendCredentials()" in html
    assert "function _enableSoccerSessionDebugLogAfterRouteStart()" in html
    assert "function _startSoccerSessionDebugLogEnablePromise(workPromise, generation)" in html
    assert "_llm.sessionDebugLogEnableGeneration += 1;" in html
    assert "const isCurrentGeneration = () => _llm.sessionDebugLogEnableGeneration === generation;" in html
    assert "if (!isCurrentGeneration()) return { ok: false, reason: 'stale_enable_result' };" in html
    assert "then((headers) => _enableSoccerDebugLogWithHeaders(reason, headers || {}))" in html
    assert "return _startSoccerSessionDebugLogEnablePromise(_getLocalMutationHeaders()" in html
    assert "enableReason: 'route_start_send_gate'" in html
    assert "reason: 'missing_csrf_token'" in html
    assert "_llm.sessionDebugLogMutationHeaders = debugLogMutationHeaders;" in html
    assert "_llm.sessionDebugLogMutationHeaders = null;" in html
    assert "_postSoccerDebugLogPayload(logPayload, _llm.sessionDebugLogMutationHeaders)" in debug_block
    assert "await enableSoccerSessionDebugLog('auto_route_start')" not in html
    assert "enableSoccerSessionDebugLog('auto_route_start')" not in html
    assert not re.search(r"if\s*\(\s*data\.ok\s*\)\s*{\s*_llm\.sessionDebugLogEnabled\s*=\s*true;", html)
    route_success_block = html.split("if (data.ok)", 1)[1].split("_llm.routeLanlanName", 1)[0]
    assert "_enableSoccerSessionDebugLogAfterRouteStart();" in route_success_block
    assert "enableSoccerSessionDebugLog('keyboard_l')" in html
    assert "session_id: _llm.sessionId" in html
    assert "game_type: 'soccer'" in html
    assert "lanlan_name: _llm.routeLanlanName || ''" in html
    assert "window.nekoLocalMutationSecurity" in debug_block
    assert "peekCachedToken" in debug_block
    assert "getMutationHeaders" in debug_block
    assert "_csrf_token: token" in html


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
    prompt = gr_runtime._build_game_prompt(
        "soccer",
        "Lan",
        "likes soccer",
        language="en",
    )

    assert "Output only the spoken line" in prompt
    assert "你正在和玩家踢一场足球比赛" not in prompt
    assert "======以上为足球游戏会话系统提示======" in prompt


@pytest.mark.unit
def test_game_voice_stt_gate_freezes_route_session_and_restores_mic():
    capture_js = (ROOT / "static" / "app" / "app-audio-capture.js").read_text(encoding="utf-8")

    assert "function getGameVoiceSttRouteSnapshot()" in capture_js
    assert "recognition._gameVoiceRouteSnapshot = routeSnapshot;" in capture_js
    assert "submitGameVoiceSttTranscript(finalText, recognition._gameVoiceRouteSnapshot)" in capture_js
    assert "result.reason === 'session_id_mismatch'" in capture_js
    assert "function restoreOrdinaryMicCaptureAfterGameVoiceSttStop" in capture_js
    assert "restoreOrdinaryMicCaptureAfterGameVoiceSttStop('gate stop')" in capture_js


@pytest.mark.unit
def test_game_voice_route_end_avoids_double_mic_restore():
    websocket_js = (ROOT / "static" / "app" / "app-websocket.js").read_text(encoding="utf-8")

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
