import ast
import asyncio
import json
import re
from pathlib import Path
from tests.static_app_parts import read_js_parts
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from starlette.requests import Request

from utils.config_manager import ConfigManager, get_config_manager

from tests.yui_guide_director_parts import DIRECTOR_SCRIPT_NAMES, read_director_source


@pytest.fixture
def reset_tracker_records():
    """Hand out a `register(lanlan)` callable; wipe registered lanlan keys
    from the module-level `_task_tracker._records` both before yield and
    in the finalizer.

    Module-level `_task_tracker` is shared across tests; without explicit
    isolation a failing assertion would leak cancelled records into the
    next test and produce spurious flakes. Putting cleanup in the fixture
    finalizer keeps it on the `finally` path so it runs even if the test
    body raises.
    """
    from app.agent_server import _task_tracker

    registered: set[str] = set()

    def register(lanlan: str) -> None:
        _task_tracker._records.pop(lanlan, None)
        registered.add(lanlan)

    yield register
    for lanlan in registered:
        _task_tracker._records.pop(lanlan, None)


def _expected_plugin_dashboard_location(v: str = "") -> str:
    from config import USER_PLUGIN_BASE

    base_ui = USER_PLUGIN_BASE.rstrip("/") + "/ui"
    return f"{base_ui}?{urlencode({'v': v})}" if v else base_ui


_AGENT_SERVER_PKG_DIR = Path("app/agent_server")


def _agent_server_package_sources():
    """Return (path, source) for every module of the app/agent_server package.

    The former monolithic ``app/agent_server.py`` was split into a package
    (facade ``__init__.py`` + domain submodules); source-scanning assertions
    search all package files.
    """
    return [
        (path, path.read_text(encoding="utf-8"))
        for path in sorted(_AGENT_SERVER_PKG_DIR.rglob("*.py"))
    ]


def _agent_server_package_source_concat() -> str:
    return "\n".join(source for _, source in _agent_server_package_sources())


def _find_agent_server_function(func_name: str, kind=(ast.FunctionDef, ast.AsyncFunctionDef)):
    """Locate a top-level function across the agent_server package.

    Returns ``(source, node)`` of the defining module so callers can use
    ``ast.get_source_segment``.
    """
    for _, source in _agent_server_package_sources():
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, kind) and node.name == func_name:
                return source, node
    raise AssertionError(f"{func_name} not found in app/agent_server package")


def _route_paths_from_decorators(py_file_path: str, target_name: str):
    source = Path(py_file_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    paths = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != target_name:
                continue
            if not decorator.args:
                continue
            first_arg = decorator.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                paths.add(first_arg.value)
    return paths


def _get_function_def(py_file_path: str, func_name: str):
    source = Path(py_file_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    raise AssertionError(f"function {func_name} not found in {py_file_path}")


def _gather_string_literals(node):
    values = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.add(child.value)
    return values


def _contains_call(func_node, attr_name: str) -> bool:
    for child in ast.walk(func_node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr == attr_name:
                return True
    return False


def test_core_config_uses_agent_model_only():
    cfg = get_config_manager().get_core_config()
    assert "AGENT_MODEL" in cfg
    assert "AGENT_MODEL_URL" in cfg
    assert "AGENT_MODEL_API_KEY" in cfg

    legacy_keys = [k for k in cfg.keys() if k.startswith("COMPUTER_USE_")]
    assert legacy_keys == []


def test_agent_server_legacy_endpoints_removed():
    paths = _route_paths_from_decorators("app/agent_server/__init__.py", "app")
    assert "/process" not in paths
    assert "/plan" not in paths
    assert "/analyze_and_plan" not in paths


def test_main_agent_router_legacy_endpoints_removed():
    paths = _route_paths_from_decorators("main_routers/agent_router.py", "router")
    assert "/api/agent/task_status" not in paths
    assert "/api/agent/notify_task_result" not in paths


def test_main_agent_router_expected_proxy_endpoints_exist():
    paths = _route_paths_from_decorators("main_routers/agent_router.py", "router")
    for expected in {
        "/flags",
        "/health",
        "/tasks",
        "/tasks/{task_id}",
        "/computer_use/availability",
        "/browser_use/availability",
        "/openclaw/availability",
        "/mcp/availability",
    }:
        assert expected in paths


@pytest.mark.asyncio
async def test_main_agent_router_plugin_dashboard_redirect_uses_base_ui_url_without_query():
    from main_routers.agent_router import redirect_plugin_dashboard

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/agent/user_plugin/dashboard",
        "headers": [],
        "query_string": b"",
    })
    response = await redirect_plugin_dashboard(request)

    assert response.headers["location"] == _expected_plugin_dashboard_location()


@pytest.mark.asyncio
async def test_main_agent_router_plugin_dashboard_redirect_keeps_only_v_query():
    from main_routers.agent_router import redirect_plugin_dashboard

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/agent/user_plugin/dashboard",
        "headers": [],
        "query_string": b"v=abc123&yui_guide=1&handoff=token",
    })
    response = await redirect_plugin_dashboard(request)

    assert response.headers["location"] == _expected_plugin_dashboard_location("abc123")


@pytest.mark.asyncio
async def test_main_agent_router_plugin_dashboard_redirect_ignores_empty_v_query():
    from main_routers.agent_router import redirect_plugin_dashboard

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/agent/user_plugin/dashboard",
        "headers": [],
        "query_string": b"v=&yui_guide=1",
    })
    response = await redirect_plugin_dashboard(request)

    assert response.headers["location"] == _expected_plugin_dashboard_location()


@pytest.mark.asyncio
async def test_main_agent_router_plugin_dashboard_redirect_keeps_loopback_yui_opener_origin():
    from config import USER_PLUGIN_BASE
    from main_routers.agent_router import redirect_plugin_dashboard

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/agent/user_plugin/dashboard",
        "headers": [],
        "query_string": b"v=abc123&yui_opener_origin=http%3A%2F%2F127.0.0.1%3A48923&unsafe=https%3A%2F%2Fexample.com",
    })
    response = await redirect_plugin_dashboard(request)

    location = response.headers["location"]
    parsed_location = urlparse(location)
    expected_location = urlparse(USER_PLUGIN_BASE.rstrip("/") + "/ui")
    assert parsed_location.scheme == expected_location.scheme
    assert parsed_location.netloc == expected_location.netloc
    assert parsed_location.path == expected_location.path
    query = parse_qs(parsed_location.query)
    assert query == {
        "v": ["abc123"],
        "yui_opener_origin": ["http://127.0.0.1:48923"],
    }


@pytest.mark.asyncio
async def test_main_agent_router_plugin_dashboard_redirect_rejects_non_loopback_yui_opener_origin():
    from main_routers.agent_router import redirect_plugin_dashboard

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/agent/user_plugin/dashboard",
        "headers": [],
        "query_string": b"yui_opener_origin=https%3A%2F%2Fexample.com",
    })
    response = await redirect_plugin_dashboard(request)

    assert response.headers["location"] == _expected_plugin_dashboard_location()


def test_home_page_opens_plugin_dashboard_through_backend_redirect_for_handoff():
    page_source = Path("templates/index.html").read_text(encoding="utf-8")
    pages_router_source = Path("main_routers/pages_router.py").read_text(encoding="utf-8")
    index_source = Path("static/js/index.js").read_text(encoding="utf-8")
    hud_source = Path("static/common-ui-hud.js").read_text(encoding="utf-8")
    handoff_source = Path("static/tutorial/yui-guide/page-handoff.js").read_text(encoding="utf-8")
    director_source = read_director_source()
    plugin_runtime_source = Path("frontend/plugin-manager/src/yui-guide-runtime.ts").read_text(encoding="utf-8")

    assert "def _user_plugin_ctx()" not in pages_router_source
    assert "window.NEKO_USER_PLUGIN_BASE = {{ user_plugin_base | tojson }};" not in page_source
    assert "data.user_plugin_base" not in index_source
    assert "var PLUGIN_DASHBOARD_REDIRECT_URL = '/api/agent/user_plugin/dashboard';" in hud_source
    assert "getPluginDashboardRedirectUrl" in hud_source
    assert "url: getPluginDashboardRedirectUrl" in hud_source
    assert "new URL('/api/agent/user_plugin/dashboard', window.location.origin)" in handoff_source
    assert "new URL('/api/agent/user_plugin/dashboard', window.location.origin)" in director_source
    assert "handoff.ready ? handoff.targetOrigin : '*'" in director_source
    assert "isTrustedPluginDashboardOrigin(event.origin)" in director_source
    assert "yui_opener_origin" in handoff_source
    assert "OPENER_ORIGIN_QUERY_PARAM = 'yui_opener_origin'" in plugin_runtime_source
    assert "getQueryOpenerOrigin()" in plugin_runtime_source
    assert "isLoopbackOrigin(origin)" in plugin_runtime_source
    assert "var PLUGIN_DASHBOARD_REDIRECT_URL = 'http://127.0.0.1:48916/ui';" not in hud_source


def test_standalone_agent_hud_show_hide_keeps_origin_position():
    hud_source = Path("static/common-ui-hud.js").read_text(encoding="utf-8")
    show_match = re.search(
        r"window\.AgentHUD\.showAgentTaskHUD = function \(\) \{(?P<body>[\s\S]*?)\n\};",
        hud_source,
    )
    hide_match = re.search(
        r"window\.AgentHUD\.hideAgentTaskHUD = function \(\) \{(?P<body>[\s\S]*?)\n\};",
        hud_source,
    )

    assert "const STANDALONE_HUD_POSITION = Object.freeze({" in hud_source
    assert "position = STANDALONE_HUD_POSITION;" in hud_source
    assert show_match is not None
    assert hide_match is not None
    show_body = show_match.group("body")
    hide_body = hide_match.group("body")

    for body in (show_body, hide_body):
        assert "const standaloneAgentHud = isStandaloneAgentHudPage();" in body
        assert "const savedPos = standaloneAgentHud ? null : localStorage.getItem('agent-task-hud-position');" in body
        assert "hud.style.left = STANDALONE_HUD_POSITION.left;" in body
        assert "hud.style.top = STANDALONE_HUD_POSITION.top;" in body
        assert "hud.style.right = STANDALONE_HUD_POSITION.right;" in body
        assert "hud.style.transform = STANDALONE_HUD_POSITION.transform;" in body

    assert "translateY(-50%) translateX(0)" in show_body
    assert "translateY(-50%) translateX(20px)" in hide_body


def test_agent_hud_viewport_clamp_uses_layout_for_non_pixel_positions():
    hud_source = Path("static/common-ui-hud.js").read_text(encoding="utf-8")

    assert "function getAgentHudPixelCoordinate(value, fallback)" in hud_source
    assert "normalized.endsWith('px') && Number.isFinite(numeric)" in hud_source
    assert "const currentLeft = getAgentHudPixelCoordinate(hud.style.left, rect.left);" in hud_source
    assert "const currentTop = getAgentHudPixelCoordinate(hud.style.top, rect.top);" in hud_source
    assert "Number.isFinite(parseFloat(hud.style.top))" not in hud_source


def test_agent_server_expected_event_driven_endpoints_exist():
    paths = set()
    for module_path, _ in _agent_server_package_sources():
        paths.update(_route_paths_from_decorators(str(module_path), "app"))
    for expected in {
        "/health",
        "/agent/flags",
        "/tasks",
        "/tasks/{task_id}",
        "/computer_use/availability",
        "/browser_use/availability",
        "/openclaw/availability",
    }:
        assert expected in paths


def test_agent_router_update_flags_keeps_user_plugin_forwarding():
    fn = _get_function_def("main_routers/agent_router.py", "update_agent_flags")
    literals = _gather_string_literals(fn)
    assert "user_plugin_enabled" in literals
    assert "openclaw_enabled" in literals
    assert "/agent/flags" in literals


def test_agent_router_update_flags_has_safe_rollback_defaults():
    fn = _get_function_def("main_routers/agent_router.py", "update_agent_flags")
    required_keys = {
        "agent_enabled",
        "computer_use_enabled",
        "browser_use_enabled",
        "user_plugin_enabled",
        "openclaw_enabled",
    }

    found_rollback_dict = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.Dict):
            continue
        key_values = set()
        all_false = True
        for key_node, value_node in zip(node.keys, node.values):
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                key_values.add(key_node.value)
            else:
                all_false = False
            if not (isinstance(value_node, ast.Constant) and value_node.value is False):
                all_false = False
        if required_keys.issubset(key_values) and all_false:
            found_rollback_dict = True
            break

    assert found_rollback_dict is True


def test_agent_router_command_syncs_core_flags_locally():
    fn = _get_function_def("main_routers/agent_router.py", "post_agent_command")
    assert _contains_call(fn, "update_agent_flags")


def test_agent_router_openclaw_optimistic_sync_clears_stale_ready():
    source = Path("main_routers/agent_router.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "post_agent_command":
            func = node
            break
    assert func is not None
    func_src = ast.get_source_segment(source, func) or ""

    assert 'if key == "openclaw_enabled":' in func_src
    assert 'flag_update["openclaw_ready"] = False' in func_src
    assert '"openclaw_ready": False' in func_src


def test_agent_router_has_internal_analyze_request_endpoint():
    paths = _route_paths_from_decorators("main_routers/agent_router.py", "router")
    assert "/internal/analyze_request" in paths


def test_yui_guide_steps_registry_uses_day1_round_without_legacy_handoff_scenes():
    source = Path("static/tutorial/yui-guide/steps.js").read_text(encoding="utf-8")
    day1_source = Path("static/tutorial/yui-guide/days/day1-home-guide.js").read_text(encoding="utf-8")

    assert "const CONTRACT_VERSION = 2;" in source
    assert "round: {" in day1_source
    for expected in (
        "id: 'day1_intro_activation'",
        "id: 'day1_intro_greeting'",
        "id: 'day1_takeover_capture_cursor'",
        "id: 'day1_takeover_return_control'",
    ):
        assert expected in day1_source

    for obsolete in (
        "api_key_intro",
        "memory_browser_intro",
        "plugin_dashboard_landing",
        "handoff_steam_workshop",
        "steam_workshop_intro",
    ):
        assert obsolete not in source
        assert obsolete not in day1_source

def test_yui_guide_overlay_supports_progress_meta_and_viewport_placement():
    overlay_source = Path("static/tutorial/yui-guide/overlay.js").read_text(encoding="utf-8")
    director_source = read_director_source()
    style_source = Path("static/css/yui-guide.css").read_text(encoding="utf-8")

    for expected in (
        "yui-guide-bubble-header",
        "yui-guide-bubble-meta",
        "scoreBubbleCandidate",
        "is-placement-",
        "isCircularFloatingButtonElement(element)",
        "geometry === 'circle' || isCircularFloatingButtonElement(element)",
    ):
        assert expected in overlay_source

    for expected in (
        "getHomePresentationSceneOrder()",
        "getBubbleMetaForScene(sceneId)",
        "主页引导 ",
        "isCircularFloatingButtonSpotlight(element)",
        "applyCircularFloatingButtonSpotlightHint(persistentSpotlightTarget)",
        "'[id$=\"-btn-mic\"], [id$=\"-btn-agent\"], [id$=\"-btn-settings\"]'",
        "this.applyCircularFloatingButtonSpotlightHint(primaryTarget)",
        "this.applyCircularFloatingButtonSpotlightHint(this.customSecondarySpotlightTarget)",
    ):
        assert expected in director_source

    for expected in (
        ".yui-guide-bubble-meta",
        ".yui-guide-bubble.is-placement-top::after",
        "@keyframes yui-guide-spotlight-sheen",
        "html[data-theme='dark'] .yui-guide-overlay",
    ):
        assert expected in style_source


def test_yui_takeover_overlay_keeps_window_hittable_during_plugin_preview_cleanup():
    overlay_source = Path("static/tutorial/yui-guide/overlay.js").read_text(encoding="utf-8")
    director_source = read_director_source()
    style_source = Path("static/css/yui-guide.css").read_text(encoding="utf-8")

    for expected in (
        "this.interactionShield = null;",
        "createElement('div', 'yui-guide-interaction-shield')",
        "stage.appendChild(interactionShield);",
        "this.interactionShieldSuppressed = false;",
        "this.tutorialInputShieldActive = false;",
        "setInteractionShieldSuppressed(active)",
        "setTutorialInputShieldActive(active)",
        "syncInteractionShield()",
        "setInteractionShieldEnabled(active)",
        "this.tutorialInputShieldActive",
        "!this.interactionShieldSuppressed",
        "(this.tutorialInputShieldActive || this.takingOverActive)",
        "isSkipControlEventTarget(target)",
        "isSystemDialogEventTarget(target)",
        "hasOpenSystemDialog()",
        "is-interaction-shield-system-dialog-suspended",
        "#storage-location-overlay:not([hidden])",
        "#prominent-notice-overlay",
        ".modal-overlay",
        "isMovementTrackingEvent(event)",
        "event.type === 'mousemove'",
        "if (this.isMovementTrackingEvent(event))",
    ):
        assert expected in overlay_source

    for expected in (
        "this.overlay.setTutorialInputShieldActive(isActive);",
        "const shouldRestoreTutorialInputShield = !!(",
        "this.overlay.tutorialInputShieldActive === true",
        "this.overlay.setTutorialInputShieldActive(false);",
        "shouldRestoreTutorialInputShield && runId === this.sceneRunId && !this.isStopping()",
        "this.overlay.setInteractionShieldSuppressed(true);",
        "this.overlay.setInteractionShieldSuppressed(false);",
    ):
        assert expected in director_source

    interaction_takeover_source = Path("static/tutorial/core/interaction-takeover.js").read_text(encoding="utf-8")
    assert "allowWindowPassthrough" not in director_source
    assert "allowWindowPassthrough" not in interaction_takeover_source
    assert "this.overlay.setInteractionShieldSuppressed(this.active" not in interaction_takeover_source

    for expected in (
        ".yui-guide-interaction-shield {",
        "pointer-events: auto;",
        "background: transparent;",
    ):
        assert expected in style_source


def test_plugin_dashboard_skip_contract_uses_skip_request_without_bypass_event():
    tutorial_source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")
    director_source = read_director_source()
    plugin_runtime_source = Path("frontend/plugin-manager/src/yui-guide-runtime.ts").read_text(encoding="utf-8")

    assert "neko:yui-guide:plugin-dashboard-skip-bypass" not in tutorial_source
    assert "neko:yui-guide:plugin-dashboard-skip-bypass" not in director_source

    for expected in (
        "const PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:skip-request';",
        "const skipButtonScreenRect = await this.getSkipButtonScreenRect();",
        "skipButtonScreenRect: skipButtonScreenRect,",
        "if (data.type === PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT) {",
        "const SKIP_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:skip-request'",
        "skipButtonScreenRect?: ScreenRect | null",
        "this.homeSkipButtonScreenRect = payload.skipButtonScreenRect",
    ):
        assert expected in (director_source + "\n" + plugin_runtime_source)


def test_home_yui_return_petal_transition_decouples_petal_opacity_from_model_fade():
    director_source = read_director_source()
    style_source = Path("static/css/yui-guide.css").read_text(encoding="utf-8")
    doc_source = Path("docs/design/home-yui-guide-text-highlight-cursor-flow.md").read_text(encoding="utf-8")
    petal_animation = Path("static/assets/tutorial/petals/yui-guide-petal-transition.webp")

    for expected in (
        "const RETURN_PETAL_SEQUENCE_URL = '/static/assets/tutorial/petals/yui-guide-petal-transition.webp';",
        "const RETURN_PETAL_ANIMATION_EXTRA_MS = 1000;",
        "const RETURN_PETAL_SEQUENCE_DURATION_MS = 6200;",
        "const RETURN_PETAL_FINAL_OPACITY = 0.6;",
        "returnPetalTransition: Object.freeze({ at: 0.7 })",
        "this.runReturnControlCueWavePerformance().catch((error) => {",
        "async runReturnControlCueWavePerformance()",
        "api.playReturnControlCueWave({",
        "loadReturnPetalSequence()",
        "image.src = RETURN_PETAL_SEQUENCE_URL;",
        "const playback = document.createElement('img');",
        "playback.className = 'yui-guide-petal-sequence';",
        "playback.src = sequence.url;",
        "playback.style.animationDuration = transitionMs + 'ms';",
        "playback.style.setProperty('--yui-guide-petal-origin-x'",
        "playback.style.setProperty('--yui-guide-petal-origin-y'",
        "playback.style.setProperty('--yui-guide-petal-final-opacity', String(finalPetalOpacity));",
        "done: () => donePromise,",
        "const hasExplicitDuration = Number.isFinite(explicitDurationMs) && explicitDurationMs >= 0;",
        "const baseTransitionDurationMs = hasExplicitDuration",
        "baseTransitionDurationMs + RETURN_PETAL_ANIMATION_EXTRA_MS",
        "RETURN_PETAL_SEQUENCE_DURATION_MS",
        "const waitForNarrationEnd = () => new Promise((resolve) => {",
        "const loadedPetalSequence = await this.loadReturnPetalSequence();",
        "sequence: loadedPetalSequence",
        "await transition.done();",
        "durationMs: transitionDurationMs",
        "finalOpacity: RETURN_PETAL_FINAL_OPACITY",
        "this.fadeReturnPetalTransitionModelOut(baseTransitionDurationMs)",
    ):
        assert expected in director_source

    assert "transition.cover()" not in director_source
    assert "coverDelayMs" not in director_source
    assert "globalFade = 1 - globalProgress" not in director_source
    assert "transition.suspend()" not in director_source
    assert "transition.resume()" not in director_source
    assert "suspendedDurationMs" not in director_source
    assert ".yui-guide-petal-sequence" in style_source
    assert "object-fit: cover;" in style_source
    assert " + 6vw)" in style_source
    assert "animation-name: yui-guide-petal-sequence-motion, yui-guide-petal-sequence-opacity;" in style_source
    assert "@keyframes yui-guide-petal-sequence-play" not in style_source
    assert "@keyframes yui-guide-petal-sequence-motion" in style_source
    assert "@keyframes yui-guide-petal-sequence-opacity" in style_source
    assert "opacity: var(--yui-guide-petal-final-opacity, 0.6);" in style_source
    assert petal_animation.exists()
    assert petal_animation.stat().st_size > 0
    assert "花瓣整体透明度与模型淡出分离" in doc_source
    assert "预渲染 30fps animated WebP" in doc_source
    assert "运行时直接用 `<img>` 播放" in doc_source
    assert "花瓣单体进一步缩小，并把总体排布调整为起点更密、终点更疏" in doc_source
    assert "持续约 4.2 秒的右手挥手 `playReturnControlCueWave()`" in doc_source
    assert "复用开场 `computeWakeupPose()` 的右手挥手曲线" in doc_source
    assert "只写 `Param75/90/92/95`" in doc_source
    assert "额外向右校准约 `6vw`" in doc_source
    assert "播放层最终通过 CSS 透明度保持约 60% 覆盖继续流动" in doc_source
    assert "最短播放约 6.2 秒" in doc_source
    assert "等待约 6.2 秒 animated WebP 剩余时间播完" in doc_source
    assert "先大幅向右形成弧线，再向左铺开并从页面左边消失" in doc_source
    assert "最后一句语音播放完成后立即调用教程头像恢复流程" in doc_source
    assert "模型快照恢复期间不暂停花瓣动画" in doc_source


def test_yui_guide_cat_paw_click_state_is_visible_before_actions():
    overlay_source = Path("static/tutorial/yui-guide/overlay.js").read_text(encoding="utf-8")
    director_source = read_director_source()
    style_source = Path("static/css/yui-guide.css").read_text(encoding="utf-8")
    plugin_runtime_source = Path("frontend/plugin-manager/src/yui-guide-runtime.ts").read_text(encoding="utf-8")

    for source in (overlay_source, director_source, plugin_runtime_source):
        assert "DEFAULT_CURSOR_CLICK_VISIBLE_MS = 420" in source

    for expected in (
        "this.cursorClickTimer",
        "window.clearTimeout(this.cursorClickTimer)",
        "this.cursorInner.classList.add('is-clicking')",
        "CURSOR_CLICK_STAR_COUNT = 7",
        "spawnCursorClickStars()",
        "this.spawnCursorClickStars();",
        "yui-guide-click-star",
        "--star-mid-x",
        "CURSOR_TRAIL_ICON_URLS",
        "CURSOR_TRAIL_BLUE_PARTICLE_CHANCE = 0.42",
        "/static/icons/send_icon.png",
        "/static/icons/paw_ui.png",
        "maybeSpawnCursorTrail(nextX, nextY, previousX, previousY, now)",
        "isBlueParticle ? 'is-blue-particle' : 'is-icon'",
        "is-blue-particle",
    ):
        assert expected in overlay_source

    for expected in (
        "this.cursor.click(clickVisibleMs)",
        "await this.waitForSceneDelay(clickVisibleMs)",
        "await this.clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS)",
    ):
        assert expected in director_source

    assert "animation: yui-guide-cursor-click 420ms ease;" in style_source
    assert "@keyframes yui-guide-click-star-burst" in style_source
    assert ".yui-guide-click-star" in style_source
    assert ".yui-guide-cursor-trail.is-glow" in style_source
    assert ".yui-guide-cursor-trail.is-icon" in style_source
    assert ".yui-guide-cursor-trail.is-blue-particle" in style_source
    assert "rgba(119, 233, 255, 0.96)" in style_source
    assert "opacity: 0.52;" in style_source
    assert "drop-shadow(0 0 7px rgba(255, 244, 164, 0.92))" in style_source
    assert "@keyframes yui-guide-cursor-trail-fade" in style_source
    assert "animation: yui-guide-plugin-click 420ms ease;" in plugin_runtime_source
    assert "CURSOR_CLICK_STAR_COUNT = 7" in plugin_runtime_source
    assert "const size = 6 + Math.random() * 6" in overlay_source
    assert "const size = 6 + Math.random() * 6" in plugin_runtime_source
    assert "0.09 + Math.random() * 0.1" in plugin_runtime_source
    assert "CURSOR_TRAIL_ICON_URLS = [sendIconUrl, pawUiUrl]" in plugin_runtime_source
    assert "CURSOR_TRAIL_BLUE_PARTICLE_CHANCE = 0.42" in plugin_runtime_source
    assert "yui-guide-plugin-click-star" in plugin_runtime_source
    assert "yui-guide-plugin-cursor-trail ${isBlueParticle ? 'is-blue-particle' : 'is-icon'}" in plugin_runtime_source
    assert "is-blue-particle" in plugin_runtime_source
    assert "maybeSpawnCursorTrail(position.x, position.y, previous.x, previous.y, now)" in plugin_runtime_source
    assert "spawnCursorClickStars()" in plugin_runtime_source
    assert "await this.waitForSceneDelay(DEFAULT_CURSOR_CLICK_VISIBLE_MS, isCurrent)" in plugin_runtime_source


_YUI_RUNTIME_SCRIPTS = (
    "tutorial/yui-guide/steps.js",
    "tutorial/yui-guide/overlay.js",
    "tutorial/yui-guide/page-handoff.js",
    "tutorial/core/interaction-takeover.js",
    *DIRECTOR_SCRIPT_NAMES,
)

_HOME_YUI_RUNTIME_SCRIPTS = (
    "tutorial/yui-guide/steps.js",
    "tutorial/yui-guide/overlay.js",
    "tutorial/yui-guide/page-handoff.js",
    "avatar/avatar-performance-stage.js",
    "tutorial/avatar/yui-stage.js",
    "tutorial/yui-guide/wakeup.js",
    "tutorial/core/interaction-takeover.js",
    *DIRECTOR_SCRIPT_NAMES,
)


def _script_tag_position(source: str, script_name: str) -> int:
    """Find the position of a `<script src="/static/{script_name}...">` tag,
    ignoring the `?v=...` cache-buster query string."""
    needle = f'<script src="/static/{script_name}'
    position = source.find(needle)
    assert position != -1, f"missing script tag for {script_name}"
    return position


def _stylesheet_tag_position(source: str, stylesheet_name: str) -> int:
    """Find a stylesheet link while allowing cache-buster query strings."""
    needle = f'<link rel="stylesheet" href="/static/css/{stylesheet_name}'
    position = source.find(needle)
    assert position != -1, f"missing stylesheet link for {stylesheet_name}"
    return position


def test_home_template_loads_yui_runtime_stack_before_tutorial_manager():
    source = Path("templates/index.html").read_text(encoding="utf-8")

    positions = [
        _script_tag_position(source, name)
        for name in (
            *_HOME_YUI_RUNTIME_SCRIPTS,
            "tutorial/core/skip-controller.js",
            "tutorial/avatar/reload-controller.js",
            "tutorial/core/universal-manager.js",
        )
    ]
    assert positions == sorted(positions)


def test_home_template_loads_yui_wakeup_before_director():
    source = Path("templates/index.html").read_text(encoding="utf-8")

    positions = [
        _script_tag_position(source, name)
        for name in (
            "tutorial/yui-guide/overlay.js",
            "tutorial/yui-guide/page-handoff.js",
            "avatar/avatar-performance-stage.js",
            "tutorial/avatar/yui-stage.js",
            "tutorial/yui-guide/wakeup.js",
            "tutorial/core/interaction-takeover.js",
            *DIRECTOR_SCRIPT_NAMES,
            "tutorial/core/skip-controller.js",
            "tutorial/avatar/reload-controller.js",
            "tutorial/core/universal-manager.js",
        )
    ]
    assert positions == sorted(positions)


def test_yui_avatar_stage_exposes_extracted_wakeup_action():
    source = Path("static/tutorial/avatar/yui-stage.js").read_text(encoding="utf-8")

    assert "class Live2DWakeupSession" in source
    assert "createWakeupSession" in source
    assert "computeWakeupPose" in source
    assert "computeWakeupRightHandWavePose" in source
    assert "class Live2DReturnControlCueWaveSession extends Live2DWakeupSession" in source
    assert "playReturnControlCueWave" in source
    assert "RETURN_CONTROL_CUE_WAVE_DURATION_MS = 4200" in source
    assert "RETURN_CONTROL_CUE_WAVE_READY_WAIT_MS = 260" in source
    assert "YUI_RETURN_CONTROL_CUE_WAVE_CAPABILITIES = Object.freeze(['params'])" in source
    assert "YUI_WAKEUP_PARAMS" in source
    assert "class Live2DIntroGreetingHugSession" in source
    assert "playIntroGreetingHug" in source
    assert "computeIntroGreetingHugPose" in source
    assert "YUI_INTRO_GREETING_HUG_PARAMS" in source
    assert "class Live2DIntroGiftHeartSession" in source
    assert "playIntroGiftHeart" in source
    assert "computeIntroGiftHeartPose" in source
    assert "YUI_INTRO_GIFT_HEART_PARAMS" in source
    assert "ParamHairFront" in source
    assert "ParamHairSide" in source
    assert "ParamHairBack" in source
    assert "Param54" in source
    assert "Param63" in source
    assert "Param64" in source
    assert "Param77" in source
    assert "Param91" in source
    assert "Param93" in source
    assert "Param96" in source
    gift_params_start = source.index("const YUI_INTRO_GIFT_HEART_PARAMS")
    gift_params_end = source.index("const YUI_INTRO_GIFT_HEART_LEG_PARAM_KEYS", gift_params_start)
    gift_params_source = source[gift_params_start:gift_params_end]
    assert "yuiRightForearmAnim: 'Param90'" in gift_params_source
    assert "yuiLeftForearmAnim: 'Param91'" in gift_params_source
    assert "yuiRightHandAnim: 'Param92'" in gift_params_source
    assert "yuiLeftHandAnim: 'Param93'" in gift_params_source
    assert "armBounce" in source
    assert "armCounterSwing" in source
    assert "this.writeWeighted('yuiRightForearmAnim', pose.yuiRightForearmAnim" in source
    assert "this.writeWeighted('yuiLeftHandAnim', pose.yuiLeftHandAnim" in source
    assert "frameScale" in source
    assert "frameY" in source
    assert "INTRO_GREETING_HUG_CLOSE_SCALE = 1.38" in source
    assert "INTRO_GREETING_HUG_SHIFT_VIEWPORT_RATIO = 0.58" in source
    assert "INTRO_GREETING_HUG_MIN_SHIFT_PX = 360" in source
    assert "INTRO_GREETING_HUG_MAX_SHIFT_PX = 820" in source
    assert "INTRO_GREETING_HUG_FINAL_SHIFT_VIEWPORT_RATIO = 0.52" in source
    assert "INTRO_GREETING_HUG_FINAL_MIN_SHIFT_PX = 340" in source
    assert "INTRO_GREETING_HUG_FINAL_MAX_SHIFT_PX = 700" in source
    assert "resolveIntroGreetingHugFrameShift" in source
    assert "initialModelFrame" in source
    assert "restoreModelFrame" in source
    assert "preserveFrameStyle" not in source
    assert "onInitialPose" in source
    assert "wakeup_initial_pose" not in source
    assert "shouldReduceMotion" not in source
    assert "isStorageLocationOverlayVisible" not in source
    assert "removeBlockingGuideOverlay" not in source
    assert "revealPreparedTutorialLive2D" not in source


def test_yui_asset_version_includes_avatar_performance_runtime():
    source = Path("main_routers/pages_router.py").read_text(encoding="utf-8")

    assert 'static/avatar/avatar-performance-stage.js' in source
    assert source.index('static/avatar/avatar-performance-stage.js') < source.index('static/tutorial/avatar/yui-stage.js')


def test_yui_wakeup_delegates_action_boundary_to_avatar_stage():
    source = Path("static/tutorial/yui-guide/wakeup.js").read_text(encoding="utf-8")
    avatar_source = Path("static/tutorial/avatar/yui-stage.js").read_text(encoding="utf-8")
    live2d_source = Path("static/live2d/live2d-model.js").read_text(encoding="utf-8")
    style_source = Path("static/css/yui-guide.css").read_text(encoding="utf-8")
    yui_model = json.loads(Path("static/yui-origin/yui-origin.model3.json").read_text(encoding="utf-8"))
    yui_display_info = json.loads(Path("static/yui-origin/yui-origin.cdi3.json").read_text(encoding="utf-8"))
    yui_param_ids = {
        item.get("Id")
        for item in yui_display_info.get("Parameters", [])
        if isinstance(item, dict)
    }

    assert "class Live2DWakeupSession" not in source
    assert "computeWakeupPose" not in source
    assert "setTemporaryPoseOverride" not in source
    assert "applyTemporaryPose" not in source
    assert "restoreCapturedParams()" not in source
    assert "createWakeupSession(context" in source
    assert "api && typeof api.shouldReduceMotion" not in source
    assert "api && typeof api.isStorageLocationOverlayVisible" not in source
    assert "api && typeof api.removeBlockingGuideOverlay" not in source
    assert "api && typeof api.revealPreparedTutorialLive2D" not in source
    assert "matchMedia" in source
    assert "storage-location-overlay" in source
    assert "yui-guide-live2d-preparing" in source
    assert "wakeup_initial_pose" in source
    assert "onInitialPose: () =>" in source
    assert "waitForLive2DContext(waitBudget)" in source
    assert "revealPreparedTutorialLive2D(live2dResult.reason || live2dResult.result)" in source
    assert "removeBlockingGuideOverlay(this.document)" in source
    cleanup_block = source.split("function removeBlockingGuideOverlay(doc)", 1)[1].split(
        "function revealPreparedTutorialLive2D(reason)",
        1,
    )[0]
    assert "#yui-guide-overlay" not in cleanup_block
    assert "yui-taking-over" not in cleanup_block
    assert ".yui-guide-wakeup-stage" in cleanup_block
    assert "shouldReduceMotion()" in source
    assert "live2d_session_unavailable" in source
    assert "avatar_stage_unavailable" in source
    assert "Live2D 苏醒动作失败" in source

    assert "class Live2DWakeupSession" in avatar_source
    assert "class Live2DReturnControlCueWaveSession extends Live2DWakeupSession" in avatar_source
    assert "playReturnControlCueWave: playReturnControlCueWave" in avatar_source
    assert "computeWakeupRightHandWavePose: computeWakeupRightHandWavePose" in avatar_source
    assert "DEFAULT_DURATION_MS = 4000" in avatar_source
    assert "LIVE2D_HANDOFF_MS = 620" in avatar_source
    assert "_suspendEyeBlinkOverride" in avatar_source
    assert "removeBlockingGuideOverlay" not in avatar_source
    assert "#yui-guide-overlay" not in avatar_source
    assert "yui-taking-over" not in avatar_source
    assert "setTemporaryPoseOverride" in avatar_source
    assert "applyTemporaryPose" in avatar_source
    assert "restoreCapturedParams()" in avatar_source
    assert "preserveFinalPose" in avatar_source
    assert "YUI_WAKEUP_POSE_BLEND_FACTORS" in avatar_source
    assert "YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS" in avatar_source
    assert "window.AvatarPerformance" in avatar_source
    assert "getDefaultCoordinator" in avatar_source
    assert "YUI_WAKEUP_PERFORMANCE_CAPABILITIES" in avatar_source
    assert "YUI_INTRO_PERFORMANCE_CAPABILITIES" in avatar_source
    assert "acquireYuiGuidePerformanceLock" in avatar_source
    assert "releaseYuiGuidePerformanceLock" in avatar_source
    assert "home-yui-guide-intro-greeting" in avatar_source
    assert "this.clearTemporaryPoseOverride();" in avatar_source
    assert "this.restoreCapturedParams();" in avatar_source
    assert "suspendTemporaryMotions" not in avatar_source
    assert "resumeTemporaryMotions" not in avatar_source
    assert "if (!this.usesTemporaryPoseOverride && this.isCurrentModel())" not in avatar_source
    assert "Live2DManager.prototype.setTemporaryPoseOverride" in live2d_source
    assert "_temporaryPoseOverrides = new Map()" in live2d_source
    assert "this._temporaryPoseOverrides.set(normalizedSource, entry)" in live2d_source
    assert "Array.from(this._temporaryPoseOverrides.values())" in live2d_source
    assert "this._temporaryPoseOverrides.delete(entry.source)" in live2d_source
    assert "_applyTemporaryPoseOverride(currentCoreModel)" in live2d_source
    for param_id in (
        "ParamEyeLOpen",
        "ParamEyeROpen",
        "ParamAngleX",
        "ParamAngleY",
        "ParamAngleZ",
        "ParamEyeBallX",
        "ParamEyeBallY",
        "ParamBodyAngleX",
        "ParamBodyAngleY",
        "ParamBodyAngleZ",
    ):
        assert param_id in avatar_source

    yui_file_refs = yui_model.get("FileReferences", {})
    assert yui_file_refs.get("Moc") == "yui-origin.moc3"
    assert yui_file_refs.get("DisplayInfo") == "yui-origin.cdi3.json"
    for param_id in ("Param75", "Param90", "Param92", "Param95"):
        assert param_id in yui_param_ids

    assert "coreModel.update =" not in avatar_source
    assert "motionManager.update =" not in avatar_source
    assert "model.focus(" not in avatar_source
    assert "document.createElement" not in avatar_source
    assert "appendChild" not in avatar_source
    assert "yui-guide-wakeup-stage" not in style_source
    assert "yui-guide-wakeup-backdrop" not in style_source
    assert "yui-guide-wakeup-particle" not in style_source


def test_yui_intro_greeting_hug_action_is_called_without_param_coupling():
    director_source = read_director_source()
    avatar_source = Path("static/tutorial/avatar/yui-stage.js").read_text(encoding="utf-8")

    assert "runIntroGreetingHugPerformance" in director_source
    assert "playAvatarMotion" in director_source
    assert "playIntroGreetingHug" in avatar_source
    assert "runIntroGiftHeartPerformance" in director_source
    assert "playIntroGiftHeart" in director_source
    assert "showIntroGiftHeart" in director_source
    assert "releaseIntroGreetingHugPerformance" not in director_source
    assert "releaseIntroGreetingHug" not in director_source
    assert "holdAfterSettle" not in director_source
    assert "Promise.all([" in director_source
    assert "this.speakGuideLine(greetingReplyText" in director_source
    assert "Param74" not in director_source
    assert "Param75" not in director_source
    assert "Param77" not in director_source
    assert "Param90" not in director_source
    assert "Param91" not in director_source
    assert "Param92" not in director_source
    assert "Param93" not in director_source
    assert "Param95" not in director_source
    assert "Param96" not in director_source


def test_yui_intro_avatar_actions_respect_reduced_motion():
    director_source = read_director_source()
    avatar_source = Path("static/tutorial/avatar/yui-stage.js").read_text(encoding="utf-8")

    assert "shouldReduceTutorialMotion()" in director_source
    assert "prefers-reduced-motion: reduce" in director_source
    assert "reducedMotion: this.shouldReduceTutorialMotion()" in director_source
    assert "approachMs: reducedMotion ? 0" in avatar_source
    assert "durationMs: reducedMotion ? 0" in avatar_source


def test_yui_plugin_dashboard_corner_peek_uses_adapter_and_releases_on_close():
    director_source = read_director_source()
    avatar_source = Path("static/tutorial/avatar/yui-stage.js").read_text(encoding="utf-8")
    performance_source = Path("static/avatar/avatar-performance-stage.js").read_text(encoding="utf-8")

    assert "class Live2DAvatarCornerPeekSession" in avatar_source
    assert "startAvatarCornerPeek: startAvatarCornerPeek" in avatar_source
    assert "startPluginDashboardCornerPeek: startPluginDashboardCornerPeek" in avatar_source
    assert "Live2DPluginDashboardCornerSession: Live2DAvatarCornerPeekSession" in avatar_source
    assert "YUI_PLUGIN_DASHBOARD_FRAME_CAPABILITIES = Object.freeze(['frame'])" in avatar_source
    assert "home-yui-guide-plugin-dashboard-corner" in avatar_source
    assert "readModelAlpha" in avatar_source
    assert "writeModelAlpha" in avatar_source
    assert "function resolveAvatarCornerPeekRotationDegrees(position)" in avatar_source
    assert "return 135;" in avatar_source
    assert "return -135;" in avatar_source
    assert "return 45;" in avatar_source
    assert "return -45;" in avatar_source
    assert "AVATAR_CORNER_PEEK_EDGE_INSET_RATIO = 0.18" in avatar_source
    assert "AVATAR_CORNER_PEEK_REGION_HEIGHT_RATIO = 0.36" in avatar_source
    assert "PLUGIN_DASHBOARD_CORNER_ELEVATED_Z_INDEX = '2147483647'" in avatar_source
    assert "elevateContainerZIndex" in avatar_source
    assert "restoreContainerZIndex" in avatar_source
    assert "this.container.style.zIndex = PLUGIN_DASHBOARD_CORNER_ELEVATED_Z_INDEX" in avatar_source
    assert "this.container.style.zIndex = this.originalContainerZIndex || ''" in avatar_source
    source_order = [
        avatar_source.index("this.phase = 'hold'"),
        avatar_source.index("this.elevateContainerZIndex()", avatar_source.index("this.phase = 'hold'")),
    ]
    assert source_order == sorted(source_order)
    assert "const isLeft = this.targetPosition === 'bottom-left' || this.targetPosition === 'top-left';" in avatar_source
    assert "const isTop = this.targetPosition === 'top-right' || this.targetPosition === 'top-left';" in avatar_source
    for position in ("bottom-right", "bottom-left", "top-right", "top-left"):
        assert position in avatar_source
    assert "const rotationDelta = resolveAvatarCornerPeekRotationDegrees(this.targetPosition) * Math.PI / 180;" in avatar_source
    assert "const rotatedPeekRegion = this.resolveRotatedRectOffset(peekRegion, rotationDelta)" in avatar_source
    assert "x: desiredLeft - rotatedPeekRegion.left" in avatar_source
    assert "y: desiredTop - rotatedPeekRegion.top" in avatar_source
    assert "PLUGIN_DASHBOARD_CORNER_BOTTOM_OVERHANG_PX" not in avatar_source
    assert "PLUGIN_DASHBOARD_CORNER_RIGHT_PADDING_PX" not in avatar_source
    assert "PLUGIN_DASHBOARD_CORNER_SCALE" not in avatar_source
    assert "cornerScale" not in avatar_source
    assert "scaleX: base.scaleX," in avatar_source
    assert "scaleY: base.scaleY," in avatar_source
    assert "rotation: base.rotation + rotationDelta" in avatar_source
    assert "this.blendFrame(this.cornerHiddenFrame, this.cornerFrame, progress)" in avatar_source
    assert "this.applyFrame(\n                    this.cornerFrame," in avatar_source
    assert "this.applyFrame(\n                    this.initialModelFrame," in avatar_source
    assert "await activeAvatarCornerPeekSession.stop('replaced')" in avatar_source

    assert "await avatarStageApi.startPluginDashboardCornerPeek({" in director_source
    assert "async startPluginDashboardCornerPeekPerformance" not in director_source
    assert "async startAvatarCornerPeekPerformance(options)" in director_source
    assert "return await api.startAvatarCornerPeek({" in director_source
    assert "position: normalizedOptions.position" in director_source
    assert "this.startAvatarCornerPeekPerformance({" in director_source
    assert "position: cue.position" in director_source
    assert "Number.isFinite(Number(cue.duration))" in director_source
    assert "this.stopPluginDashboardCornerPeekPerformance(this.takeoverTopPeekHandle, 'termination_cleanup')" in director_source
    assert "this.stopPluginDashboardCornerPeekPerformance(this.takeoverTopPeekHandle, 'destroy')" in director_source
    assert "this.takeoverTopPeekHandle = null" in director_source
    assert "async stopPluginDashboardCornerPeekPerformance(handle, reason)" in director_source
    assert "async stopAvatarCornerPeekPerformance(handle, reason)" in director_source
    assert "async stopAvatarStandInPerformance(reason)" in director_source
    assert "await handle.stop(reason || 'plugin_dashboard_closed')" in director_source
    assert "await this.stopAvatarCornerPeekPerformance(handle, reason || 'avatar_standin_clear')" in director_source
    assert "isCancelled: () => runId !== this.sceneRunId || this.isStopping()" in director_source
    assert "reducedMotion: this.shouldReduceTutorialMotion()" in director_source

    assert "AvatarCornerPeek" not in performance_source
    assert "PluginDashboardCorner" not in performance_source
    assert "plugin-dashboard" not in performance_source


def test_yui_settings_peek_second_line_triggers_panic_session_with_real_model_params():
    director_source = read_director_source()
    avatar_source = Path("static/tutorial/avatar/yui-stage.js").read_text(encoding="utf-8")
    performance_source = Path("static/avatar/avatar-performance-stage.js").read_text(encoding="utf-8")

    assert "class Live2DSettingsPeekPanicSession" in avatar_source
    assert "playSettingsPeekPanic: playSettingsPeekPanic" in avatar_source
    assert "computeSettingsPeekPanicPose" in avatar_source
    assert "home-yui-guide-settings-panic" in avatar_source
    assert "const YUI_INTRO_VOICE_LOOK_AT_CAPABILITIES = Object.freeze(['lookAt']);" in avatar_source
    assert "const YUI_SETTINGS_PEEK_PANIC_WITH_CURSOR_LOOK_AT_CAPABILITIES = Object.freeze(['frame', 'params', 'expression']);" in avatar_source
    assert "this.performanceLockCapabilities = Array.isArray(normalizedOptions.performanceLockCapabilities)" in avatar_source
    assert ": YUI_INTRO_VOICE_LOOK_AT_CAPABILITIES.slice();" in avatar_source
    assert "preserveCursorLookAt: normalizedOptions.preserveCursorLookAt !== false" in avatar_source
    assert "this.preserveCursorLookAt = normalizedOptions.preserveCursorLookAt !== false;" in avatar_source
    assert "window.nekoYuiGuideIntroVoiceLookAtActive === true" in avatar_source
    assert "YUI_SETTINGS_PEEK_PANIC_WITH_CURSOR_LOOK_AT_CAPABILITIES.slice()" in avatar_source
    assert "Param72" in avatar_source
    assert "Param73" in avatar_source
    assert "Param69" in avatar_source
    assert "Param83" in avatar_source
    assert "Param85" in avatar_source
    assert "Param90" in avatar_source
    assert "Param91" in avatar_source
    assert "Param92" in avatar_source
    assert "Param93" in avatar_source
    assert "Param95" in avatar_source
    assert "Param96" in avatar_source

    assert "async runSettingsPeekPanicPerformance(options)" in director_source
    assert "this.runSettingsPeekPanicPerformance({" in director_source
    assert "runId: runId," in director_source
    assert "targetRect: settingsPeekPanicMotionTargetRect" in director_source
    assert "settingsPeekPanicMotionTargetRect = motionRect || null;" in director_source
    assert "const ghostCursorLookAtHandle = await this.startGhostCursorLookAtPerformance" in director_source
    assert "await this.stopIntroVoiceCursorLookAtPerformance(\n                    ghostCursorLookAtHandle,\n                    'settings_peek_complete'" in director_source
    assert "async runTakeoverKeyboardControlSequence(step, performance, runId)" in director_source
    assert "await this.stopIntroVoiceCursorLookAtPerformance(\n                    ghostCursorLookAtHandle,\n                    'takeover_keyboard_control_complete'" in director_source

    assert "SettingsPeekPanic" not in performance_source
    assert "settings-panic" not in performance_source


def test_yui_interrupt_sessions_keep_scope_in_home_adapter_and_gate_runtime_reentry():
    director_source = read_director_source()
    avatar_source = Path("static/tutorial/avatar/yui-stage.js").read_text(encoding="utf-8")
    performance_source = Path("static/avatar/avatar-performance-stage.js").read_text(encoding="utf-8")

    assert ": ['frame', 'params'];" in avatar_source
    assert "guideInterruptPresentationActive = true;" in director_source
    assert "guideInterruptPresentationActive = false;" in director_source
    assert "allowDuringInterrupt" in director_source
    assert "if (this.guideInterruptPresentationActive) {" in director_source
    assert "this.emotionBridge.clear();" in director_source
    assert "startGuideMouthMotion(voiceKey, options)" in director_source
    assert "this.applyGuideEmotion(performance.emotion || 'surprised', {" in director_source
    assert "this.applyGuideEmotion(performance.emotion || 'angry', {" in director_source
    assert "return null;" in director_source
    assert "restoreCurrentScenePresentation(options)" in director_source

    assert "home-yui-guide-interrupt-resist" in avatar_source
    assert "home-yui-guide-angry-exit" in avatar_source

    assert "guideInterruptPresentationActive" not in performance_source
    assert "interrupt-resist" not in performance_source
    assert "angry-exit" not in performance_source


def test_target_page_templates_load_yui_runtime_stack_before_tutorial_manager():
    for template_path in (
        "templates/api_key_settings.html",
        "templates/memory_browser.html",
    ):
        source = Path(template_path).read_text(encoding="utf-8")
        positions = [
            _script_tag_position(source, name)
            for name in (
                *_YUI_RUNTIME_SCRIPTS,
                "tutorial/core/skip-controller.js",
                "tutorial/avatar/reload-controller.js",
                "tutorial/core/universal-manager.js",
            )
        ]
        assert positions == sorted(positions), template_path
        _stylesheet_tag_position(source, "yui-guide.css")


def test_legacy_tutorial_pages_use_separate_page_tutorial_runtime():
    for template_path in (
        "templates/live2d_emotion_manager.html",
        "templates/mmd_emotion_manager.html",
        "templates/vrm_emotion_manager.html",
        "templates/model_manager.html",
        "templates/live2d_parameter_editor.html",
        "templates/character_card_manager.html",
        "templates/voice_clone.html",
    ):
        source = Path(template_path).read_text(encoding="utf-8")
        assert "tutorial/core/universal-manager.js" not in source
        assert "driver.min.js" in source
        assert "driver.min.css" in source
        assert "tutorial-styles.css" in source
        assert "tutorial/core/page-tutorial-manager.js" in source
        assert "initPageTutorialManager" in source

    for template_path in (
        "templates/api_key_settings.html",
        "templates/memory_browser.html",
    ):
        source = Path(template_path).read_text(encoding="utf-8")
        assert "tutorial/core/universal-manager.js" in source
        assert "driver.min.js" in source
        assert "driver.min.css" in source
        assert "tutorial-styles.css" in source
        assert "tutorial/core/page-tutorial-manager.js" in source
        assert "initPageTutorialManager" in source


def test_restored_page_tutorial_helpers_use_static_asset_version():
    for template_path in (
        "templates/live2d_emotion_manager.html",
        "templates/mmd_emotion_manager.html",
        "templates/vrm_emotion_manager.html",
        "templates/model_manager.html",
        "templates/live2d_parameter_editor.html",
        "templates/character_card_manager.html",
        "templates/voice_clone.html",
    ):
        source = Path(template_path).read_text(encoding="utf-8")
        for helper_path in (
            "/static/tutorial/core/skip-controller.js",
            "/static/tutorial/avatar/reload-controller.js",
            "/static/tutorial/core/lifecycle-state-store.js",
        ):
            assert f'{helper_path}?v={{' in source, (template_path, helper_path)
            assert f'src="{helper_path}"></script>' not in source, (template_path, helper_path)

def test_pages_router_static_asset_version_tracks_tutorial_runtime_modules():
    source = Path("main_routers/pages_router.py").read_text(encoding="utf-8")

    assert "_TUTORIAL_RUNTIME_ASSET_PATHS" in source
    assert '"**/*.js", "**/*.json"' in source
    assert "*_TUTORIAL_RUNTIME_ASSET_PATHS" in source

    from main_routers import pages_router

    tracked_paths = {
        path.relative_to(Path("main_routers/pages_router.py").resolve().parent.parent).as_posix()
        for path in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    }
    assert "static/tutorial/yui-guide/days/day6-agent-guide.js" in tracked_paths
    assert "static/tutorial/core/operation-registry.js" in tracked_paths
    assert "static/tutorial/visual/resistance-controllers.js" in tracked_paths
    assert "static/tutorial/icebreaker/icebreaker_scripts.json" in tracked_paths
    assert "static/tutorial/avatar/yui-standin.js" in tracked_paths
    assert "static/tutorial/avatar/standin-controller.js" in tracked_paths
    assert "static/tutorial/core/page-tutorial-manager.js" in tracked_paths
    assert "static/css/tutorial-styles.css" in tracked_paths
    assert "static/libs/driver.min.js" in tracked_paths
    assert "static/libs/driver.min.css" in tracked_paths
    assert "static/live2d/live2d-init.js" in tracked_paths
    interpage_parts = sorted(Path("static/app/app-interpage").glob("*.js"))
    assert interpage_parts
    assert {
        path.as_posix() for path in interpage_parts
    }.issubset(tracked_paths)
    assert "static/live2d/live2d-interaction.js" in tracked_paths


@pytest.mark.asyncio
async def test_restored_tutorial_routes_supply_static_asset_version_to_template():
    """Restored page-tutorial routes must inject ``_static_assets_ctx()``.

    The templates already reference ``?v={{ static_asset_version|default('0', true) }}``
    for the Driver / page-tutorial runtime, but the version only cache-busts if
    the route actually supplies it. ``voice_clone`` and ``live2d_parameter_editor``
    previously rendered with just ``{"request": request}``, so every tutorial
    asset on those two pages pinned to ``?v=0`` forever while the other restored
    pages got versioned URLs. Guard the context here so the gap can't reappear.
    """
    from types import SimpleNamespace

    from main_routers import pages_router
    from main_routers.pages_router import live2d_parameter_editor, voice_clone_page
    from main_routers.shared_state import init_shared_state

    class _DummyTemplates:
        def TemplateResponse(self, template_name, context):
            return {"template_name": template_name, "context": context}

    init_shared_state(
        role_state={},
        steamworks=None,
        templates=_DummyTemplates(),
        config_manager=SimpleNamespace(),
        logger=None,
        initialize_character_data=None,
    )

    expected_version = pages_router._static_assets_ctx()["static_asset_version"]
    request = SimpleNamespace()

    for route in (voice_clone_page, live2d_parameter_editor):
        rendered = await route(request)
        context = rendered["context"]
        assert "static_asset_version" in context, route.__name__
        assert context["static_asset_version"] == expected_version, route.__name__


def test_react_chat_templates_use_react_asset_version_for_chat_bundle():
    react_version = "{{ react_chat_asset_version }}"
    static_version = "{{ static_asset_version }}"
    react_assets = (
        "/static/react/neko-chat/neko-chat-window.css",
        "/static/react/neko-chat/neko-chat-window.iife.js",
        "/static/app/app-chat-adapter.js",
        "/static/app/app-buttons.js",
    )
    react_host_assets = tuple(
        "/static/app/app-react-chat-window/" + path.name
        for path in sorted(Path("static/app/app-react-chat-window").glob("*.js"))
    )
    interpage_assets = tuple(
        "/static/app/app-interpage/" + path.name
        for path in sorted(Path("static/app/app-interpage").glob("*.js"))
    )
    assert react_host_assets
    assert interpage_assets

    for template_path in ("templates/index.html", "templates/chat.html"):
        source = Path(template_path).read_text(encoding="utf-8")
        assert "window.__NEKO_REACT_CHAT_ASSET_VERSION__={{ react_chat_asset_version | tojson }};" in source
        for asset_path in interpage_assets:
            assert f"{asset_path}?v={static_version}" in source
            assert f"{asset_path}?v={react_version}" not in source
        for asset_path in (*react_assets, *react_host_assets):
            assert f"{asset_path}?v={react_version}" in source
            assert f"{asset_path}?v={static_version}" not in source


def test_pages_router_react_chat_asset_version_tracks_avatar_tool_resources():
    source = Path("main_routers/pages_router.py").read_text(encoding="utf-8")

    assert '*sorted(_PROJECT_ROOT.glob("static/assets/avatar-tools/**/*.png"))' in source
    assert '*sorted(_PROJECT_ROOT.glob("static/sounds/avatar-tools/**/*.mp3"))' in source
    assert '_PROJECT_ROOT / "static/app/app-chat-adapter.js"' in source
    assert '_PROJECT_ROOT / "static/app/app-buttons.js"' in source
    assert 'glob("static/app/app-react-chat-window/*.js")' in source

    for legacy_name in ("chat_sugar", "cat_claw", "chat_hammer", "cat_moneny", "_cursor.png"):
        assert legacy_name not in source


def test_avatar_tool_resource_references_are_canonical_and_exist():
    source_paths = (
        Path("frontend/react-neko-chat/src/avatar-tools/catalog.ts"),
        Path("frontend/react-neko-chat/src/AvatarToolQuickbar.tsx"),
        Path("static/js/card_maker.js"),
        Path("docs/design/avatar-tool-rock-paper-scissors-frontend-implementation.md"),
    )
    sources = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    resource_urls = set(re.findall(
        r"/static/(?:assets|sounds)/avatar-tools/[a-z0-9_./-]+\.(?:png|mp3)",
        sources,
    ))

    assert resource_urls
    for resource_url in resource_urls:
        assert Path(resource_url.removeprefix("/")).is_file(), resource_url

    for legacy_name in (
        "/sugar/",
        "/claw/",
        "chat_sugar",
        "cat_claw",
        "chat_hammer",
        "cat_moneny",
        "rps_",
        "_cursor.png",
        "edit_tool_unified.png",
    ):
        assert legacy_name not in sources

    for bundle_path in (
        Path("static/react/neko-chat/neko-chat-window.iife.js"),
        Path("static/react/neko-chat/neko-chat-window.es.js"),
    ):
        if bundle_path.is_file():
            bundle = bundle_path.read_text(encoding="utf-8")
            assert "/static/icons/chat_sugar" not in bundle
            assert "/static/icons/cat_claw" not in bundle
            assert "/static/icons/chat_hammer" not in bundle
            assert "/static/icons/edit_tool_unified.png" not in bundle


def test_home_yui_guide_does_not_route_to_steam_workshop():
    yui_source = Path("static/tutorial/yui-guide/steps.js").read_text(encoding="utf-8")
    tutorial_source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")

    assert "handoff_steam_workshop" not in yui_source
    assert "/steam_workshop_manager" not in yui_source
    assert "yuiGuideSceneId: 'handoff_steam_workshop'" not in tutorial_source
    assert "#${p}-menu-steam-workshop" not in tutorial_source


def test_home_tutorial_reset_also_clears_backend_prompt_state():
    tutorial_source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")

    assert "/api/tutorial-prompt/reset" in tutorial_source


def test_tutorial_destroy_does_not_mark_seen_but_skip_does():
    tutorial_source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")

    assert "if (endMeta.reason === 'destroy')" in tutorial_source
    assert "if (endMeta.reason === 'skip')" in tutorial_source
    assert "neko:tutorial-ended-without-completion" in tutorial_source
    assert "neko:tutorial-skipped" in tutorial_source


def test_universal_tutorial_manager_keeps_page_normalization_without_legacy_step_bridge():
    source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")

    for expected in (
        "getYuiGuidePageKey(page = this.currentPage)",
        "return 'api_key';",
        "return pageKey === 'home' && this.isAvatarFloatingGuideRoundRegistered(1);",
    ):
        assert expected in source

    for obsolete in (
        "getPendingYuiGuideResumeScene",
        "getDirectYuiGuideSceneIdsForCurrentPage",
        "startYuiGuideSceneSequence",
        "callYuiGuideDirector",
        "notifyYuiGuideStepEnter",
        "notifyYuiGuideStepLeave",
        "applyYuiGuideResumeScene",
        "getYuiGuideMappedSceneIds",
        "yuiGuideSceneId: 'api_key_intro'",
        "yuiGuideSceneId: 'memory_browser_intro'",
    ):
        assert obsolete not in source

def test_legacy_character_card_manager_tutorial_steps_live_in_page_runtime():
    source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")
    page_source = Path("static/tutorial/core/page-tutorial-manager.js").read_text(encoding="utf-8")
    template_source = Path("templates/character_card_manager.html").read_text(encoding="utf-8")

    for obsolete in (
        "getCharaManagerSteps",
        "waitForCatgirlCards",
        "prepareCharaManagerForTutorial",
        "cleanupCharaManagerTutorialIds",
        "path.includes('character_card_manager')",
    ):
        assert obsolete not in source

    assert "getCharaManagerSteps()" in page_source
    assert "path.includes('character_card_manager')" in page_source
    assert "waitForCharacterCards" in page_source
    assert "tutorial/core/universal-manager.js" not in template_source
    assert "driver.min.js" in template_source
    assert "tutorial-styles.css" in template_source
    assert "tutorial/core/page-tutorial-manager.js" in template_source

def test_legacy_character_card_manager_tutorial_prepare_helpers_are_removed():
    source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")

    for obsolete in (
        "async prepareCharaManagerForTutorial()",
        "cleanupCharaManagerTutorialIds()",
        "async _ensureCharaManagerExpanded()",
        "async onStepChange()",
        ".catgirl-block",
        ".catgirl-details",
        ".catgirl-expand",
    ):
        assert obsolete not in source

def test_universal_tutorial_manager_uses_scoped_scroll_lock_only():
    source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")

    for expected in (
        "_tutorialScrollBlockOptions = { capture: true, passive: false }",
        "blockTutorialScrollEvent(event)",
        "event.preventDefault();",
        "this._tutorialScrollBlockResources.addEventListener(window, 'wheel'",
        "this._tutorialScrollBlockResources.addEventListener(window, 'touchmove'",
        "this._tutorialScrollBlockResources.destroy();",
    ):
        assert expected in source

    for obsolete in (
        "blockTutorialPointerEvent",
        "blockNekoTutorialClickEvent",
        "isTutorialControlEventTarget",
        "window.addEventListener('wheel'",
        "window.removeEventListener('wheel'",
    ):
        assert obsolete not in source

def test_universal_tutorial_manager_does_not_install_legacy_page_click_blockers():
    source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")

    for obsolete in (
        "blockTutorialPointerEvent(event)",
        "blockTutorialPointerEvents()",
        "blockNekoTutorialClickEvent(event)",
        "isTutorialControlEventTarget(target)",
        "driver-popover",
        "driver-overlay",
        "_tutorialPointerBlockHandler",
        "_nekoTutorialClickBlockHandler",
    ):
        assert obsolete not in source

def test_universal_tutorial_manager_no_longer_has_chara_manager_input_blocker_path():
    source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")

    assert "if (this.currentPage !== 'chara_manager') return;" not in source
    assert "blockTutorialPointerEvent(event)" not in source

def test_character_card_manager_master_profile_arrow_uses_bubble_style():
    template_source = Path("templates/character_card_manager.html").read_text(encoding="utf-8")
    css_source = Path("static/css/character_card_manager.css").read_text(encoding="utf-8")

    for expected in (
        "class=\"master-profile-arrow-bubble\"",
        "class=\"master-profile-arrow-symbol\"",
    ):
        assert expected in template_source

    for expected in (
        ".master-profile-arrow-bubble",
        ".master-profile-arrow-symbol",
        ".master-profile-header.open .master-profile-arrow-bubble",
    ):
        assert expected in css_source


def test_character_card_manager_cloudsave_button_uses_icon_badge():
    template_source = Path("templates/character_card_manager.html").read_text(encoding="utf-8")
    css_source = Path("static/css/character_card_manager.css").read_text(encoding="utf-8")

    assert "class=\"sidebar-cloudsave-icon\"" in template_source
    for expected in (
        ".sidebar-cloudsave-icon",
        ".sidebar-cloudsave-btn:focus-visible",
        "[data-theme=\"dark\"] .sidebar-cloudsave-icon",
    ):
        assert expected in css_source


def test_home_yui_guide_avatar_override_does_not_persist_tutorial_model():
    tutorial_source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")
    avatar_reload_source = Path("static/tutorial/avatar/reload-controller.js").read_text(encoding="utf-8")
    interpage_source = read_js_parts(Path("static/app/app-interpage"))
    app_ui_source = read_js_parts(Path("static/app/app-ui"))
    live2d_init_source = Path("static/live2d/live2d-init.js").read_text(encoding="utf-8")
    live2d_model_source = Path("static/live2d/live2d-model.js").read_text(encoding="utf-8")
    round_prelude_source = Path("static/tutorial/core/round-prelude-controller.js").read_text(encoding="utf-8")
    visual_runtime_source = Path("static/tutorial/core/visual-runtime.js").read_text(encoding="utf-8")
    resistance_source = Path("static/tutorial/visual/resistance-controllers.js").read_text(encoding="utf-8")
    director_source = read_director_source()

    begin_start = avatar_reload_source.index("beginOverride(")
    restore_start = avatar_reload_source.index("restoreOverride()")
    restore_end = avatar_reload_source.index("window.TutorialAvatarReloadController", restore_start)
    begin_block = avatar_reload_source[begin_start:restore_start]
    restore_block = avatar_reload_source[restore_start:restore_end]

    assert "saveTutorialModelPayload" not in begin_block
    assert "saveTutorialModelPayload" not in restore_block
    assert "await this.reloadModel(currentName, tutorialModelPayload," in begin_block
    assert "temporary: true" in begin_block
    assert "deferRevealPrepared" in begin_block
    assert "this.setPreparing(true);" in begin_block
    assert begin_block.count("this.setPreparing(true);") == 2
    reload_call_index = begin_block.index("await this.reloadModel(currentName, tutorialModelPayload,")
    assert begin_block.index("this.setPreparing(true);") < begin_block.index(
        "await this.reloadModel(currentName, tutorialModelPayload,"
    )
    assert begin_block.rindex("this.setPreparing(true);") > reload_call_index
    assert begin_block.rindex("this.setPreparing(true);") < begin_block.index(
        "this.applyIdentityOverride({"
    )
    assert "fadeOutBeforeRestore" not in avatar_reload_source
    assert "fadeOutTutorialLive2dBeforeRestore" not in tutorial_source
    avatar_interaction_restore_block = tutorial_source.split(
        "restoreAvatarFloatingModelInteractionState(reason = 'tutorial-ended') {",
        1,
    )[1].split("applyTutorialChatIdentityOverride", 1)[0]
    assert "snapshotAvatarFloatingModelInteractionState(reason = 'tutorial-started')" in tutorial_source
    assert "this.snapshotAvatarFloatingModelInteractionState('tutorial-start');" in tutorial_source
    assert "this.snapshotAvatarFloatingModelInteractionState('avatar-floating-guide-start');" in tutorial_source
    assert "const snapshot = this._avatarFloatingModelLockSnapshot" in avatar_interaction_restore_block
    assert "if (!snapshot) {" in avatar_interaction_restore_block
    assert "return;" in avatar_interaction_restore_block
    assert "window.live2dManager.setLocked(!!snapshot.live2d, { updateFloatingButtons: false });" in avatar_interaction_restore_block
    assert "window.vrmManager.core.setLocked(!!snapshot.vrm);" in avatar_interaction_restore_block
    assert "window.mmdManager.core.setLocked(!!snapshot.mmd);" in avatar_interaction_restore_block
    assert "window.pngtuberManager.setLocked(!!snapshot.pngtuber, { updateFloatingButtons: false });" in avatar_interaction_restore_block
    assert "modelType === 'live3d'" in tutorial_source
    assert "live3d_sub_type" in tutorial_source
    assert "pointerEvents: {" in tutorial_source
    assert "vrmCanvas: readPointerEvents('vrm-canvas')" in tutorial_source
    assert "mmdCanvas: readPointerEvents('mmd-canvas')" in tutorial_source
    assert "const hasSnapshotPointerEvents = snapshot.pointerEvents" in avatar_interaction_restore_block
    assert "const snapshotPointerEvents = hasSnapshotPointerEvents ? snapshot.pointerEvents[pointerKey] : null;" in avatar_interaction_restore_block
    assert "function restoreAvatarPointerEvents(element, elementId, snapshotPointerEvents, hasSnapshotPointerEvents)" in avatar_interaction_restore_block
    assert "if (hasSnapshotPointerEvents && snapshotPointerEvents) {" in avatar_interaction_restore_block
    assert "element.style.pointerEvents = snapshotPointerEvents;" in avatar_interaction_restore_block
    assert "element.style.pointerEvents = snapshot.pointerEvents[pointerKey] || '';" not in avatar_interaction_restore_block
    assert "activePrefix === 'live2d' || activePrefix === 'pngtuber'" in avatar_interaction_restore_block
    assert "const isActiveAvatarContainer = elementId === `${activePrefix}-container`;" in avatar_interaction_restore_block
    assert "if (isActiveAvatarContainer && (activePrefix === 'live2d' || activePrefix === 'pngtuber')) {" in avatar_interaction_restore_block
    assert "element.style.setProperty('pointer-events', 'none', 'important');" in avatar_interaction_restore_block
    assert "restoreAvatarPointerEvents(element, elementId, snapshotPointerEvents, hasSnapshotPointerEvents);" in avatar_interaction_restore_block
    assert "element.style.removeProperty('pointer-events');" in avatar_interaction_restore_block
    assert "element.style.pointerEvents = activeLocked ? 'none' : 'auto';" in avatar_interaction_restore_block
    assert avatar_interaction_restore_block.index("const isActiveAvatarContainer = elementId === `${activePrefix}-container`;") < avatar_interaction_restore_block.index("if (hasSnapshotPointerEvents && snapshotPointerEvents) {")
    assert "this.restoreAvatarFloatingModelInteractionState('teardown-early');" in tutorial_source
    assert ".then(() => this.clearTutorialYuiLive2dRuntimeResidue('tutorial-avatar-restored'))" in tutorial_source
    # 最终恢复统一按业务形态分流：普通模型回放交互快照，猫咪态重走 goodbye 链路。
    assert ".then(() => this.restoreAvatarFloatingModelStateAfterTutorial())" in tutorial_source
    assert tutorial_source.index(".then(() => this.restoreTutorialAvatarOverride())") < tutorial_source.index(
        ".then(() => this.clearTutorialYuiLive2dRuntimeResidue('tutorial-avatar-restored'))"
    )
    assert tutorial_source.index(
        ".then(() => this.clearTutorialYuiLive2dRuntimeResidue('tutorial-avatar-restored'))"
    ) < tutorial_source.index(".then(() => this.restoreAvatarFloatingModelStateAfterTutorial())")
    assert "async clearTutorialYuiLive2dRuntimeResidue(reason = '')" in tutorial_source
    assert "this.isCurrentRuntimeModelLive2d()" in tutorial_source
    assert "await manager.removeModel({ skipCloseWindows: true });" in tutorial_source
    assert "manager._lastLoadedModelPath = null;" in tutorial_source
    assert "manager.modelRootPath = null;" in tutorial_source
    assert "manager.modelName = null;" in tutorial_source
    assert "this.hideTutorialLive2dRuntimeSurfaceAfterResidueClear();" in tutorial_source
    assert "clearTutorialLive2dPreparingStyles()" in tutorial_source
    assert "element.style.removeProperty('opacity');" in tutorial_source
    assert "element.style.removeProperty('visibility');" in tutorial_source
    assert "element.style.removeProperty('pointer-events');" in tutorial_source
    reload_tutorial_block = tutorial_source.split("async reloadTutorialModel(", 1)[1].split(
        "setTutorialLive2dPreparing(",
        1,
    )[0]
    assert "reloadOptions.temporaryConfig = this.buildTutorialTemporaryModelConfig(payload);" in reload_tutorial_block
    assert "reloadOptions.skipIdleRestore = true;" in reload_tutorial_block
    assert "reloadOptions.skipPersistentExpressions = true;" in reload_tutorial_block
    assert "reloadOptions.deferRevealPrepared = deferRevealPrepared;" in reload_tutorial_block
    assert "await window.handleModelReload(lanlanName, reloadOptions);" in reload_tutorial_block
    assert "临时模型热切换失败，改用直接 Live2D 加载" in reload_tutorial_block
    assert "if (!useTemporaryConfig)" in reload_tutorial_block
    assert "throw error;" in reload_tutorial_block
    assert "await this.loadTemporaryTutorialLive2dModel(payload, {" in reload_tutorial_block
    assert "waitForLive2dModelLoadIdle(maxWaitTime = 30000)" in tutorial_source
    assert "waitForLive2dModelLoadIdleOrThrow(reason = '', maxWaitTime = 30000)" in tutorial_source
    assert "manager._isLoadingModel === true" in tutorial_source
    assert "['preparing', 'applying', 'settling'].includes" in tutorial_source
    assert "await this.waitForLive2dModelLoadIdleOrThrow('before-handle-model-reload');" in reload_tutorial_block
    assert "await this.waitForLive2dModelLoadIdleOrThrow('before-direct-tutorial-load');" in reload_tutorial_block
    assert reload_tutorial_block.index(
        "await this.waitForLive2dModelLoadIdleOrThrow('before-handle-model-reload');"
    ) < reload_tutorial_block.index("await window.handleModelReload(lanlanName, reloadOptions);")
    assert "const remainingMs = Math.max(0, maxWaitTime - (Date.now() - startedAt));" in tutorial_source
    assert "const live2dIdle = await this.waitForLive2dModelLoadIdle(remainingMs);" in tutorial_source
    assert "return true;" in tutorial_source
    end_request_block = tutorial_source.split("requestTutorialEnd(reason = 'destroy') {", 1)[1].split(
        "requestTutorialDestroy(reason = 'destroy') {",
        1,
    )[0]
    assert "this.clearAllTutorialLifecycles(reason);" in end_request_block
    assert "return this.onTutorialEnd();" in end_request_block
    assert "this.driver" not in end_request_block
    reset_block = tutorial_source.split("resetTutorialStartState() {", 1)[1].split(
        "emitTutorialStarted(",
        1,
    )[0]
    teardown_block = tutorial_source.split("_teardownTutorialUI() {", 1)[1].split(
        "try {\n            this.hideSkipButton();",
        1,
    )[0]
    assert "this.revealTutorialLive2dPrepared();" in reset_block
    assert "this.revealTutorialLive2dPrepared();" in teardown_block
    assert "this.revealPrepared();" in restore_block
    assert "live2d: this.tutorialModelName" in begin_block
    assert "TUTORIAL_YUI_LIVE2D_MODEL_PATH = '/static/yui-origin/yui-origin.model3.json'" in tutorial_source
    assert "AVATAR_FLOATING_GUIDE_ROUND_COUNT = 7" in tutorial_source
    launch_block = tutorial_source.split("const launchTutorial = () => {", 1)[1].split(
        "if (this.isI18nReady())",
        1,
    )[0]
    assert "this.startTutorial();" in launch_block
    assert "this.shouldStartHomeAvatarFloatingGuideRound()" in launch_block
    assert "const round = this.getHomeAvatarFloatingGuideLaunchRound();" in launch_block
    assert "this.startAvatarFloatingGuideRound(round, { source })" in launch_block
    assert "shouldStartHomeAvatarFloatingGuideRound() {" in tutorial_source
    assert "getHomeAvatarFloatingGuideStartRound(options = {})" in tutorial_source
    assert "candidates.push(state.pendingRound, state.manualResetRound, 1);" in tutorial_source
    start_tutorial_block = tutorial_source.split("startTutorial() {", 1)[1].split(
        "resetTutorialStartState() {",
        1,
    )[0]
    assert "this.currentPage === 'home'" in start_tutorial_block
    assert "const round = this.getHomeAvatarFloatingGuideLaunchRound();" in start_tutorial_block
    assert start_tutorial_block.index("const round = this.getHomeAvatarFloatingGuideLaunchRound();") < start_tutorial_block.index(
        "if (!round) {"
    )
    assert start_tutorial_block.index("if (!round) {") < start_tutorial_block.index(
        "this.snapshotAvatarFloatingModelInteractionState('tutorial-start');"
    )
    assert start_tutorial_block.index("this.snapshotAvatarFloatingModelInteractionState('tutorial-start');") < start_tutorial_block.index(
        "this.startAvatarFloatingGuideRound(round, {"
    )
    assert "this.startAvatarFloatingGuideRound(round, {" in start_tutorial_block
    restart_block = tutorial_source.split("async restartCurrentTutorial() {", 1)[1].split(
        "}\n}\n\n// 创建全局实例",
        1,
    )[0]
    assert "const restartRound = this.getHomeAvatarFloatingGuideStartRound({ includeActive: true });" in restart_block
    assert "this.resetAvatarFloatingGuideRoundState(restartRound" in restart_block
    assert "await this.startAvatarFloatingGuideRound(restartRound, { source: 'manual' });" in restart_block
    assert "this.startYuiGuideSceneSequence(sceneIds" not in tutorial_source
    assert "getDirectYuiGuideSceneIdsForCurrentPage" not in tutorial_source
    assert "useYuiOnlyHomeFlow" not in tutorial_source
    start_round_block = tutorial_source.split("async startAvatarFloatingGuideRound(day, options = {})", 1)[1].split(
        "async playAvatarFloatingRoundPrelude",
        1,
    )[0]
    assert "await this.waitForTutorialTeardownSettled('avatar-floating-guide-start');" in start_round_block
    assert "async waitForTutorialTeardownSettled(reason = '')" in tutorial_source
    assert "suppressInitialIdle: true" in tutorial_source
    assert "suppressInitialIdle: skipIdleRestore" in interpage_source
    assert "var skipPersistentExpressions = !!reloadOptions.skipPersistentExpressions;" in interpage_source
    assert "suppressPersistentExpressions: skipPersistentExpressions" in interpage_source
    assert "var frozenScreenPoint = freezePoint ? yuiGuideChatCursorFrozenScreenPoints[freezeKey] : null;" in interpage_source
    assert "if (!targetPoint && !frozenScreenPoint) return false;" in interpage_source
    assert "if (event.origin !== window.location.origin) return;" in interpage_source
    assert "if (!deferRevealPrepared) {" in interpage_source
    assert "live2dContainer2.style.removeProperty('opacity');" in interpage_source
    assert "live2dCanvas2.style.removeProperty('opacity');" in interpage_source
    assert "typeof window.showLive2d === 'function'" in interpage_source
    live2d_show_block = interpage_source.split("if (typeof window.showLive2d === 'function')", 1)[0].rsplit(
        "await window.live2dManager.loadModel(newModelPath,",
        1,
    )[1]
    assert "window.lanlan_config.model_type = newModelType;" in live2d_show_block
    assert "window.lanlan_config.live3d_sub_type = live3dSubType;" in live2d_show_block
    assert "window.live2dManager.resumeRendering();" in interpage_source
    assert "function ensureLive2DRenderActive(reason)" in interpage_source
    assert "ensureLive2DRenderActive('model-reload-live2d');" in interpage_source
    assert "function scheduleLive2DRenderActivation(reason)" in interpage_source
    assert "scheduleLive2DRenderActivation('model-reload-live2d');" in interpage_source
    assert "[80, 300].forEach(function (delayMs)" in interpage_source
    assert "currentModel.visible = true;" in interpage_source
    assert "currentModel.alpha = 1;" in interpage_source
    assert "ticker.start();" in interpage_source
    assert "ticker.update();" in interpage_source
    assert "function restoreLive2DDisplaySurface(reason)" in app_ui_source
    assert "function activateLive2DRenderForDisplay(reason)" in app_ui_source
    assert "function scheduleLive2DDisplayActivation(reason)" in app_ui_source
    assert "restoreLive2DDisplaySurface('show-live2d-fast-path');" in app_ui_source
    assert "scheduleLive2DDisplayActivation('show-live2d-fast-path');" in app_ui_source
    assert "scheduleLive2DDisplayActivation('show-live2d');" in app_ui_source
    restore_live2d_surface_block = app_ui_source.split("function restoreLive2DDisplaySurface(reason)", 1)[1].split(
        "function activateLive2DRenderForDisplay(reason)",
        1,
    )[0]
    assert "shouldPreserveYuiGuideLive2DPreparing()" in app_ui_source
    assert "if (!preserveYuiGuidePreparing) {" in restore_live2d_surface_block
    assert "document.body.classList.remove('yui-guide-live2d-preparing');" in restore_live2d_surface_block
    assert "document.body.classList.remove('yui-guide-return-petal-fade');" in restore_live2d_surface_block
    assert "document.body.style.removeProperty('--yui-guide-return-avatar-opacity');" in restore_live2d_surface_block
    assert "live2dContainer.style.removeProperty('opacity');" in restore_live2d_surface_block
    assert "live2dContainer.style.setProperty('opacity', '1', 'important');" not in restore_live2d_surface_block
    assert "live2dCanvas.style.setProperty('opacity', '1', 'important');" in restore_live2d_surface_block
    assert "live2dCanvas.style.setProperty('visibility', 'visible', 'important');" in restore_live2d_surface_block
    assert "app.renderer.render(app.stage);" in app_ui_source
    assert "function revealInitialLive2DModelWhenUiReady(reason)" in live2d_init_source
    assert "window.showLive2d();" in live2d_init_source
    assert "try {" in live2d_init_source
    assert "return false;" in live2d_init_source.split("window.showLive2d();", 1)[1].split("revealed = true;", 1)[0]
    assert "revealInitialLive2DModelWhenUiReady('initial-live2d-load');" in live2d_init_source
    assert "[0, 50, 150, 300, 600, 1000].forEach((delayMs)" in live2d_init_source
    assert "window.addEventListener('load', reveal, { once: true });" in live2d_init_source
    assert "neko-live2d-model-ready" in live2d_model_source
    assert "this._modelLoadState = 'ready';" in live2d_model_source
    operation_run_block = visual_runtime_source.split("async handleOperationRun(event, context) {", 1)[1].split(
        "async handleCompactToolWheelRotateGalgameIntoCenter",
        1,
    )[0]
    assert "let primaryTarget = Object.prototype.hasOwnProperty.call(event, 'primaryTarget')" in operation_run_block
    assert "if (!primaryTarget) {" in operation_run_block
    assert "primaryTarget = await this.resolveTarget(event.target || legacyScene.target || '', context, 'primary');" in operation_run_block
    plugin_skip_block = resistance_source.split("async handlePluginDashboardSkipRequest(data) {", 1)[1].split(
        "return {",
        1,
    )[0]
    assert "await director.skip('skip', 'skip');" in plugin_skip_block
    assert "await director.requestTermination('skip', 'skip');" not in plugin_skip_block
    assert "console.debug('[YuiGuide] interrupt_resist_light step config missing" in resistance_source
    pagehide_block = director_source.split("onPageHide() {", 1)[1].split(
        "hasOpenSystemDialog()",
        1,
    )[0]
    assert "try {" in pagehide_block
    assert "Promise.resolve(this.tutorialManager.requestTutorialEnd('pagehide')).catch" in pagehide_block
    assert "pagehide tutorial end threw" in pagehide_block
    assert "this.destroy();" in pagehide_block
    assert live2d_model_source.index("this._modelLoadState = 'ready';") < live2d_model_source.index(
        "window.dispatchEvent(new CustomEvent('neko-live2d-model-ready'"
    )
    assert "临时切换 YUI 失败，中止教程" in round_prelude_source
    assert "确认 YUI 模型失败，中止教程" in round_prelude_source
    assert "继续教程" not in round_prelude_source
    assert "const deferRevealPrepared = normalizedOptions.deferRevealPrepared === true;" in round_prelude_source
    assert "if (!deferRevealPrepared) {" in round_prelude_source
    assert "this.ensureVisible(sceneId, {" in round_prelude_source
    assert "deferRevealPrepared: true" in tutorial_source
    assert "revealPrepared: () => this.revealTutorialLive2dPrepared()" in tutorial_source
    ensure_visible_block = tutorial_source.split(
        "async ensureTutorialYuiLive2dVisible(reason = '', options = {}) {",
        1,
    )[1].split("isLive2dModelLoadBusy()", 1)[0]
    assert "const deferRevealPrepared = options && options.deferRevealPrepared === true;" in ensure_visible_block
    assert "if (!deferRevealPrepared) {" in ensure_visible_block
    assert "this.ensureTutorialLive2dRenderActive('ensure-visible-active-yui', {" in ensure_visible_block
    assert "this.ensureTutorialLive2dRenderActive('ensure-visible-after-direct-load', {" in ensure_visible_block
    render_active_block = tutorial_source.split(
        "ensureTutorialLive2dRenderActive(reason = '', options = {}) {",
        1,
    )[1].split("getTutorialLive2dScreenBounds", 1)[0]
    assert "const deferRevealPrepared = options.deferRevealPrepared === true;" in render_active_block
    assert "preservePreparingOpacity: deferRevealPrepared" in render_active_block
    assert "deferRevealPrepared: deferRevealPrepared" in render_active_block
    cooperative_end_block = tutorial_source.split(
        "requestAvatarFloatingGuideCooperativeEnd(reason = 'skip') {",
        1,
    )[1].split("handleDesktopYuiGuideSkipRequest", 1)[0]
    ensure_director_block = tutorial_source.split(
        "ensureYuiGuideDirector() {",
        1,
    )[1].split("isYuiGuideEnabledForPage", 1)[0]
    assert "this.yuiGuideDirector.destroyed || this.yuiGuideDirector.terminationRequested" in ensure_director_block
    assert "this.yuiGuideDirector.destroy();" in ensure_director_block
    assert "this.yuiGuideDirector = null;" in ensure_director_block
    assert "this.setTutorialEndReason(reason);" not in cooperative_end_block
    assert "this.clearPcTutorialGlobalOverlay(reason);" not in cooperative_end_block
    assert "this.invalidateTutorialInteractionApply(reason);" not in cooperative_end_block
    assert "return this.requestTutorialEnd(reason);" in cooperative_end_block
    assert "return this.onTutorialEnd();" not in cooperative_end_block
    clear_pc_overlay_block = tutorial_source.split(
        "clearPcTutorialGlobalOverlay(reason = 'destroy') {",
        1,
    )[1].split("requestTutorialEnd", 1)[0]
    assert clear_pc_overlay_block.index("const lifecycleEndedMessage = {") < clear_pc_overlay_block.index(
        "window.nekoTutorialOverlay.clear({"
    )
    assert "tutorialRunId: tutorialRunId," in clear_pc_overlay_block
    assert "Promise.resolve(clearResult).then" in clear_pc_overlay_block
    assert "window.nekoTutorialOverlay.clear({ reason: rawReason });" in clear_pc_overlay_block
    assert "window.nekoTutorialOverlay.relayToChat(lifecycleEndedMessage);" in clear_pc_overlay_block
    assert "window.nekoTutorialOverlay.relayToPet(lifecycleEndedMessage);" in clear_pc_overlay_block
    assert "window.appInterpage.nekoBroadcastChannel.postMessage(lifecycleEndedMessage);" in clear_pc_overlay_block
    assert clear_pc_overlay_block.rindex("window.localStorage.removeItem('yuiGuidePcOverlayRunId');") > clear_pc_overlay_block.index(
        "window.nekoTutorialOverlay.clear({"
    )
    assert "restorePreviousModelUiAfterFailedSwitch" not in interpage_source
    assert "failed to restore previous model UI after switch failure" not in interpage_source
    assert "ensureTutorialLive2dRenderActive(reason = '', options = {})" in tutorial_source
    assert "restoreTutorialLive2dDisplayState(reason = '', options = {})" in tutorial_source
    assert "this.restoreTutorialLive2dDisplayState(reason, {" in tutorial_source
    assert "document.body.classList.remove('yui-guide-return-petal-fade');" in tutorial_source
    assert "document.body.style.removeProperty('--yui-guide-return-avatar-opacity');" in tutorial_source
    assert "_tutorialLive2dRenderActivationToken" in tutorial_source
    assert "this.ensureTutorialLive2dRenderActive('load-temporary-tutorial-model', {" in tutorial_source
    assert "this.ensureTutorialLive2dRenderActive('ensure-visible-active-yui', {" in tutorial_source
    assert "this.ensureTutorialLive2dRenderActive('ensure-visible-after-direct-load', {" in tutorial_source
    assert "options.scheduleDelayed !== false" in tutorial_source
    assert "activationToken !== this._tutorialLive2dRenderActivationToken" in tutorial_source
    assert "[80, 300].forEach((delayMs)" in tutorial_source
    renderable_block = tutorial_source.split(
        "hasTutorialYuiLive2dRenderableModel(manager = window.live2dManager || null) {",
        1,
    )[1].split("async ensureTutorialYuiLive2dVisible", 1)[0]
    placement_block = tutorial_source.split("async applyTutorialLive2dViewportPlacement()", 1)[1].split(
        "ensureTutorialLive2dViewportPlacementWatcher",
        1,
    )[0]
    assert "isTutorialLive2dModelAttachedToStage(stage, model)" in tutorial_source
    assert "isTutorialLive2dRendererViewReady(app, renderer)" in tutorial_source
    assert "&& !model.destroyed" in renderable_block
    assert "&& internalModel.coreModel" in renderable_block
    assert "&& this.isTutorialLive2dModelAttachedToStage(stage, model)" in renderable_block
    assert "&& this.isTutorialLive2dRendererViewReady(app, renderer)" in renderable_block
    assert "if (!this.hasTutorialYuiLive2dRenderableModel(manager)) {" in placement_block
    assert "if (model && !model.destroyed) {" in tutorial_source
    assert "model.alpha = 1;" in tutorial_source
    assert "live2dCanvas.style.setProperty('opacity', '1', 'important');" in tutorial_source
    assert "temporaryConfig" in interpage_source
    assert "skipIdleRestore" in interpage_source
    assert "suppressToast" in interpage_source
    assert "async function _waitForLive2DManagerIdle" in interpage_source
    assert "await _waitForLive2DManagerIdle(30000);" in interpage_source


def test_tutorial_temporary_model_reload_bootstraps_live2d_manager_without_user_model_init():
    interpage_source = read_js_parts(Path("static/app/app-interpage"))
    live2d_branch = interpage_source.split("if (newModelPath) {", 1)[1].split(
        "// Load the new model",
        1,
    )[0]

    assert "if (temporaryConfig && typeof window.Live2DManager === 'function')" in live2d_branch
    assert "window.live2dManager = new window.Live2DManager();" in live2d_branch
    assert "await initLive2DModel();" in live2d_branch
    assert live2d_branch.index("temporaryConfig && typeof window.Live2DManager === 'function'") < live2d_branch.index(
        "await initLive2DModel();"
    )


def test_day1_round_activation_keeps_wakeup_after_step_registry_split():
    director_source = read_director_source()
    activation_block = director_source.split("async playDay1IntroActivationRoundScene(sceneRunId)", 1)[1].split(
        "async playDay1IntroGreetingRoundScene(sceneRunId)",
        1,
    )[0]

    assert "await this.runWakeupPrelude();" in activation_block
    assert "this.getStep('intro_basic')" not in activation_block
    assert activation_block.index("await this.runWakeupPrelude();") < activation_block.index("this.introFlowStarted = true;")
    assert "await this.waitForIntroActivationTransition();" in activation_block
    assert "const INTRO_ACTIVATION_AUTO_ADVANCE_MS = 2600;" in director_source
    assert "const INTRO_ACTIVATION_REDUCED_MOTION_AUTO_ADVANCE_MS = 720;" in director_source
    assert "const INTRO_ACTIVATION_HINT = '稍等一下，我马上开始说话啦～';" in director_source
    assert "点一下这里，我就能开始说话啦～" not in director_source

    transition_block = director_source.split("waitForIntroActivationTransition() {", 1)[1].split(
        "\n        shouldReduceTutorialMotion() {",
        1,
    )[0]
    assert "this.shouldReduceTutorialMotion()" in transition_block
    assert "INTRO_ACTIVATION_AUTO_ADVANCE_MS" in transition_block
    assert "INTRO_ACTIVATION_REDUCED_MOTION_AUTO_ADVANCE_MS" in transition_block
    assert "return wait(waitMs);" in transition_block
    assert "wait(360)" not in transition_block


def test_avatar_floating_tutorial_boot_predictor_loads_before_user_model_init():
    index_source = Path("templates/index.html").read_text(encoding="utf-8")
    pages_router_source = Path("main_routers/pages_router.py").read_text(encoding="utf-8")

    predictor_script = "/static/tutorial/core/avatar-floating-boot-predictor.js"
    assert predictor_script in index_source
    assert index_source.index(predictor_script) < index_source.index("/static/live2d/live2d-init.js")
    assert index_source.index(predictor_script) < index_source.index("/static/vrm/vrm-init.js")
    assert index_source.index(predictor_script) < index_source.index("/static/mmd/mmd-init.js")
    assert "static/tutorial/core/avatar-floating-boot-predictor.js" in pages_router_source


def test_avatar_floating_tutorial_boot_predictor_contract():
    predictor_source = Path("static/tutorial/core/avatar-floating-boot-predictor.js").read_text(encoding="utf-8")
    manager_source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")

    assert "AVATAR_FLOATING_GUIDE_STORAGE_KEY = 'neko_avatar_floating_guide_v1'" in predictor_source
    assert "manualResetRound" in predictor_source
    assert "pendingRound" in predictor_source
    assert "completedRounds" in predictor_source
    assert "skippedRounds" in predictor_source
    assert "lastAutoShownDate" in predictor_source
    assert "window.NekoAvatarFloatingBoot" in predictor_source
    assert "shouldBootIntoTutorial" in predictor_source
    assert "shouldSkipUserModelBoot" in predictor_source
    assert "getPredictedRound" in predictor_source
    assert "getSkippedUserModelBootRound" in predictor_source
    assert "markUserModelBootSkipped" in predictor_source
    assert "claimDirectTutorialBoot" in predictor_source
    assert "releaseDirectTutorialBoot" in predictor_source
    assert "yuiGuidePcOverlayRunId" in predictor_source
    assert "beginDirectTutorialLoading" not in predictor_source
    assert "clearDirectTutorialLoading" not in predictor_source
    assert "window.nekoTutorialLoadingOverlay" not in predictor_source
    assert "function isPcLoadingOverlayBridge(bridge)" not in predictor_source
    assert "window.nekoTutorialOverlay.loadingOverlay" not in predictor_source
    assert "window.nekoTutorialOverlay.beginLoading" not in predictor_source
    assert "emotion_model_icon.png" not in predictor_source
    assert "function isTutorialBootAvailable()" in predictor_source
    assert "window.innerWidth <= 768" in predictor_source
    assert "window.innerWidth <= 768" in manager_source

    should_skip_block = predictor_source.split("function shouldSkipUserModelBoot()", 1)[1].split(
        "function markUserModelBootSkipped",
        1,
    )[0]
    assert "if (!isTutorialBootAvailable()) {" in should_skip_block
    assert "return false;" in should_skip_block.split("if (!isTutorialBootAvailable()) {", 1)[1].split("}", 1)[0]
    compute_block = predictor_source.split("function computePredictedRound()", 1)[1].split(
        "function getPredictedRound()",
        1,
    )[0]
    assert compute_block.index("if (guideState.manualResetRound)") < compute_block.index(
        "if (guideState.lastAutoShownDate === getTodayLocalDate())"
    )
    assert compute_block.index("if (guideState.lastAutoShownDate === getTodayLocalDate())") < compute_block.index(
        "if (guideState.pendingRound"
    )


def test_avatar_model_initializers_skip_user_model_when_tutorial_boot_is_predicted():
    index_source = Path("static/js/index.js").read_text(encoding="utf-8")
    live2d_init_source = Path("static/live2d/live2d-init.js").read_text(encoding="utf-8")
    vrm_init_source = Path("static/vrm/vrm-init.js").read_text(encoding="utf-8")
    mmd_init_source = Path("static/mmd/mmd-init.js").read_text(encoding="utf-8")

    for source in (live2d_init_source, vrm_init_source, mmd_init_source):
        assert "window.NekoAvatarFloatingBoot" in source
        assert "shouldSkipUserModelBoot" in source
        assert "markUserModelBootSkipped" in source

    live2d_inner = live2d_init_source.split("async function _initLive2DModelInner()", 1)[1].split(
        "// 检查是否在 VRM/MMD 模式下",
        1,
    )[0]
    assert "window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot()" in live2d_inner
    assert "window.NekoAvatarFloatingBoot.markUserModelBootSkipped('live2d-init')" in live2d_inner
    pngtuber_block = index_source.split("if (modelType === 'pngtuber') {", 1)[1].split(
        "} else if (modelType === 'live3d' || modelType === 'vrm')",
        1,
    )[0]
    assert "window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot()" in pngtuber_block
    assert "window.NekoAvatarFloatingBoot.markUserModelBootSkipped('pngtuber-init')" in pngtuber_block
    assert pngtuber_block.index("window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot()") < pngtuber_block.index(
        "window.loadPNGTuberAvatar("
    )
    assert "async function autoInitMMDOnMainPage()" in mmd_init_source
    assert "window.autoInitMMDOnMainPage = autoInitMMDOnMainPage;" in mmd_init_source
    assert "autoInitMMDOnMainPage();" in mmd_init_source

    self_heal_block = live2d_init_source.split("function _nekoShouldSelfHealLive2D()", 1)[1].split(
        "function scheduleLive2DConfigRetry",
        1,
    )[0]
    assert "window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot()" in self_heal_block


def test_avatar_floating_direct_tutorial_boot_uses_manager_recheck_and_user_model_fallback():
    tutorial_source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")
    avatar_reload_source = Path("static/tutorial/avatar/reload-controller.js").read_text(encoding="utf-8")

    assert "isDirectAvatarFloatingTutorialBoot(round)" in tutorial_source
    assert "claimDirectAvatarFloatingTutorialBoot(round, source)" in tutorial_source
    assert "releaseDirectAvatarFloatingTutorialBoot(reason, options)" in tutorial_source
    assert "recoverUserModelAfterDirectTutorialBootFailure(reason)" in tutorial_source
    assert "waitForTutorialModelHostReady(maxWaitTime = 12000)" in tutorial_source
    assert "window.NekoAvatarFloatingBoot" in tutorial_source
    assert "window.NekoAvatarFloatingBoot.claimDirectTutorialBoot" in tutorial_source
    assert "window.NekoAvatarFloatingBoot.releaseDirectTutorialBoot" in tutorial_source
    assert "window.NekoAvatarFloatingBoot.recoverUserModelBoot" in tutorial_source
    assert "window.NekoAvatarFloatingBoot.clearDirectTutorialLoading" not in tutorial_source
    assert "getDirectAvatarFloatingTutorialBootRound()" in tutorial_source

    start_round_block = tutorial_source.split("async startAvatarFloatingGuideRound(day, options = {})", 1)[1].split(
        "async playAvatarFloatingRoundPrelude",
        1,
    )[0]
    assert "const directTutorialBoot = this.isDirectAvatarFloatingTutorialBoot(round);" in start_round_block
    assert "if (directTutorialBoot) {" in start_round_block
    assert "await this.waitForTutorialModelHostReady()" in start_round_block
    assert "await this.waitForFloatingButtons()" in start_round_block
    assert "this.claimDirectAvatarFloatingTutorialBoot(round, source);" in start_round_block
    # round 预留会改变预测结果；direct-boot claim 必须覆盖胶囊 prepare 的异步等待窗口。
    assert start_round_block.index("this.claimDirectAvatarFloatingTutorialBoot(round, source);") < start_round_block.index(
        "await this.prepareYuiGuideCompactChatForTutorial()"
    )
    # prepare 期间取消时释放提前 claim，但保留已跳过用户模型的恢复标记。
    cancellation_release_index = start_round_block.index(
        "this.releaseDirectAvatarFloatingTutorialBoot('avatar-floating-start-cancelled', {"
    )
    # 只检查取消分支的当前 release 调用，避免误命中后续正常 teardown 的同名参数。
    cancellation_release_block = start_round_block[cancellation_release_index:].split("});", 1)[0]
    assert "keepUserModelBootSkipped: true" in cancellation_release_block
    assert "suppressPrediction: true" in cancellation_release_block
    assert "skipSourceModelFade: directTutorialBoot" in start_round_block
    assert "clearDirectAvatarFloatingTutorialLoading" not in start_round_block
    assert "await this.recoverUserModelAfterDirectTutorialBootFailure('avatar-floating-start-failed')" in start_round_block
    assert "this.releaseDirectAvatarFloatingTutorialBoot('avatar-floating-before-teardown', {" in start_round_block
    assert "keepUserModelBootSkipped: true" in start_round_block
    assert "suppressPrediction: true" in start_round_block
    assert start_round_block.index("this.releaseDirectAvatarFloatingTutorialBoot('avatar-floating-before-teardown', {") < start_round_block.index(
        "if (!this._tutorialEndHandled) {"
    )
    assert start_round_block.index("this.releaseDirectAvatarFloatingTutorialBoot('avatar-floating-before-teardown', {") < start_round_block.index(
        "await this.requestTutorialDestroy(endReason);"
    )
    assert start_round_block.index("this.releaseDirectAvatarFloatingTutorialBoot('avatar-floating-before-teardown', {") < start_round_block.index(
        "await this.requestTutorialDestroy('destroy');"
    )
    assert "this.releaseDirectAvatarFloatingTutorialBoot('avatar-floating-start-finished');" not in start_round_block

    teardown_block = tutorial_source.split("_teardownTutorialUI() {", 1)[1].split(
        "async waitForTutorialTeardownSettled",
        1,
    )[0]
    assert "this.dispatchAvatarFloatingTutorialInputRestored(" in tutorial_source
    assert "neko:yui-guide:tutorial-input-restored" in tutorial_source
    # 输入区域刷新必须发生在模型身份和猫咪/普通业务形态都恢复完成之后。
    assert teardown_block.index("this.restoreAvatarFloatingModelStateAfterTutorial()") < teardown_block.index(
        "this.dispatchAvatarFloatingTutorialInputRestored("
    )

    begin_block = avatar_reload_source.split("beginOverride(options)", 1)[1].split("restoreOverride()", 1)[0]
    assert "const skipSourceModelFade = normalizedOptions.skipSourceModelFade === true;" in begin_block
    assert "if (!skipSourceModelFade) {" in begin_block
    assert "this.fadeOutCurrentModel({" in begin_block


def test_avatar_floating_direct_boot_does_not_wait_for_user_floating_buttons():
    tutorial_source = Path("static/tutorial/core/universal-manager.js").read_text(encoding="utf-8")
    predictor_source = Path("static/tutorial/core/avatar-floating-boot-predictor.js").read_text(encoding="utf-8")
    check_block = tutorial_source.split("async checkAndStartTutorial()", 1)[1].split(
        "async waitForTutorialTeardownSettled",
        1,
    )[0]
    should_skip_block = predictor_source.split("function shouldSkipUserModelBoot()", 1)[1].split(
        "function markUserModelBootSkipped",
        1,
    )[0]
    mark_skipped_block = predictor_source.split("function markUserModelBootSkipped(reason)", 1)[1].split(
        "function claimDirectTutorialBoot",
        1,
    )[0]
    claim_block = predictor_source.split("function claimDirectTutorialBoot(round, reason)", 1)[1].split(
        "function releaseDirectTutorialBoot",
        1,
    )[0]
    direct_boot_gate_block = tutorial_source.split("isDirectAvatarFloatingTutorialBoot(round)", 1)[1].split(
        "claimDirectAvatarFloatingTutorialBoot(round, source)",
        1,
    )[0]
    recovery_block = predictor_source.split("async function recoverUserModelBoot(reason)", 1)[1].split(
        "window.NekoAvatarFloatingBoot = {",
        1,
    )[0]

    assert "const directBootRound = this.getDirectAvatarFloatingTutorialBootRound();" in check_block
    assert "this.isDirectAvatarFloatingTutorialBoot(directBootRound)" in check_block
    assert "const round = this.getHomeAvatarFloatingGuideLaunchRound();" in check_block
    assert "beginDirectAvatarFloatingTutorialLoading" not in tutorial_source
    assert "clearDirectAvatarFloatingTutorialLoading" not in tutorial_source
    assert "this.pendingTutorialStartSource = 'manual_reset';" in check_block
    assert "this.startTutorialWhenI18nReady(1500);" in check_block
    assert check_block.index("this.isDirectAvatarFloatingTutorialBoot(directBootRound)") < check_block.index(
        "this.waitForFloatingButtons().then((found)"
    )
    assert "predictedRound === normalizedRound" in direct_boot_gate_block
    assert "skippedUserModel && predictedRound === normalizedRound" in direct_boot_gate_block
    assert "getSkippedUserModelBootRound" in direct_boot_gate_block
    assert "beginDirectTutorialLoading" not in should_skip_block
    assert "beginDirectTutorialLoading" not in mark_skipped_block
    assert "beginDirectTutorialLoading" not in claim_block
    assert "state.predictionSuppressed = false;" not in mark_skipped_block
    assert "await window.showCurrentModel();" in recovery_block
    assert recovery_block.index("await window.initLive2DModel();") < recovery_block.rindex("await window.showCurrentModel();")
    assert "await window.initMMDModel();" in recovery_block
    assert "await window.autoInitMMDOnMainPage();" in recovery_block
    assert "const isPngtuberModel = modelType === 'pngtuber';" in recovery_block
    assert "await window.loadPNGTuberAvatar(window.lanlan_config && window.lanlan_config.pngtuber || {});" in recovery_block
    assert recovery_block.index("if (isPngtuberModel) {") < recovery_block.index("await window.initLive2DModel();")
    assert "const isMmdModel = modelType === 'live3d' && subType === 'mmd';" in recovery_block
    pngtuber_branch = recovery_block.split("if (isPngtuberModel) {", 1)[1].split(
        "const isMmdModel = modelType === 'live3d' && subType === 'mmd';",
        1,
    )[0]
    assert "await window.initLive2DModel();" not in pngtuber_branch
    mmd_branch = recovery_block.split("if (isMmdModel) {", 1)[1].split(
        "} else if ((modelType === 'vrm' || modelType === 'live3d')",
        1,
    )[0]
    assert "return false;" not in mmd_branch.split("await window.showCurrentModel();", 1)[0]
    assert "return true;" not in mmd_branch.split("await window.initMMDModel();", 1)[1].split(
        "await window.showCurrentModel();",
        1,
    )[0]
    assert mmd_branch.index("if (typeof window.initMMDModel === 'function')") < mmd_branch.index(
        "await window.showCurrentModel();"
    )


def test_tutorial_lifecycle_modules_export_reusable_controllers():
    interaction_source = Path("static/tutorial/core/interaction-takeover.js").read_text(encoding="utf-8")
    skip_source = Path("static/tutorial/core/skip-controller.js").read_text(encoding="utf-8")
    avatar_reload_source = Path("static/tutorial/avatar/reload-controller.js").read_text(encoding="utf-8")

    for expected in (
        "class TutorialInteractionTakeoverController",
        "window.TutorialInteractionTakeover = {",
        "createController: function (options)",
        "setActive(active)",
        "if (this.destroyed && nextActive) {",
        "enableFaceForwardLock()",
        "if (this.destroyed || !this.active || this.page !== 'home'",
        "setExternalizedChatButtonsDisabled(disabled)",
        "this.setActive(false);",
    ):
        assert expected in interaction_source

    for expected in (
        "class TutorialSkipController",
        "window.TutorialSkipController = {",
        "show(options)",
        "resetSkipHandled",
        "button.removeAttribute('aria-disabled');",
        "hide()",
        "destroy()",
    ):
        assert expected in skip_source

    ensure_styles_block = skip_source.split("ensureStyles() {", 1)[1].split(
        "        show(options) {",
        1,
    )[0]
    assert "CSS.escape(this.buttonId)" in ensure_styles_block
    assert "${selector}:hover" in ensure_styles_block
    assert "html[data-theme='dark'] ${selector}" in ensure_styles_block
    assert "#neko-tutorial-skip-btn:hover" not in ensure_styles_block

    for expected in (
        "class TutorialAvatarReloadController",
        "window.TutorialAvatarReloadController = {",
        "beginOverride(options)",
        "restoreOverride()",
        "hasActiveOverride()",
        "getPendingPromise()",
        "tutorialModelName",
    ):
        assert expected in avatar_reload_source


def test_theme_system_preference_does_not_become_saved_user_choice():
    theme_source = Path("static/theme-manager.js").read_text(encoding="utf-8")
    plugin_dark_mode_source = Path("frontend/plugin-manager/src/composables/useDarkMode.ts").read_text(encoding="utf-8")

    assert "applyTheme(isDark, { persist: shouldPersist });" in theme_source
    assert "applyThemeAnimated(event.matches, { persist: false });" in theme_source
    assert "applyDarkMode(saved !== null ? saved : getSystemPrefersDark(), { persist: saved !== null })" in plugin_dark_mode_source
    assert "applyDarkMode(event.matches, { persist: false })" in plugin_dark_mode_source


def test_home_yui_guide_uses_platform_capability_matrix_for_cross_window_skip():
    director_source = read_director_source()
    plugin_runtime_source = Path("frontend/plugin-manager/src/yui-guide-runtime.ts").read_text(encoding="utf-8")

    assert "window.homeTutorialPlatformCapabilities" in director_source
    assert "createHomeTutorialPlatformCapabilities" in director_source
    assert "supportsExternalChat" in director_source
    assert "supportsSystemTrayHint" in director_source
    assert "supportsPluginDashboardWindow" in director_source
    assert "preferredSkipHitPadding" in director_source
    assert "forwardingTolerance" in director_source
    assert "platformCapabilities" in plugin_runtime_source
    assert "const explicitTolerance = Number(rect.forwardingTolerance)" in plugin_runtime_source
    assert "if (platform === 'linux') return Math.max(8, Math.round(basePadding * 0.35))" in plugin_runtime_source
    assert "if (platform === 'macos') return Math.max(6, Math.round(basePadding * 0.25))" in plugin_runtime_source


def test_home_yui_guide_scenes_declare_timelines_and_director_consumes_normalized_cues():
    steps_source = Path("static/tutorial/yui-guide/steps.js").read_text(encoding="utf-8")
    director_source = read_director_source()

    assert "timeline: []" in steps_source
    assert "{ at: 0.16, action: 'highlightVoiceControl' }" in steps_source
    assert "{ at: 0.54, action: 'openSettingsPanel' }" in steps_source
    assert "{ voiceKey: 'takeover_settings_peek_detail', at: Math.max(7450 / 13923, 0.55), action: 'showSecondLine' }" in steps_source
    assert "getGuideTimelineCueConfig(voiceKey, cueName)" in director_source
    assert "const timeline = Array.isArray(performance.timeline) ? performance.timeline : []" in director_source
    assert "cue.action !== normalizedCueName" in director_source
    assert "GUIDE_NARRATION_TIMELINES_BY_KEY" in director_source
    assert "estimateSpeechDurationMs(fallbackText || '')" in director_source


def test_home_yui_guide_records_local_experience_metrics_without_upload_path():
    director_source = read_director_source()

    assert "neko_home_tutorial_experience_metrics_v1" in director_source
    assert "window.homeTutorialExperienceMetrics" in director_source
    assert "recordExperienceMetric('scene_start'" in director_source
    assert "recordExperienceMetric('scene_complete'" in director_source
    assert "recordExperienceMetric('scene_failed'" in director_source
    assert "recordExperienceMetric('skip'" in director_source
    assert "recordExperienceMetric('angry_exit'" in director_source
    assert "recordExperienceMetric('handoff_failed'" in director_source
    assert ".localStorage.setItem(" in director_source


def test_plugin_manager_bootstraps_plugin_dashboard_runtime_without_overlay_bridge():
    app_source = Path("frontend/plugin-manager/src/App.vue").read_text(encoding="utf-8")
    main_source = Path("frontend/plugin-manager/src/main.ts").read_text(encoding="utf-8")

    assert "<YuiTutorialOverlay />" not in app_source
    assert "useYuiTutorialBridge" not in main_source
    assert "tutorialBridge.init()" not in main_source
    assert "initPluginDashboardYuiGuideRuntime()" in main_source


def test_task_executor_format_messages_marks_latest_user_request():
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    conversation = [
        {"role": "user", "text": "帮我打开系统计算器"},
        {"role": "assistant", "text": "已经打开了"},
    ]
    output = executor._format_messages(conversation)
    assert "LATEST_USER_REQUEST: 帮我打开系统计算器" in output
    assert "assistant: 已经打开了" in output


def test_task_executor_format_messages_mentions_image_attachments():
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    conversation = [
        {
            "role": "user",
            "content": "帮我看看这张图哪里报错了",
            "attachments": [{"type": "image_url", "url": "data:image/png;base64,abc"}],
        }
    ]
    output = executor._format_messages(conversation)
    assert "LATEST_USER_REQUEST: 帮我看看这张图哪里报错了 [Attached images: 1]" in output


def test_plugin_terminal_status_defaults_and_run_data_overrides():
    from app.agent_server import _plugin_terminal_status

    # Default: success → completed, fail → failed.
    assert _plugin_terminal_status(True, None) == "completed"
    assert _plugin_terminal_status(False, None) == "failed"
    assert _plugin_terminal_status(True, {}) == "completed"
    assert _plugin_terminal_status(False, {}) == "failed"

    # Explicit blocked signals (plugin opts in via run_data).
    assert _plugin_terminal_status(True, {"status": "clarify", "action": "clarify", "needs_confirmation": True}) == "blocked"
    assert _plugin_terminal_status(True, {"status": "confirm_required", "needs_confirmation": True}) == "blocked"
    assert _plugin_terminal_status(True, {"status": "blocked"}) == "blocked"

    # Error signal forces failed even on raw success.
    assert _plugin_terminal_status(True, {"status": "error"}) == "failed"

    # observation_only bypasses overrides → fall back to raw success.
    assert _plugin_terminal_status(True, {"status": "error", "observation_only": True}) == "completed"
    assert _plugin_terminal_status(True, {"status": "blocked", "observation_only": True}) == "completed"

    # executed=False on its own is intentionally NOT enough — many plugins use
    # it to mean "no game-side card played" while the control op succeeded
    # (e.g. STS2 stop_autoplay returns status="idle", executed=False after a
    # real stop). Inferring blocked from that misreports successful ops.
    assert _plugin_terminal_status(True, {"status": "idle", "executed": False}) == "completed"
    assert _plugin_terminal_status(True, {"status": "stale", "executed": False}) == "completed"
    assert _plugin_terminal_status(True, {"status": "ok", "executed": True}) == "completed"

    # raw_success=False must always land on "failed". run_data signals cannot
    # "upgrade" a protocol failure to a softer status like "blocked".
    assert _plugin_terminal_status(False, {"status": "blocked"}) == "failed"
    assert _plugin_terminal_status(False, {"status": "clarify", "action": "clarify", "needs_confirmation": True}) == "failed"
    assert _plugin_terminal_status(False, {"status": "confirm_required", "needs_confirmation": True}) == "failed"
    assert _plugin_terminal_status(False, {"status": "error"}) == "failed"
    # observation_only also doesn't change the picture on raw fail.
    assert _plugin_terminal_status(False, {"status": "blocked", "observation_only": True}) == "failed"


def test_callback_instruction_renders_blocked_plugin_result_as_not_executed():
    from main_logic.core import _build_callback_instruction

    output = _build_callback_instruction(
        [
            {
                "origin": "task_result",
                "status": "blocked",
                "source_kind": "plugin",
                "source_name": "示例插件",
                "summary": "需要确认后才能执行",
                "detail": "需要确认后才能执行",
                "delivery_mode": "proactive",
            }
        ],
        lang="zh",
        lanlan_name="小天",
        master_name="主人",
    )

    assert "未执行" in output
    assert "说明未执行原因" in output
    assert "执行失败" not in output
    assert "需要确认后才能执行" in output


def test_task_executor_hides_agent_auto_disabled_plugin_entries():
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    plugins = [
        {
            "id": "demo_plugin",
            "description": "示例插件",
            "entries": [
                {"id": "diagnostics_snapshot", "description": "获取诊断快照", "metadata": {"agent_auto": False}},
                {"id": "start_job", "description": "启动示例任务"},
            ],
        }
    ]

    desc = "\n".join(executor._build_plugin_desc_lines(plugins))
    assert "diagnostics_snapshot" not in desc
    assert "start_job" in desc
    plugin, entry = executor._find_plugin_entry(plugins, "demo_plugin", "diagnostics_snapshot")
    assert plugin is plugins[0]
    assert entry is None


def test_task_executor_skips_plugin_with_only_agent_hidden_entries():
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    plugins = [
        {
            "id": "demo_plugin",
            "description": "示例插件",
            "entries": [
                {"id": "diagnostics_snapshot", "description": "获取诊断快照", "metadata": {"agent_auto": False}},
            ],
        }
    ]

    assert executor._build_plugin_desc_lines(plugins) == []
    plugin, entry = executor._find_plugin_entry(plugins, "demo_plugin", "diagnostics_snapshot")
    assert plugin is plugins[0]
    assert entry is None


def test_apply_cached_short_descriptions_manifest_cache_and_fallback():
    """_apply_cached_short_descriptions is a pure manifest/cache read (zero LLM):
    a manifest-provided short_description is used as-is and primes the cache; a
    cache hit (description unchanged) is applied; missing or stale entries are
    left empty and returned as background-prewarm candidates — never generated
    here."""
    from brain.task_executor import DirectTaskExecutor

    key = DirectTaskExecutor._desc_key
    executor = object.__new__(DirectTaskExecutor)
    executor._short_desc_cache = {}
    # 缓存键是完整 description 的 hash（截断只用于喂 LLM，不当失效 key）
    executor._short_desc_cache["from_cache"] = (key("full B"), "cached B")
    executor._short_desc_cache["stale_cache"] = (key("old D"), "cached D")

    plugins = [
        {"id": "has_manifest", "description": "full A", "short_description": "short A"},
        {"id": "from_cache", "description": "full B"},
        {"id": "stale_cache", "description": "new D"},
        {"id": "missing", "description": "full C"},
        {"id": "no_desc"},
        "not_a_dict",
    ]

    missing = executor._apply_cached_short_descriptions(plugins)

    # (a) manifest 自带 → 直接用，并把它写进缓存供后续 refresh 复用
    assert plugins[0]["short_description"] == "short A"
    assert executor._short_desc_cache["has_manifest"] == (key("full A"), "short A")
    # 缓存命中（desc 未变）→ 应用缓存值
    assert plugins[1]["short_description"] == "cached B"
    # 缓存陈旧（desc 变了）→ 不应用，进 missing 候选
    assert "short_description" not in plugins[2]
    # 缺失且无缓存 → 不现生成，留空，进 missing 候选
    assert "short_description" not in plugins[3]
    # 无 description → 无可摘要内容，不进 missing 也不留空
    assert "short_description" not in plugins[4]

    assert sorted(p["id"] for p in missing) == ["missing", "stale_cache"]


@pytest.mark.asyncio
async def test_plugin_list_provider_never_generates_short_description_on_hot_path():
    """Core acceptance: the analyze hot path (plugin_list_provider) must never
    generate a short_description inline. Plugins missing one are handed to the
    background prewarm; the call returns immediately, _get_llm is not invoked
    synchronously, and analyze safely falls back to the full description."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from brain.task_executor import DirectTaskExecutor

    plugins = [{"id": "no_short", "description": "a plugin without a short description"}]
    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = []
    executor._external_plugin_provider = AsyncMock(return_value=plugins)
    executor._short_desc_cache = {}
    executor._short_desc_prewarm_inflight = set()
    executor._short_desc_prewarm_tasks = set()

    llm_factory = MagicMock(
        side_effect=AssertionError("_get_llm must not be called on the analyze hot path")
    )
    with patch.object(DirectTaskExecutor, "_get_llm", llm_factory), \
         patch.object(
             DirectTaskExecutor, "_prewarm_short_descriptions", new_callable=AsyncMock,
         ) as mock_prewarm:
        result = await executor.plugin_list_provider(force_refresh=True)

    # 返回的插件仍缺 short_description（未现生成）→ 分析侧回退到完整 description
    assert not result[0].get("short_description")
    # 热路径上 _get_llm 没被同步调用
    llm_factory.assert_not_called()
    # 缺失插件被调度进后台预热
    mock_prewarm.assert_called_once()
    assert "no_short" in executor._short_desc_prewarm_inflight
    await asyncio.sleep(0)  # 让后台任务跑掉，避免 "coroutine never awaited" 警告


def test_short_desc_cache_persists_generated_entries_across_instances(tmp_path):
    """LLM-generated short_descriptions are persisted to disk; a fresh instance
    (simulating a restart) reuses them with zero LLM. The key is a hash of the
    full description, so a manifest change invalidates the entry and triggers
    regeneration."""
    from types import SimpleNamespace
    from brain.task_executor import DirectTaskExecutor

    cfg = SimpleNamespace(config_dir=str(tmp_path), ensure_config_directory=lambda: None)
    key = DirectTaskExecutor._desc_key

    # 实例一：把一条生成的缓存落盘（key = 完整 description 的 hash）
    exec1 = object.__new__(DirectTaskExecutor)
    exec1._config_manager = cfg
    exec1._short_desc_cache_filename = "plugin_short_desc_cache.json"
    exec1._persist_generated_short_descriptions({"genplug": (key("full desc"), "generated short")})
    assert (tmp_path / "plugin_short_desc_cache.json").exists()

    # 实例二：从盘上加载（模拟重启）
    exec2 = object.__new__(DirectTaskExecutor)
    exec2._config_manager = cfg
    exec2._short_desc_cache_filename = "plugin_short_desc_cache.json"
    exec2._short_desc_cache = exec2._load_short_desc_cache()
    assert exec2._short_desc_cache == {"genplug": (key("full desc"), "generated short")}

    # desc 未变 → 命中持久化缓存，零 LLM，不进 missing
    plugins = [{"id": "genplug", "description": "full desc"}]
    assert exec2._apply_cached_short_descriptions(plugins) == []
    assert plugins[0]["short_description"] == "generated short"

    # desc 变了 → hash key 失效，重新作为生成候选
    changed = [{"id": "genplug", "description": "CHANGED desc"}]
    missing = exec2._apply_cached_short_descriptions(changed)
    assert "short_description" not in changed[0]
    assert [p["id"] for p in missing] == ["genplug"]


def test_short_desc_cache_key_uses_full_description_not_truncated_prompt():
    """Regression (Codex P2): the cache key is a hash of the FULL description;
    truncation is prompt-only. A very long description (far above
    PLUGIN_INPUT_DESC_MAX_TOKENS) must still hit the cache, rather than missing
    forever because a truncated key never matches the full description."""
    from config import PLUGIN_INPUT_DESC_MAX_TOKENS
    from brain.task_executor import DirectTaskExecutor

    key = DirectTaskExecutor._desc_key
    long_desc = ("word " * (PLUGIN_INPUT_DESC_MAX_TOKENS * 4)).strip()  # 远超输入截断阈值

    executor = object.__new__(DirectTaskExecutor)
    executor._short_desc_cache = {"big": (key(long_desc), "cached short")}

    plugins = [{"id": "big", "description": long_desc}]
    assert executor._apply_cached_short_descriptions(plugins) == []
    assert plugins[0]["short_description"] == "cached short"


def test_short_desc_cache_load_tolerates_missing_and_corrupt_file(tmp_path):
    """A missing file or corrupt JSON yields an empty cache safely (no raise)."""
    from types import SimpleNamespace
    from brain.task_executor import DirectTaskExecutor

    cfg = SimpleNamespace(config_dir=str(tmp_path), ensure_config_directory=lambda: None)
    executor = object.__new__(DirectTaskExecutor)
    executor._config_manager = cfg
    executor._short_desc_cache_filename = "plugin_short_desc_cache.json"

    # 文件不存在
    assert executor._load_short_desc_cache() == {}

    # 损坏的 JSON
    (tmp_path / "plugin_short_desc_cache.json").write_text("{not json", encoding="utf-8")
    assert executor._load_short_desc_cache() == {}


@pytest.mark.asyncio
async def test_task_executor_routes_galgame_continue_phrase_through_plugin_assessment():
    from unittest.mock import AsyncMock, patch
    from brain.task_executor import DirectTaskExecutor, UserPluginDecision

    plugins = [{
        "id": "galgame_plugin",
        "description": "galgame plugin",
        "short_description": "galgame control",
        "entries": [{"id": "galgame_continue_auto_advance", "input_schema": {}}],
    }]
    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = []
    executor._external_plugin_provider = AsyncMock(return_value=plugins)
    executor._short_desc_cache = {}

    decision = UserPluginDecision(
        has_task=True,
        can_execute=True,
        task_description="继续自动推进 galgame 剧情",
        plugin_id="galgame_plugin",
        entry_id="galgame_continue_auto_advance",
        plugin_args={"message": "继续推进剧情"},
        reason="llm_user_plugin_assessment",
    )
    with patch.object(
        DirectTaskExecutor,
        "_assess_user_plugin",
        new_callable=AsyncMock,
        return_value=decision,
    ) as mock_assess:
        result = await executor.analyze_and_execute(
            [{"role": "user", "content": "继续推进剧情"}],
            agent_flags={
                "computer_use_enabled": False,
                "browser_use_enabled": False,
                "user_plugin_enabled": True,
                "openclaw_enabled": False,
                "openfang_enabled": False,
            },
        )

    assert result is not None
    assert result.execution_method == "user_plugin"
    assert result.tool_name == "galgame_plugin"
    assert result.entry_id == "galgame_continue_auto_advance"
    assert result.tool_args == {"message": "继续推进剧情"}
    assert result.reason == "llm_user_plugin_assessment"
    mock_assess.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_executor_routes_galgame_mode_phrases_through_plugin_assessment():
    from unittest.mock import AsyncMock, patch
    from brain.task_executor import DirectTaskExecutor, UserPluginDecision

    plugins = [{
        "id": "galgame_plugin",
        "description": "galgame plugin",
        "short_description": "galgame control",
        "entries": [{"id": "galgame_set_mode", "input_schema": {}}],
    }]
    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = []
    executor._external_plugin_provider = AsyncMock(return_value=plugins)
    executor._short_desc_cache = {}

    decisions = [
        UserPluginDecision(
            has_task=True,
            can_execute=True,
            task_description="切换 galgame 到自动推进模式",
            plugin_id="galgame_plugin",
            entry_id="galgame_set_mode",
            plugin_args={"mode": "choice_advisor", "push_notifications": True},
            reason="llm_user_plugin_assessment_auto",
        ),
        UserPluginDecision(
            has_task=True,
            can_execute=True,
            task_description="切换 galgame 到伴读模式",
            plugin_id="galgame_plugin",
            entry_id="galgame_set_mode",
            plugin_args={"mode": "companion", "push_notifications": True},
            reason="llm_user_plugin_assessment_companion",
        ),
    ]
    with patch.object(
        DirectTaskExecutor,
        "_assess_user_plugin",
        new_callable=AsyncMock,
        side_effect=decisions,
    ) as mock_assess:
        auto_result = await executor.analyze_and_execute(
            [{"role": "user", "content": "开启自动推进模式"}],
            agent_flags={
                "computer_use_enabled": False,
                "browser_use_enabled": False,
                "user_plugin_enabled": True,
                "openclaw_enabled": False,
                "openfang_enabled": False,
            },
        )
        companion_result = await executor.analyze_and_execute(
            [{"role": "user", "content": "切回伴读，不要自动点"}],
            agent_flags={
                "computer_use_enabled": False,
                "browser_use_enabled": False,
                "user_plugin_enabled": True,
                "openclaw_enabled": False,
                "openfang_enabled": False,
            },
        )

    assert auto_result is not None
    assert auto_result.execution_method == "user_plugin"
    assert auto_result.tool_name == "galgame_plugin"
    assert auto_result.entry_id == "galgame_set_mode"
    assert auto_result.tool_args == {"mode": "choice_advisor", "push_notifications": True}
    assert auto_result.reason == "llm_user_plugin_assessment_auto"
    assert companion_result is not None
    assert companion_result.entry_id == "galgame_set_mode"
    assert companion_result.tool_args == {"mode": "companion", "push_notifications": True}
    assert companion_result.reason == "llm_user_plugin_assessment_companion"
    assert mock_assess.await_count == 2


def test_task_executor_plugin_desc_includes_enum_values():
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    lines = executor._build_plugin_desc_lines([
        {
            "id": "galgame_plugin",
            "description": "galgame plugin",
            "entries": [
                {
                    "id": "galgame_agent_command",
                    "description": "agent command",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": [
                                    "query_status",
                                    "query_context",
                                    "send_message",
                                    "set_standby",
                                    "list_messages",
                                    "ack_message",
                                ],
                            },
                        },
                    },
                },
            ],
        },
    ])

    assert "action:string enum=[query_status|query_context|send_message|set_standby|list_messages|ack_message]" in "\n".join(lines)


def test_task_executor_plugin_desc_truncates_long_enum_with_remainder_hint():
    """超过 12 个 enum 值时，截断标记必须在 [] 内并带 '+N more' 数量提示，
    而不是孤零零的 '...'，以避免 LLM 把可见的 12 个误当成完整合法值清单。
    """
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    long_enum = [f"v{i:02d}" for i in range(15)]  # 15 > 12，触发截断
    lines = executor._build_plugin_desc_lines([
        {
            "id": "demo_plugin",
            "description": "demo",
            "entries": [
                {
                    "id": "demo_entry",
                    "description": "demo entry",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": long_enum},
                        },
                    },
                },
            ],
        },
    ])
    rendered = "\n".join(lines)

    expected_inner = "|".join(long_enum[:12])
    assert f"kind:string enum=[{expected_inner}|... +3 more]" in rendered
    # 旧的 "]..." 形态必须消失，避免 LLM 误读为"列表完整、后面是注释省略号"
    assert "]..." not in rendered
    # 被截断的值不应该出现在 prompt 里
    for v in long_enum[12:]:
        assert v not in rendered


def test_agent_server_user_turn_fingerprint_includes_attachments():
    from app.agent_server import _build_user_turn_fingerprint as fingerprint

    text_only = fingerprint([{"role": "user", "content": "看图"}])
    with_attachment = fingerprint([
        {
            "role": "user",
            "content": "看图",
            "attachments": [{"type": "image_url", "url": "data:image/png;base64,abc"}],
        }
    ])
    image_only = fingerprint([
        {
            "role": "user",
            "content": "",
            "attachments": [{"type": "image_url", "url": "data:image/png;base64,abc"}],
        }
    ])

    assert text_only != with_attachment
    assert image_only is not None


def test_openclaw_magic_analyze_fingerprint_uses_turn_marker():
    from app.agent_server import _build_analyze_event_fingerprint

    messages = [{"role": "user", "content": "/stop"}]
    first = _build_analyze_event_fingerprint({
        "trigger": "text_openclaw_magic_command",
        "event_id": "event-a",
        "messages": messages,
    })
    second = _build_analyze_event_fingerprint({
        "trigger": "text_openclaw_magic_command",
        "event_id": "event-b",
        "messages": messages,
    })
    ordinary_first = _build_analyze_event_fingerprint({
        "trigger": "turn_end",
        "event_id": "event-a",
        "messages": messages,
    })
    ordinary_second = _build_analyze_event_fingerprint({
        "trigger": "turn_end",
        "event_id": "event-b",
        "messages": messages,
    })

    assert first != second
    assert ordinary_first == ordinary_second


def test_user_message_signature_ignores_metadata_and_role():
    from app.agent_server import _user_message_signature, _last_user_message_signature

    # 非 user 消息 / 无 text → None
    assert _user_message_signature({"role": "assistant", "text": "hi"}) is None
    assert _user_message_signature({"role": "user"}) is None
    assert _user_message_signature("not a dict") is None

    a = {"role": "user", "text": "打开天气网站并截图", "timestamp": 100}
    b = {"role": "user", "text": "打开天气网站并截图", "timestamp": 999, "id": "msg-99"}
    c = {"role": "user", "text": "打开天气网站", "timestamp": 100}
    assert _user_message_signature(a) == _user_message_signature(b)
    assert _user_message_signature(a) != _user_message_signature(c)

    # attachments 进入 signature
    d = {
        "role": "user",
        "text": "看下这个",
        "attachments": [{"url": "https://example.com/x.png"}],
    }
    e = {
        "role": "user",
        "text": "看下这个",
        "attachments": [{"url": "https://example.com/y.png"}],
    }
    assert _user_message_signature(d) != _user_message_signature(e)

    # _last_user_message_signature 取最后一条 user
    messages = [
        {"role": "user", "text": "你好"},
        {"role": "assistant", "text": "嗨"},
        {"role": "user", "text": "打开天气网站并截图"},
        {"role": "assistant", "text": "ok"},
    ]
    assert _last_user_message_signature(messages) == _user_message_signature(messages[2])


def test_redact_passthrough_when_no_cancelled_records(reset_tracker_records):
    from app.agent_server import _redact_cancelled_user_turns

    lanlan = "test-lanlan-redact-passthrough"
    reset_tracker_records(lanlan)

    messages = [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "hello"},
    ]
    assert _redact_cancelled_user_turns(messages, lanlan) is messages


def test_redact_bypasses_first_time_user_msg_with_single_trailing_assistant(reset_tracker_records):
    """核心 bypass 规则：user msg 后面有且仅有 1 条 role='assistant' 的消息时，
    它是"首次被 analyze"——即使 sig 命中 cancelled_sigs 也 bypass。这一次
    bypass 后下次 analyze 时它的 trailing-assistant 计数会涨过 1，自动失去
    豁免。"""
    from app.agent_server import (
        _user_message_signature,
        _redact_cancelled_user_turns,
        _task_tracker,
    )

    lanlan = "test-lanlan-bypass-firsttime"
    reset_tracker_records(lanlan)

    text = "打开天气"
    sig = _user_message_signature({"role": "user", "text": text})
    _task_tracker.record_completed(
        lanlan, task_id="t-cancel", method="browser_use",
        desc=text, success=False, cancelled=True,
        trigger_user_fingerprint=sig,
    )

    # 用户 cancel 之后又发了同文本，messages 末尾就是这条新 user + 它的 reply
    messages = [
        {"role": "user", "text": text},                  # 0: 旧的被取消那条，后面 2 条 assistant
        {"role": "assistant", "text": "正在打开"},        # 1
        {"role": "user", "text": text},                  # 2: 新发的复述，后面 1 条 assistant
        {"role": "assistant", "text": "再次打开中"},      # 3
    ]
    out = _redact_cancelled_user_turns(messages, lanlan)
    # 旧 user (trailing=2) 被 redact，新 user (trailing=1) bypass 保留
    from app.agent_server import REDACTED_USER_TURN_MARKER
    assert out[0] == {"role": "system", "content": REDACTED_USER_TURN_MARKER}
    assert out[1] is messages[2]
    assert out[2] is messages[3]
    assert len(out) == 3


def test_redact_three_repeats_all_redacted_after_user_continues(reset_tracker_records):
    """t/t+1/t+2 连发同文本，单次 cancel → 用户继续聊别的（新 user+assistant）
    → 三条同文本全部 redact（trailing assistant 都 >1），新 user msg
    bypass。这是用户拍板的核心诉求："cancel 抵消之前所有相关请求"。"""
    from app.agent_server import (
        _user_message_signature,
        _redact_cancelled_user_turns,
        _task_tracker,
        REDACTED_USER_TURN_MARKER,
    )

    lanlan = "test-lanlan-three-repeats"
    reset_tracker_records(lanlan)

    text = "打开天气"
    sig = _user_message_signature({"role": "user", "text": text})
    _task_tracker.record_completed(
        lanlan, task_id="t-cancel", method="browser_use",
        desc=text, success=False, cancelled=True,
        trigger_user_fingerprint=sig,
    )

    messages = [
        {"role": "user", "text": text},                  # 0: trailing assistants = 2
        {"role": "user", "text": text},                  # 1: trailing assistants = 2
        {"role": "user", "text": text},                  # 2: trailing assistants = 2
        {"role": "assistant", "text": "尝试打开"},        # 3: 第 1 条 assistant
        {"role": "user", "text": "再聊别的"},             # 4: trailing assistants = 1 → bypass
        {"role": "assistant", "text": "嗯"},              # 5: 第 2 条 assistant
    ]
    out = _redact_cancelled_user_turns(messages, lanlan)
    # 三条 X 都被 redact，"再聊别的" 保留
    assert out[0] == {"role": "system", "content": REDACTED_USER_TURN_MARKER}
    assert out[1] == {"role": "system", "content": REDACTED_USER_TURN_MARKER}
    assert out[2] == {"role": "system", "content": REDACTED_USER_TURN_MARKER}
    # "尝试打开" 紧跟最后一条 X 被吞
    assert out[3] is messages[4]                          # "再聊别的"
    assert out[4] is messages[5]                          # "嗯"
    assert len(out) == 5


def test_redact_drops_earlier_successful_segment_when_same_text_cancelled(reset_tracker_records):
    """by-design 副作用：用户先后用同文本各发一次请求，第一次成功完成、
    第二次被取消。redact 不区分历史里同 sig 的成功段和取消段——所有
    trailing-assistant > 1 的同 sig user msg 都被屏蔽。早先成功段的
    assistant 响应文本因此丢失，但 inject() 仍输出 [COMPLETED] 行让
    analyzer 知道"那个任务做过且成功"，所以语义层面不算严重损失。"""
    from app.agent_server import (
        _user_message_signature,
        _redact_cancelled_user_turns,
        _task_tracker,
        REDACTED_USER_TURN_MARKER,
    )

    lanlan = "test-lanlan-earlier-success"
    reset_tracker_records(lanlan)

    text = "打开天气"
    sig = _user_message_signature({"role": "user", "text": text})
    _task_tracker.record_completed(
        lanlan, task_id="t-cancel", method="browser_use",
        desc=text, success=False, cancelled=True,
        trigger_user_fingerprint=sig,
    )

    messages = [
        {"role": "user", "text": text},                  # 0: 早先成功的同文本
        {"role": "assistant", "text": "好的，截图已发"},   # 1: 早先成功响应
        {"role": "user", "text": "再聊别的"},             # 2
        {"role": "assistant", "text": "嗯"},              # 3
        {"role": "user", "text": text},                  # 4: 最近被取消那次
        {"role": "assistant", "text": "正在打开"},        # 5: 取消任务的进行中痕迹
        {"role": "user", "text": "继续"},                 # 6: 用户继续聊
        {"role": "assistant", "text": "嗯"},              # 7
    ]
    out = _redact_cancelled_user_turns(messages, lanlan)
    # 两条同 sig user msg 都 redact，各自之后的 assistant 段也吞掉
    assert out[0] == {"role": "system", "content": REDACTED_USER_TURN_MARKER}
    assert out[1] is messages[2]                          # "再聊别的" 保留
    assert out[2] is messages[3]                          # 它的 assistant
    assert out[3] == {"role": "system", "content": REDACTED_USER_TURN_MARKER}
    assert out[4] is messages[6]                          # "继续"
    assert out[5] is messages[7]
    assert len(out) == 6


def test_redact_preserves_system_messages_inside_dropped_span(reset_tracker_records):
    """drop_until_next_user 期间只吞 assistant/tool；夹在中间的 system
    消息（session callback / context 注入）跟被取消请求无关，必须保留。"""
    from app.agent_server import (
        _user_message_signature,
        _redact_cancelled_user_turns,
        _task_tracker,
        REDACTED_USER_TURN_MARKER,
    )

    lanlan = "test-lanlan-system-preserve"
    reset_tracker_records(lanlan)

    text = "打开天气"
    _task_tracker.record_completed(
        lanlan, task_id="t1", method="browser_use",
        desc=text, success=False, cancelled=True,
        trigger_user_fingerprint=_user_message_signature({"role": "user", "text": text}),
    )

    # cancelled user msg 后面有 2 条 assistant，所以 trailing=2 → redact
    messages = [
        {"role": "user", "text": text},
        {"role": "assistant", "text": "正在打开..."},
        {"role": "system", "content": "[session callback] something unrelated"},
        {"role": "tool", "content": "browser_screenshot.png"},
        {"role": "user", "text": "再聊别的"},
        {"role": "assistant", "text": "嗯"},
    ]
    out = _redact_cancelled_user_turns(messages, lanlan)
    assert out == [
        {"role": "system", "content": REDACTED_USER_TURN_MARKER},
        # 中间无关的 system 消息保留
        {"role": "system", "content": "[session callback] something unrelated"},
        # assistant + tool 被吞
        {"role": "user", "text": "再聊别的"},
        {"role": "assistant", "text": "嗯"},
    ]


def test_trim_protects_live_cancelled_records_against_cap_pressure():
    """繁忙 session 在 TTL 内积累大量 assigned/completed 时，still-live
    cancelled record 不能被 tail-window 裁剪挤掉——否则它代表的 redact 信号
    会丢失，被取消的 user turn 重新暴露给 analyzer。"""
    from app.agent_server import AgentTaskTracker
    from config import AGENT_TASK_TRACKER_MAX_RECORDS as CAP

    tracker = AgentTaskTracker()
    lanlan = "test-lanlan-trim-protect"

    # 先记一条 cancel，它必须被保住。
    tracker.record_completed(
        lanlan, task_id="cancel-me", method="browser_use",
        desc="x", success=False, cancelled=True,
        trigger_user_fingerprint="protected-sig",
    )

    # 然后填满超过 cap 数量的 assigned/completed 噪声。
    for i in range(CAP * 3):
        tracker.record_assigned(lanlan, task_id=f"t{i}", method="user_plugin", desc=f"task-{i}")
        tracker.record_completed(lanlan, task_id=f"t{i}", method="user_plugin", desc=f"task-{i}", success=True)

    assert len(tracker._records[lanlan]) <= CAP
    sigs = tracker.get_cancelled_user_sigs(lanlan)
    assert "protected-sig" in sigs, (
        f"cancelled record was evicted by _trim under cap pressure; got {sigs}"
    )


def test_user_message_signature_distinguishes_senders():
    """多用户场景：两个不同 user 发同样的文字，sig 必须不同——否则取消 A
    的请求会让 redact 误吞 B 的同文本请求。"""
    from app.agent_server import _user_message_signature

    a_top = {"role": "user", "text": "打开天气", "sender_id": "user-A"}
    b_top = {"role": "user", "text": "打开天气", "sender_id": "user-B"}
    a_meta = {"role": "user", "text": "打开天气", "meta": {"sender_id": "user-A"}}
    a_ctx = {"role": "user", "text": "打开天气", "_ctx": {"user_id": "user-A"}}
    no_sender = {"role": "user", "text": "打开天气"}

    sig_a = _user_message_signature(a_top)
    sig_b = _user_message_signature(b_top)
    assert sig_a and sig_b and sig_a != sig_b
    # 三种来源同一 sender_id 都归一到同一签名
    assert _user_message_signature(a_meta) == sig_a
    assert _user_message_signature(a_ctx) == sig_a
    # 无 sender 与有 sender 不同
    no_sig = _user_message_signature(no_sender)
    assert no_sig and no_sig not in (sig_a, sig_b)


def test_redact_does_not_eat_another_users_same_text_turn(reset_tracker_records):
    """A 取消了 "打开天气"，B 在同一 messages 列表后续发同文本请求——
    redact 应只动 A 的请求（sig 不同），不应误吞 B 的。"""
    from app.agent_server import (
        _user_message_signature,
        _redact_cancelled_user_turns,
        _task_tracker,
        REDACTED_USER_TURN_MARKER,
    )

    lanlan = "test-lanlan-multi-user"
    reset_tracker_records(lanlan)

    a_msg = {"role": "user", "text": "打开天气", "sender_id": "user-A"}
    b_msg = {"role": "user", "text": "打开天气", "sender_id": "user-B"}

    _task_tracker.record_completed(
        lanlan, task_id="t-a-cancel", method="browser_use",
        desc="打开天气", success=False, cancelled=True,
        trigger_user_fingerprint=_user_message_signature(a_msg),
    )

    # A 的 user msg trailing=2（被 redact），B 的 trailing=1（首次 bypass）
    messages = [
        a_msg,
        {"role": "assistant", "text": "正在打开"},
        b_msg,
        {"role": "assistant", "text": "好的"},
    ]
    out = _redact_cancelled_user_turns(messages, lanlan)
    assert out[0] == {"role": "system", "content": REDACTED_USER_TURN_MARKER}
    assert out[1] is b_msg                    # B 的请求保留
    assert out[2] == messages[3]              # B 的 assistant 响应保留
    assert len(out) == 3                      # 只吞 A 的 user + 它后面的 assistant


def test_user_message_payload_text_is_shared_between_signature_and_turn_fingerprint():
    """_user_message_signature 与 _build_user_turn_fingerprint 现在共用同一个
    normalization helper（_user_message_payload_text），避免归一化规则漂移。
    验证：单条 user 消息的 fingerprint = sha256(payload) = signature。
    """
    import hashlib
    from app.agent_server import (
        _user_message_payload_text,
        _user_message_signature,
        _build_user_turn_fingerprint,
    )

    msg = {
        "role": "user",
        "text": "打开天气",
        "attachments": [{"url": "https://example.com/x.png"}],
    }
    payload = _user_message_payload_text(msg)
    expected_sig = hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()
    assert _user_message_signature(msg) == expected_sig
    # 单条 user 消息时，turn fingerprint 也是同一个 payload 的 sha256
    assert _build_user_turn_fingerprint([msg]) == expected_sig

    # 非 user 消息：两个函数都不参与
    assert _user_message_payload_text({"role": "assistant", "text": "hi"}) is None
    assert _user_message_signature({"role": "assistant", "text": "hi"}) is None


def test_inject_skips_records_belonging_to_cancelled_tasks(reset_tracker_records):
    """被取消任务对应的 [ASSIGNED] 与 [CANCELLED] 记录都不应再注入到 analyzer
    视野——其触发的 user turn 已被 redact，重新注入只会把它拉回视野。"""
    from app.agent_server import _task_tracker

    lanlan = "test-lanlan-inject"
    reset_tracker_records(lanlan)

    _task_tracker.record_assigned(
        lanlan, task_id="t1", method="browser_use", desc="打开天气并截图",
    )
    _task_tracker.record_completed(
        lanlan, task_id="t1", method="browser_use",
        desc="打开天气并截图", success=False, cancelled=True,
        trigger_user_fingerprint="sig-1",
    )
    _task_tracker.record_assigned(
        lanlan, task_id="t2", method="user_plugin", desc="另一个任务",
    )
    _task_tracker.record_completed(
        lanlan, task_id="t2", method="user_plugin",
        desc="另一个任务", success=True,
    )

    messages = [{"role": "user", "text": "..."}]
    out = _task_tracker.inject(messages, lanlan)
    summary = next((m for m in out if isinstance(m, dict) and m.get("role") == "system"), None)
    assert summary is not None
    content = summary["content"]
    # 取消任务的 desc 完全不出现
    assert "打开天气并截图" not in content
    assert "[CANCELLED]" not in content
    # 没被取消的任务正常出现
    assert "另一个任务" in content


def test_inject_returns_messages_unchanged_when_all_tasks_cancelled(reset_tracker_records):
    """全部 records 都属于已取消任务 → inject 不应再插入空 summary 消息。"""
    from app.agent_server import _task_tracker

    lanlan = "test-lanlan-inject-empty"
    reset_tracker_records(lanlan)

    _task_tracker.record_assigned(
        lanlan, task_id="t1", method="browser_use", desc="x",
    )
    _task_tracker.record_completed(
        lanlan, task_id="t1", method="browser_use",
        desc="x", success=False, cancelled=True,
        trigger_user_fingerprint="sig-1",
    )

    messages = [{"role": "user", "text": "..."}]
    out = _task_tracker.inject(messages, lanlan)
    assert out is messages


def test_cancel_task_records_trigger_signature_for_redact():
    """cancel_task / _cancel_openclaw_tasks_for_stop 都应把 task info 里的
    _trigger_user_fingerprint 透传到 record_completed，使 redact 能定位回
    触发的 user turn。"""
    targets = {
        "cancel_task": ast.AsyncFunctionDef,
        "_cancel_openclaw_tasks_for_stop": ast.AsyncFunctionDef,
    }
    for name, kind in targets.items():
        source, node = _find_agent_server_function(name, kind)
        segment = ast.get_source_segment(source, node)
        assert "_task_tracker.record_completed" in segment, (
            f"{name} should call record_completed on cancel"
        )
        assert 'trigger_user_fingerprint=info.get("_trigger_user_fingerprint")' in segment, (
            f"{name} should forward _trigger_user_fingerprint to record_completed"
        )
        # 旧的 trigger_user_ts 字段已彻底拆除
        assert "trigger_user_ts" not in segment, (
            f"{name} still references the removed trigger_user_ts field"
        )


@pytest.mark.asyncio
async def test_task_executor_routes_openclaw_as_independent_execution_method():
    from unittest.mock import AsyncMock, MagicMock, patch
    from brain.task_executor import DirectTaskExecutor, UnifiedChannelDecision

    executor = object.__new__(DirectTaskExecutor)
    executor.computer_use = None
    executor.browser_use = None
    executor.openfang = None
    executor.plugin_list = []
    executor._external_plugin_provider = None

    # openclaw adapter 可用
    mock_openclaw = MagicMock()
    mock_openclaw.is_available.return_value = {"ready": True}
    executor.openclaw = mock_openclaw

    # mock 统一渠道评估，让 LLM 选择 qwenpaw
    mock_decision = UnifiedChannelDecision()
    mock_decision.qwenpaw = {"can_execute": True, "task_description": "搜索天气并截图", "reason": "需要浏览器操作"}

    with patch.object(DirectTaskExecutor, "_assess_unified_channels", new_callable=AsyncMock, return_value=mock_decision):
        result = await executor.analyze_and_execute(
            [{"role": "user", "text": "帮我打开浏览器搜索今天天气并截图保存到桌面"}],
            agent_flags={
                "computer_use_enabled": False,
                "browser_use_enabled": False,
                "user_plugin_enabled": False,
                "openclaw_enabled": True,
                "openfang_enabled": False,
            },
        )

    assert result is not None
    assert result.execution_method == "openclaw"
    assert result.tool_args is not None
    assert "instruction" in result.tool_args


@pytest.mark.asyncio
async def test_task_executor_routes_openclaw_with_image_attachments():
    from unittest.mock import AsyncMock, MagicMock, patch
    from brain.task_executor import DirectTaskExecutor, UnifiedChannelDecision

    executor = object.__new__(DirectTaskExecutor)
    executor.computer_use = None
    executor.browser_use = None
    executor.openfang = None
    executor.plugin_list = []
    executor._external_plugin_provider = None

    mock_openclaw = MagicMock()
    mock_openclaw.is_available.return_value = {"ready": True}
    executor.openclaw = mock_openclaw

    mock_decision = UnifiedChannelDecision()
    mock_decision.qwenpaw = {"can_execute": True, "task_description": "分析图片并修复报错", "reason": "需要多模态能力"}

    with patch.object(DirectTaskExecutor, "_assess_unified_channels", new_callable=AsyncMock, return_value=mock_decision):
        result = await executor.analyze_and_execute(
            [{
                "role": "user",
                "content": "帮我修这个报错",
                "attachments": [{"type": "image_url", "url": "data:image/png;base64,abc"}],
            }],
            agent_flags={
                "computer_use_enabled": False,
                "browser_use_enabled": False,
                "user_plugin_enabled": False,
                "openclaw_enabled": True,
                "openfang_enabled": False,
            },
        )

    assert result is not None
    assert result.execution_method == "openclaw"
    assert result.tool_args is not None
    assert result.tool_args["attachments"][0]["url"] == "data:image/png;base64,abc"


def test_openclaw_session_mapping_is_stable_per_sender_and_resettable():
    import threading
    from brain.openclaw_adapter import OpenClawAdapter

    adapter = object.__new__(OpenClawAdapter)
    adapter._session_lock = threading.Lock()
    adapter._session_cache = {}
    adapter._save_session_cache = lambda: None

    sid_one = adapter.get_or_create_persistent_session_id(role_name="LanLan", sender_id="user_a")
    sid_two = adapter.get_or_create_persistent_session_id(role_name="OtherRole", sender_id="user_a")
    sid_three = adapter.get_or_create_persistent_session_id(role_name="LanLan", sender_id="user_b")
    sid_reset = adapter.reset_persistent_session_id(role_name="LanLan", sender_id="user_a")

    assert sid_one == sid_two
    assert sid_three != sid_one
    assert sid_reset != sid_one
    assert adapter.get_or_create_persistent_session_id(role_name="LanLan", sender_id="user_a") == sid_reset


def test_openclaw_responses_payload_uses_stable_session_id_for_conversation():
    from brain.openclaw_adapter import OpenClawAdapter

    adapter = object.__new__(OpenClawAdapter)
    payload = adapter._build_responses_payload(
        session_id="stable-session",
        user_id="user_a",
        channel="console",
        instruction="帮我看下桌面文件",
        attachments=None,
    )

    assert payload["session_id"] == "stable-session"
    assert payload["conversation"]["id"] == "stable-session"
    assert payload["user_id"] == "user_a"
    assert payload["channel"] == "console"


def test_openclaw_process_payload_includes_channel():
    from brain.openclaw_adapter import OpenClawAdapter

    adapter = object.__new__(OpenClawAdapter)
    payload = adapter._build_process_payload(
        session_id="stable-session",
        channel="console",
        instruction="/stop",
        attachments=None,
    )

    assert payload["session_id"] == "stable-session"
    assert payload["channel"] == "console"


@pytest.mark.asyncio
async def test_openclaw_stop_running_falls_back_to_persistent_session():
    import threading
    from brain.openclaw_adapter import OpenClawAdapter

    adapter = object.__new__(OpenClawAdapter)
    adapter._session_lock = threading.Lock()
    adapter._session_cache = {}
    adapter._save_session_cache = lambda: None
    adapter.default_sender_id = "user_a"
    adapter.last_error = None

    session_id = adapter.get_or_create_persistent_session_id(role_name="LanLan", sender_id="user_a")
    result = await adapter.stop_running(sender_id="user_a", role_name="LanLan", task_id="task-1")

    assert result["success"] is True
    assert result["sender_id"] == "user_a"
    assert result["session_id"] == session_id
    assert result["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_openclaw_run_magic_command_rotates_session_after_new():
    import threading
    from unittest.mock import AsyncMock
    from brain.openclaw_adapter import OpenClawAdapter

    adapter = object.__new__(OpenClawAdapter)
    adapter._session_lock = threading.Lock()
    adapter._session_cache = {}
    adapter._save_session_cache = lambda: None
    adapter.default_sender_id = "user_a"
    adapter.run_instruction = AsyncMock(return_value={"success": True, "reply": "backend ok", "raw": {"ok": True}})

    initial_session = adapter.get_or_create_persistent_session_id(role_name="LanLan", sender_id="user_a")
    result = await adapter.run_magic_command("/new", sender_id="user_a", role_name="LanLan")
    current_session = adapter.get_or_create_persistent_session_id(role_name="LanLan", sender_id="user_a")

    assert result["success"] is True
    assert result["command"] == "/new"
    assert result["reply"] == "好的喵！旧的话题存档啦，主人想聊点什么新鲜事？"
    assert result["session_id"] != initial_session
    assert current_session == result["session_id"]


@pytest.mark.asyncio
async def test_task_executor_magic_intent_routes_to_openclaw_before_unified_assessment():
    from unittest.mock import AsyncMock, MagicMock, patch
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    executor.computer_use = None
    executor.browser_use = None
    executor.openfang = None
    executor.plugin_list = []
    executor._external_plugin_provider = None

    mock_openclaw = MagicMock()
    mock_openclaw.is_available.return_value = {"ready": True}
    mock_openclaw.classify_magic_intent = AsyncMock(return_value={"is_magic_intent": True, "command": "/new", "source": "test"})
    mock_openclaw.get_magic_command_task_description.return_value = "开启新的 QwenPaw 话题会话"
    executor.openclaw = mock_openclaw

    with patch.object(DirectTaskExecutor, "_assess_unified_channels", new_callable=AsyncMock) as mock_assess:
        result = await executor.analyze_and_execute(
            [{"role": "user", "content": "我们换个话题吧"}],
            agent_flags={
                "computer_use_enabled": False,
                "browser_use_enabled": False,
                "user_plugin_enabled": False,
                "openclaw_enabled": True,
                "openfang_enabled": False,
            },
        )

    assert result is not None
    assert result.execution_method == "openclaw"
    assert result.tool_args["magic_command"] == "/new"
    assert result.tool_args["direct_reply"] is True
    mock_assess.assert_not_called()


def test_agent_server_openclaw_sender_id_prefers_latest_user_identity():
    source, node = _find_agent_server_function("_resolve_openclaw_sender_id", ast.FunctionDef)
    fn_src = ast.get_source_segment(source, node)
    assert fn_src is not None

    ns = {"AGENT_HISTORY_TURNS": 8}
    exec("from typing import Any\n" + fn_src, ns)
    resolver = ns["_resolve_openclaw_sender_id"]

    result = resolver([
        {"role": "user", "content": "旧消息", "sender_id": "first_user"},
        {"role": "assistant", "content": "处理中"},
        {"role": "user", "content": "最新消息", "metadata": {"user_id": "latest_user"}},
    ])

    assert result == "latest_user"


def test_agent_server_collects_active_openclaw_tasks_for_same_sender():
    source, node = _find_agent_server_function("_collect_active_openclaw_task_ids", ast.FunctionDef)
    fn_src = ast.get_source_segment(source, node)
    assert fn_src is not None

    # channels/openclaw.py accesses the state bag as ``_shared.Modules.<attr>``
    # (single-owner rule of the package split), so the exec namespace exposes
    # the fake Modules through a stand-in ``_shared``.
    import types as _types

    ns = {
        "Optional": __import__("typing").Optional,
        "Modules": type(
            "Modules",
            (),
            {
                "task_registry": {
                    "openclaw-running": {
                        "type": "openclaw",
                        "status": "running",
                        "sender_id": "user_a",
                        "lanlan_name": "LanLan",
                    },
                    "openclaw-completed": {
                        "type": "openclaw",
                        "status": "completed",
                        "sender_id": "user_a",
                        "lanlan_name": "LanLan",
                    },
                    "openclaw-other-user": {
                        "type": "openclaw",
                        "status": "running",
                        "sender_id": "user_b",
                        "lanlan_name": "LanLan",
                    },
                    "browser-running": {
                        "type": "browser_use",
                        "status": "running",
                        "sender_id": "user_a",
                        "lanlan_name": "LanLan",
                    },
                }
            },
        ),
    }
    ns["_shared"] = _types.SimpleNamespace(Modules=ns.pop("Modules"))
    exec(fn_src, ns)
    collector = ns["_collect_active_openclaw_task_ids"]

    result = collector(sender_id="user_a", lanlan_name="LanLan", exclude_task_id="magic-stop")

    assert result == ["openclaw-running"]


def test_cross_server_analyze_request_no_http_fallback_endpoint():
    source = Path("main_logic/cross_server.py").read_text(encoding="utf-8")
    assert "/api/agent/internal/analyze_request" not in source


def test_is_agent_api_ready_allows_free_profile():
    manager = object.__new__(ConfigManager)
    manager.get_model_api_config = lambda _model_type: {
        "model": "free-agent-model",
        "base_url": "https://www.lanlan.tech/text/v1",
        "api_key": "free-access",
    }

    ready, reasons = manager.is_agent_api_ready()
    assert ready is True
    assert len(reasons) == 0


@pytest.mark.parametrize(
    ("agent_api", "expected_reason"),
    [
        ({"model": "", "base_url": "https://u", "api_key": "k"}, "Agent 模型未配置"),
        ({"model": "m", "base_url": "", "api_key": "k"}, "Agent API URL 未配置"),
        ({"model": "m", "base_url": "https://u", "api_key": ""}, "Agent API Key 未配置或不可用"),
    ],
)
def test_is_agent_api_ready_reports_missing_fields(agent_api, expected_reason):
    manager = object.__new__(ConfigManager)
    manager.get_model_api_config = lambda _model_type: agent_api

    ready, reasons = manager.is_agent_api_ready()
    assert ready is False
    assert expected_reason in reasons


def test_is_agent_api_ready_passes_free_access_key_when_agent_not_free():
    """free-access 是非空占位 token：readiness 只看三件套填没填，不再因占位 key + 非免费
    语境而拦截（免费判定已收口到 is_agent_free，脏配置由下游 401 兜底）。"""
    manager = object.__new__(ConfigManager)
    manager.get_model_api_config = lambda _model_type: {
        "model": "m", "base_url": "https://u", "api_key": "free-access",
    }
    ready, reasons = manager.is_agent_api_ready()
    assert ready is True
    assert reasons == []


def test_is_agent_free_tracks_agent_model_not_version():
    """is_agent_free() 只认 agent model 名(free-agent-model)，与 core/assist 的版本免费解耦：
    用免费语音(core=free)但 agent 换自费/自定义 model 时应为 False。"""
    manager = object.__new__(ConfigManager)
    manager.get_model_api_config = lambda _model_type: {"model": "free-agent-model"}
    assert manager.is_agent_free() is True
    manager.get_model_api_config = lambda _model_type: {"model": "qwen3.6-plus-2026-04-02"}
    assert manager.is_agent_free() is False
    manager.get_model_api_config = lambda _model_type: {"model": ""}
    assert manager.is_agent_free() is False


def test_default_free_profile_agent_model_matches_metered_name():
    """回退 DEFAULT_ASSIST_API_PROFILES['free'] 的 agent model 必须等于配额计量名
    (_free_agent_model_name)，否则 api_providers.json 缺失/不可读时免费 agent 不计
    配额、is_agent_free 误判（codex #1593）。锁住 defaults 与计量名一致。"""
    from config import DEFAULT_ASSIST_API_PROFILES
    assert DEFAULT_ASSIST_API_PROFILES['free']['AGENT_MODEL'] == ConfigManager._free_agent_model_name


def test_is_free_voice_tracks_core_not_assist():
    """is_free_voice() 只认 core(CORE_API_TYPE=='free')，与 assist 无关：
    core=qwen+assist=free（自费语音+免费文本）应为 False。"""
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {"CORE_API_TYPE": "free"}
    assert manager.is_free_voice() is True
    manager.get_core_config = lambda: {"CORE_API_TYPE": "qwen"}
    assert manager.is_free_voice() is False
    manager.get_core_config = lambda: {"CORE_API_TYPE": ""}
    assert manager.is_free_voice() is False


def test_agent_gate_is_free_version_field_sources_agent_free():
    """gate / agent 命令回包的 is_free_version 字段值取 is_agent_free()（agent model 维度），
    而非 is_free_voice()（语音/core 维度）——锁住三处同源，防回退。"""
    server = _agent_server_package_source_concat()
    assert '"is_free_version": cm.is_agent_free()' in server
    router = Path("main_routers/agent_router.py").read_text(encoding="utf-8")
    assert "_config_manager.is_agent_free()" in router
    assert "cfg.is_agent_free()" in router


def test_main_server_mounts_card_assist_router():
    source = Path("app/main_server/web_app.py").read_text(encoding="utf-8")

    assert "from main_routers.card_assist_router import router as card_assist_router" in source
    assert "app.include_router(card_assist_router)" in source


def test_agent_command_set_agent_enabled_reports_free_version_and_refreshes_capabilities():
    source, func = _find_agent_server_function("agent_command", ast.AsyncFunctionDef)
    func_src = ast.get_source_segment(source, func) or ""

    assert 'command == "set_agent_enabled"' in func_src
    assert "gate = _check_agent_api_gate()" in func_src
    assert "adapter_refreshed = _try_refresh_computer_use_adapter(force=True)" in func_src
    assert "if not adapter_refreshed and Modules.computer_use is not None:" in func_src
    assert "falling back to existing adapter" in func_src
    assert "if Modules.computer_use is not None:" in func_src
    assert '_fire_agent_llm_connectivity_check(queue=True)' in func_src
    assert '_set_capability("computer_use", False, "AGENT_CU_MODULE_NOT_LOADED")' in func_src
    assert '_set_capability("browser_use", False, "AGENT_CU_MODULE_NOT_LOADED")' in func_src
    assert 'first_reason = (gate.get("reasons") or ["AGENT_ENDPOINT_NOT_CONFIGURED"])[0]' in func_src
    assert '_set_capability("computer_use", False, first_reason)' in func_src
    assert '_set_capability("browser_use", False, first_reason)' in func_src
    assert '"is_free_version": bool(gate.get("is_free_version"))' in func_src
    assert '"agent_api_gate": gate' in func_src


def test_agent_llm_check_marks_browser_use_unloaded_instead_of_pending():
    source, func = _find_agent_server_function(
        "_fire_agent_llm_connectivity_check", ast.AsyncFunctionDef
    )
    func_src = ast.get_source_segment(source, func) or ""

    # capabilities.py accesses the state bag as ``_shared.Modules.<attr>``
    # (single-owner rule of the package split).
    assert "adapter = _shared.Modules.computer_use" in func_src
    assert "if adapter is None:" in func_src
    assert '_set_capability("computer_use", False, "AGENT_CU_MODULE_NOT_LOADED")' in func_src
    assert '_set_capability("browser_use", False, "AGENT_CU_MODULE_NOT_LOADED")' in func_src
    assert "bu = _shared.Modules.browser_use" in func_src
    assert "if bu is None:" in func_src
    assert '_set_capability("browser_use", False, "AGENT_BU_MODULE_NOT_LOADED")' in func_src


def test_agent_ui_v2_free_warning_accepts_command_gate_shape():
    source = Path("static/js/agent_ui_v2.js").read_text(encoding="utf-8")

    assert "const isFreeVersion" in source
    assert "cmdResult.is_free_version" in source
    assert "cmdResult.agent_api_gate && cmdResult.agent_api_gate.is_free_version" in source
    assert "window.showAlert(msg, title)" in source


def test_agent_ui_v2_keeps_agent_status_short_during_tutorial():
    source = Path("static/js/agent_ui_v2.js").read_text(encoding="utf-8")
    status_block = source.split("const setStatus = (msg, options) => {", 1)[1].split(
        "const currentLanlanName",
        1,
    )[0]

    assert "options.stabilizeTutorialText === true" in status_block
    assert "isTutorialAgentStatusLocked()" in status_block
    assert "shouldStabilizeTutorialText ? 'NekoClaw server ready' : (msg || '')" in status_block
    assert "s.textContent = text;" in status_block


def test_agent_popup_state_sync_includes_pngtuber_prefix():
    hud_source = Path("static/common-ui-hud.js").read_text(encoding="utf-8")
    ui_v2_source = Path("static/js/agent_ui_v2.js").read_text(encoding="utf-8")
    legacy_source = Path("static/app/app-agent.js").read_text(encoding="utf-8")

    assert "const avatarPrefix = this && typeof this._avatarPrefix === 'string'" in hud_source
    assert "statusDiv.id = `${avatarPrefix}-agent-status`;" in hud_source

    for suffix in [
        "master",
        "keyboard",
        "browser",
        "user-plugin",
        "openfang",
        "openclaw",
        "status",
    ]:
        assert f"pngtuber-agent-{suffix}" in ui_v2_source

    for suffix in [
        "master",
        "keyboard",
        "browser",
        "user-plugin",
        "openfang",
        "openclaw",
        "status",
    ]:
        assert f"pngtuber-agent-{suffix}" in legacy_source


def test_get_model_api_config_agent_uses_agent_fields_without_custom_switch():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": False,
        "AGENT_MODEL": "agent-model",
        "AGENT_MODEL_URL": "https://agent.example/v1",
        "AGENT_MODEL_API_KEY": "agent-key",
        "OPENROUTER_API_KEY": "fallback-openrouter-key",
        "OPENROUTER_URL": "https://openrouter.example/v1",
    }

    cfg = manager.get_model_api_config("agent")
    # agent 走专用字段但 is_custom 仅反映全局 ENABLE_CUSTOM_API 开关
    assert cfg["is_custom"] is False
    assert cfg["model"] == "agent-model"
    assert cfg["base_url"] == "https://agent.example/v1"
    assert cfg["api_key"] == "agent-key"


def test_get_model_api_config_agent_falls_back_to_assist_when_agent_fields_incomplete():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": False,
        "AGENT_MODEL": "agent-model",
        "AGENT_MODEL_URL": "",
        "AGENT_MODEL_API_KEY": "agent-key",
        "OPENROUTER_API_KEY": "fallback-openrouter-key",
        "OPENROUTER_URL": "https://openrouter.example/v1",
    }

    cfg = manager.get_model_api_config("agent")
    assert cfg["is_custom"] is False
    assert cfg["model"] == "agent-model"
    assert cfg["base_url"] == "https://openrouter.example/v1"
    assert cfg["api_key"] == "fallback-openrouter-key"


def test_get_model_api_config_rejects_unknown_model_type():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {}

    with pytest.raises(ValueError):
        manager.get_model_api_config("unknown_type")


def test_get_model_api_config_realtime_fallback_uses_core_and_api_type():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": False,
        "CORE_MODEL": "core-model",
        "CORE_API_KEY": "core-key",
        "CORE_URL": "https://core.example/v1",
        "CORE_API_TYPE": "qwen",
    }

    cfg = manager.get_model_api_config("realtime")
    assert cfg["is_custom"] is False
    assert cfg["model"] == "core-model"
    assert cfg["api_key"] == "core-key"
    assert cfg["base_url"] == "https://core.example/v1"
    assert cfg["api_type"] == "qwen"


def test_get_model_api_config_realtime_custom_sets_local_api_type():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": True,
        "REALTIME_MODEL": "rt-model",
        "REALTIME_MODEL_URL": "http://localhost:1234/v1",
        "REALTIME_MODEL_API_KEY": "rt-key",
    }

    cfg = manager.get_model_api_config("realtime")
    assert cfg["is_custom"] is True
    assert cfg["model"] == "rt-model"
    assert cfg["base_url"] == "http://localhost:1234/v1"
    assert cfg["api_key"] == "rt-key"
    assert cfg["api_type"] == "local"


def test_get_model_api_config_tts_custom_prefers_qwen_profile(monkeypatch):
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {
        "ENABLE_CUSTOM_API": False,
        "CORE_MODEL": "core-model",
        "ASSIST_API_KEY_QWEN": "qwen-key",
        "OPENROUTER_URL": "https://fallback.example/v1",
    }
    monkeypatch.setattr(
        "utils.config_manager.get_assist_api_profiles",
        lambda: {"qwen": {"OPENROUTER_URL": "https://qwen.example/v1"}},
    )

    cfg = manager.get_model_api_config("tts_custom")
    assert cfg["is_custom"] is False
    assert cfg["api_key"] == "qwen-key"
    assert cfg["base_url"] == "https://qwen.example/v1"






async def test_publish_analyze_and_plan_event_writes_expected_payload(monkeypatch):
    from main_logic.agent_bridge import publish_analyze_and_plan_event

    class DummyWriter:
        def __init__(self):
            self.buffer = b""

        def write(self, data):
            self.buffer += data

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    writer = DummyWriter()

    async def fake_open_connection(host, port):
        assert host == "127.0.0.1"
        assert isinstance(port, int)
        return object(), writer

    monkeypatch.setattr("main_logic.agent_bridge.asyncio.open_connection", fake_open_connection)

    messages = [{"role": "user", "content": "hello"}]
    ok = await publish_analyze_and_plan_event(messages, "LanLan")
    assert ok is True
    payload = json.loads(writer.buffer.decode("utf-8").strip())
    assert payload["type"] == "analyze_and_plan"
    assert payload["messages"] == messages
    assert payload["lanlan_name"] == "LanLan"


async def test_publish_analyze_and_plan_event_returns_false_on_error(monkeypatch):
    from main_logic.agent_bridge import publish_analyze_and_plan_event

    async def fake_open_connection(_host, _port):
        raise OSError("down")

    monkeypatch.setattr("main_logic.agent_bridge.asyncio.open_connection", fake_open_connection)
    ok = await publish_analyze_and_plan_event([], "LanLan")
    assert ok is False


async def test_agent_event_bus_publish_session_event_without_bridge_returns_false():
    import main_logic.agent_event_bus as bus

    bus.set_main_bridge(None)
    ok = await bus.publish_session_event({"type": "turn_end"})
    assert ok is False


async def test_agent_event_bus_publish_session_event_with_bridge(monkeypatch):
    import main_logic.agent_event_bus as bus

    class DummyBridge:
        def __init__(self):
            self.events = []

        async def publish_session_event(self, event):
            self.events.append(event)
            return True

    bridge = DummyBridge()
    bus.set_main_bridge(bridge)
    event = {"type": "turn_end", "session_id": "s1"}
    ok = await bus.publish_session_event(event)
    assert ok is True
    assert bridge.events == [event]
    bus.set_main_bridge(None)


async def test_agent_event_bus_publish_analyze_request_reliably_with_ack():
    import main_logic.agent_event_bus as bus
    import threading

    class DummyBridge:
        def __init__(self):
            self.events = []
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_analyze_request(self, event):
            self.events.append(event)
            bus.notify_analyze_ack(event.get("event_id"))
            return True

    bridge = DummyBridge()
    bridge.owner_loop = asyncio.get_running_loop()
    bridge.owner_thread_id = threading.get_ident()
    bus.set_main_bridge(bridge)
    try:
        ok = await bus.publish_analyze_request_reliably(
            lanlan_name="Tian",
            trigger="turn_end",
            messages=[{"role": "user", "text": "帮我打开系统计算器"}],
            ack_timeout_s=0.2,
            retries=0,
        )
        assert ok is True
        assert len(bridge.events) == 1
        assert bridge.events[0]["event_type"] == "analyze_request"
        assert bridge.events[0]["event_id"]
    finally:
        bus.set_main_bridge(None)


async def test_agent_event_bus_publish_analyze_request_reliably_without_bridge_returns_false():
    import main_logic.agent_event_bus as bus

    bus.set_main_bridge(None)
    ok = await bus.publish_analyze_request_reliably(
        lanlan_name="Tian",
        trigger="turn_end",
        messages=[{"role": "user", "text": "hello"}],
        ack_timeout_s=0.05,
        retries=0,
    )
    assert ok is False


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
def test_zmq_sync_socket_roundtrip():
    """Integration test: verify sync ZMQ PUSH/PULL actually delivers on Windows."""
    import zmq
    import threading
    import time

    addr = "tcp://127.0.0.1:49901"
    ctx = zmq.Context()

    push = ctx.socket(zmq.PUSH)
    push.setsockopt(zmq.LINGER, 500)
    push.bind(addr)

    pull = ctx.socket(zmq.PULL)
    pull.setsockopt(zmq.LINGER, 500)
    pull.setsockopt(zmq.RCVTIMEO, 3000)
    pull.connect(addr)

    received = []

    def recv_fn():
        try:
            msg = pull.recv_json()
            received.append(msg)
        except zmq.Again:
            pass

    t = threading.Thread(target=recv_fn, daemon=True)
    t.start()

    time.sleep(0.1)
    push.send_json({"hello": "world"})

    t.join(timeout=4)
    pull.close()
    push.close()
    ctx.term()

    assert received == [{"hello": "world"}], f"Expected message not received: {received}"


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
async def test_zmq_bridge_end_to_end(monkeypatch):
    """Integration test: full MainBridge -> AgentBridge roundtrip via sync ZMQ."""
    import main_logic.agent_event_bus as bus

    import random
    base = random.randint(55000, 59000)
    test_pub_addr = f"tcp://127.0.0.1:{base}"
    test_push_addr = f"tcp://127.0.0.1:{base + 1}"
    test_analyze_addr = f"tcp://127.0.0.1:{base + 2}"
    monkeypatch.setattr(bus, "SESSION_PUB_ADDR", test_pub_addr)
    monkeypatch.setattr(bus, "AGENT_PUSH_ADDR", test_push_addr)
    monkeypatch.setattr(bus, "ANALYZE_PUSH_ADDR", test_analyze_addr)

    received_on_agent = []
    received_on_main = []

    async def fake_on_session_event(event):
        received_on_agent.append(event)
        if event.get("event_type") == "analyze_request":
            event_id = event.get("event_id")
            if event_id and agent_bridge.push is not None:
                agent_bridge.push.send_json(
                    {"event_type": "analyze_ack", "event_id": event_id},
                    __import__("zmq").NOBLOCK,
                )

    async def fake_on_agent_event(event):
        received_on_main.append(event)
        if event.get("event_type") == "analyze_ack":
            bus.notify_analyze_ack(event.get("event_id", ""))

    main_bridge = bus.MainServerAgentBridge(on_agent_event=fake_on_agent_event)
    agent_bridge = bus.AgentServerEventBridge(on_session_event=fake_on_session_event)

    await main_bridge.start()
    await agent_bridge.start()

    await asyncio.sleep(0.3)

    bus.set_main_bridge(main_bridge)
    try:
        ok = await bus.publish_analyze_request_reliably(
            lanlan_name="TestChar",
            trigger="test",
            messages=[{"role": "user", "content": "hello"}],
            ack_timeout_s=2.0,
            retries=1,
        )
        assert ok is True, "analyze_request was not acked"

        await asyncio.sleep(0.5)
        assert any(
            e.get("event_type") == "analyze_request" for e in received_on_agent
        ), f"Agent did not receive analyze_request: {received_on_agent}"
        assert any(
            e.get("event_type") == "analyze_ack" for e in received_on_main
        ), f"Main did not receive analyze_ack: {received_on_main}"
    finally:
        bus.set_main_bridge(None)
        main_bridge._stop.set()
        agent_bridge._stop.set()
        await asyncio.sleep(1.5)
        for s in [main_bridge.pub, main_bridge.analyze_push, main_bridge.pull,
                   agent_bridge.sub, agent_bridge.analyze_pull, agent_bridge.push]:
            if s is not None:
                try:
                    s.close(linger=0)
                except Exception:
                    pass
        for ctx in [main_bridge.ctx, agent_bridge.ctx]:
            if ctx is not None:
                try:
                    ctx.term()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
#  ZMQ PUB/SUB roundtrip (main → agent session events)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
async def test_zmq_pubsub_roundtrip(monkeypatch):
    """Real ZMQ PUB/SUB: main publishes session event, agent receives it."""
    import main_logic.agent_event_bus as bus
    import random

    base = random.randint(55100, 55900)
    monkeypatch.setattr(bus, "SESSION_PUB_ADDR", f"tcp://127.0.0.1:{base}")
    monkeypatch.setattr(bus, "AGENT_PUSH_ADDR", f"tcp://127.0.0.1:{base + 1}")
    monkeypatch.setattr(bus, "ANALYZE_PUSH_ADDR", f"tcp://127.0.0.1:{base + 2}")

    received = []

    async def on_session(event):
        received.append(event)

    async def on_agent(event):
        pass

    main_br = bus.MainServerAgentBridge(on_agent_event=on_agent)
    agent_br = bus.AgentServerEventBridge(on_session_event=on_session)

    await main_br.start()
    await agent_br.start()
    await asyncio.sleep(0.3)
    bus.set_main_bridge(main_br)
    try:
        await main_br.publish_session_event({"event_type": "turn_end", "data": 42})
        await asyncio.sleep(1.0)
        assert any(e.get("event_type") == "turn_end" for e in received), \
            f"Agent did not receive PUB/SUB event: {received}"
    finally:
        bus.set_main_bridge(None)
        main_br._stop.set()
        agent_br._stop.set()
        await asyncio.sleep(1.5)
        for s in [main_br.pub, main_br.analyze_push, main_br.pull,
                   agent_br.sub, agent_br.analyze_pull, agent_br.push]:
            if s:
                try: s.close(linger=0)
                except Exception: pass
        for c in [main_br.ctx, agent_br.ctx]:
            if c:
                try: c.term()
                except Exception: pass


# ---------------------------------------------------------------------------
#  ZMQ PUSH/PULL roundtrip (agent → main)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
async def test_zmq_agent_to_main_push_pull(monkeypatch):
    """Real ZMQ PUSH/PULL: agent emits event, main receives it."""
    import main_logic.agent_event_bus as bus
    import random

    base = random.randint(56000, 56900)
    monkeypatch.setattr(bus, "SESSION_PUB_ADDR", f"tcp://127.0.0.1:{base}")
    monkeypatch.setattr(bus, "AGENT_PUSH_ADDR", f"tcp://127.0.0.1:{base + 1}")
    monkeypatch.setattr(bus, "ANALYZE_PUSH_ADDR", f"tcp://127.0.0.1:{base + 2}")

    received = []

    async def on_session(event):
        pass

    async def on_agent(event):
        received.append(event)

    main_br = bus.MainServerAgentBridge(on_agent_event=on_agent)
    agent_br = bus.AgentServerEventBridge(on_session_event=on_session)

    await main_br.start()
    await agent_br.start()
    await asyncio.sleep(0.3)
    try:
        ok = await agent_br.emit_to_main({"event_type": "task_result", "task_id": "t1"})
        assert ok is True
        await asyncio.sleep(1.0)
        assert any(e.get("event_type") == "task_result" for e in received), \
            f"Main did not receive agent→main PUSH event: {received}"
    finally:
        main_br._stop.set()
        agent_br._stop.set()
        await asyncio.sleep(1.5)
        for s in [main_br.pub, main_br.analyze_push, main_br.pull,
                   agent_br.sub, agent_br.analyze_pull, agent_br.push]:
            if s:
                try: s.close(linger=0)
                except Exception: pass
        for c in [main_br.ctx, agent_br.ctx]:
            if c:
                try: c.term()
                except Exception: pass


# ---------------------------------------------------------------------------
#  _emit_main_event (agent_server.py)
# ---------------------------------------------------------------------------

def test_emit_main_event_sends_via_bridge():
    """_emit_main_event calls agent_bridge.emit_to_main when bridge is available."""
    source, func = _find_agent_server_function("_emit_main_event", ast.AsyncFunctionDef)
    assert _contains_call(func, "emit_to_main"), \
        "_emit_main_event does not call emit_to_main"


def test_emit_main_event_no_http_fallback():
    """_emit_main_event must NOT contain any httpx or HTTP fallback code."""
    source, func = _find_agent_server_function("_emit_main_event", ast.AsyncFunctionDef)
    func_source = ast.get_source_segment(source, func) or ""
    assert "httpx" not in func_source, "_emit_main_event still contains httpx HTTP fallback"
    assert "http://" not in func_source, "_emit_main_event still contains HTTP URL"


# ---------------------------------------------------------------------------
#  _on_session_event (agent_server.py)
# ---------------------------------------------------------------------------

def test_on_session_event_dispatches_ack_and_analyze():
    """_on_session_event creates tasks for ack emission and background analysis."""
    source, func = _find_agent_server_function("_on_session_event", ast.AsyncFunctionDef)
    func_src = ast.get_source_segment(source, func) or ""
    assert "analyze_ack" in func_src, "_on_session_event does not emit analyze_ack"
    assert "_background_analyze_and_plan" in func_src, \
        "_on_session_event does not call _background_analyze_and_plan"
    assert "create_task" in func_src, \
        "_on_session_event does not use create_task for async dispatch"


# ---------------------------------------------------------------------------
#  publish_session_event_threadsafe from different thread
# ---------------------------------------------------------------------------

async def test_publish_session_event_threadsafe_from_different_thread():
    """Threadsafe publish correctly delivers from non-owner thread."""
    import main_logic.agent_event_bus as bus
    import threading

    published = []

    class DummyBridge:
        def __init__(self):
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_session_event(self, event):
            published.append(event)
            return True

        async def publish_session_event_threadsafe(self, event):
            if self.owner_loop is None:
                return False
            if threading.get_ident() == self.owner_thread_id:
                return await self.publish_session_event(event)
            try:
                cf = asyncio.run_coroutine_threadsafe(
                    self.publish_session_event(event), self.owner_loop,
                )
                return await asyncio.wrap_future(cf)
            except Exception:
                return False

    bridge = DummyBridge()
    bridge.owner_loop = asyncio.get_running_loop()
    bridge.owner_thread_id = threading.get_ident()
    bus.set_main_bridge(bridge)

    result_holder = [None]
    error_holder = [None]

    async def _publish_from_thread():
        try:
            ok = await bus.publish_session_event_threadsafe(
                {"event_type": "turn_end", "from_thread": True}
            )
            result_holder[0] = ok
        except Exception as e:
            error_holder[0] = e

    def thread_fn():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_publish_from_thread())
        loop.close()

    t = threading.Thread(target=thread_fn)
    t.start()
    t.join(timeout=5)

    await asyncio.sleep(0.2)
    bus.set_main_bridge(None)
    assert error_holder[0] is None, f"Thread publish raised: {error_holder[0]}"
    assert result_holder[0] is True
    assert len(published) == 1
    assert published[0]["from_thread"] is True


# ---------------------------------------------------------------------------
#  Analyze request ack timeout + retry
# ---------------------------------------------------------------------------

async def test_analyze_request_reliably_retries_on_timeout():
    """publish_analyze_request_reliably retries when ack times out."""
    import main_logic.agent_event_bus as bus
    import threading

    attempts = []

    class SlowAckBridge:
        def __init__(self):
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_analyze_request(self, event):
            attempts.append(event.get("event_id"))
            return True

    bridge = SlowAckBridge()
    bridge.owner_loop = asyncio.get_running_loop()
    bridge.owner_thread_id = threading.get_ident()
    bus.set_main_bridge(bridge)
    try:
        ok = await bus.publish_analyze_request_reliably(
            lanlan_name="Test",
            trigger="test",
            messages=[{"role": "user", "content": "hi"}],
            ack_timeout_s=0.05,
            retries=2,
        )
        assert ok is False, "Should have failed after all retries"
        assert len(attempts) == 3, f"Expected 3 attempts (1 + 2 retries), got {len(attempts)}"
        assert all(eid == attempts[0] for eid in attempts), \
            "All attempts should use the same event_id"
    finally:
        bus.set_main_bridge(None)


async def test_analyze_request_reliably_returns_true_on_delayed_ack():
    """publish_analyze_request_reliably succeeds when ack arrives within timeout."""
    import main_logic.agent_event_bus as bus
    import threading

    class DelayedAckBridge:
        def __init__(self):
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_analyze_request(self, event):
            eid = event.get("event_id")
            asyncio.get_running_loop().call_later(
                0.05, lambda: bus.notify_analyze_ack(eid)
            )
            return True

    bridge = DelayedAckBridge()
    bridge.owner_loop = asyncio.get_running_loop()
    bridge.owner_thread_id = threading.get_ident()
    bus.set_main_bridge(bridge)
    try:
        ok = await bus.publish_analyze_request_reliably(
            lanlan_name="Test",
            trigger="test",
            messages=[{"role": "user", "content": "hi"}],
            ack_timeout_s=0.5,
            retries=0,
        )
        assert ok is True
    finally:
        bus.set_main_bridge(None)


# ---------------------------------------------------------------------------
#  Bridge not ready: all publish methods return False
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("zmq") is None,
    reason="pyzmq not installed",
)
async def test_real_bridge_not_started_returns_false():
    """MainServerAgentBridge.publish_* returns False before start() is called."""
    import main_logic.agent_event_bus as bus

    async def noop(event): pass

    bridge = bus.MainServerAgentBridge(on_agent_event=noop)
    agent_bridge = bus.AgentServerEventBridge(on_session_event=noop)

    assert await bridge.publish_session_event({"t": 1}) is False
    assert await bridge.publish_analyze_request({"t": 1}) is False
    assert await agent_bridge.emit_to_main({"t": 1}) is False


# ---------------------------------------------------------------------------
#  _publish_analyze_request_with_fallback (cross_server.py)
# ---------------------------------------------------------------------------

async def test_cross_server_publish_returns_true_on_success(monkeypatch):
    """_publish_analyze_request_with_fallback returns True when reliably delivered."""
    from main_logic.cross_server import _publish_analyze_request_with_fallback

    async def fake_reliably(**kw):
        return True

    monkeypatch.setattr(
        "main_logic.cross_server.publish_analyze_request_reliably",
        fake_reliably,
    )

    ok = await _publish_analyze_request_with_fallback("Tian", "turn_end", [{"role": "user", "content": "hi"}])
    assert ok is True


async def test_cross_server_publish_returns_false_on_failure(monkeypatch):
    """_publish_analyze_request_with_fallback returns False when delivery fails."""
    from main_logic.cross_server import _publish_analyze_request_with_fallback

    async def fake_reliably(**kw):
        return False

    monkeypatch.setattr(
        "main_logic.cross_server.publish_analyze_request_reliably",
        fake_reliably,
    )

    ok = await _publish_analyze_request_with_fallback("Tian", "turn_end", [{"role": "user", "content": "hi"}])
    assert ok is False


async def test_cross_server_publish_returns_false_on_exception(monkeypatch):
    """_publish_analyze_request_with_fallback returns False when exception is raised."""
    from main_logic.cross_server import _publish_analyze_request_with_fallback

    async def fake_reliably(**kw):
        raise RuntimeError("zmq exploded")

    monkeypatch.setattr(
        "main_logic.cross_server.publish_analyze_request_reliably",
        fake_reliably,
    )

    ok = await _publish_analyze_request_with_fallback("Tian", "turn_end", [{"role": "user", "content": "hi"}])
    assert ok is False


def test_cross_server_publish_no_http_fallback():
    """_publish_analyze_request_with_fallback must NOT contain HTTP fallback."""
    source = Path("main_logic/cross_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_publish_analyze_request_with_fallback":
            func = node
            break
    assert func is not None
    func_src = ast.get_source_segment(source, func) or ""
    assert "aiohttp.ClientSession" not in func_src, \
        "_publish_analyze_request_with_fallback still contains HTTP fallback"
    assert "/agent/analyze_request" not in func_src, \
        "_publish_analyze_request_with_fallback still targets HTTP endpoint"


class _FakeInternalResponse:
    def __init__(self, status, body):
        self.status_code = status
        self.text = body


def _make_fake_internal_client(status, body, capture=None):
    class FakeInternalClient:
        async def post(self, url, **kwargs):
            if capture is not None:
                capture.append({"url": url, **kwargs})
            return _FakeInternalResponse(status, body)

    return FakeInternalClient


async def test_cross_server_post_memory_server_success_and_url_encoding(monkeypatch):
    """_post_memory_server should treat 2xx + JSON body as success and URL-encode names."""
    from main_logic.cross_server import _post_memory_server

    calls = []

    monkeypatch.setattr(
        "main_logic.cross_server.get_internal_http_client",
        lambda: _make_fake_internal_client(
            200,
            json.dumps({"status": "cached", "count": 2}, ensure_ascii=False),
            capture=calls,
        )(),
    )

    ok, err_detail, payload = await _post_memory_server(
        "cache",
        "小天/测试",
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        timeout_s=3.0,
    )

    assert ok is True
    assert err_detail == ""
    assert payload.get("status") == "cached"
    assert calls
    assert calls[0]["url"].endswith("/cache/%E5%B0%8F%E5%A4%A9%2F%E6%B5%8B%E8%AF%95")
    assert "input_history" in calls[0]["json"]


async def test_cross_server_post_memory_server_handles_http_non_2xx(monkeypatch):
    """_post_memory_server should convert non-2xx response into explicit error detail."""
    from main_logic.cross_server import _post_memory_server

    monkeypatch.setattr(
        "main_logic.cross_server.get_internal_http_client",
        lambda: _make_fake_internal_client(502, "bad gateway")(),
    )

    ok, err_detail, payload = await _post_memory_server(
        "cache",
        "Tian",
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        timeout_s=3.0,
    )

    assert ok is False
    assert "HTTP 502" in err_detail
    assert payload == {}


async def test_cross_server_post_memory_server_handles_non_json_2xx(monkeypatch):
    """_post_memory_server should fail loudly when body is non-JSON despite 2xx."""
    from main_logic.cross_server import _post_memory_server

    monkeypatch.setattr(
        "main_logic.cross_server.get_internal_http_client",
        lambda: _make_fake_internal_client(200, "<html>oops</html>")(),
    )

    ok, err_detail, payload = await _post_memory_server(
        "cache",
        "Tian",
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        timeout_s=3.0,
    )

    assert ok is False
    assert "non-JSON response" in err_detail
    assert payload == {}


async def test_cross_server_post_memory_server_handles_business_error(monkeypatch):
    """_post_memory_server should return explicit error when memory_server returns status=error."""
    from main_logic.cross_server import _post_memory_server

    monkeypatch.setattr(
        "main_logic.cross_server.get_internal_http_client",
        lambda: _make_fake_internal_client(200, json.dumps({"status": "error", "message": "boom"}))(),
    )

    ok, err_detail, payload = await _post_memory_server(
        "cache",
        "Tian",
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        timeout_s=3.0,
    )

    assert ok is False
    assert err_detail == "boom"
    assert payload.get("status") == "error"


def test_cross_server_session_end_uses_settle_for_zero_remaining():
    """session end must call /settle when everything was already /cache-synced."""
    source = Path("main_logic/cross_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {"_settle_endpoint", "_settle_payload"}:
                assignments[target.id] = ast.dump(node.value, include_attributes=False)

    assert assignments["_settle_endpoint"] == ast.dump(
        ast.parse('"process" if remaining else "settle"', mode="eval").body,
        include_attributes=False,
    )
    assert assignments["_settle_payload"] == ast.dump(
        ast.parse("remaining if remaining else []", mode="eval").body,
        include_attributes=False,
    )


def test_cross_server_memory_cache_failure_paths_are_selective():
    """No warning-rate-limit helper should be reintroduced for memory cache writes."""
    source = Path("main_logic/cross_server.py").read_text(encoding="utf-8")
    assert "MEMORY_WRITE_WARN_WINDOW_S" not in source
    assert "_warn_memory_write_issue_rate_limited" not in source


def test_cross_server_memory_write_exception_classification():
    from main_logic.cross_server import _is_expected_memory_write_exception
    import aiohttp

    assert _is_expected_memory_write_exception(asyncio.TimeoutError()) is True
    assert _is_expected_memory_write_exception(aiohttp.ClientError("x")) is True
    assert _is_expected_memory_write_exception(ConnectionError("x")) is True
    assert _is_expected_memory_write_exception(OSError("x")) is True
    assert _is_expected_memory_write_exception(ValueError("x")) is False


def test_cross_server_memory_cache_exception_logs_warning_once_then_debug(monkeypatch):
    import main_logic.cross_server as cs

    warning_msgs = []
    debug_msgs = []

    monkeypatch.setattr(
        cs.logger,
        "warning",
        lambda msg, *args, **kwargs: warning_msgs.append(msg % args if args else msg),
    )
    monkeypatch.setattr(
        cs.logger,
        "debug",
        lambda msg, *args, **kwargs: debug_msgs.append(msg % args if args else msg),
    )

    health_state = {cs.MEMORY_CACHE_SCOPE_TURN_END: False}
    cs._mark_memory_cache_exception("小天", cs.MEMORY_CACHE_SCOPE_TURN_END, asyncio.TimeoutError(), health_state)
    cs._mark_memory_cache_exception("小天", cs.MEMORY_CACHE_SCOPE_TURN_END, asyncio.TimeoutError(), health_state)

    assert len(warning_msgs) == 1
    assert len(debug_msgs) == 1
    assert "进入异常状态" in warning_msgs[0]
    assert "持续" in debug_msgs[0]
    assert health_state[cs.MEMORY_CACHE_SCOPE_TURN_END] is True


def test_cross_server_unknown_memory_cache_exception_keeps_traceback(monkeypatch):
    import main_logic.cross_server as cs

    warning_calls = []
    monkeypatch.setattr(
        cs.logger,
        "warning",
        lambda msg, *args, **kwargs: warning_calls.append(
            {"message": msg % args if args else msg, "kwargs": kwargs}
        ),
    )

    health_state = {cs.MEMORY_CACHE_SCOPE_TURN_END: False}
    cs._mark_memory_cache_exception("小天", cs.MEMORY_CACHE_SCOPE_TURN_END, ValueError("bad payload"), health_state)

    assert len(warning_calls) == 1
    assert "未知类型" in warning_calls[0]["message"]
    assert warning_calls[0]["kwargs"].get("exc_info") is True


def test_cross_server_memory_cache_business_failure_and_recovery(monkeypatch):
    import main_logic.cross_server as cs

    debug_msgs = []
    info_msgs = []

    monkeypatch.setattr(
        cs.logger,
        "debug",
        lambda msg, *args, **kwargs: debug_msgs.append(msg % args if args else msg),
    )
    monkeypatch.setattr(
        cs.logger,
        "info",
        lambda msg, *args, **kwargs: info_msgs.append(msg % args if args else msg),
    )

    health_state = {cs.MEMORY_CACHE_SCOPE_AVATAR: False}
    cs._mark_memory_cache_business_failure("小天", cs.MEMORY_CACHE_SCOPE_AVATAR, "boom", health_state)
    cs._mark_memory_cache_business_failure("小天", cs.MEMORY_CACHE_SCOPE_AVATAR, "boom2", health_state)
    cs._mark_memory_cache_success("小天", cs.MEMORY_CACHE_SCOPE_AVATAR, health_state)
    cs._mark_memory_cache_success("小天", cs.MEMORY_CACHE_SCOPE_AVATAR, health_state)

    assert "进入失败状态" in debug_msgs[0]
    assert "持续" in debug_msgs[1]
    assert len(info_msgs) == 1
    assert "已恢复" in info_msgs[0]
    assert health_state[cs.MEMORY_CACHE_SCOPE_AVATAR] is False


# ---------------------------------------------------------------------------
#  Concurrent analyze requests with correct ack matching
# ---------------------------------------------------------------------------

async def test_concurrent_analyze_requests_match_acks_correctly():
    """Multiple concurrent analyze_request_reliably calls each get their own ack."""
    import main_logic.agent_event_bus as bus
    import threading

    ack_delays = {"req1": 0.05, "req2": 0.10}

    class ConcurrentBridge:
        def __init__(self):
            self.owner_loop = None
            self.owner_thread_id = None

        async def publish_analyze_request(self, event):
            eid = event.get("event_id")
            name = event.get("lanlan_name")
            delay = ack_delays.get(name, 0.05)
            asyncio.get_running_loop().call_later(
                delay, lambda: bus.notify_analyze_ack(eid)
            )
            return True

    bridge = ConcurrentBridge()
    bridge.owner_loop = asyncio.get_running_loop()
    bridge.owner_thread_id = threading.get_ident()
    bus.set_main_bridge(bridge)
    try:
        results = await asyncio.gather(
            bus.publish_analyze_request_reliably(
                lanlan_name="req1", trigger="t", messages=[{"r": "u"}],
                ack_timeout_s=1.0, retries=0,
            ),
            bus.publish_analyze_request_reliably(
                lanlan_name="req2", trigger="t", messages=[{"r": "u"}],
                ack_timeout_s=1.0, retries=0,
            ),
        )
        assert results == [True, True], f"Expected both True, got {results}"
    finally:
        bus.set_main_bridge(None)
