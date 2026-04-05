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
def test_live2d_manager_prefers_display_info_over_coarse_autonamed_hitarea(mock_page: Page, running_server: str):
    """Regression test for models whose repaired HitArea is much coarser than DisplayInfo."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.live2dManager && window.live2dManager.getHeadScreenRectInfo)",
        timeout=10000,
    )

    selected = mock_page.evaluate(
        """
        () => {
            const manager = window.live2dManager;
            const originalGetModelScreenBounds = manager.getModelScreenBounds.bind(manager);
            const originalGetHeadHitAreaScreenRectInfo = manager._getHeadHitAreaScreenRectInfo.bind(manager);
            const originalGetDisplayInfoPartScreenRectInfo = manager._getDisplayInfoPartScreenRectInfo.bind(manager);

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
            const coarseHitArea = {
                rect: {
                    left: 146,
                    top: 80,
                    right: 364,
                    bottom: 456,
                    width: 218,
                    height: 376,
                    centerX: 255,
                    centerY: 268
                },
                mode: 'face',
                source: 'hitArea',
                hitAreaId: 'HitAreaHead',
                hitAreaName: 'HitAreaHead',
                autoNamed: true
            };
            const displayInfo = {
                rect: {
                    left: 192,
                    top: 212,
                    right: 320,
                    bottom: 392,
                    width: 128,
                    height: 180,
                    centerX: 256,
                    centerY: 302
                },
                mode: 'face',
                source: 'displayInfo'
            };

            try {
                manager.getModelScreenBounds = () => bounds;
                manager._getHeadHitAreaScreenRectInfo = () => coarseHitArea;
                manager._getDisplayInfoPartScreenRectInfo = (kind) => kind === 'head' ? displayInfo : null;

                const selectedInfo = manager.getHeadScreenRectInfo();
                return {
                    source: selectedInfo?.source || null,
                    top: selectedInfo?.rect?.top || null,
                    width: selectedInfo?.rect?.width || null
                };
            } finally {
                manager.getModelScreenBounds = originalGetModelScreenBounds;
                manager._getHeadHitAreaScreenRectInfo = originalGetHeadHitAreaScreenRectInfo;
                manager._getDisplayInfoPartScreenRectInfo = originalGetDisplayInfoPartScreenRectInfo;
            }
        }
        """
    )

    assert selected["source"] == "displayInfo"
    assert selected["top"] == 212
    assert selected["width"] == 128


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
                bodyTop: bodyRect.top,
                bodyHeight: bodyRect.height
            };
        }
        """
    )

    expect(mock_page.locator("#avatar-reaction-bubble")).to_have_attribute("aria-hidden", "false")
    assert metrics["bubbleHeight"] > 0
    assert metrics["top"] <= metrics["bodyTop"] - metrics["bodyHeight"] * 0.25
    assert metrics["top"] >= metrics["bodyTop"] - metrics["bodyHeight"] * 0.8


@pytest.mark.frontend
def test_live2d_display_info_rect_uses_relaxed_head_reliability(mock_page: Page, running_server: str):
    """Regression test for stylized DisplayInfo head rects that are valid but taller than HitArea heuristics."""
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
                left: 190,
                top: 220,
                right: 322,
                bottom: 430,
                width: 132,
                height: 210,
                centerX: 256,
                centerY: 325
            };
            const bodyRect = {
                left: 154,
                top: 352,
                right: 358,
                bottom: 552,
                width: 204,
                height: 200,
                centerX: 256,
                centerY: 452
            };

            window.vrmManager = null;
            window.mmdManager = null;
            window.NEKO_DEBUG_BUBBLE_POSITION = false;
            window.NEKO_DEBUG_BUBBLE_LIFECYCLE = false;
            window.live2dManager = {
                getModelScreenBounds() {
                    return bounds;
                },
                getHeadScreenAnchor() {
                    return {
                        x: headRect.centerX,
                        y: headRect.top + headRect.height * 0.32
                    };
                },
                getHeadScreenRectInfo() {
                    return {
                        rect: headRect,
                        mode: 'face',
                        source: 'displayInfo'
                    };
                },
                getBodyScreenRectInfo() {
                    return {
                        rect: bodyRect,
                        mode: 'body',
                        source: 'displayInfo'
                    };
                },
                getBubbleAnchorDebugInfo() {
                    return null;
                }
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-live2d-displayinfo-relaxed', timestamp: Date.now() }
            }));

            const top = parseFloat(bubble.style.top || '0');
            const bubbleHeight = parseFloat(getComputedStyle(bubble).getPropertyValue('--bubble-height') || '0');

            return {
                top,
                bubbleHeight,
                headTop: headRect.top,
                bodyTop: bodyRect.top
            };
        }
        """
    )

    assert metrics["bubbleHeight"] > 0
    assert metrics["top"] <= metrics["headTop"] - metrics["bubbleHeight"] * 0.08 + 2
    assert metrics["top"] >= metrics["headTop"] - metrics["bubbleHeight"] * 0.4 - 2
    assert metrics["top"] < metrics["bodyTop"] - metrics["bubbleHeight"] * 0.35


@pytest.mark.frontend
def test_live2d_display_info_top_offset_does_not_float_too_high(mock_page: Page, running_server: str):
    """Regression test for precise DisplayInfo models whose bubble used to float too high above the head."""
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
                left: 124,
                top: 64,
                right: 388,
                bottom: 724,
                width: 264,
                height: 660,
                centerX: 256,
                centerY: 394
            };
            const headRect = {
                left: 188,
                top: 158,
                right: 324,
                bottom: 334,
                width: 136,
                height: 176,
                centerX: 256,
                centerY: 246
            };
            const bodyRect = {
                left: 166,
                top: 292,
                right: 346,
                bottom: 586,
                width: 180,
                height: 294,
                centerX: 256,
                centerY: 439
            };

            window.vrmManager = null;
            window.mmdManager = null;
            window.NEKO_DEBUG_BUBBLE_POSITION = false;
            window.NEKO_DEBUG_BUBBLE_LIFECYCLE = false;
            window.live2dManager = {
                getModelScreenBounds() {
                    return bounds;
                },
                getHeadScreenAnchor() {
                    return {
                        x: headRect.centerX,
                        y: headRect.top + headRect.height * 0.36
                    };
                },
                getHeadScreenRectInfo() {
                    return {
                        rect: headRect,
                        mode: 'face',
                        source: 'displayInfo'
                    };
                },
                getBodyScreenRectInfo() {
                    return {
                        rect: bodyRect,
                        mode: 'body',
                        source: 'displayInfo'
                    };
                },
                getBubbleAnchorDebugInfo() {
                    return null;
                }
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-live2d-displayinfo-head-close', timestamp: Date.now() }
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

    assert metrics["bubbleHeight"] > 0
    assert metrics["top"] <= metrics["headTop"] - metrics["bubbleHeight"] * 0.08 + 2
    assert metrics["top"] >= metrics["headTop"] - metrics["bubbleHeight"] * 0.32 - 2


@pytest.mark.frontend
def test_live2d_hitarea_head_rect_does_not_force_bubble_too_high(mock_page: Page, running_server: str):
    """Regression test for coarse head hit areas that start far above the visible face."""
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
            const coarseHeadRect = {
                left: 160,
                top: 60,
                right: 352,
                bottom: 280,
                width: 192,
                height: 220,
                centerX: 256,
                centerY: 170
            };
            const headAnchor = {
                x: 256,
                y: 278
            };

            window.vrmManager = null;
            window.mmdManager = null;
            window.NEKO_DEBUG_BUBBLE_POSITION = false;
            window.NEKO_DEBUG_BUBBLE_LIFECYCLE = false;
            window.live2dManager = {
                getModelScreenBounds() {
                    return bounds;
                },
                getHeadScreenAnchor() {
                    return headAnchor;
                },
                getHeadScreenRectInfo() {
                    return {
                        rect: coarseHeadRect,
                        mode: 'face',
                        source: 'hitArea'
                    };
                },
                getBodyScreenRectInfo() {
                    return null;
                },
                getBubbleAnchorDebugInfo() {
                    return null;
                }
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-live2d-hitarea-not-too-high', timestamp: Date.now() }
            }));

            const top = parseFloat(bubble.style.top || '0');

            return {
                top,
                headTop: coarseHeadRect.top
            };
        }
        """
    )

    assert metrics["top"] >= metrics["headTop"] + 10


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


@pytest.mark.frontend
def test_live2d_debug_snapshot_is_lazy_when_debug_disabled(mock_page: Page, running_server: str):
    """Regression test that bubble positioning debug data is not sampled when debug is off."""
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

            live2dContainer.style.display = 'block';
            live2dContainer.style.visibility = 'visible';
            vrmContainer.style.display = 'none';
            mmdContainer.style.display = 'none';

            const bounds = {
                left: 136,
                top: 96,
                right: 376,
                bottom: 576,
                width: 240,
                height: 480,
                centerX: 256,
                centerY: 336
            };
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

            let debugCalls = 0;
            window.NEKO_DEBUG_BUBBLE_POSITION = false;
            window.NEKO_DEBUG_BUBBLE_LIFECYCLE = false;
            window.vrmManager = null;
            window.mmdManager = null;
            window.live2dManager = {
                getModelScreenBounds() {
                    return bounds;
                },
                getHeadScreenAnchor() {
                    return {
                        x: headRect.centerX,
                        y: headRect.top + headRect.height * 0.42
                    };
                },
                getHeadScreenRectInfo() {
                    return {
                        rect: headRect,
                        mode: 'face',
                        source: 'displayInfo'
                    };
                },
                getBodyScreenRectInfo() {
                    return null;
                },
                getBubbleAnchorDebugInfo() {
                    debugCalls += 1;
                    return {
                        modelName: 'debug-off'
                    };
                }
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-live2d-debug-disabled', timestamp: Date.now() }
            }));

            return { debugCalls };
        }
        """
    )

    assert metrics["debugCalls"] == 0
