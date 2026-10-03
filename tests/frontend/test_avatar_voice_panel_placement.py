"""Browser regressions for voice menus near viewport and toolbar boundaries."""

import json

import pytest
from playwright.sync_api import Page

from tests.frontend.voice_popover_harness import (
    ROOT,
    install_voice_popover_harness,
)

BUTTON_PARTS = (
    "core.js", "idle-assets-and-question.js", "idle-playground.js",
    "idle-actions-and-audio.js", "idle-drag-and-subactions.js",
    "idle-journey-and-presentation.js", "idle-cat-mind-observations.js",
    "methods-setup.js", "methods-buttons.js", "methods-return.js",
    "methods-state-and-cleanup.js",
)


def _install_toolbar(
    page: Page, *, width=1420, height=786, x=1029, scale=1, pet=False,
) -> None:
    install_voice_popover_harness(page, deferred_permission=False)
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("""({zh, pet}) => {
        document.getElementById('live2d-popup-mic').remove();
        document.body.insertAdjacentHTML('beforeend',
            '<canvas id="live2d-canvas"></canvas><div id="chat-container"></div>');
        window.isMobileWidth = () => !pet && innerWidth <= 768;
        window.Live2DManager = class {};
        window.t = key => {
            let value = zh;
            for (const part of key.split('.')) value = value?.[part];
            return typeof value === 'string' ? value : key;
        };
        navigator.mediaDevices.getDisplayMedia = () => {};
        const style = document.createElement('style');
        style.textContent = 'body{margin:0;font-family:Segoe UI,Arial;pointer-events:none;overflow:hidden;'
            + '--neko-popup-text:#eee;--neko-popup-text-sub:#bbb;--neko-popup-bg:#444;'
            + '--neko-popup-separator:#777;--neko-popup-hover:#666}';
        document.head.appendChild(style);
    }""", {"zh": json.loads((ROOT / "static/locales/zh-CN.json").read_text(encoding="utf-8")),
           "pet": pet})
    for part in BUTTON_PARTS:
        page.add_script_tag(path=str(ROOT / "static/avatar/avatar-ui-buttons" / part))
    for filename in (
        "static/avatar/avatar-popup-common.js", "static/avatar/avatar-ui-popup.js",
        "static/avatar/avatar-ui-popup-config.js", "static/live2d/live2d-ui-buttons.js",
    ):
        page.add_script_tag(path=str(ROOT / filename))
    page.evaluate("""({x, scale}) => {
        const manager = window.live2dManager = new Live2DManager();
        manager.isLocked = false;
        const configs = manager.getDefaultButtonConfigs();
        // Only the unrelated settings/Agent contents are omitted. The voice
        // toolbar, popup creation, show animation and click handler are real.
        manager.getDefaultButtonConfigs = () => configs.filter(c => c.id === 'mic');
        manager._syncButtonStatesWithGlobalState = () => {};
        const ticks = [];
        manager.pixi_app = { ticker: { add(fn) { ticks.push(fn); }, remove() {} },
            view: document.getElementById('live2d-canvas') };
        window.__setModelBounds = (x, scale) => {
            const height = 576 * scale;
            const centerY = 350 + 144 * scale;
            window.__bounds = { left: x - 320, right: x + 80,
                top: centerY - height / 2, bottom: centerY + height / 2 };
        };
        window.__setModelBounds(x, scale);
        manager.setupFloatingButtons({ parent: {}, getBounds: () => window.__bounds });
        window.__toolbarTick = () => ticks.forEach(fn => fn());
        window.__toolbarTick();
        const toolbar = document.getElementById('live2d-floating-buttons');
        for (let i = 1; i < 5; i++) {
            const {btnWrapper, btn} = manager.createButtonElement({ id: 'test-' + i, title: 'test' }, toolbar);
            btnWrapper.appendChild(btn);toolbar.appendChild(btnWrapper);
        }
        toolbar.style.display = 'flex';
        window.__placements = 0;
        const position = AvatarPopupUI.positionSidePanel;
        AvatarPopupUI.positionSidePanel = (...args) => {
            window.__placements++;return position(...args);
        };
    }""", {"x": x, "scale": scale})
    page.locator(".live2d-trigger-btn").click()
    page.locator('[data-neko-mic-main-action="screen"]').wait_for(state="visible")
    page.wait_for_function("""() => {
        const popup = document.getElementById('live2d-popup-mic');
        return getComputedStyle(popup).opacity === '1' && !popup.getAnimations().length
            && !popup.classList.contains('is-positioning');
    }""")


def _open_action(page: Page, key="screen") -> None:
    page.locator(f'[data-neko-mic-main-action="{key}"]').hover()
    page.wait_for_function("""() => {
        const panel = document.querySelector('.neko-mic-subwindow');
        return panel && panel.dataset.placement;
    }""")
    page.evaluate("async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)}")


def _snapshot(page: Page) -> dict:
    return page.evaluate("""() => {
        const popup = document.getElementById('live2d-popup-mic');
        const panel = document.querySelector('.neko-mic-subwindow');
        const rect = e => {
            const r = e.getBoundingClientRect();
            return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, width:r.width, height:r.height};
        };
        const r = rect(panel);
        const overlap = (a,b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
        const close = panel.querySelector('[aria-label="Close"]').getBoundingClientRect();
        const hit = document.elementFromPoint((close.left+close.right)/2,(close.top+close.bottom)/2);
        return {popup:rect(popup), panel:r, placement:panel.dataset.placement,
            opensLeft:popup.dataset.opensLeft, goLeft:panel.dataset.goLeft,
            hitsButton: [...document.querySelectorAll('[id^="live2d-btn-"], .live2d-trigger-btn')]
                .some(e => {const b=rect(e);return b.width && b.height && overlap(r,b);}),
            closeReachable: !!hit && panel.querySelector('[aria-label="Close"]').contains(hit),
            bodyHeight: panel.querySelector('.neko-mic-subwindow-body').clientHeight,
            errors:window.__voicePopoverTest.capturedErrors};
    }""")


def _assert_usable(result: dict, width: int, height: int) -> None:
    r = result["panel"]
    assert 0 <= r["left"] < r["right"] <= width + 0.5
    assert 0 <= r["top"] < r["bottom"] <= height + 0.5
    # Enough viewport for a whole normal control row, not merely a positive
    # height (the former check accepted the three-pixel compact-body regression).
    assert result["bodyHeight"] >= 36
    assert result["closeReachable"]
    assert not result["hitsButton"]
    assert not result["errors"]


@pytest.mark.frontend
@pytest.mark.parametrize("key", ["screen", "device", "voice-recognition", "speaker-device"])
@pytest.mark.parametrize("scale", [0.5, 0.75, 1])
@pytest.mark.parametrize("pet", [False, True])
def test_voice_panel_uses_other_side_without_moving_usable_owner(page: Page, key, scale, pet):
    _install_toolbar(page, x=1420 - 391 * scale, scale=scale, pet=pet)
    before = page.locator("#live2d-popup-mic").bounding_box()
    _open_action(page, key)
    result = _snapshot(page)
    assert page.locator("#live2d-popup-mic").bounding_box() == before
    assert result["placement"] == "side"
    assert result["panel"]["right"] < result["popup"]["left"]
    _assert_usable(result, 1420, 786)


@pytest.mark.frontend
@pytest.mark.parametrize("height", [400, 420, 480, 540])
@pytest.mark.parametrize("key", ["screen", "device", "voice-recognition"])
def test_voice_panel_remains_operable_in_short_windows(page: Page, height, key):
    _install_toolbar(page, height=height)
    _open_action(page, key)
    result = _snapshot(page)
    _assert_usable(result, 1420, height)
    assert result["popup"]["bottom"] <= height - 60 + 0.5
    assert result["panel"]["height"] >= 80


@pytest.mark.frontend
def test_popup_direction_is_measured_without_entry_animation(page: Page):
    _install_toolbar(page, x=1103)
    assert page.locator("#live2d-popup-mic").get_attribute("data-opens-left") == "true"
    _open_action(page)
    _assert_usable(_snapshot(page), 1420, 786)


@pytest.mark.frontend
def test_open_voice_panels_follow_toolbar_moves_and_window_resize(page: Page):
    _install_toolbar(page, x=400)
    _open_action(page)
    page.evaluate("__setModelBounds(1029, 1);__toolbarTick()")
    page.wait_for_function("document.querySelector('.neko-mic-subwindow').dataset.goLeft === 'true'")
    _assert_usable(_snapshot(page), 1420, 786)
    page.set_viewport_size({"width": 900, "height": 600})
    page.evaluate("__toolbarTick()")
    page.wait_for_timeout(120)
    result = _snapshot(page)
    _assert_usable(result, 900, 600)
    assert result["popup"]["right"] <= 900
    placements = page.evaluate("window.__placements")
    page.wait_for_timeout(400)
    assert page.evaluate("window.__placements") == placements
    page.locator("#live2d-popup-mic").evaluate("e=>e.remove()")
    page.wait_for_function("!document.querySelector('.neko-mic-subwindow')")
    page.evaluate("__setModelBounds(500, 0.75);__toolbarTick()")
    page.wait_for_timeout(120)
    assert page.evaluate("window.__placements") == placements


@pytest.mark.frontend
def test_device_hover_bridge_allows_transfer_but_expires_in_blank_gap(page: Page):
    _install_toolbar(page)
    _open_action(page, "device")
    result = _snapshot(page)
    a, b = result["popup"], result["panel"]
    # The popup padding and toolbar clearance are outside either hover surface.
    gap_x = a["left"] - 6
    gap_y = b["top"] + 20
    page.mouse.move(gap_x, gap_y)
    page.wait_for_timeout(360)
    assert page.locator(".neko-mic-subwindow").count() == 1
    page.mouse.move(b["right"] - 6, gap_y)
    page.wait_for_timeout(360)
    assert page.locator(".neko-mic-subwindow").count() == 1
    page.mouse.move(gap_x, gap_y)
    page.wait_for_function("!document.querySelector('.neko-mic-subwindow')", timeout=1800)


@pytest.mark.frontend
@pytest.mark.parametrize("scale", [0.5, 1, 1.25])
@pytest.mark.parametrize("opens_left,owner_left", [(False, 400), (True, 900), (False, 820)])
def test_adaptive_placement_preserves_existing_usable_side_layout(page: Page, scale, opens_left, owner_left):
    page.set_viewport_size({"width": 1420, "height": 786})
    page.set_content(f'''
        <div id="live2d-floating-buttons" style="transform:scale({scale});width:0;height:0"></div>
        <div id="live2d-popup-mic" data-opens-left="{str(opens_left).lower()}"
          style="position:fixed;left:{owner_left}px;top:120px;width:220px;height:420px"></div>
        <div id="panel" style="position:fixed;width:360px;height:137px;box-sizing:border-box"></div>
    ''')
    page.add_script_tag(path=str(ROOT / "static/avatar/avatar-popup-common.js"))
    result = page.evaluate("""() => {
        const popup = document.getElementById('live2d-popup-mic');
        const panel = document.getElementById('panel');panel._popupElement=popup;
        const snapshot = () => {
            const r=panel.getBoundingClientRect();return [r.left,r.top,r.width,r.height];
        };
        AvatarPopupUI.positionSidePanel(panel,popup,{},false);
        AvatarPopupUI.applySidePanelTransform(panel,'none');
        const before=snapshot();
        AvatarPopupUI.positionSidePanel(panel,popup,{adaptivePlacement:true});
        return {before,after:snapshot()};
    }""")
    assert result["after"] == pytest.approx(result["before"], abs=0.5)


@pytest.mark.frontend
def test_compact_voice_panel_keeps_close_and_last_control_reachable(page: Page):
    install_voice_popover_harness(page, deferred_permission=False)
    page.set_viewport_size({"width": 320, "height": 400})
    page.add_script_tag(path=str(ROOT / "static/avatar/avatar-popup-common.js"))
    page.evaluate("""async () => {
        navigator.mediaDevices.getDisplayMedia=()=>{};
        const popup=document.getElementById('live2d-popup-mic');
        await renderFloatingMicList(popup);
        Object.assign(popup.style,{left:'8px',top:'8px',height:'350px'});
        const button=document.createElement('button');button.id='live2d-btn-mic';
        button.style.cssText='position:fixed;left:240px;top:150px;width:48px;height:48px';
        document.body.appendChild(button);
        window.__voicePopoverTest.action('screen').click();
    }""")
    page.wait_for_function("document.querySelector('.neko-mic-subwindow')?.dataset.placement === 'compact'")
    _assert_usable(_snapshot(page), 320, 400)
    page.locator('[data-neko-browser-screen-share]').click()
    assert page.evaluate("window.__screenToggleCalls") == 1
    page.evaluate("window.__voicePopoverTest.action('device').click()")
    page.locator('.neko-mic-subwindow [aria-label="Close"]').click()
    assert page.locator('.neko-mic-subwindow').count() == 0


@pytest.mark.frontend
@pytest.mark.parametrize("width,height,dpr", [(3840, 2160, 1), (2560, 1440, 1.5),
    (1920, 1080, 2), (1536, 864, 2.5), (1280, 720, 3), (960, 540, 4)])
def test_4k_voice_layouts_are_usable_at_both_right_edge_positions(browser, width, height, dpr):
    with browser.new_context(device_scale_factor=dpr) as context:
        for x in [width - 391, width - 80]:
            page = context.new_page()
            try:
                _install_toolbar(page, width=width, height=height, x=x)
                _open_action(page)
                result = _snapshot(page)
                _assert_usable(result, width, height)
                assert result["placement"] == "side"
            finally:
                page.close()


@pytest.mark.frontend
@pytest.mark.parametrize("scale", [0.5, 1, 1.25])
@pytest.mark.parametrize("box_sizing", ["border-box", "content-box"])
def test_adaptive_height_bounds_include_padding_and_ui_scale(page: Page, scale, box_sizing):
    page.set_viewport_size({"width": 900, "height": 400})
    page.set_content(f'''
        <div id="live2d-floating-buttons" style="transform:scale({scale})"></div>
        <div id="live2d-popup-mic" data-opens-left="false"
          style="position:fixed;left:500px;top:8px;width:220px;height:350px"></div>
        <div id="panel" style="position:fixed;width:360px;height:500px;padding:8px;
          border:1px solid;box-sizing:{box_sizing}">content</div>
    ''')
    page.add_script_tag(path=str(ROOT / "static/avatar/avatar-popup-common.js"))
    result = page.evaluate("""() => {
        const panel=document.getElementById('panel'),popup=document.getElementById('live2d-popup-mic');
        panel._popupElement=popup;
        AvatarPopupUI.positionSidePanel(panel,popup,{adaptivePlacement:true});
        const r=panel.getBoundingClientRect();
        return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height};
    }""")
    assert result["left"] >= 8
    assert result["right"] <= 892.5
    assert result["top"] >= 8
    assert result["bottom"] <= 340.5
    assert result["height"] >= 80 * scale


@pytest.mark.frontend
def test_open_voice_panel_tracks_scale_and_late_content_without_a_layout_loop(page: Page):
    _install_toolbar(page, x=1029, scale=0.5)
    _open_action(page)
    page.evaluate("__setModelBounds(1029,1);__toolbarTick()")
    page.wait_for_function("document.querySelector('.neko-mic-subwindow').dataset.nekoUiScale === '1'")
    page.locator('[data-neko-sidepanel-content]').evaluate("""body=>{
        const content=document.createElement('div');content.style.cssText='height:700px;flex-shrink:0';
        content.textContent='late sources';body.appendChild(content);
    }""")
    page.wait_for_timeout(120)
    result = _snapshot(page)
    _assert_usable(result, 1420, 786)
    assert result["panel"]["height"] > 300
    count = page.evaluate("window.__placements")
    page.wait_for_timeout(400)
    assert page.evaluate("window.__placements") == count


@pytest.mark.frontend
def test_voice_panels_follow_visual_viewport_changes(page: Page):
    _install_toolbar(page)
    _open_action(page)
    page.evaluate("""()=>{
        const viewport = Object.assign(new EventTarget(),
            {offsetLeft:200,offsetTop:50,width:1000,height:500});
        Object.defineProperty(window,'visualViewport',{value:viewport,configurable:true});
        // Re-render so listeners attach to the new viewport test double.
        return renderFloatingMicList();
    }""")
    _open_action(page)
    page.wait_for_timeout(80)
    result = _snapshot(page)
    _assert_usable(result, 1200, 550)
    assert result["panel"]["left"] >= 208
    assert result["panel"]["top"] >= 58
    page.evaluate("visualViewport.height=400;visualViewport.dispatchEvent(new Event('resize'))")
    page.wait_for_timeout(80)
    result = _snapshot(page)
    assert result["panel"]["bottom"] <= 450
    assert result["popup"]["top"] >= 58
    assert result["popup"]["bottom"] <= 390.5


@pytest.mark.frontend
def test_compact_screen_header_with_long_translation_keeps_close_reachable(page: Page):
    install_voice_popover_harness(page, deferred_permission=False)
    page.set_viewport_size({"width": 320, "height": 400})
    page.add_script_tag(path=str(ROOT / "static/avatar/avatar-popup-common.js"))
    page.evaluate("""async()=>{
        window.getDesktopCaptureProvider=()=>({getSources(){},sourceEnumerationMayPrompt:false});
        window.t=key=>key==='app.screenSource.rememberWindow' ? 'Remember the previously selected window '.repeat(4) : key;
        const popup=window.__voicePopoverTest.popup();
        await renderFloatingMicList(popup);
        Object.assign(popup.style,{left:'8px',top:'8px',height:'350px'});
        const button=document.createElement('button');button.id='live2d-btn-mic';
        button.style.cssText='position:fixed;left:240px;top:150px;width:48px;height:48px';
        document.body.appendChild(button);
        window.__voicePopoverTest.action('screen').click();
    }""")
    page.wait_for_function("document.querySelector('.neko-mic-subwindow')?.dataset.placement === 'compact'")
    _assert_usable(_snapshot(page), 320, 400)
    page.locator('.neko-mic-subwindow [aria-label="Close"]').click()
    assert page.locator('.neko-mic-subwindow').count() == 0


@pytest.mark.frontend
def test_adaptive_option_keeps_niri_virtual_coordinate_ownership(page: Page):
    page.set_viewport_size({"width": 400, "height": 400})
    page.set_content('''
        <div id="live2d-popup-mic" data-opens-left="true"
          style="position:fixed;left:20px;top:20px;width:220px;height:300px"></div>
        <div id="panel" style="position:fixed;width:280px;height:120px"></div>
    ''')
    page.evaluate("""()=>{
        window.__nekoNiriPetPhysicalCrop={isActive:()=>true,
            getState:()=>({virtualBounds:{width:1920,height:1080}}),
            toVirtualRect:r=>({...r,x:r.x+1000,y:r.y+100})};
    }""")
    page.add_script_tag(path=str(ROOT / "static/avatar/avatar-popup-common.js"))
    result = page.evaluate("""()=>{
        const popup=document.getElementById('live2d-popup-mic'),panel=document.getElementById('panel');
        panel._popupElement=popup;
        AvatarPopupUI.positionSidePanel(panel,popup,{adaptivePlacement:true},false);
        return {left:parseFloat(panel.style.left),top:parseFloat(panel.style.top),
            crop:panel.dataset.niriPhysicalCropPositioned,down:panel.dataset.goDown};
    }""")
    assert result == {"left": 1252, "top": 120, "crop": "true", "down": "false"}


@pytest.mark.frontend
@pytest.mark.parametrize("active", [False, True])
def test_popup_uses_virtual_bounds_only_for_active_niri_crop(page: Page, active):
    page.set_viewport_size({"width": 400, "height": 400})
    page.set_content('''
        <div style="position:fixed;left:300px;top:20px;width:48px;height:48px">
          <button id="live2d-btn-mic" style="width:48px;height:48px"></button>
          <div id="live2d-popup-mic" style="position:absolute;width:220px;height:120px;
            transform:translateX(-10px)"></div>
        </div>
    ''')
    page.evaluate("""active=>{
        window.__virtualRectCalls=0;
        window.__nekoNiriPetPhysicalCrop={isActive:()=>active,
            getState:()=>({virtualBounds:{width:1920,height:1080}}),
            toVirtualRect:r=>{window.__virtualRectCalls++;return {...r,x:r.x+1000,y:r.y+100}}};
    }""", active)
    page.add_script_tag(path=str(ROOT / "static/avatar/avatar-popup-common.js"))
    result = page.evaluate("""()=>{
        const popup=document.getElementById('live2d-popup-mic');
        const position=AvatarPopupUI.positionPopup(popup,{buttonId:'mic'});
        return {opensLeft:position.opensLeft,usedVirtualRect:window.__virtualRectCalls>0};
    }""")
    assert result == {"opensLeft": not active, "usedVirtualRect": active}


@pytest.mark.frontend
@pytest.mark.parametrize("width,height", [(240, 160), (260, 160), (240, 180), (280, 180)])
def test_compact_panel_remeasures_wrapped_header_and_relaxes_footer(page: Page, width, height):
    install_voice_popover_harness(page, deferred_permission=False)
    page.set_viewport_size({"width": width, "height": height})
    page.add_script_tag(path=str(ROOT / "static/avatar/avatar-popup-common.js"))
    page.evaluate("""async ({zh,width,height})=>{
        window.t=key=>{let value=zh;for(const part of key.split('.'))value=value?.[part];
            return typeof value==='string'?value:key};
        window.getDesktopCaptureProvider=()=>({getSources(){},sourceEnumerationMayPrompt:false});
        const popup=window.__voicePopoverTest.popup();await renderFloatingMicList(popup);
        Object.assign(popup.style,{left:'8px',top:'8px',height:'132px'});
        const button=document.createElement('button');button.id='live2d-btn-mic';
        button.style.cssText=`position:fixed;left:${width-80}px;top:8px;width:72px;height:${height-16}px`;
        document.body.appendChild(button);window.__voicePopoverTest.action('screen').click();
    }""", {"zh": json.loads((ROOT / "static/locales/zh-CN.json").read_text(encoding="utf-8")),
           "width": width, "height": height})
    page.wait_for_function("document.querySelector('.neko-mic-subwindow')?.dataset.placement === 'compact'")
    _assert_usable(_snapshot(page), width, height)
    # The taller wrapped title remains visible while the body exposes a whole
    # control row. Both the body input and close action still accept real input.
    field = page.locator('.neko-mic-subwindow .screen-source-title-filter')
    field.fill('Editor')
    assert field.input_value() == 'Editor'
    page.locator('.neko-mic-subwindow [aria-label="Close"]').click()
    assert page.locator('.neko-mic-subwindow').count() == 0


@pytest.mark.frontend
def test_meter_and_hover_paint_updates_do_not_scan_layout(page: Page):
    _install_toolbar(page)
    _open_action(page)
    page.wait_for_timeout(300)
    result = page.evaluate("""async()=>{
        const popup=document.getElementById('live2d-popup-mic');
        const read=popup.getBoundingClientRect.bind(popup);
        let reads=0;popup.getBoundingClientRect=()=>{reads++;return read()};
        const before=window.__placements;
        const meter=document.getElementById('mic-volume-bar-fill');
        const status=document.getElementById('mic-volume-status');
        const row=document.querySelector('[data-neko-mic-main-action-row="screen"]');
        for(let i=0;i<30;i++){
            meter.style.width=i+'%';status.textContent='level '+i;
            row.style.background=i%2?'#555':'#666';
            await new Promise(requestAnimationFrame);
        }
        await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
        delete popup.getBoundingClientRect;
        return {reads,placements:window.__placements-before};
    }""")
    assert result == {"reads": 0, "placements": 0}


@pytest.mark.frontend
def test_popup_layout_subscription_cancels_queued_work_and_is_idempotent(page: Page):
    page.set_content('<div id="popup" style="display:flex;opacity:1;width:220px;height:100px"></div>')
    page.add_script_tag(path=str(ROOT / 'static/avatar/avatar-popup-common.js'))
    result = page.evaluate("""async()=>{
        const popup=document.getElementById('popup');let callbacks=0,reads=0;
        const read=popup.getBoundingClientRect.bind(popup);
        popup.getBoundingClientRect=()=>{reads++;return read()};
        const observer=AvatarPopupUI.observePopupLayout(popup,()=>{callbacks++});
        observer.disconnect();observer.disconnect();
        popup.style.width='240px';window.dispatchEvent(new Event('resize'));
        await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
        return {callbacks,reads};
    }""")
    assert result == {"callbacks": 0, "reads": 0}
