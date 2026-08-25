import re
import shutil
import struct
from pathlib import Path

import pytest
from tests.node_harness import run_node_stdin
from tests.static_app_parts import read_js_parts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
APP_SETTINGS_PATH = PROJECT_ROOT / "static" / "app" / "app-settings.js"
AVATAR_UI_POPUP_PATH = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-popup.js"
FORGE_DROP_OVERLAY_PATH = PROJECT_ROOT / "static" / "forge-drop-overlay.js"
APP_SOCIAL_UI_PATH = PROJECT_ROOT / "static" / "app-social-ui.js"
FORGE_AVATAR_REACTION_PATH = PROJECT_ROOT / "static" / "forge-avatar-reaction.js"
FORGE_DROP_TOKENS_PATH = PROJECT_ROOT / "static" / "forge-drop-tokens.js"
FORGE_SOUND_DIR = PROJECT_ROOT / "static" / "sounds" / "forge"


def _extract_js_function(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    quote = None
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
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated JavaScript function: {signature}")


@pytest.mark.unit
def test_social_open_request_is_deduped_before_fetching_config():
    source = read_js_parts(APP_UI_PATH)

    assert "const SOCIAL_OPEN_DEDUPE_MS = 1200;" in source
    assert "window.__nekoSocialOpenState" in source
    assert "function shouldIgnoreSocialOpenRequest()" in source
    assert "function releaseSocialOpenRequest()" in source

    listener_start = source.index("window.addEventListener('live2d-social-click', async () => {")
    listener_end = source.index("// 睡觉按钮（请她离开）", listener_start)
    listener = source[listener_start:listener_end]

    assert listener.index("if (shouldIgnoreSocialOpenRequest()) {") < listener.index(
        "fetch('/api/system/social/config')"
    )
    assert "let socialOpenRequestReleased = false;" in listener
    assert listener.count("releaseSocialOpenRequest();") == 2
    assert "if (!socialOpenRequestReleased)" in listener
    # Community opens in-app (Electron framed child / browser tab); OAuth may still use openExternal.
    helper_start = listener.index("const openElectronSocialWindow = (targetUrl) => {")
    helper_end = listener.index("const fetchNativeSyncTicket = async () => {", helper_start)
    electron_helper = listener[helper_start:helper_end]
    assert re.search(
        r"window\.open\(\s*resolvedTargetUrl\.toString\(\),\s*"
        r"'neko-social',\s*"
        r"'popup=yes,width=1200,height=800,resizable=yes'\s*\)",
        electron_helper,
    )
    assert "openElectronSocialWindow(url)" in listener
    assert listener.index("releaseSocialOpenRequest();") > listener.index("openElectronSocialWindow(url)")
    assert "fetch('/api/card-drop/sync-ticket', {" in listener
    assert "hashParams.set('native_sync', syncTicket)" in listener
    assert "fetch('/api/card-drop/native-delegate', {" in listener
    assert "hashParams.set('native_delegate', nativeDelegate)" in listener
    assert "const initialSyncTicketPromise = fetchNativeSyncTicket();" in listener
    assert "const initialClientIdPromise = fetchSocialClientId();" in listener
    assert "const initialNativeHandoffPromise = fetchNativeDelegate();" in listener
    assert "const [initialSyncTicket, clientId] = await Promise.all([" in listener
    assert "applyNativeSyncTicket(targetUrl, initialSyncTicket);" in listener
    assert listener.count("setTimeout(() => controller.abort(), 4000)") == 2
    assert listener.count("signal: controller.signal") == 2
    assert listener.count("clearTimeout(timeoutId)") == 2
    assert "native session sync ticket fetch failed: HTTP" in listener
    assert "native delegate fetch failed (non-fatal):" in listener
    assert "targetUrl.searchParams.set('cid', clientId)" in listener
    assert "const attachResolvedTheme = (targetUrl) => {" in listener
    assert "function isResolvedDarkTheme()" in source
    assert "window.nekoTheme.isDark()" in source
    assert "targetUrl.searchParams.set('neko_theme', isResolvedDarkTheme() ? 'dark' : 'light')" in listener
    assert "targetUrl.searchParams.set('neko_source_origin', window.location.origin)" in listener
    assert "popupRoot.style.colorScheme = popupDark ? 'dark' : 'light'" in listener
    assert "popupRoot.style.backgroundColor = popupDark ? '#070c13' : '#edf8ff'" in listener
    assert "function registerSocialThemeTarget(targetWindow, targetUrl)" in source
    assert "window.addEventListener('neko-theme-changed', (event) => {" in source
    assert "typeof requestedTheme === 'boolean' ? requestedTheme : isResolvedDarkTheme()" in source
    assert "target.targetWindow.postMessage({" in source
    assert "source: 'neko-desktop'" in source
    assert "type: 'theme-change'" in source
    assert "state.targets.find((candidate)" in source
    assert "event.source === candidate.targetWindow && event.origin === candidate.targetOrigin" in source
    assert "data.source !== 'neko-community' || data.type !== 'theme-ready'" in source
    assert "postSocialTheme(target, isResolvedDarkTheme())" in source
    assert "state.targets = [target]" in source
    assert "state.targets = state.targets.filter" in source
    assert "function queueSocialThemeSync(target)" in source
    assert "[0, 100, 300, 1000].forEach" in source
    assert "attachResolvedTheme(parsedTarget)" in listener
    assert "currentPopup.location.replace(navigationTarget)" in listener
    assert "themeTarget = registerSocialThemeTarget(currentPopup, parsedTarget)" in listener
    assert "if (navigated) queueSocialThemeSync(themeTarget)" in listener
    assert "if (target.targetWindow.closed)" not in source
    assert "currentPopup.opener = null" in listener
    assert "currentPopup.opener = window" not in listener
    assert "throw new Error('failed to navigate browser community window')" not in listener
    assert "const resolvedTargetUrl = attachResolvedTheme(" in listener
    assert "resolvedTargetUrl.toString()" in listener
    assert "registerSocialThemeTarget(socialWin, resolvedTargetUrl)" in listener
    assert "social_base_url" in listener
    assert "/feed" in listener
    # Feed first; Desktop OAuth only after the native handoff proves the desktop is logged out.
    assert "fetch('/api/card-drop/auth-status', { cache: 'no-store' })" in listener
    assert "fetch('/api/card-drop/oauth/start'" in listener
    assert "请在浏览器完成统一账号登录" in listener
    assert listener.index("openElectronSocialWindow(url)") < listener.index(
        "const initialNativeHandoff = await initialNativeHandoffPromise;"
    )
    assert listener.index("const initialNativeHandoff = await initialNativeHandoffPromise;") < listener.index(
        "fetch('/api/card-drop/auth-status'"
    )
    assert listener.index("fetch('/api/card-drop/auth-status'") < listener.index(
        "fetch('/api/card-drop/oauth/start'"
    )
    assert "openExternal(authUrl)" in listener
    protocol_guard = "targetUrl.protocol !== 'http:' && targetUrl.protocol !== 'https:'"
    assert protocol_guard in listener
    assert listener.index(protocol_guard) < listener.index(
        "const initialSyncTicketPromise = fetchNativeSyncTicket();"
    )
    assert listener.index(protocol_guard) < listener.index("attachResolvedTheme(targetUrl)")
    assert listener.index("attachResolvedTheme(targetUrl)") < listener.index(
        "applyNativeSyncTicket(targetUrl, initialSyncTicket);"
    )
    # A slow delegate must not delay the initial Electron Community navigation.
    assert listener.index("const initialNativeHandoffPromise = fetchNativeDelegate();") < listener.index(
        "openElectronSocialWindow(url)"
    )
    assert listener.index("openElectronSocialWindow(url)") < listener.index(
        "const initialNativeHandoff = await initialNativeHandoffPromise;"
    )
    helper_start = listener.index(
        "const completeInitialCommunityHandoff = async (targetUrl, initialNativeDelegate = '') => {"
    )
    helper_end = listener.index("\n            try {", helper_start)
    helper = listener[helper_start:helper_end]
    assert "navigateBrowserPopup(targetUrl, { keepReference: true })" not in helper
    assert "const retryHandoff = await fetchNativeDelegate();" in helper
    assert helper.index("let nativeDelegate = initialNativeDelegate;") < helper.index(
        "openElectronSocialWindow(delegateTargetUrl.toString())"
    )
    assert re.search(
        r"const delegateTargetUrl = await attachNativeSyncTicket\(\s*"
        r"new URL\(targetUrl, window\.location\.href\)\s*\);",
        listener,
    )
    assert "attachNativeDelegate(delegateTargetUrl, nativeDelegate);" in listener
    assert "const completeInitialCommunityHandoff = async (targetUrl, initialNativeDelegate = '') => {" in listener
    assert listener.count(
        "await completeInitialCommunityHandoff("
    ) == 2
    main_flow = listener[helper_end:]
    assert "let communityLoggedIn = initialNativeHandoff.loginState === 'logged-in';" in main_flow
    assert "if (initialNativeHandoff.loginState === 'unknown')" in main_flow
    assert main_flow.index("fetch('/api/card-drop/auth-status'") < main_flow.index(
        "await completeInitialCommunityHandoff("
    )
    assert re.search(
        r"else \{\s*await completeInitialCommunityHandoff\(\s*"
        r"url,\s*initialNativeHandoff\.nativeDelegate\s*\);\s*\}",
        listener,
    )


@pytest.mark.unit
def test_social_native_delegate_is_the_fast_path_login_proof_with_safe_fallback():
    source = read_js_parts(APP_UI_PATH)

    listener_start = source.index("window.addEventListener('live2d-social-click', async () => {")
    listener_end = source.index("// 睡觉按钮（请她离开）", listener_start)
    listener = source[listener_start:listener_end]

    delegate_start = listener.index("const fetchNativeDelegate = async () => {")
    delegate_end = listener.index("const fetchSocialClientId = async () => {", delegate_start)
    delegate_helper = listener[delegate_start:delegate_end]
    assert "response.status === 409" in delegate_helper
    assert "{ nativeDelegate: '', loginState: 'logged-out' }" in delegate_helper
    assert "loginState: nativeDelegate ? 'logged-in' : 'unknown'" in delegate_helper
    assert delegate_helper.count("{ nativeDelegate: '', loginState: 'unknown' }") == 2

    main_start = listener.index("const initialNativeHandoff = await initialNativeHandoffPromise;")
    main_flow = listener[main_start:]
    unknown_guard = "if (initialNativeHandoff.loginState === 'unknown')"
    assert unknown_guard in main_flow
    assert main_flow.index(unknown_guard) < main_flow.index(
        "fetch('/api/card-drop/auth-status', { cache: 'no-store' })"
    )
    assert main_flow.index("if (!communityLoggedIn)") < main_flow.index(
        "fetch('/api/card-drop/oauth/start'"
    )
    assert "initialNativeHandoff.nativeDelegate" in main_flow


@pytest.mark.unit
def test_social_browser_fallback_preopens_popup_before_async_fetches():
    source = read_js_parts(APP_UI_PATH)

    listener_start = source.index("window.addEventListener('live2d-social-click', async () => {")
    listener_end = source.index("// 睡觉按钮（请她离开）", listener_start)
    listener = source[listener_start:listener_end]

    preopen = "popupRef = window.open('about:blank', '_blank');"
    assert preopen in listener
    assert listener.index(preopen) < listener.index(
        "const cfgRes = await fetch('/api/system/social/config');"
    )
    assert "oauthPopupRef" not in listener
    assert "const navigateBrowserPopup = (targetUrl, options = {}) => {" in listener
    assert listener.count("window.open('about:blank', '_blank')") == 1
    assert "currentPopup.opener = null;" in listener
    assert "currentPopup.location.replace(navigationTarget);" in listener
    assert "if (navigated && !options.keepReference)" in listener
    assert "const waitForOAuthCompletion = async (timeoutMs, requirePopup) => {" in listener
    assert "if (requirePopup)" in listener
    assert "let pollDelayMs = 1000;" in listener
    assert "Math.min(Math.ceil(pollDelayMs * 1.5), 5000)" in listener
    assert "fetch('/api/card-drop/oauth/status', { cache: 'no-store' })" in listener
    assert "navigateBrowserPopup(authUrl, { keepReference: true })" in listener
    assert "await waitForOAuthCompletion(" in listener
    assert "const refreshedTargetUrl = await attachNativeSyncTicket(" in listener
    assert "const refreshedDelegatePromise = fetchNativeDelegate();" in listener
    assert "const refreshedHandoff = await refreshedDelegatePromise;" in listener
    assert re.search(
        r"attachNativeDelegate\(\s*refreshedTargetUrl,\s*"
        r"refreshedHandoff\.nativeDelegate\s*\)",
        listener,
    )
    assert "navigateBrowserPopup(refreshedTargetUrl.toString())" in listener
    assert "openElectronSocialWindow(refreshedTargetUrl.toString())" in listener
    assert "const shouldWaitForOAuth = (isElectron && oauthLaunched)" in listener
    assert "|| (!isElectron && browserOAuthStarted);" in listener
    assert re.search(
        r"await waitForOAuthCompletion\(\s*browserOAuthTimeoutMs,\s*!isElectron\s*\)",
        listener,
    )
    assert listener.index("navigateBrowserPopup(url, { keepReference: true })") < listener.index(
        "const initialNativeHandoff = await initialNativeHandoffPromise;"
    )
    assert listener.index("fetch('/api/card-drop/auth-status'") < listener.index(
        "navigateBrowserPopup(authUrl, { keepReference: true })"
    )
    assert listener.index("navigateBrowserPopup(authUrl, { keepReference: true })") < listener.index(
        "await waitForOAuthCompletion("
    )
    assert re.search(
        r"else if \(!navigateBrowserPopup\(authUrl, \{ keepReference: true \}\)\) \{\s*"
        r"closePopup\(\);",
        listener,
    )
    assert listener.index("releaseSocialOpenRequest();") < listener.index(
        "await waitForOAuthCompletion("
    )
    assert listener.index("await waitForOAuthCompletion(") < listener.index(
        "navigateBrowserPopup(refreshedTargetUrl.toString())"
    )
    assert "window.open(authUrl, '_blank'" not in listener
    assert "closePopup();" in listener


@pytest.mark.unit
def test_credit_drop_event_plays_forge_overlay_animation():
    source = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")
    handler_start = source.index("function onCreditDropEvent(event) {")
    handler_end = source.index("function boot() {", handler_start)
    handler = source[handler_start:handler_end]

    assert "cachedCredits = Math.max(0, detail.active_count - 1);" in handler
    assert "play(queuedDetail);" in handler


@pytest.mark.unit
def test_forge_drop_effects_can_be_disabled_without_hiding_credit_updates():
    popup = AVATAR_UI_POPUP_PATH.read_text(encoding="utf-8")
    settings = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    overlay = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")
    reaction = FORGE_AVATAR_REACTION_PATH.read_text(encoding="utf-8")

    assert "settings.toggles.forgeDropEffects" in popup
    assert "window.forgeDropEffectsEnabled = enabled;" in popup
    assert "neko-forge-drop-effects-changed" in popup
    assert "forgeDropEffectsEnabled: currentForgeDropEffects" in settings
    assert "window.forgeDropEffectsEnabled = settings.forgeDropEffectsEnabled;" in settings
    drop_handler = _extract_js_function(overlay, "function onCreditDropEvent(event)")
    state_handler = _extract_js_function(overlay, "function onCreditStateEvent(event)")
    effects_handler = _extract_js_function(overlay, "function onDropEffectsChanged(event)")
    reaction_handler = _extract_js_function(reaction, "function react(detail)")
    play_one = _extract_js_function(overlay, "function playOne(payload)")
    assert "if (window.forgeDropEffectsEnabled === false)" not in drop_handler
    assert "play(queuedDetail);" in drop_handler
    assert "renderForgeBadge(" in state_handler
    assert "detail.active_count" in state_handler
    assert "audio.pause();" in effects_handler
    assert "activeAnimationCompleters" in effects_handler
    assert reaction_handler.index(
        "if (window.forgeDropEffectsEnabled === false) return;"
    ) < reaction_handler.index("var now = Date.now();")
    assert "renderForgeBadge(active, true);" in play_one
    assert "playGeneration !== dropEffectsGeneration" in play_one
    # 关闭效果的入口分支同样要按 revision 守卫，否则队尾券会用陈旧
    # active_count 覆盖权威刷新写过的角标。
    disabled_entry = play_one[play_one.index("if (window.forgeDropEffectsEnabled === false)"):]
    disabled_entry = disabled_entry[: disabled_entry.index("complete();")]
    assert "payloadRevision === creditStateRevision" in disabled_entry
    assert "renderForgeBadge(payload.active_count, true);" in disabled_entry

    for locale in ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"):
        locale_source = (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(
            encoding="utf-8"
        )
        assert '"forgeDropEffects"' in locale_source


@pytest.mark.unit
def test_credit_drop_avatar_bounds_and_clamp_execute_for_each_model_type():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")
    overlay = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")
    functions = "\n".join(
        _extract_js_function(overlay, signature)
        for signature in (
            "function normalizeAvatarBounds(bounds)",
            "function getActiveAvatarBounds()",
            "function clampCardCoordinate(value, size, viewportSize, margin)",
        )
    )
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
let pngBounds = null;
global.document = {{
  querySelector() {{
    return pngBounds ? {{ getBoundingClientRect: () => pngBounds }} : null;
  }}
}};
{functions}
const bounds = (left) => ({{ left, top: 20, right: left + 100, bottom: 220 }});
const manager = (left) => ({{ getModelScreenBounds: () => bounds(left) }});
window.live2dManager = manager(10);
window.vrmManager = manager(110);
window.mmdManager = manager(210);

window.lanlan_config = {{ model_type: 'live2d' }};
assert.equal(getActiveAvatarBounds().left, 10);
window.lanlan_config = {{ model_type: 'live3d', live3d_sub_type: 'vrm' }};
assert.equal(getActiveAvatarBounds().left, 110);
window.lanlan_config = {{ model_type: 'live3d', live3d_sub_type: 'mmd' }};
assert.equal(getActiveAvatarBounds().left, 210);
pngBounds = bounds(310);
window.lanlan_config = {{ model_type: 'pngtuber' }};
assert.equal(getActiveAvatarBounds().left, 310);

window.live2dManager = null;
window.vrmManager = null;
window.mmdManager = null;
window.lanlan_config = {{ model_type: 'unknown' }};
assert.equal(getActiveAvatarBounds().left, 310);
pngBounds = null;
assert.equal(getActiveAvatarBounds(), null);
assert.equal(clampCardCoordinate(-50, 180, 160, 12), 0);
assert.equal(clampCardCoordinate(999, 80, 160, 12), 80);
"""
    result = run_node_stdin(node, script, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.unit
def test_only_low_rarity_drops_anchor_to_avatar_lower_right():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")
    overlay = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")
    constants = "\n".join(
        line.strip()
        for line in overlay.splitlines()
        if re.match(
            r"\s*var (AVATAR_ANCHORED_RARITIES|ANCHOR_CARD_MAX_W|ANCHOR_CARD_MIN_W"
            r"|CENTER_CARD_MAX_W|CARD_MARGIN|CARD_ASPECT) =",
            line,
        )
    )
    functions = "\n".join(
        _extract_js_function(overlay, signature)
        for signature in (
            "function clampCardCoordinate(value, size, viewportSize, margin)",
            "function shouldAnchorToAvatar(rarity)",
            "function resolveCardWidth(rarity, avatarBounds)",
            "function getCardPlacement(cardWidth, cardHeight, avatarBounds)",
        )
    )
    script = f"""
const assert = require('node:assert/strict');
global.window = {{ innerWidth: 1200, innerHeight: 800 }};
{constants}
{functions}
const avatar = {{ left: 300, top: 100, right: 700, bottom: 620, width: 400, height: 520 }};

// N/R：贴角色右下方，卡宽跟随模型（400 * 0.55 = 220，落在 180–280 之间）。
for (const rarity of ['N', 'R']) {{
  assert.equal(shouldAnchorToAvatar(rarity), true, rarity);
  const width = resolveCardWidth(rarity, avatar);
  assert.ok(Math.abs(width - 220) < 1e-6, `${{rarity}} width=${{width}}`);
  const height = Math.round(width / CARD_ASPECT);
  const placement = getCardPlacement(width, height, avatar);
  assert.equal(placement.left, Math.round(avatar.right - width * 0.18), rarity);
  assert.ok(placement.left > avatar.left, rarity);
  assert.ok(placement.top + height <= avatar.bottom, rarity);
}}

// SR 及以上：保持屏幕中央的原始大卡演出，且不随模型边界漂移。
for (const rarity of ['SR', 'SSR', 'UR']) {{
  assert.equal(shouldAnchorToAvatar(rarity), false, rarity);
  const width = resolveCardWidth(rarity, avatar);
  assert.equal(width, 360, rarity);
  const height = Math.round(width / CARD_ASPECT);
  const placement = getCardPlacement(width, height, null);
  assert.equal(placement.left, Math.round(window.innerWidth * 0.5 - width / 2), rarity);
  assert.equal(placement.top, Math.round(window.innerHeight * 0.42 - height / 2), rarity);
}}

// 窄窗口下两档都不越界。
window.innerWidth = 240;
assert.equal(resolveCardWidth('N', avatar), 216);
assert.equal(resolveCardWidth('UR', avatar), 216);
"""
    result = run_node_stdin(node, script, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.unit
def test_credit_drop_uses_yui_ticket_art_for_every_drop_rarity():
    overlay = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")
    tokens = FORGE_DROP_TOKENS_PATH.read_text(encoding="utf-8")

    assert "ticketArt.className = 'ticket-art';" in overlay
    assert "t.ticketPath(rarity)" in overlay
    assert "var ANCHOR_CARD_MAX_W = 280;" in overlay
    assert "var ANCHOR_CARD_MIN_W = 180;" in overlay
    assert "var CENTER_CARD_MAX_W = 360;" in overlay
    assert "var CARD_MARGIN = 12;" in overlay
    assert "var CARD_ASPECT = 1192 / 445;" in overlay
    assert "var AVATAR_ANCHORED_RARITIES = { N: true, R: true };" in overlay
    assert "window.innerWidth - CARD_MARGIN * 2" in overlay
    # SR 及以上不读模型边界，直接走屏幕中央兜底路径。
    assert (
        "var avatarBounds = shouldAnchorToAvatar(rarity) ? getActiveAvatarBounds() : null;"
        in overlay
    )
    assert "var CARD_W = resolveCardWidth(rarity, avatarBounds);" in overlay
    assert "function getActiveAvatarBounds()" in overlay
    assert "live3dSubType === 'mmd' ? managers.mmd : managers.vrm" in overlay
    assert "live3dSubType === 'mmd' ? managers.vrm : managers.mmd" in overlay
    assert "manager.getModelScreenBounds()" in overlay
    assert "var availableWidth = Math.max(1, window.innerWidth - CARD_MARGIN * 2);" in overlay
    assert "function clampCardCoordinate(value, size, viewportSize, margin)" in overlay
    assert "function getCardPlacement(cardWidth, cardHeight, avatarBounds)" in overlay
    assert "avatarBounds.right - overlapX" in overlay
    assert "avatarBounds.bottom - cardHeight - liftY" in overlay
    assert "ticketAuraArt.className = 'ticket-aura-art';" in overlay
    assert "spark.textContent" not in overlay
    assert "className = 'rk'" not in overlay
    assert "className = 'meta'" not in overlay

    expected_assets = {
        "N": "forge-ticket-n.png",
        "R": "forge-ticket-r.png",
        "SR": "forge-ticket-sr.png",
        "SSR": "forge-ticket-ssr.png",
        "UR": "forge-ticket-ur.png",
    }
    for rarity, filename in expected_assets.items():
        version = "20260718-hd" if rarity == "UR" else "20260717-hd"
        assert f"{rarity}: '/static/assets/forge-tickets/{filename}?v={version}'" in tokens
        asset = PROJECT_ROOT / "static" / "assets" / "forge-tickets" / filename
        assert asset.is_file()
        png_header = asset.read_bytes()[:24]
        assert png_header[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", png_header[16:24])
        assert width >= 1000
        assert height >= 400


@pytest.mark.unit
def test_credit_drop_preloads_and_plays_the_supplied_rarity_sounds():
    overlay = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")
    tokens = FORGE_DROP_TOKENS_PATH.read_text(encoding="utf-8")

    expected_sounds = {
        "N": "rarity-n.mp3",
        "R": "rarity-r.mp3",
        "SR": "rarity-sr.wav",
        "SSR": "rarity-ssr.mp3",
        "UR": "rarity-ur.mp3",
    }
    for rarity, filename in expected_sounds.items():
        assert f"{rarity}: '/static/sounds/forge/{filename}?v=20260718-user'" in tokens
        audio = FORGE_SOUND_DIR / filename
        assert audio.is_file()
        assert audio.stat().st_size > 1_000
        header = audio.read_bytes()[:12]
        if audio.suffix == ".wav":
            assert header[:4] == b"RIFF"
            assert header[8:12] == b"WAVE"
        else:
            assert header[:3] == b"ID3" or header[:1] == b"\xff"

    assert "function preloadDropSounds()" in overlay
    assert "function playDropSound(rarity)" in overlay
    assert "audio.preload = 'auto';" in overlay
    assert "audio.currentTime = 0;" in overlay
    assert "var playResult = audio.play();" in overlay
    assert "playResult.catch(function () {});" in overlay
    assert "playDropSound(rarity);" in overlay
    assert "preloadDropSounds();" in overlay


@pytest.mark.unit
def test_credit_badge_uses_only_pc_pushed_cloud_state():
    source = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")

    assert "/api/card-drop/credits" not in source
    assert "window.addEventListener('neko-forge-credit-state'" in source
    assert "scheduleExpiryClear(detail.next_expires_at);" in source
    assert "neko-forge-credit-state-refresh" in source
    assert "function requestCreditStateRefresh()" in source
    refresh = _extract_js_function(source, "function requestCreditStateRefresh()")
    assert "neko-forge-credit-state-refresh" in refresh
    assert "replaySnapshots" not in refresh
    assert "renderForgeBadge(0, false);" not in source
    assert "neko-forge-credit-animation-complete" in source
    assert "earliest - now + 1000" in source


@pytest.mark.unit
def test_credit_badge_social_bridge_forwards_pc_credit_state():
    source = APP_SOCIAL_UI_PATH.read_text(encoding="utf-8")

    assert "window.nekoSocial.onForgeCreditChanged" in source
    assert "new window.CustomEvent('neko-forge-credit-state'" in source
    assert "detail: data || {}" in source
    # Refresh is owned by PC CREDIT_STATE_REFRESH. Replaying cached snapshots
    # here would hide expiry and keep the old next_expires_at timer.
    assert "replaySnapshots" not in source
    assert "neko-forge-credit-state-refresh" not in source


@pytest.mark.unit
def test_credit_badge_caches_count_before_button_mount():
    source = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")
    render_start = source.index("function renderForgeBadge(count, bump) {")
    render_end = source.index("function startForgeBadgeObserver()", render_start)
    render = source[render_start:render_end]

    assert render.index("cachedCredits = n;") < render.index("if (!badge) return;")


@pytest.mark.unit
def test_authoritative_credit_refresh_cannot_be_overwritten_by_queued_animation():
    source = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")

    assert "creditStateRevision += 1;" in source
    assert "__credit_state_revision: creditStateRevision" in source
    assert "payloadRevision === creditStateRevision" in source
    assert "function onCreditStateEvent(event)" in source
    assert "neko-forge-credit-state" in source
    assert "/api/card-drop/credits/local-summary" not in source
    assert "neko-forge-credit-animation-complete" in source
