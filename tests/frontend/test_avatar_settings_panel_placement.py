"""Settings cascades must use screen bounds while the Pet window is cropped."""

from pathlib import Path

import pytest
from playwright.sync_api import Page


ROOT = Path(__file__).resolve().parents[2]


def _install_settings_panel(page: Page, *, prefix, scale, cropped, edge, width=1440):
    page.set_viewport_size({"width": min(560, width) if cropped else width, "height": 900})
    page.set_content("<style>body{margin:0;font-family:Arial}</style>")
    page.evaluate("""({prefix, scale, cropped, edge, width}) => {
        const x = edge === 'right' ? width - 340 : 72;
        const nativeWidth = window.innerWidth;
        const offset = cropped ? {x: Math.max(32, x - 80), y: 100} : {x: 0, y: 0};
        Object.assign(document.body.style, {
            width: width + 'px', height: '900px',
            transform: cropped ? `translate(${-offset.x}px, ${-offset.y}px)` : 'none',
            transformOrigin: '0 0'
        });
        window.isMobileWidth = () => false;
        if (cropped) {
            Object.defineProperty(window, 'innerWidth', {configurable:true, value:width});
            window.__nekoNiriPetPhysicalCrop = {
                isActive: () => true,
                getState: () => ({source:'power-save:test', virtualBounds:{width, height:900}}),
                toVirtualRect: r => ({...r, x:r.x + offset.x, y:r.y + offset.y})
            };
        }
        const toolbar = document.createElement('div');
        toolbar.id = prefix + '-floating-buttons';
        toolbar.style.cssText = `position:fixed;left:0;top:0;transform:scale(${scale});transform-origin:0 0`;
        document.body.appendChild(toolbar);
        const button = document.createElement('button');
        button.id = prefix + '-btn-settings';
        button.style.cssText = `position:fixed;left:${edge === 'right' ? x + 250 : 8}px;top:400px;width:48px;height:48px`;
        document.body.appendChild(button);
        const popup = document.createElement('div');
        popup.id = prefix + '-popup-settings';
        popup.dataset.opensLeft = String(edge === 'right');
        popup.style.cssText = `position:fixed;left:${x}px;top:300px;width:240px;height:450px;background:#ddd`;
        popup.innerHTML = '<div id="anchor" style="margin-top:50px;height:32px">动画设置</div>';
        document.body.appendChild(popup);
        window.__settingsCase = {prefix, offset, nativeWidth};
    }""", dict(prefix=prefix, scale=scale, cropped=cropped, edge=edge, width=width))
    for name in ("avatar-popup-common.js", "avatar-ui-popup.js"):
        page.add_script_tag(path=str(ROOT / "static/avatar" / name))
    page.evaluate("""() => {
        const {prefix} = window.__settingsCase;
        class Manager {}
        AvatarPopupMixin.apply(Manager.prototype, prefix);
        const manager = new Manager();
        const panel = manager._createAnimationSettingsSidePanel();
        panel.id = 'settings-panel';
        panel._popupElement = document.getElementById(prefix + '-popup-settings');
        panel._anchorElement = document.getElementById('anchor');
        document.body.appendChild(panel);
        panel._expand();
    }""")
    page.wait_for_function("getComputedStyle(document.getElementById('settings-panel')).opacity === '1'")


def _snapshot(page: Page):
    return page.evaluate("""() => {
        const {prefix, offset} = window.__settingsCase;
        const panel = document.getElementById('settings-panel');
        const rect = element => {
            const r = element.getBoundingClientRect();
            return {left:r.left + offset.x, right:r.right + offset.x,
                top:r.top + offset.y, bottom:r.bottom + offset.y, width:r.width, height:r.height};
        };
        return {panel:rect(panel), popup:rect(panel._popupElement),
            button:rect(document.getElementById(prefix + '-btn-settings')),
            down:panel.dataset.goDown, left:panel.dataset.goLeft,
            scrollable:panel.scrollHeight > panel.clientHeight,
            sliders:[...panel.querySelectorAll('input[type=range]')].map(el=>el.getBoundingClientRect().width)};
    }""")


@pytest.mark.frontend
@pytest.mark.parametrize("prefix", ["live2d", "vrm", "mmd", "pngtuber"])
@pytest.mark.parametrize("scale", [1, 1.25])
@pytest.mark.parametrize("cropped", [False, True])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_settings_cascade_keeps_direction_width_and_screen_bounds(page, prefix, scale, cropped, edge):
    _install_settings_panel(page, prefix=prefix, scale=scale, cropped=cropped, edge=edge)
    result = _snapshot(page)
    panel, owner, button = result["panel"], result["popup"], result["button"]
    assert result["left"] == str(edge == "right").lower()
    assert result["down"] == "false"
    assert panel["width"] >= 225 * scale
    assert panel["left"] >= 7.5 and panel["right"] <= 1432.5
    assert panel["top"] >= 7.5 and panel["bottom"] <= 840.5
    if edge == "right":
        assert panel["right"] <= owner["left"]
    else:
        assert panel["left"] >= owner["right"]
    assert panel["right"] <= button["left"] or panel["left"] >= button["right"]
    assert min(result["sliders"]) >= 60 * scale


@pytest.mark.frontend
@pytest.mark.parametrize("cropped", [False, True])
def test_settings_cascade_stacks_and_scrolls_when_side_has_no_room(page, cropped):
    _install_settings_panel(page, prefix="live2d", scale=1, cropped=cropped, edge="right", width=540)
    page.evaluate("""() => {
        const panel = document.getElementById('settings-panel');
        // Model a settings panel with more content than the free vertical space.
        panel.insertAdjacentHTML('beforeend', '<div style="height:500px;flex-shrink:0">Settings</div>');
        AvatarPopupUI.positionSidePanel(panel, panel._anchorElement, {}, false);
        AvatarPopupUI.applySidePanelTransform(panel, 'none');
    }""")
    result = _snapshot(page)
    panel, owner = result["panel"], result["popup"]
    assert result["down"] == "true"
    assert panel["width"] >= 225
    assert panel["left"] >= 7.5 and panel["right"] <= 532.5
    assert panel["top"] >= 7.5 and panel["bottom"] <= 840.5
    assert panel["bottom"] <= owner["top"] or panel["top"] >= owner["bottom"]
    assert result["scrollable"]


@pytest.mark.frontend
@pytest.mark.parametrize("cropped", [False, True])
@pytest.mark.parametrize("delayed", [False, True])
def test_settings_collision_correction_uses_screen_coordinates(page, cropped, delayed):
    _install_settings_panel(page, prefix="live2d", scale=1, cropped=cropped, edge="right")
    initial = page.evaluate("""delayed => {
        const panel = document.getElementById('settings-panel');
        const position = () => {
            AvatarPopupUI.positionSidePanel(panel, panel._anchorElement, {}, delayed);
            AvatarPopupUI.applySidePanelTransform(panel, 'none');
        };
        // Arm a fresh 300ms check before introducing a new floating button.
        if (delayed) position();
        const button = document.createElement('button');
        button.id = 'live2d-btn-collision';
        button.style.cssText = 'position:fixed;left:850px;top:350px;width:48px;height:48px';
        document.body.appendChild(button);
        if (!delayed) position();
        return {left:panel.style.left, hasTimer:panel._nekoPositionCheckTimer != null};
    }""", delayed)
    assert initial["hasTimer"] is delayed
    if delayed:
        assert initial["left"] != "8px"
        page.wait_for_function("document.getElementById('settings-panel').style.left === '8px'")
    else:
        assert initial["left"] == "8px"
    result = _snapshot(page)
    assert result["panel"]["left"] == pytest.approx(8, abs=0.5)
    assert result["panel"]["width"] >= 225
    assert result["panel"]["right"] <= 838.5


@pytest.mark.frontend
@pytest.mark.parametrize("cropped", [False, True])
@pytest.mark.parametrize("delayed", [False, True])
def test_settings_collision_stacks_when_buttons_leave_no_side_space(page, cropped, delayed):
    _install_settings_panel(page, prefix="live2d", scale=1, cropped=cropped, edge="right")
    page.evaluate("""delayed => {
        const panel = document.getElementById('settings-panel');
        const position = () => {
            AvatarPopupUI.positionSidePanel(panel, panel._anchorElement, {}, delayed);
            AvatarPopupUI.applySidePanelTransform(panel, 'none');
        };
        if (delayed) position();
        const button = document.createElement('button');
        button.id = 'live2d-btn-edge';
        button.style.cssText = 'position:fixed;left:8px;top:350px;width:48px;height:48px';
        document.body.appendChild(button);
        if (!delayed) position();
    }""", delayed)
    if delayed:
        page.wait_for_timeout(350)
    result = _snapshot(page)
    panel, owner = result["panel"], result["popup"]
    assert result["down"] == "true"
    assert panel["width"] >= 225
    assert panel["left"] >= 7.5 and panel["right"] <= 1432.5
    assert panel["bottom"] <= owner["top"] or panel["top"] >= owner["bottom"]


@pytest.mark.frontend
def test_pending_settings_check_repositions_after_crop_mode_changes(page):
    _install_settings_panel(page, prefix="live2d", scale=1, cropped=True, edge="right")
    revision = page.evaluate("""() => {
        const panel = document.getElementById('settings-panel');
        AvatarPopupUI.positionSidePanel(panel, panel._anchorElement);
        AvatarPopupUI.applySidePanelTransform(panel, 'none');
        const revision = panel._nekoPositionRevision;
        delete window.__nekoNiriPetPhysicalCrop;
        Object.defineProperty(window, 'innerWidth', {configurable:true, value:window.__settingsCase.nativeWidth});
        Object.assign(window.__settingsCase.offset, {x:0, y:0});
        Object.assign(document.body.style, {transform:'none', width:innerWidth + 'px'});
        panel._popupElement.style.left = '200px';
        document.getElementById('live2d-btn-settings').style.left = '450px';
        return revision;
    }""")
    page.wait_for_timeout(350)
    result = _snapshot(page)
    assert page.evaluate("document.getElementById('settings-panel')._nekoPositionRevision") > revision
    assert result["panel"]["width"] >= 225
    assert result["panel"]["left"] >= 7.5 and result["panel"]["right"] <= 552.5
