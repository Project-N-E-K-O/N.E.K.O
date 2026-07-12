from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_UI_PATH = PROJECT_ROOT / "static" / "app-ui.js"
FORGE_DROP_OVERLAY_PATH = PROJECT_ROOT / "static" / "forge-drop-overlay.js"


@pytest.mark.unit
def test_social_open_request_is_deduped_before_fetching_config():
    source = APP_UI_PATH.read_text(encoding="utf-8")

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


@pytest.mark.unit
def test_social_browser_fallback_preopens_popup_before_async_fetches():
    source = APP_UI_PATH.read_text(encoding="utf-8")

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
def test_credit_badge_uses_bounded_retry_and_low_frequency_reconciliation():
    source = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")

    assert "var STARTUP_RETRY_DELAYS_MS = [2000, 10000, 30000];" in source
    assert "startupRetryIndex >= STARTUP_RETRY_DELAYS_MS.length" in source
    assert "var PASSIVE_REFRESH_MS = 10 * 60 * 1000;" in source
    assert "}, PASSIVE_REFRESH_MS);" in source
    assert "window.addEventListener('focus', requestInteractiveRefresh);" in source
    assert "document.addEventListener('visibilitychange'" in source
    assert "scheduleExpiryRefresh(data.credits);" in source
    assert "earliest - now + 1000" in source


@pytest.mark.unit
def test_authoritative_credit_refresh_cannot_be_overwritten_by_queued_animation():
    source = FORGE_DROP_OVERLAY_PATH.read_text(encoding="utf-8")

    assert "creditStateRevision += 1;" in source
    assert "__credit_state_revision: creditStateRevision" in source
    assert "payloadRevision === creditStateRevision" in source
    assert "requestRevision !== creditStateRevision" in source
    assert "creditRefreshAfterInFlight = true;" in source
    assert "cache: 'no-store'" in source
