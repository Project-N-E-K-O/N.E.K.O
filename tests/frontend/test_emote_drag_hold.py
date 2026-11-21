"""Test the long-press ring and option-card drag ghost.

These frontend interactions can be driven independently of a real animation
template, keeping ring and ghost behavior covered by browser tests.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

SETUP = """() => {
    document.body.insertAdjacentHTML('beforeend',
        '<button id="hold-target" style="position:fixed;left:200px;top:200px;width:120px;height:60px">'
        + '<img src="/static/icons/close_button.png" alt=""><span>064_叹号</span></button>');
    window.__CALLS = [];
    const el = document.getElementById('hold-target');
    el.addEventListener('pointerdown', (event) => {
        window.__pointerId = event.pointerId;
        window.__CTRL = window.NekoEmoteDragHold.armHold(event, {
            holdMs: 600,
            onComplete: (point) => window.__CALLS.push(['done', Math.round(point.clientX), Math.round(point.clientY)]),
            onCancel: (why) => window.__CALLS.push(['cancel', why]),
        });
    });
    return true;
}"""

RING = """() => {
    const ring = document.querySelector('.pack-hold-ring');
    if (!ring) return null;
    const svg = ring.querySelector('svg');
    const progress = ring.querySelector('.pack-hold-progress');
    const track = ring.querySelector('.pack-hold-track');
    return {
        hollowCenter: svg !== null && progress.getAttribute('fill') === 'none'
            && track.getAttribute('fill') === 'none',
        offset: Number(progress.getAttribute('stroke-dashoffset')),
        circumference: Number(ring.dataset.circumference),
        full: ring.classList.contains('is-full'),
        size: Math.round(ring.getBoundingClientRect().width),
    };
}"""


def _open(page: Page, running_server: str) -> None:
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.wait_for_timeout(300)
    assert page.evaluate("() => typeof window.NekoEmoteDragHold === 'object'"), \
        "模板要用和其它模块一致的 ?v= 形式载入 emote_drag_hold.js"
    page.evaluate(SETUP)
    page.wait_for_timeout(120)


def _centre(page: Page) -> tuple[int, int]:
    box = page.locator("#hold-target").bounding_box()
    return int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)


def test_the_ring_is_hollow_and_fills_with_time(page: Page, running_server: str) -> None:
    _open(page, running_server)
    x, y = _centre(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(240)
    early = page.evaluate(RING)
    assert early, "长按时没有出现圆环"
    assert early["hollowCenter"] is True, "圆心必须是空的：只填描边"
    assert early["size"] > 20, early
    page.wait_for_timeout(200)
    later = page.evaluate(RING)
    assert later["offset"] < early["offset"], (early, later)
    assert later["offset"] > 0, "不该在计时结束前就满"
    page.mouse.up()


def test_releasing_early_cancels_and_removes_the_ring(page: Page, running_server: str) -> None:
    _open(page, running_server)
    x, y = _centre(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(160)
    assert page.evaluate("() => Boolean(document.querySelector('.pack-hold-ring'))")
    page.mouse.up()
    page.wait_for_timeout(300)
    calls = page.evaluate("() => window.__CALLS")
    assert calls == [["cancel", "release"]], calls
    assert page.evaluate("() => document.querySelectorAll('.pack-hold-ring').length") == 0, \
        "取消后圆环要消失，不能留在屏幕上"


@pytest.mark.parametrize("ending", ["release", "pointercancel", "cancel"])
def test_finishing_ring_can_be_cancelled_before_drag_starts(page: Page, running_server: str, ending: str) -> None:
    _open(page, running_server)
    x, y = _centre(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_function("Boolean(document.querySelector('.pack-hold-ring.is-full'))")
    if ending == "release":
        page.mouse.up()
    elif ending == "pointercancel":
        page.evaluate("window.dispatchEvent(new PointerEvent('pointercancel', {pointerId: window.__pointerId}))")
    else:
        page.evaluate("window.__CTRL.cancel()")
    page.wait_for_timeout(250)
    reason = "release" if ending == "release" else "abort"
    assert page.evaluate("window.__CALLS") == [["cancel", reason]]
    assert page.evaluate("window.__CTRL.isHeld()") is False
    assert page.locator('.pack-hold-ring').count() == 0
    page.mouse.up()


def test_moving_before_the_hold_completes_is_not_a_drag(page: Page, running_server: str) -> None:
    _open(page, running_server)
    x, y = _centre(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(150)
    page.mouse.move(x + 40, y, steps=3)      # 远超 8px 的容差
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.__CALLS") == [["cancel", "abort"]], \
        page.evaluate("() => window.__CALLS")
    assert page.evaluate("() => document.querySelectorAll('.pack-hold-ring').length") == 0


def test_a_full_ring_pops_before_the_drag_starts(page: Page, running_server: str) -> None:
    _open(page, running_server)
    x, y = _centre(page)
    page.mouse.move(x, y)
    page.mouse.down()
    # 等"状态"而不是猜时间：弹动只存在 170ms，按固定延时采样一定会飘
    page.wait_for_function(
        """() => {
            const ring = document.querySelector('.pack-hold-ring');
            if (!ring) return false;
            const progress = ring.querySelector('.pack-hold-progress');
            return ring.classList.contains('is-full') && progress.getAttribute('stroke-dashoffset') === '0';
        }""",
        timeout=2000,
    )
    page.wait_for_function("() => window.__CALLS.some((c) => c[0] === 'done')", timeout=2000)
    point = page.evaluate("() => window.__CALLS.find((c) => c[0] === 'done')")
    assert abs(point[1] - x) <= 2 and abs(point[2] - y) <= 2, (point, x, y)
    page.wait_for_function(
        "() => document.querySelectorAll('.pack-hold-ring').length === 0", timeout=2000)
    page.mouse.up()


def test_the_ghost_is_the_option_card_not_a_label(page: Page, running_server: str) -> None:
    """Render the dragged option card rather than a text-only ghost."""
    _open(page, running_server)
    built = page.evaluate(
        """() => {
            const ghost = window.NekoEmoteDragHold.buildGhost(
                { kind: 'scene', sceneId: 'a', label: '064_叹号' },
                document.getElementById('hold-target'));
            document.body.appendChild(ghost);
            const thumb = ghost.querySelector('.pack-drag-thumb');
            const label = ghost.querySelector('.pack-drag-label');
            const box = ghost.getBoundingClientRect();
            const out = {
                structure: thumb !== null && label !== null,
                text: label ? label.textContent : '',
                hasImage: Boolean(thumb && thumb.querySelector('img')),
                imgSrc: thumb && thumb.querySelector('img') ? thumb.querySelector('img').getAttribute('src') : '',
                width: Math.round(box.width), height: Math.round(box.height),
                shadow: getComputedStyle(ghost).boxShadow !== 'none',
            };
            ghost.remove();
            return out;
        }"""
    )
    assert built["structure"] is True, built
    assert built["hasImage"] is True, "ghost 要带缩略图"
    assert "close_button" in built["imgSrc"], built
    assert built["text"] == "064_叹号", built
    assert built["width"] > 60 and built["height"] > 30, built
    assert built["shadow"] is True, built


def test_the_ring_still_fills_without_motion_when_reduced(page: Page, running_server: str) -> None:
    """Preserve visible hold progress when reduced motion disables scaling."""
    page.emulate_media(reduced_motion="reduce")
    _open(page, running_server)
    x, y = _centre(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(150)
    first = page.evaluate(RING)
    assert first, "减少动效时圆环也该在"
    page.wait_for_timeout(50)
    second = page.evaluate(RING)
    assert second, "长按还没到点，圆环不该消失"
    assert second["offset"] < first["offset"], (first, second)
    page.wait_for_timeout(300)
    page.wait_for_function("() => window.__CALLS.some((c) => c[0] === 'done')", timeout=5000)
    page.mouse.up()


def test_a_normal_click_never_shows_the_ring(page: Page, running_server: str) -> None:
    """Delay the hold ring so an ordinary pointer press does not flash it."""
    _open(page, running_server)
    x, y = _centre(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(45)                 # 明显短于圆环出现延时
    page.mouse.up()
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.querySelectorAll('.pack-hold-ring').length") == 0, \
        "一次普通点击不该出现圆环"
    # 取消本身是正常的（按下又松开），这条要断言的是"没起拖"
    calls = page.evaluate("() => window.__CALLS")
    assert not any(c[0] == "done" for c in calls), f"一次点击不该起拖: {calls}"


def test_the_ghost_floats_and_does_not_move_the_page(page: Page, running_server: str) -> None:
    """Keep the drag ghost outside document flow so dragging cannot stretch the page."""
    _open(page, running_server)
    before = page.evaluate("() => document.documentElement.scrollHeight")
    shape = page.evaluate(
        """() => {
            const ghost = window.NekoEmoteDragHold.buildGhost({ label: '064_叹号' }, null);
            document.body.appendChild(ghost);
            ghost.style.left = '400px';
            ghost.style.top = '400px';
            const cs = getComputedStyle(ghost);
            const grew = document.documentElement.scrollHeight - %d;
            const box = ghost.getBoundingClientRect();
            const out = {
                position: cs.position,
                pointerEvents: cs.pointerEvents,
                z: Number(cs.zIndex),
                overlayZ: Number(getComputedStyle(document.querySelector('.st-overlay') || document.body).zIndex) || 84,
                grew,
                insideViewport: box.top > -500 && box.left > -500,
            };
            ghost.remove();
            return out;
        }""" % before
    )
    assert shape["position"] == "fixed", shape
    assert shape["pointerEvents"] == "none", shape
    assert shape["z"] > shape["overlayZ"], "拖拽影必须浮在进阶编辑器之上"
    assert shape["grew"] <= 1, f"ghost 把页面撑长了 {shape['grew']}px"
    assert shape["insideViewport"] is True, shape


def test_the_ring_outranks_the_stage_overlay(page: Page, running_server: str) -> None:
    """Keep the hold ring visible above the state-stage overlay."""
    _open(page, running_server)
    layer = page.evaluate(
        """() => {
            document.body.insertAdjacentHTML('beforeend', "<div class='st-overlay'></div>");
            const ring = window.NekoEmoteDragHold.createRing(200, 200, 3200, 0);
            const z = Number(getComputedStyle(ring).zIndex);
            const overlay = Number(getComputedStyle(document.querySelector('.st-overlay')).zIndex);
            const pointerEvents = getComputedStyle(ring).pointerEvents;
            ring.remove();
            document.querySelector('.st-overlay').remove();
            return { z, overlay, pointerEvents };
        }"""
    )
    assert layer["z"] > layer["overlay"], layer
    assert layer["pointerEvents"] == "none", "圆环不能吃指针，否则挡住落点"


def test_a_native_dragstart_does_not_steal_our_drag(page: Page, running_server: str) -> None:
    """Prevent native image dragging from interrupting pointer events and scrolling the list."""
    _open(page, running_server)
    x, y = _centre(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(150)
    assert page.evaluate("() => document.getElementById('hold-target').dispatchEvent("
                         "new DragEvent('dragstart', { bubbles: true, cancelable: true }))") is False
    page.wait_for_timeout(400)
    try:
        page.wait_for_function("() => window.__CALLS.some((c) => c[0] === 'done')", timeout=5000)
    except Exception:
        raise AssertionError("原生拖拽把长按起拖打断了: "
            + str(page.evaluate("() => window.__CALLS")))
    page.mouse.up()


def test_the_cursor_stays_grabbing_over_any_widget(page: Page, running_server: str) -> None:
    """Keep the drag cursor when the pointer crosses buttons or text controls."""
    _open(page, running_server)
    page.evaluate("() => document.body.classList.add('is-pack-dragging')")
    states = page.evaluate(
        """() => ['button', 'a', 'input', 'select', '#hold-target']
            .map((sel) => document.querySelector(sel))
            .filter((el) => el !== null)
            .map((el) => el.tagName + ':' + getComputedStyle(el).cursor)"""
    )
    page.evaluate("() => document.body.classList.remove('is-pack-dragging')")
    assert states, "页面上没有可测的控件"
    assert all(c.endswith(":grabbing") for c in states), states


def test_the_ghost_swings_with_the_pointer_and_settles(page: Page, running_server: str) -> None:
    _open(page, running_server)
    motion = page.evaluate(
        """async () => {
            const ghost = window.NekoEmoteDragHold.buildGhost({ label: '挥手' }, null);
            document.body.appendChild(ghost);
            const swing = window.NekoEmoteDragHold.createGhostSwing(ghost);
            swing.start();
            const raf = () => new Promise((r) => requestAnimationFrame(r));
            let x = 200;
            for (let i = 0; i < 8; i += 1) { x += 26; swing.moveTo(x, 200); await raf(); }
            const swung = ghost.style.getPropertyValue('--drag-tilt');
            for (let i = 0; i < 30; i += 1) { swing.moveTo(x, 200); await raf(); }
            const settled = Number(ghost.style.getPropertyValue('--drag-tilt').replace('deg', ''));
            const transform = getComputedStyle(ghost).transform;
            swing.stop();
            ghost.remove();
            return { swung, settled, usesVar: transform !== 'none' };
        }"""
    )
    assert motion["swung"] and motion["swung"] != "0.00deg", motion
    assert abs(motion["settled"]) < 0.6, f"停下后应该回正: {motion}"
    assert motion["usesVar"] is True, motion


def test_the_basic_rows_are_addressable_as_drop_targets(page: Page, running_server: str) -> None:
    """Resolve drop targets by data-state-key so dropping updates the intended state."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.wait_for_timeout(400)
    page.evaluate("() => document.querySelector('#inspector-tab-export')?.click()")
    page.wait_for_timeout(400)
    rows = page.evaluate(
        """() => Array.from(document.querySelectorAll('#pack-basic-rows .pack-basic-row'))
            .map((row) => row.dataset.stateKey || '')"""
    )
    assert rows == ["idle_image", "talking_image", "drag_image", "click_image",
                    "happy_image", "sad_image", "angry_image", "surprised_image"], rows
    # 落点用走带蓝（原来的琥珀色被判定"松手会进这里"，不能和选中态的蓝轨混用
    # 落点样式：整行不再描边（那圈粗光正是"难看"的来源），改成下拉框本身染色
    # + 左边一道轨；目标区用细虚线，而不是实线粗框。
    rules = page.evaluate(
        """() => {
            const flat = [];
            for (const sheet of document.styleSheets) {
                try { flat.push(...sheet.cssRules) } catch (_) {}
            }
            const find = (sel) => flat.find((r) => r.selectorText === sel);
            return {
                rowRinged: Boolean(find('.pack-basic-row.is-drop-target')
                    && find('.pack-basic-row.is-drop-target').style.boxShadow),
                selectHighlighted: Boolean(find('.pack-basic-row.is-drop-target select')),
                rail: Boolean(find('.pack-basic-row.is-drop-target::before')),
                zoneStyle: find('#pack-basic-rows') ? find('#pack-basic-rows').style.borderStyle : '',
                zoneHinted: Boolean(find('#pack-basic-rows.is-drop-hint')),
            };
        }"""
    )
    assert rules["rowRinged"] is False, rules
    assert rules["selectHighlighted"] is True, rules
    assert rules["rail"] is True, rules
    assert rules["zoneStyle"] == "dashed", rules
    assert rules["zoneHinted"] is True, rules



def test_the_click_guard_starts_counting_at_release(page: Page, running_server: str) -> None:
    """Start click suppression on release, not drag start, so long holds cannot outlast it."""
    _open(page, running_server)
    verdict = page.evaluate(
        """async () => {
            const guard = window.NekoEmoteDragHold.createClickGuard(600);
            const raf = (ms) => new Promise((r) => setTimeout(r, ms));
            const before = guard.shouldSwallow();
            guard.arm();
            const duringDrag = guard.shouldSwallow();
            await raf(900);                       // 拖在手里久一点，超过 600ms
            const stillArmed = guard.shouldSwallow();
            window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
            const justAfterRelease = guard.shouldSwallow();
            await raf(750);
            const afterWindow = guard.shouldSwallow();
            return { before, duringDrag, stillArmed, justAfterRelease, afterWindow };
        }"""
    )
    assert verdict["before"] is False, verdict
    assert verdict["duringDrag"] is True, verdict
    assert verdict["stillArmed"] is True, "还在手里就不该因为时间到而放行"
    assert verdict["justAfterRelease"] is True, verdict
    assert verdict["afterWindow"] is False, "过了窗口要能正常点击"
