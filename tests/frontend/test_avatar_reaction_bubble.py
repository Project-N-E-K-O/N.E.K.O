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

    mock_page.wait_for_timeout(260)
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko-assistant-speech-start', {
                detail: {
                    turnId: 'turn-late-audio',
                    source: 'late-tts-test',
                    timestamp: Date.now()
                }
            }));
        }
        """
    )
    mock_page.wait_for_timeout(640)

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
def test_vrm_direct_head_anchor_drives_reaction_bubble_position(mock_page: Page, running_server: str):
    """Regression test: VRM head anchor changes should directly drive bubble anchor updates."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.appState && window.avatarReactionBubble)",
        timeout=10000,
    )

    mock_page.evaluate(
        """
        () => {
            const live2dContainer = document.getElementById('live2d-container');
            const vrmContainer = document.getElementById('vrm-container');
            const mmdContainer = document.getElementById('mmd-container');

            live2dContainer.style.display = 'none';
            mmdContainer.style.display = 'none';
            vrmContainer.style.display = 'block';
            vrmContainer.style.visibility = 'visible';

            const bounds = {
                left: 120,
                top: 60,
                right: 520,
                bottom: 680,
                width: 400,
                height: 620,
                centerX: 320,
                centerY: 370
            };

            window.__testVrmHeadAnchor = { x: 262, y: 334 };
            window.live2dManager = null;
            window.mmdManager = null;
            window.vrmManager = {
                getModelScreenBounds() {
                    return bounds;
                },
                getHeadDetectionGeometryInfo() {
                    const p = window.__testVrmHeadAnchor;
                    return {
                        type: 'vrm',
                        bounds,
                        rawHeadAnchor: { x: p.x, y: p.y },
                        headAnchor: { x: p.x, y: p.y },
                        headRect: null,
                        headMode: 'head',
                        headSource: 'bone',
                        bodyRect: null,
                        bodySource: null,
                        reliableHeadRect: false,
                        preciseDisplayInfoRect: false,
                        coarseHitAreaHeadRect: false
                    };
                },
                getHeadScreenAnchor() {
                    const p = window.__testVrmHeadAnchor;
                    return { x: p.x, y: p.y };
                }
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-vrm-head-anchor-1', timestamp: Date.now() }
            }));
        }
        """
    )

    mock_page.wait_for_timeout(120)
    first = mock_page.evaluate(
        """
        () => {
            const s = window.avatarReactionBubble.getState();
            return { anchorX: s.anchorX, anchorY: s.anchorY };
        }
        """
    )

    mock_page.evaluate(
        """
        () => {
            window.__testVrmHeadAnchor = { x: 498, y: 176 };
            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-vrm-head-anchor-2', timestamp: Date.now() }
            }));
        }
        """
    )

    mock_page.wait_for_timeout(120)
    second = mock_page.evaluate(
        """
        () => {
            const s = window.avatarReactionBubble.getState();
            return { anchorX: s.anchorX, anchorY: s.anchorY };
        }
        """
    )

    expect(mock_page.locator("#avatar-reaction-bubble")).to_have_attribute("aria-hidden", "false")
    assert abs(second["anchorX"] - first["anchorX"]) > 120
    assert first["anchorY"] - second["anchorY"] > 70


@pytest.mark.frontend
def test_vrm_debug_snapshot_contains_3d_proxy_rects(mock_page: Page, running_server: str):
    """Regression test: 3D avatars should expose debug rects even without Live2D-style head/body rectangles."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.appState && window.avatarReactionBubble)",
        timeout=10000,
    )

    snapshot = mock_page.evaluate(
        """
        () => {
            const live2dContainer = document.getElementById('live2d-container');
            const vrmContainer = document.getElementById('vrm-container');
            const mmdContainer = document.getElementById('mmd-container');

            live2dContainer.style.display = 'none';
            mmdContainer.style.display = 'none';
            vrmContainer.style.display = 'block';
            vrmContainer.style.visibility = 'visible';

            const bounds = {
                left: 120,
                top: 60,
                right: 520,
                bottom: 680,
                width: 400,
                height: 620,
                centerX: 320,
                centerY: 370
            };

            window.live2dManager = null;
            window.mmdManager = null;
            window.vrmManager = {
                getModelScreenBounds() {
                    return bounds;
                },
                getHeadScreenAnchor() {
                    return { x: 328, y: 214 };
                }
            };

            window.avatarReactionBubble.setDebugOverlayEnabled(true);
            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-vrm-debug-proxy-rects', timestamp: Date.now() }
            }));

            const s = window.avatarReactionBubble.getState().lastDebugSnapshot || {};
            return {
                model: s.model || null,
                headWidth: s.headRect?.width || null,
                headTop: s.headRect?.top || null,
                bodyTop: s.bodyRect?.top || null,
                boundsWidth: s.bounds?.width || null,
                bubbleWidth: s.bubbleRect?.width || null
            };
        }
        """
    )

    expect(mock_page.locator("#avatar-reaction-bubble")).to_have_attribute("aria-hidden", "false")
    assert snapshot["model"] == "vrm"
    assert snapshot["headWidth"] is not None and snapshot["headWidth"] > 0
    assert snapshot["bodyTop"] is not None and snapshot["headTop"] is not None
    assert snapshot["bodyTop"] > snapshot["headTop"]
    assert snapshot["boundsWidth"] is not None and snapshot["boundsWidth"] > 0
    assert snapshot["bubbleWidth"] is not None and snapshot["bubbleWidth"] > 0


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


@pytest.mark.frontend
def test_live2d_manager_prefers_drawable_rects_for_chibi_with_coarse_hitareas(mock_page: Page, running_server: str):
    """Regression test for q-version / mascot models whose HitArea boxes cover far too much of the figure."""
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
            const originalGetModelLogicalRect = manager._getModelLogicalRect.bind(manager);
            const originalGetRenderableDrawableScreenRects = manager._getRenderableDrawableScreenRects.bind(manager);
            const originalGetHeadHitAreaScreenRectInfo = manager._getHeadHitAreaScreenRectInfo.bind(manager);
            const originalGetBodyHitAreaScreenRectInfo = manager._getBodyHitAreaScreenRectInfo.bind(manager);
            const originalGetDisplayInfoPartScreenRectInfo = manager._getDisplayInfoPartScreenRectInfo.bind(manager);

            const bounds = {
                left: 96,
                top: 48,
                right: 416,
                bottom: 560,
                width: 320,
                height: 512,
                centerX: 256,
                centerY: 304
            };
            const coarseHeadHitArea = {
                rect: {
                    left: 118,
                    top: 54,
                    right: 394,
                    bottom: 318,
                    width: 276,
                    height: 264,
                    centerX: 256,
                    centerY: 186
                },
                mode: 'face',
                source: 'hitArea',
                hitAreaId: 'Head',
                hitAreaName: 'Head',
                autoNamed: false
            };
            const coarseBodyHitArea = {
                rect: {
                    left: 104,
                    top: 140,
                    right: 408,
                    bottom: 506,
                    width: 304,
                    height: 366,
                    centerX: 256,
                    centerY: 323
                },
                mode: 'body',
                source: 'hitArea',
                hitAreaId: 'Body',
                hitAreaName: 'Body',
                autoNamed: false
            };
            const drawableRects = [
                {
                    left: 174,
                    top: 92,
                    right: 338,
                    bottom: 212,
                    width: 164,
                    height: 120,
                    centerX: 256,
                    centerY: 152
                },
                {
                    left: 196,
                    top: 224,
                    right: 316,
                    bottom: 380,
                    width: 120,
                    height: 156,
                    centerX: 256,
                    centerY: 302
                },
                {
                    left: 158,
                    top: 112,
                    right: 230,
                    bottom: 170,
                    width: 72,
                    height: 58,
                    centerX: 194,
                    centerY: 141
                },
                {
                    left: 282,
                    top: 108,
                    right: 354,
                    bottom: 168,
                    width: 72,
                    height: 60,
                    centerX: 318,
                    centerY: 138
                }
            ];

            try {
                manager.getModelScreenBounds = () => bounds;
                manager._getModelLogicalRect = () => ({
                    left: 0,
                    top: 0,
                    right: 1,
                    bottom: 1,
                    width: 1,
                    height: 1
                });
                manager._getRenderableDrawableScreenRects = () => drawableRects;
                manager._getHeadHitAreaScreenRectInfo = () => coarseHeadHitArea;
                manager._getBodyHitAreaScreenRectInfo = () => coarseBodyHitArea;
                manager._getDisplayInfoPartScreenRectInfo = () => null;

                const headInfo = manager.getHeadScreenRectInfo();
                const bodyInfo = manager.getBodyScreenRectInfo(headInfo);
                return {
                    headSource: headInfo?.source || null,
                    headTop: headInfo?.rect?.top || null,
                    headWidth: headInfo?.rect?.width || null,
                    bodySource: bodyInfo?.source || null,
                    bodyTop: bodyInfo?.rect?.top || null,
                    bodyWidth: bodyInfo?.rect?.width || null
                };
            } finally {
                manager.getModelScreenBounds = originalGetModelScreenBounds;
                manager._getModelLogicalRect = originalGetModelLogicalRect;
                manager._getRenderableDrawableScreenRects = originalGetRenderableDrawableScreenRects;
                manager._getHeadHitAreaScreenRectInfo = originalGetHeadHitAreaScreenRectInfo;
                manager._getBodyHitAreaScreenRectInfo = originalGetBodyHitAreaScreenRectInfo;
                manager._getDisplayInfoPartScreenRectInfo = originalGetDisplayInfoPartScreenRectInfo;
            }
        }
        """
    )

    assert selected["headSource"] == "drawableHeuristic"
    assert selected["bodySource"] == "drawableHeuristic"
    assert selected["headTop"] >= 92
    assert selected["headWidth"] < 210
    assert selected["bodyTop"] == 224
    assert selected["bodyWidth"] == 120


@pytest.mark.frontend
def test_live2d_manager_ignores_accessory_hitareas_that_only_look_like_heads(mock_page: Page, running_server: str):
    """Regression test for workshop models whose accessory names contain “head” tokens."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.live2dManager && window.live2dManager._getHeadHitAreaLogicalRectInfo)",
        timeout=10000,
    )

    selected = mock_page.evaluate(
        """
        () => {
            const manager = window.live2dManager;
            const originalCurrentModel = manager.currentModel;
            const originalGetDrawableLogicalRect = manager._getDrawableLogicalRect.bind(manager);

            try {
                manager.currentModel = {
                    internalModel: {
                        settings: {
                            hitAreas: [
                                { Id: 'ArtMesh95', Name: '左头饰' },
                                { Id: 'ArtMesh45', Name: '刘海' }
                            ]
                        },
                        hitAreas: {
                            ArtMesh95: { index: 0 },
                            ArtMesh45: { index: 1 },
                            左头饰: { index: 0 },
                            刘海: { index: 1 }
                        },
                        coreModel: {
                            getDrawableIndex(id) {
                                return id === 'ArtMesh95'
                                    ? 0
                                    : id === 'ArtMesh45'
                                        ? 1
                                        : -1;
                            }
                        }
                    }
                };
                manager._getDrawableLogicalRect = (index) => (
                    index === 0
                        ? { left: 0.10, top: 0.04, right: 0.22, bottom: 0.16, width: 0.12, height: 0.12 }
                        : index === 1
                            ? { left: 0.28, top: 0.02, right: 0.56, bottom: 0.18, width: 0.28, height: 0.16 }
                            : null
                );

                const headInfo = manager._getHeadHitAreaLogicalRectInfo();
                return headInfo
                    ? {
                        id: headInfo.id || null,
                        name: headInfo.name || null
                    }
                    : null;
            } finally {
                manager.currentModel = originalCurrentModel;
                manager._getDrawableLogicalRect = originalGetDrawableLogicalRect;
            }
        }
        """
    )

    assert selected is None


@pytest.mark.frontend
def test_live2d_manager_keeps_large_upper_head_cluster_when_body_hint_spans_whole_chibi(mock_page: Page, running_server: str):
    """Regression test for chibi models whose inferred body rect covers almost the whole figure."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.live2dManager && window.live2dManager._normalizeDrawableHeadScreenRect)",
        timeout=10000,
    )

    selected = mock_page.evaluate(
        """
        () => {
            const manager = window.live2dManager;
            const bounds = {
                left: 1226.94,
                top: 883.23,
                right: 1800.88,
                bottom: 1305.59,
                width: 573.94,
                height: 422.36,
                centerX: 1513.91,
                centerY: 1094.41
            };
            const mergedHeadRect = {
                left: 1316.04,
                top: 925.55,
                right: 1585.19,
                bottom: 1095.25,
                width: 269.15,
                height: 169.70,
                centerX: 1450.61,
                centerY: 1010.40
            };
            const oversizedBodyRect = {
                left: 1298.18,
                top: 925.53,
                right: 1743.28,
                bottom: 1267.79,
                width: 445.10,
                height: 342.26,
                centerX: 1520.73,
                centerY: 1096.66
            };

            const normalized = manager._normalizeDrawableHeadScreenRect(
                mergedHeadRect,
                bounds,
                oversizedBodyRect,
                null
            );

            return {
                width: normalized?.width || null,
                height: normalized?.height || null,
                top: normalized?.top || null
            };
        }
        """
    )

    assert selected["width"] >= 250
    assert selected["height"] >= 160
    assert selected["top"] == 925.55


@pytest.mark.frontend
def test_live2d_manager_trims_inferred_body_that_starts_inside_large_chibi_head(mock_page: Page, running_server: str):
    """Regression test so body inference no longer swallows the whole chibi head."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.live2dManager && window.live2dManager._normalizeDrawableBodyScreenRect)",
        timeout=10000,
    )

    selected = mock_page.evaluate(
        """
        () => {
            const manager = window.live2dManager;
            const bounds = {
                left: 1226.94,
                top: 883.23,
                right: 1800.88,
                bottom: 1305.59,
                width: 573.94,
                height: 422.36,
                centerX: 1513.91,
                centerY: 1094.41
            };
            const bodyRect = {
                left: 1298.18,
                top: 925.53,
                right: 1743.28,
                bottom: 1267.79,
                width: 445.10,
                height: 342.26,
                centerX: 1520.73,
                centerY: 1096.66
            };
            const headRect = {
                left: 1316.04,
                top: 925.55,
                right: 1585.19,
                bottom: 1095.25,
                width: 269.15,
                height: 169.70,
                centerX: 1450.61,
                centerY: 1010.40
            };

            const normalized = manager._normalizeDrawableBodyScreenRect(
                bodyRect,
                bounds,
                headRect
            );

            return {
                top: normalized?.top || null,
                height: normalized?.height || null
            };
        }
        """
    )

    assert selected["top"] > 1000
    assert selected["height"] < 270


@pytest.mark.frontend
def test_live2d_manager_uses_compact_bubble_head_proxy_for_large_drawable_chibi_heads(mock_page: Page, running_server: str):
    """Regression test so big-head recognition does not explode the reaction bubble size."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.live2dManager && window.live2dManager._createBubbleDrawableHeadProxyRect)",
        timeout=10000,
    )

    selected = mock_page.evaluate(
        """
        () => {
            const manager = window.live2dManager;
            const bounds = {
                left: 1226.94,
                top: 883.23,
                right: 1800.88,
                bottom: 1305.59,
                width: 573.94,
                height: 422.36,
                centerX: 1513.91,
                centerY: 1094.41
            };
            const largeHeadRect = {
                left: 1316.04,
                top: 925.55,
                right: 1585.19,
                bottom: 1095.25,
                width: 269.15,
                height: 169.70,
                centerX: 1450.61,
                centerY: 1010.40
            };
            const trimmedBodyRect = {
                left: 1298.18,
                top: 1023.98,
                right: 1743.28,
                bottom: 1267.79,
                width: 445.10,
                height: 243.81,
                centerX: 1520.73,
                centerY: 1145.88
            };

            const shouldProxy = manager._shouldUseBubbleDrawableHeadProxy(
                largeHeadRect,
                bounds,
                trimmedBodyRect,
                'drawableHeuristic'
            );
            const bubbleHeadRect = manager._createBubbleDrawableHeadProxyRect(
                largeHeadRect,
                bounds,
                trimmedBodyRect
            );

            return {
                shouldProxy,
                width: bubbleHeadRect?.width || null,
                height: bubbleHeadRect?.height || null,
                centerX: bubbleHeadRect?.centerX || null
            };
        }
        """
    )

    assert selected["shouldProxy"] is True
    assert 100 <= selected["width"] <= 160
    assert 72 <= selected["height"] <= 110
    assert selected["centerX"] > 1480


@pytest.mark.frontend
def test_live2d_manager_trims_accessory_skewed_chibi_head_with_contributor_core(mock_page: Page, running_server: str):
    """Regression test for q-version heads skewed by top-left accessories and hair fragments."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.live2dManager && window.live2dManager._extractDrawableHeadContributorCoreScreenRect)",
        timeout=10000,
    )

    selected = mock_page.evaluate(
        """
        () => {
            const manager = window.live2dManager;
            const bounds = {
                left: 742.92,
                top: 213.69,
                right: 1806.30,
                bottom: 996.22,
                width: 1063.38,
                height: 782.53,
                centerX: 1274.61,
                centerY: 604.95
            };
            const mergedHeadRect = {
                left: 908.00,
                top: 294.57,
                right: 1404.87,
                bottom: 602.89,
                width: 496.87,
                height: 308.31,
                centerX: 1156.44,
                centerY: 448.73
            };
            const oversizedBodyRect = {
                left: 872.76,
                top: 295.42,
                right: 1699.58,
                bottom: 926.18,
                width: 826.82,
                height: 630.77,
                centerX: 1286.17,
                centerY: 610.80
            };
            const contributors = [
                { score: 10.20, rect: { left: 1070.23, top: 319.41, right: 1378.52, bottom: 469.14, width: 308.29, height: 149.73, centerX: 1224.37, centerY: 394.27 } },
                { score: 10.11, rect: { left: 1125.18, top: 330.00, right: 1338.09, bottom: 433.33, width: 212.91, height: 103.34, centerX: 1231.64, centerY: 381.66 } },
                { score: 9.94, rect: { left: 1232.00, top: 307.02, right: 1404.87, bottom: 602.89, width: 172.87, height: 295.87, centerX: 1318.44, centerY: 454.95 } },
                { score: 9.51, rect: { left: 1033.84, top: 338.50, right: 1094.71, bottom: 390.20, width: 60.87, height: 51.70, centerX: 1064.28, centerY: 364.35 } },
                { score: 9.30, rect: { left: 1071.31, top: 294.88, right: 1127.25, bottom: 362.32, width: 55.95, height: 67.44, centerX: 1099.28, centerY: 328.60 } },
                { score: 9.18, rect: { left: 910.46, top: 310.12, right: 1093.49, bottom: 408.93, width: 183.03, height: 98.81, centerX: 1001.97, centerY: 359.52 } }
            ];

            const normalized = manager._normalizeDrawableHeadScreenRect(
                mergedHeadRect,
                bounds,
                oversizedBodyRect,
                null,
                contributors
            );

            return {
                left: normalized?.left || null,
                top: normalized?.top || null,
                width: normalized?.width || null,
                centerX: normalized?.centerX || null
            };
        }
        """
    )

    assert selected["left"] >= 1050
    assert selected["top"] >= 306
    assert selected["width"] < 380
    assert selected["centerX"] > 1210


@pytest.mark.frontend
def test_live2d_manager_trims_accessory_inflated_chibi_body_with_contributor_core(mock_page: Page, running_server: str):
    """Regression test for q-version bodies stretched by side accessories and phone-like drawables."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.live2dManager && window.live2dManager._extractDrawableBodyContributorCoreScreenRect)",
        timeout=10000,
    )

    selected = mock_page.evaluate(
        """
        () => {
            const manager = window.live2dManager;
            const bounds = {
                left: 742.92,
                top: 213.69,
                right: 1806.30,
                bottom: 996.22,
                width: 1063.38,
                height: 782.53,
                centerX: 1274.61,
                centerY: 604.95
            };
            const wideBodyRect = {
                left: 873.62,
                top: 474.09,
                right: 1699.58,
                bottom: 926.18,
                width: 825.96,
                height: 452.09,
                centerX: 1286.60,
                centerY: 700.14
            };
            const headRect = {
                left: 1058.02,
                top: 306.67,
                right: 1404.94,
                bottom: 603.45,
                width: 346.91,
                height: 296.78,
                centerX: 1231.48,
                centerY: 455.06
            };
            const contributors = [
                { score: 7.76, rect: { left: 1240.77, top: 616.31, right: 1335.36, bottom: 665.99, width: 94.59, height: 49.68, centerX: 1288.07, centerY: 641.15 } },
                { score: 7.58, rect: { left: 1275.28, top: 572.54, right: 1361.75, bottom: 639.46, width: 86.47, height: 66.92, centerX: 1318.51, centerY: 606.00 } },
                { score: 7.43, rect: { left: 1127.16, top: 570.21, right: 1230.06, bottom: 650.85, width: 102.90, height: 80.64, centerX: 1178.61, centerY: 610.53 } },
                { score: 7.36, rect: { left: 1135.26, top: 630.85, right: 1325.59, bottom: 732.81, width: 190.33, height: 101.95, centerX: 1230.42, centerY: 681.83 } },
                { score: 7.24, rect: { left: 1238.40, top: 677.01, right: 1315.11, bottom: 760.62, width: 76.70, height: 83.61, centerX: 1276.75, centerY: 718.82 } },
                { score: 7.23, rect: { left: 1082.14, top: 611.56, right: 1199.81, bottom: 698.74, width: 117.68, height: 87.19, centerX: 1140.97, centerY: 655.15 } },
                { score: 6.79, rect: { left: 908.48, top: 484.06, right: 1450.65, bottom: 926.18, width: 542.17, height: 442.12, centerX: 1179.56, centerY: 705.12 } },
                { score: 5.69, rect: { left: 1412.77, top: 457.38, right: 1699.58, bottom: 545.04, width: 286.81, height: 87.66, centerX: 1556.18, centerY: 501.21 } },
                { score: 6.48, rect: { left: 873.62, top: 583.85, right: 922.87, bottom: 647.94, width: 49.24, height: 64.09, centerX: 898.25, centerY: 615.90 } }
            ];

            const normalized = manager._normalizeDrawableBodyScreenRect(
                wideBodyRect,
                bounds,
                headRect,
                contributors
            );

            return {
                left: normalized?.left || null,
                top: normalized?.top || null,
                right: normalized?.right || null,
                width: normalized?.width || null
            };
        }
        """
    )

    assert selected["left"] >= 900
    assert selected["top"] >= 474
    assert selected["right"] < 1500
    assert selected["width"] < 600


@pytest.mark.frontend
def test_live2d_bubble_geometry_keeps_raw_head_rect_separate_from_bubble_head_rect(mock_page: Page, running_server: str):
    """Regression test that compact bubble geometry no longer overwrites the raw recognized head rect."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.live2dManager && window.live2dManager.getBubbleAnchorGeometryInfo)",
        timeout=10000,
    )

    selected = mock_page.evaluate(
        """
        () => {
            const manager = window.live2dManager;
            const originalGetModelScreenBounds = manager.getModelScreenBounds.bind(manager);
            const originalGetHeadScreenRectInfo = manager.getHeadScreenRectInfo.bind(manager);
            const originalGetBodyScreenRectInfo = manager.getBodyScreenRectInfo.bind(manager);
            const originalGetHeadScreenAnchor = manager.getHeadScreenAnchor.bind(manager);

            const bounds = {
                left: 1226.94,
                top: 883.23,
                right: 1800.88,
                bottom: 1305.59,
                width: 573.94,
                height: 422.36,
                centerX: 1513.91,
                centerY: 1094.41
            };
            const rawHeadRect = {
                left: 1316.04,
                top: 925.55,
                right: 1585.19,
                bottom: 1095.25,
                width: 269.15,
                height: 169.70,
                centerX: 1450.61,
                centerY: 1010.40
            };
            const bodyRect = {
                left: 1298.18,
                top: 1023.98,
                right: 1743.28,
                bottom: 1267.79,
                width: 445.10,
                height: 243.81,
                centerX: 1520.73,
                centerY: 1145.88
            };

            try {
                manager.getModelScreenBounds = () => bounds;
                manager.getHeadScreenRectInfo = () => ({
                    rect: rawHeadRect,
                    mode: 'face',
                    source: 'drawableHeuristic'
                });
                manager.getBodyScreenRectInfo = () => ({
                    rect: bodyRect,
                    mode: 'body',
                    source: 'drawableHeuristic'
                });
                manager.getHeadScreenAnchor = () => ({
                    x: rawHeadRect.centerX,
                    y: rawHeadRect.top + rawHeadRect.height * 0.42
                });

                const geometryInfo = manager.getBubbleAnchorGeometryInfo();
                return {
                    headWidth: geometryInfo?.headRect?.width || null,
                    bubbleHeadWidth: geometryInfo?.bubbleHeadRect?.width || null,
                    headCenterX: geometryInfo?.headRect?.centerX || null,
                    bubbleHeadCenterX: geometryInfo?.bubbleHeadRect?.centerX || null
                };
            } finally {
                manager.getModelScreenBounds = originalGetModelScreenBounds;
                manager.getHeadScreenRectInfo = originalGetHeadScreenRectInfo;
                manager.getBodyScreenRectInfo = originalGetBodyScreenRectInfo;
                manager.getHeadScreenAnchor = originalGetHeadScreenAnchor;
            }
        }
        """
    )

    assert selected["headWidth"] == 269.15
    assert selected["bubbleHeadWidth"] < selected["headWidth"]
    assert selected["bubbleHeadCenterX"] > selected["headCenterX"]


@pytest.mark.frontend
def test_live2d_bubble_uses_bubble_head_rect_without_overwriting_raw_head_rect(mock_page: Page, running_server: str):
    """Regression test that layout uses the compact bubble head rect while debug/state keep the raw head rect."""
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
                left: 1226.94,
                top: 883.23,
                right: 1800.88,
                bottom: 1305.59,
                width: 573.94,
                height: 422.36,
                centerX: 1513.91,
                centerY: 1094.41
            };
            const rawHeadRect = {
                left: 1316.04,
                top: 925.55,
                right: 1585.19,
                bottom: 1095.25,
                width: 269.15,
                height: 169.70,
                centerX: 1450.61,
                centerY: 1010.40
            };
            const bubbleHeadRect = {
                left: 1444.60,
                top: 942.52,
                right: 1552.67,
                bottom: 1024.44,
                width: 108.07,
                height: 81.92,
                centerX: 1498.64,
                centerY: 983.48
            };
            const bodyRect = {
                left: 1298.18,
                top: 1023.98,
                right: 1743.28,
                bottom: 1267.79,
                width: 445.10,
                height: 243.81,
                centerX: 1520.73,
                centerY: 1145.88
            };

            window.vrmManager = null;
            window.mmdManager = null;
            window.live2dManager = {
                getModelScreenBounds() {
                    return bounds;
                },
                getBubbleAnchorGeometryInfo() {
                    return {
                        bounds,
                        rawHeadAnchor: {
                            x: rawHeadRect.centerX,
                            y: rawHeadRect.top + rawHeadRect.height * 0.42
                        },
                        headAnchor: {
                            x: bubbleHeadRect.centerX,
                            y: bubbleHeadRect.top + bubbleHeadRect.height * 0.42
                        },
                        headRect: rawHeadRect,
                        bubbleHeadRect,
                        headMode: 'face',
                        headSource: 'drawableHeuristic',
                        bodyRect,
                        bodySource: 'drawableHeuristic',
                        reliableHeadRect: true,
                        preciseDisplayInfoRect: false,
                        coarseHitAreaHeadRect: false
                    };
                },
                getBubbleAnchorDebugInfo() {
                    return null;
                }
            };

            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: { turnId: 'turn-live2d-bubble-head-split', timestamp: Date.now() }
            }));

            const bubbleWidth = parseFloat(getComputedStyle(bubble).getPropertyValue('--bubble-width') || '0');
            const snapshot = window.avatarReactionBubble.getState().lastDebugSnapshot;

            return {
                bubbleWidth,
                rawHeadWidth: snapshot?.headRect?.width || null,
                bubbleHeadWidth: snapshot?.bubbleHeadRect?.width || null
            };
        }
        """
    )

    assert metrics["rawHeadWidth"] == pytest.approx(269.15, abs=0.1)
    assert metrics["bubbleHeadWidth"] == pytest.approx(108.07, abs=0.1)
    assert metrics["bubbleWidth"] < 220


@pytest.mark.frontend
def test_live2d_manager_keeps_hitareas_for_non_chibi_models(mock_page: Page, running_server: str):
    """Regression test that ordinary models do not get reclassified into q-version heuristics."""
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
            const originalGetModelLogicalRect = manager._getModelLogicalRect.bind(manager);
            const originalGetRenderableDrawableScreenRects = manager._getRenderableDrawableScreenRects.bind(manager);
            const originalGetHeadHitAreaScreenRectInfo = manager._getHeadHitAreaScreenRectInfo.bind(manager);
            const originalGetBodyHitAreaScreenRectInfo = manager._getBodyHitAreaScreenRectInfo.bind(manager);
            const originalGetDisplayInfoPartScreenRectInfo = manager._getDisplayInfoPartScreenRectInfo.bind(manager);

            const bounds = {
                left: 86,
                top: 44,
                right: 426,
                bottom: 804,
                width: 340,
                height: 760,
                centerX: 256,
                centerY: 424
            };
            const headHitArea = {
                rect: {
                    left: 187,
                    top: 116,
                    right: 325,
                    bottom: 296,
                    width: 138,
                    height: 180,
                    centerX: 256,
                    centerY: 206
                },
                mode: 'face',
                source: 'hitArea',
                hitAreaId: 'Head',
                hitAreaName: 'Head',
                autoNamed: false
            };
            const bodyHitArea = {
                rect: {
                    left: 160,
                    top: 244,
                    right: 352,
                    bottom: 638,
                    width: 192,
                    height: 394,
                    centerX: 256,
                    centerY: 441
                },
                mode: 'body',
                source: 'hitArea',
                hitAreaId: 'Body',
                hitAreaName: 'Body',
                autoNamed: false
            };
            const drawableRects = [
                {
                    left: 198,
                    top: 132,
                    right: 314,
                    bottom: 288,
                    width: 116,
                    height: 156,
                    centerX: 256,
                    centerY: 210
                },
                {
                    left: 168,
                    top: 256,
                    right: 344,
                    bottom: 616,
                    width: 176,
                    height: 360,
                    centerX: 256,
                    centerY: 436
                }
            ];

            try {
                manager.getModelScreenBounds = () => bounds;
                manager._getModelLogicalRect = () => ({
                    left: 0,
                    top: 0,
                    right: 1,
                    bottom: 1,
                    width: 1,
                    height: 1
                });
                manager._getRenderableDrawableScreenRects = () => drawableRects;
                manager._getHeadHitAreaScreenRectInfo = () => headHitArea;
                manager._getBodyHitAreaScreenRectInfo = () => bodyHitArea;
                manager._getDisplayInfoPartScreenRectInfo = () => null;

                const headInfo = manager.getHeadScreenRectInfo();
                const bodyInfo = manager.getBodyScreenRectInfo(headInfo);
                return {
                    headSource: headInfo?.source || null,
                    headTop: headInfo?.rect?.top || null,
                    bodySource: bodyInfo?.source || null,
                    bodyTop: bodyInfo?.rect?.top || null
                };
            } finally {
                manager.getModelScreenBounds = originalGetModelScreenBounds;
                manager._getModelLogicalRect = originalGetModelLogicalRect;
                manager._getRenderableDrawableScreenRects = originalGetRenderableDrawableScreenRects;
                manager._getHeadHitAreaScreenRectInfo = originalGetHeadHitAreaScreenRectInfo;
                manager._getBodyHitAreaScreenRectInfo = originalGetBodyHitAreaScreenRectInfo;
                manager._getDisplayInfoPartScreenRectInfo = originalGetDisplayInfoPartScreenRectInfo;
            }
        }
        """
    )

    assert selected["headSource"] == "hitArea"
    assert selected["bodySource"] == "hitArea"
    assert selected["headTop"] == 116
    assert selected["bodyTop"] == 244


@pytest.mark.frontend
def test_live2d_face_mode_rect_expands_to_full_head(mock_page: Page, running_server: str):
    """Regression test: face-mode head rects expand to include hair/top-of-head for bubble sizing."""
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => !!(window.live2dManager && window.live2dManager._normalizeBubbleHeadRect)",
        timeout=10000,
    )

    result = mock_page.evaluate(
        """
        () => {
            const manager = window.live2dManager;
            const bounds = {
                left: 96, top: 48, right: 416, bottom: 860,
                width: 320, height: 812, centerX: 256, centerY: 454
            };
            const faceRect = {
                left: 210, top: 180, right: 302, bottom: 290,
                width: 92, height: 110, centerX: 256, centerY: 235
            };
            const bodyRect = {
                left: 160, top: 340, right: 352, bottom: 780,
                width: 192, height: 440, centerX: 256, centerY: 560
            };

            const expanded = manager._normalizeBubbleHeadRect(
                faceRect, bounds, bodyRect, 'hitArea', 'face'
            );
            const original = manager._normalizeBubbleHeadRect(
                faceRect, bounds, bodyRect, 'hitArea', null
            );
            return {
                expandedWidth: expanded?.width || 0,
                expandedTop: expanded?.top || 0,
                originalWidth: original?.width || 0,
                originalTop: original?.top || 0,
                expandedHeight: expanded?.height || 0,
                originalHeight: original?.height || 0
            };
        }
        """
    )

    assert result["expandedWidth"] > result["originalWidth"]
    assert result["expandedTop"] < result["originalTop"]
    assert result["expandedHeight"] > result["originalHeight"]
