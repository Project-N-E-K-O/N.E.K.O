import ast
import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from starlette.requests import Request

from utils.config_manager import ConfigManager, get_config_manager


def _expected_plugin_dashboard_location(v: str = "") -> str:
    from config import USER_PLUGIN_BASE

    base_ui = USER_PLUGIN_BASE.rstrip("/") + "/ui"
    return f"{base_ui}?{urlencode({'v': v})}" if v else base_ui


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
    paths = _route_paths_from_decorators("agent_server.py", "app")
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
    handoff_source = Path("static/yui-guide-page-handoff.js").read_text(encoding="utf-8")
    director_source = Path("static/yui-guide-director.js").read_text(encoding="utf-8")
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


def test_agent_server_expected_event_driven_endpoints_exist():
    paths = _route_paths_from_decorators("agent_server.py", "app")
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


def test_agent_router_has_internal_analyze_request_endpoint():
    paths = _route_paths_from_decorators("main_routers/agent_router.py", "router")
    assert "/internal/analyze_request" in paths


def test_yui_guide_steps_registry_keeps_m1_to_m4_home_flow_contract():
    source = Path("static/yui-guide-steps.js").read_text(encoding="utf-8")

    for expected in (
        "const CONTRACT_VERSION = 2;",
        "'intro_basic'",
        "'takeover_capture_cursor'",
        "'takeover_plugin_preview'",
        "'takeover_settings_peek'",
        "'takeover_return_control'",
        "'handoff_api_key'",
        "'handoff_memory_browser'",
        "'handoff_plugin_dashboard'",
        "steps.handoff_api_key.navigation.resumeScene = 'api_key_intro';",
        "steps.handoff_memory_browser.navigation.resumeScene = 'memory_browser_intro';",
        "steps.handoff_plugin_dashboard.navigation.resumeScene = 'plugin_dashboard_landing';",
        "steps.plugin_dashboard_landing = createBaseStep('plugin_dashboard_landing', 'plugin_dashboard', '#plugin-list');",
        "steps.api_key_intro = createBaseStep('api_key_intro', 'api_key', '#coreApiSelect-dropdown-trigger');",
        "steps.memory_browser_intro = createBaseStep('memory_browser_intro', 'memory_browser', '#memory-file-list');",
        "api_key: ['api_key_intro']",
        "memory_browser: ['memory_browser_intro']",
        "plugin_dashboard: ['plugin_dashboard_landing']",
    ):
        assert expected in source

    # Keep concatenated literals here so search/grep does not match this test file.
    for removed in (
        "'intro_" + "proactive'",
        "'intro_" + "cat_paw'",
        "steps.intro_" + "proactive",
        "steps.intro_" + "cat_paw",
        "'handoff_steam_workshop'",
        "steps.handoff_steam_workshop",
        "steps.steam_workshop_intro",
        "steam_workshop: ['steam_workshop_intro']",
        "/steam_workshop_manager",
    ):
        assert removed not in source


def test_yui_guide_overlay_supports_progress_meta_and_viewport_placement():
    overlay_source = Path("static/yui-guide-overlay.js").read_text(encoding="utf-8")
    director_source = Path("static/yui-guide-director.js").read_text(encoding="utf-8")
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


def test_yui_guide_cat_paw_click_state_is_visible_before_actions():
    overlay_source = Path("static/yui-guide-overlay.js").read_text(encoding="utf-8")
    director_source = Path("static/yui-guide-director.js").read_text(encoding="utf-8")
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
    "yui-guide-steps.js",
    "yui-guide-overlay.js",
    "yui-guide-page-handoff.js",
    "yui-guide-director.js",
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
        for name in (*_YUI_RUNTIME_SCRIPTS, "universal-tutorial-manager.js")
    ]
    assert positions == sorted(positions)


def test_home_template_loads_yui_wakeup_before_director():
    source = Path("templates/index.html").read_text(encoding="utf-8")

    positions = [
        _script_tag_position(source, name)
        for name in (
            "yui-guide-overlay.js",
            "yui-guide-page-handoff.js",
            "yui-guide-wakeup.js",
            "yui-guide-director.js",
            "universal-tutorial-manager.js",
        )
    ]
    assert positions == sorted(positions)


def test_yui_wakeup_live2d_session_keeps_m2_boundaries():
    source = Path("static/yui-guide-wakeup.js").read_text(encoding="utf-8")
    live2d_source = Path("static/live2d-model.js").read_text(encoding="utf-8")
    style_source = Path("static/css/yui-guide.css").read_text(encoding="utf-8")
    yui_model = json.loads(Path("static/yui-origin/yui-origin.model3.json").read_text(encoding="utf-8"))
    yui_display_info = json.loads(Path("static/yui-origin/yui-origin.cdi3.json").read_text(encoding="utf-8"))
    yui_param_ids = {
        item.get("Id")
        for item in yui_display_info.get("Parameters", [])
        if isinstance(item, dict)
    }

    assert "class Live2DWakeupSession" in source
    assert "DEFAULT_DURATION_MS = 4000" in source
    assert "LIVE2D_HANDOFF_MS = 620" in source
    assert "LIVE2D_HANDOFF_MS" in source
    assert "_suspendEyeBlinkOverride" in source
    assert "removeBlockingGuideOverlay" in source
    assert "#yui-guide-overlay" in source
    assert "yui-taking-over" in source
    assert "setTemporaryPoseOverride" in source
    assert "applyTemporaryPose" in source
    assert "restoreCapturedParams()" in source
    assert "this.clearTemporaryPoseOverride();" in source
    assert "this.clearMotionHold();" in source
    assert "this.restoreCapturedParams();" in source
    assert "if (!this.usesTemporaryPoseOverride && this.isCurrentModel())" not in source
    assert "Live2DManager.prototype.setTemporaryPoseOverride" in live2d_source
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
        assert param_id in source

    yui_file_refs = yui_model.get("FileReferences", {})
    assert yui_file_refs.get("Moc") == "yui-origin.moc3"
    assert yui_file_refs.get("DisplayInfo") == "yui-origin.cdi3.json"
    for param_id in ("Param75", "Param90", "Param92", "Param95"):
        assert param_id in yui_param_ids

    assert "coreModel.update =" not in source
    assert "motionManager.update =" not in source
    assert "model.focus(" not in source
    assert "document.createElement" not in source
    assert "appendChild" not in source
    assert "yui-guide-wakeup-stage" not in style_source
    assert "yui-guide-wakeup-backdrop" not in style_source
    assert "yui-guide-wakeup-particle" not in style_source


def test_target_page_templates_load_yui_runtime_stack_before_tutorial_manager():
    for template_path in (
        "templates/api_key_settings.html",
        "templates/memory_browser.html",
    ):
        source = Path(template_path).read_text(encoding="utf-8")
        positions = [
            _script_tag_position(source, name)
            for name in (*_YUI_RUNTIME_SCRIPTS, "universal-tutorial-manager.js")
        ]
        assert positions == sorted(positions), template_path
        _stylesheet_tag_position(source, "yui-guide.css")


def test_home_yui_guide_does_not_route_to_steam_workshop():
    yui_source = Path("static/yui-guide-steps.js").read_text(encoding="utf-8")
    tutorial_source = Path("static/universal-tutorial-manager.js").read_text(encoding="utf-8")

    assert "handoff_steam_workshop" not in yui_source
    assert "/steam_workshop_manager" not in yui_source
    assert "yuiGuideSceneId: 'handoff_steam_workshop'" not in tutorial_source
    assert "#${p}-menu-steam-workshop" not in tutorial_source


def test_universal_tutorial_manager_normalizes_api_key_handoff_and_resume_scene_mappings():
    source = Path("static/universal-tutorial-manager.js").read_text(encoding="utf-8")

    for expected in (
        "getYuiGuidePageKey(page = this.currentPage)",
        "return 'api_key';",
        "getPendingYuiGuideResumeScene(page = this.currentPage)",
        "applyYuiGuideResumeScene(validSteps)",
        "yuiGuideSceneId: 'api_key_intro'",
        "yuiGuideSceneId: 'memory_browser_intro'",
    ):
        assert expected in source


def test_home_yui_guide_avatar_override_does_not_persist_tutorial_model():
    tutorial_source = Path("static/universal-tutorial-manager.js").read_text(encoding="utf-8")
    interpage_source = Path("static/app-interpage.js").read_text(encoding="utf-8")

    begin_start = tutorial_source.index("beginTutorialAvatarOverride()")
    restore_start = tutorial_source.index("restoreTutorialAvatarOverride()")
    restore_end = tutorial_source.index("/**", restore_start)
    begin_block = tutorial_source[begin_start:restore_start]
    restore_block = tutorial_source[restore_start:restore_end]

    assert "saveTutorialModelPayload" not in begin_block
    assert "saveTutorialModelPayload" not in restore_block
    assert "this.reloadTutorialModel(currentName, tutorialModelPayload, { temporary: true })" in begin_block
    assert "live2d: TUTORIAL_YUI_LIVE2D_MODEL_NAME" in begin_block
    assert "TUTORIAL_YUI_LIVE2D_MODEL_PATH = '/static/yui-origin/yui-origin.model3.json'" in tutorial_source
    assert "suppressInitialIdle: true" in tutorial_source
    assert "suppressInitialIdle: skipIdleRestore" in interpage_source
    assert "temporaryConfig" in interpage_source
    assert "skipIdleRestore" in interpage_source
    assert "suppressToast" in interpage_source
    assert "async function _waitForLive2DManagerIdle" in interpage_source
    assert "await _waitForLive2DManagerIdle(30000);" in interpage_source


def test_theme_system_preference_does_not_become_saved_user_choice():
    theme_source = Path("static/theme-manager.js").read_text(encoding="utf-8")
    plugin_dark_mode_source = Path("frontend/plugin-manager/src/composables/useDarkMode.ts").read_text(encoding="utf-8")

    assert "applyTheme(isDark, { persist: shouldPersist });" in theme_source
    assert "applyThemeAnimated(event.matches, { persist: false });" in theme_source
    assert "applyDarkMode(saved !== null ? saved : getSystemPrefersDark(), { persist: saved !== null })" in plugin_dark_mode_source
    assert "applyDarkMode(event.matches, { persist: false })" in plugin_dark_mode_source


def test_home_yui_guide_uses_platform_capability_matrix_for_cross_window_skip():
    director_source = Path("static/yui-guide-director.js").read_text(encoding="utf-8")
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
    steps_source = Path("static/yui-guide-steps.js").read_text(encoding="utf-8")
    director_source = Path("static/yui-guide-director.js").read_text(encoding="utf-8")

    assert "timeline: []" in steps_source
    assert "{ at: 0.16, action: 'highlightVoiceControl' }" in steps_source
    assert "{ at: 0.54, action: 'openSettingsPanel' }" in steps_source
    assert "{ voiceKey: 'takeover_settings_peek_detail', at: 7450 / 13923, action: 'showSecondLine' }" in steps_source
    assert "getGuideTimelineCueConfig(voiceKey, cueName)" in director_source
    assert "const timeline = Array.isArray(performance.timeline) ? performance.timeline : []" in director_source
    assert "cue.action !== normalizedCueName" in director_source
    assert "GUIDE_NARRATION_TIMELINES_BY_KEY" in director_source
    assert "estimateSpeechDurationMs(fallbackText || '')" in director_source


def test_home_yui_guide_records_local_experience_metrics_without_upload_path():
    director_source = Path("static/yui-guide-director.js").read_text(encoding="utf-8")

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


def test_agent_server_user_turn_fingerprint_includes_attachments():
    source = Path("agent_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn_src = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_build_user_turn_fingerprint":
            fn_src = ast.get_source_segment(source, node)
            break
    assert fn_src is not None

    ns = {}
    exec("import hashlib\nfrom typing import Any, Optional\n" + fn_src, ns)
    fingerprint = ns["_build_user_turn_fingerprint"]

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
    source = Path("agent_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn_src = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_resolve_openclaw_sender_id":
            fn_src = ast.get_source_segment(source, node)
            break
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
    source = Path("agent_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn_src = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_collect_active_openclaw_task_ids":
            fn_src = ast.get_source_segment(source, node)
            break
    assert fn_src is not None

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
    exec(fn_src, ns)
    collector = ns["_collect_active_openclaw_task_ids"]

    result = collector(sender_id="user_a", lanlan_name="LanLan", exclude_task_id="magic-stop")

    assert result == ["openclaw-running"]


def test_cross_server_analyze_request_no_http_fallback_endpoint():
    source = Path("main_logic/cross_server.py").read_text(encoding="utf-8")
    assert "/api/agent/internal/analyze_request" not in source


def test_is_agent_api_ready_allows_free_profile():
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {"IS_FREE_VERSION": True}
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
        ({"model": "m", "base_url": "https://u", "api_key": "free-access"}, "Agent API Key 未配置或不可用"),
    ],
)
def test_is_agent_api_ready_reports_missing_fields(agent_api, expected_reason):
    manager = object.__new__(ConfigManager)
    manager.get_core_config = lambda: {"IS_FREE_VERSION": False}
    manager.get_model_api_config = lambda _model_type: agent_api

    ready, reasons = manager.is_agent_api_ready()
    assert ready is False
    assert expected_reason in reasons


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
    source = Path("agent_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_emit_main_event":
            func = node
            break
    assert func is not None, "_emit_main_event not found"
    assert _contains_call(func, "emit_to_main"), \
        "_emit_main_event does not call emit_to_main"


def test_emit_main_event_no_http_fallback():
    """_emit_main_event must NOT contain any httpx or HTTP fallback code."""
    source = Path("agent_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_emit_main_event":
            func = node
            break
    assert func is not None
    func_source = ast.get_source_segment(source, func) or ""
    assert "httpx" not in func_source, "_emit_main_event still contains httpx HTTP fallback"
    assert "http://" not in func_source, "_emit_main_event still contains HTTP URL"


# ---------------------------------------------------------------------------
#  _on_session_event (agent_server.py)
# ---------------------------------------------------------------------------

def test_on_session_event_dispatches_ack_and_analyze():
    """_on_session_event creates tasks for ack emission and background analysis."""
    source = Path("agent_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_on_session_event":
            func = node
            break
    assert func is not None, "_on_session_event not found"
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
