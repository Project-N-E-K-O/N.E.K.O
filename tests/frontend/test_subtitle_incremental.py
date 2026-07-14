from pathlib import Path

import pytest
from playwright.sync_api import Page


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _add_script_parts(page: Page, relative_dir: str) -> None:
    part_dir = PROJECT_ROOT / "static" / relative_dir
    part_paths = sorted(part_dir.glob("*.js"))
    assert part_paths, f"no JavaScript parts found under {part_dir}"
    for part_path in part_paths:
        page.add_script_tag(path=str(part_path))


def test_subtitle_panel_uses_two_thicker_corner_lines_without_texture():
    css = (PROJECT_ROOT / "static/css/subtitle.css").read_text(encoding="utf-8")

    assert "--subtitle-corner-line" in css
    assert "--subtitle-corner-line: rgba(30, 165, 255, 0.7);" in css
    assert "--subtitle-corner-line: rgba(96, 205, 255, 0.7);" in css
    assert "#subtitle-display::before" in css
    assert "#subtitle-display::after" in css
    assert "#subtitle-display::before {\n    top: 5px;\n    left: 5px;" in css
    assert "#subtitle-display::after {\n    right: 5px;\n    bottom: 5px;" in css
    assert "border-top: 3px solid var(--subtitle-corner-line);" in css
    assert "border-left: 3px solid var(--subtitle-corner-line);" in css
    assert "border-right: 3px solid var(--subtitle-corner-line);" in css
    assert "border-bottom: 3px solid var(--subtitle-corner-line);" in css
    assert "body.subtitle-settings-window-host #subtitle-display::before" in css
    assert "body.subtitle-settings-window-host #subtitle-display::after" in css
    assert "paw_ui.png" not in css
    assert "background-repeat: repeat;" not in css
    assert "mask-image:" not in css
    assert "#subtitle-scroll {\n    position: relative;\n    z-index: 1;" in css


def test_subtitle_danmaku_mode_switch_uses_soft_blue_when_enabled():
    css = (PROJECT_ROOT / "static/css/subtitle.css").read_text(encoding="utf-8")
    index_template = (PROJECT_ROOT / "templates/index.html").read_text(encoding="utf-8")
    subtitle_template = (PROJECT_ROOT / "templates/subtitle.html").read_text(encoding="utf-8")
    settings_template = (PROJECT_ROOT / "static/subtitle-settings.html").read_text(encoding="utf-8")

    assert "subtitle-settings-switch subtitle-danmaku-switch" in index_template
    assert "subtitle-settings-switch subtitle-danmaku-switch" in subtitle_template
    assert "subtitle-settings-switch subtitle-danmaku-switch" in settings_template
    assert ".subtitle-settings-switch.subtitle-danmaku-switch" in css
    assert "width: 42px;" in css
    assert "height: 22px;" in css
    assert "#subtitle-danmaku-mode-btn:checked + .subtitle-settings-track" in css
    assert "background: rgba(59, 130, 246, 0.9);" in css
    assert "#subtitle-danmaku-mode-btn + .subtitle-settings-track::before" in css
    assert "background-image: url('/static/icons/emotion_model_icon.png');" in css
    assert "width: 22px;" in css
    assert "height: 22px;" in css
    assert "#subtitle-danmaku-mode-btn + .subtitle-settings-track::after" in css
    assert "width: 44px;" in css
    assert "height: 44px;" in css
    assert "background-size: 44px 44px;" in css
    assert "#subtitle-danmaku-mode-btn:checked + .subtitle-settings-track::before" in css
    assert "background-image: url('/static/icons/exclamation.png');" in css
    assert "#subtitle-danmaku-mode-btn:checked + .subtitle-settings-track::after" in css
    assert "background-size: 40px 40px;" in css
    assert "transform: translate(20px, -50%);" in css
    assert "--subtitle-danmaku-edge-fade: 18px;" in css
    assert "-webkit-mask: linear-gradient(" in css
    assert "mask: linear-gradient(" in css


def test_web_subtitle_opacity_slider_matches_design_minimum():
    index_template = (PROJECT_ROOT / "templates/index.html").read_text(encoding="utf-8")
    subtitle_template = (PROJECT_ROOT / "templates/subtitle.html").read_text(encoding="utf-8")

    assert 'id="subtitle-opacity-slider" min="0" max="100"' in index_template
    assert 'id="subtitle-opacity-slider" min="0" max="100"' in subtitle_template


def test_web_danmaku_layout_uses_animation_frames_for_visual_tracking():
    script = (PROJECT_ROOT / "static/subtitle/subtitle.js").read_text(encoding="utf-8")
    layout_block = script.split("function attachWebDanmakuModeLayout", 1)[1].split(
        "function applySharedSubtitleSettings", 1
    )[0]

    assert "var WEB_DANMAKU_LAYOUT_POLL_MS" not in script
    assert "window.setTimeout" not in layout_block
    assert "window.requestAnimationFrame(function()" in layout_block
    assert "requestLayoutFrame();" in layout_block
    assert "now - lastStateSyncAt >= WEB_DANMAKU_STATE_SYNC_MS" in layout_block


def test_subtitle_named_color_schemes_use_classic_palette():
    css = (PROJECT_ROOT / "static/css/subtitle.css").read_text(encoding="utf-8")

    for scheme, color, rgb in [
        ("red", "#ff0000", "255, 0, 0"),
        ("orange", "#ff8c00", "255, 140, 0"),
        ("yellow", "#ffd400", "255, 212, 0"),
        ("green", "#00a651", "0, 166, 81"),
        ("blue", "#0066ff", "0, 102, 255"),
        ("indigo", "#4b0082", "75, 0, 130"),
        ("violet", "#8a2be2", "138, 43, 226"),
    ]:
        block = css.split(f'#subtitle-display[data-subtitle-color-scheme="{scheme}"] {{', 1)[1].split("}", 1)[0]
        assert f"--subtitle-panel-text: {color};" in block
        assert f"--subtitle-text-fill: {color};" in block
        assert f"--subtitle-placeholder-fill: rgba({rgb}, 0.62);" in block
        assert f"--subtitle-corner-line: {color};" in block

    for scheme, color, rgb in [
        ("red", "#ff4d4d", "255, 77, 77"),
        ("orange", "#ffb347", "255, 179, 71"),
        ("yellow", "#ffe066", "255, 224, 102"),
        ("green", "#39d98a", "57, 217, 138"),
        ("blue", "#66a3ff", "102, 163, 255"),
        ("indigo", "#8a7cff", "138, 124, 255"),
        ("violet", "#c084fc", "192, 132, 252"),
    ]:
        selector = (
            f'html[data-theme="dark"] #subtitle-display[data-subtitle-color-scheme="{scheme}"],\n'
            f'html.dark #subtitle-display[data-subtitle-color-scheme="{scheme}"] {{'
        )
        block = css.split(selector, 1)[1].split("}", 1)[0]
        assert f"--subtitle-panel-text: {color};" in block
        assert f"--subtitle-text-fill: {color};" in block
        assert f"--subtitle-placeholder-fill: rgba({rgb}, 0.72);" in block
        assert f"--subtitle-corner-line: {color};" in block


@pytest.mark.frontend
def test_subtitle_danmaku_mode_switch_persists_and_propagates(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <div id="subtitle-settings-panel">
                <label class="subtitle-settings-switch">
                    <input type="checkbox" id="subtitle-danmaku-mode-btn" title="弹幕模式" aria-label="弹幕模式">
                    <span class="subtitle-settings-track" aria-hidden="true"></span>
                </label>
            </div>
        </div>
        """,
        path="/subtitle-danmaku-mode-switch-harness",
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const propagated = [];
            const shared = window.nekoSubtitleShared;
            shared.initSubtitleUI({
                host: 'window',
                propagateSetting: (change) => propagated.push({
                    type: change.type,
                    value: change.value,
                    transient: change.transient === true,
                    stateValue: change.state && change.state.subtitleDanmakuMode,
                }),
            });
            const button = document.getElementById('subtitle-danmaku-mode-btn');
            const before = {
                checked: button.checked,
                disabled: button.disabled,
                setting: shared.getSettings().subtitleDanmakuMode,
                render: shared.getRenderState().subtitleDanmakuMode,
                stored: window.localStorage.getItem('subtitleDanmakuMode'),
            };
            button.checked = true;
            button.dispatchEvent(new Event('change', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterOn = {
                checked: button.checked,
                disabled: button.disabled,
                setting: shared.getSettings().subtitleDanmakuMode,
                render: shared.getRenderState().subtitleDanmakuMode,
                stored: window.localStorage.getItem('subtitleDanmakuMode'),
                propagated: propagated.slice(),
            };
            button.checked = false;
            button.dispatchEvent(new Event('change', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterOff = {
                checked: button.checked,
                setting: shared.getSettings().subtitleDanmakuMode,
                render: shared.getRenderState().subtitleDanmakuMode,
                stored: window.localStorage.getItem('subtitleDanmakuMode'),
                propagated: propagated.slice(),
            };
            return { before, afterOn, afterOff };
        }
        """
    )

    assert result["before"] == {
        "checked": False,
        "disabled": False,
        "setting": False,
        "render": False,
        "stored": None,
    }
    assert result["afterOn"]["checked"] is True
    assert result["afterOn"]["setting"] is True
    assert result["afterOn"]["render"] is True
    assert result["afterOn"]["stored"] == "true"
    assert result["afterOn"]["propagated"] == [
        {"type": "danmakuMode", "value": True, "transient": False, "stateValue": True}
    ]
    assert result["afterOff"]["checked"] is False
    assert result["afterOff"]["setting"] is False
    assert result["afterOff"]["render"] is False
    assert result["afterOff"]["stored"] == "false"
    assert result["afterOff"]["propagated"] == [
        {"type": "danmakuMode", "value": True, "transient": False, "stateValue": True},
        {"type": "danmakuMode", "value": False, "transient": False, "stateValue": False},
    ]


@pytest.mark.frontend
def test_subtitle_window_danmaku_mode_tracks_avatar_head_and_restores(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden">
                <label class="subtitle-settings-switch">
                    <input type="checkbox" id="subtitle-danmaku-mode-btn">
                    <span class="subtitle-settings-track" aria-hidden="true"></span>
                </label>
            </div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge" data-resize-dir="se"></span>
            </div>
        </div>
        """,
        path="/subtitle-window-danmaku-layout-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({ width: 655, height: 109 }));
            window.localStorage.setItem('subtitlePanelLocked', 'false');
            window.localStorage.setItem('subtitleInteractionPassthrough', 'false');
            window.localStorage.setItem('subtitleOpacity', '72');
            window.__subtitleNativeBounds = { x: 100, y: 300, width: 667, height: 121 };
            window.__subtitleSetBoundsCalls = [];
            window.__subtitleSettingsChanges = [];
            window.__subtitleSettingsCloseCount = 0;
            window.__avatarBoundsSubscriptions = [];
            window.__avatarBoundsHandler = null;
            window.nekoSubtitle = {
                enableInteraction: () => {},
                disableInteraction: () => {},
                getCursorPoint: () => Promise.resolve({ screenX: 0, screenY: 0 }),
                getBounds: () => Promise.resolve({ ...window.__subtitleNativeBounds }),
                setBounds: (x, y, w, h) => {
                    const next = { x, y, width: w, height: h };
                    window.__subtitleSetBoundsCalls.push(next);
                    window.__subtitleNativeBounds = next;
                },
                setSize: () => {},
                changeSettings: (change) => window.__subtitleSettingsChanges.push(change),
                updateSettingsWindow: () => {},
                openSettings: () => {},
                closeSettings: () => { window.__subtitleSettingsCloseCount += 1; },
                subscribeAvatarBounds: (active) => window.__avatarBoundsSubscriptions.push(!!active),
                onAvatarBounds: (handler) => {
                    window.__avatarBoundsHandler = handler;
                    return () => { window.__avatarBoundsHandler = null; };
                },
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))
    mock_page.evaluate("() => document.dispatchEvent(new Event('DOMContentLoaded'))")

    result = mock_page.evaluate(
        """
        async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');
            const panel = document.getElementById('subtitle-settings-panel');
            document.getElementById('subtitle-settings-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            shared.updateSettings({ subtitleDanmakuMode: true }, { source: 'test-enable-danmaku' });
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.__avatarBoundsHandler({
                bounds: {
                    left: 800,
                    top: 300,
                    right: 1000,
                    bottom: 700,
                    width: 200,
                    height: 400,
                    centerX: 900,
                    centerY: 500,
                },
                display: {
                    workArea: { x: 0, y: 0, width: 1920, height: 1080 },
                },
            });
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.dispatchEvent(new CustomEvent('neko-subtitle-state-sync', {
                detail: {
                    opacity: 72,
                    bounds: { width: 655, height: 109 },
                    locked: false,
                    interactionPassthrough: false,
                    danmakuMode: true,
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterOn = {
                subscriptions: window.__avatarBoundsSubscriptions.slice(),
                settings: shared.getSettings(),
                nativeBounds: window.__subtitleNativeBounds,
                calls: window.__subtitleSetBoundsCalls.slice(),
                changes: window.__subtitleSettingsChanges.slice(),
                panelState: display.dataset.subtitlePanelState || '',
                backgroundOpacity: display.dataset.subtitleBackgroundOpacity || '',
                panelHidden: panel.classList.contains('hidden'),
                closeCount: window.__subtitleSettingsCloseCount,
            };
            document.getElementById('subtitle-settings-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            shared.updateSettings({ subtitleDanmakuMode: false }, { source: 'test-disable-danmaku' });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterOff = {
                subscriptions: window.__avatarBoundsSubscriptions.slice(),
                settings: shared.getSettings(),
                nativeBounds: window.__subtitleNativeBounds,
                calls: window.__subtitleSetBoundsCalls.slice(),
                changes: window.__subtitleSettingsChanges.slice(),
                panelState: display.dataset.subtitlePanelState || '',
                panelHidden: panel.classList.contains('hidden'),
                closeCount: window.__subtitleSettingsCloseCount,
            };
            return { afterOn, afterOff };
        }
        """
    )

    assert result["afterOn"]["subscriptions"] == [True]
    assert result["afterOn"]["settings"]["subtitleDanmakuMode"] is True
    assert result["afterOn"]["settings"]["subtitlePanelLocked"] is True
    assert result["afterOn"]["settings"]["subtitleInteractionPassthrough"] is True
    assert result["afterOn"]["settings"]["subtitleOpacity"] == 0
    assert result["afterOn"]["settings"]["subtitlePanelBounds"] == {"width": 228, "height": 76}
    assert result["afterOn"]["nativeBounds"] == {"x": 780, "y": 244, "width": 240, "height": 88}
    assert result["afterOn"]["panelState"] == "clean"
    assert result["afterOn"]["backgroundOpacity"] == "0"
    assert result["afterOn"]["panelHidden"] is True
    assert result["afterOn"]["closeCount"] == 1
    assert {"type": "lock", "value": True, "transient": True} in result["afterOn"]["changes"]
    assert {"type": "opacity", "value": 0, "transient": True} in result["afterOn"]["changes"]
    assert {
        "type": "bounds",
        "value": {"width": 228, "height": 76},
        "transient": True,
    } in result["afterOn"]["changes"]

    assert result["afterOff"]["subscriptions"] == [True, False]
    assert result["afterOff"]["settings"]["subtitleDanmakuMode"] is False
    assert result["afterOff"]["settings"]["subtitlePanelLocked"] is False
    assert result["afterOff"]["settings"]["subtitleInteractionPassthrough"] is False
    assert result["afterOff"]["settings"]["subtitleOpacity"] == 72
    assert result["afterOff"]["settings"]["subtitlePanelBounds"] == {"width": 655, "height": 109}
    assert result["afterOff"]["nativeBounds"] == {"x": 100, "y": 300, "width": 667, "height": 121}
    assert result["afterOff"]["panelState"] == "clean"
    assert result["afterOff"]["panelHidden"] is True
    assert result["afterOff"]["closeCount"] == 2
    assert {"type": "lock", "value": False, "transient": True} in result["afterOff"]["changes"]
    assert {"type": "opacity", "value": 72, "transient": True} in result["afterOff"]["changes"]
    assert {
        "type": "bounds",
        "value": {"width": 655, "height": 109},
        "transient": True,
    } in result["afterOff"]["changes"]


@pytest.mark.frontend
def test_subtitle_window_danmaku_mode_restores_delayed_native_bounds(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden">
                <label class="subtitle-settings-switch">
                    <input type="checkbox" id="subtitle-danmaku-mode-btn">
                    <span class="subtitle-settings-track" aria-hidden="true"></span>
                </label>
            </div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge" data-resize-dir="se"></span>
            </div>
        </div>
        """,
        path="/subtitle-window-danmaku-delayed-bounds-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({ width: 600, height: 90 }));
            window.localStorage.setItem('subtitlePanelLocked', 'false');
            window.localStorage.setItem('subtitleInteractionPassthrough', 'false');
            window.localStorage.setItem('subtitleOpacity', '64');
            window.__subtitleNativeBounds = { x: 70, y: 260, width: 612, height: 102 };
            window.__subtitleSetBoundsCalls = [];
            window.__subtitleSettingsChanges = [];
            window.__avatarBoundsSubscriptions = [];
            window.__avatarBoundsHandler = null;
            window.__resolveSubtitleBounds = [];
            window.nekoSubtitle = {
                enableInteraction: () => {},
                disableInteraction: () => {},
                getCursorPoint: () => Promise.resolve({ screenX: 0, screenY: 0 }),
                getBounds: () => {
                    const captured = { ...window.__subtitleNativeBounds };
                    return new Promise((resolve) => {
                        window.__resolveSubtitleBounds.push(() => resolve(captured));
                    });
                },
                setBounds: (x, y, w, h) => {
                    const next = { x, y, width: w, height: h };
                    window.__subtitleSetBoundsCalls.push(next);
                    window.__subtitleNativeBounds = next;
                },
                setSize: () => {},
                changeSettings: (change) => window.__subtitleSettingsChanges.push(change),
                updateSettingsWindow: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                subscribeAvatarBounds: (active) => window.__avatarBoundsSubscriptions.push(!!active),
                onAvatarBounds: (handler) => {
                    window.__avatarBoundsHandler = handler;
                    return () => { window.__avatarBoundsHandler = null; };
                },
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))
    mock_page.evaluate("() => document.dispatchEvent(new Event('DOMContentLoaded'))")

    result = mock_page.evaluate(
        """
        async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
            const shared = window.nekoSubtitleShared;
            shared.updateSettings({ subtitleDanmakuMode: true }, { source: 'test-enable-danmaku' });
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.__avatarBoundsHandler({
                bounds: {
                    left: 400,
                    top: 300,
                    width: 180,
                    height: 360,
                    centerX: 490,
                    centerY: 480,
                },
                display: {
                    workArea: { x: 0, y: 0, width: 1280, height: 800 },
                },
            });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const whileOn = {
                settings: shared.getSettings(),
                nativeBounds: { ...window.__subtitleNativeBounds },
                mask: document.getElementById('subtitle-display').dataset.subtitleDanmakuSwitching || '',
            };
            shared.updateSettings({ subtitleDanmakuMode: false }, { source: 'test-disable-danmaku' });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterOffBeforeBounds = {
                settings: shared.getSettings(),
                nativeBounds: { ...window.__subtitleNativeBounds },
                handlerDetached: window.__avatarBoundsHandler === null,
                mask: document.getElementById('subtitle-display').dataset.subtitleDanmakuSwitching || '',
                maskStyle: (() => {
                    const style = getComputedStyle(document.getElementById('subtitle-display'));
                    return {
                        opacity: style.opacity,
                        visibility: style.visibility,
                        pointerEvents: style.pointerEvents,
                    };
                })(),
            };
            window.__resolveSubtitleBounds.forEach((resolve) => resolve());
            await new Promise((resolve) => setTimeout(resolve, 0));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterNativeBoundsImmediate = {
                settings: shared.getSettings(),
                nativeBounds: { ...window.__subtitleNativeBounds },
                setBoundsCalls: window.__subtitleSetBoundsCalls.slice(),
                subscriptions: window.__avatarBoundsSubscriptions.slice(),
                mask: document.getElementById('subtitle-display').dataset.subtitleDanmakuSwitching || '',
            };
            await new Promise((resolve) => setTimeout(resolve, 170));
            const afterNativeBoundsSettled = {
                settings: shared.getSettings(),
                nativeBounds: { ...window.__subtitleNativeBounds },
                subscriptions: window.__avatarBoundsSubscriptions.slice(),
                mask: document.getElementById('subtitle-display').dataset.subtitleDanmakuSwitching || '',
            };
            return { whileOn, afterOffBeforeBounds, afterNativeBoundsImmediate, afterNativeBoundsSettled };
        }
        """
    )

    assert result["whileOn"]["settings"]["subtitleDanmakuMode"] is True
    assert result["whileOn"]["settings"]["subtitlePanelLocked"] is True
    assert result["whileOn"]["settings"]["subtitleOpacity"] == 0
    assert result["whileOn"]["nativeBounds"] == {"x": 70, "y": 260, "width": 612, "height": 102}
    assert result["whileOn"]["mask"] == "true"

    assert result["afterOffBeforeBounds"]["settings"]["subtitleDanmakuMode"] is False
    assert result["afterOffBeforeBounds"]["settings"]["subtitlePanelLocked"] is False
    assert result["afterOffBeforeBounds"]["settings"]["subtitleOpacity"] == 64
    assert result["afterOffBeforeBounds"]["nativeBounds"] == {"x": 70, "y": 260, "width": 612, "height": 102}
    assert result["afterOffBeforeBounds"]["handlerDetached"] is True
    assert result["afterOffBeforeBounds"]["mask"] == "true"
    assert result["afterOffBeforeBounds"]["maskStyle"] == {
        "opacity": "0",
        "visibility": "hidden",
        "pointerEvents": "none",
    }

    assert result["afterNativeBoundsImmediate"]["settings"]["subtitlePanelBounds"] == {"width": 600, "height": 90}
    assert result["afterNativeBoundsImmediate"]["nativeBounds"] == {"x": 70, "y": 260, "width": 612, "height": 102}
    assert result["afterNativeBoundsImmediate"]["subscriptions"] == [True, False]
    assert result["afterNativeBoundsImmediate"]["mask"] == "true"
    assert result["afterNativeBoundsSettled"]["nativeBounds"] == {"x": 70, "y": 260, "width": 612, "height": 102}
    assert result["afterNativeBoundsSettled"]["subscriptions"] == [True, False]
    assert result["afterNativeBoundsSettled"]["mask"] == ""


@pytest.mark.frontend
def test_subtitle_window_danmaku_mode_does_not_move_when_native_bounds_fail(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true"></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true"></div>
        </div>
        """,
        path="/subtitle-window-danmaku-bounds-fail-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({ width: 600, height: 90 }));
            window.localStorage.setItem('subtitlePanelLocked', 'false');
            window.localStorage.setItem('subtitleInteractionPassthrough', 'false');
            window.localStorage.setItem('subtitleOpacity', '64');
            window.__subtitleNativeBounds = { x: 70, y: 260, width: 612, height: 102 };
            window.__subtitleSetBoundsCalls = [];
            window.__avatarBoundsSubscriptions = [];
            window.__avatarBoundsHandler = null;
            window.nekoSubtitle = {
                enableInteraction: () => {},
                disableInteraction: () => {},
                getCursorPoint: () => Promise.resolve({ screenX: 0, screenY: 0 }),
                getBounds: () => Promise.reject(new Error('bounds unavailable')),
                setBounds: (x, y, w, h) => {
                    const next = { x, y, width: w, height: h };
                    window.__subtitleSetBoundsCalls.push(next);
                    window.__subtitleNativeBounds = next;
                },
                setSize: () => {},
                changeSettings: () => {},
                updateSettingsWindow: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                subscribeAvatarBounds: (active) => window.__avatarBoundsSubscriptions.push(!!active),
                onAvatarBounds: (handler) => {
                    window.__avatarBoundsHandler = handler;
                    return () => { window.__avatarBoundsHandler = null; };
                },
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))
    mock_page.evaluate("() => document.dispatchEvent(new Event('DOMContentLoaded'))")

    result = mock_page.evaluate(
        """
        async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');
            shared.updateSettings({ subtitleDanmakuMode: true }, { source: 'test-enable-danmaku' });
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.__avatarBoundsHandler({
                bounds: { left: 400, top: 300, width: 180, height: 360, centerX: 490, centerY: 480 },
                display: { workArea: { x: 0, y: 0, width: 1280, height: 800 } },
            });
            await new Promise((resolve) => setTimeout(resolve, 170));
            const whileOn = {
                settings: shared.getSettings(),
                nativeBounds: { ...window.__subtitleNativeBounds },
                calls: window.__subtitleSetBoundsCalls.slice(),
                mask: display.dataset.subtitleDanmakuSwitching || '',
            };
            shared.updateSettings({ subtitleDanmakuMode: false }, { source: 'test-disable-danmaku' });
            await new Promise((resolve) => setTimeout(resolve, 170));
            return {
                whileOn,
                afterOff: {
                    settings: shared.getSettings(),
                    nativeBounds: { ...window.__subtitleNativeBounds },
                    calls: window.__subtitleSetBoundsCalls.slice(),
                    subscriptions: window.__avatarBoundsSubscriptions.slice(),
                    handlerDetached: window.__avatarBoundsHandler === null,
                    mask: display.dataset.subtitleDanmakuSwitching || '',
                },
            };
        }
        """
    )

    assert result["whileOn"]["settings"]["subtitleDanmakuMode"] is True
    assert result["whileOn"]["settings"]["subtitlePanelLocked"] is True
    assert result["whileOn"]["settings"]["subtitleOpacity"] == 0
    assert result["whileOn"]["nativeBounds"] == {"x": 70, "y": 260, "width": 612, "height": 102}
    assert result["whileOn"]["calls"] == []
    assert result["whileOn"]["mask"] == ""

    assert result["afterOff"]["settings"]["subtitleDanmakuMode"] is False
    assert result["afterOff"]["settings"]["subtitlePanelLocked"] is False
    assert result["afterOff"]["settings"]["subtitleOpacity"] == 64
    assert result["afterOff"]["nativeBounds"] == {"x": 70, "y": 260, "width": 612, "height": 102}
    assert result["afterOff"]["calls"] == []
    assert result["afterOff"]["subscriptions"] == [True, False]
    assert result["afterOff"]["handlerDetached"] is True
    assert result["afterOff"]["mask"] == ""


@pytest.mark.frontend
def test_web_subtitle_danmaku_mode_tracks_avatar_head_and_restores(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="settings">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="false">
                <button type="button" id="subtitle-settings-btn"></button>
            </div>
            <div id="subtitle-settings-panel">
                <label class="subtitle-settings-switch">
                    <input type="checkbox" id="subtitle-danmaku-mode-btn">
                    <span class="subtitle-settings-track" aria-hidden="true"></span>
                </label>
            </div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge" data-resize-dir="se"></span>
            </div>
        </div>
        """,
        path="/subtitle-web-danmaku-layout-harness",
    )
    mock_page.set_viewport_size({"width": 1280, "height": 720})
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({ width: 655, height: 109 }));
            window.localStorage.setItem('subtitlePanelPosition', JSON.stringify({
                left: 300,
                top: 500,
                coordinateSpace: 'viewport',
            }));
            window.localStorage.setItem('subtitlePanelLocked', 'false');
            window.localStorage.setItem('subtitleInteractionPassthrough', 'false');
            window.localStorage.setItem('subtitleOpacity', '72');
            window.fetch = () => Promise.resolve({
                json: () => Promise.resolve({ success: true, language: 'zh' }),
            });
            window.lanlan_config = { model_type: 'live2d' };
            window.__avatarBoundsCalls = 0;
            window.live2dManager = {
                currentModel: {},
                getModelScreenBounds: () => {
                    window.__avatarBoundsCalls += 1;
                    return {
                        left: 800,
                        top: 300,
                        right: 1000,
                        bottom: 700,
                        width: 200,
                        height: 400,
                        centerX: 900,
                        centerY: 500,
                    };
                },
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))
    mock_page.evaluate("() => document.dispatchEvent(new Event('DOMContentLoaded'))")

    result = mock_page.evaluate(
        """
        async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');
            const panel = document.getElementById('subtitle-settings-panel');
            shared.updateSettings({ subtitleDanmakuMode: true }, { source: 'test-enable-web-danmaku' });
            await new Promise((resolve) => setTimeout(resolve, 150));
            shared.updateSettings({
                subtitlePanelBounds: { width: 655, height: 109 },
                subtitlePanelPosition: {
                    left: 300,
                    top: 500,
                    coordinateSpace: 'viewport',
                },
                subtitlePanelLocked: false,
                subtitleInteractionPassthrough: false,
                subtitleOpacity: 72,
                subtitleDanmakuMode: true,
            }, {
                persist: false,
                source: 'test-settings-refresh',
            });
            await new Promise((resolve) => setTimeout(resolve, 50));
            const afterOn = {
                settings: shared.getSettings(),
                style: {
                    left: display.style.left,
                    top: display.style.top,
                    width: display.style.width,
                    height: display.style.height,
                },
                panelState: display.dataset.subtitlePanelState || '',
                backgroundOpacity: display.dataset.subtitleBackgroundOpacity || '',
                panelHidden: panel.classList.contains('hidden'),
                storage: {
                    bounds: JSON.parse(window.localStorage.getItem('subtitlePanelBounds')),
                    position: JSON.parse(window.localStorage.getItem('subtitlePanelPosition')),
                    locked: window.localStorage.getItem('subtitlePanelLocked'),
                    passthrough: window.localStorage.getItem('subtitleInteractionPassthrough'),
                    opacity: window.localStorage.getItem('subtitleOpacity'),
                    danmaku: window.localStorage.getItem('subtitleDanmakuMode'),
                },
                avatarBoundsCalls: window.__avatarBoundsCalls,
            };
            shared.updateSettings({ subtitleDanmakuMode: false }, { source: 'test-disable-web-danmaku' });
            await new Promise((resolve) => setTimeout(resolve, 50));
            const afterOff = {
                settings: shared.getSettings(),
                style: {
                    left: display.style.left,
                    top: display.style.top,
                    width: display.style.width,
                    height: display.style.height,
                },
                panelState: display.dataset.subtitlePanelState || '',
                panelHidden: panel.classList.contains('hidden'),
                storage: {
                    bounds: JSON.parse(window.localStorage.getItem('subtitlePanelBounds')),
                    position: JSON.parse(window.localStorage.getItem('subtitlePanelPosition')),
                    locked: window.localStorage.getItem('subtitlePanelLocked'),
                    passthrough: window.localStorage.getItem('subtitleInteractionPassthrough'),
                    opacity: window.localStorage.getItem('subtitleOpacity'),
                    danmaku: window.localStorage.getItem('subtitleDanmakuMode'),
                },
            };
            return { afterOn, afterOff };
        }
        """
    )

    assert result["afterOn"]["settings"]["subtitleDanmakuMode"] is True
    assert result["afterOn"]["settings"]["subtitlePanelLocked"] is True
    assert result["afterOn"]["settings"]["subtitleInteractionPassthrough"] is True
    assert result["afterOn"]["settings"]["subtitleOpacity"] == 0
    assert result["afterOn"]["settings"]["subtitlePanelBounds"] == {"width": 228, "height": 76}
    assert result["afterOn"]["settings"]["subtitlePanelPosition"] == {
        "left": 786,
        "top": 250,
        "coordinateSpace": "viewport",
    }
    assert result["afterOn"]["style"] == {
        "left": "786px",
        "top": "250px",
        "width": "228px",
        "height": "76px",
    }
    assert result["afterOn"]["panelState"] == "clean"
    assert result["afterOn"]["backgroundOpacity"] == "0"
    assert result["afterOn"]["panelHidden"] is True
    assert result["afterOn"]["storage"] == {
        "bounds": {"width": 655, "height": 109},
        "position": {"left": 300, "top": 500, "coordinateSpace": "viewport"},
        "locked": "false",
        "passthrough": "false",
        "opacity": "72",
        "danmaku": "true",
    }
    assert result["afterOn"]["avatarBoundsCalls"] >= 1

    assert result["afterOff"]["settings"]["subtitleDanmakuMode"] is False
    assert result["afterOff"]["settings"]["subtitlePanelLocked"] is False
    assert result["afterOff"]["settings"]["subtitleInteractionPassthrough"] is False
    assert result["afterOff"]["settings"]["subtitleOpacity"] == 72
    assert result["afterOff"]["settings"]["subtitlePanelBounds"] == {"width": 655, "height": 109}
    assert result["afterOff"]["settings"]["subtitlePanelPosition"] == {
        "left": 300,
        "top": 500,
        "coordinateSpace": "viewport",
    }
    assert result["afterOff"]["style"] == {
        "left": "300px",
        "top": "500px",
        "width": "655px",
        "height": "109px",
    }
    assert result["afterOff"]["panelState"] == "clean"
    assert result["afterOff"]["panelHidden"] is True
    assert result["afterOff"]["storage"] == {
        "bounds": {"width": 655, "height": 109},
        "position": {"left": 300, "top": 500, "coordinateSpace": "viewport"},
        "locked": "false",
        "passthrough": "false",
        "opacity": "72",
        "danmaku": "false",
    }


@pytest.mark.frontend
def test_web_subtitle_danmaku_mode_tracks_each_frame_without_rerendering_text(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-settings-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden">
                <input type="checkbox" id="subtitle-danmaku-mode-btn">
            </div>
        </div>
        """,
        path="/subtitle-web-danmaku-raf-tracking-harness",
    )
    mock_page.set_viewport_size({"width": 1280, "height": 720})
    mock_page.evaluate(
        """
        () => {
            window.fetch = () => Promise.resolve({
                json: () => Promise.resolve({ success: true, language: 'zh' }),
            });
            window.__rafId = 0;
            window.__rafQueue = [];
            window.requestAnimationFrame = (callback) => {
                const id = ++window.__rafId;
                window.__rafQueue.push({ id, callback });
                return id;
            };
            window.cancelAnimationFrame = (id) => {
                window.__rafQueue = window.__rafQueue.filter((frame) => frame.id !== id);
            };
            window.__runNextRaf = () => {
                const frame = window.__rafQueue.shift();
                if (!frame) {
                    return { ran: false, queued: window.__rafQueue.length };
                }
                frame.callback(window.performance.now());
                return { ran: true, queued: window.__rafQueue.length };
            };
            window.lanlan_config = { model_type: 'live2d' };
            window.__avatarLeft = 780;
            window.__avatarBoundsCalls = 0;
            window.live2dManager = {
                currentModel: {},
                getModelScreenBounds: () => {
                    window.__avatarBoundsCalls += 1;
                    window.__avatarLeft += 20;
                    return {
                        left: window.__avatarLeft,
                        top: 300,
                        right: window.__avatarLeft + 200,
                        bottom: 700,
                        width: 200,
                        height: 400,
                        centerX: window.__avatarLeft + 100,
                        centerY: 500,
                    };
                },
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))
    mock_page.evaluate("() => document.dispatchEvent(new Event('DOMContentLoaded'))")

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const originalRender = shared.renderSubtitleDanmakuText;
            const display = document.getElementById('subtitle-display');
            window.__danmakuRenderCalls = [];
            shared.renderSubtitleDanmakuText = function(refs, text, options) {
                window.__danmakuRenderCalls.push({
                    text,
                    enabled: !!(options && options.enabled),
                });
                return originalRender.apply(this, arguments);
            };

            shared.updateSettings({ subtitleDanmakuMode: true }, {
                source: 'test-enable-web-danmaku',
            });
            window.writeSubtitleText('One, two. Three! Four? Five.');
            const afterWrite = {
                calls: window.__danmakuRenderCalls.length,
                queued: window.__rafQueue.length,
            };
            const firstFrame = window.__runNextRaf();
            const afterFirstFrame = {
                left: Number.parseFloat(display.style.left),
                top: Number.parseFloat(display.style.top),
                calls: window.__danmakuRenderCalls.length,
                queued: window.__rafQueue.length,
                avatarBoundsCalls: window.__avatarBoundsCalls,
            };
            const secondFrame = window.__runNextRaf();
            const afterSecondFrame = {
                left: Number.parseFloat(display.style.left),
                top: Number.parseFloat(display.style.top),
                calls: window.__danmakuRenderCalls.length,
                queued: window.__rafQueue.length,
                avatarBoundsCalls: window.__avatarBoundsCalls,
            };
            return {
                afterWrite,
                firstFrame,
                secondFrame,
                afterFirstFrame,
                afterSecondFrame,
                itemCount: document.querySelectorAll('.subtitle-danmaku-item').length,
            };
        }
        """
    )

    assert result["afterWrite"] == {"calls": 1, "queued": 1}
    assert result["firstFrame"]["ran"] is True
    assert result["secondFrame"]["ran"] is True
    assert result["afterFirstFrame"]["calls"] == 1
    assert result["afterSecondFrame"]["calls"] == 1
    assert result["afterSecondFrame"]["left"] > result["afterFirstFrame"]["left"]
    assert result["afterFirstFrame"]["top"] == result["afterSecondFrame"]["top"]
    assert result["afterSecondFrame"]["queued"] == 1
    assert result["afterSecondFrame"]["avatarBoundsCalls"] == 2
    assert result["itemCount"] == 3


@pytest.mark.frontend
def test_web_subtitle_danmaku_mode_does_not_take_over_electron_pet(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host lanlan-pet-mode",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
        path="/subtitle-web-danmaku-electron-pet-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.__LANLAN_IS_ELECTRON_PET__ = true;
            window.__NEKO_MULTI_WINDOW__ = true;
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({ width: 655, height: 109 }));
            window.localStorage.setItem('subtitlePanelPosition', JSON.stringify({
                left: 300,
                top: 500,
                coordinateSpace: 'viewport',
            }));
            window.localStorage.setItem('subtitlePanelLocked', 'false');
            window.localStorage.setItem('subtitleInteractionPassthrough', 'false');
            window.localStorage.setItem('subtitleOpacity', '72');
            window.fetch = () => Promise.resolve({
                json: () => Promise.resolve({ success: true, language: 'zh' }),
            });
            window.lanlan_config = { model_type: 'live2d' };
            window.__avatarBoundsCalls = 0;
            window.live2dManager = {
                currentModel: {},
                getModelScreenBounds: () => {
                    window.__avatarBoundsCalls += 1;
                    return { left: 800, top: 300, width: 200, height: 400, centerX: 900, centerY: 500 };
                },
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))
    mock_page.evaluate("() => document.dispatchEvent(new Event('DOMContentLoaded'))")

    result = mock_page.evaluate(
        """
        async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');
            shared.updateSettings({ subtitleDanmakuMode: true }, { source: 'test-enable-web-danmaku' });
            await new Promise((resolve) => setTimeout(resolve, 150));
            return {
                settings: shared.getSettings(),
                style: {
                    left: display.style.left,
                    top: display.style.top,
                    width: display.style.width,
                    height: display.style.height,
                },
                avatarBoundsCalls: window.__avatarBoundsCalls,
            };
        }
        """
    )

    assert result["settings"]["subtitleDanmakuMode"] is True
    assert result["settings"]["subtitlePanelLocked"] is False
    assert result["settings"]["subtitleInteractionPassthrough"] is False
    assert result["settings"]["subtitleOpacity"] == 72
    assert result["settings"]["subtitlePanelBounds"] == {"width": 655, "height": 109}
    assert result["settings"]["subtitlePanelPosition"] == {
        "left": 300,
        "top": 500,
        "coordinateSpace": "viewport",
    }
    assert result["style"] == {
        "left": "300px",
        "top": "500px",
        "width": "655px",
        "height": "109px",
    }
    assert result["avatarBoundsCalls"] == 0


def _open_subtitle_harness(
    mock_page: Page,
    body_class: str,
    body_html: str,
    path: str = "/subtitle-harness",
) -> None:
    mock_page.route(
        f"**{path}",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=(
                "<!doctype html><html><head></head>"
                f"<body class=\"{body_class}\">{body_html}</body></html>"
            ),
        ),
    )
    mock_page.goto(f"http://neko.test{path}")


@pytest.mark.frontend
def test_goodbye_temporarily_hides_subtitle_without_disabling_translation(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
        path="/subtitle-goodbye-suppress-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.fetch = () => Promise.resolve({
                json: () => Promise.resolve({ success: true, language: 'zh' }),
            });
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))
    mock_page.evaluate("() => document.dispatchEvent(new Event('DOMContentLoaded'))")

    result = mock_page.evaluate(
        """
        async () => {
            const waitFor = async (predicate, label) => {
                const deadline = Date.now() + 1000;
                while (Date.now() < deadline) {
                    if (predicate()) return;
                    await new Promise((resolve) => setTimeout(resolve, 20));
                }
                throw new Error(`Timed out waiting for ${label}`);
            };
            await waitFor(
                () => window.nekoSubtitleShared
                    && window.subtitleBridge
                    && document.getElementById('subtitle-display')
                    && document.getElementById('subtitle-text'),
                'subtitle setup'
            );
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            window.subtitleBridge.setSubtitleEnabled(true, { source: 'test-enable' });
            window.subtitleBridge.markStructured();
            await waitFor(
                () => shared.getRenderState().visible === true
                    && display.classList.contains('hidden') === false
                    && text.textContent.length > 0,
                'subtitle visible before goodbye'
            );
            const before = {
                enabled: shared.getSettings().subtitleEnabled,
                renderEnabled: shared.getRenderState().subtitleEnabled,
                visible: shared.getRenderState().visible,
                hidden: display.classList.contains('hidden'),
                text: text.textContent,
            };

            window.dispatchEvent(new CustomEvent('live2d-goodbye-click'));
            await waitFor(
                () => shared.getSettings().subtitleEnabled === true
                    && shared.getRenderState().visible === false
                    && display.classList.contains('hidden') === true,
                'subtitle hidden during goodbye'
            );
            const duringGoodbye = {
                enabled: shared.getSettings().subtitleEnabled,
                renderEnabled: shared.getRenderState().subtitleEnabled,
                visible: shared.getRenderState().visible,
                hidden: display.classList.contains('hidden'),
                text: text.textContent,
            };

            window.dispatchEvent(new CustomEvent('live2d-return-click'));
            await waitFor(
                () => shared.getRenderState().visible === true
                    && display.classList.contains('hidden') === false
                    && text.textContent === before.text,
                'subtitle restored after return'
            );
            const afterReturn = {
                enabled: shared.getSettings().subtitleEnabled,
                renderEnabled: shared.getRenderState().subtitleEnabled,
                visible: shared.getRenderState().visible,
                hidden: display.classList.contains('hidden'),
                text: text.textContent,
            };

            window.dispatchEvent(new CustomEvent('live2d-goodbye-click'));
            await waitFor(
                () => shared.getRenderState().visible === false
                    && display.classList.contains('hidden') === true,
                'subtitle hidden before character switch clear'
            );
            window.dispatchEvent(new CustomEvent('neko:goodbye-state-cleared', {
                detail: { reason: 'character-switch' },
            }));
            await waitFor(
                () => shared.getRenderState().visible === true
                    && display.classList.contains('hidden') === false
                    && text.textContent === before.text,
                'subtitle restored after character switch clear'
            );
            const afterCharacterSwitchClear = {
                enabled: shared.getSettings().subtitleEnabled,
                renderEnabled: shared.getRenderState().subtitleEnabled,
                visible: shared.getRenderState().visible,
                hidden: display.classList.contains('hidden'),
                text: text.textContent,
            };
            return { before, duringGoodbye, afterReturn, afterCharacterSwitchClear };
        }
        """
    )

    subtitle_text = result["before"]["text"]
    assert subtitle_text
    assert result["before"] == {
        "enabled": True,
        "renderEnabled": True,
        "visible": True,
        "hidden": False,
        "text": subtitle_text,
    }
    assert result["duringGoodbye"] == {
        "enabled": True,
        "renderEnabled": True,
        "visible": False,
        "hidden": True,
        "text": subtitle_text,
    }
    assert result["afterReturn"] == result["before"]
    assert result["afterCharacterSwitchClear"] == result["before"]


@pytest.mark.frontend
def test_subtitle_danmaku_renderer_groups_every_two_punctuation_marks(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
        path="/subtitle-danmaku-renderer-harness",
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const refs = controller.refs;
            const text = '第一段，第二段，第三段。第四段！第五段？';
            refs.text.textContent = text;
            const segments = shared.renderSubtitleDanmakuText(refs, text, { enabled: true });
            const items = Array.from(document.querySelectorAll('.subtitle-danmaku-item'))
                .map((item) => ({
                    index: Number(item.dataset.subtitleDanmakuIndex),
                    text: item.textContent,
                    lane: item.dataset.subtitleDanmakuLane,
                    itemAnimationName: getComputedStyle(item).animationName,
                    laneAnimationName: getComputedStyle(item.parentElement).animationName,
                    laneDisplay: getComputedStyle(item.parentElement).display,
                    laneGap: getComputedStyle(item.parentElement).gap,
                }))
                .sort((a, b) => a.index - b.index);
            const active = {
                segments,
                items,
                layerExists: !!document.querySelector('.subtitle-danmaku-layer'),
                scrollClass: refs.scroll.classList.contains('subtitle-danmaku-scroll'),
                activeFlag: refs.display.dataset.subtitleDanmakuActive || '',
                count: refs.display.dataset.subtitleDanmakuCount || '',
                textVisibility: getComputedStyle(refs.text).visibility,
            };
            shared.renderSubtitleDanmakuText(refs, text, { enabled: false });
            const cleared = {
                layerExists: !!document.querySelector('.subtitle-danmaku-layer'),
                scrollClass: refs.scroll.classList.contains('subtitle-danmaku-scroll'),
                activeFlag: refs.display.dataset.subtitleDanmakuActive || '',
                count: refs.display.dataset.subtitleDanmakuCount || '',
            };
            return { active, cleared };
        }
        """
    )

    expected_segments = ["第一段，第二段，", "第三段。第四段！", "第五段？"]
    assert result["active"]["segments"] == expected_segments
    assert [item["text"] for item in result["active"]["items"]] == expected_segments
    assert [item["lane"] for item in result["active"]["items"]] == ["0", "1", "0"]
    assert {item["laneAnimationName"] for item in result["active"]["items"]} == {
        "subtitle-danmaku-scroll"
    }
    assert {item["itemAnimationName"] for item in result["active"]["items"]} == {"none"}
    assert {item["laneDisplay"] for item in result["active"]["items"]} == {"flex"}
    assert {item["laneGap"] for item in result["active"]["items"]} == {"42px"}
    assert result["active"]["layerExists"] is True
    assert result["active"]["scrollClass"] is True
    assert result["active"]["activeFlag"] == "true"
    assert result["active"]["count"] == "3"
    assert result["active"]["textVisibility"] == "hidden"
    assert result["cleared"] == {
        "layerExists": False,
        "scrollClass": False,
        "activeFlag": "",
        "count": "",
    }


@pytest.mark.frontend
def test_web_subtitle_write_text_uses_danmaku_renderer_when_mode_enabled(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
        path="/subtitle-web-danmaku-write-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleDanmakuMode', 'true');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        () => {
            window.writeSubtitleText('第一段，第二段，第三段。第四段！第五段？');
            return {
                text: document.getElementById('subtitle-text').textContent,
                active: document.getElementById('subtitle-display').dataset.subtitleDanmakuActive || '',
                items: Array.from(document.querySelectorAll('.subtitle-danmaku-item'))
                    .map((item) => ({
                        index: Number(item.dataset.subtitleDanmakuIndex),
                        text: item.textContent,
                    }))
                    .sort((a, b) => a.index - b.index)
                    .map((item) => item.text),
            };
        }
        """
    )

    assert result["text"] == "第一段，第二段，第三段。第四段！第五段？"
    assert result["active"] == "true"
    assert result["items"] == ["第一段，第二段，", "第三段。第四段！", "第五段？"]


@pytest.mark.frontend
def test_subtitle_background_opacity_tracks_dark_theme(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            document.documentElement.setAttribute('data-theme', 'dark');
            window.localStorage.setItem('subtitleOpacity', '80');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/dark-mode.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            const snapshot = () => {
                const style = getComputedStyle(display);
                const textStyle = getComputedStyle(text);
                return {
                    inlineBackground: display.style.background,
                    cssAlpha: display.style.getPropertyValue('--subtitle-panel-alpha'),
                    softAlpha: display.style.getPropertyValue('--subtitle-panel-soft-alpha'),
                    softMidAlpha: display.style.getPropertyValue('--subtitle-panel-soft-mid-alpha'),
                    softEdgeAlpha: display.style.getPropertyValue('--subtitle-panel-soft-edge-alpha'),
                    backgroundColor: style.backgroundColor,
                    backgroundImage: style.backgroundImage,
                    boxShadow: style.boxShadow,
                    borderRadius: style.borderRadius,
                    color: style.color,
                    textColor: textStyle.color,
                    textStroke: textStyle.webkitTextStrokeColor,
                    textShadow: textStyle.textShadow,
                    opacityDataset: display.dataset.subtitleBackgroundOpacity,
                };
            };
            const dark = snapshot();
            document.documentElement.removeAttribute('data-theme');
            await new Promise((resolve) => setTimeout(resolve, 0));
            const light = snapshot();
            document.documentElement.setAttribute('data-theme', 'dark');
            await new Promise((resolve) => setTimeout(resolve, 0));
            const darkAfterAttributeChange = snapshot();
            const opacityBounds = [];
            for (const value of [0, 50, 100]) {
                shared.updateSettings({ subtitleOpacity: value }, { source: 'phase-7-opacity-bound' });
                await new Promise((resolve) => setTimeout(resolve, 0));
                opacityBounds.push({
                    value,
                    cssAlpha: display.style.getPropertyValue('--subtitle-panel-alpha'),
                    softAlpha: display.style.getPropertyValue('--subtitle-panel-soft-alpha'),
                    softMidAlpha: display.style.getPropertyValue('--subtitle-panel-soft-mid-alpha'),
                    softEdgeAlpha: display.style.getPropertyValue('--subtitle-panel-soft-edge-alpha'),
                    opacityDataset: display.dataset.subtitleBackgroundOpacity,
                    backgroundColor: getComputedStyle(display).backgroundColor,
                    backgroundImage: getComputedStyle(display).backgroundImage,
                    boxShadow: getComputedStyle(display).boxShadow,
                });
            }
            controller.destroy();
            return { dark, light, darkAfterAttributeChange, opacityBounds };
        }
        """
    )

    assert result["dark"]["inlineBackground"] == ""
    assert result["dark"]["cssAlpha"] == "0.8"
    assert result["dark"]["softAlpha"] == "0.8"
    assert result["dark"]["softMidAlpha"] == "0.8"
    assert result["dark"]["softEdgeAlpha"] == "0.8"
    assert result["dark"]["opacityDataset"] == "80"
    assert result["dark"]["backgroundColor"] == "rgba(18, 20, 23, 0.8)"
    assert result["dark"]["backgroundImage"] == "none"
    assert result["dark"]["boxShadow"] == "none"
    assert result["dark"]["borderRadius"] == "16px"
    assert result["dark"]["color"] == "rgb(255, 255, 255)"
    assert result["dark"]["textColor"] == "rgb(255, 255, 255)"
    assert "rgba(0, 0, 0, 0.52)" in result["dark"]["textShadow"]
    assert result["dark"]["textStroke"] == "rgba(0, 0, 0, 0.46)"
    assert result["light"]["inlineBackground"] == ""
    assert result["light"]["backgroundColor"] == "rgba(255, 255, 255, 0.8)"
    assert result["light"]["backgroundImage"] == "none"
    assert result["light"]["color"] == "rgb(8, 10, 13)"
    assert result["light"]["textColor"] == "rgb(5, 7, 10)"
    assert result["light"]["textStroke"] == "rgba(255, 255, 255, 0.78)"
    assert "rgba(255, 255, 255, 0.95)" in result["light"]["textShadow"]
    assert "rgba(255, 255, 255, 0.78)" in result["light"]["textShadow"]
    assert result["darkAfterAttributeChange"]["backgroundColor"] == "rgba(18, 20, 23, 0.8)"
    assert result["darkAfterAttributeChange"]["backgroundImage"] == "none"
    assert [
        {
            "value": row["value"],
            "cssAlpha": row["cssAlpha"],
            "softAlpha": row["softAlpha"],
            "softMidAlpha": row["softMidAlpha"],
            "softEdgeAlpha": row["softEdgeAlpha"],
            "opacityDataset": row["opacityDataset"],
            "backgroundColor": row["backgroundColor"],
            "backgroundImage": row["backgroundImage"],
            "boxShadow": row["boxShadow"],
        }
        for row in result["opacityBounds"]
    ] == [
        {"value": 0, "cssAlpha": "0", "softAlpha": "0", "softMidAlpha": "0", "softEdgeAlpha": "0", "opacityDataset": "0", "backgroundColor": "rgba(18, 20, 23, 0)", "backgroundImage": "none", "boxShadow": "none"},
        {"value": 50, "cssAlpha": "0.5", "softAlpha": "0.5", "softMidAlpha": "0.5", "softEdgeAlpha": "0.5", "opacityDataset": "50", "backgroundColor": "rgba(18, 20, 23, 0.5)", "backgroundImage": "none", "boxShadow": "none"},
        {"value": 100, "cssAlpha": "1", "softAlpha": "1", "softMidAlpha": "1", "softEdgeAlpha": "1", "opacityDataset": "100", "backgroundColor": "rgb(18, 20, 23)", "backgroundImage": "none", "boxShadow": "none"},
    ]


@pytest.mark.frontend
def test_standalone_subtitle_background_uses_stored_dark_theme_on_open(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 600, "height": 200})
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            document.documentElement.classList.add('subtitle-window-host');
            window.localStorage.setItem('neko-dark-mode', 'true');
            window.localStorage.setItem('subtitleOpacity', '80');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/dark-mode.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/theme-manager.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const controller = window.nekoSubtitleShared.initSubtitleUI({ host: 'window' });
            const display = document.getElementById('subtitle-display');
            const displayStyle = getComputedStyle(display);
            const inlineBackground = display.style.background;
            const htmlBackground = getComputedStyle(document.documentElement).backgroundColor;
            const bodyBackground = getComputedStyle(document.body).backgroundColor;
            const theme = document.documentElement.getAttribute('data-theme');
            controller.destroy();
            return {
                background: displayStyle.backgroundColor,
                backgroundImage: displayStyle.backgroundImage,
                boxShadow: displayStyle.boxShadow,
                borderRadius: displayStyle.borderRadius,
                bodyBackground,
                htmlBackground,
                inlineBackground,
                theme,
            };
        }
        """
    )

    assert result["theme"] == "dark"
    assert result["inlineBackground"] == ""
    assert result["background"] == "rgba(18, 20, 23, 0.8)"
    assert result["backgroundImage"] == "none"
    assert result["boxShadow"] == "none"
    assert result["borderRadius"] == "16px"
    assert result["htmlBackground"] == "rgba(0, 0, 0, 0)"
    assert result["bodyBackground"] == "rgba(0, 0, 0, 0)"


@pytest.mark.frontend
def test_subtitle_settings_state_persists_panel_position_and_locked_state(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'pt-BR');
            window.localStorage.setItem('subtitleOpacity', '80');
            window.localStorage.setItem('subtitleDragAnywhere', 'true');
            window.localStorage.setItem('subtitleSize', 'large');
            window.localStorage.setItem('subtitlePanelScale', '133');
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 720,
                height: 96,
            }));
            window.localStorage.setItem('subtitlePanelPosition', JSON.stringify({
                left: 120,
                top: 240,
                coordinateSpace: 'viewport',
            }));
            window.localStorage.setItem('subtitlePanelLocked', 'true');
            window.localStorage.setItem('subtitleInteractionPassthrough', 'true');
            window.localStorage.setItem('subtitleFontSize', '26');
            window.localStorage.setItem('subtitleColorScheme', 'blue');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const before = shared.getSettings();
            const renderBefore = shared.getRenderState();
            const events = [];
            window.addEventListener(shared.SETTINGS_EVENT, (event) => {
                events.push(event.detail);
            });
            const after = shared.updateSettings({
                subtitlePanelPosition: { x: 44, y: 88 },
                subtitlePanelLocked: false,
                subtitleInteractionPassthrough: false,
                subtitleFontSize: 44,
            }, { source: 'phase-2-test' });
            const renderAfter = shared.getRenderState();
            return {
                before,
                renderBefore,
                after,
                renderAfter,
                storedBounds: window.localStorage.getItem('subtitlePanelBounds'),
                storedPosition: window.localStorage.getItem('subtitlePanelPosition'),
                storedLocked: window.localStorage.getItem('subtitlePanelLocked'),
                storedPassthrough: window.localStorage.getItem('subtitleInteractionPassthrough'),
                storedFontSize: window.localStorage.getItem('subtitleFontSize'),
                storedColorScheme: window.localStorage.getItem('subtitleColorScheme'),
                legacyDragAnywhere: window.localStorage.getItem('subtitleDragAnywhere'),
                legacySize: window.localStorage.getItem('subtitleSize'),
                legacyScale: window.localStorage.getItem('subtitlePanelScale'),
                events: events.map((detail) => ({
                    changedKeys: detail.changedKeys,
                    source: detail.source,
                    bounds: detail.state.subtitlePanelBounds,
                    position: detail.state.subtitlePanelPosition,
                    locked: detail.state.subtitlePanelLocked,
                    passthrough: detail.state.subtitleInteractionPassthrough,
                    fontSize: detail.state.subtitleFontSize,
                    colorScheme: detail.state.subtitleColorScheme,
                })),
            };
        }
        """
    )

    assert result["before"]["userLanguage"] == "pt"
    assert result["before"]["subtitlePanelBounds"] == {
        "width": 720,
        "height": 96,
    }
    assert result["before"]["subtitlePanelPosition"] == {
        "left": 120,
        "top": 240,
        "coordinateSpace": "viewport",
    }
    assert result["before"]["subtitlePanelLocked"] is True
    assert result["before"]["subtitleInteractionPassthrough"] is True
    assert result["before"]["subtitleFontSize"] == 26
    assert result["before"]["subtitleColorScheme"] == "blue"
    assert "subtitleDragAnywhere" not in result["before"]
    assert "subtitleSize" not in result["before"]
    assert "subtitlePanelScale" not in result["before"]
    assert result["renderBefore"]["subtitlePanelBounds"] == result["before"]["subtitlePanelBounds"]
    assert result["renderBefore"]["subtitlePanelPosition"] == result["before"]["subtitlePanelPosition"]
    assert result["renderBefore"]["subtitlePanelLocked"] is True
    assert result["renderBefore"]["subtitleInteractionPassthrough"] is True
    assert result["renderBefore"]["subtitleFontSize"] == 26
    assert result["renderBefore"]["subtitleColorScheme"] == "blue"
    assert result["renderBefore"]["subtitlePanelState"] == "clean"
    assert result["after"]["subtitlePanelPosition"] == {
        "left": 44,
        "top": 88,
        "coordinateSpace": "viewport",
    }
    assert result["after"]["subtitlePanelLocked"] is False
    assert result["after"]["subtitleInteractionPassthrough"] is False
    assert result["after"]["subtitleFontSize"] == 44
    assert result["renderAfter"]["subtitlePanelPosition"] == result["after"]["subtitlePanelPosition"]
    assert result["renderAfter"]["subtitlePanelLocked"] is False
    assert result["renderAfter"]["subtitleInteractionPassthrough"] is False
    assert result["renderAfter"]["subtitleFontSize"] == 44
    assert result["storedBounds"] == '{"width":720,"height":96}'
    assert result["storedPosition"] == '{"left":44,"top":88,"coordinateSpace":"viewport"}'
    assert result["storedLocked"] == "false"
    assert result["storedPassthrough"] == "false"
    assert result["storedFontSize"] == "44"
    assert result["storedColorScheme"] == "blue"
    assert result["legacyDragAnywhere"] == "true"
    assert result["legacySize"] == "large"
    assert result["legacyScale"] == "133"
    assert result["events"] == [
        {
            "changedKeys": ["subtitlePanelPosition", "subtitlePanelLocked", "subtitleInteractionPassthrough", "subtitleFontSize"],
            "source": "phase-2-test",
            "bounds": {
                "width": 720,
                "height": 96,
            },
            "position": {
                "left": 44,
                "top": 88,
                "coordinateSpace": "viewport",
            },
            "locked": False,
            "passthrough": False,
            "fontSize": 44,
            "colorScheme": "blue",
        }
    ]


@pytest.mark.frontend
def test_subtitle_shared_does_not_migrate_legacy_passthrough_to_locked(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
        path="/subtitle-legacy-passthrough-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleInteractionPassthrough', 'true');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const settings = shared.getSettings();
            const render = shared.getRenderState();
            return {
                locked: settings.subtitlePanelLocked,
                passthrough: settings.subtitleInteractionPassthrough,
                renderLocked: render.subtitlePanelLocked,
                renderPassthrough: render.subtitleInteractionPassthrough,
                storedLocked: window.localStorage.getItem('subtitlePanelLocked'),
                storedPassthrough: window.localStorage.getItem('subtitleInteractionPassthrough'),
            };
        }
        """
    )

    assert result == {
        "locked": False,
        "passthrough": True,
        "renderLocked": False,
        "renderPassthrough": True,
        "storedLocked": None,
        "storedPassthrough": "true",
    }


@pytest.mark.frontend
def test_subtitle_shared_restores_explicit_passthrough_separately_from_lock(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
        path="/subtitle-explicit-passthrough-restore-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleInteractionPassthrough', 'true');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const initial = shared.getSettings();
            shared.updateSettings({
                subtitlePanelLocked: true,
                subtitleInteractionPassthrough: true,
                subtitleOpacity: 0,
            }, {
                persist: false,
                source: 'test-danmaku-enter',
            });
            const whileDanmaku = shared.getSettings();
            const restored = shared.updateSettings({
                subtitlePanelLocked: initial.subtitlePanelLocked,
                subtitleInteractionPassthrough: initial.subtitleInteractionPassthrough,
                subtitleOpacity: initial.subtitleOpacity,
            }, {
                persist: false,
                source: 'test-danmaku-restore',
            });
            const render = shared.getRenderState();
            return { initial, whileDanmaku, restored, render };
        }
        """
    )

    assert result["initial"]["subtitlePanelLocked"] is False
    assert result["initial"]["subtitleInteractionPassthrough"] is True
    assert result["whileDanmaku"]["subtitlePanelLocked"] is True
    assert result["whileDanmaku"]["subtitleInteractionPassthrough"] is True
    assert result["restored"]["subtitlePanelLocked"] is False
    assert result["restored"]["subtitleInteractionPassthrough"] is True
    assert result["render"]["subtitlePanelLocked"] is False
    assert result["render"]["subtitleInteractionPassthrough"] is True


@pytest.mark.frontend
def test_subtitle_color_scheme_select_persists_and_updates_panel(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="colorScheme">配色</span>
                    <select id="subtitle-color-scheme-select" title="配色">
                        <option value="default" data-subtitle-color-scheme-label="colorSchemeDefault">默认</option>
                        <option value="red" data-subtitle-color-scheme-label="colorSchemeRed">红</option>
                        <option value="orange" data-subtitle-color-scheme-label="colorSchemeOrange">橙</option>
                        <option value="yellow" data-subtitle-color-scheme-label="colorSchemeYellow">黄</option>
                        <option value="green" data-subtitle-color-scheme-label="colorSchemeGreen">绿</option>
                        <option value="blue" data-subtitle-color-scheme-label="colorSchemeBlue">蓝</option>
                        <option value="indigo" data-subtitle-color-scheme-label="colorSchemeIndigo">靛</option>
                        <option value="violet" data-subtitle-color-scheme-label="colorSchemeViolet">紫</option>
                    </select>
                </div>
                <label class="subtitle-settings-switch">
                    <input type="checkbox" id="subtitle-danmaku-mode-btn" title="Danmaku" aria-label="Danmaku">
                    <span class="subtitle-settings-track" aria-hidden="true"></span>
                </label>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            const labels = {
                'subtitle.settings.colorScheme': 'Color',
                'subtitle.settings.colorSchemeDefault': 'Default',
                'subtitle.settings.colorSchemeRed': 'Red',
                'subtitle.settings.colorSchemeOrange': 'Orange',
                'subtitle.settings.colorSchemeYellow': 'Yellow',
                'subtitle.settings.colorSchemeGreen': 'Green',
                'subtitle.settings.colorSchemeBlue': 'Blue',
                'subtitle.settings.colorSchemeIndigo': 'Indigo',
                'subtitle.settings.colorSchemeViolet': 'Violet',
                'subtitle.settings.danmakuMode': 'Danmaku',
            };
            window.t = (key) => labels[key] || key;
            window.localStorage.setItem('subtitleColorScheme', 'green');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const propagated = [];
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({
                host: 'web',
                propagateSetting: (change) => propagated.push({
                    type: change.type,
                    value: change.value,
                    patch: change.patch,
                }),
            });
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            const select = document.getElementById('subtitle-color-scheme-select');
            const danmakuButton = document.getElementById('subtitle-danmaku-mode-btn');
            const before = {
                setting: shared.getSettings().subtitleColorScheme,
                render: shared.getRenderState().subtitleColorScheme,
                selectValue: select.value,
                dataset: display.dataset.subtitleColorScheme,
                textColor: getComputedStyle(text).color,
                cornerTopColor: getComputedStyle(display, '::before').borderTopColor,
                cornerBottomColor: getComputedStyle(display, '::after').borderBottomColor,
                title: select.title,
                optionLabels: Array.from(select.options).map((option) => option.textContent),
                danmakuDisabled: danmakuButton.disabled,
                danmakuPlaceholder: danmakuButton.dataset.subtitleDanmakuPlaceholder,
                danmakuTitle: danmakuButton.title,
                danmakuAria: danmakuButton.getAttribute('aria-label'),
                danmakuType: danmakuButton.type,
            };
            select.value = 'violet';
            select.dispatchEvent(new Event('change', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const after = {
                setting: shared.getSettings().subtitleColorScheme,
                render: shared.getRenderState().subtitleColorScheme,
                selectValue: select.value,
                dataset: display.dataset.subtitleColorScheme,
                textColor: getComputedStyle(text).color,
                cornerTopColor: getComputedStyle(display, '::before').borderTopColor,
                cornerBottomColor: getComputedStyle(display, '::after').borderBottomColor,
                stored: window.localStorage.getItem('subtitleColorScheme'),
                propagated,
            };
            controller.destroy();
            return { before, after };
        }
        """
    )

    assert result["before"] == {
        "setting": "green",
        "render": "green",
        "selectValue": "green",
        "dataset": "green",
        "textColor": "rgb(0, 166, 81)",
        "cornerTopColor": "rgb(0, 166, 81)",
        "cornerBottomColor": "rgb(0, 166, 81)",
        "title": "Color",
        "optionLabels": ["Default", "Red", "Orange", "Yellow", "Green", "Blue", "Indigo", "Violet"],
        "danmakuDisabled": False,
        "danmakuPlaceholder": None,
        "danmakuTitle": "Danmaku",
        "danmakuAria": "Danmaku",
        "danmakuType": "checkbox",
    }
    assert result["after"] == {
        "setting": "violet",
        "render": "violet",
        "selectValue": "violet",
        "dataset": "violet",
        "textColor": "rgb(138, 43, 226)",
        "cornerTopColor": "rgb(138, 43, 226)",
        "cornerBottomColor": "rgb(138, 43, 226)",
        "stored": "violet",
        "propagated": [
            {
                "type": "colorScheme",
                "value": "violet",
                "patch": {"subtitleColorScheme": "violet"},
            }
        ],
    }


@pytest.mark.frontend
def test_subtitle_color_scheme_storage_event_updates_window_realtime(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
        </div>
        """,
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'window' });
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            const before = {
                colorScheme: shared.getSettings().subtitleColorScheme,
                dataset: display.dataset.subtitleColorScheme,
                textColor: getComputedStyle(text).color,
            };
            window.dispatchEvent(new StorageEvent('storage', {
                key: 'subtitleColorScheme',
                newValue: 'violet',
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const after = {
                colorScheme: shared.getSettings().subtitleColorScheme,
                renderColorScheme: shared.getRenderState().subtitleColorScheme,
                dataset: display.dataset.subtitleColorScheme,
                textColor: getComputedStyle(text).color,
            };
            controller.destroy();
            return { before, after };
        }
        """
    )

    assert result["before"] == {
        "colorScheme": "default",
        "dataset": "default",
        "textColor": "rgb(5, 7, 10)",
    }
    assert result["after"] == {
        "colorScheme": "violet",
        "renderColorScheme": "violet",
        "dataset": "violet",
        "textColor": "rgb(138, 43, 226)",
    }


@pytest.mark.frontend
def test_subtitle_font_size_select_persists_and_updates_panel(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="fontSize"><span class="subtitle-settings-label-text">字体</span></span>
                    <select id="subtitle-font-size-select" title="字体">
                        <option value="16" data-subtitle-font-size-label="fontSizeSmall">小号</option>
                        <option value="21" data-subtitle-font-size-label="fontSizeSmaller">较小</option>
                        <option value="26" data-subtitle-font-size-label="fontSizeDefault" selected>默认</option>
                        <option value="34" data-subtitle-font-size-label="fontSizeLarger">较大</option>
                        <option value="44" data-subtitle-font-size-label="fontSizeLarge">大号</option>
                    </select>
                </div>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            const labels = {
                'subtitle.settings.fontSizeSmall': { [Symbol.toStringTag]: 'Module' },
                'subtitle.settings.fontSizeSmaller': 'Smaller',
                'subtitle.settings.fontSizeDefault': 'Default',
                'subtitle.settings.fontSizeLarger': 'Larger',
                'subtitle.settings.fontSizeLarge': 'Large',
            };
            window.t = (key) => labels[key] || key;
            window.localStorage.setItem('subtitleFontSize', '34');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const propagated = [];
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({
                host: 'web',
                propagateSetting: (change) => propagated.push({
                    type: change.type,
                    value: change.value,
                    patch: change.patch,
                }),
            });
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            const select = document.getElementById('subtitle-font-size-select');
            const before = {
                setting: shared.getSettings().subtitleFontSize,
                render: shared.getRenderState().subtitleFontSize,
                selectValue: select.value,
                optionLabels: Array.from(select.options).map((option) => option.textContent),
                textFontSize: getComputedStyle(text).fontSize,
                dataset: display.dataset.subtitleFontSize,
            };
            select.value = '44';
            select.dispatchEvent(new Event('change', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const after = {
                setting: shared.getSettings().subtitleFontSize,
                render: shared.getRenderState().subtitleFontSize,
                selectValue: select.value,
                textFontSize: getComputedStyle(text).fontSize,
                dataset: display.dataset.subtitleFontSize,
                stored: window.localStorage.getItem('subtitleFontSize'),
                propagated,
            };
            controller.destroy();
            return { before, after };
        }
        """
    )

    assert result["before"] == {
        "setting": 34,
        "render": 34,
        "selectValue": "34",
        "optionLabels": ["小号", "Smaller", "Default", "Larger", "Large"],
        "textFontSize": "34px",
        "dataset": "34",
    }
    assert result["after"] == {
        "setting": 44,
        "render": 44,
        "selectValue": "44",
        "textFontSize": "44px",
        "dataset": "44",
        "stored": "44",
        "propagated": [
            {
                "type": "fontSize",
                "value": 44,
                "patch": {"subtitleFontSize": 44},
            }
        ],
    }


@pytest.mark.frontend
def test_subtitle_font_size_change_reflows_existing_inline_text(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll">
                <span id="subtitle-text" style="font-size: 12px;">Translated text.</span>
            </div>
            <div id="subtitle-settings-panel">
                <select id="subtitle-font-size-select" title="字体">
                    <option value="16" data-subtitle-font-size-label="fontSizeSmall">小号</option>
                    <option value="21" data-subtitle-font-size-label="fontSizeSmaller">较小</option>
                    <option value="26" data-subtitle-font-size-label="fontSizeDefault" selected>默认</option>
                    <option value="34" data-subtitle-font-size-label="fontSizeLarger">较大</option>
                    <option value="44" data-subtitle-font-size-label="fontSizeLarge">大号</option>
                </select>
            </div>
        </div>
        """,
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const text = document.getElementById('subtitle-text');
            const select = document.getElementById('subtitle-font-size-select');
            const before = {
                inline: text.style.fontSize,
                computed: getComputedStyle(text).fontSize,
            };
            select.value = '44';
            select.dispatchEvent(new Event('change', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const after = {
                inline: text.style.fontSize,
                computed: getComputedStyle(text).fontSize,
                setting: window.nekoSubtitleShared.getSettings().subtitleFontSize,
            };
            return { before, after };
        }
        """
    )

    assert result["before"] == {
        "inline": "12px",
        "computed": "12px",
    }
    assert result["after"] == {
        "inline": "",
        "computed": "44px",
        "setting": 44,
    }


@pytest.mark.frontend
def test_subtitle_panel_runtime_state_is_render_only_not_persisted(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitlePanelPosition', '{not-json');
            window.localStorage.setItem('subtitlePanelLocked', 'false');
            window.localStorage.setItem('subtitleInteractionPassthrough', 'false');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const initialSettings = shared.getSettings();
            const initialRender = shared.getRenderState();
            const nextRender = shared.updateRenderState({
                subtitlePanelState: 'settings',
                subtitlePanelPosition: { left: -10, top: 15 },
                subtitlePanelLocked: true,
            }, { source: 'phase-2-render-test' });
            const settingsAfterRenderOnlyUpdate = shared.getSettings();
            shared.updateSettings({ subtitlePanelPosition: { left: 5, top: 6 } }, {
                source: 'phase-2-prime-position',
            });
            shared.updateSettings({ subtitlePanelPosition: null }, { source: 'phase-2-clear-position' });
            return {
                initialSettings,
                initialRender,
                nextRender,
                settingsAfterRenderOnlyUpdate,
                storedPanelState: window.localStorage.getItem('subtitlePanelState'),
                storedPositionAfterClear: window.localStorage.getItem('subtitlePanelPosition'),
                storedLockedAfterRenderOnlyUpdate: window.localStorage.getItem('subtitlePanelLocked'),
            };
        }
        """
    )

    assert result["initialSettings"]["subtitlePanelPosition"] is None
    assert result["initialSettings"]["subtitlePanelLocked"] is False
    assert result["initialRender"]["subtitlePanelState"] == "clean"
    assert result["nextRender"]["subtitlePanelState"] == "settings"
    assert result["nextRender"]["subtitlePanelPosition"] == {
        "left": 0,
        "top": 15,
        "coordinateSpace": "viewport",
    }
    assert result["nextRender"]["subtitlePanelLocked"] is True
    assert result["settingsAfterRenderOnlyUpdate"]["subtitlePanelPosition"] is None
    assert result["settingsAfterRenderOnlyUpdate"]["subtitlePanelLocked"] is False
    assert result["storedPanelState"] is None
    assert result["storedPositionAfterClear"] is None
    assert result["storedLockedAfterRenderOnlyUpdate"] == "false"


@pytest.mark.frontend
def test_subtitle_panel_controls_settings_state_machine(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden">
                <button type="button" id="subtitle-settings-inner">inside</button>
            </div>
        </div>
        """,
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const controls = document.getElementById('subtitle-panel-controls');
            const settingsBtn = document.getElementById('subtitle-settings-btn');
            const settingsPanel = document.getElementById('subtitle-settings-panel');
            const inner = document.getElementById('subtitle-settings-inner');
            const scroll = document.getElementById('subtitle-scroll');
            const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
            const waitForControlsDelay = () => new Promise((resolve) => setTimeout(resolve, 700));
            const snap = () => ({
                dataset: display.dataset.subtitlePanelState,
                render: shared.getRenderState().subtitlePanelState,
                controlsHidden: controls.getAttribute('aria-hidden'),
                settingsHidden: settingsPanel.classList.contains('hidden'),
                settingsExpanded: settingsBtn.getAttribute('aria-expanded'),
            });

            const initial = snap();
            display.dispatchEvent(new Event('pointerenter'));
            await tick();
            const afterPointerEnter = snap();
            display.dispatchEvent(new Event('pointerleave'));
            await waitForControlsDelay();
            const afterPointerLeaveDelay = snap();
            display.click();
            await tick();
            const afterPanelClick = snap();
            settingsBtn.click();
            await tick();
            const afterSettingsOpen = snap();
            display.dispatchEvent(new Event('pointerleave'));
            await waitForControlsDelay();
            const afterPointerLeaveWithSettingsOpen = snap();
            inner.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
            await tick();
            const afterSettingsInnerMouseDown = snap();
            scroll.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
            await tick();
            const afterSettingsOutsideMouseDown = snap();
            settingsBtn.click();
            await tick();
            const afterSettingsReopen = snap();
            display.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
            await tick();
            const afterFirstEscape = snap();
            display.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
            await tick();
            const afterSecondEscape = snap();
            controller.destroy();

            return {
                initial,
                afterPointerEnter,
                afterPointerLeaveDelay,
                afterPanelClick,
                afterSettingsOpen,
                afterPointerLeaveWithSettingsOpen,
                afterSettingsInnerMouseDown,
                afterSettingsOutsideMouseDown,
                afterSettingsReopen,
                afterFirstEscape,
                afterSecondEscape,
            };
        }
        """
    )

    assert result["initial"] == {
        "dataset": "clean",
        "render": "clean",
        "controlsHidden": "true",
        "settingsHidden": True,
        "settingsExpanded": "false",
    }
    assert result["afterPointerEnter"]["dataset"] == "controls"
    assert result["afterPointerEnter"]["controlsHidden"] == "false"
    assert result["afterPointerLeaveDelay"]["dataset"] == "clean"
    assert result["afterPanelClick"]["dataset"] == "controls"
    assert result["afterSettingsOpen"] == {
        "dataset": "settings",
        "render": "settings",
        "controlsHidden": "false",
        "settingsHidden": False,
        "settingsExpanded": "true",
    }
    assert result["afterPointerLeaveWithSettingsOpen"]["dataset"] == "settings"
    assert result["afterPointerLeaveWithSettingsOpen"]["settingsHidden"] is False
    assert result["afterSettingsInnerMouseDown"]["dataset"] == "settings"
    assert result["afterSettingsInnerMouseDown"]["settingsHidden"] is False
    assert result["afterSettingsOutsideMouseDown"]["dataset"] == "clean"
    assert result["afterSettingsOutsideMouseDown"]["settingsHidden"] is True
    assert result["afterSettingsReopen"]["dataset"] == "settings"
    assert result["afterSettingsReopen"]["settingsHidden"] is False
    assert result["afterFirstEscape"]["dataset"] == "controls"
    assert result["afterFirstEscape"]["settingsHidden"] is True
    assert result["afterSecondEscape"]["dataset"] == "clean"


@pytest.mark.frontend
def test_subtitle_panel_controls_hide_after_settings_button_keeps_focus(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const controls = document.getElementById('subtitle-panel-controls');
            const settingsBtn = document.getElementById('subtitle-settings-btn');
            const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
            const waitForControlsDelay = () => new Promise((resolve) => setTimeout(resolve, 700));

            display.dispatchEvent(new Event('pointerenter'));
            await tick();
            settingsBtn.focus();
            settingsBtn.click();
            await tick();
            settingsBtn.focus();
            settingsBtn.click();
            await tick();
            const afterClose = {
                panelState: display.dataset.subtitlePanelState,
                controlsHidden: controls.getAttribute('aria-hidden'),
                activeId: document.activeElement && document.activeElement.id,
            };
            display.dispatchEvent(new Event('pointerleave'));
            await waitForControlsDelay();
            const afterLeaveDelay = {
                panelState: display.dataset.subtitlePanelState,
                controlsHidden: controls.getAttribute('aria-hidden'),
                activeId: document.activeElement && document.activeElement.id,
            };
            controller.destroy();
            return { afterClose, afterLeaveDelay };
        }
        """
    )

    assert result["afterClose"] == {
        "panelState": "controls",
        "controlsHidden": "false",
        "activeId": "",
    }
    assert result["afterLeaveDelay"] == {
        "panelState": "clean",
        "controlsHidden": "true",
        "activeId": "",
    }


@pytest.mark.frontend
def test_subtitle_panel_controls_follow_mousemove_when_pointerenter_is_missed(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean"
             style="position:fixed;left:120px;top:80px;width:320px;height:88px;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'window' });
            const display = document.getElementById('subtitle-display');
            const controls = document.getElementById('subtitle-panel-controls');
            const waitForControlsDelay = () => new Promise((resolve) => setTimeout(resolve, 700));
            const snap = () => ({
                panelState: display.dataset.subtitlePanelState,
                controlsHidden: controls.getAttribute('aria-hidden'),
            });

            const initial = snap();
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: 180,
                clientY: 120,
            }));
            const afterMoveInside = snap();
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: 40,
                clientY: 40,
            }));
            await waitForControlsDelay();
            const afterMoveOutsideDelay = snap();
            controller.destroy();
            return { initial, afterMoveInside, afterMoveOutsideDelay };
        }
        """
    )

    assert result["initial"] == {
        "panelState": "clean",
        "controlsHidden": "true",
    }
    assert result["afterMoveInside"] == {
        "panelState": "controls",
        "controlsHidden": "false",
    }
    assert result["afterMoveOutsideDelay"] == {
        "panelState": "clean",
        "controlsHidden": "true",
    }


@pytest.mark.frontend
def test_web_subtitle_locked_passthrough_includes_text_area(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 800, "height": 500})
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <button id="underlay-target" type="button" style="position:fixed;left:50%;bottom:30px;width:360px;height:80px;transform:translateX(-50%);">under</button>
        <div id="subtitle-display" class="show" data-subtitle-panel-state="clean" style="display:flex;opacity:1;visibility:visible;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 360,
                height: 80,
            }));
            window.localStorage.setItem('subtitlePanelLocked', 'true');
            document.getElementById('subtitle-text').textContent = Array.from(
                { length: 20 },
                (_, index) => `line ${index + 1}`
            ).join('\\n');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    initial = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            window.__subtitleController = shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            const displayRect = display.getBoundingClientRect();
            const textRect = text.getClientRects()[0] || text.getBoundingClientRect();
            const transparentPoint = {
                x: Math.round(displayRect.left + 18),
                y: Math.round(displayRect.top + 18),
            };
            return {
                displayPointerEvents: getComputedStyle(display).pointerEvents,
                textPointerEvents: getComputedStyle(text).pointerEvents,
                lockedDataset: display.dataset.subtitlePanelLocked,
                passthroughDataset: display.dataset.subtitleInteractionPassthrough,
                passthroughToggleExists: !!document.getElementById('subtitle-passthrough-toggle'),
                textPoint: {
                    x: Math.round(textRect.left + textRect.width / 2),
                    y: Math.round(textRect.top + textRect.height / 2),
                },
                transparentPoint,
                transparentHitId: document.elementFromPoint(
                    transparentPoint.x,
                    transparentPoint.y
                ).id,
                textHitId: document.elementFromPoint(
                    Math.round(textRect.left + textRect.width / 2),
                    Math.round(textRect.top + textRect.height / 2)
                ).id,
            };
        }
        """
    )

    mock_page.mouse.move(initial["textPoint"]["x"], initial["textPoint"]["y"])
    mock_page.wait_for_timeout(50)
    after_text_hover = mock_page.evaluate(
        """
        () => ({
            panelState: document.getElementById('subtitle-display').dataset.subtitlePanelState,
            controlsHidden: document.getElementById('subtitle-panel-controls').getAttribute('aria-hidden'),
        })
        """
    )
    mock_page.mouse.move(20, 20)
    mock_page.wait_for_timeout(1300)
    after_leave_delay = mock_page.evaluate(
        """
        (point) => {
            const display = document.getElementById('subtitle-display');
            const controls = document.getElementById('subtitle-panel-controls');
            const hit = document.elementFromPoint(point.x, point.y);
            return {
                panelState: display.dataset.subtitlePanelState,
                controlsHidden: controls.getAttribute('aria-hidden'),
                transparentHitId: hit && hit.id,
            };
        }
        """,
        initial["transparentPoint"],
    )
    after_unlock = mock_page.evaluate(
        """
        (point) => {
            const display = document.getElementById('subtitle-display');
            window.nekoSubtitleShared.updateSettings({
                subtitlePanelLocked: false,
            }, { source: 'test-unlock' });
            const hit = document.elementFromPoint(point.x, point.y);
            const result = {
                displayPointerEvents: getComputedStyle(display).pointerEvents,
                textPointerEvents: getComputedStyle(document.getElementById('subtitle-text')).pointerEvents,
                lockedDataset: display.dataset.subtitlePanelLocked,
                passthroughDataset: display.dataset.subtitleInteractionPassthrough,
                passthroughToggleExists: !!document.getElementById('subtitle-passthrough-toggle'),
                storedLocked: window.localStorage.getItem('subtitlePanelLocked'),
                storedPassthrough: window.localStorage.getItem('subtitleInteractionPassthrough'),
                settingLocked: window.nekoSubtitleShared.getSettings().subtitlePanelLocked,
                settingPassthrough: window.nekoSubtitleShared.getSettings().subtitleInteractionPassthrough,
                transparentHitId: hit && hit.id,
            };
            window.__subtitleController.destroy();
            delete window.__subtitleController;
            return result;
        }
        """,
        initial["transparentPoint"],
    )

    assert initial["displayPointerEvents"] == "none"
    assert initial["textPointerEvents"] == "none"
    assert initial["lockedDataset"] == "true"
    assert initial["passthroughDataset"] == "true"
    assert initial["passthroughToggleExists"] is False
    assert initial["transparentHitId"] == "underlay-target"
    assert initial["textHitId"] == "underlay-target"
    assert after_text_hover == {
        "panelState": "controls",
        "controlsHidden": "false",
    }
    assert after_leave_delay == {
        "panelState": "clean",
        "controlsHidden": "true",
        "transparentHitId": "underlay-target",
    }
    assert after_unlock == {
        "displayPointerEvents": "auto",
        "textPointerEvents": "auto",
        "lockedDataset": "false",
        "passthroughDataset": "false",
        "passthroughToggleExists": False,
        "storedLocked": "false",
        "storedPassthrough": "false",
        "settingLocked": False,
        "settingPassthrough": False,
        "transparentHitId": "subtitle-display",
    }


@pytest.mark.frontend
def test_subtitle_panel_lock_and_close_buttons_update_runtime_state(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('subtitlePanelLocked', 'false');
            window.localStorage.setItem('subtitleInteractionPassthrough', 'false');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const closeCalls = [];
            const propagated = [];
            const controller = shared.initSubtitleUI({
                host: 'web',
                onClose: () => {
                    closeCalls.push('closed');
                    shared.updateSettings({ subtitleEnabled: false }, { source: 'test-close' });
                },
                propagateSetting: (change) => {
                    propagated.push({ type: change.type, value: change.value });
                },
            });
            const display = document.getElementById('subtitle-display');
            const lockBtn = document.getElementById('subtitle-lock-btn');
            const closeBtn = document.getElementById('subtitle-close-btn');
            lockBtn.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterLock = {
                locked: shared.getSettings().subtitlePanelLocked,
                passthrough: shared.getSettings().subtitleInteractionPassthrough,
                storedLocked: window.localStorage.getItem('subtitlePanelLocked'),
                storedPassthrough: window.localStorage.getItem('subtitleInteractionPassthrough'),
                ariaPressed: lockBtn.getAttribute('aria-pressed'),
                iconState: lockBtn.dataset.subtitleLockIcon,
                iconPath: lockBtn.querySelector('path')?.getAttribute('d') || '',
                lockToggleExists: !!document.getElementById('subtitle-lock-toggle'),
                renderLocked: shared.getRenderState().subtitlePanelLocked,
                panelState: display.dataset.subtitlePanelState,
                propagated: propagated.slice(),
            };
            lockBtn.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterUnlock = {
                locked: shared.getSettings().subtitlePanelLocked,
                passthrough: shared.getSettings().subtitleInteractionPassthrough,
                storedLocked: window.localStorage.getItem('subtitlePanelLocked'),
                storedPassthrough: window.localStorage.getItem('subtitleInteractionPassthrough'),
                ariaPressed: lockBtn.getAttribute('aria-pressed'),
                iconState: lockBtn.dataset.subtitleLockIcon,
                iconPath: lockBtn.querySelector('path')?.getAttribute('d') || '',
                lockToggleExists: !!document.getElementById('subtitle-lock-toggle'),
                renderLocked: shared.getRenderState().subtitlePanelLocked,
                propagated: propagated.slice(),
            };
            closeBtn.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterClose = {
                closeCalls: closeCalls.slice(),
                enabled: shared.getSettings().subtitleEnabled,
                storedEnabled: window.localStorage.getItem('subtitleEnabled'),
                panelState: display.dataset.subtitlePanelState,
            };
            controller.destroy();
            return { afterLock, afterUnlock, afterClose };
        }
        """
    )

    assert result["afterLock"] == {
        "locked": True,
        "passthrough": True,
        "storedLocked": "true",
        "storedPassthrough": "true",
        "ariaPressed": "true",
        "iconState": "locked",
        "iconPath": "M7 10V7a5 5 0 0110 0v3h1a1 1 0 011 1v9a1 1 0 01-1 1H6a1 1 0 01-1-1v-9a1 1 0 011-1h1zm2 0h6V7a3 3 0 00-6 0v3z",
        "lockToggleExists": False,
        "renderLocked": True,
        "panelState": "controls",
        "propagated": [{"type": "lock", "value": True}],
    }
    assert result["afterUnlock"] == {
        "locked": False,
        "passthrough": False,
        "storedLocked": "false",
        "storedPassthrough": "false",
        "ariaPressed": "false",
        "iconState": "unlocked",
        "iconPath": "M12 17a2 2 0 100-4 2 2 0 000 4zm6-7h-8V7a3 3 0 015.64-1.44 1 1 0 001.73-1A5 5 0 008 7v3H6a1 1 0 00-1 1v9a1 1 0 001 1h12a1 1 0 001-1v-9a1 1 0 00-1-1z",
        "lockToggleExists": False,
        "renderLocked": False,
        "propagated": [
            {"type": "lock", "value": True},
            {"type": "lock", "value": False},
        ],
    }
    assert result["afterClose"] == {
        "closeCalls": ["closed"],
        "enabled": False,
        "storedEnabled": "false",
        "panelState": "clean",
    }


@pytest.mark.frontend
def test_subtitle_panel_close_fallback_updates_state_before_propagating(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleEnabled', 'true');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const propagated = [];
            const controller = shared.initSubtitleUI({
                host: 'window',
                propagateSetting: (change) => {
                    propagated.push({
                        type: change.type,
                        value: change.value,
                        enabled: change.state.subtitleEnabled,
                    });
                },
            });
            document.getElementById('subtitle-close-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const snapshot = {
                enabled: shared.getSettings().subtitleEnabled,
                storedEnabled: window.localStorage.getItem('subtitleEnabled'),
                panelState: display.dataset.subtitlePanelState,
                isHidden: display.classList.contains('hidden'),
                isShown: display.classList.contains('show'),
                renderVisible: shared.getRenderState().visible,
                renderEnabled: shared.getRenderState().subtitleEnabled,
                propagated,
            };
            controller.destroy();
            return snapshot;
        }
        """
    )

    assert result == {
        "enabled": False,
        "storedEnabled": "false",
        "panelState": "clean",
        "isHidden": True,
        "isShown": False,
        "renderVisible": False,
        "renderEnabled": False,
        "propagated": [{"type": "toggle", "value": False, "enabled": False}],
    }


@pytest.mark.frontend
def test_react_translate_button_tracks_external_subtitle_enabled_changes(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="react-chat-window-overlay" hidden>
            <div id="react-chat-window-shell">
                <div id="react-chat-window-drag-handle"></div>
                <div id="react-chat-window-root"></div>
            </div>
        </div>
        """,
        path="/subtitle-react-toggle-sync-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.appState = { subtitleEnabled: true };
            window.__reactChatPropsHistory = [];
            window.NekoChatWindow = {
                mount: (_root, props) => {
                    window.__reactChatPropsHistory.push({
                        translateEnabled: props.translateEnabled,
                        chatSurfaceMode: props.chatSurfaceMode,
                    });
                    window.__lastReactChatProps = props;
                },
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    _add_script_parts(mock_page, "app/app-react-chat-window")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoSubtitleShared",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        async () => {
            const host = window.reactChatWindowHost;
            const shared = window.nekoSubtitleShared;
            host.openWindow();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterOpen = window.__lastReactChatProps.translateEnabled;

            shared.updateSettings(
                { subtitleEnabled: false },
                { source: 'subtitle-ui-close' },
            );
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterExternalClose = window.__lastReactChatProps.translateEnabled;

            shared.updateSettings(
                { subtitleEnabled: true },
                { persist: false, source: 'subtitle-storage-sync' },
            );
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterExternalOpen = window.__lastReactChatProps.translateEnabled;

            return {
                afterOpen,
                afterExternalClose,
                afterExternalOpen,
                appStateEnabled: window.appState.subtitleEnabled,
                settingsEnabled: shared.getSettings().subtitleEnabled,
                history: window.__reactChatPropsHistory.slice(),
            };
        }
        """
    )

    assert result["afterOpen"] is True
    assert result["afterExternalClose"] is False
    assert result["afterExternalOpen"] is True
    assert result["appStateEnabled"] is True
    assert result["settingsEnabled"] is True
    assert [entry["translateEnabled"] for entry in result["history"]] == [
        True,
        False,
        True,
    ]


@pytest.mark.frontend
def test_react_translate_button_accepts_initial_shared_state_without_changed_keys(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="react-chat-window-overlay" hidden>
            <div id="react-chat-window-shell">
                <div id="react-chat-window-drag-handle"></div>
                <div id="react-chat-window-root"></div>
            </div>
        </div>
        """,
        path="/subtitle-react-initial-empty-changed-keys-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.appState = { subtitleEnabled: false };
            window.__reactChatPropsHistory = [];
            window.NekoChatWindow = {
                mount: (_root, props) => {
                    window.__reactChatPropsHistory.push({
                        translateEnabled: props.translateEnabled,
                    });
                    window.__lastReactChatProps = props;
                },
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    _add_script_parts(mock_page, "app/app-react-chat-window")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoSubtitleShared",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        async () => {
            const host = window.reactChatWindowHost;
            await new Promise((resolve) => setTimeout(resolve, 0));
            host.openWindow();
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                appStateEnabled: window.appState.subtitleEnabled,
                settingsEnabled: window.nekoSubtitleShared.getSettings().subtitleEnabled,
                translateEnabled: window.__lastReactChatProps.translateEnabled,
                history: window.__reactChatPropsHistory.slice(),
            };
        }
        """
    )

    assert result["appStateEnabled"] is False
    assert result["settingsEnabled"] is True
    assert result["translateEnabled"] is True
    assert result["history"] == [{"translateEnabled": True}]


@pytest.mark.frontend
def test_react_translate_button_direct_toggle_controls_subtitle_window(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="react-chat-window-overlay" hidden>
            <div id="react-chat-window-shell">
                <div id="react-chat-window-drag-handle"></div>
                <div id="react-chat-window-root"></div>
            </div>
        </div>
        """,
        path="/subtitle-react-direct-toggle-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleEnabled', 'false');
            window.appState = { subtitleEnabled: false };
            window.__subtitleWindowCalls = [];
            window.__reactChatPropsHistory = [];
            window.__subtitleToggleValues = [true, false];
            window.subtitleBridge = {
                toggle: () => {
                    const next = window.__subtitleToggleValues.shift();
                    window.appState.subtitleEnabled = next;
                    window.localStorage.setItem('subtitleEnabled', String(next));
                    return next;
                },
            };
            window.nekoSubtitleWindow = {
                setEnabled: (enabled) => window.__subtitleWindowCalls.push(`set:${enabled}`),
                show: () => window.__subtitleWindowCalls.push('show'),
                hide: () => window.__subtitleWindowCalls.push('hide'),
            };
            window.NekoChatWindow = {
                mount: (_root, props) => {
                    window.__reactChatPropsHistory.push({
                        translateEnabled: props.translateEnabled,
                    });
                    window.__lastReactChatProps = props;
                },
            };
        }
        """
    )
    _add_script_parts(mock_page, "app/app-react-chat-window")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.__lastReactChatProps === undefined",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        async () => {
            const host = window.reactChatWindowHost;
            host.openWindow();
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.__lastReactChatProps.onTranslateToggle();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterOn = window.__lastReactChatProps.translateEnabled;
            window.__lastReactChatProps.onTranslateToggle();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterOff = window.__lastReactChatProps.translateEnabled;
            return {
                afterOn,
                afterOff,
                calls: window.__subtitleWindowCalls.slice(),
                history: window.__reactChatPropsHistory.slice(),
            };
        }
        """
    )

    assert result["afterOn"] is True
    assert result["afterOff"] is False
    assert result["calls"] == ["set:true", "set:false"]
    assert [entry["translateEnabled"] for entry in result["history"]] == [
        False,
        True,
        False,
    ]


@pytest.mark.frontend
def test_react_translate_button_fallback_uses_current_view_state_after_desktop_sync(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="react-chat-window-overlay" hidden>
            <div id="react-chat-window-shell">
                <div id="react-chat-window-drag-handle"></div>
                <div id="react-chat-window-root"></div>
            </div>
        </div>
        """,
        path="/subtitle-react-desktop-view-props-toggle-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleEnabled', 'false');
            window.appState = { subtitleEnabled: false };
            window.__subtitleWindowCalls = [];
            window.__reactChatPropsHistory = [];
            window.nekoSubtitleWindow = {
                setEnabled: (enabled) => window.__subtitleWindowCalls.push(`set:${enabled}`),
            };
            window.NekoChatWindow = {
                mount: (_root, props) => {
                    window.__reactChatPropsHistory.push({
                        translateEnabled: props.translateEnabled,
                    });
                    window.__lastReactChatProps = props;
                },
            };
        }
        """
    )
    _add_script_parts(mock_page, "app/app-react-chat-window")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.__lastReactChatProps === undefined",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        async () => {
            const host = window.reactChatWindowHost;
            host.openWindow();
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.dispatchEvent(new CustomEvent('react-chat-window:set-view-props', {
                detail: { viewProps: { translateEnabled: true } },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterDesktopSync = window.__lastReactChatProps.translateEnabled;
            window.__lastReactChatProps.onTranslateToggle();
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                afterDesktopSync,
                afterClick: window.__lastReactChatProps.translateEnabled,
                calls: window.__subtitleWindowCalls.slice(),
                appStateEnabled: window.appState.subtitleEnabled,
                storedEnabled: window.localStorage.getItem('subtitleEnabled'),
                history: window.__reactChatPropsHistory.slice(),
            };
        }
        """
    )

    assert result["afterDesktopSync"] is True
    assert result["afterClick"] is False
    assert result["calls"] == ["set:false"]
    assert result["appStateEnabled"] is False
    assert result["storedEnabled"] == "false"
    assert [entry["translateEnabled"] for entry in result["history"]] == [
        False,
        True,
        False,
    ]


@pytest.mark.frontend
def test_subtitle_incremental_translation_starts_when_sentence_punctuation_arrives(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: '你好世界。',
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('Hello world.');
            await new Promise((resolve) => setTimeout(resolve, 450));
            return {
                text: document.getElementById('subtitle-text').textContent,
                requests: window.__translateRequests,
            };
        }
        """
    )

    assert result["text"] == "你好世界。"
    assert [request["text"] for request in result["requests"]] == ["Hello world."]


@pytest.mark.frontend
def test_electron_chat_window_does_not_start_subtitle_translation_requests(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
        path="/chat",
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoChatWindow = {};
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: '你好世界。',
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('Hello world.');
            await window.translateAndShowSubtitle('Hello world.');
            await new Promise((resolve) => setTimeout(resolve, 450));
            return {
                text: document.getElementById('subtitle-text').textContent,
                requests: window.__translateRequests,
            };
        }
        """
    )

    assert result["text"] == ""
    assert result["requests"] == []


@pytest.mark.frontend
def test_subtitle_streaming_does_not_show_original_text_while_translation_is_pending(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__resolveTranslate = null;
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    await new Promise((resolve) => { window.__resolveTranslate = resolve; });
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: '你好世界。',
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('Hello world.');
            await new Promise((resolve) => setTimeout(resolve, 350));
            const beforeResolve = document.getElementById('subtitle-text').textContent;
            window.__resolveTranslate();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === '你好世界。') {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('translated subtitle did not render'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            return {
                beforeResolve,
                afterResolve: document.getElementById('subtitle-text').textContent,
            };
        }
        """
    )

    assert result["beforeResolve"] == ""
    assert result["beforeResolve"] != "Hello world."
    assert result["afterResolve"] == "你好世界。"


@pytest.mark.frontend
def test_subtitle_incremental_translation_does_not_merge_fast_streaming_sentences(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.__translateResolvers = {};
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    await new Promise((resolve) => {
                        window.__translateResolvers[body.text] = resolve;
                    });
                    const translated = body.text === 'First sentence.'
                        ? '第一句。'
                        : '第二句。';
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: translated,
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('First sentence.');
            await new Promise((resolve) => setTimeout(resolve, 350));
            window.updateSubtitleStreamingText('First sentence. Second sentence.');
            await new Promise((resolve) => setTimeout(resolve, 350));
            const requestsBeforeResolve = window.__translateRequests.map((request) => request.text);

            window.__translateResolvers['First sentence.']();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === '第一句。') {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('first translated subtitle did not render'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            const afterFirstResolve = document.getElementById('subtitle-text').textContent;
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (window.__translateRequests.map((request) => request.text).includes('Second sentence.')) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('second sentence translation request did not start'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            const requestsAfterFirstResolve = window.__translateRequests.map((request) => request.text);

            window.__translateResolvers['Second sentence.']();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === '第一句。 第二句。') {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('second translated subtitle did not render'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            return {
                requestsBeforeResolve,
                requestsAfterFirstResolve,
                afterFirstResolve,
                finalText: document.getElementById('subtitle-text').textContent,
            };
        }
        """
    )

    assert result["requestsBeforeResolve"] == ["First sentence."]
    assert result["requestsAfterFirstResolve"] == ["First sentence.", "Second sentence."]
    assert result["afterFirstResolve"] == "第一句。"
    assert result["finalText"] == "第一句。 第二句。"


@pytest.mark.frontend
def test_subtitle_incremental_translation_waits_for_user_language_before_request(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.__resolveLanguage = null;
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    await new Promise((resolve) => { window.__resolveLanguage = resolve; });
                    return new Response(JSON.stringify({ success: true, language: 'en' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: 'Hello world.',
                        source_lang: 'zh',
                        target_lang: body.target_lang || 'en',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.removeItem('userLanguage');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('你好世界。');
            await new Promise((resolve) => setTimeout(resolve, 80));
            const requestsBeforeLanguage = window.__translateRequests.slice();
            window.__resolveLanguage();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (window.__translateRequests.length > 0) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('translation request did not start'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            return {
                requestsBeforeLanguage,
                requests: window.__translateRequests,
                text: document.getElementById('subtitle-text').textContent,
            };
        }
        """
    )

    assert result["requestsBeforeLanguage"] == []
    assert result["requests"][0]["target_lang"] == "en"
    assert result["text"] == "Hello world."


@pytest.mark.frontend
@pytest.mark.parametrize(
    ("configured_language", "expected_target_lang", "original_text"),
    [
        ("es-MX", "es", "Hola mundo."),
        ("pt-BR", "pt", "Ola mundo."),
    ],
)
def test_subtitle_same_language_response_displays_for_spanish_and_portuguese_targets(
    mock_page: Page,
    configured_language: str,
    expected_target_lang: str,
    original_text: str,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        ({ configuredLanguage }) => {
            window.__translateRequests = [];
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: configuredLanguage }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: body.text,
                        source_lang: body.target_lang,
                        target_lang: body.target_lang,
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', configuredLanguage);
        }
        """,
        {"configuredLanguage": configured_language},
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async ({ originalText }) => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText(originalText);
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === originalText) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('same-language subtitle did not render'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            return {
                text: document.getElementById('subtitle-text').textContent,
                requests: window.__translateRequests,
                settingLanguage: window.nekoSubtitleShared.getSettings().userLanguage,
            };
        }
        """,
        {"originalText": original_text},
    )

    assert result["text"] == original_text
    assert result["settingLanguage"] == expected_target_lang
    assert [request["target_lang"] for request in result["requests"]] == [expected_target_lang]


@pytest.mark.frontend
@pytest.mark.parametrize(
    (
        "original_text",
        "source_lang",
        "first_translation",
        "second_translation",
    ),
    [
        (
            "明明没什么本事。你还到处惹麻烦。",
            "zh",
            "明明没什么本事, you still keep acting tough.",
            "You keep causing trouble.",
        ),
        (
            "こんにちは。まだ翻訳されていません。",
            "ja",
            "こんにちは, still not translated.",
            "Still not translated.",
        ),
        (
            "안녕하세요. 아직 번역되지 않았습니다.",
            "ko",
            "안녕하세요, still not translated.",
            "Still not translated.",
        ),
    ],
)
def test_subtitle_skips_translated_sentence_with_unexpected_source_residue(
    mock_page: Page,
    original_text: str,
    source_lang: str,
    first_translation: str,
    second_translation: str,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        ({ sourceLang, firstTranslation, secondTranslation }) => {
            let requestCount = 0;
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'en' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    requestCount += 1;
                    const translated = requestCount === 1
                        ? firstTranslation
                        : secondTranslation;
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: translated,
                        source_lang: sourceLang,
                        target_lang: body.target_lang || 'en',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'en');
        }
        """,
        {
            "sourceLang": source_lang,
            "firstTranslation": first_translation,
            "secondTranslation": second_translation,
        },
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async ({ originalText, expectedText }) => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText(originalText);
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    const text = document.getElementById('subtitle-text').textContent;
                    if (text === expectedText) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1200) {
                        reject(new Error('clean translated subtitle did not render'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            return document.getElementById('subtitle-text').textContent;
        }
        """,
        {
            "originalText": original_text,
            "expectedText": second_translation,
        },
    )

    assert result == second_translation


@pytest.mark.frontend
def test_subtitle_reenable_restarts_current_turn_after_pending_queue_cancelled(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.__translateResolvers = [];
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    await new Promise((resolve) => {
                        window.__translateResolvers.push({ text: body.text, resolve });
                    });
                    const translated = body.text === 'First sentence.'
                        ? '第一句。'
                        : '第二句。';
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: translated,
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('First sentence. Second sentence.');
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (window.__translateRequests.map((request) => request.text).includes('First sentence.')) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('first sentence translation request did not start'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            window.subtitleBridge.setSubtitleEnabled(false);
            window.__translateResolvers[0].resolve();
            await new Promise((resolve) => setTimeout(resolve, 80));
            const requestsWhileDisabled = window.__translateRequests.map((request) => request.text);
            const textAfterDisabledResolve = document.getElementById('subtitle-text').textContent;
            window.translateAndShowSubtitle('First sentence. Second sentence.');
            window.subtitleBridge.setSubtitleEnabled(true);
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (window.__translateRequests.map((request) => request.text).filter((text) => text === 'First sentence.').length === 2) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('first sentence translation did not restart after re-enable'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            window.__translateResolvers[1].resolve();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (window.__translateRequests.map((request) => request.text).includes('Second sentence.')) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('second sentence translation request did not restart'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            window.__translateResolvers[2].resolve();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === '第一句。 第二句。') {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('queued subtitle did not finish after re-enable'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            return {
                requestsWhileDisabled,
                textAfterDisabledResolve,
                finalRequests: window.__translateRequests.map((request) => request.text),
                finalText: document.getElementById('subtitle-text').textContent,
            };
        }
        """
    )

    assert result["requestsWhileDisabled"] == ["First sentence."]
    assert result["textAfterDisabledResolve"] == ""
    assert result["finalRequests"] == ["First sentence.", "First sentence.", "Second sentence."]
    assert result["finalText"] == "第一句。 第二句。"


@pytest.mark.frontend
def test_subtitle_retranslate_invalidates_stale_incremental_response(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.__translateResolvers = [];
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'en' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    await new Promise((resolve) => {
                        window.__translateResolvers.push(resolve);
                    });
                    const translated = body.target_lang === 'ja' ? 'こんにちは。' : 'Hello.';
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: translated,
                        source_lang: 'zh',
                        target_lang: body.target_lang || 'en',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'en');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('你好。');
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (window.__translateRequests.length === 1) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('initial translation request did not start'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            window.subtitleBridge.setUserLanguage('ja');
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (window.__translateRequests.length === 2) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('retranslation request did not start'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            window.__translateResolvers[0]();
            await new Promise((resolve) => setTimeout(resolve, 80));
            const afterStaleResolve = document.getElementById('subtitle-text').textContent;
            window.__translateResolvers[1]();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === 'こんにちは。') {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('retranslated subtitle did not render'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            return {
                requests: window.__translateRequests,
                afterStaleResolve,
                finalText: document.getElementById('subtitle-text').textContent,
            };
        }
        """
    )

    assert [request["target_lang"] for request in result["requests"]] == ["en", "ja"]
    assert result["afterStaleResolve"] == ""
    assert result["afterStaleResolve"] != "Hello."
    assert result["finalText"] == "こんにちは。"


@pytest.mark.frontend
def test_subtitle_structured_mode_invalidates_pending_incremental_response(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__resolveTranslate = null;
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    await new Promise((resolve) => { window.__resolveTranslate = resolve; });
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: '你好世界。',
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('Hello world.');
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (window.__resolveTranslate) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('translation request did not start'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            window.markSubtitleStructured();
            const placeholder = document.getElementById('subtitle-text').textContent;
            window.__resolveTranslate();
            await new Promise((resolve) => setTimeout(resolve, 120));
            return {
                placeholder,
                finalText: document.getElementById('subtitle-text').textContent,
            };
        }
        """
    )

    assert result["placeholder"] == "[markdown]"
    assert result["finalText"] == "[markdown]"


@pytest.mark.frontend
def test_subtitle_turn_end_keeps_pending_incremental_sentence_queue(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.__translateResolvers = {};
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    await new Promise((resolve) => {
                        window.__translateResolvers[body.text] = resolve;
                    });
                    const translated = body.text === 'First sentence.'
                        ? '第一句。'
                        : '第二句。';
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: translated,
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('First sentence.');
            await new Promise((resolve) => setTimeout(resolve, 50));
            window.updateSubtitleStreamingText('First sentence. Second sentence.');
            window.translateAndShowSubtitle('First sentence. Second sentence.');
            await new Promise((resolve) => setTimeout(resolve, 50));
            const requestsAfterTurnEnd = window.__translateRequests.map((request) => request.text);

            window.__translateResolvers['First sentence.']();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === '第一句。') {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('first translated subtitle did not render after turn end'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });

            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (window.__translateRequests.map((request) => request.text).includes('Second sentence.')) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('second sentence translation request did not start after turn end'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            window.__translateResolvers['Second sentence.']();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === '第一句。 第二句。') {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('second translated subtitle did not render after turn end'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });
            return {
                requestsAfterTurnEnd,
                finalRequests: window.__translateRequests.map((request) => request.text),
                finalText: document.getElementById('subtitle-text').textContent,
            };
        }
        """
    )

    assert result["requestsAfterTurnEnd"] == ["First sentence."]
    assert result["finalRequests"] == ["First sentence.", "Second sentence."]
    assert "First sentence. Second sentence." not in result["finalRequests"]
    assert result["finalText"] == "第一句。 第二句。"


@pytest.mark.frontend
def test_subtitle_translation_failure_does_not_fall_back_to_original_and_next_turn_recovers(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    if (window.__translateRequests.length === 1) {
                        return new Response(JSON.stringify({ success: false }), {
                            status: 500,
                            headers: { 'Content-Type': 'application/json' },
                        });
                    }
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: '下一轮恢复。',
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.updateSubtitleStreamingText('Hello world.');
            await new Promise((resolve) => setTimeout(resolve, 450));
            await window.translateAndShowSubtitle('Hello world.');
            const afterFailure = document.getElementById('subtitle-text').textContent;

            window.beginSubtitleTurn();
            window.updateSubtitleStreamingText('Next turn recovers.');
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const poll = () => {
                    if (document.getElementById('subtitle-text').textContent === '下一轮恢复。') {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 1000) {
                        reject(new Error('subtitle did not recover after translation failure'));
                        return;
                    }
                    setTimeout(poll, 20);
                };
                poll();
            });

            return {
                afterFailure,
                finalText: document.getElementById('subtitle-text').textContent,
                requests: window.__translateRequests.map((request) => request.text),
            };
        }
        """
    )

    assert result["afterFailure"] == ""
    assert result["afterFailure"] != "Hello world."
    assert result["finalText"] == "下一轮恢复。"
    assert result["requests"] == ["Hello world.", "Next turn recovers."]


@pytest.mark.frontend
def test_subtitle_toggle_off_hides_panel_and_persists_disabled_state(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text">你好世界。</span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.appState = {};
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        () => {
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            display.classList.remove('hidden');
            display.classList.add('show');
            display.style.opacity = '1';
            text.textContent = '你好世界。';

            window.subtitleBridge.setSubtitleEnabled(false);

            return {
                isHidden: display.classList.contains('hidden'),
                isShown: display.classList.contains('show'),
                opacity: display.style.opacity,
                text: text.textContent,
                storedEnabled: window.localStorage.getItem('subtitleEnabled'),
                appStateEnabled: window.appState.subtitleEnabled,
            };
        }
        """
    )

    assert result == {
        "isHidden": True,
        "isShown": False,
        "opacity": "0",
        "text": "",
        "storedEnabled": "false",
        "appStateEnabled": False,
    }


@pytest.mark.frontend
def test_subtitle_initial_enabled_shows_empty_panel_after_refresh(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text" data-subtitle-placeholder="No translation yet"></span></div>
            <button type="button" id="subtitle-close-btn"></button>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.appState = {};
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'en');
            window.fetch = async () => ({
                json: async () => ({ success: true, language: 'en' }),
            });
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            const renderState = window.nekoSubtitleShared.getRenderState();
            return {
                isHidden: display.classList.contains('hidden'),
                isShown: display.classList.contains('show'),
                opacity: display.style.opacity,
                text: text.textContent,
                storedEnabled: window.localStorage.getItem('subtitleEnabled'),
                renderVisible: renderState.visible,
                renderEnabled: renderState.subtitleEnabled,
            };
        }
        """
    )

    assert result == {
        "isHidden": False,
        "isShown": True,
        "opacity": "1",
        "text": "",
        "storedEnabled": "true",
        "renderVisible": True,
        "renderEnabled": True,
    }


@pytest.mark.frontend
def test_subtitle_empty_turn_does_not_request_translation_or_show_original_text(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="hidden">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__translateRequests = [];
            window.fetch = async (url, options) => {
                const requestUrl = String(url);
                const body = options && options.body ? JSON.parse(options.body) : {};
                if (requestUrl === '/api/config/user_language') {
                    return new Response(JSON.stringify({ success: true, language: 'zh' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (requestUrl === '/api/translate') {
                    window.__translateRequests.push(body);
                    return new Response(JSON.stringify({
                        success: true,
                        translated_text: '不应请求翻译',
                        source_lang: 'en',
                        target_lang: body.target_lang || 'zh',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                throw new Error('Unexpected request: ' + requestUrl);
            };
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('userLanguage', 'zh');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            window.beginSubtitleTurn();
            window.subtitleBridge.setSubtitleEnabled(true);
            window.dispatchEvent(new Event('neko-assistant-turn-start'));
            window.updateSubtitleStreamingText('   ');
            await window.translateAndShowSubtitle('   ');
            await new Promise((resolve) => setTimeout(resolve, 120));
            const display = document.getElementById('subtitle-display');
            return {
                text: document.getElementById('subtitle-text').textContent,
                requests: window.__translateRequests,
                isHidden: display.classList.contains('hidden'),
                isShown: display.classList.contains('show'),
            };
        }
        """
    )

    assert result["text"] == ""
    assert result["requests"] == []
    assert result["isHidden"] is False
    assert result["isShown"] is True


@pytest.mark.frontend
def test_subtitle_window_template_keeps_current_panel_control_scaffold():
    template = (PROJECT_ROOT / "templates" / "subtitle.html").read_text(encoding="utf-8")
    assert 'id="subtitle-display"' in template
    assert 'id="subtitle-scroll"' in template
    assert 'id="subtitle-text"' in template
    assert 'data-subtitle-panel-state="clean"' in template
    assert 'id="subtitle-panel-controls"' in template
    # Phase 3 verifies shared DOM structure only; button behavior is covered by Phase 5 tests.
    assert 'id="subtitle-lock-btn"' in template
    assert 'id="subtitle-settings-btn"' in template
    assert 'id="subtitle-close-btn"' in template
    assert 'fill="white"' not in template
    assert 'stroke="white"' not in template
    assert 'fill="currentColor"' in template
    assert 'stroke="currentColor"' in template
    assert 'id="subtitle-settings-panel"' in template
    assert 'id="subtitle-drag-mode-toggle"' not in template
    assert 'data-subtitle-label="dragAnywhere"' not in template
    assert 'id="subtitle-drag-handle"' not in template
    assert 'id="subtitle-drag-arrows"' not in template
    assert 'data-subtitle-placeholder="暂无翻译内容"' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="targetLang" for="subtitle-lang-select"><span class="subtitle-settings-label-text">语言</span></label>' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="opacity" for="subtitle-opacity-slider"><span class="subtitle-settings-label-text">不透明度</span></label>' in template
    assert 'id="subtitle-opacity-slider" min="0" max="100"' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="fontSize" for="subtitle-font-size-select"><span class="subtitle-settings-label-text">字体</span></label>' in template
    assert 'id="subtitle-font-size-select"' in template
    for size, label_key, fallback in [
        ("16", "fontSizeSmall", "小号"),
        ("21", "fontSizeSmaller", "较小"),
        ("26", "fontSizeDefault", "默认"),
        ("34", "fontSizeLarger", "较大"),
        ("44", "fontSizeLarge", "大号"),
    ]:
        assert f'<option value="{size}"' in template
        assert f'data-subtitle-font-size-label="{label_key}"' in template
        assert f'>{fallback}</option>' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="colorScheme" for="subtitle-color-scheme-select"><span class="subtitle-settings-label-text">配色</span></label>' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="danmakuMode" for="subtitle-danmaku-mode-btn"><span class="subtitle-settings-label-text">弹幕模式</span></label>' in template
    assert 'id="subtitle-color-scheme-select"' in template
    for scheme, label_key, fallback in [
        ("default", "colorSchemeDefault", "默认"),
        ("red", "colorSchemeRed", "红"),
        ("orange", "colorSchemeOrange", "橙"),
        ("yellow", "colorSchemeYellow", "黄"),
        ("green", "colorSchemeGreen", "绿"),
        ("blue", "colorSchemeBlue", "蓝"),
        ("indigo", "colorSchemeIndigo", "靛"),
        ("violet", "colorSchemeViolet", "紫"),
    ]:
        assert f'<option value="{scheme}"' in template
        assert f'data-subtitle-color-scheme-label="{label_key}"' in template
        assert f'>{fallback}</option>' in template
    assert 'id="subtitle-danmaku-mode-btn"' in template
    assert 'type="checkbox" id="subtitle-danmaku-mode-btn"' in template
    assert 'subtitle-settings-switch-placeholder' not in template
    assert 'data-subtitle-danmaku-placeholder="true"' not in template
    assert 'type="checkbox" id="subtitle-danmaku-mode-btn" title="弹幕模式" aria-label="弹幕模式"' in template
    assert 'subtitle-settings-track' in template
    assert 'data-subtitle-label="lockPosition"' not in template
    assert 'id="subtitle-lock-toggle"' not in template
    assert 'data-subtitle-label="passthroughInteraction"' not in template
    assert 'id="subtitle-passthrough-toggle"' not in template
    assert 'id="subtitle-resize-handles"' in template
    for direction in ["n", "e", "s", "w", "ne", "se", "sw", "nw"]:
        assert f'data-resize-dir="{direction}"' in template
    assert 'data-subtitle-label="size"' not in template
    assert 'id="subtitle-size-slider"' not in template
    assert 'id="subtitle-size-value"' not in template
    assert 'subtitle-size-btn' not in template
    assert 'data-size="small"' not in template
    assert template.index('id="subtitle-scroll"') < template.index('id="subtitle-panel-controls"')
    assert template.index('id="subtitle-panel-controls"') < template.index('id="subtitle-settings-panel"')


@pytest.mark.frontend
def test_chat_template_wires_day6_subtitle_controls():
    template = (PROJECT_ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'id="subtitle-display"' in template
    assert 'id="subtitle-scroll"' in template
    assert 'id="subtitle-text"' in template
    assert 'id="subtitle-panel-controls"' in template
    assert 'id="subtitle-lock-btn"' in template
    assert 'id="subtitle-settings-btn"' in template
    assert 'id="subtitle-close-btn"' in template
    assert template.index('id="subtitle-scroll"') < template.index('id="subtitle-panel-controls"')
    assert template.index('id="subtitle-panel-controls"') < template.index('id="subtitle-settings-panel"')
    assert 'id="subtitle-settings-panel"' in template
    assert 'data-subtitle-label="dragAnywhere"' not in template
    assert 'id="subtitle-drag-mode-toggle"' not in template
    assert 'data-subtitle-label="size"' not in template
    assert 'class="subtitle-size-btn"' not in template
    assert 'data-size="small"' not in template
    assert 'data-size="medium"' not in template
    assert 'data-size="large"' not in template
    assert 'id="subtitle-drag-handle"' not in template
    assert 'id="subtitle-drag-arrows"' not in template
    assert 'id="subtitle-passthrough-toggle"' not in template
    assert 'subtitle-resize-edge' not in template


@pytest.mark.frontend
def test_chat_template_keeps_subtitle_as_hidden_bridge_placeholder():
    template = (PROJECT_ROOT / "templates" / "chat.html").read_text(encoding="utf-8")

    assert '<div id="subtitle-display" class="hidden" style="display:none;"><span id="subtitle-text"></span></div>' in template
    assert 'id="subtitle-panel-controls"' not in template
    assert 'id="subtitle-settings-panel"' not in template
    assert 'id="subtitle-resize-handles"' not in template
    assert 'id="subtitle-lock-toggle"' not in template
    assert 'id="subtitle-passthrough-toggle"' not in template


@pytest.mark.frontend
def test_subtitle_shared_drops_legacy_panel_control_branches():
    shared_script = (PROJECT_ROOT / "static" / "subtitle" / "subtitle-shared.js").read_text(encoding="utf-8")

    legacy_tokens = [
        "#subtitle-passthrough-toggle",
        "#subtitle-drag-mode-toggle",
        "#subtitle-drag-handle",
        ".subtitle-size-btn",
        "PANEL_SIZE_PRESETS",
        "getPanelSizePresetName",
        "setPanelSizePreset",
        "passthroughToggle",
        "dragModeToggle",
        "sizeButtons",
        "refs.dragHandle",
        "subtitle-ui-drag-mode",
        "subtitle-ui-size",
    ]
    for token in legacy_tokens:
        assert token not in shared_script
    assert "if (refs.display.classList.contains('hidden')) return;" in shared_script


@pytest.mark.frontend
def test_subtitle_window_settings_hides_passthrough_toggle_and_allows_small_bounds():
    css = (PROJECT_ROOT / "static/css/subtitle.css").read_text(encoding="utf-8")
    assert "body.subtitle-window-host .subtitle-passthrough-setting-row" not in css
    assert "min-width: 200px" not in css
    assert "min-width: 180px" not in css


@pytest.mark.frontend
def test_subtitle_settings_window_includes_color_and_danmaku_switch():
    template = (PROJECT_ROOT / "static" / "subtitle-settings.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "subtitle" / "subtitle-settings-window.js").read_text(encoding="utf-8")

    assert 'id="subtitle-color-scheme-select"' in template
    assert 'data-subtitle-color-scheme-label="colorSchemeDefault"' in template
    assert 'data-subtitle-color-scheme-label="colorSchemeViolet"' in template
    assert 'id="subtitle-danmaku-mode-btn"' in template
    assert 'type="checkbox" id="subtitle-danmaku-mode-btn"' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="targetLang" for="subtitle-lang-select"><span class="subtitle-settings-label-text">语言</span></label>' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="opacity" for="subtitle-opacity-slider"><span class="subtitle-settings-label-text">不透明度</span></label>' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="fontSize" for="subtitle-font-size-select"><span class="subtitle-settings-label-text">字体</span></label>' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="colorScheme" for="subtitle-color-scheme-select"><span class="subtitle-settings-label-text">配色</span></label>' in template
    assert '<label class="subtitle-settings-label" data-subtitle-label="danmakuMode" for="subtitle-danmaku-mode-btn"><span class="subtitle-settings-label-text">弹幕模式</span></label>' in template
    assert "data.type === 'danmakuMode'" in script
    assert 'patch.subtitleDanmakuMode = !!data.value;' in script
    assert script.count("Object.prototype.hasOwnProperty.call(data, 'subtitleFontSize')") == 1
    assert script.count("Object.prototype.hasOwnProperty.call(data, 'subtitleColorScheme')") == 1
    assert 'subtitle-settings-switch-placeholder' not in template
    assert 'data-subtitle-danmaku-placeholder="true"' not in template
    assert 'subtitle-settings-track' in template


@pytest.mark.frontend
def test_subtitle_settings_window_panel_size_matches_added_rows():
    css = (PROJECT_ROOT / "static/css/subtitle.css").read_text(encoding="utf-8")

    assert "body.subtitle-settings-window-host {\n    margin: 0;\n    width: 100vw;\n    min-width: 300px;" in css
    assert "body.subtitle-settings-window-host #subtitle-display" in css
    assert "min-height: 188px" in css


@pytest.mark.frontend
def test_subtitle_window_resize_handles_share_web_offsets():
    css = (PROJECT_ROOT / "static/css/subtitle.css").read_text(encoding="utf-8")

    assert "body.subtitle-window-host .subtitle-resize-n" not in css
    assert "body.subtitle-window-host .subtitle-resize-s" not in css
    assert "body.subtitle-window-host .subtitle-resize-e" not in css
    assert "body.subtitle-window-host .subtitle-resize-w" not in css
    assert ".subtitle-resize-n {\n    top: -4px;" in css
    assert ".subtitle-resize-e {\n    right: -4px;" in css


@pytest.mark.frontend
def test_subtitle_window_resize_method_matches_desktop_chat_handle_bridge():
    script = (PROJECT_ROOT / "static/subtitle/subtitle-window.js").read_text(encoding="utf-8")

    assert "target.closest('[data-resize-dir]')" in script
    assert "refs.display.addEventListener('mousedown', onPointerDown, true)" in script
    assert "document.addEventListener('mousedown', onPointerDown, true)" not in script
    assert "function getResizeDirectionFromPoint" not in script


@pytest.mark.frontend
def test_subtitle_window_handles_stay_inside_native_window_hit_margin(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-n" data-resize-dir="n"></span>
                <span class="subtitle-resize-edge subtitle-resize-e" data-resize-dir="e"></span>
                <span class="subtitle-resize-edge subtitle-resize-s" data-resize-dir="s"></span>
                <span class="subtitle-resize-edge subtitle-resize-w" data-resize-dir="w"></span>
                <span class="subtitle-resize-edge subtitle-resize-se" data-resize-dir="se"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.nekoSubtitle = {
                getBounds: () => Promise.resolve({ x: 10, y: 20, width: 272, height: 80 }),
                getCursorPoint: () => Promise.resolve({ x: 0, y: 0 }),
                setSize: () => {},
                changeSettings: () => {},
                resizeStart: () => {},
                resizeStop: () => {},
                dragStart: () => {},
                dragStop: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display').getBoundingClientRect();
            const handles = Array.from(document.querySelectorAll('.subtitle-resize-edge')).map((handle) => {
                const rect = handle.getBoundingClientRect();
                return {
                    dir: handle.dataset.resizeDir,
                    left: Math.round(rect.left),
                    top: Math.round(rect.top),
                    right: Math.round(rect.right),
                    bottom: Math.round(rect.bottom),
                };
            });
            return {
                display: {
                    left: Math.round(display.left),
                    top: Math.round(display.top),
                    bottom: Math.round(display.bottom),
                    width: Math.round(display.width),
                    height: Math.round(display.height),
                },
                handles,
                viewport: {
                    width: document.documentElement.clientWidth,
                    height: document.documentElement.clientHeight,
                },
            };
        }
        """
    )

    assert result["display"]["left"] == 6
    assert result["display"]["bottom"] == result["viewport"]["height"] - 6
    assert result["display"]["width"] == 260
    assert result["display"]["height"] == 68
    assert all(handle["left"] >= 0 and handle["top"] >= 0 for handle in result["handles"])
    assert all(
        handle["right"] <= result["viewport"]["width"] and
        handle["bottom"] <= result["viewport"]["height"]
        for handle in result["handles"]
    )


@pytest.mark.frontend
def test_subtitle_window_size_bridge_expands_only_native_bounds(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__setSizeCalls = [];
            window.nekoSubtitle = {
                getBounds: () => Promise.resolve({ x: 10, y: 20, width: 272, height: 80 }),
                getCursorPoint: () => Promise.resolve({ x: 0, y: 0 }),
                setSize: (w, h, options) => window.__setSizeCalls.push({
                    width: w,
                    height: h,
                    panelBounds: options && options.panelBounds,
                }),
                changeSettings: () => {},
                resizeStart: () => {},
                resizeStop: () => {},
                dragStart: () => {},
                dragStop: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            return {
                calls: window.__setSizeCalls,
                inlineWidth: display.style.width,
                inlineHeight: display.style.height,
            };
        }
        """
    )

    assert result["inlineWidth"] == "260px"
    assert result["inlineHeight"] == "68px"
    assert result["calls"][-1] == {
        "width": 272,
        "height": 80,
        "panelBounds": {"width": 260, "height": 68},
    }


@pytest.mark.frontend
def test_subtitle_window_fallback_resize_includes_native_edge_insets(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-se" data-resize-dir="se"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__setSizeCalls = [];
            window.__settingsChanges = [];
            window.nekoSubtitle = {
                setSize: (w, h, options) => window.__setSizeCalls.push({
                    width: w,
                    height: h,
                    panelBounds: options && options.panelBounds,
                }),
                changeSettings: (change) => window.__settingsChanges.push(change),
                dragStart: () => {},
                dragStop: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.evaluate(
        """
        () => {
            window.__controller = window.nekoSubtitleShared.initSubtitleUI({
                host: 'window',
                api: window.nekoSubtitle,
                windowEdgeInset: 6,
                propagateSetting: window.nekoSubtitle.changeSettings,
            });
        }
        """
    )

    result = mock_page.evaluate(
        """
        async () => {
            const display = document.getElementById('subtitle-display');
            const handle = document.querySelector('.subtitle-resize-se');
            handle.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: 260,
                clientY: 68,
            }));
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: 300,
                clientY: 90,
            }));
            document.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true,
                clientX: 300,
                clientY: 90,
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                inlineWidth: display.style.width,
                inlineHeight: display.style.height,
                setSizeCalls: window.__setSizeCalls,
                settingsChanges: window.__settingsChanges,
            };
        }
        """
    )

    assert result["inlineWidth"] == "300px"
    assert result["inlineHeight"] == "90px"
    assert result["setSizeCalls"][-1] == {
        "width": 312,
        "height": 102,
        "panelBounds": {"width": 300, "height": 90},
    }
    assert result["settingsChanges"][-1]["type"] == "bounds"
    assert result["settingsChanges"][-1]["value"] == {"width": 300, "height": 90}


@pytest.mark.frontend
def test_subtitle_window_skips_duplicate_size_bridge_updates(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__setSizeCalls = [];
            window.nekoSubtitle = {
                getBounds: () => Promise.resolve({ x: 10, y: 20, width: 612, height: 80 }),
                getCursorPoint: () => Promise.resolve({ x: 0, y: 0 }),
                setSize: (w, h, options) => window.__setSizeCalls.push({
                    width: w,
                    height: h,
                    panelBounds: options && options.panelBounds,
                }),
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 600,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.dispatchEvent(new Event('resize'));
            window.dispatchEvent(new Event('resize'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            return window.__setSizeCalls;
        }
        """
    )

    assert result == [{
        "width": 612,
        "height": 80,
        "panelBounds": {"width": 600, "height": 68},
    }]


@pytest.mark.frontend
def test_subtitle_window_resize_closes_settings_float_before_native_resize(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;" data-subtitle-panel-state="settings">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <button type="button" id="subtitle-settings-btn" aria-expanded="true"></button>
            <div id="subtitle-panel-controls" aria-hidden="false"></div>
            <div id="subtitle-settings-panel">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">不透明度</span>
                    <input type="range" id="subtitle-opacity-slider" min="0" max="100" value="95">
                    <span id="subtitle-opacity-value">95%</span>
                </div>
            </div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-se" data-resize-dir="se"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__subtitleCalls = [];
            window.nekoSubtitle = {
                getBounds: () => Promise.resolve({ x: 10, y: 20, width: 272, height: 80 }),
                getCursorPoint: () => Promise.resolve({ x: 0, y: 0 }),
                setSize: (w, h, options) => window.__subtitleCalls.push({
                    type: 'setSize',
                    width: w,
                    height: h,
                    panelBounds: options && options.panelBounds,
                }),
                setBounds: (x, y, w, h) => window.__subtitleCalls.push({
                    type: 'setBounds',
                    x,
                    y,
                    width: w,
                    height: h,
                }),
                getWorkArea: () => Promise.resolve({ x: 0, y: 0, width: 1000, height: 800 }),
                resizeStart: (direction, options) => window.__subtitleCalls.push({
                    type: 'resizeStart',
                    direction,
                    minWidth: options && options.minWidth,
                    minHeight: options && options.minHeight,
                }),
                resizeStop: () => window.__subtitleCalls.push({ type: 'resizeStop' }),
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.__subtitleCalls = [];
            const handle = document.querySelector('.subtitle-resize-se');
            const rect = handle.getBoundingClientRect();
            handle.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: rect.left + rect.width / 2,
                clientY: rect.top + rect.height / 2,
            }));
            await new Promise((resolve) => setTimeout(resolve, 30));
            return {
                settingsHidden: document.getElementById('subtitle-settings-panel').classList.contains('hidden'),
                panelState: document.getElementById('subtitle-display').dataset.subtitlePanelState,
                expanded: document.getElementById('subtitle-settings-btn').getAttribute('aria-expanded'),
                calls: window.__subtitleCalls,
            };
        }
        """
    )

    assert result["settingsHidden"] is True
    assert result["panelState"] == "controls"
    assert result["expanded"] == "false"
    assert result["calls"][0] == {
        "type": "setSize",
        "width": 272,
        "height": 80,
        "panelBounds": {"width": 260, "height": 68},
    }
    assert result["calls"][1]["type"] == "resizeStart"
    assert result["calls"][1]["minWidth"] == 240
    assert result["calls"][1]["minHeight"] == 52
    assert all(call["type"] != "setBounds" for call in result["calls"])


@pytest.mark.frontend
def test_subtitle_window_left_and_top_resize_use_native_bridge_without_carrier_bounds(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="controls">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="false">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn" aria-expanded="true"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">不透明度</span>
                    <input type="range" id="subtitle-opacity-slider" min="0" max="100" value="95">
                    <span id="subtitle-opacity-value">95%</span>
                </div>
            </div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-w" data-resize-dir="w"></span>
                <span class="subtitle-resize-edge subtitle-resize-n" data-resize-dir="n"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            Object.defineProperty(window, 'screenX', { value: 100, configurable: true });
            Object.defineProperty(window, 'screenY', { value: 120, configurable: true });
            window.__subtitleCalls = [];
            window.nekoSubtitle = {
                getBounds: () => Promise.resolve({ x: window.screenX, y: window.screenY, width: 272, height: 164 }),
                getCursorPoint: () => Promise.resolve({ x: 0, y: 0 }),
                getWorkArea: () => Promise.resolve({ x: 0, y: 0, width: 1000, height: 800 }),
                setBounds: (x, y, w, h) => {
                    window.__subtitleCalls.push({ type: 'setBounds', x, y, width: w, height: h });
                    Object.defineProperty(window, 'screenX', { value: x, configurable: true });
                    Object.defineProperty(window, 'screenY', { value: y, configurable: true });
                    window.dispatchEvent(new Event('resize'));
                },
                setSize: (w, h, options) => window.__subtitleCalls.push({
                    type: 'setSize',
                    width: w,
                    height: h,
                    panelBounds: options && options.panelBounds,
                }),
                resizeStart: (direction, options) => window.__subtitleCalls.push({
                    type: 'resizeStart',
                    direction,
                    minWidth: options && options.minWidth,
                    minHeight: options && options.minHeight,
                }),
                resizeStop: () => window.__subtitleCalls.push({ type: 'resizeStop' }),
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const start = display.getBoundingClientRect();
            window.__subtitleCalls = [];
            for (const item of [
                { selector: '.subtitle-resize-w', dx: -40, dy: 0, x: start.left + 1, y: start.top + start.height / 2 },
                { selector: '.subtitle-resize-n', dx: 0, dy: -24, x: start.left + start.width / 2, y: start.top + 1 },
            ]) {
                const handle = document.querySelector(item.selector);
                handle.dispatchEvent(new MouseEvent('mousedown', {
                    bubbles: true,
                    button: 0,
                    clientX: item.x,
                    clientY: item.y,
                    screenX: window.screenX + item.x,
                    screenY: window.screenY + item.y,
                }));
                await new Promise((resolve) => setTimeout(resolve, 0));
                document.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles: true,
                    clientX: item.x + item.dx,
                    clientY: item.y + item.dy,
                    screenX: window.screenX + item.x + item.dx,
                    screenY: window.screenY + item.y + item.dy,
                }));
                document.dispatchEvent(new MouseEvent('mouseup', {
                    bubbles: true,
                    clientX: item.x + item.dx,
                    clientY: item.y + item.dy,
                    screenX: window.screenX + item.x + item.dx,
                    screenY: window.screenY + item.y + item.dy,
                }));
                await new Promise((resolve) => setTimeout(resolve, 80));
            }
            return {
                settingsHidden: document.getElementById('subtitle-settings-panel').classList.contains('hidden'),
                storedBounds: JSON.parse(window.localStorage.getItem('subtitlePanelBounds')),
                calls: window.__subtitleCalls,
                nativeResizing: display.dataset.subtitleNativeResizing || '',
                carrierResizing: display.dataset.subtitleCarrierResizing || '',
            };
        }
        """
    )

    assert result["settingsHidden"] is True
    assert [call["direction"] for call in result["calls"] if call["type"] == "resizeStart"] == ["w", "n"]
    assert [call["type"] for call in result["calls"]].count("resizeStop") == 2
    assert result["calls"][-1]["type"] == "resizeStop"
    assert all(call["type"] != "setBounds" for call in result["calls"])
    assert all(
        call["type"] != "setSize" or call["height"] == 80
        for call in result["calls"]
    )
    assert result["nativeResizing"] == ""
    assert result["carrierResizing"] == ""


@pytest.mark.frontend
def test_subtitle_panel_bounds_enforce_usable_minimum_without_legacy_scale_controls(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" data-subtitle-panel-state="controls" style="display:flex; opacity:1; visibility:visible; animation:none; transform:none;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls">
                <button type="button" class="subtitle-panel-control-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" class="subtitle-panel-control-btn"></button>
            </div>
        </div>
        """,
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');
            const bounds = shared.getPanelBounds({ width: 80, height: 20 });
            shared.applySubtitlePanelBounds(display, bounds, { host: 'web' });
            const rect = display.getBoundingClientRect();
            const controlsRect = document.getElementById('subtitle-panel-controls').getBoundingClientRect();
            const style = getComputedStyle(display);
            return {
                bounds,
                rect: {
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                },
                controlsContained: controlsRect.left >= rect.left
                    && controlsRect.right <= rect.right
                    && controlsRect.top >= rect.top
                    && controlsRect.bottom <= rect.bottom,
                cssMinWidth: style.minWidth,
                legacySlider: document.querySelectorAll('#subtitle-size-slider').length,
                legacyButtons: document.querySelectorAll('.subtitle-size-btn').length,
            };
        }
        """
    )

    assert result["bounds"] == {"width": 228, "height": 40}
    assert result["rect"] == {"width": 228, "height": 40}
    assert result["controlsContained"] is True
    assert result["cssMinWidth"] == "228px"
    assert result["legacySlider"] == 0
    assert result["legacyButtons"] == 0


@pytest.mark.frontend
def test_web_subtitle_panel_minimum_does_not_overflow_a_small_viewport(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 180, "height": 32})
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible;"></div>
        """,
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const display = document.getElementById('subtitle-display');
            const logical = window.nekoSubtitleShared.applySubtitlePanelBounds(
                display,
                { width: 80, height: 20 },
                { host: 'web' },
            );
            const rect = display.getBoundingClientRect();
            return {
                logical,
                datasetWidth: display.dataset.subtitlePanelWidth,
                datasetHeight: display.dataset.subtitlePanelHeight,
                rectWidth: Math.round(rect.width),
                rectHeight: Math.round(rect.height),
                cssMinWidth: getComputedStyle(display).minWidth,
                cssMinHeight: getComputedStyle(display).minHeight,
            };
        }
        """
    )

    assert result == {
        "logical": {"width": 228, "height": 40},
        "datasetWidth": "180",
        "datasetHeight": "32",
        "rectWidth": 180,
        "rectHeight": 32,
        "cssMinWidth": "180px",
        "cssMinHeight": "32px",
    }


@pytest.mark.frontend
def test_web_subtitle_panel_reapplies_viewport_limits_after_resize(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 800, "height": 300})
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible;"></div>
        """,
    )
    mock_page.evaluate(
        """
        () => localStorage.setItem(
            'subtitlePanelBounds',
            JSON.stringify({ width: 655, height: 109 }),
        )
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.evaluate(
        """
        () => {
            window.subtitleTestController = window.nekoSubtitleShared.initSubtitleUI({ host: 'web' });
        }
        """
    )

    def panel_size() -> dict[str, int]:
        return mock_page.evaluate(
            """
            () => {
                const rect = document.getElementById('subtitle-display').getBoundingClientRect();
                return { width: Math.round(rect.width), height: Math.round(rect.height) };
            }
            """
        )

    assert panel_size() == {"width": 655, "height": 109}

    mock_page.set_viewport_size({"width": 180, "height": 32})
    mock_page.wait_for_function(
        """
        () => document.getElementById('subtitle-display').dataset.subtitlePanelHeight === '32'
        """
    )
    assert panel_size() == {"width": 180, "height": 32}

    mock_page.set_viewport_size({"width": 800, "height": 300})
    mock_page.wait_for_function(
        """
        () => document.getElementById('subtitle-display').dataset.subtitlePanelHeight === '109'
        """
    )
    assert panel_size() == {"width": 655, "height": 109}
    mock_page.evaluate("() => window.subtitleTestController.destroy()")


@pytest.mark.frontend
def test_subtitle_boundary_resize_persists_free_panel_bounds(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 1200, "height": 720})
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible;">
            <div id="subtitle-scroll"><span id="subtitle-text" data-subtitle-placeholder="暂无翻译内容"></span></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-n" data-resize-dir="n"></span>
                <span class="subtitle-resize-edge subtitle-resize-e" data-resize-dir="e"></span>
                <span class="subtitle-resize-edge subtitle-resize-s" data-resize-dir="s"></span>
                <span class="subtitle-resize-edge subtitle-resize-w" data-resize-dir="w"></span>
                <span class="subtitle-resize-edge subtitle-resize-ne" data-resize-dir="ne"></span>
                <span class="subtitle-resize-edge subtitle-resize-se" data-resize-dir="se"></span>
                <span class="subtitle-resize-edge subtitle-resize-sw" data-resize-dir="sw"></span>
                <span class="subtitle-resize-edge subtitle-resize-nw" data-resize-dir="nw"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.clear();
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 600,
                height: 68,
            }));
            window.localStorage.setItem('subtitlePanelPosition', JSON.stringify({
                left: 300,
                top: 300,
                coordinateSpace: 'viewport',
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const handle = document.querySelector('.subtitle-resize-se');
            display.style.animation = 'none';
            display.style.transform = 'translateX(-50%)';
            await new Promise((resolve) => setTimeout(resolve, 0));
            const beforeRect = display.getBoundingClientRect();
            handle.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: beforeRect.right,
                clientY: beforeRect.bottom,
            }));
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: beforeRect.right + 160,
                clientY: beforeRect.bottom + 42,
            }));
            const resizingDuringMove = display.classList.contains('resizing');
            document.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true,
                clientX: beforeRect.right + 160,
                clientY: beforeRect.bottom + 42,
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterRect = display.getBoundingClientRect();
            const settings = shared.getSettings();
            const renderState = shared.getRenderState();
            const style = getComputedStyle(display);
            const response = {
                resizingDuringMove,
                before: {
                    width: Math.round(beforeRect.width),
                    height: Math.round(beforeRect.height),
                },
                after: {
                    width: Math.round(afterRect.width),
                    height: Math.round(afterRect.height),
                },
                settingsBounds: settings.subtitlePanelBounds,
                renderBounds: renderState.subtitlePanelBounds,
                storedBounds: JSON.parse(window.localStorage.getItem('subtitlePanelBounds')),
                storedPosition: JSON.parse(window.localStorage.getItem('subtitlePanelPosition')),
                styleWidth: display.style.width,
                styleHeight: display.style.height,
                contentMaxHeight: display.style.getPropertyValue('--subtitle-content-max-height'),
                borderTopWidth: style.borderTopWidth,
                borderTopStyle: style.borderTopStyle,
                legacySlider: document.querySelectorAll('#subtitle-size-slider').length,
                legacyButtons: document.querySelectorAll('.subtitle-size-btn').length,
                legacyScaleStorage: window.localStorage.getItem('subtitlePanelScale'),
                legacySizeStorage: window.localStorage.getItem('subtitleSize'),
                hasDragHandle: !!document.getElementById('subtitle-drag-handle'),
            };
            controller.destroy();
            return response;
        }
        """
    )

    assert result["resizingDuringMove"] is True
    assert result["before"] == {"width": 600, "height": 68}
    assert result["after"] == {"width": 760, "height": 110}
    assert result["settingsBounds"] == {"width": 760, "height": 110}
    assert result["renderBounds"] == {"width": 760, "height": 110}
    assert result["storedBounds"] == {"width": 760, "height": 110}
    assert result["storedPosition"]["coordinateSpace"] == "viewport"
    assert result["styleWidth"] == "760px"
    assert result["styleHeight"] == "110px"
    assert result["contentMaxHeight"] == "86px"
    assert result["borderTopWidth"] == "0px"
    assert result["borderTopStyle"] == "none"
    assert result["legacySlider"] == 0
    assert result["legacyButtons"] == 0
    assert result["legacyScaleStorage"] is None
    assert result["legacySizeStorage"] is None
    assert result["hasDragHandle"] is False


@pytest.mark.frontend
def test_subtitle_window_boundary_resize_uses_native_window_resize_bounds(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-se" data-resize-dir="se"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__nativeResizeCalls = [];
            window.__propagatedSubtitleSettings = [];
            window.nekoSubtitle = {
                resizeStart: (direction, options) => window.__nativeResizeCalls.push({
                    type: 'start',
                    direction,
                    minWidth: options && options.minWidth,
                    minHeight: options && options.minHeight,
                    visualBounds: options && options.visualBounds,
                }),
                resizeStop: () => window.__nativeResizeCalls.push({ type: 'stop' }),
                getBounds: () => Promise.resolve({ x: window.screenX || 10, y: window.screenY || 20, width: 420, height: 90 }),
                getWorkArea: () => Promise.resolve({ x: 0, y: 0, width: 1000, height: 800 }),
                setBounds: (x, y, w, h) => {
                    window.__nativeResizeCalls.push({ type: 'setBounds', x, y, width: w, height: h });
                    Object.defineProperty(window, 'screenX', { value: x, configurable: true });
                    Object.defineProperty(window, 'screenY', { value: y, configurable: true });
                    window.dispatchEvent(new Event('resize'));
                },
                setSize: () => window.__nativeResizeCalls.push({ type: 'setSize' }),
                changeSettings: (change) => window.__propagatedSubtitleSettings.push(change),
                dragStart: () => {},
                dragStop: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result_start = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.__nativeResizeCalls = [];
            const handle = document.querySelector('.subtitle-resize-se');
            handle.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: 260,
                clientY: 68,
                screenX: 260,
                screenY: 68,
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const displayDuringResize = document.getElementById('subtitle-display');
            const computedDuringResize = getComputedStyle(displayDuringResize);
            return {
                calls: window.__nativeResizeCalls,
                inlineWidth: displayDuringResize.style.width,
                inlineHeight: displayDuringResize.style.height,
                computedWidth: computedDuringResize.width,
                computedHeight: computedDuringResize.height,
                nativeFrameWidth: displayDuringResize.style.getPropertyValue('--subtitle-native-resize-width'),
                nativeFrameHeight: displayDuringResize.style.getPropertyValue('--subtitle-native-resize-height'),
                nativeResizing: displayDuringResize.dataset.subtitleNativeResizing,
                carrierResizing: displayDuringResize.dataset.subtitleCarrierResizing || '',
                resizingClass: document.documentElement.classList.contains('neko-resizing'),
            };
        }
        """
    )

    mock_page.set_viewport_size({"width": 432, "height": 102})

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            await new Promise((resolve) => requestAnimationFrame(resolve));
            const displayDuringResize = document.getElementById('subtitle-display');
            const computedDuringResize = getComputedStyle(displayDuringResize);
            const duringResize = {
                computedWidth: computedDuringResize.width,
                computedHeight: computedDuringResize.height,
                nativeFrameWidth: displayDuringResize.style.getPropertyValue('--subtitle-native-resize-width'),
                nativeFrameHeight: displayDuringResize.style.getPropertyValue('--subtitle-native-resize-height'),
            };
            document.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true,
                clientX: 420,
                clientY: 90,
                screenX: 420,
                screenY: 90,
            }));
            await new Promise((resolve) => setTimeout(resolve, 80));
            const display = document.getElementById('subtitle-display');
            const snapshot = {
                calls: window.__nativeResizeCalls,
                duringResize,
                settingsBounds: shared.getSettings().subtitlePanelBounds,
                storedBounds: JSON.parse(window.localStorage.getItem('subtitlePanelBounds')),
                displayWidth: display.style.width,
                displayHeight: display.style.height,
                propagated: window.__propagatedSubtitleSettings,
            };
            return snapshot;
        }
        """
    )

    assert result_start["calls"][0]["type"] == "start"
    assert result_start["calls"][0]["direction"] == "se"
    assert all(call["type"] != "setBounds" for call in result_start["calls"])
    assert result_start == {
        "calls": result_start["calls"],
        "inlineWidth": "260px",
        "inlineHeight": "68px",
        "computedWidth": "260px",
        "computedHeight": "68px",
        "nativeFrameWidth": "260px",
        "nativeFrameHeight": "68px",
        "nativeResizing": "true",
        "carrierResizing": "",
        "resizingClass": True,
    }
    assert result["calls"][-1]["type"] == "stop"
    assert all(call["type"] != "setBounds" for call in result["calls"])
    assert result["duringResize"] == {
        "computedWidth": "420px",
        "computedHeight": "90px",
        "nativeFrameWidth": "420px",
        "nativeFrameHeight": "90px",
    }
    assert result["settingsBounds"] == {"width": 420, "height": 90}
    assert result["storedBounds"] == {"width": 420, "height": 90}
    assert result["displayWidth"] == "420px"
    assert result["displayHeight"] == "90px"
    assert result["propagated"] == [
        {"type": "bounds", "value": {"width": 420, "height": 90}},
    ]


@pytest.mark.frontend
def test_subtitle_window_native_resize_updates_controls_scale_before_mouseup(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="controls" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="false">
                <button type="button" id="subtitle-lock-btn" class="subtitle-panel-control-btn"></button>
                <button type="button" id="subtitle-settings-btn" class="subtitle-panel-control-btn"></button>
                <button type="button" id="subtitle-close-btn" class="subtitle-panel-control-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-se" data-resize-dir="se"></span>
            </div>
        </div>
        """,
        path="/subtitle-window-native-resize-controls-scale-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.__nativeResizeCalls = [];
            window.nekoSubtitle = {
                resizeStart: (direction) => window.__nativeResizeCalls.push({ type: 'start', direction }),
                resizeStop: () => window.__nativeResizeCalls.push({ type: 'stop' }),
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 655,
                height: 109,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const controls = document.getElementById('subtitle-panel-controls');
            controls.style.transition = 'none';
            const button = document.getElementById('subtitle-settings-btn');
            button.style.transition = 'none';
            const handle = document.querySelector('.subtitle-resize-se');
            handle.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: 655,
                clientY: 109,
                screenX: 655,
                screenY: 109,
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const beforeFrameResize = {
                scale: getComputedStyle(display).getPropertyValue('--subtitle-control-scale').trim(),
                width: Math.round(button.getBoundingClientRect().width),
            };
            return { beforeFrameResize };
        }
        """
    )

    mock_page.set_viewport_size({"width": 1322, "height": 230})
    during_resize = mock_page.evaluate(
        """
        async () => {
            await new Promise((resolve) => requestAnimationFrame(resolve));
            const display = document.getElementById('subtitle-display');
            const button = document.getElementById('subtitle-settings-btn');
            return {
                scale: getComputedStyle(display).getPropertyValue('--subtitle-control-scale').trim(),
                dataset: display.dataset.subtitleControlScale,
                width: Math.round(button.getBoundingClientRect().width),
                height: Math.round(button.getBoundingClientRect().height),
                nativeFrameWidth: display.style.getPropertyValue('--subtitle-native-resize-width'),
                nativeFrameHeight: display.style.getPropertyValue('--subtitle-native-resize-height'),
            };
        }
        """
    )
    mock_page.evaluate("() => window.localStorage.removeItem('subtitlePanelBounds')")

    assert result["beforeFrameResize"] == {"scale": "1", "width": 22}
    assert during_resize == {
        "scale": "2",
        "dataset": "2",
        "width": 44,
        "height": 44,
        "nativeFrameWidth": "1310px",
        "nativeFrameHeight": "218px",
    }


@pytest.mark.frontend
def test_subtitle_window_native_resize_keeps_panel_size_until_main_frame_arrives(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-n" data-resize-dir="n"></span>
            </div>
        </div>
        """,
    )
    mock_page.set_viewport_size({"width": 612, "height": 220})
    mock_page.evaluate(
        """
        () => {
            window.__nativeResizeCalls = [];
            window.nekoSubtitle = {
                resizeStart: (direction, options) => window.__nativeResizeCalls.push({
                    type: 'start',
                    direction,
                    minWidth: options && options.minWidth,
                    minHeight: options && options.minHeight,
                    visualBounds: options && options.visualBounds,
                }),
                resizeStop: () => window.__nativeResizeCalls.push({ type: 'stop' }),
                getBounds: () => Promise.resolve({ x: window.screenX || 10, y: window.screenY || 20, width: 272, height: 80 }),
                getWorkArea: () => Promise.resolve({ x: 0, y: 0, width: 1000, height: 800 }),
                setBounds: (x, y, w, h) => {
                    window.__nativeResizeCalls.push({ type: 'setBounds', x, y, width: w, height: h });
                    Object.defineProperty(window, 'screenX', { value: x, configurable: true });
                    Object.defineProperty(window, 'screenY', { value: y, configurable: true });
                    window.dispatchEvent(new Event('resize'));
                },
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.__nativeResizeCalls = [];
            const display = document.getElementById('subtitle-display');
            const before = display.getBoundingClientRect();
            const handle = document.querySelector('.subtitle-resize-n');
            handle.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: before.left + before.width / 2,
                clientY: before.top,
            }));
            const during = display.getBoundingClientRect();
            const duringStyle = getComputedStyle(display);
            document.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true,
                clientX: before.left + before.width / 2,
                clientY: before.top,
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                before: {
                    width: Math.round(before.width),
                    height: Math.round(before.height),
                },
                during: {
                    width: Math.round(during.width),
                    height: Math.round(during.height),
                },
                computedWidth: duringStyle.width,
                computedHeight: duringStyle.height,
                nativeWidthVar: display.style.getPropertyValue('--subtitle-native-resize-width'),
                nativeHeightVar: display.style.getPropertyValue('--subtitle-native-resize-height'),
                calls: window.__nativeResizeCalls,
            };
        }
        """
    )

    assert result["before"] == {"width": 260, "height": 68}
    assert result["during"] == {"width": 260, "height": 68}
    assert result["computedWidth"] == "260px"
    assert result["computedHeight"] == "68px"
    assert result["nativeWidthVar"] == "260px"
    assert result["nativeHeightVar"] == "68px"
    assert result["calls"][0]["type"] == "start"
    assert result["calls"][0]["direction"] == "n"
    assert result["calls"][-1]["type"] == "stop"
    assert all(call["type"] != "setBounds" for call in result["calls"])


@pytest.mark.frontend
def test_subtitle_window_uses_web_font_size_without_desktop_shrink(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <button type="button" id="subtitle-settings-btn"></button>
            <button type="button" id="subtitle-close-btn"></button>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-se" data-resize-dir="se"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__subtitleWindowSizes = [];
            window.nekoSubtitle = {
                setSize: (width, height, options) => window.__subtitleWindowSizes.push({
                    width,
                    height,
                    panelBounds: options && options.panelBounds,
                }),
                getBounds: () => Promise.resolve({ x: 10, y: 20, width: 612, height: 80 }),
                getCursorPoint: () => Promise.resolve({ x: 20, y: 20, screenX: 30, screenY: 40 }),
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 600,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            const display = document.getElementById('subtitle-display');
            display.style.transition = 'none';
            window.nekoSubtitleShared.applySubtitlePanelBounds(display, {
                width: 600,
                height: 68,
            }, { host: 'window' });
            window.dispatchEvent(new CustomEvent('neko-ws-transcript', {
                detail: {
                    translated: true,
                    transcript: 'This is a longer translated subtitle that should keep the same readable size after desktop layout.',
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const text = document.getElementById('subtitle-text');
            const textStyle = getComputedStyle(text);
            return {
                displayFontSize: getComputedStyle(display).fontSize,
                inlineDisplayFontSize: display.style.fontSize,
                textFontSize: textStyle.fontSize,
                inlineTextFontSize: text.style.fontSize,
                lastSize: window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1],
            };
        }
        """
    )

    assert result["displayFontSize"] == "26px"
    assert result["inlineDisplayFontSize"] == "26px"
    assert result["textFontSize"] == "26px"
    assert result["inlineTextFontSize"] == ""
    assert result["lastSize"] == {"width": 612, "height": 80, "panelBounds": {"width": 600, "height": 68}}


@pytest.mark.frontend
def test_subtitle_window_state_sync_updates_font_size_realtime(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <button type="button" id="subtitle-settings-btn"></button>
            <button type="button" id="subtitle-close-btn"></button>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.nekoSubtitle = {
                setSize: () => {},
                getBounds: () => Promise.resolve({ x: 10, y: 20, width: 612, height: 80 }),
                getCursorPoint: () => Promise.resolve({ x: 20, y: 20, screenX: 30, screenY: 40 }),
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            const before = {
                setting: window.nekoSubtitleShared.getSettings().subtitleFontSize,
                colorScheme: window.nekoSubtitleShared.getSettings().subtitleColorScheme,
                danmakuMode: window.nekoSubtitleShared.getSettings().subtitleDanmakuMode,
                textFontSize: getComputedStyle(text).fontSize,
                textColor: getComputedStyle(text).color,
                dataset: display.dataset.subtitleFontSize,
                colorDataset: display.dataset.subtitleColorScheme,
            };
            window.dispatchEvent(new CustomEvent('neko-subtitle-state-sync', {
                detail: { type: 'colorScheme', value: 'orange' },
            }));
            window.dispatchEvent(new CustomEvent('neko-subtitle-state-sync', {
                detail: { type: 'danmakuMode', value: true },
            }));
            window.dispatchEvent(new CustomEvent('neko-subtitle-state-sync', {
                detail: { type: 'fontSize', value: 44 },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const after = {
                setting: window.nekoSubtitleShared.getSettings().subtitleFontSize,
                colorScheme: window.nekoSubtitleShared.getSettings().subtitleColorScheme,
                danmakuMode: window.nekoSubtitleShared.getSettings().subtitleDanmakuMode,
                textFontSize: getComputedStyle(text).fontSize,
                textColor: getComputedStyle(text).color,
                dataset: display.dataset.subtitleFontSize,
                colorDataset: display.dataset.subtitleColorScheme,
            };
            return { before, after };
        }
        """
    )

    assert result["before"] == {
        "setting": 26,
        "colorScheme": "default",
        "danmakuMode": False,
        "textFontSize": "26px",
        "textColor": "rgb(5, 7, 10)",
        "dataset": "26",
        "colorDataset": "default",
    }
    assert result["after"] == {
        "setting": 44,
        "colorScheme": "orange",
        "danmakuMode": True,
        "textFontSize": "44px",
        "textColor": "rgb(255, 140, 0)",
        "dataset": "44",
        "colorDataset": "orange",
    }


@pytest.mark.frontend
def test_subtitle_window_resize_handles_do_not_start_window_drag(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-n" data-resize-dir="n"></span>
                <span class="subtitle-resize-edge subtitle-resize-e" data-resize-dir="e"></span>
                <span class="subtitle-resize-edge subtitle-resize-s" data-resize-dir="s"></span>
                <span class="subtitle-resize-edge subtitle-resize-w" data-resize-dir="w"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__nativeResizeCalls = [];
            window.__dragCalls = [];
            window.nekoSubtitle = {
                resizeStart: (direction, options) => window.__nativeResizeCalls.push({
                    type: 'start',
                    direction,
                    minWidth: options && options.minWidth,
                    minHeight: options && options.minHeight,
                }),
                resizeStop: () => window.__nativeResizeCalls.push({ type: 'stop' }),
                getBounds: () => Promise.resolve({ x: window.screenX || 10, y: window.screenY || 20, width: 260, height: 68 }),
                getWorkArea: () => Promise.resolve({ x: 0, y: 0, width: 1000, height: 800 }),
                setBounds: (x, y, w, h) => {
                    window.__nativeResizeCalls.push({ type: 'setBounds', x, y, width: w, height: h });
                    Object.defineProperty(window, 'screenX', { value: x, configurable: true });
                    Object.defineProperty(window, 'screenY', { value: y, configurable: true });
                    window.dispatchEvent(new Event('resize'));
                },
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => window.__dragCalls.push('start'),
                dragStop: () => window.__dragCalls.push('stop'),
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const dispatchHandleDown = (selector) => {
                const handle = document.querySelector(selector);
                const rect = handle.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                handle.dispatchEvent(new MouseEvent('mousedown', {
                    bubbles: true,
                    button: 0,
                    clientX: x,
                    clientY: y,
                }));
                document.dispatchEvent(new MouseEvent('mouseup', {
                    bubbles: true,
                    clientX: x,
                    clientY: y,
                }));
            };

            dispatchHandleDown('.subtitle-resize-w');
            await new Promise((resolve) => setTimeout(resolve, 60));
            dispatchHandleDown('.subtitle-resize-e');
            await new Promise((resolve) => setTimeout(resolve, 60));
            dispatchHandleDown('.subtitle-resize-n');
            await new Promise((resolve) => setTimeout(resolve, 60));
            dispatchHandleDown('.subtitle-resize-s');
            await new Promise((resolve) => setTimeout(resolve, 60));

            const snapshot = {
                resizeCalls: window.__nativeResizeCalls,
                dragCalls: window.__dragCalls,
            };
            return snapshot;
        }
        """
    )

    assert result["resizeCalls"]
    assert all(call["type"] in {"start", "stop"} for call in result["resizeCalls"])
    assert [call["direction"] for call in result["resizeCalls"] if call["type"] == "start"] == ["w", "e", "n", "s"]
    assert result["dragCalls"] == []


@pytest.mark.frontend
def test_subtitle_window_drag_starts_only_after_non_edge_movement(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-n" data-resize-dir="n"></span>
                <span class="subtitle-resize-edge subtitle-resize-e" data-resize-dir="e"></span>
                <span class="subtitle-resize-edge subtitle-resize-s" data-resize-dir="s"></span>
                <span class="subtitle-resize-edge subtitle-resize-w" data-resize-dir="w"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__nativeResizeCalls = [];
            window.__dragCalls = [];
            window.nekoSubtitle = {
                resizeStart: (direction) => window.__nativeResizeCalls.push({ type: 'start', direction }),
                resizeStop: () => window.__nativeResizeCalls.push({ type: 'stop' }),
                getBounds: () => Promise.resolve({ x: 10, y: 20, width: 260, height: 68 }),
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => window.__dragCalls.push('start'),
                dragStop: () => window.__dragCalls.push('stop'),
                openSettings: () => {},
                closeSettings: () => {},
                updateSettingsWindow: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const rect = display.getBoundingClientRect();
            const center = {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2,
            };

            display.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: center.x,
                clientY: center.y,
            }));
            document.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true,
                clientX: center.x,
                clientY: center.y,
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterClick = {
                dragCalls: window.__dragCalls.slice(),
                resizeCalls: window.__nativeResizeCalls.slice(),
            };

            display.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: center.x,
                clientY: center.y,
            }));
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: center.x + 12,
                clientY: center.y + 4,
            }));
            document.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true,
                clientX: center.x + 12,
                clientY: center.y + 4,
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));

            return {
                afterClick,
                finalDragCalls: window.__dragCalls,
                finalResizeCalls: window.__nativeResizeCalls,
            };
        }
        """
    )

    assert result["afterClick"] == {
        "dragCalls": [],
        "resizeCalls": [],
    }
    assert result["finalDragCalls"] == ["start", "stop"]
    assert result["finalResizeCalls"] == []


@pytest.mark.frontend
def test_subtitle_empty_placeholder_is_visual_only_and_uses_text_edge_protection(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible;">
            <div id="subtitle-scroll"><span id="subtitle-text" data-subtitle-placeholder="fallback"></span></div>
            <select id="subtitle-lang-select">
                <option value="en">English</option>
                <option value="ja">日本語</option>
            </select>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            document.documentElement.lang = 'zh-CN';
            window.localStorage.setItem('i18nextLng', 'zh-CN');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const text = document.getElementById('subtitle-text');
            const textStyle = getComputedStyle(text);
            const placeholderStyle = getComputedStyle(text, '::before');
            const before = {
                textContent: text.textContent,
                placeholderAttr: text.getAttribute('data-subtitle-placeholder'),
                placeholderContent: placeholderStyle.content,
                placeholderDisplay: placeholderStyle.display,
                fillColor: textStyle.color,
                strokeColor: textStyle.webkitTextStrokeColor,
                strokeWidth: textStyle.webkitTextStrokeWidth,
            };
            text.textContent = '已有译文';
            const afterStyle = getComputedStyle(text, '::before');
            const after = {
                textContent: text.textContent,
                placeholderContent: afterStyle.content,
            };
            controller.destroy();
            return { before, after };
        }
        """
    )

    assert result["before"]["textContent"] == ""
    assert result["before"]["placeholderAttr"] == "暂无翻译内容"
    assert "暂无翻译内容" in result["before"]["placeholderContent"]
    assert result["before"]["placeholderDisplay"] == "inline-block"
    assert result["before"]["fillColor"] == "rgb(5, 7, 10)"
    assert result["before"]["strokeColor"] == "rgba(255, 255, 255, 0.78)"
    assert result["before"]["strokeWidth"] == "0.35px"
    assert result["after"]["textContent"] == "已有译文"
    assert result["after"]["placeholderContent"] == "none"


@pytest.mark.frontend
def test_subtitle_empty_placeholder_follows_target_language(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible;">
            <div id="subtitle-scroll"><span id="subtitle-text" data-subtitle-placeholder="fallback"></span></div>
            <select id="subtitle-lang-select">
                <option value="en">English</option>
                <option value="ja">日本語</option>
            </select>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            document.documentElement.lang = 'zh-CN';
            window.localStorage.setItem('i18nextLng', 'zh-CN');
            window.localStorage.setItem('userLanguage', 'en');
            window.t = (key) => key === 'subtitle.display.emptyHint'
                ? '暂无翻译内容'
                : key;
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const text = document.getElementById('subtitle-text');
            const select = document.getElementById('subtitle-lang-select');
            const before = text.getAttribute('data-subtitle-placeholder');
            select.value = 'ja';
            select.dispatchEvent(new Event('change', { bubbles: true }));
            const after = text.getAttribute('data-subtitle-placeholder');
            const storedLanguage = window.localStorage.getItem('userLanguage');
            controller.destroy();
            return { before, after, storedLanguage };
        }
        """
    )

    assert result["before"] == "No translation yet"
    assert result["after"] == "翻訳はまだありません"
    assert result["storedLanguage"] == "ja"


@pytest.mark.frontend
def test_subtitle_window_settings_button_uses_external_layer_without_resizing(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;" data-subtitle-panel-state="controls">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <button type="button" id="subtitle-settings-btn" aria-expanded="false"></button>
            <div id="subtitle-settings-panel" class="hidden">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">不透明度</span>
                    <input type="range" id="subtitle-opacity-slider" min="0" max="100" value="95">
                    <span id="subtitle-opacity-value">95%</span>
                </div>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__subtitleWindowSizes = [];
            window.__subtitleSettingsOpenPayloads = [];
            window.__subtitleSettingsCloseCount = 0;
            window.nekoSubtitle = {
                setSize: (width, height, options) => window.__subtitleWindowSizes.push({
                    width,
                    height,
                    panelBounds: options && options.panelBounds,
                }),
                openSettings: (payload) => window.__subtitleSettingsOpenPayloads.push(payload),
                closeSettings: () => { window.__subtitleSettingsCloseCount += 1; },
                updateSettingsWindow: () => {},
                getBounds: () => Promise.resolve({ x: window.screenX || 100, y: window.screenY || 200, width: 612, height: 80 }),
                getCursorPoint: () => Promise.resolve({ x: 20, y: 20, screenX: 120, screenY: 220 }),
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 600,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const panel = document.getElementById('subtitle-settings-panel');
            const sizeBeforeOpen = window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1];
            document.getElementById('subtitle-settings-btn').click();
            const displayRect = display.getBoundingClientRect();
            const immediate = {
                panelState: display.dataset.subtitlePanelState || '',
                panelHidden: panel.classList.contains('hidden'),
                displayTop: displayRect.top,
                setSize: window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1],
                sizeCount: window.__subtitleWindowSizes.length,
                externalPayload: window.__subtitleSettingsOpenPayloads[0] || null,
            };
            display.dispatchEvent(new Event('pointerleave'));
            await new Promise((resolve) => setTimeout(resolve, 1400));
            const afterCleanDelay = {
                panelState: display.dataset.subtitlePanelState || '',
                panelHidden: panel.classList.contains('hidden'),
                openCount: window.__subtitleSettingsOpenPayloads.length,
                closeCount: window.__subtitleSettingsCloseCount,
                setSize: window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1],
                sizeCount: window.__subtitleWindowSizes.length,
            };
            display.dispatchEvent(new Event('pointerenter'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterPointerReturn = {
                panelState: display.dataset.subtitlePanelState || '',
                openCount: window.__subtitleSettingsOpenPayloads.length,
                closeCount: window.__subtitleSettingsCloseCount,
            };
            document.getElementById('subtitle-settings-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterSecondClick = {
                panelState: display.dataset.subtitlePanelState || '',
                closeCount: window.__subtitleSettingsCloseCount,
                openCount: window.__subtitleSettingsOpenPayloads.length,
                panelHidden: panel.classList.contains('hidden'),
                setSize: window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1],
                sizeCount: window.__subtitleWindowSizes.length,
            };
            display.dispatchEvent(new Event('pointerleave'));
            for (let i = 0; i < 6; i += 1) {
                await new Promise((resolve) => requestAnimationFrame(resolve));
            }
            await new Promise((resolve) => setTimeout(resolve, 1400));
            const settled = {
                panelState: display.dataset.subtitlePanelState || '',
                panelHidden: panel.classList.contains('hidden'),
                setSize: window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1],
                sizeCount: window.__subtitleWindowSizes.length,
            };
            document.getElementById('subtitle-settings-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.dispatchEvent(new CustomEvent('neko-subtitle-settings-closed', {
                detail: { reason: 'outside-blur', panelState: 'clean' },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterHostClosed = {
                panelState: display.dataset.subtitlePanelState || '',
                closeCount: window.__subtitleSettingsCloseCount,
                openCount: window.__subtitleSettingsOpenPayloads.length,
                panelHidden: panel.classList.contains('hidden'),
            };
            return { sizeBeforeOpen, immediate, afterCleanDelay, afterPointerReturn, afterSecondClick, settled, afterHostClosed };
        }
        """
    )

    assert result["sizeBeforeOpen"] == {"width": 612, "height": 80, "panelBounds": {"width": 600, "height": 68}}
    assert result["immediate"]["panelState"] == "settings"
    assert result["immediate"]["panelHidden"] is True
    assert result["immediate"]["setSize"] == result["sizeBeforeOpen"]
    assert result["immediate"]["sizeCount"] == 1
    assert result["immediate"]["externalPayload"]["state"]["subtitlePanelBounds"] == {"width": 600, "height": 68}
    assert result["immediate"]["externalPayload"]["anchor"]["width"] == 600
    assert result["immediate"]["externalPayload"]["anchor"]["height"] == 68
    assert result["afterCleanDelay"] == {
        "panelState": "settings",
        "panelHidden": True,
        "openCount": 1,
        "closeCount": 0,
        "setSize": result["sizeBeforeOpen"],
        "sizeCount": 1,
    }
    assert result["afterPointerReturn"] == {
        "panelState": "settings",
        "openCount": 1,
        "closeCount": 0,
    }
    assert result["afterSecondClick"] == {
        "panelState": "controls",
        "closeCount": 1,
        "openCount": 1,
        "panelHidden": True,
        "setSize": result["sizeBeforeOpen"],
        "sizeCount": 1,
    }
    assert result["settled"] == {
        "panelState": "clean",
        "panelHidden": True,
        "setSize": result["sizeBeforeOpen"],
        "sizeCount": 1,
    }
    assert result["afterHostClosed"] == {
        "panelState": "clean",
        "closeCount": 2,
        "openCount": 2,
        "panelHidden": True,
    }


@pytest.mark.frontend
def test_subtitle_window_settings_button_falls_back_to_inline_panel_without_external_bridge(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;" data-subtitle-panel-state="controls">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <button type="button" id="subtitle-settings-btn" aria-expanded="false"></button>
            <div id="subtitle-settings-panel" class="hidden">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">不透明度</span>
                    <input type="range" id="subtitle-opacity-slider" min="0" max="100" value="95">
                    <span id="subtitle-opacity-value">95%</span>
                </div>
            </div>
        </div>
        """,
        path="/subtitle-window-inline-fallback-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.__subtitleWindowSizes = [];
            window.nekoSubtitle = {
                setSize: (width, height, options) => window.__subtitleWindowSizes.push({
                    width,
                    height,
                    panelBounds: options && options.panelBounds,
                }),
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const panel = document.getElementById('subtitle-settings-panel');
            const button = document.getElementById('subtitle-settings-btn');
            const sizeBeforeOpen = window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1];
            button.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const panelRect = panel.getBoundingClientRect();
            const sizeAfterOpen = window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1];
            const opened = {
                panelState: display.dataset.subtitlePanelState || '',
                panelHidden: panel.classList.contains('hidden'),
                expanded: button.getAttribute('aria-expanded'),
                externalDataset: display.dataset.subtitleWindowInteractions || '',
                panelHeight: Math.round(panelRect.height),
                sizeBeforeOpen,
                sizeAfterOpen,
            };
            button.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const sizeAfterClose = window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1];
            return {
                opened,
                closed: {
                    panelState: display.dataset.subtitlePanelState || '',
                    panelHidden: panel.classList.contains('hidden'),
                    expanded: button.getAttribute('aria-expanded'),
                    sizeAfterClose,
                },
            };
        }
        """
    )

    assert result["opened"]["panelState"] == "settings"
    assert result["opened"]["panelHidden"] is False
    assert result["opened"]["expanded"] == "true"
    assert result["opened"]["externalDataset"] == ""
    assert result["opened"]["sizeBeforeOpen"] == {
        "width": 667,
        "height": 121,
        "panelBounds": {"width": 655, "height": 109},
    }
    assert result["opened"]["panelHeight"] > 0
    assert result["opened"]["sizeAfterOpen"]["width"] == 667
    assert result["opened"]["sizeAfterOpen"]["height"] == 121 + result["opened"]["panelHeight"] + 8
    assert result["opened"]["sizeAfterOpen"]["panelBounds"] == {"width": 655, "height": 109}
    assert result["closed"]["panelState"] == "controls"
    assert result["closed"]["panelHidden"] is True
    assert result["closed"]["expanded"] == "false"
    assert result["closed"]["sizeAfterClose"] == result["opened"]["sizeBeforeOpen"]


@pytest.mark.frontend
def test_subtitle_external_settings_button_works_without_inline_panel(mock_page: Page):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;" data-subtitle-panel-state="controls">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <button type="button" id="subtitle-settings-btn" aria-expanded="false"></button>
        </div>
        """,
        path="/subtitle-window-external-no-inline-panel-harness",
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const calls = [];
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({
                host: 'window',
                windowInteractions: 'external',
                openExternalSettings: (state, refs, detail) => calls.push({
                    type: 'open',
                    source: detail && detail.source,
                    bounds: state.subtitlePanelBounds,
                }),
                closeExternalSettings: (detail) => calls.push({
                    type: 'close',
                    source: detail && detail.source,
                }),
            });
            const display = document.getElementById('subtitle-display');
            const button = document.getElementById('subtitle-settings-btn');
            button.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const opened = {
                panelState: display.dataset.subtitlePanelState || '',
                expanded: button.getAttribute('aria-expanded'),
                calls: calls.slice(),
            };
            button.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const closed = {
                panelState: display.dataset.subtitlePanelState || '',
                expanded: button.getAttribute('aria-expanded'),
                calls: calls.slice(),
            };
            controller.destroy();
            return { opened, closed };
        }
        """
    )

    assert result["opened"]["panelState"] == "settings"
    assert result["opened"]["expanded"] == "true"
    assert result["opened"]["calls"] == [
            {
                "type": "open",
                "source": "subtitle-ui-panel",
                "bounds": {"width": 655, "height": 109},
            }
        ]
    assert result["closed"]["panelState"] == "controls"
    assert result["closed"]["expanded"] == "false"
    assert result["closed"]["calls"] == [
        result["opened"]["calls"][0],
        {"type": "close", "source": "subtitle-ui-panel"},
    ]


@pytest.mark.frontend
def test_subtitle_window_external_settings_closes_when_resize_or_drag_starts(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" style="display:flex;" data-subtitle-panel-state="controls">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <button type="button" id="subtitle-settings-btn" aria-expanded="false"></button>
            <div id="subtitle-settings-panel" class="hidden"></div>
            <div id="subtitle-resize-handles" aria-hidden="true">
                <span class="subtitle-resize-edge subtitle-resize-e" data-resize-dir="e"></span>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__subtitleSettingsOpenPayloads = [];
            window.__subtitleSettingsCloseCount = 0;
            window.__nativeResizeCalls = [];
            window.__dragCalls = [];
            window.nekoSubtitle = {
                setSize: () => {},
                setBounds: () => {},
                getBounds: () => Promise.resolve({ x: window.screenX || 100, y: window.screenY || 200, width: 612, height: 80 }),
                getCursorPoint: () => Promise.resolve({ x: 20, y: 20, screenX: 120, screenY: 220 }),
                getWorkArea: () => Promise.resolve({ x: 0, y: 0, width: 1000, height: 800 }),
                resizeStart: (direction) => window.__nativeResizeCalls.push({ type: 'start', direction }),
                resizeStop: () => window.__nativeResizeCalls.push({ type: 'stop' }),
                dragStart: () => window.__dragCalls.push('start'),
                dragStop: () => window.__dragCalls.push('stop'),
                openSettings: (payload) => window.__subtitleSettingsOpenPayloads.push(payload),
                closeSettings: () => { window.__subtitleSettingsCloseCount += 1; },
                updateSettingsWindow: () => {},
                changeSettings: () => {},
                enableInteraction: () => {},
                disableInteraction: () => {},
            };
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 600,
                height: 68,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const settingsBtn = document.getElementById('subtitle-settings-btn');
            const resizeHandle = document.querySelector('.subtitle-resize-e');

            settingsBtn.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const resizeRect = resizeHandle.getBoundingClientRect();
            resizeHandle.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: resizeRect.left + resizeRect.width / 2,
                clientY: resizeRect.top + resizeRect.height / 2,
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterResizeStart = {
                panelState: display.dataset.subtitlePanelState || '',
                closeCount: window.__subtitleSettingsCloseCount,
                openCount: window.__subtitleSettingsOpenPayloads.length,
                resizeCalls: window.__nativeResizeCalls.slice(),
            };
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 40));

            settingsBtn.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const rect = display.getBoundingClientRect();
            display.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: rect.left + rect.width / 2,
                clientY: rect.top + rect.height / 2,
            }));
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: rect.left + rect.width / 2 + 16,
                clientY: rect.top + rect.height / 2 + 4,
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterDragStart = {
                panelState: display.dataset.subtitlePanelState || '',
                closeCount: window.__subtitleSettingsCloseCount,
                openCount: window.__subtitleSettingsOpenPayloads.length,
                dragCalls: window.__dragCalls.slice(),
            };
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 0));

            settingsBtn.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            settingsBtn.click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterReopen = {
                panelState: display.dataset.subtitlePanelState || '',
                closeCount: window.__subtitleSettingsCloseCount,
                openCount: window.__subtitleSettingsOpenPayloads.length,
            };
            return { afterResizeStart, afterDragStart, afterReopen };
        }
        """
    )

    assert result["afterResizeStart"]["closeCount"] == 1
    assert result["afterResizeStart"]["openCount"] == 1
    assert result["afterResizeStart"]["panelState"] == "controls"
    assert result["afterResizeStart"]["resizeCalls"][0]["type"] == "start"
    assert result["afterDragStart"]["closeCount"] == 2
    assert result["afterDragStart"]["openCount"] == 2
    assert result["afterDragStart"]["panelState"] == "controls"
    assert result["afterDragStart"]["dragCalls"] == ["start"]
    assert result["afterReopen"] == {
        "panelState": "settings",
        "closeCount": 2,
        "openCount": 3,
    }


@pytest.mark.frontend
def test_subtitle_window_height_uses_content_bounds_not_dropdown_height(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <button type="button" id="subtitle-settings-btn"></button>
            <div id="subtitle-settings-panel" class="hidden">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="targetLang">语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option><option value="en">English</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="opacity">不透明度</span>
                    <input type="range" id="subtitle-opacity-slider" min="0" max="100" value="95">
                    <span id="subtitle-opacity-value">95%</span>
                </div>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__subtitleWindowSizes = [];
            window.__subtitleSettingsOpenPayloads = [];
            window.nekoSubtitle = {
                setSize: (width, height, options) => window.__subtitleWindowSizes.push({
                    width,
                    height,
                    panelBounds: options && options.panelBounds,
                }),
                openSettings: (payload) => window.__subtitleSettingsOpenPayloads.push(payload),
                closeSettings: () => {},
                updateSettingsWindow: () => {},
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const emptySize = window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1];
            window.dispatchEvent(new CustomEvent('neko-ws-transcript', {
                detail: {
                    transcript: '这是一段很长很长的翻译字幕，用来测试窗口高度会按内容增长，但是不会超过中号字幕的最大高度。'.repeat(8),
                    translated: true,
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const longSize = window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1];
            document.getElementById('subtitle-settings-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.dispatchEvent(new Event('resize'));
            for (let index = 0; index < 6; index += 1) {
                await new Promise((resolve) => requestAnimationFrame(resolve));
            }
            const panelOpenSize = window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1];
            const displayRect = document.getElementById('subtitle-display').getBoundingClientRect();
            const scrollRect = document.getElementById('subtitle-scroll').getBoundingClientRect();
            const settingsBtnRect = document.getElementById('subtitle-settings-btn').getBoundingClientRect();
            const panel = document.getElementById('subtitle-settings-panel');
            const displayStyle = getComputedStyle(document.getElementById('subtitle-display'));
            const scrollStyle = getComputedStyle(document.getElementById('subtitle-scroll'));
            const scrollThumbStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar-thumb');
            const scrollBarStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar');
            const scrollTrackStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar-track');
            const textStyle = getComputedStyle(document.getElementById('subtitle-text'));
            return {
                emptySize,
                longSize,
                panelOpenSize,
                externalSettingsOpened: window.__subtitleSettingsOpenPayloads.length,
                panelHidden: panel.classList.contains('hidden'),
                displayHeight: displayRect.height,
                scrollHeight: scrollRect.height,
                scrollRight: scrollRect.right,
                settingsBtnLeft: settingsBtnRect.left,
                hasDragHandle: !!document.getElementById('subtitle-drag-handle'),
                displayTop: displayRect.top,
                displayOverflow: displayStyle.overflowY,
                scrollOverflow: scrollStyle.overflowY,
                scrollPointerEvents: scrollStyle.pointerEvents,
                textPointerEvents: textStyle.pointerEvents,
                scrollBarWidth: scrollStyle.scrollbarWidth,
                scrollBarColor: scrollStyle.scrollbarColor,
                scrollBarGutter: scrollStyle.scrollbarGutter,
                webkitScrollBarWidth: scrollBarStyle.width,
                scrollTrackBackground: scrollTrackStyle.backgroundColor,
                scrollThumbBackground: scrollThumbStyle.backgroundColor,
                textMarginRight: textStyle.marginRight,
            };
        }
        """
    )

    assert result["emptySize"]["height"] == 121
    assert result["longSize"]["height"] == 121
    assert result["displayHeight"] == 109
    assert result["panelOpenSize"]["panelBounds"] == {"width": 655, "height": 109}
    assert result["panelOpenSize"]["height"] == result["displayHeight"] + 12
    assert result["externalSettingsOpened"] == 1
    assert result["panelHidden"] is True
    assert result["displayOverflow"] == "visible"
    assert result["scrollOverflow"] == "auto"
    assert result["scrollPointerEvents"] == "none"
    assert result["textPointerEvents"] == "auto"
    assert result["scrollRight"] <= result["settingsBtnLeft"] - 6
    assert result["hasDragHandle"] is False
    assert result["scrollBarWidth"] == "none"
    assert "rgba(0, 0, 0, 0)" in result["scrollBarColor"]
    assert result["scrollBarGutter"] == "auto"
    assert result["webkitScrollBarWidth"] == "0px"
    assert result["scrollTrackBackground"] == "rgba(0, 0, 0, 0)"
    assert result["scrollHeight"] <= result["displayHeight"] - 12
    assert result["scrollThumbBackground"] == "rgba(0, 0, 0, 0)"
    assert result["textMarginRight"] == "0px"


@pytest.mark.frontend
def test_subtitle_scroll_box_accepts_mouse_wheel_for_long_translation(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 360, "height": 120})
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
        path="/subtitle-scroll-wheel-harness",
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            shared.initSubtitleUI({ host: 'window' });
            shared.applySubtitlePanelBounds(document.getElementById('subtitle-display'), {
                width: 240,
                height: 60,
            }, { host: 'window' });
            const scroll = document.getElementById('subtitle-scroll');
            const text = document.getElementById('subtitle-text');
            text.textContent = Array.from({ length: 30 }, (_, index) => `line ${index + 1}`).join('\\n');
            await new Promise((resolve) => setTimeout(resolve, 0));

            scroll.scrollTop = 0;
            const scrollStyle = getComputedStyle(scroll);
            const textStyle = getComputedStyle(text);
            const wheelDown = new WheelEvent('wheel', {
                bubbles: true,
                cancelable: true,
                deltaY: 80,
            });
            const wheelDownResult = text.dispatchEvent(wheelDown);
            const afterDown = scroll.scrollTop;
            const wheelUp = new WheelEvent('wheel', {
                bubbles: true,
                cancelable: true,
                deltaY: -40,
            });
            const wheelUpResult = text.dispatchEvent(wheelUp);
            const afterUp = scroll.scrollTop;

            scroll.scrollTop = 0;
            const wheelPastTop = new WheelEvent('wheel', {
                bubbles: true,
                cancelable: true,
                deltaY: -80,
            });
            const wheelPastTopResult = text.dispatchEvent(wheelPastTop);
            const afterPastTop = scroll.scrollTop;

            scroll.scrollTop = scroll.scrollHeight - scroll.clientHeight;
            const beforePastBottom = scroll.scrollTop;
            const wheelPastBottom = new WheelEvent('wheel', {
                bubbles: true,
                cancelable: true,
                deltaY: 80,
            });
            const wheelPastBottomResult = text.dispatchEvent(wheelPastBottom);

            return {
                overflowY: scrollStyle.overflowY,
                scrollPointerEvents: scrollStyle.pointerEvents,
                textPointerEvents: textStyle.pointerEvents,
                scrollHeight: scroll.scrollHeight,
                clientHeight: scroll.clientHeight,
                maxScrollTop: scroll.scrollHeight - scroll.clientHeight,
                wheelDownResult,
                wheelDownPrevented: wheelDown.defaultPrevented,
                afterDown,
                wheelUpResult,
                wheelUpPrevented: wheelUp.defaultPrevented,
                afterUp,
                wheelPastTopResult,
                wheelPastTopPrevented: wheelPastTop.defaultPrevented,
                afterPastTop,
                beforePastBottom,
                wheelPastBottomResult,
                wheelPastBottomPrevented: wheelPastBottom.defaultPrevented,
                afterPastBottom: scroll.scrollTop,
            };
        }
        """
    )

    assert result["overflowY"] == "auto"
    assert result["scrollPointerEvents"] == "none"
    assert result["textPointerEvents"] == "auto"
    assert result["scrollHeight"] > result["clientHeight"]
    assert result["maxScrollTop"] > 0
    assert result["wheelDownResult"] is False
    assert result["wheelDownPrevented"] is True
    assert result["afterDown"] > 0
    assert result["wheelUpResult"] is False
    assert result["wheelUpPrevented"] is True
    assert result["afterUp"] < result["afterDown"]
    assert result["wheelPastTopResult"] is True
    assert result["wheelPastTopPrevented"] is False
    assert result["afterPastTop"] == 0
    assert result["wheelPastBottomResult"] is True
    assert result["wheelPastBottomPrevented"] is False
    assert result["afterPastBottom"] == result["beforePastBottom"]


@pytest.mark.frontend
def test_subtitle_overflow_auto_scroll_is_slow_and_wheel_cancels_it(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 360, "height": 120})
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
        </div>
        """,
        path="/subtitle-auto-scroll-harness",
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            shared.initSubtitleUI({ host: 'window' });
            shared.applySubtitlePanelBounds(document.getElementById('subtitle-display'), {
                width: 240,
                height: 60,
            }, { host: 'window' });
            const scroll = document.getElementById('subtitle-scroll');
            const text = document.getElementById('subtitle-text');
            text.textContent = Array.from({ length: 30 }, (_, index) => `line ${index + 1}`).join('\\n');
            await new Promise((resolve) => setTimeout(resolve, 0));

            const waitFrames = (count) => new Promise((resolve) => {
                const tick = () => {
                    count -= 1;
                    if (count <= 0) {
                        resolve();
                        return;
                    }
                    requestAnimationFrame(tick);
                };
                requestAnimationFrame(tick);
            });

            scroll.scrollTop = 0;
            const maxScrollTop = scroll.scrollHeight - scroll.clientHeight;
            shared.requestSubtitleAutoScroll(scroll, {
                speedPixelsPerSecond: 240,
                delayMs: 0,
            });
            await waitFrames(8);
            const afterAuto = scroll.scrollTop;
            const wheelUp = new WheelEvent('wheel', {
                bubbles: true,
                cancelable: true,
                deltaY: -999,
            });
            const wheelResult = text.dispatchEvent(wheelUp);
            const afterWheel = scroll.scrollTop;
            shared.requestSubtitleAutoScroll(scroll, {
                speedPixelsPerSecond: 240,
                delayMs: 0,
            });
            await waitFrames(8);
            const afterWheelWait = scroll.scrollTop;

            return {
                maxScrollTop,
                afterAuto,
                wheelResult,
                wheelPrevented: wheelUp.defaultPrevented,
                afterWheel,
                afterWheelWait,
                scrollableDataset: scroll.dataset.subtitleScrollable,
            };
        }
        """
    )

    assert result["maxScrollTop"] > 0
    assert 0 < result["afterAuto"] < result["maxScrollTop"]
    assert result["wheelResult"] is False
    assert result["wheelPrevented"] is True
    assert result["afterWheel"] == 0
    assert result["afterWheelWait"] == 0
    assert result["scrollableDataset"] == "true"


@pytest.mark.frontend
def test_subtitle_translation_write_path_starts_overflow_auto_scroll(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 360, "height": 120})
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible;">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
        path="/subtitle-write-auto-scroll-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleEnabled', 'true');
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 240,
                height: 60,
            }));
            window.waitForStorageLocationStartupBarrier = () => Promise.resolve();
            window.fetch = async (url) => {
                if (String(url).includes('/api/config/user_language')) {
                    return { json: async () => ({ success: true, language: 'en' }) };
                }
                if (String(url).includes('/api/translate')) {
                    return {
                        ok: true,
                        json: async () => ({
                            success: true,
                            translated_text: Array.from({ length: 30 }, (_, index) => `line ${index + 1}`).join('\\n'),
                            target_lang: 'en',
                        }),
                    };
                }
                throw new Error(`Unexpected fetch: ${url}`);
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const waitFrames = (count) => new Promise((resolve) => {
                const tick = () => {
                    count -= 1;
                    if (count <= 0) {
                        resolve();
                        return;
                    }
                    requestAnimationFrame(tick);
                };
                requestAnimationFrame(tick);
            });
            const scroll = document.getElementById('subtitle-scroll');
            scroll.scrollTop = 0;
            await window.subtitleBridge.finalizeTurnWithTranslation('A complete sentence.');
            await new Promise((resolve) => setTimeout(resolve, 0));
            const maxScrollTop = scroll.scrollHeight - scroll.clientHeight;
            await waitFrames(20);
            return {
                maxScrollTop,
                scrollTop: scroll.scrollTop,
                scrollableDataset: scroll.dataset.subtitleScrollable,
                textLength: document.getElementById('subtitle-text').textContent.length,
            };
        }
        """
    )

    assert result["textLength"] > 0
    assert result["maxScrollTop"] > 0
    assert result["scrollTop"] > 0
    assert result["scrollTop"] < result["maxScrollTop"]
    assert result["scrollableDataset"] == "true"


@pytest.mark.frontend
def test_subtitle_window_transcript_event_starts_overflow_auto_scroll(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 360, "height": 120})
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
        path="/subtitle-window-auto-scroll-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 240,
                height: 60,
            }));
            window.nekoSubtitle = {
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const waitFrames = (count) => new Promise((resolve) => {
                const tick = () => {
                    count -= 1;
                    if (count <= 0) {
                        resolve();
                        return;
                    }
                    requestAnimationFrame(tick);
                };
                requestAnimationFrame(tick);
            });
            const scroll = document.getElementById('subtitle-scroll');
            scroll.scrollTop = 0;
            window.dispatchEvent(new CustomEvent('neko-ws-transcript', {
                detail: {
                    translated: true,
                    transcript: Array.from({ length: 30 }, (_, index) => `line ${index + 1}`).join('\\n'),
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const maxScrollTop = scroll.scrollHeight - scroll.clientHeight;
            await waitFrames(20);
            return {
                maxScrollTop,
                scrollTop: scroll.scrollTop,
                scrollableDataset: scroll.dataset.subtitleScrollable,
            };
        }
        """
    )

    assert result["maxScrollTop"] > 0
    assert result["scrollTop"] > 0
    assert result["scrollTop"] < result["maxScrollTop"]
    assert result["scrollableDataset"] == "true"


@pytest.mark.frontend
def test_subtitle_window_danmaku_mode_suppresses_overflow_auto_scroll(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 360, "height": 120})
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
        path="/subtitle-window-danmaku-auto-scroll-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleDanmakuMode', 'true');
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 240,
                height: 60,
            }));
            window.nekoSubtitle = {
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const waitFrames = (count) => new Promise((resolve) => {
                const tick = () => {
                    count -= 1;
                    if (count <= 0) {
                        resolve();
                        return;
                    }
                    requestAnimationFrame(tick);
                };
                requestAnimationFrame(tick);
            });
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');
            const scroll = document.getElementById('subtitle-scroll');
            window.dispatchEvent(new CustomEvent('neko-ws-transcript', {
                detail: {
                    translated: true,
                    transcript: Array.from(
                        { length: 30 },
                        (_, index) => `line ${index + 1}, phrase ${index + 1}.`
                    ).join('\\n'),
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const maxScrollTop = scroll.scrollHeight - scroll.clientHeight;
            const afterRender = scroll.scrollTop;
            scroll.scrollTop = maxScrollTop;
            const wheelDuringDanmaku = new WheelEvent('wheel', {
                bubbles: true,
                cancelable: true,
                deltaY: 24,
            });
            scroll.dispatchEvent(wheelDuringDanmaku);
            const afterStateUpdate = scroll.scrollTop;
            shared.requestSubtitleAutoScroll(scroll, {
                speedPixelsPerSecond: 240,
                delayMs: 0,
            });
            await waitFrames(20);
            const afterAuto = scroll.scrollTop;
            const scrollToBottomReturn = shared.scrollSubtitleToBottom(scroll);
            const afterScrollToBottom = scroll.scrollTop;
            return {
                active: display.dataset.subtitleDanmakuActive || '',
                afterRender,
                afterAuto,
                afterStateUpdate,
                afterScrollToBottom,
                itemCount: document.querySelectorAll('.subtitle-danmaku-item').length,
                maxScrollTop,
                scrollableDataset: scroll.dataset.subtitleScrollable,
                scrollToBottomReturn,
                wheelPrevented: wheelDuringDanmaku.defaultPrevented,
            };
        }
        """
    )

    assert result["active"] == "true"
    assert result["itemCount"] > 0
    assert result["maxScrollTop"] > 0
    assert result["afterRender"] == 0
    assert result["afterStateUpdate"] == 0
    assert result["afterAuto"] == 0
    assert result["scrollToBottomReturn"] == 0
    assert result["afterScrollToBottom"] == 0
    assert result["wheelPrevented"] is False
    assert result["scrollableDataset"] == "false"


@pytest.mark.frontend
def test_subtitle_window_ignores_raw_transcript_after_translated_render_state(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <button type="button" id="subtitle-settings-btn"></button>
            <div id="subtitle-settings-panel" class="hidden">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="targetLang">语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option><option value="en">English</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="opacity">不透明度</span>
                    <input type="range" id="subtitle-opacity-slider" min="0" max="100" value="95">
                    <span id="subtitle-opacity-value">95%</span>
                </div>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.nekoSubtitle = {
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));

            window.dispatchEvent(new CustomEvent('neko-ws-transcript', {
                detail: { transcript: 'Translated subtitle text.', translated: true },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterTranslated = document.getElementById('subtitle-text').textContent;

            window.dispatchEvent(new CustomEvent('neko-ws-transcript', {
                detail: { transcript: 'Raw original transcript.' },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterRawTranscript = document.getElementById('subtitle-text').textContent;

            window.dispatchEvent(new CustomEvent('neko-ws-transcript', {
                detail: { transcript: '', translated: true },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterTranslatedClear = document.getElementById('subtitle-text').textContent;

            return { afterTranslated, afterRawTranscript, afterTranslatedClear };
        }
        """
    )

    assert result["afterTranslated"] == "Translated subtitle text."
    assert result["afterRawTranscript"] == "Translated subtitle text."
    assert result["afterTranslatedClear"] == ""


@pytest.mark.frontend
def test_subtitle_window_danmaku_mode_renders_translated_text_as_scrolling_items(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
        path="/subtitle-window-danmaku-render-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitleDanmakuMode', 'true');
            window.nekoSubtitle = {
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));

            window.dispatchEvent(new CustomEvent('neko-ws-transcript', {
                detail: {
                    transcript: '第一段，第二段，第三段。第四段！第五段？',
                    translated: true,
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));

            return {
                text: document.getElementById('subtitle-text').textContent,
                active: document.getElementById('subtitle-display').dataset.subtitleDanmakuActive || '',
                items: Array.from(document.querySelectorAll('.subtitle-danmaku-item'))
                    .map((item) => ({
                        index: Number(item.dataset.subtitleDanmakuIndex),
                        text: item.textContent,
                    }))
                    .sort((a, b) => a.index - b.index)
                    .map((item) => item.text),
            };
        }
        """
    )

    assert result["text"] == "第一段，第二段，第三段。第四段！第五段？"
    assert result["active"] == "true"
    assert result["items"] == ["第一段，第二段，", "第三段。第四段！", "第五段？"]


@pytest.mark.frontend
def test_subtitle_window_native_passthrough_toggles_by_cursor_position(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 360, "height": 80})
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated subtitle.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__subtitleInteractionCalls = [];
            window.__cursorPoint = { x: 20, y: 18, screenX: 120, screenY: 138 };
            window.nekoSubtitle = {
                getBounds: () => Promise.resolve({ x: 100, y: 120, width: 360, height: 80 }),
                getCursorPoint: () => Promise.resolve(window.__cursorPoint),
                enableInteraction: () => window.__subtitleInteractionCalls.push('enable'),
                disableInteraction: () => window.__subtitleInteractionCalls.push('disable'),
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
            };
            window.localStorage.setItem('subtitleInteractionPassthrough', 'true');
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 360,
                height: 80,
            }));
            document.getElementById('subtitle-text').textContent = Array.from(
                { length: 20 },
                (_, index) => `line ${index + 1}`
            ).join('\\n');
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const text = document.getElementById('subtitle-text');
            const textRect = text.getClientRects()[0] || text.getBoundingClientRect();
            const textPoint = {
                x: Math.round(textRect.left + textRect.width / 2),
                y: Math.round(textRect.top + textRect.height / 2),
            };
            await new Promise((resolve) => setTimeout(resolve, 120));
            const afterTransparentArea = window.__subtitleInteractionCalls.slice();
            window.__cursorPoint = {
                x: textPoint.x,
                y: textPoint.y,
                screenX: 100 + textPoint.x,
                screenY: 120 + textPoint.y,
            };
            await new Promise((resolve) => setTimeout(resolve, 120));
            const afterText = window.__subtitleInteractionCalls.slice();
            window.__cursorPoint = { x: 20, y: 18, screenX: 120, screenY: 138 };
            await new Promise((resolve) => setTimeout(resolve, 120));
            const afterTransparentAgain = window.__subtitleInteractionCalls.slice();
            window.nekoSubtitleShared.updateSettings({
                subtitlePanelLocked: false,
            }, { source: 'test-disable-passthrough' });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterDisabled = window.__subtitleInteractionCalls.slice();
            return { afterTransparentArea, afterText, afterTransparentAgain, afterDisabled };
        }
        """
    )

    assert result["afterTransparentArea"] == ["disable"]
    assert result["afterText"] == ["disable"]
    assert result["afterTransparentAgain"] == ["disable"]
    assert result["afterDisabled"] == ["disable", "enable"]


@pytest.mark.frontend
def test_subtitle_window_passthrough_poll_matches_desktop_chat_latency():
    script = (PROJECT_ROOT / "static/subtitle/subtitle-window.js").read_text(encoding="utf-8")

    # The responsive cadence near the panel still matches the desktop chat passthrough
    # poll (16ms) so interaction latency stays imperceptible where the cursor can act.
    assert "var INTERACTION_PASSTHROUGH_POLL_MS = 16;" in script
    # Idle backoff: while the cursor is parked away from the panel the poll relaxes so a
    # visible subtitle doesn't drive a 60Hz bridge round-trip that never changes state.
    assert "var INTERACTION_PASSTHROUGH_IDLE_POLL_MS = 96;" in script
    assert "var INTERACTION_PASSTHROUGH_NEAR_MARGIN = 64;" in script
    assert "computeNextInteractionPollDelay" in script
    assert "scheduleInteractionPoll(computeNextInteractionPollDelay(" in script
    # Proximity reuses the hit-test's page-space conversion, so the idle backoff engages
    # for both the {screenX,screenY} and the {x,y} cursor-point shapes the bridge returns.
    assert "cursorPointToPagePoint(point, bounds)" in script
    # Relaxed cadence ramps in from the responsive rate rather than hard-jumping to the
    # ceiling, so a cursor that only just left the panel stays responsive to a return.
    assert "interactionFarStreak" in script
    # Never regress to a fixed slow poll for the responsive path.
    assert "setInterval(updateNativeInteractionPassthrough, 80)" not in script


@pytest.mark.frontend
def test_subtitle_window_passthrough_poll_backs_off_when_cursor_is_far(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 360, "height": 80})
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display" data-subtitle-panel-state="clean">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated subtitle.</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.__pollCount = 0;
            window.__cursorPoint = { x: 20, y: 18, screenX: 120, screenY: 138 };
            window.nekoSubtitle = {
                getBounds: () => Promise.resolve({ x: 100, y: 120, width: 360, height: 80 }),
                getCursorPoint: () => {
                    window.__pollCount += 1;
                    return Promise.resolve(window.__cursorPoint);
                },
                enableInteraction: () => {},
                disableInteraction: () => {},
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
            };
            window.localStorage.setItem('subtitleInteractionPassthrough', 'true');
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 360,
                height: 80,
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            const WINDOW_MS = 360;
            // Settle past the 16->32->64->96ms ramp so we measure the steady cadence.
            const SETTLE_MS = 150;
            async function measure(cursor) {
                window.__cursorPoint = cursor;
                window.dispatchEvent(new Event('focus'));
                await new Promise((resolve) => setTimeout(resolve, SETTLE_MS));
                window.__pollCount = 0;
                await new Promise((resolve) => setTimeout(resolve, WINDOW_MS));
                return window.__pollCount;
            }
            // Cursor parked far outside the panel bounds -> relaxed idle cadence.
            const farScreen = await measure({ x: 1880, y: 1900, screenX: 1980, screenY: 2020 });
            // Cursor over the panel -> responsive 16ms cadence.
            const near = await measure({ x: 20, y: 18, screenX: 120, screenY: 138 });
            // Cursor-point shape with only window-local {x, y} (no screen coords), far
            // from the panel -> must still relax (idle backoff keys off page-space coords).
            const farLocalOnly = await measure({ x: 1880, y: 1900 });
            return { farScreen, near, farLocalOnly };
        }
        """
    )

    # Over a 360ms window: ~4 polls at the 96ms idle cadence when far, ~22 at 16ms when
    # near. Use generous bounds to stay robust against headless timer jitter.
    assert result["farScreen"] <= 8
    # {x, y}-only cursor must back off too, not fall through to the responsive path.
    assert result["farLocalOnly"] <= 8
    assert result["near"] >= 10
    assert result["near"] >= result["farScreen"] * 2
    assert result["near"] >= result["farLocalOnly"] * 2


@pytest.mark.frontend
def test_launcher_packages_top_level_static_html_files():
    launcher_spec = (PROJECT_ROOT / "specs/launcher.spec").read_text(encoding="utf-8")

    assert "add_data('static/*.html', 'static')" in launcher_spec


@pytest.mark.frontend
def test_subtitle_shared_cleanup_and_owner_guard_contracts():
    shared_script = (PROJECT_ROOT / "static/subtitle/subtitle-shared.js").read_text(encoding="utf-8")
    subtitle_script = (PROJECT_ROOT / "static/subtitle/subtitle.js").read_text(encoding="utf-8")
    subtitle_window_script = (PROJECT_ROOT / "static/subtitle/subtitle-window.js").read_text(encoding="utf-8")
    show_block = subtitle_script.split("function showSubtitleWithoutOriginalAndRestartCurrentTurn()", 1)[1].split(
        "if (currentTurnIsStructured)",
        1,
    )[0]
    host_apply_block = subtitle_script.split("onSettingsApplied: function(state, refs, detail)", 1)[1].split(
        "syncSubtitleRenderState",
        1,
    )[0]

    assert "width = Math.max(MIN_PANEL_WIDTH, Math.min(node.offsetWidth + 8, maxWidth));" in shared_script
    assert "if (refs.settingsBtn)" in shared_script
    assert "if (refs.settingsBtn && refs.settingsPanel)" not in shared_script
    assert "var windowEdgeInset = host === 'window' ? Math.max(0, Number(options && options.windowEdgeInset) || 0) : 0;" in shared_script
    assert "result.bounds.width + windowEdgeInset * 2" in shared_script
    assert "result.bounds.height + windowEdgeInset * 2" in shared_script
    assert "handleMouseUp();" in shared_script
    assert "stopDrag();" in shared_script
    assert "document.body.style.userSelect = '';" in shared_script
    assert "document.body.style.cursor = '';" in shared_script
    assert "refs.display.classList.remove('resizing');" in shared_script
    assert "if (!isSubtitleTranslationOwner())" in show_block
    assert "subtitle-non-owner-skip-show" in show_block
    assert "detail.source === 'subtitle-ui-resize'" in host_apply_block
    assert "writeSubtitleText(refs.text.textContent);" in host_apply_block
    assert "if (uiOptions.windowInteractions === 'external') {\n            desktopWindowInteractionsCleanup = attachDesktopWindowInteractions(subtitleWindowController);\n        }" in subtitle_window_script
    assert "function getEventScreenPoint(e)" in subtitle_window_script
    assert "function pushNativeResizeCursor(e)" in subtitle_window_script
    assert "if (!api || typeof api.resizeMove !== 'function') return;" in subtitle_window_script
    assert "if (point) api.resizeMove(point);" in subtitle_window_script
    assert "pushNativeResizeCursor(e);" in subtitle_window_script
    assert "pushNativeResizeCursor(e.touches[0]);" in subtitle_window_script
    assert "cursor: getEventScreenPoint(e)" in subtitle_window_script
    assert "windowEdgeInset: DESKTOP_WINDOW_EDGE_INSET" in subtitle_window_script


@pytest.mark.frontend
def test_web_subtitle_settings_panel_does_not_overlap_subtitle_text(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible;">
            <div id="subtitle-scroll"><span id="subtitle-text"></span></div>
            <button type="button" id="subtitle-settings-btn"></button>
            <div id="subtitle-settings-panel" class="hidden">
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="targetLang">语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option><option value="en">English</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="opacity">不透明度</span>
                    <input type="range" id="subtitle-opacity-slider" min="0" max="100" value="95">
                    <span id="subtitle-opacity-value">95%</span>
                </div>
            </div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {}
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            shared.initSubtitleUI({ host: 'web' });
            shared.applySubtitlePanelBounds(document.getElementById('subtitle-display'), {
                width: 600,
                height: 68,
            }, { host: 'web' });
            document.getElementById('subtitle-text').textContent =
                'Hmph, you persistent idiot. You and now you are hooked, huh?';
            document.getElementById('subtitle-settings-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const scrollRect = document.getElementById('subtitle-scroll').getBoundingClientRect();
            const settingsBtnRect = document.getElementById('subtitle-settings-btn').getBoundingClientRect();
            const panelRect = document.getElementById('subtitle-settings-panel').getBoundingClientRect();
            const displayStyle = getComputedStyle(document.getElementById('subtitle-display'));
            const scrollStyle = getComputedStyle(document.getElementById('subtitle-scroll'));
            const scrollThumbStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar-thumb');
            const scrollBarStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar');
            const scrollTrackStyle = getComputedStyle(document.getElementById('subtitle-scroll'), '::-webkit-scrollbar-track');
            const textStyle = getComputedStyle(document.getElementById('subtitle-text'));
            return {
                scrollTop: scrollRect.top,
                scrollBottom: scrollRect.bottom,
                scrollRight: scrollRect.right,
                settingsBtnLeft: settingsBtnRect.left,
                hasDragHandle: !!document.getElementById('subtitle-drag-handle'),
                panelTop: panelRect.top,
                panelBottom: panelRect.bottom,
                overlapsVertically: panelRect.bottom > scrollRect.top && panelRect.top < scrollRect.bottom,
                panelHidden: document.getElementById('subtitle-settings-panel').classList.contains('hidden'),
                displayOverflow: displayStyle.overflowY,
                scrollOverflow: scrollStyle.overflowY,
                scrollPointerEvents: scrollStyle.pointerEvents,
                textPointerEvents: textStyle.pointerEvents,
                scrollBarWidth: scrollStyle.scrollbarWidth,
                scrollBarColor: scrollStyle.scrollbarColor,
                scrollBarGutter: scrollStyle.scrollbarGutter,
                webkitScrollBarWidth: scrollBarStyle.width,
                scrollTrackBackground: scrollTrackStyle.backgroundColor,
                scrollThumbBackground: scrollThumbStyle.backgroundColor,
                textMarginRight: textStyle.marginRight,
            };
        }
        """
    )

    assert result["panelHidden"] is False
    assert result["overlapsVertically"] is False
    assert result["displayOverflow"] == "visible"
    assert result["scrollOverflow"] == "auto"
    assert result["scrollPointerEvents"] == "none"
    assert result["textPointerEvents"] == "auto"
    assert result["scrollRight"] <= result["settingsBtnLeft"] - 6
    assert result["hasDragHandle"] is False
    assert result["scrollBarWidth"] == "none"
    assert "rgba(0, 0, 0, 0)" in result["scrollBarColor"]
    assert result["scrollBarGutter"] == "auto"
    assert result["webkitScrollBarWidth"] == "0px"
    assert result["scrollTrackBackground"] == "rgba(0, 0, 0, 0)"
    assert result["scrollThumbBackground"] == "rgba(0, 0, 0, 0)"
    assert result["textMarginRight"] == "0px"


@pytest.mark.frontend
def test_subtitle_panel_controls_scale_up_from_default_bounds_without_shrinking(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 1200, "height": 800})
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" data-subtitle-panel-state="controls" style="display:flex; opacity:1; visibility:visible;">
            <div id="subtitle-scroll"><span id="subtitle-text">可缩放字幕</span></div>
            <div id="subtitle-panel-controls" aria-hidden="false">
                <button type="button" id="subtitle-lock-btn" class="subtitle-panel-control-btn"></button>
                <button type="button" id="subtitle-settings-btn" class="subtitle-panel-control-btn"></button>
                <button type="button" id="subtitle-close-btn" class="subtitle-panel-control-btn"></button>
            </div>
        </div>
        """,
        path="/subtitle-controls-scale-harness",
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');
            const controls = document.getElementById('subtitle-panel-controls');
            const button = document.getElementById('subtitle-settings-btn');
            display.style.animation = 'none';
            controls.style.transition = 'none';
            button.style.transition = 'none';

            function snapshot(bounds) {
                shared.applySubtitlePanelBounds(display, bounds, { host: 'web' });
                const rect = button.getBoundingClientRect();
                return {
                    scale: getComputedStyle(display).getPropertyValue('--subtitle-control-scale').trim(),
                    dataset: display.dataset.subtitleControlScale,
                    controlsTransform: getComputedStyle(controls).transform,
                    buttonWidth: Math.round(rect.width),
                    buttonHeight: Math.round(rect.height),
                };
            }

            return {
                small: snapshot({ width: 420, height: 52 }),
                normal: snapshot({ width: 655, height: 109 }),
                large: snapshot({ width: 983, height: 164 }),
                huge: snapshot({ width: 1800, height: 320 }),
            };
        }
        """
    )

    assert result["small"]["scale"] == "1"
    assert result["small"]["dataset"] == "1"
    assert result["small"]["buttonWidth"] == 22
    assert result["small"]["buttonHeight"] == 22
    assert result["normal"]["scale"] == "1"
    assert result["normal"]["buttonWidth"] == 22
    assert result["large"]["scale"] == "1.5"
    assert result["large"]["buttonWidth"] == 33
    assert result["large"]["buttonHeight"] == 33
    assert result["large"]["controlsTransform"] != "none"
    assert result["huge"]["scale"] == "2"
    assert result["huge"]["dataset"] == "2"
    assert result["huge"]["buttonWidth"] == 44
    assert result["huge"]["buttonHeight"] == 44


@pytest.mark.frontend
def test_web_subtitle_panel_drag_persists_position_and_lock_blocks_drag(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 900, "height": 600})
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible; width:260px; min-height:80px;">
            <div id="subtitle-scroll"><span id="subtitle-text">可拖动字幕</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitlePanelLocked', 'false');
            window.localStorage.setItem('subtitlePanelBounds', JSON.stringify({
                width: 260,
                height: 80,
            }));
            window.localStorage.setItem('subtitlePanelPosition', JSON.stringify({
                left: 320,
                top: 220,
                coordinateSpace: 'viewport',
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const dragTarget = document.getElementById('subtitle-text');

            function rectSnapshot() {
                const rect = display.getBoundingClientRect();
                return {
                    left: Math.round(rect.left),
                    top: Math.round(rect.top),
                };
            }

            async function dragBy(dx, dy) {
                const before = rectSnapshot();
                dragTarget.dispatchEvent(new MouseEvent('mousedown', {
                    bubbles: true,
                    button: 0,
                    clientX: before.left + 30,
                    clientY: before.top + 24,
                }));
                document.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles: true,
                    clientX: before.left + 30 + dx,
                    clientY: before.top + 24 + dy,
                }));
                const draggingDuringMove = display.classList.contains('dragging');
                document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                await new Promise((resolve) => setTimeout(resolve, 0));
                return {
                    before,
                    after: rectSnapshot(),
                    draggingDuringMove,
                    stored: JSON.parse(window.localStorage.getItem('subtitlePanelPosition')),
                    settings: shared.getSettings().subtitlePanelPosition,
                };
            }

            const pointerEvents = getComputedStyle(display).pointerEvents;
            const textPointerEvents = getComputedStyle(dragTarget).pointerEvents;
            const firstDrag = await dragBy(42, 27);
            shared.updateSettings({ subtitlePanelLocked: true }, { source: 'lock-test' });
            const lockedDrag = await dragBy(60, 35);
            shared.updateSettings({ subtitlePanelLocked: false }, { source: 'unlock-test' });
            const secondDrag = await dragBy(18, 12);
            controller.destroy();

            return {
                pointerEvents,
                textPointerEvents,
                hasDragHandle: !!document.getElementById('subtitle-drag-handle'),
                firstDrag,
                lockedDrag,
                secondDrag,
            };
        }
        """
    )

    assert result["pointerEvents"] == "auto"
    assert result["textPointerEvents"] == "auto"
    assert result["hasDragHandle"] is False
    assert result["firstDrag"]["draggingDuringMove"] is True
    assert result["firstDrag"]["after"]["left"] - result["firstDrag"]["before"]["left"] == 42
    assert result["firstDrag"]["after"]["top"] - result["firstDrag"]["before"]["top"] == 27
    assert result["firstDrag"]["stored"] == result["firstDrag"]["settings"]
    assert result["lockedDrag"]["draggingDuringMove"] is False
    assert result["lockedDrag"]["after"] == result["lockedDrag"]["before"]
    assert result["lockedDrag"]["stored"] == result["firstDrag"]["stored"]
    assert result["secondDrag"]["draggingDuringMove"] is True
    assert result["secondDrag"]["after"]["left"] - result["secondDrag"]["before"]["left"] == 18
    assert result["secondDrag"]["after"]["top"] - result["secondDrag"]["before"]["top"] == 12

    mock_page.reload()
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    reopened = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const rect = display.getBoundingClientRect();
            const stored = JSON.parse(window.localStorage.getItem('subtitlePanelPosition'));
            controller.destroy();
            return {
                left: Math.round(rect.left),
                top: Math.round(rect.top),
                stored,
            };
        }
        """
    )

    assert abs(reopened["left"] - result["secondDrag"]["stored"]["left"]) <= 1
    assert abs(reopened["top"] - result["secondDrag"]["stored"]["top"]) <= 1
    assert reopened["stored"] == result["secondDrag"]["stored"]


@pytest.mark.frontend
def test_web_subtitle_panel_position_clamps_to_viewport_on_open_and_resize(
    mock_page: Page,
):
    mock_page.set_viewport_size({"width": 640, "height": 360})
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
            <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible; width:260px; min-height:80px;">
            <div id="subtitle-scroll"><span id="subtitle-text">可拖动字幕</span></div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
        path="/subtitle-clamp-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.localStorage.setItem('subtitlePanelPosition', JSON.stringify({
                left: 9999,
                top: 9999,
                coordinateSpace: 'viewport',
            }));
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    initial = mock_page.evaluate(
        """
        async () => {
            const controller = window.nekoSubtitleShared.initSubtitleUI({ host: 'web' });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const rect = display.getBoundingClientRect();
            const stored = JSON.parse(window.localStorage.getItem('subtitlePanelPosition'));
            return {
                rect: {
                    left: Math.round(rect.left),
                    top: Math.round(rect.top),
                    right: Math.round(rect.right),
                    bottom: Math.round(rect.bottom),
                },
                stored,
                viewport: { width: window.innerWidth, height: window.innerHeight },
            };
        }
        """
    )

    assert initial["rect"]["right"] <= initial["viewport"]["width"]
    assert initial["rect"]["bottom"] <= initial["viewport"]["height"]
    assert round(initial["stored"]["left"]) == initial["rect"]["left"]
    assert round(initial["stored"]["top"]) == initial["rect"]["top"]

    mock_page.set_viewport_size({"width": 360, "height": 220})
    resized = mock_page.evaluate(
        """
        async () => {
            window.dispatchEvent(new Event('resize'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const display = document.getElementById('subtitle-display');
            const rect = display.getBoundingClientRect();
            const stored = JSON.parse(window.localStorage.getItem('subtitlePanelPosition'));
            return {
                rect: {
                    left: Math.round(rect.left),
                    top: Math.round(rect.top),
                    right: Math.round(rect.right),
                    bottom: Math.round(rect.bottom),
                },
                stored,
                viewport: { width: window.innerWidth, height: window.innerHeight },
            };
        }
        """
    )

    assert resized["rect"]["left"] >= 0
    assert resized["rect"]["top"] >= 0
    assert resized["rect"]["right"] <= resized["viewport"]["width"]
    assert resized["rect"]["bottom"] <= resized["viewport"]["height"]
    assert round(resized["stored"]["left"]) == resized["rect"]["left"]
    assert round(resized["stored"]["top"]) == resized["rect"]["top"]


@pytest.mark.frontend
def test_window_subtitle_drag_bridge_respects_panel_lock(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
        path="/subtitle-window-drag-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.__dragCalls = [];
            window.nekoSubtitle = {
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => window.__dragCalls.push('start'),
                dragStop: () => window.__dragCalls.push('stop'),
            };
            window.localStorage.setItem('subtitlePanelLocked', 'false');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({
                host: 'window',
                api: window.nekoSubtitle,
            });
            const display = document.getElementById('subtitle-display');
            const controls = document.getElementById('subtitle-panel-controls');

            display.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: 20,
                clientY: 20,
            }));
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));

            shared.updateSettings({ subtitlePanelLocked: true }, {
                source: 'lock-test',
            });
            await new Promise((resolve) => setTimeout(resolve, 0));
            display.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: 20,
                clientY: 20,
            }));
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));

            shared.updateSettings({ subtitlePanelLocked: false }, {
                source: 'unlock-test',
            });
            await new Promise((resolve) => setTimeout(resolve, 0));
            controls.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: 20,
                clientY: 20,
            }));
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            controller.destroy();

            return {
                dragCalls: window.__dragCalls,
                locked: shared.getSettings().subtitlePanelLocked,
            };
        }
        """
    )

    assert result["dragCalls"] == ["start", "stop"]
    assert result["locked"] is False


@pytest.mark.frontend
def test_subtitle_window_state_sync_lock_blocks_drag_bridge(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-window-host",
        """
        <div id="subtitle-display">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
            <div id="subtitle-panel-controls" aria-hidden="true">
                <button type="button" id="subtitle-lock-btn"></button>
                <button type="button" id="subtitle-settings-btn"></button>
                <button type="button" id="subtitle-close-btn"></button>
            </div>
            <div id="subtitle-settings-panel" class="hidden"></div>
        </div>
        """,
        path="/subtitle-window-sync-lock-harness",
    )
    mock_page.evaluate(
        """
        () => {
            window.__dragCalls = [];
            window.nekoSubtitle = {
                setSize: () => {},
                changeSettings: () => {},
                dragStart: () => window.__dragCalls.push('start'),
                dragStop: () => window.__dragCalls.push('stop'),
            };
            window.localStorage.setItem('subtitlePanelLocked', 'false');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle/subtitle-window.js"))

    result = mock_page.evaluate(
        """
        async () => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');

            window.dispatchEvent(new CustomEvent('neko-subtitle-state-sync', {
                detail: { locked: true },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            display.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: 20,
                clientY: 20,
            }));
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: 36,
                clientY: 24,
            }));
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            const afterLockedDrag = window.__dragCalls.slice();
            const afterLockedState = {
                locked: shared.getSettings().subtitlePanelLocked,
                passthrough: shared.getSettings().subtitleInteractionPassthrough,
            };

            window.dispatchEvent(new CustomEvent('neko-subtitle-state-sync', {
                detail: { subtitlePanelLocked: false },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            display.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                button: 0,
                clientX: 20,
                clientY: 20,
            }));
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: 36,
                clientY: 24,
            }));
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));

            return {
                afterLockedDrag,
                afterLockedState,
                finalDragCalls: window.__dragCalls,
                locked: shared.getSettings().subtitlePanelLocked,
                passthrough: shared.getSettings().subtitleInteractionPassthrough,
            };
        }
        """
    )

    assert result["afterLockedDrag"] == []
    assert result["afterLockedState"] == {"locked": True, "passthrough": True}
    assert result["finalDragCalls"] == ["start", "stop"]
    assert result["locked"] is False
    assert result["passthrough"] is False
