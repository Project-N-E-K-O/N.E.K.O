from __future__ import annotations

from pathlib import Path
from tests.static_app_parts import read_path_or_parts

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_AGENT_PATH = REPO_ROOT / "static" / "app" / "app-agent.js"
APP_UI_PATH = REPO_ROOT / "static" / "app" / "app-ui"
APP_WEBSOCKET_PATH = REPO_ROOT / "static" / "app" / "app-websocket.js"
COMMON_UI_HUD_PATH = REPO_ROOT / "static" / "common-ui-hud.js"
AGENTHUD_TEMPLATE_PATH = REPO_ROOT / "templates" / "agenthud.html"
PNGTUBER_PATH = REPO_ROOT / "static" / "pngtuber-core.js"
PLUGIN_DASHBOARD_PATH = REPO_ROOT / "frontend" / "plugin-manager" / "src" / "views" / "Dashboard.vue"
PLUGIN_METRICS_VIEW_PATH = REPO_ROOT / "frontend" / "plugin-manager" / "src" / "views" / "Metrics.vue"
PLUGIN_METRICS_PATH = REPO_ROOT / "frontend" / "plugin-manager" / "src" / "stores" / "metrics.ts"
SUBTITLE_PATH = REPO_ROOT / "static" / "subtitle" / "subtitle.js"


def _read(path: Path) -> str:
    return read_path_or_parts(path)


def _js_function_block(source: str, function_name: str) -> str:
    marker = f"function {function_name}("
    return _js_block_from_marker(source, marker, function_name)


def _js_assignment_function_block(source: str, name: str) -> str:
    marker = f"{name} = function"
    return _js_block_from_marker(source, marker, name)


def _js_method_block(source: str, method_name: str) -> str:
    marker = f"        {method_name}("
    return _js_block_from_marker(source, marker, method_name)


def _js_block_from_marker(source: str, marker: str, description: str) -> str:
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing JS block {description}")
    header_quote: str | None = None
    header_escaped = False
    paren_depth = 0
    brace = -1
    for index in range(start, len(source)):
        char = source[index]
        if header_quote:
            if header_escaped:
                header_escaped = False
            elif char == "\\":
                header_escaped = True
            elif char == header_quote:
                header_quote = None
            continue
        if char in {"'", '"', "`"}:
            header_quote = char
            continue
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "{" and paren_depth == 0:
            brace = index
            break
    if brace < 0:
        raise AssertionError(f"missing opening brace for JS block {description}")

    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JS block {description}")


@pytest.mark.unit
def test_goodbye_resource_suspend_waits_for_cat_transition_and_uses_token_snapshot():
    source = _read(APP_UI_PATH)
    begin_suspend = _js_function_block(source, "beginGoodbyeResourceSuspend")
    complete_suspend = _js_function_block(source, "completeGoodbyeResourceSuspend")
    restore_suspend = _js_function_block(source, "restoreGoodbyeResourceSuspend")

    assert "NEKO_MODEL_CAT_TRANSITION_DURATION_MS = 850;" in source
    assert "const GOODBYE_RESOURCE_SUSPEND_STORAGE_KEY = 'neko-goodbye-resource-suspended';" in source
    assert "window.goodbyeResourceSuspended = suspended;" in source
    assert "window.__nekoGoodbyeResourceSuspendPending = pending;" in source
    assert "window.isNekoGoodbyeResourceSuspendingOrSuspended = function ()" in source
    assert "localStorage.setItem(GOODBYE_RESOURCE_SUSPEND_STORAGE_KEY, suspended ? 'true' : 'false')" in source
    assert "publishGoodbyeResourceState(null, 'goodbye-resource-boot');" in source
    assert "window.addEventListener('neko:goodbye-state-cleared'" in source
    assert "restoreGoodbyeResourceSuspend(reason);" in source

    goodbye_handler_start = source.index("window.addEventListener('live2d-goodbye-click'")
    token_start = source.index("const goodbyeResourceToken = beginGoodbyeResourceSuspend", goodbye_handler_start)
    hide_timer_start = source.index("window._goodbyeHideTimerId = setTimeout", token_start)
    complete_start = source.index("completeGoodbyeResourceSuspend(goodbyeResourceToken);", hide_timer_start)
    delay_start = source.index("NEKO_MODEL_CAT_TRANSITION_DURATION_MS", complete_start)

    assert token_start < hide_timer_start < complete_start < delay_start
    assert "const token = ++goodbyeResourceSuspendToken;" in begin_suspend
    assert "pending: true" in begin_suspend
    assert "suspended: false" in begin_suspend
    assert "pausedByCat: { live2d: false, vrm: false, mmd: false, pngtuber: false }" in begin_suspend
    assert "subtitleWindowWasVisible: wasSubtitleVisibleBeforeGoodbyeSnapshot()" in begin_suspend
    assert "agentHudWasVisible: isAgentHudVisible()" in begin_suspend

    assert "if (!snapshot || snapshot.token !== token) return;" in complete_suspend
    assert "publishGoodbyeResourceState(snapshot, 'goodbye-resource-suspended');" in complete_suspend
    assert complete_suspend.index("publishGoodbyeResourceState(snapshot, 'goodbye-resource-suspended');") < complete_suspend.index("hideGoodbyeAuxiliaryWindows(snapshot);")
    assert complete_suspend.index("hideGoodbyeAuxiliaryWindows(snapshot);") < complete_suspend.index("pauseModelRenderingForGoodbye(snapshot);")

    assert "goodbyeResourceSuspendToken += 1;" in restore_suspend
    assert restore_suspend.index("resumeModelRenderingFromGoodbye(snapshot);") < restore_suspend.index("publishGoodbyeResourceState(null, reason || 'goodbye-resource-restoring');")
    assert "restoreWindow: true" in restore_suspend
    assert "window.AgentHUD.showAgentTaskHUD();" in restore_suspend
    assert "window.checkAndToggleTaskHUD();" in restore_suspend
    assert "window.nekoAgentHud.show();" not in restore_suspend


@pytest.mark.unit
def test_goodbye_resource_suspend_pauses_only_active_render_loops_and_restores_only_cat_paused_models():
    source = _read(APP_UI_PATH)
    is_rendering = _js_function_block(source, "isModelRenderingActive")
    pause_rendering = _js_function_block(source, "pauseModelRenderingForGoodbye")
    resume_rendering = _js_function_block(source, "resumeModelRenderingFromGoodbye")
    get_manager = _js_function_block(source, "getModelManagerByType")

    assert "if (type === 'live2d')" in is_rendering
    assert "ticker && ticker.started !== false" in is_rendering
    assert "if (type === 'vrm' || type === 'mmd')" in is_rendering
    assert "return !!manager._animationFrameId;" in is_rendering
    assert "if (type === 'pngtuber')" in is_rendering
    assert "document.getElementById('pngtuber-container')" in is_rendering
    assert "container.style.display !== 'none'" in is_rendering

    assert "if (type === 'pngtuber') return window.pngtuberManager;" in get_manager
    assert "const activeModelType = snapshot && snapshot.activeModelType;" in pause_rendering
    assert "if (activeModelType !== type && !isModelRenderingActive(type, manager)) return;" in pause_rendering
    assert "['live2d', 'vrm', 'mmd', 'pngtuber'].forEach" in pause_rendering
    assert "typeof manager.pauseRendering !== 'function'" in pause_rendering
    assert "manager.pauseRendering();" in pause_rendering
    assert "snapshot.pausedByCat[type] = true;" in pause_rendering

    assert "['live2d', 'vrm', 'mmd', 'pngtuber'].forEach" in resume_rendering
    assert "if (!snapshot.pausedByCat[type]) return;" in resume_rendering
    assert "manager.resumeRendering();" in resume_rendering


@pytest.mark.unit
def test_goodbye_resource_suspend_pauses_pngtuber_animation_loops():
    source = _read(PNGTUBER_PATH)
    pause_rendering = _js_method_block(source, "pauseRendering")
    resume_rendering = _js_method_block(source, "resumeRendering")

    assert "this._renderingPaused = false;" in source
    assert "this._renderingPaused = true;" in pause_rendering
    assert "this.clearLayeredTimers();" in pause_rendering
    assert "clearTimeout(this.speakingMouthTimer);" in pause_rendering
    assert "clearTimeout(this.returnIdleTimer);" in pause_rendering
    assert "clearTimeout(this.clickTimer);" in pause_rendering
    assert "cancelAnimationFrame(this.lipSyncFrame);" in pause_rendering
    assert "this.stopTalkingHopAnimation();" in pause_rendering
    assert "this.stopSpeakingBounceAnimation();" in pause_rendering

    assert "if (!this._renderingPaused) return;" in resume_rendering
    assert "this._renderingPaused = false;" in resume_rendering
    assert "this.startLayeredAnimationLoop({ preserveTimeline: true });" in resume_rendering
    assert "this.startSpeakingMouthAnimation();" in resume_rendering

    for method_name in (
        "playLayeredAnimation",
        "startLayeredAnimationLoop",
        "startLayeredBreathingLoop",
        "startSpeakingBounceAnimation",
        "startTalkingHopAnimation",
        "startLipSync",
        "scheduleSpeakingMouthFrame",
        "startSpeakingMouthAnimation",
        "setSpeaking",
    ):
        assert "this._renderingPaused" in _js_method_block(source, method_name)


@pytest.mark.unit
def test_goodbye_subtitles_cancel_active_work_and_do_not_replay_missed_turns():
    source = _read(SUBTITLE_PATH)
    cancel_pending = _js_function_block(source, "cancelPendingSubtitleTranslations")
    suppress = _js_function_block(source, "suppressSubtitleForGoodbye")
    restore = _js_function_block(source, "restoreSubtitleAfterGoodbye")
    begin_turn = _js_function_block(source, "beginSubtitleTurn")
    translate = _js_function_block(source, "translateAndShowSubtitle")

    assert "let subtitleDropCurrentTurnUntilNextStart = false;" in source
    assert "currentTranslationRequestId += 1;" in cancel_pending
    assert "currentTranslateAbortController.abort();" in cancel_pending
    assert "resetIncrementalTranslationState();" in cancel_pending

    assert "subtitleWasVisibleBeforeGoodbye = isSubtitleDisplayCurrentlyVisible();" in suppress
    assert "subtitleSuppressedByGoodbye = true;" in suppress
    assert "subtitleDropCurrentTurnUntilNextStart = true;" in suppress
    assert "cancelPendingSubtitleTranslations();" in suppress
    assert "clearSubtitleTextOnly();" in suppress
    assert "window.nekoSubtitleWindow.hide()" in suppress

    assert "if (isGoodbyeResourceStateActive())" in restore
    assert "subtitleSuppressedByGoodbye = false;" in restore
    assert "cancelPendingSubtitleTranslations();" in restore
    assert "clearSubtitleTextOnly();" in restore
    assert "const shouldRestoreVisible = subtitleEnabled && subtitleWasVisibleBeforeGoodbye;" in restore
    assert "window.nekoSubtitleWindow.show()" in restore
    assert "translateAndShowSubtitle(currentTurnOriginalText)" not in restore
    assert "resumeIncrementalTranslationQueue()" not in restore

    assert "subtitleDropCurrentTurnUntilNextStart = isSubtitleTemporarilySuppressed();" in begin_turn
    assert "if (isSubtitleTemporarilySuppressed() || subtitleDropCurrentTurnUntilNextStart)" in translate
    assert "cancelPendingSubtitleTranslations();" in translate
    assert "clearSubtitleTextOnly();" in translate

    assert "suspendForGoodbye: function()" in source
    assert "restoreAfterGoodbye: function(options)" in source
    assert "cancelPendingTranslations: cancelPendingSubtitleTranslations" in source
    assert "wasVisibleBeforeGoodbye: function()" in source


@pytest.mark.unit
def test_goodbye_agent_hud_and_websocket_ui_timers_are_suppressed_without_stopping_plugins():
    agent_source = _read(APP_AGENT_PATH)
    hud_source = _read(COMMON_UI_HUD_PATH)
    websocket_source = _read(APP_WEBSOCKET_PATH)
    dashboard_source = _read(PLUGIN_DASHBOARD_PATH)
    metrics_source = _read(PLUGIN_METRICS_PATH)
    metrics_view_source = _read(PLUGIN_METRICS_VIEW_PATH)
    app_ui_source = _read(APP_UI_PATH)

    agent_guard = _js_function_block(agent_source, "isGoodbyeAgentUiSuppressed")
    start_polling = _js_assignment_function_block(agent_source, "window.startAgentTaskPolling")
    update_times = _js_function_block(agent_source, "updateTaskRunningTimes")
    check_hud = _js_function_block(agent_source, "checkAndToggleTaskHUD")

    assert "window.isNekoGoodbyeResourceSuspendingOrSuspended()" in agent_guard
    assert "window.isNekoGoodbyeModeActive()" in agent_guard
    assert "if (isGoodbyeAgentUiSuppressed())" in start_polling
    assert "hideAgentTaskHUD();" in start_polling
    assert "if (isGoodbyeAgentUiSuppressed())" in update_times
    assert "return;" in update_times
    assert "window.stopAgentTaskPolling();" in check_hud

    assert "function isAgentHudSuppressedByGoodbye()" in hud_source
    assert "if (isAgentHudSuppressedByGoodbye())" in hud_source
    assert "cancelAnimationFrame(this._updateRafId);" in hud_source
    assert "this.hideAgentTaskHUD();" in hud_source

    assert "function isGoodbyeUiSuppressed()" in websocket_source
    assert "!isGoodbyeUiSuppressed()" in websocket_source
    assert "window._agentTaskTimeUpdateInterval = setInterval" in websocket_source

    assert "function isGoodbyeResourceSuspendingOrSuspended()" in metrics_source
    assert "isNekoGoodbyeResourceSuspendingOrSuspended" in metrics_source
    assert "(window as any).goodbyeResourceSuspended === true" in metrics_source
    assert "(window as any).__nekoGoodbyeResourceSuspendPending === true" in metrics_source
    assert "window.localStorage.getItem('neko-goodbye-resource-suspended') === 'true'" in metrics_source
    assert "if (isGoodbyeResourceSuspendingOrSuspended())" in metrics_source

    for view_source in (dashboard_source, metrics_view_source):
        assert "function isGoodbyeResourceSuspendingOrSuspended()" in view_source
        assert "(window as any).__nekoGoodbyeResourceSuspendPending === true" in view_source
        assert "window.addEventListener('neko:goodbye-resource-suspend-state', handleGoodbyeResourceState)" in view_source
        assert "window.removeEventListener('neko:goodbye-resource-suspend-state', handleGoodbyeResourceState)" in view_source
        assert "function handleGoodbyeResourceStorage(event: StorageEvent)" in view_source
        assert "window.addEventListener('storage', handleGoodbyeResourceStorage)" in view_source
        assert "window.removeEventListener('storage', handleGoodbyeResourceStorage)" in view_source
        assert "event.key !== GOODBYE_RESOURCE_SUSPEND_STORAGE_KEY" in view_source
        assert "if (isGoodbyeResourceSuspendingOrSuspended()) return" in view_source
        assert "stopAutoRefresh()" in view_source

    assert "stop_plugin" not in app_ui_source
    assert "stop_plugin" not in agent_source
    assert "stop_plugin" not in hud_source
    assert "stop_plugin" not in websocket_source
    assert "stop_plugin" not in metrics_source


@pytest.mark.unit
def test_standalone_agent_hud_page_keeps_root_background_transparent():
    template = _read(AGENTHUD_TEMPLATE_PATH)

    assert '<html lang="en" class="agent-hud-standalone-page">' in template
    assert "html.agent-hud-standalone-page," in template
    assert "body.agent-hud-standalone-page:not(.lanlan-pet-mode)" in template
    assert "background: transparent !important;" in template
    assert "background: #1a1a2e;" not in template
