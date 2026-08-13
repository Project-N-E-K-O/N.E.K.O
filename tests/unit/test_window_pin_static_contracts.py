import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_text(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def flatten_keys(value: dict, prefix: str = "") -> set[str]:
    keys = set()
    for key, nested_value in value.items():
        dotted_key = f"{prefix}.{key}" if prefix else key
        if isinstance(nested_value, dict):
            keys.update(flatten_keys(nested_value, dotted_key))
        else:
            keys.add(dotted_key)
    return keys


def assert_pin_precedes_minimize(
    source: str,
    path: str,
    minimize_token: str = 'data-neko-window-control="minimize"',
) -> None:
    pin_index = source.index('data-neko-window-control="pin"')
    minimize_index = source.index(minimize_token)
    assert pin_index < minimize_index, path


def test_shared_window_controls_bind_only_explicit_hidden_pin_buttons():
    script = read_text("static/js/window_controls.js")
    stylesheet = read_text("static/css/window_controls.css")
    asset_version_source = read_text("main_routers/pages_router.py")

    assert 'data-neko-window-control="pin"' in script
    assert 'querySelectorAll(`${CONTROL_SELECTOR}[data-neko-window-control="pin"]`)' in script
    assert "pinButtons.forEach" in script
    assert "typeof api.getPinState !== 'function'" in script
    assert "typeof api.togglePin !== 'function'" in script
    assert "PIN_STATE_RETRY_DELAYS_MS = [50, 150, 350, 750]" in script
    assert "function schedulePinStateRefreshRetry(generation, retryIndex)" in script
    assert "if (!normalizedState.available)" in script
    assert "window.addEventListener('focus', () => refreshPinState())" in script
    assert re.search(
        r"const state = await api\.togglePin\(\);\s*"
        r"\+\+pinStateRefreshGeneration;\s*"
        r"updatePinState\(state",
        script,
    ), "a successful toggle must invalidate older pin-state refreshes"
    assert "pinButton.hidden = !available" in script
    assert "pinButton.classList.toggle('is-pinned', pinned)" in script
    assert "common.pinWindow" in script
    assert "common.unpinWindow" in script
    assert "data-neko-pin-label" in script
    assert "data-neko-unpin-label" in script
    assert ".neko-window-pin-icon" in stylesheet
    assert ".neko-window-control-btn.is-pinned" in stylesheet
    assert '[data-neko-window-control="pin"].is-pinned .neko-window-pin-icon' in stylesheet
    assert "@keyframes neko-window-pin-lock" in stylesheet
    assert "prefers-reduced-motion: reduce" in stylesheet
    assert '_PROJECT_ROOT / "static/css/window_controls.css"' in asset_version_source
    assert '_PROJECT_ROOT / "static/js/window_controls.js"' in asset_version_source
    assert not re.search(
        r'\.neko-window-control-btn\[data-neko-window-control="pin"\]\s*\{[^}]*\bcolor\s*:',
        stylesheet,
        re.DOTALL,
    ), "shared pin styles must not override page-specific icon colors"


def test_only_requested_top_level_templates_define_pin_controls():
    pin_templates = (
        "templates/voice_clone.html",
        "templates/api_key_settings.html",
        "templates/memory_browser.html",
        "templates/cookies_login.html",
        "templates/cookies_guide.html",
        "templates/cloudsave_manager.html",
    )
    for path in pin_templates:
        source = read_text(path)
        assert 'data-neko-window-control="pin"' in source, path
        assert 'data-neko-window-control="pin" hidden' in source, path
        assert 'class="neko-window-pin-icon" aria-hidden="true"' in source, path
        assert_pin_precedes_minimize(source, path)

    character_manager = read_text("templates/character_card_manager.html")
    assert 'data-neko-window-control="pin" hidden' in character_manager
    assert 'class="neko-window-pin-icon" aria-hidden="true"' in character_manager
    assert_pin_precedes_minimize(
        character_manager,
        "templates/character_card_manager.html",
        # Anchor on the id, not on ``class="minimize-btn"``: #2750 unified the
        # window-control styling by prepending ``neko-window-control-btn`` to the
        # class list, so a token that assumed minimize-btn was the FIRST class
        # stopped matching and ``str.index`` raised instead of asserting. The id
        # survives restyling (same reason the openclaw_guide case below uses one).
        'id="minimizeBtn"',
    )

    openclaw_guide = read_text("templates/openclaw_guide.html")
    assert 'data-neko-window-control="pin" hidden' in openclaw_guide
    assert 'class="neko-window-pin-icon" aria-hidden="true"' in openclaw_guide
    assert "/static/css/window_controls.css" in openclaw_guide
    assert "/static/js/window_controls.js" in openclaw_guide
    assert_pin_precedes_minimize(
        openclaw_guide,
        "templates/openclaw_guide.html",
        'id="minimizeGuideBtn"',
    )

    for path in (
        "templates/card_maker.html",
        "templates/jukebox_manager.html",
        "templates/live2d_emotion_manager.html",
        "templates/vrm_emotion_manager.html",
        "templates/mmd_emotion_manager.html",
    ):
        assert 'data-neko-window-control="pin"' not in read_text(path), path


def test_pin_templates_version_shared_window_control_and_locale_assets():
    for path in (
        "templates/voice_clone.html",
        "templates/api_key_settings.html",
        "templates/character_card_manager.html",
        "templates/memory_browser.html",
        "templates/cookies_login.html",
        "templates/cookies_guide.html",
        "templates/cloudsave_manager.html",
        "templates/jukebox.html",
        "templates/openclaw_guide.html",
    ):
        source = read_text(path)
        assert "/static/i18n-i18next.js?v={{ static_asset_version" in source, path
        assert "/static/css/window_controls.css?v={{ static_asset_version" in source, path
        assert "/static/js/window_controls.js?v={{ static_asset_version" in source, path

    routes = read_text("main_routers/pages_router.py")
    jukebox_route = re.search(
        r"async def get_jukebox_page\(request: Request\):(?P<body>[\s\S]*?)"
        r"(?=\n@router\.get)",
        routes,
    )
    assert jukebox_route
    assert "**_static_assets_ctx()" in jukebox_route.group("body")

    agent_routes = read_text("main_routers/agent_router.py")
    openclaw_route = re.search(
        r"async def openclaw_guide_page\(request: Request\):(?P<body>[\s\S]*?)"
        r"(?=\n@router\.get)",
        agent_routes,
    )
    assert openclaw_route
    assert "**_static_assets_ctx()" in openclaw_route.group("body")

    auth_routes = read_text("main_routers/cookies_login_router.py")
    credential_guide_route = re.search(
        r"async def render_auth_guide\(request: Request\):(?P<body>[\s\S]*?)"
        r"(?=\n# ============)",
        auth_routes,
    )
    assert credential_guide_route
    assert 'templates.TemplateResponse("cookies_guide.html"' in credential_guide_route.group("body")


def test_credentials_page_opens_the_universal_guide_in_a_named_window():
    template = read_text("templates/cookies_login.html")
    script = read_text("static/js/cookies_login.js")

    assert 'href="/api/auth/guide"' in template
    assert 'target="neko_credential_guide"' in template
    assert '<details class="tutorial-banner tutorial-disclosure">' not in template
    assert "function openCredentialGuide(event)" in script
    assert "window.open(guideUrl.toString(), windowName, features)" in script


def test_credentials_universal_guide_hides_for_cookie_string_platforms():
    """The universal guide teaches per-field copying, wrong for cookie-header platforms."""
    script = read_text("static/js/cookies_login.js")

    assert "cookieStringMode: true" in script
    assert "const hideTutorial = Boolean(config.authMode || config.cookieStringMode);" in script
    assert "tutorialBanner.style.display = hideTutorial ? 'none' : ''" in script


def test_credentials_shared_submit_button_is_scoped_to_the_latest_request():
    """#submit-btn is shared by every platform; a stale finally must not unlock it."""
    script = read_text("static/js/cookies_login.js")

    assert "let submitButtonGeneration = 0;" in script
    assert "const generation = ++submitButtonGeneration;" in script
    # 恢复必须过代数闸，不能再有裸的 submitBtn.disabled = false
    release = re.search(
        r"function releaseSubmitButtonLock\(generation\) \{(?P<body>[\s\S]*?)\n\}",
        script,
    )
    assert release
    assert "if (generation !== submitButtonGeneration) return;" in release.group("body")

    # 保存凭证和 Twitch 授权两条路径都走同一把锁，函数体里不许再直接动按钮。
    assert script.count("const submitLock = beginSubmitButtonLock();") == 2
    assert script.count("releaseSubmitButtonLock(submitLock);") == 2
    for name in ("startTwitchDeviceCode", "submitCurrentCookie"):
        body = re.search(
            rf"async function {name}\((?:[^)]*)\) \{{(?P<body>[\s\S]*?)\n\}}", script
        )
        assert body, name
        assert "beginSubmitButtonLock()" in body.group("body"), name
        assert "releaseSubmitButtonLock(" in body.group("body"), name
        assert not re.search(r"\.\s*disabled\s*=", body.group("body")), name

    # 换平台放行共用按钮，但语言切换的同平台重渲染不能放行。
    assert re.search(
        r"if \(!isReRender && previousPlatform !== platformKey\) \{\s*\n"
        r"\s*submitButtonGeneration\+\+;",
        script,
    )


def test_credentials_mascot_bubble_leaves_the_accessibility_tree_when_hidden():
    """The bubble carries aria-live, so opacity alone still gets announced."""
    template = read_text("templates/cookies_login.html")

    assert 'class="mascot-bubble" aria-live="polite"' in template
    base = re.search(r"\n        \.mascot-bubble \{(?P<body>[\s\S]*?)\n        \}", template)
    visible = re.search(
        r"\n        \.mascot-bubble\.visible \{(?P<body>[\s\S]*?)\n        \}", template
    )
    assert base and visible
    assert "visibility: hidden;" in base.group("body")
    assert "visibility: visible;" in visible.group("body")
    assert re.search(
        r"\.character-banner\.credential-privacy-active \.mascot-bubble \{"
        r"[\s\S]*?visibility: hidden !important;",
        template,
    )


def test_credentials_status_icon_lookup_ignores_inherited_properties():
    """A platform key like `constructor` would otherwise render function source."""
    template = read_text("templates/cookies_login.html")

    assert "Object.prototype.hasOwnProperty.call(platformIcons, key)" in template
    assert not re.search(r"var svg = platformIcons\[key\];", template)


def test_credentials_tabs_are_wired_to_the_single_tab_panel():
    template = read_text("templates/cookies_login.html")

    tab_buttons = re.findall(r'<button class="tab-btn[^>]*>', template)
    # 凭证源会持续增删，锁死具体个数只会让每个新增源都红一次；真正要守的是每个按钮的
    # ARIA 接线。和 switchTab 调用数交叉比对，正则一旦失配就会立刻不等，避免测出 0 个假绿。
    switch_calls = template.count("switchTab('")
    assert switch_calls > 0
    assert len(tab_buttons) == switch_calls
    for button in tab_buttons:
        # 逐个按钮各带一次调用，才能保证上面那个总数相等不是靠"这里漏一次、别处多一次"凑出来的。
        assert button.count("switchTab('") == 1, button
        assert 'role="tab"' in button, button
        assert 'aria-controls="main-panel"' in button, button
    assert re.search(
        r'<div class="tab-content" id="main-panel"[^>]*role="tabpanel"', template
    )


def test_credentials_deferred_mascot_reaction_has_a_bounded_deadline():
    """A deferred reaction needs a ceiling, or a stale one fires minutes later."""
    script = read_text("static/js/cookies_login.js")

    assert "const MASCOT_DEFERRED_MAX_MS = 4000;" in script
    assert "deferMascotReaction(type, duration, Date.now() + MASCOT_DEFERRED_MAX_MS)" in script

    defer_body = re.search(
        r"const deferMascotReaction = \(type, duration, deadline\) => \{"
        r"(?P<body>[\s\S]*?)\n    \};",
        script,
    )
    assert defer_body
    # 重排必须沿用同一个 deadline，重新取一次 Date.now() 等于上限失效。
    assert "if (Date.now() >= deadline) return;" in defer_body.group("body")
    assert "deferMascotReaction(type, duration, deadline);" in defer_body.group("body")
    assert "Date.now() + MASCOT_DEFERRED_MAX_MS" not in defer_body.group("body")


def test_credentials_guide_screenshot_keeps_no_readable_cookie_value():
    """The Cookie Value pane in the step-5 screenshot must carry no real credential."""
    import numpy as np
    from PIL import Image, ImageFilter

    asset = PROJECT_ROOT / "static" / "images" / "cookies" / "guide" / "step-5-value.png"
    with Image.open(asset) as image:
        pane = image.convert("RGB").crop((334, 843, image.width, image.height))

    rgb = np.asarray(pane).astype(int)
    # 面板上压着红色标注箭头，它本来就是高对比度的，先连边缘一起排除。
    red = (
        (rgb[:, :, 0] > 140)
        & (rgb[:, :, 1] < 110)
        & (rgb[:, :, 2] < 110)
        & (rgb[:, :, 0] - rgb[:, :, 1] > 60)
    )
    keep = ~np.asarray(
        Image.fromarray((red * 255).astype("uint8")).filter(ImageFilter.MaxFilter(9))
    ).astype(bool)

    # 字形靠相邻像素的突变成立；抹干净之后剩下的只有平滑渐变。
    # 逐通道比而不是转灰度：白字压在浅蓝选中底色上，亮度差只有 38，
    # 转灰度会把这条判据糊过去，逐通道能看到 70。
    horizontal = np.abs(np.diff(rgb, axis=1)).max(axis=2)[keep[:, :-1] & keep[:, 1:]]
    vertical = np.abs(np.diff(rgb, axis=0)).max(axis=2)[keep[:-1, :] & keep[1:, :]]
    contrast = int(max(horizontal.max(), vertical.max()))
    assert contrast < 20, f"Cookie Value 面板仍有高对比度笔画（{contrast}）"


def test_credentials_tutorial_link_has_distinct_interaction_states():
    template = read_text("templates/cookies_login.html")

    assert ".tutorial-link:hover {" in template
    assert ".tutorial-link:focus-visible {" in template
    assert ".tutorial-link:active {" in template
    assert "0 0 0 3px var(--accent-glow)" in template
    assert ".tutorial-link:is(:hover, :focus-visible) .tutorial-link-icon" in template
    assert ".tutorial-link:is(:hover, :focus-visible) .tutorial-link-arrow" in template


def test_credentials_qr_entry_does_not_reflow_twice_on_platform_switch():
    template = read_text("templates/cookies_login.html")
    script = read_text("static/js/cookies_login.js")

    qr_styles = re.findall(r"#QRLogin \{(?P<body>[\s\S]*?)\n\s*\}", template)
    assert qr_styles
    assert all("transition: all" not in style for style in qr_styles)
    assert any("transition: max-height" in style for style in qr_styles)
    assert "let qrSupportedPlatformsRequest = null;" in script
    assert "if (!qrSupportedPlatformsRequest)" in script
    assert "supportedPlatforms = await getQrSupportedPlatforms();" in script
    assert "qrLoginBox.style.display = 'none';" in script
    assert "qrLoginBox.style.removeProperty('display');" in script


def test_credentials_mobile_layout_uses_one_vertical_scroll_container():
    template = read_text("templates/cookies_login.html")

    assert "height: 760px" not in template
    assert "(max-width: 900px) and (max-height: 540px)" in template
    assert re.search(
        r"@media \(max-width: 640px\),[\s\S]*?\.tab-content \{"
        r"[\s\S]*?height: auto;[\s\S]*?max-height: none;[\s\S]*?overflow: visible;",
        template,
    )


def test_credentials_async_dom_updates_are_scoped_to_the_latest_state():
    script = read_text("static/js/cookies_login.js")

    assert "const submittedPlatform = currentPlatform;" in script
    assert "platform: submittedPlatform" in script
    assert "if (currentPlatform === submittedPlatform)" in script
    assert "const refreshGeneration = ++statusRefreshGeneration;" in script
    assert "if (refreshGeneration !== statusRefreshGeneration) return;" in script
    assert "const hasLoadFailure = entries.some(entry => !entry.loaded);" in script
    assert "signal: abortController.signal" in script
    assert "requestGeneration !== qrRequestGeneration" in script
    assert "entryGeneration !== qrEntryGeneration" in script
    assert "const preserveQrState = isReRender" in script
    assert "currentPlatform !== platformAtStart || twitchClientId() !== clientId" in script
    assert "if (dialog.open) return Promise.resolve(false);" in script
    assert "if (deletingPlatforms.has(platformKey)) return;" in script


def test_credentials_page_and_guide_share_window_control_buttons():
    page = read_text("templates/cookies_login.html")
    guide = read_text("templates/cookies_guide.html")
    controls_pattern = re.compile(
        r'<div class="header-right neko-window-controls">(?P<body>[\s\S]*?)</div>'
    )
    page_controls = controls_pattern.search(page)
    guide_controls = controls_pattern.search(guide)

    assert page_controls
    assert guide_controls
    assert page_controls.group("body") == guide_controls.group("body")
    controls = page_controls.group("body")
    assert controls.index('data-neko-window-control="minimize"') < controls.index(
        'data-neko-window-control="maximize"'
    ) < controls.index('data-neko-window-control="close"')


def test_credentials_guide_uses_the_five_step_screenshot_sequence():
    guide = read_text("templates/cookies_guide.html")
    expected_assets = {
        "step-1-site.png": (417, 181),
        "step-1-login.png": (386, 425),
        "step-2-devtools.png": (1218, 802),
        "step-3-application.png": (784, 800),
        "step-4-cookies.png": (1052, 899),
        "step-5-value.png": (1159, 998),
    }

    assert guide.count('class="step" data-step=') == 5
    assert 'data-i18n-aria="cookiesLogin.guide.stepsLabel"' in guide
    assert "contain: layout paint style;" in guide
    assert "backdrop-filter: none;" in guide
    for asset, (width, height) in expected_assets.items():
        assert f"/static/images/cookies/guide/{asset}" in guide
        assert f'width="{width}" height="{height}"' in guide
        assert (PROJECT_ROOT / "static" / "images" / "cookies" / "guide" / asset).is_file()

    for locale_path in (PROJECT_ROOT / "static" / "locales").glob("*.json"):
        guide_copy = json.loads(locale_path.read_text(encoding="utf-8"))["cookiesLogin"]["guide"]
        for key in (
            "stepsLabel",
            "step1Title",
            "step1LoginAlt",
            "step2Title",
            "step2",
            "step3Title",
            "step4Title",
            "step5Title",
            "step5",
            "step5Tip",
        ):
            assert guide_copy[key], f"{locale_path.name}: {key}"


def test_credentials_i18n_keys_and_locale_line_counts_stay_aligned():
    locale_paths = sorted((PROJECT_ROOT / "static" / "locales").glob("*.json"))
    locale_data = {
        path.name: json.loads(path.read_text(encoding="utf-8")) for path in locale_paths
    }

    cookie_key_sets = {
        name: flatten_keys(data["cookiesLogin"]) for name, data in locale_data.items()
    }
    reference_keys = cookie_key_sets["zh-CN.json"]
    for name, keys in cookie_key_sets.items():
        assert keys == reference_keys, name

    line_counts = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in locale_paths
    }
    assert len(set(line_counts.values())) == 1, line_counts
    assert locale_data["es.json"]["cookiesLogin"]["qrLogin"]["retry"] == "Reintentar"


def test_jukebox_has_an_explicit_pin_before_minimize_without_touching_manager():
    shell = read_text("static/jukebox/jukebox/shell.js")
    template = read_text("templates/jukebox.html")
    manager = read_text("static/jukebox/jukebox/manager.js")

    assert 'data-neko-window-control="pin" hidden' in shell
    assert 'class="jukebox-pin neko-window-control-btn"' in shell
    assert "color: rgba(45, 78, 104, 0.8);" in shell
    assert_pin_precedes_minimize(
        shell,
        "static/jukebox/jukebox/shell.js",
        'class="jukebox-minimize"',
    )
    assert "/static/css/window_controls.css" in template
    assert "/static/js/window_controls.js" in template
    assert "nekoWindowControls.init" in shell
    assert 'data-neko-window-control="pin"' not in manager


def test_chat_export_preview_uses_named_windows_and_pin_contract():
    script = read_text("static/app/app-chat-export.js")
    window_chrome = re.search(
        r"function buildWindowChromeHtml\(title\) \{(?P<body>.*?)\n    \}",
        script,
        re.DOTALL,
    )

    assert window_chrome
    assert "exportPreviewAssetVersion = getCurrentExportAssetVersion()" in script
    assert "function getVersionedExportAssetUrl(path)" in script
    assert "getVersionedExportAssetUrl('/static/css/window_controls.css')" in script
    assert "getVersionedExportAssetUrl('/static/js/window_controls.js')" in script
    assert "script.src = getVersionedExportAssetUrl('/static/js/window_controls.js')" in script
    assert 'data-neko-window-control="pin"' in script
    assert "windowControls.appendChild(pinButton)" in script
    assert "windowControls.appendChild(minimizeButton)" in script
    assert script.index("windowControls.appendChild(pinButton)") < script.index(
        "windowControls.appendChild(minimizeButton)"
    )
    assert "neko_chat_export_preview_main_" in script
    assert "neko_chat_export_preview_child_" in script
    assert "window.open('', getExportPreviewWindowName('main')" in script
    assert "window.open('', getExportPreviewWindowName('child')" in script
    assert 'data-neko-window-control="pin" hidden' in script
    assert "function syncPinButtonLocale(button)" in script
    assert "button.setAttribute('data-neko-pin-label', pinLabel)" in script
    assert "button.setAttribute('data-neko-unpin-label', unpinLabel)" in script
    assert script.count("syncPinButtonLocale(pinButton)") >= 2
    assert "syncPinButtonLocale(modal.pinButton)" in script
    assert "translateLabel('common.pinWindow', 'Pin Window')" in window_chrome.group("body")
    assert "translateLabel('common.unpinWindow', 'Unpin Window')" in window_chrome.group(
        "body"
    )
    assert 'data-neko-pin-label="' in window_chrome.group("body")
    assert 'data-neko-unpin-label="' in window_chrome.group("body")


def test_plugin_manager_pin_control_and_bridge_contract():
    component = read_text("frontend/plugin-manager/src/components/layout/AppLayout.vue")
    types = read_text("frontend/plugin-manager/env.d.ts")

    pin_button = component.index('class="titlebar-control titlebar-control--pin"')
    minimize_button = component.index('@click="minimizeWindow"')
    assert pin_button < minimize_button
    assert 'v-if="pinAvailable"' in component
    assert "api.getPinState" in component
    assert "api.togglePin" in component
    assert ':disabled="pinPending"' in component
    assert "if (pinPending.value) return" in component
    assert "PIN_STATE_RETRY_DELAYS_MS = [50, 150, 350, 750]" in component
    assert "function schedulePinStateRetry(generation: number, retryIndex: number)" in component
    assert "const generation = ++pinRequestGeneration" in component
    assert "generation === pinRequestGeneration" in component
    assert "clearPinStateRetry()" in component
    assert "pinDisposed = true" in component
    assert "pinAvailable.value = !!state.available" in component
    assert "isPinned.value = !!state.pinned" in component
    assert "@keyframes neko-plugin-pin-lock" in component
    assert "prefers-reduced-motion: reduce" in component
    assert "getPinState?:" in types
    assert "togglePin?:" in types
    assert "available?: boolean" in types
    assert "pinned?: boolean" in types


def test_pin_labels_exist_in_all_main_and_plugin_locales():
    i18n_bootstrap = read_text("static/i18n-i18next.js")
    # 只要求存在一个非空的 LOCALE_VERSION（locale 文件靠它做 cache-bust），
    # 不再钉死具体取值：钉死等于让每一次无关的版本串变更都打红这条用例。
    # #2465「Refactor memory browser UI」把它从 2026-07-22-window-pin-controls-i18n
    # 改成 2026-07-24-memory-browser-ui-refactor，这条就一直红着——当时
    # tests/unit 还没进 CI，没人看见。
    locale_version = re.search(
        r"const\s+LOCALE_VERSION\s*=\s*'([^']+)'", i18n_bootstrap
    )
    assert locale_version and locale_version.group(1).strip(), (
        "i18n-i18next.js 必须带一个非空的 LOCALE_VERSION 常量做 locale cache-bust"
    )

    locale_names = (
        "en",
        "es",
        "ja",
        "ko",
        "pt",
        "ru",
        "zh-CN",
        "zh-TW",
    )
    for locale_name in locale_names:
        payload = json.loads(read_text(f"static/locales/{locale_name}.json"))
        assert payload["common"]["pinWindow"], locale_name
        assert payload["common"]["unpinWindow"], locale_name

    plugin_locale_names = (
        "en-US",
        "es",
        "ja",
        "ko",
        "pt",
        "ru",
        "zh-CN",
        "zh-TW",
    )
    for locale_name in plugin_locale_names:
        source = read_text(f"frontend/plugin-manager/src/i18n/locales/{locale_name}.ts")
        assert re.search(r"\bpinWindow\s*:\s*['\"].+['\"]", source), locale_name
        assert re.search(r"\bunpinWindow\s*:\s*['\"].+['\"]", source), locale_name
