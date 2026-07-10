from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_UI_PATH = PROJECT_ROOT / "static" / "app-ui.js"


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
