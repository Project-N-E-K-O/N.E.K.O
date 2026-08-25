import json
import re
import shutil
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from config.prompts.prompts_soccer import (
    get_soccer_pregame_context_prompt,
    get_soccer_quick_lines_prompt,
    get_soccer_quick_lines_user_prompt,
    get_soccer_system_prompt,
)
from main_routers.game_router import runtime as gr_runtime
from scripts import check_no_temperature
from tests.node_harness import run_node_stdin


ROOT = Path(__file__).resolve().parents[2]
SOCCER_TEMPLATE_PATH = ROOT / "templates" / "soccer_demo.html"
SOCCER_SCRIPT_PATH = ROOT / "static" / "game" / "games" / "soccer" / "soccer-demo.js"
SOCCER_STYLE_PATH = ROOT / "static" / "game" / "games" / "soccer" / "soccer-demo.css"


@pytest.mark.unit
def test_soccer_template_loads_split_css_and_javascript_assets():
    template = SOCCER_TEMPLATE_PATH.read_text(encoding="utf-8")
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    style = SOCCER_STYLE_PATH.read_text(encoding="utf-8")

    assert '/static/game/games/soccer/soccer-demo.css?v={{ static_asset_version }}' in template
    assert '/static/game/games/soccer/soccer-demo.js?v={{ static_asset_version }}' in template
    assert 'id="soccer-runtime-config" type="application/json"' in template
    assert "<style" not in template
    assert "window.SoccerDemo =" not in template
    assert "window.SoccerDemo =" in script
    assert "#soccer-start-button" in style
    template_head = template.split("</head>", 1)[0]
    runtime_script = '<script src="/static/game/games/soccer/soccer-demo.js'
    assert runtime_script not in template_head
    assert template.index('<script type="importmap">') < template.index(runtime_script)
    assert template.index(runtime_script) < template.index('<script src="/static/vrm/vrm-init.js">')


@pytest.mark.unit
def test_soccer_template_renders_runtime_config_and_asset_version():
    rendered = Environment(loader=FileSystemLoader(ROOT), autoescape=True).get_template(
        "templates/soccer_demo.html"
    ).render(
        vrm_defaults={"ambientIntensity": 1.25},
        static_asset_version="test-version",
    )
    config_match = re.search(
        r'<script id="soccer-runtime-config" type="application/json">(.*?)</script>',
        rendered,
        re.DOTALL,
    )

    assert config_match
    assert json.loads(config_match.group(1))["vrm_defaults"]["ambientIntensity"] == 1.25
    assert "soccer-demo.css?v=test-version" in rendered
    assert "soccer-demo.js?v=test-version" in rendered


@pytest.mark.unit
def test_soccer_first_language_payload_waits_for_character_response():
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    quick_start = script.index("async function loadGeneratedQuickLines()")
    quick_section = script[quick_start:script.index("loadGeneratedQuickLines();", quick_start)]
    route_start = script.index("async function _startGameRoute()")
    route_section = script[route_start:script.index("function _deliverPendingOpeningLine()", route_start)]

    resolver_start = script.index("window.SoccerExplicitConversationLang = function (characterName)")
    resolver_section = script[resolver_start:script.index("const _currentI18nLang", resolver_start)]

    assert "let soccerCharacterLanguagePreferenceResolved = false;" in script
    assert "characterInfo?.language_preference_resolved === true" in script
    assert "soccerCharacterExplicitLanguage = normalizeSoccerExplicitLanguage(characterInfo?.language);" in script
    assert "soccerCharacterLanguageRevision === languageRevision" in script
    assert script.count("soccerCharacterLanguageRevision += 1;") == 4
    assert script.count("soccerCharacterLanguagePreferenceResolved = true;") == 3
    assert "if (!currentCharacterName)" in script
    assert resolver_section.index("if (soccerCharacterLanguagePreferenceResolved)") < resolver_section.index(
        "window.getExplicitConversationLanguagePreference(characterName)"
    )
    assert "if (characterName !== _soccerConversationCharacterName()) return '';" in resolver_section
    assert "if (explicitLanguage) payload.i18n_language = explicitLanguage;" in script
    assert "window.hydrateExplicitConversationLanguagePreference" not in script
    assert quick_section.index("await ensureSoccerCharacterInfo();") < quick_section.index(
        "..._conversationLanguagePayload()"
    )
    assert route_section.index("await ensureSoccerCharacterInfo();") < route_section.index(
        "_gameRoutePayload("
    )
    assert "window.getExplicitConversationLanguagePreference(characterName)" in script
    assert "neko:conversation-language-changed" in script
    assert "neko:conversation-language-cleared" in script


@pytest.mark.unit
def test_soccer_direct_open_language_change_wins_inflight_character_response():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the soccer language race harness")

    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    state_start = script.index("const normalizeSoccerExplicitLanguage")
    state_end = script.index("    (async () => {", state_start)
    listener_start = script.index(
        "window.SoccerExplicitConversationLang = function (characterName)"
    )
    listener_end = script.index("      const canvas =", listener_start)
    behavior_source = script[state_start:state_end] + script[listener_start:listener_end]

    harness = f"""
(async () => {{
  const listeners = {{}}, trustedLanguages = new Map();
  globalThis.window = globalThis;
  window.location = {{ origin: 'http://127.0.0.1', search: '' }};
  window.lanlan_config = {{ lanlan_name: 'soccer_demo' }};
  window.__SoccerResolvedLanlanName = '';
  window.i18next = {{ language: 'en' }};
  window.SoccerCurrentI18nLang = () => 'en';
  window.addEventListener = (name, listener) => {{ listeners[name] = listener; }};
  window.getExplicitConversationLanguagePreference = (name) => trustedLanguages.get(name) || '';

  let releaseCharacterResponse;
  globalThis.fetch = () => new Promise((resolve) => {{
    releaseCharacterResponse = () => resolve({{
      ok: true,
      json: async () => ({{ lanlan_name: 'Mimi', language: 'en', language_preference_resolved: true }}),
    }});
  }});

  eval({json.dumps(behavior_source)} + `
    globalThis.__loadSoccerCharacter = ensureSoccerCharacterInfo;
    globalThis.__soccerLanguagePayload = _conversationLanguagePayload;
    globalThis.__soccerLanguageState = () => ({{
      explicit: soccerCharacterExplicitLanguage,
      resolved: soccerCharacterLanguagePreferenceResolved,
      revision: soccerCharacterLanguageRevision,
      characterName: _soccerConversationCharacterName(),
    }});
  `);

  const pending = globalThis.__loadSoccerCharacter();
  trustedLanguages.set('Mimi', 'ja');
  listeners['neko:conversation-language-changed']({{ detail: {{ character_name: 'Mimi', language: 'ja' }} }});
  const duringRequest = globalThis.__soccerLanguageState();
  if (duringRequest.revision !== 1 || duringRequest.resolved || duringRequest.explicit) {{
    throw new Error('unknown-identity event must only invalidate the response');
  }}

  releaseCharacterResponse();
  await pending;
  const afterResponse = globalThis.__soccerLanguageState();
  const payload = globalThis.__soccerLanguagePayload();
  if (afterResponse.characterName !== 'Mimi' || afterResponse.resolved) {{
    throw new Error('late response must resolve identity without resolving stale language');
  }}
  if (payload.i18n_language !== 'ja') {{
    throw new Error('trusted changed language did not win: ' + JSON.stringify(payload));
  }}
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    result = run_node_stdin(
        node,
        harness,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.unit
def test_game_llm_paths_do_not_send_temperature_kwarg():
    assert check_no_temperature.main([
        "main_routers/game_router",
        "main_logic/omni_offline_client",
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
    script = ROOT.joinpath("static/game/games/soccer/soccer-demo.js").read_text(encoding="utf-8")
    headers_block = script.split("function _getLocalMutationHeaders()", 1)[1].split(
        "function _refreshLocalMutationHeaders()",
        1,
    )[0]
    context_block = script.split("async function _sendRealtimeGameContext(source, items = [])", 1)[1].split(
        "async function _mirrorGameAssistantText",
        1,
    )[0]

    assert "window.nekoLocalMutationSecurity" in headers_block
    assert "getMutationHeaders" in headers_block
    assert "headers['X-CSRF-Token'] = config.autostart_csrf_token;" in headers_block
    assert "cache: 'no-store'" in headers_block
    assert "credentials: 'same-origin'" in context_block
    assert re.search(r"headers,\s+body: bodyJson", context_block)
    assert "await postWithHeaders(await _getLocalMutationHeaders())" in context_block
    assert "errorPayload.error_code === 'csrf_validation_failed'" in context_block
    assert "await postWithHeaders(await _refreshLocalMutationHeaders())" in context_block


@pytest.mark.unit
def test_soccer_script_posts_session_debug_errors():
    script = ROOT.joinpath("static/game/games/soccer/soccer-demo.js").read_text(encoding="utf-8")
    debug_start_anchor = "function _sendSoccerDebugLog(payload)"
    debug_end_anchor = "function soccerSessionDebugLog"
    assert debug_start_anchor in script
    assert debug_end_anchor in script
    debug_block = script.split(debug_start_anchor, 1)[1].split(debug_end_anchor, 1)[0]

    assert "/api/game/logs" in script
    assert "/api/game/logs/enable" in script
    assert "window.SoccerDemoDebugLog = soccerSessionDebugLog" in script
    assert "window.EnableSoccerSessionDebugLog = enableSoccerSessionDebugLog" in script
    assert "window.addEventListener('error'" in script
    assert "window.addEventListener('unhandledrejection'" in script
    assert "console.warn = function soccerDebugConsoleWarn" in script
    assert "console.error = function soccerDebugConsoleError" in script
    assert "sessionDebugLogEnabled: false" in script
    assert "sessionDebugLogEnablePromise: null" in script
    assert "sessionDebugLogEnableGeneration: 0" in script
    assert "sessionDebugLogMutationHeaders: null" in script
    assert "if (!_llm.sessionDebugLogEnabled) return;" in debug_block
    assert "function resetSoccerSessionDebugLogEnableState()" in script
    assert "resetSoccerSessionDebugLogEnableState();" in script
    assert "SOCCER_SESSION_DEBUG_ENABLE_TIMEOUT_MS" in script
    assert "function _hasSoccerSessionDebugLogSendCredentials()" in script
    assert "function _enableSoccerSessionDebugLogAfterRouteStart()" in script
    assert "function _startSoccerSessionDebugLogEnablePromise(workPromise, generation)" in script
    assert "_llm.sessionDebugLogEnableGeneration += 1;" in script
    assert "const isCurrentGeneration = () => _llm.sessionDebugLogEnableGeneration === generation;" in script
    assert "if (!isCurrentGeneration()) return { ok: false, reason: 'stale_enable_result' };" in script
    assert "then((headers) => _enableSoccerDebugLogWithHeaders(reason, headers || {}))" in script
    assert "return _startSoccerSessionDebugLogEnablePromise(_getLocalMutationHeaders()" in script
    assert "enableReason: 'route_start_send_gate'" in script
    assert "reason: 'missing_csrf_token'" in script
    assert "_llm.sessionDebugLogMutationHeaders = debugLogMutationHeaders;" in script
    assert "_llm.sessionDebugLogMutationHeaders = null;" in script
    assert "_postSoccerDebugLogPayload(logPayload, _llm.sessionDebugLogMutationHeaders)" in debug_block
    assert "await enableSoccerSessionDebugLog('auto_route_start')" not in script
    assert "enableSoccerSessionDebugLog('auto_route_start')" not in script
    assert not re.search(r"if\s*\(\s*data\.ok\s*\)\s*{\s*_llm\.sessionDebugLogEnabled\s*=\s*true;", script)
    route_success_block = script.split("if (data.ok)", 1)[1].split("_llm.routeLanlanName", 1)[0]
    assert "_enableSoccerSessionDebugLogAfterRouteStart();" in route_success_block
    assert "enableSoccerSessionDebugLog('keyboard_l')" in script
    assert "session_id: _llm.sessionId" in script
    assert "game_type: 'soccer'" in script
    assert "lanlan_name: _llm.routeLanlanName || ''" in script
    assert "window.nekoLocalMutationSecurity" in debug_block
    assert "peekCachedToken" in debug_block
    assert "getMutationHeaders" in debug_block
    assert "_csrf_token: token" in script


@pytest.mark.unit
def test_soccer_mood_rotation_only_runs_for_pure_game_fallback():
    script = ROOT.joinpath("static/game/games/soccer/soccer-demo.js").read_text(encoding="utf-8")

    assert "function _shouldUsePureGameMoodRotationFallback()" in script
    assert "source === 'fallback' || !!error" in script
    assert "moodRotationFallbackEnabled" in script
    assert "'mood_rotation_policy'" in script
    assert "默认开启 20s 心情轮换" not in script
    assert "if (!moodDebugMode) enableMoodRotation(20);" not in script
    assert "setTimeout(() => SoccerDemo.enableMoodRotation(20), 15000)" in script

    llm_control_block = script.split("if (result.control.mood && SoccerDemo.MOODS.includes(result.control.mood))", 1)[1].split(
        "} else if (result.control.mood)",
        1,
    )[0]
    assert "_llm.moodRotationFallbackEnabled" in llm_control_block


@pytest.mark.unit
def test_soccer_passive_guard_writes_structured_debug_events():
    script = ROOT.joinpath("static/game/games/soccer/soccer-demo.js").read_text(encoding="utf-8")
    router_source = ROOT.joinpath("main_routers/game_router/runtime.py").read_text(encoding="utf-8")

    assert "function _passiveGuardDebugLog(" in script
    assert "'passive_guard'" in script
    assert "'passive_guard_counter'" in script
    assert "'passive_guard_hint'" in script
    assert "'passive_guard_sidecar'" in script
    assert "'passive_guard_modal'" in script
    assert "'passive_guard_teaching'" in script
    assert "'passive_guard_state_change'" in script
    assert "PASSIVE_GUARD_DEBUG_LOG_LIMIT_PER_WINDOW = 80" in script
    assert "passiveGuardSentInWindow" in script
    assert "FALLBACK_DIAGNOSTIC_REPEAT_EVERY = 20" in script
    assert "fallbackStatusState.hitCounts.set(key, hits)" in script
    assert "const isPassiveGuardLog = payload?.category === 'passive_guard'" in script
    assert "startsWith('passive_guard_')" in script
    assert "PASSIVE_GUARD_SIDE_CAR_TIMEOUT_MS = 7000" in script

    set_difficulty_block = script.split("function setDifficultyInternal(name, opts = {})", 1)[1].split(
        "function targetDifficultyForScoreDiff",
        1,
    )[0]
    set_mood_block = script.split("setMood = function(name, opts = {})", 1)[1].split(
        "const __cycleDiffBase",
        1,
    )[0]
    sidecar_block = script.split("async function _requestPassiveGuardSidecar", 1)[1].split(
        "function _handleSidecarAction",
        1,
    )[0]
    exit_prompt_line_block = script.split("async function _requestExitPromptLine", 1)[1].split(
        "async function _prepareExitPrompt",
        1,
    )[0]
    prepare_exit_prompt_block = script.split("async function _prepareExitPrompt", 1)[1].split(
        "async function _requestPassiveGuardSidecar",
        1,
    )[0]
    external_route_input_block = script.split("if (output && output.type === 'game_external_input')", 1)[1].split(
        "if (!output || output.type !== 'game_llm_result')",
        1,
    )[0]
    rest_candidate_block = script.split("if (promptType === 'rest') {", 1)[1].split(
        "const streak = Number(passiveGuard.lv4PlayerGoalStreak",
        1,
    )[0]
    withdrawn_goal_block = script.split("function _handleWithdrawnGoal", 1)[1].split(
        "function _handleOrdinaryGoal",
        1,
    )[0]
    passive_guard_ai_block = router_source.split("async def _run_soccer_passive_guard_ai", 1)[1].split(
        "# ── 路由端点",
        1,
    )[0]
    passive_guard_backend_block = router_source.split('set_call_type("game_passive_guard")', 1)[1].split(
        "async with llm:",
        1,
    )[0]

    assert "'passive_guard_state_change'" in set_difficulty_block
    assert "_clearOrdinaryCandidate('difficulty_left_lv4')" in script
    assert "_clearRestCandidate('difficulty_left_lv4')" in script
    assert "'passive_guard_state_change'" in set_mood_block
    assert "'passive_guard_sidecar'" in sidecar_block
    assert "requestSessionId = _llm.sessionId" in sidecar_block
    assert "requestGeneration = passiveGuard.sidecarGeneration" in sidecar_block
    assert "discard_stale_result" in sidecar_block
    assert "stale_sidecar_error" in sidecar_block
    assert "function _passiveGuardExitPromptCandidateState(promptType, stage, options = {})" in script
    assert "skip_inactive_candidate" in script
    assert "_passiveGuardExitPromptCandidateState(promptType, stage)" in script
    assert "allowPreparedModal = options.allowPreparedModal === true" in script
    assert "_passiveGuardExitPromptCandidateState(promptType, stage, { allowPreparedModal: true })" in (
        prepare_exit_prompt_block
    )
    assert "function _releasePreparedExitPrompt(type)" in script
    assert prepare_exit_prompt_block.index("_llm.cleanedUp || !isGameRuntimeReady()") < prepare_exit_prompt_block.index(
        "_showExitPrompt(type, firstLine"
    )
    assert prepare_exit_prompt_block.index("skip_cleaned_up_before_show") < prepare_exit_prompt_block.index(
        "_showExitPrompt(type, firstLine"
    )
    assert prepare_exit_prompt_block.index("skip_inactive_candidate_before_show") < prepare_exit_prompt_block.index(
        "_showExitPrompt(type, firstLine"
    )
    assert "_llm.cleanedUp ||" in prepare_exit_prompt_block
    assert "!isGameRuntimeReady() ||" in prepare_exit_prompt_block
    assert "_prepareExitPrompt('rest', 'sidecar_prepare_exit_prompt', { stage })" in script
    assert "_prepareExitPrompt('surrender', 'sidecar_prepare_exit_prompt', { stage })" in script
    assert "function _externalGameRouteInputText(output)" in script
    assert "_handlePassiveGuardUserSpeech(" in external_route_input_block
    assert "_externalGameRouteInputText(output)" in external_route_input_block
    assert "_get_game_route_summary_llm_info(lanlan_name)" in passive_guard_ai_block
    assert "_get_game_route_summary_llm_info," in router_source
    assert "rest_streak_below_stage" in script
    assert "ordinary_candidate_below_stage" in script
    assert "passiveGuard.sidecarGeneration = Number(passiveGuard.sidecarGeneration || 0) + 1" in script
    assert "reason: 'surrender_reminder_disabled'" in script
    assert "reason: 'surrender_reminder_disabled'" in rest_candidate_block
    assert withdrawn_goal_block.index("if (!passiveGuard.surrenderReminderEnabled)") < withdrawn_goal_block.index(
        "passiveGuard.withdrawnRestGoalStreak++"
    )
    assert withdrawn_goal_block.index("if (!passiveGuard.surrenderReminderEnabled)") < withdrawn_goal_block.index(
        "passiveGuard.restLightHintSent = true"
    )
    assert withdrawn_goal_block.index("if (!passiveGuard.surrenderReminderEnabled)") < withdrawn_goal_block.index(
        "passiveGuard.restSidecar7Called = true"
    )
    assert withdrawn_goal_block.index("if (!passiveGuard.surrenderReminderEnabled)") < withdrawn_goal_block.index(
        "passiveGuard.restSidecar8Called = true"
    )
    assert "new AbortController()" in exit_prompt_line_block
    assert "signal: controller.signal" in exit_prompt_line_block
    assert "clearTimeout(timeoutId)" in exit_prompt_line_block
    assert 'provider_type=char_info.get("provider_type")' in passive_guard_backend_block
    assert 'elif action != "prepare_exit_prompt"' not in router_source
    assert 'f"暂不支持 {game_type} 的 PassiveGuard"' in router_source
    assert '"recommendedAction": "observe_more"' in router_source


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
    """The omni_realtime_client package must not carry game-route-specific
    APIs after Phase 1 of the dialog-passthrough refactor — that logic
    belongs in main_routers/game_router.py + main_logic/core.py
    (mirror_*) + main_logic/cross_server.py (mirror_meta detection)."""
    realtime_package = ROOT / "main_logic" / "omni_realtime_client"
    realtime_py = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(realtime_package.glob("*.py"))
    )

    assert "set_game_route_stt_only" not in realtime_py
    assert "_game_route_stt_only" not in realtime_py
    assert "qwen_manual_commit" not in realtime_py
    assert "_active_instructions" not in realtime_py
    assert "_can_forward_model_output" not in realtime_py
    assert "_qwen_server_vad_turn_detection_config" not in realtime_py
    assert "_openai_audio_input_config" not in realtime_py
