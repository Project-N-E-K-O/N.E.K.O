from pathlib import Path

import pytest
from playwright.sync_api import Page


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        async () => {
            const shared = window.nekoSubtitleShared;
            const controller = shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const snapshot = () => {
                const style = getComputedStyle(display);
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
    assert result["dark"]["color"] == "rgb(244, 246, 248)"
    assert result["light"]["inlineBackground"] == ""
    assert result["light"]["backgroundColor"] == "rgba(250, 250, 247, 0.8)"
    assert result["light"]["backgroundImage"] == "none"
    assert result["light"]["color"] == "rgb(32, 36, 40)"
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
            window.localStorage.setItem('subtitleInteractionPassthrough', 'false');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
                subtitleInteractionPassthrough: true,
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
    assert result["before"]["subtitleInteractionPassthrough"] is False
    assert "subtitleDragAnywhere" not in result["before"]
    assert "subtitleSize" not in result["before"]
    assert "subtitlePanelScale" not in result["before"]
    assert result["renderBefore"]["subtitlePanelBounds"] == result["before"]["subtitlePanelBounds"]
    assert result["renderBefore"]["subtitlePanelPosition"] == result["before"]["subtitlePanelPosition"]
    assert result["renderBefore"]["subtitlePanelLocked"] is True
    assert result["renderBefore"]["subtitleInteractionPassthrough"] is False
    assert result["renderBefore"]["subtitlePanelState"] == "clean"
    assert result["after"]["subtitlePanelPosition"] == {
        "left": 44,
        "top": 88,
        "coordinateSpace": "viewport",
    }
    assert result["after"]["subtitlePanelLocked"] is False
    assert result["after"]["subtitleInteractionPassthrough"] is True
    assert result["renderAfter"]["subtitlePanelPosition"] == result["after"]["subtitlePanelPosition"]
    assert result["renderAfter"]["subtitlePanelLocked"] is False
    assert result["renderAfter"]["subtitleInteractionPassthrough"] is True
    assert result["storedBounds"] == '{"width":720,"height":96}'
    assert result["storedPosition"] == '{"left":44,"top":88,"coordinateSpace":"viewport"}'
    assert result["storedLocked"] == "false"
    assert result["storedPassthrough"] == "true"
    assert result["legacyDragAnywhere"] == "true"
    assert result["legacySize"] == "large"
    assert result["legacyScale"] == "133"
    assert result["events"] == [
        {
            "changedKeys": ["subtitlePanelPosition", "subtitlePanelLocked", "subtitleInteractionPassthrough"],
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
            "passthrough": True,
        }
    ]


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
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
            const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
            const waitForControlsDelay = () => new Promise((resolve) => setTimeout(resolve, 1250));
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
    assert result["afterFirstEscape"]["dataset"] == "controls"
    assert result["afterFirstEscape"]["settingsHidden"] is True
    assert result["afterSecondEscape"]["dataset"] == "clean"


@pytest.mark.frontend
def test_web_subtitle_transparent_area_passes_through_while_text_stays_interactive(
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
            <div id="subtitle-settings-panel" class="hidden">
                <label class="subtitle-settings-switch">
                    <input type="checkbox" id="subtitle-passthrough-toggle" checked>
                    <span class="subtitle-settings-track"></span>
                </label>
            </div>
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
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

    initial = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            window.__subtitleController = shared.initSubtitleUI({ host: 'web' });
            const display = document.getElementById('subtitle-display');
            const text = document.getElementById('subtitle-text');
            const displayRect = display.getBoundingClientRect();
            const textRect = text.getBoundingClientRect();
            const transparentPoint = {
                x: Math.round(displayRect.left + 18),
                y: Math.round(displayRect.top + 18),
            };
            return {
                displayPointerEvents: getComputedStyle(display).pointerEvents,
                textPointerEvents: getComputedStyle(text).pointerEvents,
                passthroughDataset: display.dataset.subtitleInteractionPassthrough,
                toggleChecked: document.getElementById('subtitle-passthrough-toggle').checked,
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
    mock_page.mouse.move(initial["transparentPoint"]["x"], initial["transparentPoint"]["y"])
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
    after_toggle_off = mock_page.evaluate(
        """
        (point) => {
            const display = document.getElementById('subtitle-display');
            const toggle = document.getElementById('subtitle-passthrough-toggle');
            toggle.checked = false;
            toggle.dispatchEvent(new Event('change', { bubbles: true }));
            const hit = document.elementFromPoint(point.x, point.y);
            const result = {
                displayPointerEvents: getComputedStyle(display).pointerEvents,
                passthroughDataset: display.dataset.subtitleInteractionPassthrough,
                toggleChecked: toggle.checked,
                storedPassthrough: window.localStorage.getItem('subtitleInteractionPassthrough'),
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
    assert initial["textPointerEvents"] == "auto"
    assert initial["passthroughDataset"] == "true"
    assert initial["toggleChecked"] is True
    assert initial["transparentHitId"] == "underlay-target"
    assert initial["textHitId"] == "subtitle-text"
    assert after_text_hover == {
        "panelState": "controls",
        "controlsHidden": "false",
    }
    assert after_leave_delay == {
        "panelState": "clean",
        "controlsHidden": "true",
        "transparentHitId": "underlay-target",
    }
    assert after_toggle_off == {
        "displayPointerEvents": "auto",
        "passthroughDataset": "false",
        "toggleChecked": False,
        "storedPassthrough": "false",
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
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
                storedLocked: window.localStorage.getItem('subtitlePanelLocked'),
                ariaPressed: lockBtn.getAttribute('aria-pressed'),
                renderLocked: shared.getRenderState().subtitlePanelLocked,
                panelState: display.dataset.subtitlePanelState,
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
            return { afterLock, afterClose };
        }
        """
    )

    assert result["afterLock"] == {
        "locked": True,
        "storedLocked": "true",
        "ariaPressed": "true",
        "renderLocked": True,
        "panelState": "controls",
        "propagated": [{"type": "lock", "value": True}],
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
        "propagated": [{"type": "toggle", "value": False, "enabled": False}],
    }


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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle.js"))

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
@pytest.mark.parametrize(
    "template_name",
    ["index.html", "chat.html", "subtitle.html"],
)
def test_subtitle_templates_share_new_panel_control_scaffold(
    template_name: str,
):
    template = (PROJECT_ROOT / "templates" / template_name).read_text(encoding="utf-8")

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
    assert 'data-subtitle-label="opacity">背景不透明度</span>' in template
    assert 'id="subtitle-opacity-slider" min="0" max="100"' in template
    assert 'data-subtitle-label="passthroughInteraction"' in template
    assert 'id="subtitle-passthrough-toggle"' in template
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
    assert result["before"]["fillColor"] == "rgb(31, 36, 41)"
    assert result["before"]["strokeColor"] == "rgba(255, 255, 255, 0.62)"
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
                    <span class="subtitle-settings-label" data-subtitle-label="targetLang">目标语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option><option value="en">English</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="opacity">背景不透明度</span>
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
            window.nekoSubtitle = {
                setSize: (width, height) => window.__subtitleWindowSizes.push({ width, height }),
                changeSettings: () => {},
                dragStart: () => {},
                dragStop: () => {},
            };
        }
        """
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
            const panelOpenSize = window.__subtitleWindowSizes[window.__subtitleWindowSizes.length - 1];
            const displayRect = document.getElementById('subtitle-display').getBoundingClientRect();
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
                emptySize,
                longSize,
                panelOpenSize,
                displayHeight: displayRect.height,
                scrollHeight: scrollRect.height,
                scrollRight: scrollRect.right,
                settingsBtnLeft: settingsBtnRect.left,
                hasDragHandle: !!document.getElementById('subtitle-drag-handle'),
                panelBottom: panelRect.bottom,
                overlapsVertically: panelRect.bottom > scrollRect.top && panelRect.top < scrollRect.bottom,
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

    assert result["emptySize"]["height"] == 68
    assert result["longSize"]["height"] == 68
    assert result["panelOpenSize"]["height"] >= result["panelBottom"]
    assert result["overlapsVertically"] is False
    assert result["displayOverflow"] == "visible"
    assert result["scrollOverflow"] == "hidden"
    assert result["scrollPointerEvents"] == "none"
    assert result["textPointerEvents"] == "auto"
    assert result["scrollRight"] <= result["settingsBtnLeft"] - 6
    assert result["hasDragHandle"] is False
    assert result["scrollBarWidth"] == "none"
    assert "rgba(0, 0, 0, 0)" in result["scrollBarColor"]
    assert result["scrollBarGutter"] == "auto"
    assert result["webkitScrollBarWidth"] == "0px"
    assert result["scrollTrackBackground"] == "rgba(0, 0, 0, 0)"
    assert result["scrollHeight"] <= 86
    assert result["scrollThumbBackground"] == "rgba(0, 0, 0, 0)"
    assert result["textMarginRight"] == "0px"


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
                    <span class="subtitle-settings-label" data-subtitle-label="targetLang">目标语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option><option value="en">English</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="opacity">背景不透明度</span>
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
                    <span class="subtitle-settings-label" data-subtitle-label="targetLang">目标语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option><option value="en">English</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label" data-subtitle-label="opacity">背景不透明度</span>
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
    assert result["scrollOverflow"] == "hidden"
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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

    assert result["pointerEvents"] == "none"
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            const afterLockedDrag = window.__dragCalls.slice();

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
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));

            return {
                afterLockedDrag,
                finalDragCalls: window.__dragCalls,
                locked: shared.getSettings().subtitlePanelLocked,
            };
        }
        """
    )

    assert result["afterLockedDrag"] == []
    assert result["finalDragCalls"] == ["start", "stop"]
    assert result["locked"] is False
