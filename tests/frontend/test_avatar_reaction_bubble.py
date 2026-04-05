import pytest
from playwright.sync_api import Page, expect


@pytest.mark.frontend
def test_turn_end_does_not_clear_completion_before_late_speech_start(mock_page: Page, running_server: str):
    """Regression test for late TTS start after turn end."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.appState && window.avatarReactionBubble)",
        timeout=10000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-late-audio', timestamp: Date.now() }
            }));
            window.dispatchEvent(new CustomEvent('neko-assistant-turn-end', {
                detail: { turnId: 'turn-late-audio', source: 'test', timestamp: Date.now() }
            }));
        }
        """
    )

    mock_page.wait_for_timeout(900)

    completion_turn_id = mock_page.evaluate("() => window.appState.assistantTurnCompletedId")
    expect(mock_page.locator("#avatar-reaction-bubble")).to_have_attribute("aria-hidden", "false")
    assert completion_turn_id == "turn-late-audio"


@pytest.mark.frontend
def test_live2d_face_rect_keeps_bubble_near_head(mock_page: Page, running_server: str):
    """Regression test for Live2D models with large blank space above the visible face."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.appState && window.avatarReactionBubble)",
        timeout=10000,
    )

    metrics = mock_page.evaluate(
        """
        () => {
            const live2dContainer = document.getElementById('live2d-container');
            const vrmContainer = document.getElementById('vrm-container');
            const mmdContainer = document.getElementById('mmd-container');
            const bubble = document.getElementById('avatar-reaction-bubble');

            live2dContainer.style.display = 'block';
            live2dContainer.style.visibility = 'visible';
            vrmContainer.style.display = 'none';
            mmdContainer.style.display = 'none';

            const bounds = {
                left: 96,
                top: -280,
                right: 416,
                bottom: 1120,
                width: 320,
                height: 1400,
                centerX: 256,
                centerY: 420
            };
            const headRect = {
                left: 186,
                top: 198,
                right: 326,
                bottom: 364,
                width: 140,
                height: 166,
                centerX: 256,
                centerY: 281
            };
            const headAnchor = { x: 256, y: 266 };

            window.vrmManager = null;
            window.mmdManager = null;
            window.live2dManager = {
                getModelScreenBounds() {
                    return bounds;
                },
                getHeadScreenAnchor() {
                    return headAnchor;
                },
                getHeadScreenRectInfo() {
                    return {
                        rect: headRect,
                        mode: 'face'
                    };
                },
                getBodyScreenRectInfo() {
                    return null;
                }
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-live2d-face-lock', timestamp: Date.now() }
            }));

            const top = parseFloat(bubble.style.top || '0');
            const bubbleHeight = parseFloat(getComputedStyle(bubble).getPropertyValue('--bubble-height') || '0');

            return {
                top,
                bubbleHeight,
                headTop: headRect.top
            };
        }
        """
    )

    expect(mock_page.locator("#avatar-reaction-bubble")).to_have_attribute("aria-hidden", "false")
    assert metrics["bubbleHeight"] > 0
    assert metrics["top"] >= metrics["headTop"] - metrics["bubbleHeight"] * 0.34 - 2
    assert metrics["top"] <= metrics["headTop"] - metrics["bubbleHeight"] * 0.18 + 2


@pytest.mark.frontend
def test_live2d_body_top_fallback_ignores_blank_space(mock_page: Page, running_server: str):
    """Regression test for Live2D models without usable head data."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.appState && window.avatarReactionBubble)",
        timeout=10000,
    )

    metrics = mock_page.evaluate(
        """
        () => {
            const live2dContainer = document.getElementById('live2d-container');
            const vrmContainer = document.getElementById('vrm-container');
            const mmdContainer = document.getElementById('mmd-container');
            const bubble = document.getElementById('avatar-reaction-bubble');

            live2dContainer.style.display = 'block';
            live2dContainer.style.visibility = 'visible';
            vrmContainer.style.display = 'none';
            mmdContainer.style.display = 'none';

            const bounds = {
                left: 96,
                top: -280,
                right: 416,
                bottom: 1120,
                width: 320,
                height: 1400,
                centerX: 256,
                centerY: 420
            };
            const bodyRect = {
                left: 162,
                top: 320,
                right: 350,
                bottom: 676,
                width: 188,
                height: 356,
                centerX: 256,
                centerY: 498
            };

            window.vrmManager = null;
            window.mmdManager = null;
            window.live2dManager = {
                getModelScreenBounds() {
                    return bounds;
                },
                getHeadScreenAnchor() {
                    return null;
                },
                getHeadScreenRectInfo() {
                    return null;
                },
                getBodyScreenRectInfo() {
                    return {
                        rect: bodyRect,
                        mode: 'body'
                    };
                }
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-live2d-body-top', timestamp: Date.now() }
            }));

            const top = parseFloat(bubble.style.top || '0');
            const bubbleHeight = parseFloat(getComputedStyle(bubble).getPropertyValue('--bubble-height') || '0');

            return {
                top,
                bubbleHeight,
                bodyTop: bodyRect.top
            };
        }
        """
    )

    expect(mock_page.locator("#avatar-reaction-bubble")).to_have_attribute("aria-hidden", "false")
    assert metrics["bubbleHeight"] > 0
    assert metrics["top"] >= metrics["bodyTop"] - metrics["bubbleHeight"] * 0.34 - 2
    assert metrics["top"] <= metrics["bodyTop"] - metrics["bubbleHeight"] * 0.18 + 2


@pytest.mark.frontend
def test_live2d_face_rect_keeps_bounds_driven_bubble_size(mock_page: Page, running_server: str):
    """Regression test that face rect anchoring does not freeze Live2D bubble sizing."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.appState && window.avatarReactionBubble)",
        timeout=10000,
    )

    metrics = mock_page.evaluate(
        """
        () => {
            const live2dContainer = document.getElementById('live2d-container');
            const vrmContainer = document.getElementById('vrm-container');
            const mmdContainer = document.getElementById('mmd-container');
            const bubble = document.getElementById('avatar-reaction-bubble');

            live2dContainer.style.display = 'block';
            live2dContainer.style.visibility = 'visible';
            vrmContainer.style.display = 'none';
            mmdContainer.style.display = 'none';

            const headRect = {
                left: 190,
                top: 170,
                right: 322,
                bottom: 332,
                width: 132,
                height: 162,
                centerX: 256,
                centerY: 251
            };
            const headAnchor = {
                x: headRect.centerX,
                y: headRect.top + headRect.height * 0.42
            };

            let currentBounds = {
                left: 136,
                top: 96,
                right: 376,
                bottom: 576,
                width: 240,
                height: 480,
                centerX: 256,
                centerY: 336
            };

            window.vrmManager = null;
            window.mmdManager = null;
            window.live2dManager = {
                getModelScreenBounds() {
                    return currentBounds;
                },
                getHeadScreenAnchor() {
                    return headAnchor;
                },
                getHeadScreenRectInfo() {
                    return {
                        rect: headRect,
                        mode: 'face'
                    };
                },
                getBodyScreenRectInfo() {
                    return null;
                }
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-live2d-small-bounds', timestamp: Date.now() }
            }));

            const smallWidth = parseFloat(getComputedStyle(bubble).getPropertyValue('--bubble-width') || '0');
            const smallHeight = parseFloat(getComputedStyle(bubble).getPropertyValue('--bubble-height') || '0');

            window.avatarReactionBubble.forceHide();

            currentBounds = {
                left: 76,
                top: -120,
                right: 436,
                bottom: 700,
                width: 360,
                height: 820,
                centerX: 256,
                centerY: 290
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-live2d-large-bounds', timestamp: Date.now() }
            }));

            const largeWidth = parseFloat(getComputedStyle(bubble).getPropertyValue('--bubble-width') || '0');
            const largeHeight = parseFloat(getComputedStyle(bubble).getPropertyValue('--bubble-height') || '0');

            return {
                smallWidth,
                smallHeight,
                largeWidth,
                largeHeight
            };
        }
        """
    )

    assert metrics["smallWidth"] > 0
    assert metrics["smallHeight"] > 0
    assert metrics["largeWidth"] > metrics["smallWidth"]
    assert metrics["largeHeight"] > metrics["smallHeight"]
