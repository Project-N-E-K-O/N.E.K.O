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
MINIGAME_SDK_PATH = ROOT / "static" / "game" / "sdk" / "neko-minigame-sdk.js"
MINIGAME_SDK_TYPES_PATH = ROOT / "static" / "game" / "sdk" / "neko-minigame-sdk.d.ts"
MINIGAME_AUDIO_HOST_PATH = ROOT / "static" / "game" / "sdk" / "neko-minigame-audio-host.js"
MINIGAME_AVATAR_HOST_PATH = ROOT / "static" / "game" / "sdk" / "neko-minigame-avatar-host.js"
MINIGAME_SAME_ORIGIN_HOST_PATH = ROOT / "static" / "game" / "sdk" / "neko-minigame-same-origin-host.js"
SOCCER_ADAPTER_PATH = ROOT / "static" / "game" / "games" / "soccer" / "soccer-neko-adapter.js"
SOCCER_AVATAR_HOST_PATH = ROOT / "static" / "game" / "games" / "soccer" / "soccer-avatar-host.js"
SOCCER_SCRIPT_PATH = ROOT / "static" / "game" / "games" / "soccer" / "soccer-demo.js"
SOCCER_STYLE_PATH = ROOT / "static" / "game" / "games" / "soccer" / "soccer-demo.css"
LIVE2D_CORE_PATH = ROOT / "static" / "live2d" / "live2d-core.js"
VRM_CORE_PATH = ROOT / "static" / "vrm" / "vrm-core.js"


def _soccer_host_and_adapter_source() -> str:
    return "\n".join(
        (
            MINIGAME_SAME_ORIGIN_HOST_PATH.read_text(encoding="utf-8"),
            SOCCER_ADAPTER_PATH.read_text(encoding="utf-8"),
        )
    )


@pytest.mark.unit
def test_minigame_audio_controller_types_cover_the_runtime_surface():
    declarations = MINIGAME_SDK_TYPES_PATH.read_text(encoding="utf-8")

    for declaration in (
        "readonly config: Readonly<AudioMountConfiguration>;",
        "getCurrentBgmSrc(): string;",
        "isCurrentBgm(value: JsonValue): boolean;",
        "onError(handler: (error: Readonly<Record<string, unknown>>) => void): () => void;",
    ):
        assert declaration in declarations


@pytest.mark.unit
def test_soccer_template_loads_split_css_and_javascript_assets():
    template = SOCCER_TEMPLATE_PATH.read_text(encoding="utf-8")
    sdk = MINIGAME_SDK_PATH.read_text(encoding="utf-8")
    host = MINIGAME_SAME_ORIGIN_HOST_PATH.read_text(encoding="utf-8")
    adapter = _soccer_host_and_adapter_source()
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    style = SOCCER_STYLE_PATH.read_text(encoding="utf-8")

    assert '/static/game/games/soccer/soccer-demo.css?v={{ static_asset_version }}' in template
    assert '/static/game/games/soccer/soccer-neko-adapter.js?v={{ static_asset_version }}' in template
    assert '/static/game/sdk/neko-minigame-sdk.js?v={{ static_asset_version }}' in template
    assert '/static/game/sdk/neko-minigame-audio-host.js?v={{ static_asset_version }}' in template
    assert '/static/game/sdk/neko-minigame-avatar-host.js?v={{ static_asset_version }}' in template
    assert 'id="neko-minigame-host-launch" type="application/json"' in template
    assert '/static/game/games/soccer/soccer-neko-host-registration.js?v={{ static_asset_version }}' in template
    assert '/static/game/sdk/neko-minigame-same-origin-bootstrap.js?v={{ static_asset_version }}' in template
    assert '/static/game/games/soccer/soccer-avatar-host.js?v={{ static_asset_version }}' in template
    assert '/static/game/games/soccer/soccer-demo.js?v={{ static_asset_version }}' in template
    assert template.index("neko-minigame-sdk.js") < template.index("soccer-neko-adapter.js")
    assert template.index("neko-minigame-sdk.js") < template.index("neko-minigame-audio-host.js")
    assert template.index("neko-minigame-audio-host.js") < template.index("soccer-neko-adapter.js")
    assert template.index("neko-minigame-sdk.js") < template.index("neko-minigame-avatar-host.js")
    assert template.index("neko-minigame-avatar-host.js") < template.index("soccer-neko-adapter.js")
    assert template.index("neko-minigame-avatar-host.js") < template.index("soccer-avatar-host.js")
    assert template.index("soccer-avatar-host.js") < template.index("soccer-neko-adapter.js")
    assert template.index("soccer-neko-host-registration.js") < template.index("neko-minigame-same-origin-bootstrap.js")
    assert template.index("neko-minigame-same-origin-bootstrap.js") < template.index("soccer-neko-adapter.js")
    assert template.index("soccer-neko-adapter.js") < template.index("soccer-demo.js")
    assert 'id="soccer-runtime-config" type="application/json"' in template
    assert "<style" not in template
    assert "window.SoccerDemo =" not in template
    assert "window.SoccerDemo =" in script
    assert "window.createSoccerNekoAdapter" in adapter
    assert "const FACTORY_PROPERTY = 'createNekoMiniGameSameOriginHost'" in host
    assert "Object.defineProperty(window, FACTORY_PROPERTY" in host
    assert "global.NekoMiniGame" in sdk
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
    assert "neko-minigame-sdk.js?v=test-version" in rendered
    assert "soccer-neko-adapter.js?v=test-version" in rendered
    assert "soccer-demo.js?v=test-version" in rendered


@pytest.mark.unit
def test_soccer_avatar_rendering_uses_sdk_fixed_viewport_contract():
    sdk = MINIGAME_SDK_PATH.read_text(encoding="utf-8")
    avatar_host = MINIGAME_AVATAR_HOST_PATH.read_text(encoding="utf-8")
    soccer_avatar_host = SOCCER_AVATAR_HOST_PATH.read_text(encoding="utf-8")
    adapter = _soccer_host_and_adapter_source()
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    live2d_core = LIVE2D_CORE_PATH.read_text(encoding="utf-8")
    vrm_core = VRM_CORE_PATH.read_text(encoding="utf-8")

    assert "'avatar-renderer'" in sdk
    assert "const MAX_AVATAR_RENDERERS = 8;" in sdk
    assert "async mount(configInput)" in sdk
    assert "disposeAvatarController(controllerState)" in sdk
    assert "global.NekoMiniGameAvatarHost" in avatar_host
    assert "ResizeObserverImpl" in avatar_host
    assert "fitLive2DModel" in avatar_host
    assert "async mountAvatar(config)" in adapter
    # The host adapter runtime suite verifies injected Avatar ownership and
    # exactly-once disposal; SDK private storage/helper names are not contracts.

    assert "optionalCapabilities: ['dialogue', 'quick-lines', 'voice-input', 'avatar-renderer', 'storage']" in script
    assert "viewport: Object.freeze({ mode: 'fixed', width: 200, height: 300 })" in script
    assert "align: 'bottom-center'" in script
    assert "resize: Object.freeze({ mode: 'fixed' })" in script
    assert script.count("soccerGame.avatar.mount(") >= 4
    assert "window.__SoccerAiAvatarController?.focus?." in script
    assert "window.__SoccerAiAvatarController?.setEmotion?." in script
    assert "window.NekoMiniGameAvatarHost.create({" in soccer_avatar_host
    assert "window.createSoccerAvatarHost({" in script
    assert "window.createSoccerAvatarHost =" in soccer_avatar_host
    assert "let avatarEventEmitter = null;" in script
    assert "avatarEventEmitter = emitEvent;" in script
    assert "if (window.__SoccerPlayerAvatarController\n            && !window.__SoccerPlayerAvatarController.disposed)" in script
    assert "if (window.__SoccerAiAvatarController\n            && !window.__SoccerAiAvatarController.disposed)" in script
    assert "soccerAvatarControllers" not in script
    assert "requireSoccerAvatarLayout" not in script
    assert "new VRMManager()" not in script
    assert "live2dManager.initPIXI" not in script

    fixed_branch = live2d_core.index("if (resizeMode === 'fixed')")
    resize_listener = live2d_core.index("window.addEventListener('resize', this._screenChangeHandler)")
    assert fixed_branch < resize_listener
    assert "delete pixiOptions.resizeMode;" in live2d_core
    vrm_fixed_branch = vrm_core.index("if (resizeMode === 'fixed') return;")
    vrm_resize_listener = vrm_core.index("window.addEventListener('resize', this.manager._resizeHandler)")
    assert vrm_fixed_branch < vrm_resize_listener
    assert "resizeMode: 'fixed'" in soccer_avatar_host


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
  const soccerHost = {{
    getCharacter: () => new Promise((resolve) => {{
      releaseCharacterResponse = () => resolve({{
        ok: true,
        json: async () => ({{ lanlan_name: 'Mimi', language: 'en', language_preference_resolved: true }}),
      }});
    }}),
  }};

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
def test_soccer_removes_disabled_realtime_context_bypass():
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    adapter = _soccer_host_and_adapter_source()

    assert "this._window.nekoLocalMutationSecurity" in adapter
    assert "getMutationHeaders()" in adapter
    assert "refreshMutationHeaders()" in adapter
    assert "headers['X-CSRF-Token'] = config.autostart_csrf_token;" in adapter
    assert "this.getPageConfig(lanlanName)" in adapter
    assert "cache: 'no-store'" in adapter
    assert "credentials: 'same-origin'" in adapter
    assert "response.clone().json()" in adapter
    assert "errorPayload?.error_code !== 'csrf_validation_failed'" in adapter
    assert "requestWithHeaders(await this.refreshMutationHeaders())" in adapter
    assert "function _getLocalMutationHeaders()" not in script
    assert "function _refreshLocalMutationHeaders()" not in script
    assert "_sendRealtimeGameContext" not in script
    assert "soccerHost.sendRealtimeContextWithCsrf" not in script


@pytest.mark.unit
def test_soccer_script_posts_session_debug_errors():
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    adapter = _soccer_host_and_adapter_source()

    assert "/api/game/logs" in adapter
    assert "/api/game/logs/enable" in adapter
    assert "window.SoccerDemoDebugLog = soccerSessionDebugLog" in script
    assert "window.EnableSoccerSessionDebugLog = enableSoccerSessionDebugLog" in script
    assert "soccerGame.logger.configure({" in script
    assert "soccerGame.logger.log(" in script
    assert "soccerGame.logger.enable(reason)" in script
    assert "soccerGame.logger.enableAfterRuntimeStart()" in script
    assert "soccerGame.logger.reset()" in script
    assert "enabled: false" in adapter
    assert "enablePromise: null" in adapter
    assert "enableGeneration: 0" in adapter
    assert "mutationHeaders: null" in adapter
    assert "if (!logger.enabled) return;" in adapter
    assert "function resetSoccerSessionDebugLogEnableState()" in script
    assert "resetSoccerSessionDebugLogEnableState();" in script
    assert "SOCCER_SESSION_DEBUG_ENABLE_TIMEOUT_MS" in script
    assert "function _enableSoccerSessionDebugLogAfterRouteStart()" in script
    assert "_startLoggerEnablePromise(workPromise, generation)" in adapter
    assert "logger.enableGeneration += 1;" in adapter
    assert "const isCurrentGeneration = () => logger.enableGeneration === generation;" in adapter
    assert "if (!isCurrentGeneration()) return { ok: false, reason: 'stale_enable_result' };" in adapter
    assert "this.getMutationHeaders()" in adapter
    assert "enableReason: 'route_start_send_gate'" in adapter
    assert "reason: 'missing_csrf_token'" in adapter
    assert "logger.mutationHeaders = debugLogMutationHeaders" in adapter
    assert "logger.mutationHeaders = null;" in adapter
    assert "await enableSoccerSessionDebugLog('auto_route_start')" not in script
    assert "enableSoccerSessionDebugLog('auto_route_start')" not in script
    route_success_block = script.split("if (data.ok)", 1)[1].split("console.log('[SoccerRoute]", 1)[0]
    assert "await _enableSoccerSessionDebugLogAfterRouteStart();" in route_success_block
    assert "_runtimeCharacterName()" in route_success_block
    assert "transport.applyRuntimeState(routeState)" in MINIGAME_SDK_PATH.read_text(encoding="utf-8")
    assert "enableSoccerSessionDebugLog('keyboard_l')" in script
    assert "session_id: context.sessionId" in adapter
    assert "gameType: 'soccer'" in SOCCER_ADAPTER_PATH.read_text(encoding="utf-8")
    assert "lanlan_name: context.lanlanName" in adapter
    assert "this._window.nekoLocalMutationSecurity" in adapter
    assert "peekCachedToken" in adapter
    assert "getMutationHeaders" in adapter
    assert "this.postLog(payload" in adapter
    assert "this.enableLog(payload, mutationHeaders)" in adapter
    assert "_csrf_token: token" in adapter
    assert "sensitive_possible: !!sensitivePossible" in adapter
    assert "DEFAULT_LOG_QUEUE_LIMIT = 256" in adapter
    assert "this._logTransport = {" in adapter
    assert "transport.queue.length + transport.inFlight.size >= transport.queueLimit" in adapter
    assert "transport.inFlight.size < transport.concurrency" in adapter
    assert "event: 'log_queue_overflow'" in adapter
    assert "'repeated_log_summary'" in adapter
    assert "'repeated_log_recovered'" in adapter
    assert "flush: this.flushLogger.bind(this)" in adapter
    assert "DEFAULT_LOG_LIMIT_PER_WINDOW" not in adapter
    assert "DEFAULT_PASSIVE_GUARD_LOG_LIMIT_PER_WINDOW" not in adapter
    assert "addEventListener('error'" in adapter
    assert "addEventListener('unhandledrejection'" in adapter
    assert "removeEventListener('error'" in adapter
    assert "removeEventListener('unhandledrejection'" in adapter
    assert "this._console.warn = captureRegistry.originalWarn" in adapter
    assert "this._console.error = captureRegistry.originalError" in adapter
    assert "enableTimeoutId" in adapter
    assert "_cancelLoggerEnableTimeout" in adapter
    assert "get sessionId()" in adapter
    assert "get routeLanlanName()" in adapter
    assert "resetSession({ newSession = false } = {})" in adapter
    assert "sessionId: _llm.sessionId" not in script
    assert "_llm.routeLanlanName" not in script


@pytest.mark.unit
def test_soccer_speech_playback_bridge_is_owned_and_disposed_by_host_adapter():
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    adapter = _soccer_host_and_adapter_source()

    assert "soccerGame.speech.onState((data) =>" in script
    assert "soccerGame.speech.onError((error) =>" in script
    assert "soccerGame.speech.getState()" in script
    assert "soccerHost.startSpeechPlaybackBridge({" not in script
    assert "new BroadcastChannel(" not in script
    assert "event.key !== SPEECH_PLAYBACK_STATE_KEY" not in script
    assert "window.addEventListener('neko-speech-playback-state'" not in script
    assert "startSpeechPlaybackBridge(options = {})" in adapter
    assert "stopSpeechPlaybackBridge()" in adapter
    assert "requestSpeechOutput(payload, options = {})" in adapter
    assert "startSpeechOutputBridge(options = {})" in adapter
    assert "stopSpeechOutputBridge()" in adapter
    assert "this._window.removeEventListener('storage', bridge.storageHandler)" in adapter
    assert "this._window.removeEventListener(bridge.windowEventName, bridge.windowEventHandler)" in adapter
    assert "bridge.channel.close()" in adapter
    dispose_block = adapter.split("dispose(options = {})", 1)[1]
    assert "this.stopSpeechPlaybackBridge();" in dispose_block


@pytest.mark.unit
def test_soccer_requests_use_adapter_and_runtime_lifecycle_is_owned_by_sdk():
    template = SOCCER_TEMPLATE_PATH.read_text(encoding="utf-8")
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    adapter = _soccer_host_and_adapter_source()
    sdk = MINIGAME_SDK_PATH.read_text(encoding="utf-8")

    assert template.index("soccer-neko-adapter.js") < template.index("soccer-demo.js")
    assert template.index("neko-minigame-sdk.js") < template.index("soccer-neko-adapter.js")
    assert "const soccerHost = await window.createSoccerNekoAdapter" in script
    assert "fetch(" not in script
    assert "navigator.sendBeacon" not in script
    assert "soccerGame.runtime.configure({" in script
    assert "soccerGame.runtime.pulse(force)" in script
    assert "soccerGame.events.on('runtime-inactive'" in script
    assert "soccerGame.events.on('runtime-error'" in script
    assert "soccerGame.events.on('runtime-output'" in script
    assert "startRuntimeMonitoring({ heartbeat = true, outputs = true } = {})" in sdk
    assert "documentImpl?.addEventListener?.('visibilitychange', visibilityHandler)" in sdk
    assert "stopRuntimeMonitoring();" in sdk
    assert "transport.heartbeat(" in sdk
    assert "transport.drain(" in sdk
    assert "startHeartbeat(" not in adapter
    assert "startDrain(" not in adapter
    assert "pageExit: {" in script
    assert "soccerGame.events.on('page-exit'" in script
    assert "client.dispose({ preserveRuntimeEnd: true });" in sdk
    assert "window.addEventListener('pagehide'" not in script

    for path in (
        "character",
        "quick-lines",
        "chat",
        "passive-guard",
        "route/start",
        "route/heartbeat",
        "route/drain",
        "route/voice-transcript",
        "realtime-context",
        "mirror-assistant",
        "speak",
        "end",
    ):
        assert f"'{path}'" in adapter

    assert "addEventListener?.('visibilitychange', visibilityHandler)" in sdk
    assert "removeEventListener?.('visibilitychange', visibilityHandler)" in sdk
    assert "clearInterval?.(heartbeatLifecycle.timer)" in sdk
    assert "heartbeatLifecycle.controller.abort()" in sdk
    assert "clearInterval?.(outputLifecycle.timer)" in sdk


@pytest.mark.unit
def test_soccer_uses_sdk_voice_and_speech_facades_instead_of_host_bypasses():
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    adapter = _soccer_host_and_adapter_source()

    assert "class NekoMiniGameHostError extends Error" in adapter
    assert "DEFAULT_PENDING_REQUEST_LIMIT = 64" in adapter
    assert "this._pendingRequests = new Map()" in adapter
    assert "this._pendingRequests.size >= this._pendingRequestLimit" in adapter
    assert "if (preserveOperations.has(entry.operation)) continue;" in adapter
    assert "externalSignal.addEventListener('abort'" in adapter
    assert "externalSignal.removeEventListener?.('abort'" in adapter
    assert "this._pendingRequests.delete(requestId)" in adapter
    assert "cancelPendingRequests(reason = 'cancelled', options = {})" in adapter
    assert "this.cancelPendingRequests('disposed', { preserveOperations });" in adapter
    assert "code === 'timeout'" in adapter
    assert "code === 'disposed'" in adapter

    assert "soccerGame.voice.start()" in script
    assert "soccerGame.voice.stop()" in script
    assert "soccerGame.voice.onTranscript" in script
    assert "soccerGame.speech.mirror({" in script
    assert "soccerGame.dialogue.quickLines({" in script
    assert "soccerHost.startSpeechRecognition" not in script
    assert "soccerHost.stopSpeechRecognition" not in script
    assert "soccerHost.isSpeechRecognitionSupported" not in script
    assert "soccerHost.submitVoiceTranscript" not in script
    assert "soccerHost.mirrorAssistant" not in script
    assert "soccerHost.getQuickLines" not in script
    direct_host_calls = set(re.findall(r"soccerHost\.([A-Za-z0-9_]+)", script))
    # Soccer-only legacy preference migration is deliberately outside the
    # public SDK; ordinary reads/writes still use client.storage.
    assert direct_host_calls == {"getCharacter", "evaluatePassiveGuard", "migrateLegacySettings"}
    assert "'storage'" in script.split("optionalCapabilities:", 1)[1].split("]", 1)[0]
    assert "soccerGame.storage.get(SOCCER_VOICE_MIX_STORAGE_KEY)" in script
    assert "soccerGame.storage.set(SOCCER_VOICE_MIX_STORAGE_KEY" in script
    assert "soccerGame.storage.get(SURRENDER_REMINDER_STORAGE_KEY)" in script
    assert "soccerGame.storage.set(SURRENDER_REMINDER_STORAGE_KEY" in script
    assert "neko.soccerGameAudio.voiceMix" not in script
    assert "neko.soccer.surrenderReminderEnabled" not in script
    assert "new SpeechRecognition()" not in script
    assert "new BrowserSpeechRecognition()" not in script


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
def test_soccer_voice_mix_uses_50_percent_as_neutral_without_mutating_global_volume():
    template = ROOT.joinpath("templates/soccer_demo.html").read_text(encoding="utf-8")
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    playback = ROOT.joinpath("static/app/app-audio-playback.js").read_text(encoding="utf-8")
    websocket = ROOT.joinpath("static/app/app-websocket.js").read_text(encoding="utf-8")

    assert 'id="game-voice-volume"' in template
    assert 'value="50"' in template
    assert "DEFAULT_SOCCER_VOICE_MIX_PERCENT = 50" in script
    assert "return soccerVoiceMixPercent / DEFAULT_SOCCER_VOICE_MIX_PERCENT" in script
    assert "relativeGain: _soccerVoicePlaybackGain()" in script
    assert "setSpeakerVolume(" not in script
    assert "playbackGain: playbackGain" in websocket
    assert "createDynamicsCompressor()" in playback
    assert "releaseAssistantPlaybackGraph(source)" in playback


@pytest.mark.unit
def test_soccer_audio_channel_labels_toggle_mute_and_voice_warns_about_in_flight_audio():
    template = SOCCER_TEMPLATE_PATH.read_text(encoding="utf-8")
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    style = SOCCER_STYLE_PATH.read_text(encoding="utf-8")

    for channel in ("bgm", "sfx", "voice"):
        assert f'id="game-{channel}-mute"' in template
        assert f"{channel}MuteButton?.addEventListener('click'" in script
    assert 'aria-pressed="false"' in template
    assert 'data-i18n-title="soccer.debugControls.voiceVolumeHint"' in template
    assert "无法调整正在播放的语音音量" in template
    assert "lastNonZeroBgmVolume" in script
    assert "lastNonZeroSfxVolume" in script
    assert "lastNonZeroVoiceMixPercent" in script
    assert "_syncChannelMuteButton" in script
    assert '.game-audio-channel-toggle[aria-pressed="true"]' in style
    assert ":focus-visible" in style

    for locale in ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"):
        data = json.loads(ROOT.joinpath(f"static/locales/{locale}.json").read_text(encoding="utf-8"))
        hint = data["soccer"]["debugControls"]["voiceVolumeHint"]
        assert isinstance(hint, str) and hint.strip()
    zh_hint = json.loads(
        ROOT.joinpath("static/locales/zh-CN.json").read_text(encoding="utf-8")
    )["soccer"]["debugControls"]["voiceVolumeHint"]
    assert zh_hint == "点击可静音或恢复。无法调整正在播放的语音音量。"


@pytest.mark.unit
def test_soccer_settings_panel_remains_available_before_kickoff():
    template = SOCCER_TEMPLATE_PATH.read_text(encoding="utf-8")
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    style = SOCCER_STYLE_PATH.read_text(encoding="utf-8")

    assert '<div id="game-top-controls">' in template
    assert 'id="top-voice-control-slot"' in template
    assert 'id="settings-voice-control-slot"' in template
    assert template.count('id="game-voice-chat-control"') == 1
    assert 'id="soccer-settings-button"' in template
    assert 'aria-controls="controls"' in template
    assert 'aria-expanded="false"' in template
    assert '<div id="controls" hidden' in template
    assert 'id="exit-to-start-button"' in template
    assert template.index('id="game-top-controls"') < template.index('id="soccer-settings-button"')
    assert template.index('id="top-voice-control-slot"') < template.index('id="soccer-settings-button"')
    assert template.index('id="soccer-settings-button"') < template.index('id="exit-to-start-button"')
    assert template.index('id="controls"') < template.index('id="surrender-reminder-control"')
    assert template.index('id="surrender-reminder-control"') < template.index('id="game-top-controls"')
    assert template.index('id="surrender-reminder-toggle"') < template.index('id="boundary-toggle"')
    assert template.index('id="boundary-toggle"') < template.index('id="voice-output-toggle"')
    assert template.count('class="settings-checkbox-row"') == 3
    assert template.index('id="loading-actions"') < template.index('id="soccer-start-button"')
    assert template.index('id="soccer-start-button"') < template.index('id="game-memory-option"')
    assert template.index('id="game-memory-option"') < template.index('id="game-memory-hint"')

    controls_rule = re.search(r"#controls\s*\{(?P<body>.*?)\}", style, re.DOTALL)
    overlay_rule = re.search(r"#loading-overlay\s*\{(?P<body>.*?)\}", style, re.DOTALL)
    assert controls_rule is not None
    assert overlay_rule is not None
    controls_z = int(re.search(r"z-index:\s*(\d+)", controls_rule.group("body")).group(1))
    overlay_z = int(re.search(r"z-index:\s*(\d+)", overlay_rule.group("body")).group(1))
    assert controls_z > overlay_z
    assert "pointer-events: auto" in controls_rule.group("body")
    assert "#controls[hidden]" in style
    assert "#soccer-settings-button" in style
    assert "#top-voice-control-slot #game-voice-chat-copy { display: none; }" in style
    assert "#controls .settings-checkbox-row" in style
    assert "grid-template-columns: 16px minmax(0, 1fr)" in style
    assert "#controls .settings-checkbox-row input[type=\"checkbox\"]" in style
    assert "@media (prefers-reduced-motion: reduce)" in style
    assert "const settingsButton = document.getElementById('soccer-settings-button')" in script
    assert "const settingsPanel = document.getElementById('controls')" in script
    assert "function syncGameVoiceControlPlacement(settingsOpen)" in script
    assert "targetSlot.appendChild(gameVoiceChatControl)" in script
    assert "function setSettingsPanelOpen(open" in script
    assert "settingsButton.setAttribute('aria-expanded'" in script
    assert "syncGameVoiceControlPlacement(next)" in script
    assert "if (e.key === 'Escape')" in script
    assert "settingsUiAbortController.abort()" in script
    assert "button.hidden = !visible" in script
    assert "const debugMoodVisible = soccerTestEnabled ||" in script
    for locale in ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"):
        soccer = json.loads(ROOT.joinpath(f"static/locales/{locale}.json").read_text(encoding="utf-8"))["soccer"]
        assert set(soccer["settings"]) == {"button", "title", "game", "audio", "debug"}
        assert all(str(value).strip() for value in soccer["settings"].values())

    difficulty_block = script.split("function setDifficultyInternal(name, opts = {})", 1)[1].split(
        "function targetDifficultyForScoreDiff",
        1,
    )[0]
    assert "soccerTestEnabled" in difficulty_block
    assert "!_llm.gameStarted" in difficulty_block
    assert "startScreenDifficultyOverridden = true" in difficulty_block
    assert difficulty_block.index("startScreenDifficultyOverridden = true") < difficulty_block.index(
        "if (i === difficultyIdx) return false"
    )

    pregame_block = script.split("function _applyPreGameContext(routeState = {})", 1)[1].split(
        "function _schedulePreGameContextRefresh",
        1,
    )[0]
    assert "!startScreenDifficultyOverridden" in pregame_block
    reset_start = script.find("function _resetGameFieldForStartScreen()")
    assert reset_start != -1, "start-screen reset function is missing"
    reset_end = script.find("function _resetGameRouteRuntime(", reset_start)
    assert reset_end > reset_start, "route reset boundary after start-screen reset is missing"
    reset_block = script[reset_start:reset_end]
    assert "startScreenDifficultyOverridden = false" in reset_block


@pytest.mark.unit
def test_soccer_player_voice_bubble_subscribes_to_sdk_transcripts_with_bounded_dedupe():
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")

    voice_control_block = script.split("function _initGameVoiceChatControl()", 1)[1].split(
        "const _refreshGameVoiceChatLocale",
        1,
    )[0]
    assert "soccerGame.voice.onTranscript((transcript) =>" in voice_control_block
    assert "showPlayerTranscriptBubble(transcript)" in voice_control_block
    assert "let lastPlayerTranscriptKey = ''" in script
    assert "let lastPlayerTranscriptAt = 0" in script
    assert "function showPlayerTranscriptBubble(transcript" in script
    assert "transcript?.requestId" in script
    assert "key === lastPlayerTranscriptKey && (requestId ||" in script
    assert "showPlayerTranscriptBubble({" in script
    assert "new Set" not in script.split("function showPlayerTranscriptBubble(transcript", 1)[1].split(
        "function clearPlayerSpeechBubble",
        1,
    )[0]


@pytest.mark.unit
def test_soccer_voice_chat_uses_official_host_microphone_bridge():
    template = SOCCER_TEMPLATE_PATH.read_text(encoding="utf-8")
    index_template = ROOT.joinpath("templates/index.html").read_text(encoding="utf-8")
    chat_template = ROOT.joinpath("templates/chat.html").read_text(encoding="utf-8")
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    sdk = MINIGAME_SDK_PATH.read_text(encoding="utf-8")
    adapter = _soccer_host_and_adapter_source()
    host_bridge = ROOT.joinpath("static/app/app-game-voice-control.js").read_text(encoding="utf-8")

    assert 'id="game-voice-chat-button"' in template
    assert '/static/icons/mic_icon_off.png' in template
    assert '/static/icons/mic_icon_on.png' in template
    assert 'aria-pressed="false"' in template
    assert 'aria-describedby="game-voice-chat-status"' in template
    assert 'role="status" aria-live="polite"' in template

    assert "window.NekoMiniGame.connect({" in script
    assert "requiredCapabilities: ['runtime', 'logging', 'audio', 'speech-output', 'memory', 'context-read']" in script
    assert "soccerGame.runtime.start(" in script
    assert "soccerGame.runtime.end(" in script
    assert "soccerGame.session" not in script
    assert "soccerGame.voice.onState" in script
    assert "soccerGame.voice.toggle()" in script
    assert "game_voice_control_toggle" in script
    assert "new BroadcastChannel(" not in script
    assert "document.getElementById('micButton')" not in script

    assert "startVoiceControlBridge(options = {})" in adapter
    assert "requestVoiceControl(action = 'query', options = {})" in adapter
    assert "DEFAULT_VOICE_CONTROL_PENDING_LIMIT = 4" in adapter
    assert "bridge.pending.size >= bridge.pendingLimit" in adapter
    assert "this.stopVoiceControlBridge('disposed');" in adapter
    assert "bridge.pending.clear()" in adapter

    assert "'voice-input'" in sdk
    assert "MANDATORY_CAPABILITIES = Object.freeze(['logging'])" in sdk
    assert "onTranscript(handler)" in sdk
    assert "MAX_LISTENERS_PER_EVENT = 32" in sdk
    assert "transport.startVoiceControlBridge" in sdk

    assert "await soccerGame.speech.speak({" in script
    assert "soccerHost.speak(" not in script
    assert "'speech-output'" in sdk
    assert "const speech = Object.freeze({" in sdk
    assert "MAX_SPEECH_PENDING_REQUESTS = 4" in sdk
    assert "MAX_SPEECH_REQUEST_METADATA = 64" in sdk
    assert "transport.requestSpeechOutput" in sdk
    assert "transport.startSpeechOutputBridge" in sdk

    audio_host = MINIGAME_AUDIO_HOST_PATH.read_text(encoding="utf-8")
    assert "await (async () => {" in script
    assert "await soccerGame.audio.mount({" in script
    assert "new gameSystem.GameAudioSystem" not in script
    assert "mountAudio(config)" in adapter
    assert "'audio'" in sdk
    assert "const audio = Object.freeze({" in sdk
    assert "NekoMiniGameAudioHost" in audio_host
    assert "maxPreloadEntries" in audio_host
    assert "controller.dispose()" in audio_host

    assert "document.getElementById('micButton')" in host_bridge
    assert "micButton.click()" in host_bridge
    assert "window.stopMicCapture" in host_bridge
    assert "routeMatches(request)" in host_bridge
    assert "setInterval(function () { broadcastState({}, false); }, STATE_POLL_INTERVAL_MS)" in host_bridge
    assert "clearInterval(stateTimer)" in host_bridge
    assert "channel.close()" in host_bridge
    assert "type: 'game_voice_transcript'" in host_bridge
    assert "neko:user-voice-content-received" in host_bridge
    assert "/static/app/app-game-voice-control.js" in index_template
    assert index_template.index("/static/app/app-buttons.js") < index_template.index(
        "/static/app/app-game-voice-control.js"
    )
    assert "/static/app/app-game-voice-control.js" not in chat_template

    required_keys = {
        "label", "connecting", "unavailable", "idle", "starting", "stopping",
        "active", "muted", "failed", "start", "stop",
    }
    for locale in ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"):
        data = json.loads(ROOT.joinpath(f"static/locales/{locale}.json").read_text(encoding="utf-8"))
        assert required_keys <= set(data["soccer"]["voiceChat"])


@pytest.mark.unit
def test_soccer_passive_guard_writes_structured_debug_events():
    script = SOCCER_SCRIPT_PATH.read_text(encoding="utf-8")
    adapter = _soccer_host_and_adapter_source()
    router_source = ROOT.joinpath("main_routers/game_router/runtime.py").read_text(encoding="utf-8")

    assert "function _passiveGuardDebugLog(" in script
    assert "'passive_guard'" in script
    assert "'passive_guard_counter'" in script
    assert "'passive_guard_hint'" in script
    assert "'passive_guard_sidecar'" in script
    assert "'passive_guard_modal'" in script
    assert "'passive_guard_teaching'" in script
    assert "'passive_guard_state_change'" in script
    assert "PASSIVE_GUARD_DEBUG_LOG_LIMIT_PER_WINDOW" not in script
    assert "passiveGuardSentInWindow" not in adapter
    assert "FALLBACK_DIAGNOSTIC_REPEAT_EVERY = 20" in script
    assert "fallbackStatusState.hitCounts.set(key, hits)" in script
    assert "_recordOrSendLogPayload(payload)" in adapter
    assert "event: 'log_queue_overflow'" in adapter
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
    assert "requestSessionId = _runtimeSessionId()" in sidecar_block
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
    assert "_showExternalUserVoiceBubble(output)" in external_route_input_block
    assert "const playerBubbleEl = document.getElementById('player-speech-bubble')" in script
    assert "positionSpeechBubble(playerBubbleEl, playerEl" in script
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
    assert "timeoutMs: EXIT_PROMPT_LINE_WAIT_MS" in exit_prompt_line_block
    assert "new AbortController()" not in exit_prompt_line_block
    assert "timeoutMs: PASSIVE_GUARD_SIDE_CAR_TIMEOUT_MS" in sidecar_block
    assert "new AbortController()" not in sidecar_block
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
def test_game_voice_unknown_backend_mode_falls_back_instead_of_disabling_transcription():
    websocket_js = (ROOT / "static" / "app" / "app-websocket.js").read_text(encoding="utf-8")
    capture_js = (ROOT / "static" / "app" / "app-audio-capture.js").read_text(encoding="utf-8")

    assert "GAME_VOICE_TRANSCRIPTION_MODES.indexOf(transcriptionMode) === -1" in websocket_js
    assert "transcriptionMode = 'unavailable';" in websocket_js
    assert "['backend_pending', 'native_core', 'independent_asr'].indexOf(transcriptionMode)" in websocket_js
    assert "publishGameVoiceBrowserTranscriptionState(false, errorCode);" in capture_js


@pytest.mark.unit
def test_game_voice_transcription_contract_is_host_owned_and_capability_based():
    app_state_js = (ROOT / "static" / "app" / "app-state.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "static" / "app" / "app-websocket.js").read_text(encoding="utf-8")
    host_bridge_js = (ROOT / "static" / "app" / "app-game-voice-control.js").read_text(encoding="utf-8")
    capture_js = (ROOT / "static" / "app" / "app-audio-capture.js").read_text(encoding="utf-8")

    assert "gameVoiceTranscriptionMode: 'unavailable'" in app_state_js
    assert "function setGameVoiceTranscriptionState(next)" in websocket_js
    for mode in (
        "backend_pending",
        "native_core",
        "independent_asr",
        "browser_fallback",
        "unavailable",
    ):
        assert f"'{mode}'" in websocket_js
    assert "capture_owner: 'host'" in websocket_js
    assert "transcription_mode: transcriptionMode" in host_bridge_js
    assert "provider: transcriptionProvider" in host_bridge_js
    assert "ready: transcriptionReady" in host_bridge_js
    assert "publishGameVoiceBrowserTranscriptionState(true, 'browser_ready')" in capture_js
    assert "if (coreApi" not in host_bridge_js
    assert "if (provider === 'free')" not in host_bridge_js


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
