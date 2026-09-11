import json
import re
from pathlib import Path

import pytest

from main_routers import pages_router


ROOT = Path(__file__).resolve().parents[2]
DRAWING_GUESS_TEMPLATE = ROOT / "templates" / "drawing_guess.html"
DRAWING_GUESS_SCRIPT = ROOT / "static" / "game" / "games" / "drawing_guess" / "drawing-guess.js"
I18N_SCRIPT = ROOT / "static" / "i18n-i18next.js"
LOCALES_DIR = ROOT / "static" / "locales"


class _FakePageRequest:
    query_params = {}


class _FakeTemplates:
    def TemplateResponse(self, template_name: str, context: dict):
        return {"template_name": template_name, "context": context}


def _html() -> str:
    return DRAWING_GUESS_TEMPLATE.read_text(encoding="utf-8")


def _script() -> str:
    return DRAWING_GUESS_SCRIPT.read_text(encoding="utf-8")


def _i18n_script() -> str:
    return I18N_SCRIPT.read_text(encoding="utf-8")


def _css_declarations(source: str, selector: str) -> str:
    selector_pattern = r"\s+".join(re.escape(part) for part in selector.split())
    match = re.search(
        rf"(?m)^\s*{selector_pattern}\s*\{{(?P<body>[^}}]*)\}}",
        source,
    )
    assert match is not None, f"missing CSS rule for {selector}"
    return match.group("body")


def _get_nested(payload: dict, dotted_key: str):
    node = payload
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drawing_guess_page_renders_shell(monkeypatch):
    monkeypatch.setattr(pages_router, "get_templates", lambda: _FakeTemplates())

    result = await pages_router.drawing_guess_demo(_FakePageRequest())

    assert result["template_name"] == "templates/drawing_guess.html"
    assert "static_asset_version" in result["context"]


@pytest.mark.unit
def test_drawing_guess_uses_minigame_sdk_for_host_lifecycle():
    html = _html()
    script = _script()
    launch_match = re.search(
        r'<script id="neko-minigame-host-launch" type="application/json">\s*(.*?)\s*</script>',
        html,
        re.DOTALL,
    )
    assert launch_match is not None
    launch = json.loads(launch_match.group(1))
    registration = launch["registrations"]["drawing-guess"]
    assert registration == {
        "mode": "development",
        "gameId": "drawing-guess",
        "routeGameType": "drawing_guess",
        "publisherId": "project-neko",
        "version": "0.1.0",
        "allowedCapabilities": [
            "runtime",
            "logging",
            "voice-input",
            "speech-output",
            "avatar-renderer",
            "memory",
            "storage",
        ],
        "commandRoutes": {
            "round:start": {
                "path": "round/start",
                "maxRequestBytes": 65536,
                "maxTimeoutMs": 30000,
            },
            "round:ai-draw": {
                "path": "ai-draw",
                "maxRequestBytes": 65536,
                "maxTimeoutMs": 90000,
            },
            "round:ai-draw-review": {
                "path": "ai-draw/review",
                "maxRequestBytes": 2097152,
                "maxTimeoutMs": 120000,
            },
            "round:input": {
                "path": "input",
                "maxRequestBytes": 65536,
                "maxTimeoutMs": 30000,
            },
            "round:feedback": {
                "path": "input",
                "maxRequestBytes": 2097152,
                "maxTimeoutMs": 350000,
            },
            "round:choose-word": {
                "path": "choose-word",
                "maxRequestBytes": 65536,
                "maxTimeoutMs": 30000,
            },
            "round:timeout": {
                "path": "timeout",
                "maxRequestBytes": 65536,
                "maxTimeoutMs": 30000,
            },
            "round:vision-guess": {
                "path": "vision-guess",
                "maxRequestBytes": 2097152,
                "maxTimeoutMs": 350000,
            },
        },
    }
    assert html.index("neko-minigame-avatar-host.js") < html.index("neko-minigame-host-launch")
    assert html.index("neko-minigame-drawing-avatar-host.js") < html.index("neko-minigame-host-launch")
    assert html.index("avatarHostFactory: function (options)") < html.index(
        "neko-minigame-same-origin-bootstrap.js"
    )
    assert "delete window.NekoMiniGameDrawingAvatarHost" in html
    assert "delete window.NekoMiniGameAvatarHost" in html
    assert html.index("neko-minigame-same-origin-bootstrap.js") < html.index("neko-minigame-sdk.js")
    assert html.index("neko-minigame-sdk.js") < html.index("drawing-guess.js")

    assert "var SDK_GAME_ID = 'drawing-guess';" in script
    assert (
        "requiredCapabilities: ['runtime', 'logging', 'speech-output', "
        "'avatar-renderer', 'memory']"
    ) in script
    assert "optionalCapabilities: ['voice-input', 'storage']" in script
    assert "commands: ROUND_COMMAND_CONTRACTS" in script
    assert "window.NekoMiniGame.connect" in script
    assert "client.runtime.configure" in script
    assert "client.runtime.start(routePayload(), { timeoutMs: 12000 })" in script
    assert "client.runtime.end(sdkRouteEndPayload(options)" in script
    assert "runtime.reset({ newSession: true })" not in script
    assert "client.events.on('runtime-output'" not in script
    assert "client.events.on('runtime-state'" in script
    assert "client.events.on('runtime-inactive'" in script
    assert "client.events.on('page-exit'" in script
    assert "client.logger.enableAfterRuntimeStart()" in script
    assert "function logSdkBestEffort(client, level, category, event, message, details)" in script
    assert script.count("logSdkBestEffort(") == 6
    assert ".logger.info(" not in script
    assert ".logger.warn(" not in script
    assert ".logger.error(" not in script
    assert "heartbeat: { intervalMs: 2500, timeoutMs: 5000 }" in script
    assert "outputs: { intervalMs: 900, timeoutMs: 6000, limit: 50 }" not in script
    assert "outputs: false" in script
    assert "SDK_ROUTE_CANVAS_DATA_MAX_CHARS = 200 * 1024" in script
    assert "captureRouteCanvasSnapshot" in script
    assert "function sdkRouteInstanceId()" in script
    assert "client.runtime.session.routeInstanceId" in script
    assert "session_id:" not in script
    assert "sdk_route_instance_id:" not in script
    assert "game_type:" not in script
    assert "&sdk_route_instance_id=" not in script

    for suffix in ("start", "heartbeat", "drain", "end"):
        assert f"ROUTE_API + '/route/{suffix}'" not in script
        assert f"'/route/{suffix}'" not in html
    assert "heartbeatTimer" not in script
    assert "routeDrainTimer" not in script
    assert "window.addEventListener('beforeunload'" not in script
    assert "window.addEventListener('pageshow'" in script
    assert "if (event && event.persisted) window.location.reload();" in script
    assert "navigator.sendBeacon" not in script
    assert "heartbeatTimer" not in html
    assert "navigator.sendBeacon" not in html

    # Game code declares portable commands, speech output, and voice input;
    # endpoint selection, route identity, and audio transport stay host-owned.
    for command in (
        "round:start",
        "round:ai-draw",
        "round:ai-draw-review",
        "round:input",
        "round:feedback",
        "round:choose-word",
        "round:timeout",
        "round:vision-guess",
    ):
        assert f"'{command}'" in script
    assert "client.commands.execute(command, payload || {}" in script
    assert "client.speech.speak({" in script
    assert "client.speech.onState(handleSpeechPlaybackState)" in script
    assert "client.voice.onState(handleSdkVoiceState)" in script
    assert "client.voice.onTranscript(handleSdkVoiceTranscript)" in script
    assert "client.voice.onError(handleSdkVoiceError)" in script
    assert "client.voice.toggle({ timeoutMs: 12000 })" in script
    assert "client.voice.stop({ timeoutMs: 6500 })" in script
    assert "client.events.on('page-exit', handleSdkPageExit)" in script
    assert "state.playerTextChain = Promise.resolve(state.playerTextChain)" in script
    assert "client.window." not in script
    assert "window.nekoHost" not in script
    assert "try { window.close(); }" in script
    assert "window.location.assign('/')" in script
    assert "startSpeechAudioSocket" not in script
    for direct_transport_marker in (
        "/api/game",
        "ROUND_API",
        "ROUTE_API",
        "new WebSocket",
        "speech/ws",
        "/speak",
        "speechAudioSocket",
        "pendingAudioChunkMetaQueue",
        "enqueueIncomingAudioBlob",
        "pushSpeechAudioHeader",
        "suppress_primary_audio",
    ):
        assert direct_transport_marker not in script

    sdk_dir = ROOT / "static" / "game" / "sdk"
    for name in (
        "neko-minigame-sdk.js",
        "neko-minigame-sdk.d.ts",
        "neko-minigame-manifest.schema.json",
        "neko-minigame-same-origin-bootstrap.js",
        "neko-minigame-same-origin-host.js",
        "neko-minigame-avatar-host.js",
        "neko-minigame-drawing-avatar-host.js",
        "README.md",
    ):
        assert (sdk_dir / name).is_file()


@pytest.mark.unit
def test_drawing_guess_static_route_contract():
    html = _html()
    script = _script()
    toolbar_rule = _css_declarations(html, ".dg-toolbar")
    canvas_wrap_rule = _css_declarations(html, ".dg-canvas-wrap")
    canvas_stage_rule = _css_declarations(html, ".dg-canvas-stage")
    size_preview_rule = _css_declarations(html, ".dg-size-preview")
    size_preview_hidden_rule = _css_declarations(html, ".dg-size-preview[hidden]")
    model_stage_rule = _css_declarations(html, ".dg-model-stage")
    model_layers_rule = _css_declarations(
        html,
        ".dg-model-renderer, .dg-model-loading, .dg-model-fallback",
    )

    assert "/static/game/games/drawing_guess/drawing-guess.js" in html
    assert "var GAME_TYPE = 'drawing_guess';" in script
    assert "ROUND_API" not in script
    assert "ROUTE_API" not in script
    assert "lanlan_name: queryLanlan || ''" in html
    assert "lanlan_name: queryLanlan || 'drawing_guess_demo'" not in html
    assert "source: 'drawing_guess_demo'" not in html
    assert "source: 'drawing_guess_demo'" not in script
    assert "client.avatar.getCurrentCharacter()" in script
    assert "client.avatar.getCharacter(requestedName)" in script
    assert "client.avatar.listCharacters()" not in script
    assert "fetch('/api/characters" not in script
    assert '<button id="start-button"' not in html
    assert '<button id="reload-character-button"' not in html
    assert 'data-i18n="drawingGuess.layout.modelTitle"' not in html
    assert "--dg-bg: #eef7ff;" in html
    assert "--dg-accent: #17a7ff;" in html
    assert "/static/icons/icon_systray.ico" in html
    assert 'id="debug-trigger"' not in html
    assert 'id="debug-panel"' not in html
    assert "debug_start_phase" not in script
    assert 'id="canvas-badge"' in html
    assert "/static/icons/paw_ui.png" not in html
    assert "/static/icons/image_icon.svg" in html
    assert "/static/icons/chat_icon.png" in html
    assert 'class="dg-side"' in html
    assert 'id="side-pane" class="dg-side"' in html
    assert 'id="side-resizer"' in html
    assert "grid-template-rows: minmax(220px, 64%) 12px minmax(280px, 1fr);" in html
    assert "min-height: 512px;" in html
    assert 'class="dg-model-column"' not in html
    assert 'class="dg-chat-column"' not in html
    assert 'class="dg-actions"' not in html
    assert 'class="dg-canvas-actions"' in html
    assert 'id="canvas-placeholder-detail" data-i18n="drawingGuess.layout.canvasWaiting"' in html
    assert ".dg-canvas-actions .dg-button:not(:disabled):hover" in html
    assert ".dg-button-danger:not(:disabled):hover" in html
    assert "z-index: 18;" in toolbar_rule
    assert "overflow: visible;" in toolbar_rule
    assert "z-index: 1;" in canvas_wrap_rule
    assert 'class="dg-tool-popover"' in html
    assert 'class="dg-tool-menu"' in html
    assert 'id="brush-size" class="dg-size"' in html
    assert 'id="eraser-size" class="dg-size"' in html
    assert 'id="brush-mode-brush"' in html
    assert 'id="brush-mode-bucket"' in html
    assert 'data-brush-tool="bucket"' in html
    assert 'data-brush-kind="brush"' in html
    assert 'id="color-panel-toggle"' in html
    assert 'id="color-panel" class="dg-color-panel"' in html
    assert 'id="color-wheel" class="dg-color-wheel"' in html
    assert 'id="brush-color" type="color"' in html
    assert 'id="color-panel-preview" class="dg-color-current"' in html
    assert 'id="color-history-colors" class="dg-color-grid dg-color-grid-history"' in html
    assert 'id="eyedropper-button"' in html
    assert 'data-i18n-aria="drawingGuess.tools.eyedropper"' in html
    assert 'data-i18n="drawingGuess.tools.basicColors"' in html
    assert 'data-i18n="drawingGuess.tools.historyColors"' in html
    assert 'class="dg-swatch"' not in html
    assert 'class="dg-tool-control dg-brush-size-control"' in html
    assert 'id="brush-size-preview" class="dg-size-preview" hidden' in html
    assert 'id="brush-size-preview-ring" class="dg-size-preview-ring" aria-hidden="true"' in html
    assert '<input id="brush-size" class="dg-size" type="range" min="2" max="28" value="7"' in html
    assert '<input id="eraser-size" class="dg-size" type="range" min="4" max="56" value="13"' in html
    assert "place-items: center;" in canvas_stage_rule
    assert "overflow: hidden;" in canvas_stage_rule
    assert "z-index: 18;" in size_preview_rule
    assert "inset: 0;" in size_preview_rule
    assert "pointer-events: none;" in size_preview_rule
    assert "display: none !important;" in size_preview_hidden_rule
    assert ".dg-size-preview-ring" in html
    assert "width: var(--dg-size-preview-diameter, 20px);" in html
    assert "border: var(--dg-size-preview-border, 2px) solid var(--dg-size-preview-color, var(--dg-accent-strong));" in html
    assert "border-style: dashed;" in html
    assert ".dg-tool-mode-group" in html
    assert ".dg-tool svg[hidden]" in html
    assert '.dg-tool[data-brush-kind="brush"] .dg-bucket-icon' in html
    assert '.dg-tool[data-brush-kind="bucket"] .dg-brush-icon' in html
    assert '.dg-tool[data-brush-kind="bucket"] .dg-bucket-icon' in html
    assert ".dg-color-panel" in html
    assert ".dg-color-wheel" in html
    assert ".dg-color-wheel::after" in html
    assert "pointer-events: none;" in html
    assert ".dg-color-chip" in html
    assert ".dg-color-grid-history" in html
    assert "min-height: 34px;" in html
    assert "background: transparent;" in html
    assert ".dg-color-eyedropper" in html
    assert ".dg-tool-menu[data-brush-kind=\"bucket\"] .dg-brush-size-control" in html
    assert ".dg-size-preview" in html
    assert "left: -220px;" in html
    assert "right: -32px;" in html
    assert "height: 48px;" in html
    assert ".dg-tool-popover.is-open .dg-tool-menu" in html
    assert ".dg-tool-popover.is-open::after" in html
    assert "pointer-events: auto;" in html
    assert "transition-delay: 0ms;" in html
    assert 'id="clear-canvas-button" class="dg-tool"' in html
    assert 'id="model-stage"' in html
    assert 'data-model-kind="loading" data-model-mood="idle" data-model-load-state="loading"' in html
    assert 'id="model-loading" class="dg-model-loading"' in html
    assert 'data-i18n="drawingGuess.modelStates.loading"' in html
    assert 'id="live2d-container"' in html
    assert 'id="live2d-canvas"' in html
    assert 'id="vrm-container"' in html
    assert 'id="vrm-canvas"' in html
    assert 'id="mmd-container"' in html
    assert 'id="mmd-canvas"' in html
    assert 'id="pngtuber-container"' in html
    assert 'class="dg-model-renderer dg-pngtuber-renderer hidden"' in html
    assert 'id="model-fallback-container" class="dg-model-fallback hidden" aria-hidden="true" hidden' in html
    assert ".dg-model-loading[hidden]" in html
    assert ".dg-pngtuber-renderer" in html
    assert "dg-model-loading-spin" in html
    assert "padding: 0;" in model_stage_rule
    assert "inset: 0;" in model_layers_rule
    assert "display: grid;" in model_layers_rule
    assert "place-items: center;" in model_layers_rule
    assert 'id="model-state"' not in html
    assert 'id="model-kind"' not in html
    assert 'class="dg-model-status"' not in html
    assert 'class="dg-model-controls"' in html
    assert 'id="model-scale-control"' not in html
    assert 'id="model-x-control"' not in html
    assert 'id="model-y-control"' not in html
    assert 'id="model-reset-control"' in html
    assert "/static/live2d/live2d-core.js" in html
    assert "/static/app/app-state.js" in html
    assert "/static/app/app-audio-playback.js" in html
    assert "/static/live2d/live2d-emotion.js" in html
    assert "/static/live2d/live2d-interaction.js" in html
    assert "/static/live2d/live2d-model.js" in html
    assert "function setModelMood" in script
    assert "els.modelState.hidden = !shouldShowModelState;" not in script
    assert "function handleModelWheel" in script
    assert "function modelViewTranslateTarget" in script
    assert "function modelViewDragReferenceSize" in script
    assert "target.offsetWidth || target.clientWidth || width" in script
    assert "function beginModelDrag" in script
    assert "function moveModelDrag" in script
    assert "function endModelDrag" in script
    assert "addEventListener('wheel', handleModelWheel" in script
    assert "setModelView({ scale: current.scale * step }, true);" in script
    assert "var SIDE_SPLIT_DEFAULT_RATIO = 0.64;" in script
    assert "var SIDE_MODEL_MIN_HEIGHT = 220;" in script
    assert "var SIDE_CHAT_MIN_HEIGHT = 280;" in script
    assert "minmax(' + SIDE_CHAT_MIN_HEIGHT + 'px, 1fr)'" in script
    assert "function beginSideResize" in script
    assert "function moveSideResize" in script
    assert "function endSideResize" in script
    assert "function handleSideResizeKey" in script
    assert "els.sideResizer.addEventListener('pointerdown', beginSideResize);" in script
    assert "if (!state.sideResize)" in script
    assert "state.avatarController.setView(view)" in script
    assert "state.avatarController.setView(normalizeModelView(state.modelView))" in script
    assert "clampNumber(view.scale, 0.5, 5000" in script
    assert "scale: 325.63" in script
    assert "x: -0.96" in script
    assert "y: 66.41" in script
    assert "function resetModelView" in script
    assert "function loadModelViewSettings" in script
    assert "function pulseModelMood" in script
    assert "state.avatarController.setEmotion(normalized)" in script
    assert "function initModelSlotForCurrentCharacter" in script
    assert "['loading', els.modelLoading]" in script
    assert "['mmd', els.mmdContainer]" in script
    assert "['pngtuber', els.pngtuberContainer]" in script
    assert "showModelLayer('loading');" in script
    assert "function mountAvatarDescriptor" in script
    assert "client.avatar.mount({" in script
    assert "slot: 'drawing-guess-character'" in script
    assert "characterName: descriptor.name" in script
    assert "function disposeAvatarController" in script
    assert "controller.dispose();" in script
    assert "/static/pngtuber-core.js" in html
    assert "/static/vrm/vrm-init.js" in html
    assert "/static/mmd/mmd-init.js" in html
    assert '"@moeru/three-mmd"' in html
    assert "window.__DRAWING_GUESS_AVATAR_SLOT__ = true;" in html
    assert "window._cardExportPage = true;" in html
    assert "state.avatarController.setSpeaking(true)" in script
    assert "state.avatarController.setSpeaking(false)" in script
    for direct_avatar_marker in (
        "window.Live2DManager",
        "window.VRMManager",
        "window.MMDManager",
        "window.PNGTuberManager",
        "window.appState",
        "window.globalAnalyser",
        "window.lanlan_config",
    ):
        assert direct_avatar_marker not in script
    assert "lipSyncStopTimer: null" in script
    assert "function isSpeechPlaybackAudible" in script
    assert "function armDrawingGuessLipSyncStop" in script
    assert "if (isSpeechPlaybackAudible(detail))" in script
    assert "scheduleDrawingGuessLipSyncStart();\n      return;\n    }\n    var response" not in script
    assert "window.NekoMiniGame.connect" in script
    assert "client.runtime.start(routePayload(), { timeoutMs: 12000 })" in script
    assert "client.runtime.end(sdkRouteEndPayload(options)" in script
    assert "client.events.on('runtime-output'" not in script
    assert "function pushCanvasContextForRoute" in script
    assert "function publishFinalSummaryRouteState" in script
    assert "sdkPulseForceRequestedSequence: 0" in script
    assert "sdkPulseForceAcknowledgedSequence: 0" in script
    assert "sdkPulsePayloadForce: false" in script
    assert "var forceCanvas = state.sdkPulsePayloadForce;" in script
    assert "forceCanvasContext" not in script
    assert "state.sdkPulseForceRequestedSequence += 1;" in script
    assert "client.runtime.pulse(true)" in script
    assert "var targetForceSequence = state.sdkPulseForceRequestedSequence;" in script
    assert "return runPulse();" in script
    assert "state.sdkPulseForceAcknowledgedSequence" in script
    assert "state.sdkPulsePayloadForce = forceThisPulse;" in script
    assert "state.sdkPulsePayloadForce = false;" in script
    assert "if (state.sdkPulsePromise === pulsePromise) state.sdkPulsePromise = null;" in script
    assert "state.canvasContextLastPayloadKind = 'clear';" in script
    assert "attemptedCanvasKind === 'clear'" in script
    assert "preserveCanvasRouteState" not in script
    assert "game_canvas_context_request" not in script
    command_timeout_patterns = {
        "round start": (
            r"executeRoundCommand\(ROUND_COMMANDS\.START,\s*"
            r"roundCommandPayload\(\),\s*10000\)"
        ),
        "AI draw": (
            r"executeRoundCommand\(ROUND_COMMANDS\.AI_DRAW,\s*"
            r"roundCommandPayload\(\),\s*AI_DRAW_REQUEST_TIMEOUT_MS\)"
        ),
        "AI drawing review": (
            r"executeRoundCommand\(ROUND_COMMANDS\.AI_DRAW_REVIEW,\s*"
            r"roundCommandPayload\(\{[^}]*image_data_url:[^}]*\}\),\s*"
            r"AI_DRAW_REVIEW_REQUEST_TIMEOUT_MS\)"
        ),
        "user input": (
            r"executeRoundCommand\(ROUND_COMMANDS\.INPUT,\s*"
            r"roundCommandPayload\(Object\.assign\(\{\s*text: text\s*\},\s*"
            r"inputMetadata \|\| \{\}\)\),\s*ROUND_INPUT_REQUEST_TIMEOUT_MS\)"
        ),
        "round chat": (
            r"executeRoundCommand\(ROUND_COMMANDS\.INPUT,\s*"
            r"roundCommandPayload\(Object\.assign\(\{[^}]*summary_chat_only:[^}]*\},\s*"
            r"options\.inputMetadata \|\| \{\}\)\),\s*ROUND_INPUT_REQUEST_TIMEOUT_MS\)"
        ),
        "drawing feedback": (
            r"executeRoundCommand\(ROUND_COMMANDS\.FEEDBACK,\s*"
            r"roundCommandPayload\(Object\.assign\(\{[^}]*image_data_url:[^}]*\},\s*"
            r"inputMetadata \|\| \{\}\)\),\s*"
            r"AI_GUESS_REQUEST_TIMEOUT_MS\)"
        ),
        "word choice": (
            r"executeRoundCommand\(ROUND_COMMANDS\.CHOOSE_WORD,\s*"
            r"roundCommandPayload\(\{\s*word_id: wordId\s*\}\),\s*10000\)"
        ),
        "round timeout": (
            r"executeRoundCommand\(\s*ROUND_COMMANDS\.TIMEOUT,\s*"
            r"roundCommandPayload\(\{\s*timeout_kind: 'user_guessing'\s*\}\),\s*10000\s*\)"
        ),
        "vision guess": (
            r"executeRoundCommand\(ROUND_COMMANDS\.VISION_GUESS,\s*"
            r"roundCommandPayload\(\{[^}]*image_data_url:[^}]*\}\),\s*"
            r"AI_GUESS_REQUEST_TIMEOUT_MS\)"
        ),
    }
    for flow, pattern in command_timeout_patterns.items():
        assert re.search(pattern, script, re.DOTALL), f"missing SDK command/timeout contract for {flow}"
    assert "var completedRoute = options.completedRoute === undefined" in script
    assert "reason: options.reason || (completedRoute ? 'drawing_guess_game_over' : 'drawing_guess_abandoned')" in script
    assert "roundCompleted: completedRoute" in script
    assert "function renderFinalSummary() {\n    // 最终结算是回合终态" in script
    assert "beginRoundFlow();\n    clearNekoVoiceQueue();\n    state.aiGuessInFlight = false;" in script
    assert "if (state.phase !== 'ai_drawing') return;" in script
    assert "function continueAfterAiDrawingHalf(res, flowToken) {\n    if (!isCurrentRoundFlow(flowToken)) return;" in script
    assert "cancelGuessTimeoutRetry();\n    prepareUserDrawing(" in script
    assert "function requestGuessTimeout(flowToken, attempt)" in script
    assert "function isGuessTimeoutFlowActive(flowToken)" in script
    assert "if (!isGuessTimeoutResult(res)) throw new Error('timeout_transition_unavailable');" in script
    assert "state.guessTimeoutRetryTimer = setTimeout(function ()" in script
    assert "setPhase('loading_round');\n    requestGuessTimeout(flowToken, 0);" in script
    assert "roundSessionReady: false" in script
    assert "els.endButton.disabled = !routeReady || !state.roundSessionReady;" in script
    assert "if (!res || !res.ok) throw new Error" in script
    assert "state.roundSessionReady = true;\n        updateControls();" in script
    assert (
        "function finishGame() {\n"
        "    if (!state.roundSessionReady) return false;\n"
        "    renderFinalSummary();\n"
        "    showExitConfirm();\n"
        "    return true;\n"
        "  }"
    ) in script
    assert "return endRoute(false, { finalSummary: true }).finally(showExitConfirm);" not in script
    assert 'id="voice-route-button" class="dg-voice-button"' in html
    assert "function handleVoiceRouteButton" in script
    assert "client.voice.query({ timeoutMs: 5000 })" in script
    assert "client.voice.stop({ timeoutMs: 6500 })" in script
    assert "client.voice.toggle({ timeoutMs: 12000 })" in script
    assert "client.voice.handoff(" not in script
    assert "voiceHandoff" not in script
    assert "function handleSdkVoiceTranscript" in script
    assert "function submitPlayerText" in script
    assert "input_kind: 'user-voice'" in script
    assert "source: 'sdk_voice_input'" in script
    assert "game_voice_stt_gate" not in script
    assert "game_external_input" not in script
    assert "externalInputTakeover" not in script
    assert "external_input_takeover" not in script
    assert "function speechRecognitionCtor" not in script
    assert "function startInternalVoiceRecognition" not in script
    assert "function stopInternalVoiceRecognition" not in script
    assert "function submitInternalVoiceTranscript" not in script
    assert "browser_speech_recognition" not in script
    assert "state.aiGuessNextAt = Date.now() + delay;" in script
    assert "roundFlowToken: 0" in script
    assert "roundRequestControllers: new Set()" in script
    assert "function abortRoundRequests()" in script
    assert "client_round_token: state.roundFlowToken" in script
    assert "function ensureCurrentRoundFlow" in script
    stale_abort_guard = script.index("if (!isCurrentRoundFlow(requestFlowToken)) {")
    timeout_abort_guard = script.index(
        "if (err && (err.code === 'timeout' || err.code === 'request_timeout')) {"
    )
    assert stale_abort_guard < timeout_abort_guard
    assert "return undefined;" in script[stale_abort_guard:timeout_abort_guard]
    assert "if (!state.routeActive || state.routeEnding) return Promise.resolve(undefined);" in script
    assert "if (err && err.staleRoundFlow) return;" in script
    assert "function reconcileFailedSdkStart" in script
    assert "client.runtime.end(sdkRouteEndPayload({" in script
    assert "client.runtime.startMonitoring({ heartbeat: false, outputs: false });" in script
    assert "if (state.sdkReconcilePromise) return state.sdkReconcilePromise;" in script
    assert "if (state.sdkStartPromise) return state.sdkStartPromise;" in script
    assert "if (!reconciled) throw sdkStartRecoveryBlockedError();" in script
    assert 'value="saved"' not in html
    assert 'drawingGuess.tutorial.memorySaved' not in html
    assert "dg-tutorial-guide" in html
    assert "dg-tutorial-voice" in html
    assert "drawingGuess.tutorial.quickTitle" in html
    assert "drawingGuess.tutorial.quickDraw" in html
    assert "drawingGuess.tutorial.quickGuess" in html
    assert "drawingGuess.tutorial.quickSummary" in html
    assert "drawingGuess.tutorial.voiceHint" in html
    assert "function normalizeMemoryConsent" in script
    assert "function configureSdkMemoryConsent(client)" in script
    assert "client.memory.configureConsent(requested, { timeoutMs: 8000 })" in script
    assert script.index("configureSdkMemoryConsent(client)") < script.index(
        "client.runtime.start(routePayload(), { timeoutMs: 12000 })"
    )
    assert "memory_consent: state.memoryConsent" not in script
    assert "i18n_language: currentLanguage()" not in script
    assert "client.locale." not in script
    assert "function syncPageLocale()" in script
    assert "(window.i18n && window.i18n.language)" in script
    assert "window.addEventListener('localechange', syncPageLocale)" in script
    assert "hydrateSdkPreferences(client)" in script
    assert "'settings/model-views'" in script
    assert "'settings/side-split-ratio'" in script
    assert "'settings/color-history'" in script
    assert "client.storage.get(channel.key" in script
    assert "client.storage.set(channel.key" in script
    for direct_platform_marker in (
        "localStorage",
        "window.i18next",
        "window.__nekoI18nLanguage",
        "window.NEKO_I18N_LANGUAGE",
    ):
        assert direct_platform_marker not in script
    assert "gameStarted: state.phase !== 'tutorial'" in script
    assert "var tutorialOpen = !!els.tutorialOverlay && !els.tutorialOverlay.hidden;" in script
    assert "var routeReady = !!state.lanlanName && state.routeActive && !lifecycleBusy;" in script
    assert "if (els.tutorialOverlay) els.tutorialOverlay.hidden = false;" in script
    assert "function isCanvasEditablePhase()" in script
    assert "function isCanvasInteractionEnabled()" in script
    assert "if (!isCanvasInteractionEnabled() || event.button !== 0) return;" in script
    assert "if (!state.isDrawing || !isCanvasInteractionEnabled()) return;" in script
    assert "state.isDrawing = false;" in script
    assert "if (!isSdkRouteRunning(client)) return false;" in script
    assert "return isSdkRouteRunning(client);" in script
    assert "els.doneButton.hidden = roundSummaryOpen || finalSummaryOpen;" in script
    assert "els.nextRoundButton.hidden = !roundSummaryOpen;" in script
    assert "els.endButton.hidden = finalSummaryOpen;" in script
    assert "els.doneButton.disabled = tutorialOpen || !routeReady || !canvasEditable;" in script
    assert "eraserSize: $('eraser-size')" in script
    assert "brushToolKind: 'brush'" in script
    assert "colorPanelDrag: null" in script
    assert "colorWheelPointerId: null" in script
    assert "brushModeBucket: $('brush-mode-bucket')" in script
    assert "modelLoading: $('model-loading')" in script
    assert "colorPanelToggle: $('color-panel-toggle')" in script
    assert "eyedropperButton: $('eyedropper-button')" in script
    assert "colorWheel: $('color-wheel')" in script
    assert "colorHistoryColors: $('color-history-colors')" in script
    assert "canvasStage: $('canvas-stage')" in script
    assert "sizePreview: $('brush-size-preview')" in script
    assert "sizePreviewRing: $('brush-size-preview-ring')" in script
    assert "function normalizeHexColor" in script
    assert "function currentBrushColor" in script
    assert "COLOR_HISTORY_VISIBLE_COUNT = 7" in script
    assert "COLOR_HISTORY_MAX_COUNT = 28" in script
    assert "function renderColorHistory" in script
    assert "function removeBrushColorFromHistory" in script
    assert "function setBrushColor" in script
    assert "function hsvToHex" in script
    assert "function colorWheelGeometry" in script
    assert "borderLeftWidth" not in script
    assert "angle * 180 / Math.PI + 450" in script
    assert "function pickColorFromWheel" in script
    assert "function beginColorWheelPick" in script
    assert "function requestEyeDropperColor" in script
    assert "new window.EyeDropper().open()" in script
    assert "function hexToRgba" in script
    assert "function canvasDisplayPixelBounds" in script
    assert "canvasDisplayPixelBounds(els.canvas, els.canvasStage)" in script
    assert "view.getComputedStyle(ancestor)" in script
    assert "view && view.visualViewport" in script
    assert "if (!displayBounds) return false;" in script
    assert "function floodFillCanvas" in script
    assert "state.brushMode === 'brush' && state.brushToolKind === 'bucket'" in script
    assert "function setBrushToolKind" in script
    assert "function syncBrushToolButton" in script
    assert "function toggleColorPanel" in script
    assert "function beginColorPanelDrag" in script
    assert "dataset.brushKind" in script
    assert "function showSizePreview" in script
    assert "Math.max(2, size * ((scaleX + scaleY) / 2))" in script
    assert "Math.max(1, Math.min(3, diameter / 5))" in script
    assert "els.sizePreview.style.setProperty('--dg-size-preview-diameter', diameter.toFixed(2) + 'px');" in script
    assert "els.sizePreview.style.setProperty('--dg-size-preview-border', borderWidth.toFixed(2) + 'px');" in script
    assert "Math.max(18" not in script
    assert "els.sizePreview.style.setProperty('--dg-size-preview-color', currentBrushColor());" in script
    assert "els.sizePreview.style.removeProperty('--dg-size-preview-color');" in script
    assert "els.canvas.classList.contains('dg-hidden')) return" not in script
    assert "setTimeout(hideSizePreview, 2600)" in script
    assert "els.ctx.lineWidth = Number(els.eraserSize.value || 13);" in script
    assert "els.ctx.lineWidth = Number(els.brushSize.value || 7);" in script
    assert "Number(els.brushSize.value || 7) * 1.8" not in script
    assert "function closeToolPopovers" in script
    assert "function openToolPopover" in script
    assert "function bindToolPopoverEvents" in script
    assert "document.querySelectorAll('.dg-tool-popover')" in script
    assert "popover.addEventListener('pointerleave'" in script
    assert "closeToolPopovers(popover);" in script
    assert "bindToolPopoverEvents();" in script
    assert "els.brushModeBucket.addEventListener('click', function () { setBrushToolKind('bucket'); });" in script
    assert "els.brushSize.addEventListener('pointerdown', function () { showSizePreview('brush'); });" in script
    assert "els.brushSize.addEventListener('input', function () { showSizePreview('brush'); });" in script
    assert "els.eraserSize.addEventListener('pointerdown', function () { showSizePreview('eraser'); });" in script
    assert "els.eraserSize.addEventListener('input', function () { showSizePreview('eraser'); });" in script
    assert "els.colorPanelToggle.addEventListener('click', toggleColorPanel);" in script
    assert "els.eyedropperButton.addEventListener('click', requestEyeDropperColor);" in script
    assert "els.colorWheel.addEventListener('pointerdown', beginColorWheelPick);" in script
    assert "els.colorWheel.addEventListener('pointermove', moveColorWheelPick);" in script
    assert "els.brushColor.addEventListener('change', function ()" in script
    assert 'id="exit-confirm"' in html
    assert 'id="exit-reopen-button"' in html
    assert "function showExitConfirm" in script
    assert "function deferExitConfirm" in script
    assert "function showExitReopenButton" in script
    assert "els.exitReopenButton.addEventListener('click', showExitConfirm);" in script
    assert "function leaveDrawingGuessPage" in script
    assert "var ROUND_FALLBACK_SECONDS = 5 * 60;" in script
    assert "var AI_DRAW_REQUEST_TIMEOUT_MS = 90 * 1000;" in script
    assert "var AI_DRAW_REVIEW_REQUEST_TIMEOUT_MS = 120 * 1000;" in script
    assert "var AI_DRAW_REVIEW_WIDTH = 384;" in script
    assert "var AI_DRAW_REVIEW_HEIGHT = 288;" in script
    assert "var ROUND_INPUT_REQUEST_TIMEOUT_MS = 30 * 1000;" in script
    assert "var AI_GUESS_REQUEST_TIMEOUT_MS = ROUND_FALLBACK_SECONDS * 1000 + 50000;" in script
    assert "var AI_GUESS_SETTLEMENT_REQUEST_TIMEOUT_MS = 30 * 1000;" in script
    assert "var AI_GUESS_MIN_DELAY_MS = 10000;" in script
    assert "var AI_GUESS_MAX_DELAY_MS = 60000;" in script
    assert "var AI_DRAWING_PLACEHOLDER_DELAY_MS = 1200;" in script
    assert "placeholderDotsTimer: null" in script
    assert "aiDrawingPlaceholderTimer: null" in script
    assert "placeholderDetail: $('canvas-placeholder-detail')" in script
    assert "function startDotAnimation" in script
    assert "function scheduleAiDrawingPlaceholderHint" in script
    assert "startCanvasPlaceholderDots('drawingGuess.messages.aiDrawingWaiting'" in script
    assert "scheduleAiDrawingPlaceholderHint();" in script
    assert "clearAiDrawingPlaceholderHint(true);" in script
    assert "startCountdown(res.guess_seconds || ROUND_FALLBACK_SECONDS" in script
    assert (
        "executeRoundCommand(ROUND_COMMANDS.AI_DRAW, roundCommandPayload(), "
        "AI_DRAW_REQUEST_TIMEOUT_MS)"
    ) in script
    assert "timeoutError.code = 'request_timeout';" in script
    assert "function readableRequestError" in script
    assert "startCountdown(seconds || ROUND_FALLBACK_SECONDS" in script
    assert "els.canvas.toDataURL('image/png')" in script
    assert "state.phase !== 'user_drawing'" in script
    assert "submitGameChat(value, { inputMetadata: options.inputMetadata })" in script
    assert "var feedbackImage = captureUserCanvasPng();" in script
    assert "var feedbackCommandImage = captureVisionCommandImage();" in script
    assert "image_data_url: feedbackCommandImage" in script
    assert "summary_chat_only: !!options.summaryChatOnly" in script
    assert "submitGameChat(value, { summaryChatOnly: true, inputMetadata: options.inputMetadata });" in script
    assert "addUserMessage(value)" in script
    assert "state.phase !== 'summary'" in script
    assert "startThinkingEventMessage('drawingGuess.messages.aiGuessing'" in script
    assert "stopThinkingEventMessage()" in script
    assert "setChatPlaceholder('drawingGuess.input.summaryPlaceholder'" in script
    assert "startCountdown(ROUND_FALLBACK_SECONDS, handleAiGuessTimeout)" in script
    assert "postVisionGuess('', { first_guess: true, image_data_url: commandImage })" in script
    assert "if (!manual) {\n        setPhase('ai_guessing');\n        settleAiGuessTimeout();" in script
    assert "function addAiGuessOutcomeMessage" in script
    assert "drawingGuess.messages.aiGuessCorrect" in script
    assert "drawingGuess.messages.aiGuessWrong" in script
    assert "drawingGuess.messages.aiGuessMissAnswer" in script
    assert "function triggerSupplementGuess" in script
    assert "drawingGuess.messages.userSupplemented" not in script
    assert "function captureUserCanvasPng" in script
    assert "function captureVisionCommandImage" in script
    assert "var VISION_COMMAND_DATA_MAX_CHARS = 1800000;" in script
    assert "data:image/jpeg;base64," in script
    assert "function persistCurrentUserCanvasSnapshot" in script
    assert "persistCurrentUserCanvasSnapshot();\n    setPhase('summary');" in script
    assert "state.pendingSupplementImage = commandImage" in script
    assert "boundedVisionCommandImage(imageDataUrl) || captureVisionCommandImage()" in script
    assert "postVisionGuess('', { supplement: true, image_data_url: commandImage })" in script
    assert "state.pendingSupplementGuess = true" in script
    assert "scheduleNextRandomAiGuess()" in script
    assert "state.pendingAutoGuess = true" in script
    assert "state.pendingAutoGuessImage = commandImage" in script
    assert "triggerRandomAiGuess(autoImage)" in script
    assert "flushDeferredAiGuessWork()" in script
    assert "res.correct || res.kind === 'give_up'" in script
    assert "function routeOutputMatchesCurrentRound" not in script
    assert "function handleRouteDrainOutput" not in script
    assert "lastVoiceTranscriptRequestId" in script
    assert "requestSequence !== state.voiceControlRequestSequence" in script
    assert "res.reason === 'session_busy'" in script
    assert "setTimeout(retryWhenReady, 180);" in script
    assert "aiGuessTimeoutRetryTimer: null" in script
    assert "AI_GUESS_TIMEOUT_MAX_RETRIES = 2" in script
    assert "AI_GUESS_TIMEOUT_PHASE_ADVANCE_MAX_RETRIES = 1" in script
    assert "AI_GUESS_TIMEOUT_BUSY_MAX_POLLS = 50" in script
    assert "AI_GUESS_TIMEOUT_BUSY_RETRY_WINDOW_MS = AI_GUESS_SETTLEMENT_REQUEST_TIMEOUT_MS" in script
    assert "AI_GUESS_TIMEOUT_BUSY_RETRY_DELAY_MS = 750" in script
    assert "aiGuessTimeoutSettling: false" in script
    assert "function isAiGuessTimeoutSettlementActive(flowToken)" in script
    assert "function failAiGuessTimeoutSettlement(reason)" in script
    assert "Date.now() - busyWindowStartedAt >= AI_GUESS_TIMEOUT_BUSY_RETRY_WINDOW_MS" in script
    settlement_start = script.index("function settleAiGuessTimeout")
    assert script.index("if (responsePhase === 'summary')", settlement_start) < script.index(
        "if (!res || !res.ok)", settlement_start
    )
    assert "&& !state.aiGuessTimeoutSettling\n      && isCanvasEditablePhase();" in script
    assert "return !state.aiGuessTimeoutSettling\n      && ['user_guessing'" in script
    assert (
        "executeRoundCommand(\n"
        "      ROUND_COMMANDS.TIMEOUT,\n"
        "      roundCommandPayload({ timeout_kind: 'ai_guessing' }),\n"
        "      AI_GUESS_SETTLEMENT_REQUEST_TIMEOUT_MS\n"
        "    )"
    ) in script
    assert "failAiGuessTimeoutSettlement('session_busy')" in script
    assert "if (attempt < AI_GUESS_TIMEOUT_MAX_RETRIES)" in script
    assert "settleAiGuessTimeout(0, phaseAdvanceAttempt + 1, 0)" in script
    assert "failAiGuessTimeoutSettlement(readableRequestError(err))" in script
    assert "recentNekoMessages" not in script
    assert "client.speech.speak({" in script
    assert "client.speech.onState(handleSpeechPlaybackState)" in script
    assert "roundFlowToken: state.roundFlowToken" in script
    assert "sessionId: state.sessionId" in script
    assert "routeInstanceId: sdkRouteInstanceId()" in script
    assert "item.roundFlowToken !== state.roundFlowToken" in script
    assert "item.sessionId !== state.sessionId" in script
    assert "item.routeInstanceId !== sdkRouteInstanceId()" in script
    assert "speechAudioTapReady" not in script
    assert "speech_tap_ready" not in script
    assert "suppress_primary_audio" not in script
    assert "if (!isCurrentRoundFlow(flowToken)) return;\n      state.chatInFlight = false;" in script
    assert "function submitGameChat(text, options) {\n    options = options || {};\n    var flowToken = state.roundFlowToken;" in script
    assert "if (!isCurrentRoundFlow(flowToken)) return;\n      state.aiGuessInFlight = false;" in script
    assert "resetCanvas();\n        pushCanvasContextForRoute(true);" in script
    assert "if (state.brushMode === 'brush') state.hasDrawn = true;" in script
    assert "settle_on_miss: !!(options && options.settle_on_miss)" in script
    assert "ZH_PLACEHOLDER_FALLBACKS" in script
    assert "\u5148\u7ee7\u7eed\u804a\u5929\uff1b\u60f3\u8ba9\u5979\u518d\u731c\u65f6\u518d\u7ed9\u63d0\u793a" in script
    assert "DRAW_PICK_DURATION_MS" in script
    assert "prepareUserDrawing(res.user_draw_options || res.user_draw_answer" in script
    assert "showDrawPickAnimation(drawOptions, seconds)" in script
    assert "chooseUserDrawWord" in script
    assert "drawingGuess.messages.drawingPickReady" in script
    assert "dg-draw-pick-revealed" in script
    assert "dg-draw-pick-spread" in script
    assert 'id="draw-pick"' in html
    assert "dg-pick-deck" in html
    assert "dg-pick-card" in html
    assert "dg-pick-card-inner" in script
    assert "dg-pick-face-front" in script
    assert "dg-pick-option" in script
    assert "dg-draw-pick-ready .dg-pick-card-1" in html
    assert "dg-message-user" in html
    assert "dg-message-neko" in html
    assert "dg-message-event" in html
    assert "}), AI_GUESS_REQUEST_TIMEOUT_MS)" in script
    assert "saveAiSvg" in script
    assert "saveAiPngFile" in script
    assert "saveUserPng" in script
    assert "svgMarkupToPngBlob" in script
    assert "context.fillStyle = '#fffdfa';" in script
    assert "dg-summary-evaluation" in script
    assert "state.roundSummaries" in script
    assert "state.aiAnswerLabel = ''" in script
    assert "aiAnswerLabel: state.aiAnswerLabel || ''" in script
    assert "function summaryArtworkTitle" in script
    assert "var nekoTitle = summaryArtworkTitle('drawingGuess.summary.nekoArt'" in script
    assert "var userTitle = summaryArtworkTitle('drawingGuess.summary.userArt'" in script
    assert "drawingGuess.summary.userDrawAnswer" not in script
    assert "function renderFinalSummary" in script
    assert "setPhase('final_summary');\n    publishFinalSummaryRouteState();" in script
    assert "function finishGame" in script
    assert "endRoute(false, { finalSummary: true })" not in script
    assert "els.summary.classList.toggle('dg-summary-final', !!finalSummary);" in script
    assert "dg-summary-list" in script
    assert ".dg-summary.dg-summary-final" in html
    assert "display: block;" in html
    assert "padding-bottom: 16px;" in html
    assert ".dg-summary.dg-summary-final .dg-round-summary" in html
    assert ".dg-summary.dg-summary-final .dg-summary-grid" in html
    assert ".dg-summary.dg-summary-final .dg-thumb" in html
    assert "grid-template-rows: auto auto auto;" in html
    assert ".dg-summary.dg-summary-final .dg-thumb-preview" in html
    assert "aspect-ratio: 4 / 3;" in html
    assert "width: 100%;" in html
    assert "height: auto;" in html
    assert ".dg-summary:not(.dg-summary-final) .dg-summary-list" in html
    assert "overflow: hidden;" in html
    assert "'summary', 'final_summary'].indexOf(state.phase) >= 0" in script
    assert "drawingGuess.summary.finalTitle" in script
    assert "drawingGuess.summary.roundLabel" in script
    assert "drawingGuess.summary.noRounds" in script
    assert "data-save-ai-svg-index" in script
    assert "data-save-ai-png-index" in script
    assert "data-save-user-png-index" in script
    assert "data-download-ai-index" not in script
    assert "data-download-user-index" not in script
    assert "drawingGuess.summary.score" not in script
    assert "drawingGuess.summary.outcome." not in script
    assert "animateAiDrawing" in script
    assert "function normalizeAiDrawingPlan(value)" in script
    assert "function renderAiDrawingPlanToCanvas(value, canvas, elementLimit)" in script
    assert "function captureAiDrawingReviewImage(value)" in script
    assert "review.toDataURL('image/jpeg', 0.78)" in script
    assert "function aiDrawingPlanToSvg(value)" in script
    assert "function showAiDrawingPlan(value)" in script
    assert "function prepareAiDrawing(drawing, flowToken)" in script
    assert "function reviewAiDrawingInBackground(drawing, flowToken)" in script
    assert "reviewAiDrawingInBackground(res.drawing, flowToken);" in script
    assert "if (!state.aiDrawingPlan || !showAiDrawingPlan(state.aiDrawingPlan))" in script
    assert "state.aiSvg = serializeAiDrawingSvg(els.aiDrawing) || state.aiSvg" in script
    assert "function screenRectToSvgBounds" in script
    assert "function measureSvgContentMetrics" in script
    assert "function clampCenterForBounds" in script
    assert "svg.setAttribute('preserveAspectRatio', 'none')" in script
    assert "'xMidYMid meet'" not in script
    assert "stageRect.width / stageRect.height" in script
    assert "var viewBoxRatio = 240 / 180;" not in script
    assert "transform 180ms" not in script
    assert "prefers-reduced-motion: reduce" in script
    assert "navigator.sendBeacon" not in script


@pytest.mark.unit
def test_ai_drawing_keeps_compact_loading_badge_during_animation():
    script = _script()
    plan_display = script.split("function showAiDrawingPlan(value)", 1)[1].split(
        "function aiDrawingPlanFromResponse", 1,
    )[0]
    svg_display = script.split("function showAiDrawing(svgMarkup)", 1)[1].split(
        "function normalizeAiDrawingSvg", 1,
    )[0]

    assert "setBadge('');" not in plan_display
    assert "setBadge('');" not in svg_display


@pytest.mark.unit
def test_ai_drawing_plan_is_visible_while_visual_review_runs():
    script = _script()
    round_flow = script.split("function startRound()", 1)[1].split(
        "function prepareUserDrawing", 1,
    )[0]

    draft_display = round_flow.index("draftVisible = showAiDrawingPlan(draftPlan)")
    guessing_phase = round_flow.index("setPhase('user_guessing')")
    visual_review = round_flow.index("reviewAiDrawingInBackground(res.drawing, flowToken)")
    assert draft_display < guessing_phase < visual_review
    assert "startCountdown(res.guess_seconds || ROUND_FALLBACK_SECONDS" in round_flow


@pytest.mark.unit
def test_ai_and_user_canvases_fill_the_same_stage_bounds():
    html = _html()
    ai_container = html.split(".dg-ai-drawing {", 1)[1].split("}", 1)[0]
    ai_svg = html.split(".dg-ai-drawing svg {", 1)[1].split("}", 1)[0]
    ai_canvas = html.split(".dg-ai-drawing canvas {", 1)[1].split("}", 1)[0]

    assert "padding: 0;" in ai_container
    assert "width: 100%;" in ai_svg and "height: 100%;" in ai_svg
    assert "width: 100%;" in ai_canvas and "height: 100%;" in ai_canvas
    assert "object-fit: fill;" in ai_canvas
    assert "width: 80%;" not in ai_svg + ai_canvas
    assert "height: 80%;" not in ai_svg + ai_canvas


@pytest.mark.unit
def test_drawing_guess_locale_cache_version_bumped_for_save_art_actions():
    script = _i18n_script()

    assert "2026-09-10-drawing-guess-pngtuber-import-status" in script


@pytest.mark.unit
def test_drawing_guess_i18n_keys_exist_in_all_static_locales():
    required_keys = (
        "drawingGuess.title",
        "drawingGuess.status.loadingCharacter",
        "drawingGuess.status.active",
        "drawingGuess.layout.canvasTitle",
        "drawingGuess.layout.sideResize",
        "drawingGuess.tools.color",
        "drawingGuess.tools.bucket",
        "drawingGuess.tools.eyedropper",
        "drawingGuess.tools.basicColors",
        "drawingGuess.tools.historyColors",
        "drawingGuess.tutorial.memoryNone",
        "drawingGuess.tutorial.memorySummary",
        "drawingGuess.tutorial.quickTitle",
        "drawingGuess.tutorial.quickDraw",
        "drawingGuess.tutorial.quickGuess",
        "drawingGuess.tutorial.quickSummary",
        "drawingGuess.tutorial.voiceHint",
        "drawingGuess.actions.done",
        "drawingGuess.actions.saveNekoSvg",
        "drawingGuess.actions.saveNekoPng",
        "drawingGuess.actions.saveUserPng",
        "drawingGuess.exitConfirm.title",
        "drawingGuess.exitConfirm.message",
        "drawingGuess.exitConfirm.stay",
        "drawingGuess.exitConfirm.leave",
        "drawingGuess.exitConfirm.reopen",
        "drawingGuess.actions.start",
        "drawingGuess.memory.noneShort",
        "drawingGuess.phases.drawing_pick",
        "drawingGuess.phases.user_drawing",
        "drawingGuess.phases.final_summary",
        "drawingGuess.modelStates.idle",
        "drawingGuess.modelStates.loading",
        "drawingGuess.modelStates.drawing",
        "drawingGuess.modelStates.thinking",
        "drawingGuess.modelStates.guessing",
        "drawingGuess.modelStates.talking",
        "drawingGuess.modelStates.happy",
        "drawingGuess.modelControls.group",
        "drawingGuess.modelControls.scale",
        "drawingGuess.modelControls.x",
        "drawingGuess.modelControls.y",
        "drawingGuess.modelControls.reset",
        "drawingGuess.timer.seconds",
        "drawingGuess.messages.drawingPickTitle",
        "drawingGuess.messages.drawingPickSubtitle",
        "drawingGuess.messages.drawingPickReady",
        "drawingGuess.messages.drawingPickReveal",
        "drawingGuess.messages.aiDrawingWaiting",
        "drawingGuess.messages.aiGuessLine",
        "drawingGuess.messages.aiGuessCorrect",
        "drawingGuess.messages.aiGuessWrong",
        "drawingGuess.messages.aiGuessMissAnswer",
        "drawingGuess.messages.summaryReady",
        "drawingGuess.messages.userSupplemented",
        "drawingGuess.messages.requestTimeout",
        "drawingGuess.input.placeholder",
        "drawingGuess.input.hintPlaceholder",
        "drawingGuess.input.summaryPlaceholder",
        "drawingGuess.voice.connected",
        "drawingGuess.voice.connectHint",
        "drawingGuess.voice.connectedNotice",
        "drawingGuess.voice.connectHintNotice",
        "drawingGuess.voice.starting",
        "drawingGuess.voice.unavailable",
        "drawingGuess.voice.controlFailed",
        "drawingGuess.voice.routeNotReady",
        "drawingGuess.voice.buttonLabel",
        "drawingGuess.summary.title",
        "drawingGuess.summary.finalTitle",
        "drawingGuess.summary.roundLabel",
        "drawingGuess.summary.noRounds",
        "drawingGuess.summary.userDrawAnswer",
        "drawingGuess.summary.nekoArt",
    )
    locale_files = sorted(LOCALES_DIR.glob("*.json"))
    assert {path.name for path in locale_files} >= {
        "en.json",
        "ja.json",
        "ko.json",
        "zh-CN.json",
        "zh-TW.json",
        "ru.json",
        "pt.json",
        "es.json",
    }

    for path in locale_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in required_keys:
            value = _get_nested(payload, key)
            assert isinstance(value, str) and value.strip(), f"{path.name} missing {key}"

        drawing_guess = payload["drawingGuess"]
        assert "???" not in drawing_guess["layout"]["canvasWaiting"]
        assert "???" not in drawing_guess["messages"]["aiDrawingWaiting"]
        assert "???" not in drawing_guess["messages"]["userSupplemented"]
        assert "???" not in drawing_guess["messages"]["requestTimeout"]
        assert "???" not in drawing_guess["exitConfirm"]["message"]
        assert "???" not in drawing_guess["exitConfirm"]["reopen"]
        assert "???" not in drawing_guess["summary"]["finalTitle"]
    assert "memorySaved" not in drawing_guess["tutorial"]
    assert "savedShort" not in drawing_guess["memory"]
