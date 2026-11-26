from pathlib import Path

import pytest
from playwright.sync_api import Page


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SCRIPT = PROJECT_ROOT / "static" / "app" / "app-interpage" / "bootstrap-resources-and-model-reload.js"
RELAY_SCRIPT = PROJECT_ROOT / "static" / "app" / "app-interpage" / "guide-message-relay.js"
CHARACTER_SCRIPT = PROJECT_ROOT / "static" / "app" / "app-character.js"


def _open_same_origin_blank(page: Page, running_server: str) -> None:
    # A JSON route gives the page the app origin without loading the full home
    # runtime (tutorial/model/audio startup would add irrelevant async noise).
    page.goto(f"{running_server}/api/config/page_config", wait_until="domcontentloaded")
    page.set_content("<html><body></body></html>")


@pytest.mark.frontend
def test_same_millisecond_bc_switches_are_ordered_by_revision(
    mock_page: Page,
    running_server: str,
):
    _open_same_origin_blank(mock_page, running_server)
    mock_page.evaluate(
        """
        () => {
            window.appState = {};
            window.lanlan_config = { lanlan_name: 'A', model_type: 'live2d' };
            window.showStatusToast = () => {};
        }
        """
    )
    mock_page.add_script_tag(path=str(BOOTSTRAP_SCRIPT))
    mock_page.evaluate(
        """
        () => {
            const I = window.__appInterpageParts;
            I.isIcebreakerBridgeAction = () => false;
            I.shouldBypassYuiGuideMessageDedup = () => false;
            I.isYuiGuideLifecycleStartAction = () => false;
            I.isYuiGuideLifecycleScopedAction = () => false;
            I.isYuiGuideMessageForCurrentLifecycle = () => true;
            I.isYuiGuidePcOverlayRunEnded = () => false;
            I.clearYuiGuidePcOverlayBridgeState = () => {};
            I.isHighVolumeBroadcastChannelAction = () => false;
            I.drainPendingYuiGuideChatBridgeQueue = () => {};
            window.__switchCalls = [];
            window.handleCatgirlSwitch = (next, previous, options = {}) => {
                window.__switchCalls.push({
                    next,
                    previous,
                    revision: options.switchRevision || 0
                });
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(RELAY_SCRIPT))
    mock_page.evaluate("""() => {
        const I = window.__appInterpageParts;
        I.nekoBroadcastChannel.onmessage = I.handleNekoBroadcastMessage;
    }""")

    sender = mock_page.context.new_page()
    try:
        _open_same_origin_blank(sender, running_server)
        sender.evaluate(
            """
            () => {
                window.__switchOrderingSender = new BroadcastChannel('neko_page_channel');
                const channel = window.__switchOrderingSender;
                const timestamp = Date.now();
                channel.postMessage({
                    action: 'catgirl_switched', new_catgirl: 'B', old_catgirl: 'A',
                    switch_revision: 910, timestamp
                });
                channel.postMessage({
                    action: 'catgirl_switched', new_catgirl: 'C', old_catgirl: 'B',
                    switch_revision: 911, timestamp
                });
            }
            """
        )
        mock_page.wait_for_function("() => window.__switchCalls.length >= 2", timeout=3000)
        state = mock_page.evaluate("() => ({ calls: window.__switchCalls })")
        sender.evaluate("() => window.__switchOrderingSender?.close()")
    finally:
        sender.close()

    assert state["calls"] == [
        {"next": "B", "previous": "A", "revision": 910},
        {"next": "C", "previous": "B", "revision": 911},
    ]


@pytest.mark.frontend
def test_initial_page_revision_rejects_delayed_older_switch_in_real_browser(
    mock_page: Page,
    running_server: str,
):
    _open_same_origin_blank(mock_page, running_server)
    mock_page.evaluate(
        """
        () => {
            window.appState = { incomingAudioEpoch: 0 };
            window.lanlan_config = {
                lanlan_name: 'C', model_type: 'live2d', switch_revision: 921
            };
            window.invalidatePendingMusicSearch = () => {};
            window.connectWebSocket = () => {};
            window.showStatusToast = () => {};
            window.clearAudioQueue = async () => {};
            window.__avatarBegins = 0;
            window.appChatAvatarState = {
                beginCharacterSwitch() { window.__avatarBegins += 1; },
                commitCharacterSwitch() {},
                rollbackCharacterSwitch() {}
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(CHARACTER_SCRIPT))

    state = mock_page.evaluate(
        """
        async () => {
            await window.handleCatgirlSwitch('B', 'A', { switchRevision: 920 });
            return {
                name: window.lanlan_config.lanlan_name,
                pageRevision: window.lanlan_config.switch_revision,
                stateRevision: window.appState._latestCatgirlSwitchRevision,
                begins: window.__avatarBegins
            };
        }
        """
    )

    assert state == {
        "name": "C",
        "pageRevision": 921,
        "stateRevision": 921,
        "begins": 0,
    }
