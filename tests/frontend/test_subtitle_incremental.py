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
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
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
                storedLocked: window.localStorage.getItem('subtitlePanelLocked'),
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
        "storedLocked": "true",
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
        "storedLocked": "false",
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
    ["index.html", "subtitle.html"],
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
    assert 'data-subtitle-label="lockPosition"' not in template
    assert 'id="subtitle-lock-toggle"' not in template
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
def test_chat_template_keeps_subtitle_as_hidden_bridge_placeholder():
    template = (PROJECT_ROOT / "templates" / "chat.html").read_text(encoding="utf-8")

    assert '<div id="subtitle-display" class="hidden" style="display:none;"><span id="subtitle-text"></span></div>' in template
    assert 'id="subtitle-panel-controls"' not in template
    assert 'id="subtitle-settings-panel"' not in template
    assert 'id="subtitle-resize-handles"' not in template
    assert 'id="subtitle-lock-toggle"' not in template
    assert 'id="subtitle-passthrough-toggle"' not in template


@pytest.mark.frontend
def test_subtitle_window_settings_keeps_passthrough_visible_and_allows_small_bounds():
    css = (PROJECT_ROOT / "static/css/subtitle.css").read_text(encoding="utf-8")
    assert "body.subtitle-window-host .subtitle-passthrough-setting-row" not in css
    assert "min-width: 200px" not in css
    assert "min-width: 180px" not in css


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
    script = (PROJECT_ROOT / "static/subtitle-window.js").read_text(encoding="utf-8")

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
                    <span class="subtitle-settings-label">目标语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">背景不透明度</span>
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
                    <span class="subtitle-settings-label">背景不透明度</span>
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
def test_subtitle_panel_bounds_are_free_not_legacy_size_limited(
    mock_page: Page,
):
    _open_subtitle_harness(
        mock_page,
        "subtitle-web-host",
        """
        <div id="subtitle-display" class="show" style="display:flex; opacity:1; visibility:visible; animation:none; transform:none;">
            <div id="subtitle-scroll"><span id="subtitle-text">Translated text.</span></div>
        </div>
        """,
    )
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static/css/subtitle.css"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

    result = mock_page.evaluate(
        """
        () => {
            const shared = window.nekoSubtitleShared;
            const display = document.getElementById('subtitle-display');
            const bounds = shared.getPanelBounds({ width: 80, height: 36 });
            shared.applySubtitlePanelBounds(display, bounds, { host: 'web' });
            const rect = display.getBoundingClientRect();
            const style = getComputedStyle(display);
            return {
                bounds,
                rect: {
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                },
                cssMinWidth: style.minWidth,
                legacySlider: document.querySelectorAll('#subtitle-size-slider').length,
                legacyButtons: document.querySelectorAll('.subtitle-size-btn').length,
            };
        }
        """
    )

    assert result["bounds"] == {"width": 80, "height": 36}
    assert result["rect"] == {"width": 80, "height": 36}
    assert result["cssMinWidth"] == "0px"
    assert result["legacySlider"] == 0
    assert result["legacyButtons"] == 0


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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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

    assert result["displayFontSize"] == "18px"
    assert result["inlineDisplayFontSize"] == "18px"
    assert result["textFontSize"] == "18px"
    assert result["inlineTextFontSize"] == ""
    assert result["lastSize"] == {"width": 612, "height": 80, "panelBounds": {"width": 600, "height": 68}}


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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
                    <span class="subtitle-settings-label">目标语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">背景不透明度</span>
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
            return { sizeBeforeOpen, immediate, afterCleanDelay, afterPointerReturn, afterSecondClick, settled };
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
                    <span class="subtitle-settings-label">目标语言</span>
                    <select id="subtitle-lang-select"><option value="zh">中文</option></select>
                </div>
                <div class="subtitle-settings-row">
                    <span class="subtitle-settings-label">背景不透明度</span>
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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
        "width": 612,
        "height": 80,
        "panelBounds": {"width": 600, "height": 68},
    }
    assert result["opened"]["panelHeight"] > 0
    assert result["opened"]["sizeAfterOpen"]["width"] == 612
    assert result["opened"]["sizeAfterOpen"]["height"] == 80 + result["opened"]["panelHeight"] + 8
    assert result["opened"]["sizeAfterOpen"]["panelBounds"] == {"width": 600, "height": 68}
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))

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
            "bounds": {"width": 600, "height": 68},
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
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-shared.js"))
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static/subtitle-window.js"))

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

    assert result["emptySize"]["height"] == 80
    assert result["longSize"]["height"] == 80
    assert result["displayHeight"] == 68
    assert result["panelOpenSize"]["panelBounds"] == {"width": 600, "height": 68}
    assert result["panelOpenSize"]["height"] == result["displayHeight"] + 12
    assert result["externalSettingsOpened"] == 1
    assert result["panelHidden"] is True
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
            const textRect = document.getElementById('subtitle-text').getBoundingClientRect();
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
                subtitleInteractionPassthrough: false,
            }, { source: 'test-disable-passthrough' });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const afterDisabled = window.__subtitleInteractionCalls.slice();
            return { afterTransparentArea, afterText, afterTransparentAgain, afterDisabled };
        }
        """
    )

    assert result["afterTransparentArea"] == ["disable"]
    assert result["afterText"] == ["disable", "enable"]
    assert result["afterTransparentAgain"] == ["disable", "enable", "disable"]
    assert result["afterDisabled"] == ["disable", "enable", "disable", "enable"]


@pytest.mark.frontend
def test_subtitle_window_passthrough_poll_matches_desktop_chat_latency():
    script = (PROJECT_ROOT / "static/subtitle-window.js").read_text()

    assert "var INTERACTION_PASSTHROUGH_POLL_MS = 16;" in script
    assert "setInterval(updateNativeInteractionPassthrough, INTERACTION_PASSTHROUGH_POLL_MS)" in script
    assert "setInterval(updateNativeInteractionPassthrough, 80)" not in script


@pytest.mark.frontend
def test_launcher_packages_top_level_static_html_files():
    launcher_spec = (PROJECT_ROOT / "specs/launcher.spec").read_text(encoding="utf-8")

    assert "add_data('static/*.html', 'static')" in launcher_spec


@pytest.mark.frontend
def test_subtitle_shared_cleanup_and_owner_guard_contracts():
    shared_script = (PROJECT_ROOT / "static/subtitle-shared.js").read_text(encoding="utf-8")
    subtitle_script = (PROJECT_ROOT / "static/subtitle.js").read_text(encoding="utf-8")
    subtitle_window_script = (PROJECT_ROOT / "static/subtitle-window.js").read_text(encoding="utf-8")
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
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: 36,
                clientY: 24,
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
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: 36,
                clientY: 24,
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
