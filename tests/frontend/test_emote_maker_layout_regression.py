"""The emote maker's resizable layout must never let a panel be dragged off-window.

Regression this pins: the two side handles were clamped by *fixed* pixel ceilings
(left 200-520, right 220-560) that ignored the actual container width, while the centre
column had its own minimum. At a 1120px window each side could legally reach its own
maximum and the three minima summed past the container — 52px of horizontal overflow,
with the inspector pushed 36px off-screen and the handle itself outside the viewport, so
the user could no longer grab it to undo the damage. The clamp now works on the *sum* of
both side columns against the measured container, and re-runs when the window shrinks.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect
from tests.emote_fixtures import spine_zip

# Empty-library mode hides the inspector and the list dock; the grid geometry does not
# depend on content, so the probe reveals them to exercise the desktop layout.
REVEAL = """() => {
    document.body.classList.remove('editor-empty');
    document.querySelector('.animation-dock').hidden = false;
    document.querySelector('.inspector-panel').hidden = false;
}"""

GEOMETRY = """() => {
    const grid = document.querySelector('.maker-grid');
    const rect = (sel) => document.querySelector(sel).getBoundingClientRect();
    const shown = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        const box = el.getBoundingClientRect();
        return getComputedStyle(el).display !== 'none' && box.width > 0 && box.height > 0;
    };
    const ws = document.querySelector('.workspace-panel');
    const dock = document.querySelector('.animation-dock');
    return {
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        tracks: getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length,
        gridOverflowX: grid.scrollWidth - grid.clientWidth,
        documentOverflowX: document.documentElement.scrollWidth - window.innerWidth,
        assetRight: Math.round(rect('.asset-panel').right),
        workspaceLeft: Math.round(rect('.workspace-panel').left),
        inspectorLeft: Math.round(rect('.inspector-panel').left),
        inspectorTop: Math.round(rect('.inspector-panel').top),
        inspectorRight: Math.round(rect('.inspector-panel').right),
        assetTop: Math.round(rect('.asset-panel').top),
        canvasBottom: Math.round(rect('.canvas-stage').bottom),
        dockTop: Math.round(rect('.animation-dock').top),
        handlesShown: ['.resize-handle-col', '#resize-dock'].every(shown),
        // 纵向：播放控件要么在视口内，要么被 .workspace-panel 的面内滚动接住。
        playbackBottom: Math.round(rect('.playback-controls').bottom),
        playbackShown: shown('.playback-controls'),
        workspaceCanScroll: ws.scrollHeight > ws.clientHeight,
        dockClip: dock.scrollHeight - dock.clientHeight,
    };
}"""


def _open(page: Page, server: str, width: int, height: int = 900) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{server}/emote_maker", wait_until="networkidle")
    page.wait_for_timeout(200)
    page.evaluate(REVEAL)
    page.wait_for_timeout(120)


def _drag_both_sides_past_the_limit(page: Page) -> None:
    # 60 x Shift+Arrow = 1440px per side: far beyond every ceiling.
    page.keyboard.down("Shift")
    page.focus("#resize-left")
    for _ in range(60):
        page.keyboard.press("ArrowRight")
    page.focus("#resize-right")
    for _ in range(60):
        page.keyboard.press("ArrowLeft")
    page.keyboard.up("Shift")
    page.wait_for_timeout(120)


def test_title_bar_uses_shared_host_window_controls(page: Page, running_server: str) -> None:
    page.add_init_script("""(() => {
        let pinned = false;
        let maximized = false;
        window.pinToggleCount = 0;
        window.windowControlCalls = [];
        window.nekoHost = {
            closeWindow: async () => {
                window.windowControlCalls.push('close');
                return { ok: true };
            },
        };
        window.nekoWindowControl = {
            getPinState: async () => ({ available: true, pinned }),
            togglePin: async () => {
                window.pinToggleCount += 1;
                pinned = !pinned;
                return { available: true, pinned };
            },
            minimize: async () => { window.windowControlCalls.push('minimize'); },
            isMaximized: async () => maximized,
            maximize: async () => {
                window.windowControlCalls.push('maximize');
                maximized = !maximized;
                return { ok: true, isMaximized: maximized };
            },
        };
    })();""")
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    pin = page.locator('[data-neko-window-control="pin"]')
    expect(pin).to_be_visible()
    expect(pin).to_have_attribute("aria-pressed", "false")
    minimize = page.locator('[data-neko-window-control="minimize"]')
    assert pin.bounding_box()["x"] < minimize.bounding_box()["x"]

    controls = page.locator('.neko-window-controls > button')
    assert controls.evaluate_all("buttons => buttons.map(button => button.dataset.nekoWindowControl)") == [
        "pin", "minimize", "maximize", "close",
    ]
    assert controls.evaluate_all("""buttons => buttons.map(button => {
        const rect = button.getBoundingClientRect();
        return [rect.width, rect.height];
    })""") == [
        [32, 30], [32, 30], [32, 30], [32, 30],
    ]

    minimize.click()
    maximize = page.locator('[data-neko-window-control="maximize"]')
    maximize.click()
    expect(page.locator('html')).to_have_class('neko-window-maximized')
    maximize.click()
    expect(page.locator('html')).not_to_have_class('neko-window-maximized')

    page.locator('[data-neko-window-control="close"]').click()
    page.wait_for_function("window.windowControlCalls.includes('close')")
    assert page.evaluate("window.windowControlCalls") == ["minimize", "maximize", "maximize", "close"]
    assert page.url == f"{running_server}/emote_maker"

    pin.click()
    expect(pin).to_have_attribute("aria-pressed", "true")
    pin.click()
    expect(pin).to_have_attribute("aria-pressed", "false")
    assert page.evaluate("window.pinToggleCount") == 2

    page.evaluate("delete window.nekoWindowControl")
    page.evaluate("window.nekoWindowControls.refresh()")
    expect(pin).to_be_hidden()


def test_desktop_grid_keeps_three_columns(page: Page, running_server: str) -> None:
    _open(page, running_server, 1400)
    box = page.evaluate(GEOMETRY)
    assert box["tracks"] == 5, "两侧 + 两条手柄轨道"
    assert box["handlesShown"], "桌面布局下手柄应当可见"
    assert box["assetRight"] <= box["workspaceLeft"] + 1, "素材库与画布不重叠"
    assert box["workspaceLeft"] < box["inspectorLeft"], "设置面板在画布右侧"
    assert abs(box["assetTop"] - box["inspectorTop"]) < 4, "三栏在同一行"
    assert box["dockTop"] >= box["canvasBottom"] - 2, "列表在画布下方"


def test_preview_zoom_pans_without_moving_editor_layout(page: Page, running_server: str) -> None:
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.set_input_files("#template-archive-file", {
        "name": "preview.zip", "mimeType": "application/zip", "buffer": spine_zip(),
    })
    expect(page.locator("#emote-spine-canvas")).to_be_visible()
    frame = page.locator("#canvas-frame")
    stage = page.locator(".canvas-stage")
    base = frame.bounding_box()
    viewport = stage.bounding_box()
    value = page.locator("#zoom-value")
    zoom_in = page.locator("#zoom-in-btn")
    for _ in range(13):
        zoom_in.click()
    expect(value).to_have_text("400%")
    page.wait_for_function("""() => {
        const frame = document.querySelector('#canvas-frame');
        return Math.abs(frame.getBoundingClientRect().width / frame.offsetWidth - 4) < .01;
    }""")
    assert stage.bounding_box() == viewport

    x, y = viewport["x"] + viewport["width"] / 2, viewport["y"] + viewport["height"] / 2
    for button, dx, dy in (("left", 40, -35), ("middle", -30, 25)):
        before_pan = frame.bounding_box()
        page.mouse.move(x, y)
        page.mouse.down(button=button)
        page.mouse.move(x + dx, y + dy, steps=4)
        page.mouse.up(button=button)
        after_pan = frame.bounding_box()
        assert abs(after_pan["x"] - before_pan["x"] - dx) < 1, button
        assert abs(after_pan["y"] - before_pan["y"] - dy) < 1, button
        assert stage.bounding_box() == viewport

    page.locator("#fit-btn").click()
    expect(value).to_have_text("100%")
    assert frame.bounding_box() == base
    page.mouse.move(x + 50, y + 10)
    page.mouse.wheel(0, -100)
    expect(value).to_have_text("113%")
    page.locator("#reset-view-btn").click()
    expect(value).to_have_text("100%")
    assert frame.bounding_box() == base


def test_resizing_panels_keeps_preview_square(page: Page, running_server: str, builtin_emote_template_dir) -> None:
    page.set_viewport_size({"width": 1400, "height": 1000})
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    canvas = page.locator("#emote-spine-canvas")
    expect(canvas).to_be_visible()
    frame = page.locator("#canvas-frame")
    layers = (frame, canvas, page.locator("#wardrobe-placements"))

    def drag_handle(selector, dx, dy):
        bounds = page.locator(selector).bounding_box()
        x, y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + dx, y + dy, steps=6)
        page.mouse.up()

    def square_preview():
        for layer in layers:
            box = layer.bounding_box()
            assert abs(box["width"] - box["height"]) < 1, box

    initial_width = frame.bounding_box()["width"]
    drag_handle("#resize-left", 500, 0)
    drag_handle("#resize-right", -500, 0)
    assert frame.bounding_box()["width"] < initial_width
    square_preview()
    drag_handle("#resize-dock", 0, -500)
    square_preview()
    page.set_viewport_size({"width": 800, "height": 760})
    square_preview()


def test_part_and_color_popovers_keep_the_inspector_layout_stable(
    page: Page, running_server: str, builtin_emote_template_dir,
) -> None:
    page.set_viewport_size({"width": 1400, "height": 650})
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    expect(page.locator("[data-template-attachment-slot]")).to_have_count(18)

    parts = page.locator("#template-attachment-controls")
    assert parts.evaluate("node => getComputedStyle(node).overflowY === 'auto' && node.scrollHeight > node.clientHeight")
    parts.locator(".template-compat-row").last.scroll_into_view_if_needed()
    assert parts.evaluate("node => node.scrollTop") > 0
    part_trigger = parts.locator(".emote-select-trigger").last
    list_height = parts.evaluate("node => node.scrollHeight")
    part_menu = page.locator("body > .emote-select-menu.is-open")
    for close_menu in (
        part_trigger.click,
        lambda: page.keyboard.press("Escape"),
        page.locator("#part-settings-title").click,
        part_menu.locator('[aria-selected="true"]').click,
    ):
        part_trigger.click()
        expect(part_menu).to_be_visible()
        expect(part_trigger).to_have_attribute("aria-expanded", "true")
        assert parts.evaluate("node => node.scrollHeight") == list_height
        close_menu()
        expect(part_menu).to_have_count(0)
        expect(part_trigger).to_have_attribute("aria-expanded", "false")

    page.locator("#inspector-tab-colors").click()
    panel = page.locator("#inspector-panel-colors")
    size = "node => [node.scrollHeight, node.scrollTop, node.querySelector('#template-slot-controls').offsetHeight]"
    initial = panel.evaluate(size)
    menu = panel.locator(".part-color-menu")
    menu.click()
    options = panel.locator(".part-color-options:popover-open")
    expect(options).to_be_visible()
    options.locator("[data-color-part]").last.scroll_into_view_if_needed()
    assert options.evaluate("node => node.scrollTop") > 0
    assert panel.evaluate(size) == initial
    menu.press("Escape")
    expect(options).to_have_count(0)

    color_trigger = panel.locator('button.part-color-trigger[data-color-field="tintColor"]')
    color_trigger.click()
    floating = panel.locator(".part-color-picker:popover-open")
    expect(floating.locator(".part-color-hex")).to_be_focused()
    assert panel.evaluate(size) == initial
    assert floating.evaluate("node => { const rect = node.getBoundingClientRect(); return rect.top >= 0"
                             " && rect.bottom <= window.innerHeight && rect.left >= 0 && rect.right <= window.innerWidth; }")
    trigger_box = color_trigger.bounding_box()
    assert floating.bounding_box()["y"] >= trigger_box["y"] + trigger_box["height"]

    color_trigger.evaluate("""node => {
        const offset = window.innerHeight - node.getBoundingClientRect().bottom - 24;
        node.style.translate = `0 ${offset}px`;
        window.dispatchEvent(new Event('resize'));
    }""")
    picker_box = floating.bounding_box()
    assert picker_box["y"] + picker_box["height"] <= color_trigger.bounding_box()["y"]


def test_recolored_parts_can_scroll_back_to_top(page: Page, running_server: str, builtin_emote_template_dir) -> None:
    page.set_viewport_size({"width": 1400, "height": 510})
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    expect(page.locator("#emote-spine-canvas")).to_be_visible()
    expect(page.locator("#export-png-btn img")).to_have_count(0)
    page.locator("#inspector-tab-colors").click()

    panel = page.locator("#inspector-panel-colors")
    part_menu = panel.locator(".part-color-menu")
    part_list = panel.locator(".part-color-options:popover-open")
    color_trigger = panel.locator('#template-slot-controls [data-color-field="tintColor"]')
    hex_input = panel.locator(".part-color-picker:popover-open .part-color-hex")
    back_to_top = page.locator("#color-back-to-top")
    expect(back_to_top).to_be_hidden()
    part_menu.click()
    names = part_list.locator("[data-color-part]").evaluate_all(
        "buttons => buttons.slice(0, 8).map(button => button.dataset.colorPart)"
    )
    for index, name in enumerate(names):
        if index:
            part_menu.click()
        part_list.locator(f'[data-color-part="{name}"]').click()
        color_trigger.click()
        hex_input.fill("#a52bed")
        hex_input.press("Enter")
        hex_input.press("Escape")
    expect(panel.locator("[data-changed-color-part]")).to_have_count(8)
    expect(back_to_top).to_be_hidden()

    scroll_height = panel.evaluate("node => node.scrollHeight")
    panel.evaluate("node => { node.scrollTop = node.scrollHeight; }")
    expect(back_to_top).to_be_visible()
    arrow = back_to_top.bounding_box()
    footer = page.locator(".export-actions").bounding_box()
    assert abs(arrow["x"] + arrow["width"] / 2 - footer["x"] - footer["width"] / 2) < 1
    assert panel.evaluate("node => node.scrollHeight") == scroll_height

    back_to_top.click()
    expect(back_to_top).to_be_hidden()
    expect(part_menu).to_be_focused()
    assert panel.evaluate("node => node.scrollTop") == 0


def test_side_handles_cannot_be_dragged_off_the_window(page: Page, running_server: str) -> None:
    for width in (1400, 1200, 1120):
        _open(page, running_server, width)
        _drag_both_sides_past_the_limit(page)
        box = page.evaluate(GEOMETRY)
        assert box["gridOverflowX"] <= 1, f"{width}px: 网格横向溢出 {box['gridOverflowX']}px"
        assert box["documentOverflowX"] <= 1, f"{width}px: 文档横向溢出"
        assert box["inspectorRight"] <= box["viewportWidth"] + 1, (
            f"{width}px: 右栏被推到视口外 ({box['inspectorRight']} > {box['viewportWidth']})"
        )


def test_narrowing_the_window_reclamps_an_existing_layout(page: Page, running_server: str) -> None:
    """A ratio chosen on a wide window must not overflow after the window shrinks —
    and the handle has to stay reachable so the user can undo it."""
    _open(page, running_server, 2000)
    _drag_both_sides_past_the_limit(page)
    page.set_viewport_size({"width": 1120, "height": 900})
    page.wait_for_timeout(250)
    box = page.evaluate(GEOMETRY)
    assert box["gridOverflowX"] <= 1, f"缩小后仍溢出 {box['gridOverflowX']}px"
    assert box["inspectorRight"] <= box["viewportWidth"] + 1, "缩小后右栏跑出视口"


def test_handles_are_hidden_in_the_stacked_layout(page: Page, running_server: str) -> None:
    _open(page, running_server, 1000)
    for selector in ('#resize-left', '#resize-right', '#resize-dock'):
        expect(page.locator(selector)).to_be_hidden()


def _drag_dock_to_the_limit(page: Page) -> None:
    page.keyboard.down("Shift")
    page.focus("#resize-dock")
    for _ in range(40):
        page.keyboard.press("ArrowUp")
    page.keyboard.up("Shift")
    page.wait_for_timeout(200)


def test_default_layout_fits_vertically(page: Page, running_server: str) -> None:
    """Keep playback and progress controls inside the default viewport."""
    for width, height in ((1600, 1024), (1400, 900), (1280, 800)):
        _open(page, running_server, width, height)
        box = page.evaluate(GEOMETRY)
        assert box["playbackShown"], f"{width}x{height}: 播放控件不可见"
        assert box["playbackBottom"] <= box["viewportHeight"] + 1, (
            f"{width}x{height}: 默认布局下播放控件已在窗口外"
        )
        assert box["dockClip"] <= 1, f"{width}x{height}: dock 内容被裁 {box['dockClip']}px"


def test_playback_controls_stay_reachable_when_dock_is_maximised(
    page: Page, running_server: str
) -> None:
    """Exclude vertical padding when clamping the dock resize handle.

        At its maximum height the dock must fit without clipping or overflow;
        allowing a scroll fallback here would hide the original regression.
        Scrolling remains a valid fallback only for the default layout.
        """
    for width, height in ((1600, 1024), (1400, 900), (1280, 800), (1200, 720), (1120, 660)):
        _open(page, running_server, width, height)
        page.focus('#resize-dock')
        for _ in range(10):
            page.keyboard.press('Shift+ArrowDown')
        before = page.locator('.animation-dock').bounding_box()['height']
        _drag_dock_to_the_limit(page)
        assert page.locator('.animation-dock').bounding_box()['height'] > before + 1
        box = page.evaluate(GEOMETRY)
        assert box["playbackShown"], f"{width}x{height}: 播放控件不可见"
        assert box["dockClip"] <= 1, f"{width}x{height}: dock 内容被裁 {box['dockClip']}px"
        assert not box["workspaceCanScroll"], (
            f"{width}x{height}: 拖到上限后仍纵向溢出，说明行手柄上限算多了"
        )
        assert box["playbackBottom"] <= box["viewportHeight"] + 1, (
            f"{width}x{height}: 播放控件被推到窗口外 "
            f"(bottom={box['playbackBottom']} > {box['viewportHeight']})"
        )


def test_short_workspace_scrolls_only_the_action_list(page: Page, running_server: str) -> None:
    page.set_viewport_size({"width": 1280, "height": 525})
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.set_input_files("#template-archive-file", {
        "name": "many-actions.zip", "mimeType": "application/zip", "buffer": spine_zip(animations=11),
    })
    expect(page.locator("#emote-spine-canvas")).to_be_visible()
    play = page.locator("#play-animation-btn")
    workspace = page.locator(".workspace-panel")
    fits = "node => node.scrollHeight <= node.clientHeight"
    expect(play).to_be_in_viewport(ratio=1)
    assert workspace.evaluate(fits)

    handle = page.locator("#resize-dock").bounding_box()
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 300, steps=6)
    page.mouse.up()
    expect(play).to_be_in_viewport(ratio=1)
    assert workspace.evaluate(fits)

    actions = page.locator("#animation-list")
    actions.locator(".animation-card").last.scroll_into_view_if_needed()
    assert actions.evaluate("node => node.scrollTop") > 0
    assert workspace.evaluate("node => node.scrollTop") == 0


def test_stacked_mode_keeps_the_playback_bar_intact(
    page: Page, running_server: str
) -> None:
    """Keep a usable dock height when narrow windows stack the panels.

        Page scrolling is intentional in this layout. Assert an unclipped dock
        with room for its controls, and bound the square canvas height so it
        cannot consume the entire stacked panel.
        """
    for width in (1080, 1000, 900, 820):
        _open(page, running_server, width, 760)
        box = page.evaluate(GEOMETRY)
        # 堆叠的判据是"检查器落到了工作台下方"，不是 grid-template —— 那个断言
        # 上一版写了，结果 ≤1080 仍然是 5 列 grid（靠 grid-column 跨列堆的）。
        assert box["inspectorTop"] > box["canvasBottom"], (width, "没有堆叠", box)
        assert box["dockClip"] <= 1, (width, f"dock 内容被裁 {box['dockClip']}px", box)
        sizes = page.evaluate(
            """() => {
                const h = (s) => Math.round(document.querySelector(s).getBoundingClientRect().height);
                return { dock: h('.animation-dock'), frame: h('.canvas-frame'),
                         // 只量渲染出来的按钮：组条里那几个在没有模板时是设计性隐藏的，
                         // 把 0 高度的隐藏控件算进"不该被压扁"会误报
                         buttons: Array.from(document.querySelectorAll('.animation-dock button'))
                             .filter((b) => b.offsetParent !== null)
                             .map((b) => Math.round(b.getBoundingClientRect().height)) };
            }"""
        )
        assert sizes["dock"] >= 60, (width, sizes)
        assert sizes["frame"] <= 760 * 0.62, (width, sizes)
        assert sizes["buttons"] and min(sizes["buttons"]) >= 18, (width, sizes)
