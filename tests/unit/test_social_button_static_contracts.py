import struct
from pathlib import Path

import pytest
from tests.static_app_parts import read_js_parts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
FORGE_DROP_OVERLAY_PATH = PROJECT_ROOT / "static" / "forge-drop-overlay.js"
FORGE_DROP_TOKENS_PATH = PROJECT_ROOT / "static" / "forge-drop-tokens.js"
FORGE_SOUND_DIR = PROJECT_ROOT / "static" / "sounds" / "forge"


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
    assert listener.index("finally {") < listener.index("releaseSocialOpenRequest();")
    assert listener.index("releaseSocialOpenRequest();") > listener.index("window.electronShell.openExternal(url)")
    assert "fetch('/api/card-drop/sync-ticket', { cache: 'no-store' })" in listener
    assert "native_sync: String(ticketJson.sync_ticket)" in listener
    assert "targetUrl.searchParams.set('cid', cidJson.client_id)" in listener
    assert "social_base_url" in listener
    assert "/feed" in listener
    # Feed first; Desktop OAuth only after open when not logged in.
    assert "fetch('/api/card-drop/auth-status', { cache: 'no-store' })" in listener
    assert "fetch('/api/card-drop/oauth/start'" in listener
    assert "请在浏览器完成统一账号登录" in listener
    assert listener.index("openExternal(url)") < listener.index(
        "fetch('/api/card-drop/auth-status'"
    )
    assert listener.index("fetch('/api/card-drop/auth-status'") < listener.index(
        "fetch('/api/card-drop/oauth/start'"
    )
    protocol_guard = "targetUrl.protocol !== 'http:' && targetUrl.protocol !== 'https:'"
    assert protocol_guard in listener
    assert listener.index(protocol_guard) < listener.index(
        "fetch('/api/card-drop/sync-ticket', { cache: 'no-store' })"
    )


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
    assert "popupRef.opener = null;" in listener
    assert listener.index("popupRef.opener = null;") < listener.index("popupRef.location.replace(url);")
    assert "popupRef.location.replace(url);" in listener
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
def test_credit_drop_uses_yui_ticket_art_for_every_drop_rarity():
    overlay = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")
    tokens = FORGE_DROP_TOKENS_PATH.read_text(encoding="utf-8")

    assert "ticketArt.className = 'ticket-art';" in overlay
    assert "t.ticketPath(rarity)" in overlay
    assert "var CARD_MAX_W = 360;" in overlay
    assert "var CARD_MARGIN = 12;" in overlay
    assert "var CARD_ASPECT = 1192 / 445;" in overlay
    assert "window.innerWidth - CARD_MARGIN * 2" in overlay
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
def test_credit_badge_uses_bounded_retry_and_low_frequency_reconciliation():
    source = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")

    assert "fetch('/api/card-drop/credits/local-summary'" in source
    assert "fetch('/api/card-drop/credits'," not in source
    assert "var STARTUP_RETRY_DELAYS_MS = [2000, 10000, 30000];" in source
    assert "startupRetryIndex >= STARTUP_RETRY_DELAYS_MS.length" in source
    assert "var PASSIVE_REFRESH_MS = 10 * 60 * 1000;" in source
    assert "}, PASSIVE_REFRESH_MS);" in source
    assert "window.addEventListener('focus', requestInteractiveRefresh);" in source
    assert "document.addEventListener('visibilitychange'" in source
    assert "scheduleExpiryRefresh(data.next_expires_at);" in source
    assert "earliest - now + 1000" in source


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
    assert "requestRevision !== creditStateRevision" in source
    assert "creditRefreshAfterInFlight = true;" in source
    assert "cache: 'no-store'" in source
